#!/usr/bin/env python3
"""WOWY, with or without you: what each part of the stack is worth.

Every part is switched off from the reference (without) and switched on alone
over a small base (with), and each variant is scored against the patch-blind
line: the mean gain in today's bugs caught across a range of costs.  The
difference from the reference is bootstrapped over changes, paired (both
variants on the same resampled changes), so the interval is on the difference
itself.  Model variants are trained with several seeds.

Decisions are made by cross-validation on the training months (--mode cv, the
default): the training months are split in two folds, and each fold's model,
history and blind order are fitted on the other.  The held-out test months are
a lockbox, scored with --mode lockbox only to confirm.  A family of pure noise
is a negative control: it must come out unclear.

  .venv/bin/python eval/wowy.py                         # cv; eval/results/wowy.json
  .venv/bin/python eval/wowy.py --mode lockbox          # eval/results/wowy-lockbox.json
  .venv/bin/python eval/wowy.py --only families --only spend
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from classes import CLASSES  # noqa: E402
from grade import full, grade  # noqa: E402
from predictors import ranked  # noqa: E402
from predictors.model import FAMILIES, Model  # noqa: E402
from predictors.principles import Principles, load  # noqa: E402
from predictors.risk import Risk, changed_lines  # noqa: E402
from predictors.simple import Simple  # noqa: E402

ROOT = HERE.parent
RATES = [1.0, 0.4, 0.25, 0.15, 0.1, 0.07, 0.05, 0.035, 0.025, 0.015, 0.01]
GRID = [0.15, 0.175, 0.2, 0.225, 0.25, 0.275, 0.3, 0.35, 0.4]
BOOT = 1000
SEEDS = (1, 2, 3)
BASE = {"base", "edited"}
REF_FAMILIES = set(FAMILIES) - {"base"}         # base rates hurt (WOWY, round 1)
NOISE = 4                                        # noise columns, the negative control
SMALL = 25                                       # lines: the size gate


def month(day: str) -> int:
	return int(day[:4]) * 12 + int(day[5:7])


class Split:
	"""One evaluation set, kept per change so resampling only re-adds numbers."""

	def __init__(self, cases: list[dict], costs: dict):
		self.cases, self.costs = cases, costs
		self.changes = sorted({c["change"] for c in cases})
		self.idx = {ch: i for i, ch in enumerate(self.changes)}
		self.fulls = [full(c) for c in cases]
		per = defaultdict(int)
		for c in cases:
			if c["faulty"]:
				per[c["change"]] += len(c["bugs"])
		self.w = [1 / per[c["change"]] if c["faulty"] and per[c["change"]] else 0.0 for c in cases]

	def aggregate(self, texts: list[str]) -> np.ndarray:
		"""Per change: [caught, full caught, cost, actual] for one selection per case."""
		out = np.zeros((len(self.changes), 4))
		for c, t, f, w in zip(self.cases, texts, self.fulls, self.w):
			g = grade(c, t, self.costs)
			i = self.idx[c["change"]]
			if w:
				out[i, 0] += w * g["caught"]
				out[i, 1] += w * f["caught"]
			if c["sampled"]:
				out[i, 2] += g["cost_s"]
				out[i, 3] += g["actual_s"]
		return out


def curve(aggs, weights=None):
	pts = []
	for a in aggs:
		s = a.sum(0) if weights is None else (a * weights[:, None]).sum(0)
		if s[1] and s[3]:
			pts.append((s[2] / s[3], s[0] / s[1]))
	return sorted(pts)


def interp(pts, x):
	if not pts or x < pts[0][0] or x > pts[-1][0]:
		return None
	for (a, b), (c, d) in zip(pts, pts[1:]):
		if a <= x <= c:
			return b if c == a else b + (d - b) * (x - a) / (c - a)
	return pts[-1][1]


def gain(pts, blind):
	vals = {}
	for x in GRID:
		v, b = interp(pts, x), interp(blind, x)
		if v is not None and b is not None:
			vals[x] = v - b
	return (sum(vals.values()) / len(vals) if vals else None), vals


def summarize(name, aggs, ref_aggs, blind_aggs, n_changes, rng_seed=7) -> dict:
	g, at = gain(curve(aggs), curve(blind_aggs))
	rng = np.random.default_rng(rng_seed)
	diffs, gains = [], []
	for _ in range(BOOT):
		w = np.bincount(rng.integers(0, n_changes, n_changes), minlength=n_changes).astype(float)
		bl = curve(blind_aggs, w)
		mine, _ = gain(curve(aggs, w), bl)
		if mine is None:
			continue
		gains.append(mine)
		if ref_aggs is not None:
			ref, _ = gain(curve(ref_aggs, w), bl)
			if ref is not None:
				diffs.append(mine - ref)
	ci = lambda v: [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))] if v else None  # noqa: E731
	return {"name": name, "gain": g, "gain_ci": ci(gains), "at20": at.get(0.2), "at25": at.get(0.25),
		"diff": float(np.mean(diffs)) if diffs else None, "diff_ci": ci(diffs), "curve": curve(aggs)}


class Fold:
	"""Everything fitted on one fold's training cases, and its evaluation cases'
	features: a variant only picks columns and refits the classifier."""

	def __init__(self, train: list[dict], evals: list[dict], costs: dict, seed: int = 0):
		self.train, self.evals = train, evals
		self.M = Model(costs, CLASSES, families=FAMILIES).fit(train)
		self.risk = Risk().fit(train)
		self.order = ranked.rank(train, costs, CLASSES)
		self.names = list(self.M.cands)
		tags = [f for f, _ in self.M.tagged(train[0]["input"], self.names[0], train[0]["change"])]
		self.col = {fam: [i for i, f in enumerate(tags) if f == fam] for fam in FAMILIES}
		rows = self.M.rows
		rng = np.random.default_rng(1000 + seed)
		self.Xtr = np.array([[v for _, v in self.M.tagged(c["input"], n, c["change"])] for c, n, _ in rows])
		self.ytr = np.array([y for _, _, y in rows])
		per = defaultdict(int)
		for c, _, _ in rows:
			per[c["change"]] += 1
		self.wtr = np.array([1.0 / per[c["change"]] for c, _, _ in rows])
		self.wtr *= len(self.wtr) / self.wtr.sum()
		self.groups = []
		last = None
		for c, _, _ in rows:
			if id(c) != last:
				self.groups.append(0)
				last = id(c)
			self.groups[-1] += 1
		self.Xev = np.array([[v for _, v in self.M.tagged(c["input"], n)] for c in evals for n in self.names])
		# the negative control: columns of noise, the same for every variant
		self.Xtr = np.hstack([self.Xtr, rng.normal(size=(len(self.Xtr), NOISE))])
		self.Xev = np.hstack([self.Xev, rng.normal(size=(len(self.Xev), NOISE))])
		width = self.Xtr.shape[1]
		self.col["noise"] = list(range(width - NOISE, width))
		self.risks = [self.risk(c["input"]) for c in evals]

	def probabilities(self, families, kind, seed):
		cols = sorted(i for f in families for i in self.col[f])
		Xtr, Xev = self.Xtr[:, cols], self.Xev[:, cols]
		if kind == "gbdt":
			from sklearn.ensemble import HistGradientBoostingClassifier
			clf = HistGradientBoostingClassifier(max_depth=4, learning_rate=0.05, max_iter=250,
							     l2_regularization=1.0, min_samples_leaf=40, random_state=seed)
			clf.fit(Xtr, self.ytr, sample_weight=self.wtr)
			p = clf.predict_proba(Xev)[:, 1]
		elif kind == "lgbm-rank":
			import lightgbm as lgb
			from sklearn.isotonic import IsotonicRegression
			rk = lgb.LGBMRanker(n_estimators=300, learning_rate=0.05, num_leaves=15, min_child_samples=40,
					    random_state=seed, verbose=-1)
			rk.fit(Xtr, self.ytr, group=self.groups)
			# a ranker's scores are not chances: map them onto the training rows' catch rate
			iso = IsotonicRegression(out_of_bounds="clip").fit(rk.predict(Xtr), self.ytr)
			p = iso.predict(rk.predict(Xev))
		else:
			from sklearn.linear_model import LogisticRegression
			from sklearn.preprocessing import StandardScaler
			sc = StandardScaler().fit(Xtr)
			lr = LogisticRegression(C=0.3, max_iter=3000).fit(sc.transform(Xtr), self.ytr, sample_weight=self.wtr)
			p = lr.predict_proba(sc.transform(Xev))[:, 1]
		return p.reshape(len(self.evals), len(self.names))

	def texts(self, families, kind, seed, *, seed_principles=False, redundancy="none", alpha=0.0, gate=False):
		"""Per operating point, each evaluation case's Test-Parameters."""
		p = self.probabilities(families, kind, seed)
		out = []
		for rate in RATES:
			row = []
			for c, sc, r in zip(self.evals, p, self.risks):
				spend = (r / self.risk.mean) ** alpha if alpha else 1.0
				if gate and changed_lines(c["input"]) <= SMALL:
					spend = 0.0
				row.append(self.M.select(c["input"], rate, dict(zip(self.names, sc)), redundancy=redundancy,
							 seed=seed_principles, spend=spend))
			out.append(row)
		return out


def main() -> None:
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("--cases", default=str(ROOT / "data" / "cases.jsonl"))
	ap.add_argument("--mode", choices=["cv", "lockbox"], default="cv")
	ap.add_argument("--only", choices=["families", "selector", "spend", "rules", "models", "candidate", "risk"],
			action="append")
	ap.add_argument("--seeds", type=int, default=len(SEEDS))
	ap.add_argument("--ref", default="lgbm-rank", choices=["gbdt", "lgbm-rank", "linear"])
	args = ap.parse_args()
	out_path = HERE / "results" / ("wowy.json" if args.mode == "cv" else "wowy-lockbox.json")
	sections = set(args.only or ["families", "selector", "spend", "models", "rules", "candidate", "risk"])
	t0 = time.time()
	cases = [json.loads(x) for x in open(args.cases)]
	costs = json.loads((HERE / "costs.json").read_text())
	train = [c for c in cases if c["split"] == "train"]
	if args.mode == "cv":
		# two folds of the training months, alternating by month
		folds = [([c for c in train if month(c["day"]) % 2 != f], [c for c in train if month(c["day"]) % 2 == f])
			 for f in (0, 1)]
		views = {"cv": None}
	else:
		test = [c for c in cases if c["split"] == "test"]
		folds = [(train, test)]
		views = {"test": None, "recent": lambda c: c.get("recent")}
	fits = [Fold(tr, ev, costs, i) for i, (tr, ev) in enumerate(folds)]
	evals = [c for f in fits for c in f.evals]
	splits = {v: Split([c for c in evals if not keep or keep(c)], costs) for v, keep in views.items()}
	print(f"{args.mode}: {len(fits)} folds, {sum(len(f.Xtr) for f in fits)} training rows, "
	      f"{len(evals)} evaluation cases, features in {time.time() - t0:.0f} s", file=sys.stderr)

	pos = {id(c): i for i, c in enumerate(evals)}

	def aggregate(per_fold_texts):
		"""Join the folds' texts in evaluation order, then aggregate per view."""
		joined = [sum((ft[i] for ft in per_fold_texts), []) for i in range(len(per_fold_texts[0]))]
		return {v: [sp.aggregate([row[pos[id(c)]] for c in sp.cases]) for row in joined] for v, sp in splits.items()}

	blind = aggregate([[[ranked.prediction(f.order, n, CLASSES)] * len(f.evals) for n in range(0, 61)]
			   for f in fits])

	def averaged(families, kind=args.ref, **kw):
		runs = [aggregate([f.texts(families, kind, s, **kw) for f in fits]) for s in SEEDS[:args.seeds]]
		return {v: [sum(r[v][i] for r in runs) / len(runs) for i in range(len(RATES))] for v in splits}

	results: dict = {"mode": args.mode, "ref": args.ref, "grid": GRID, "seeds": SEEDS[:args.seeds], "sections": {}}
	if args.only and out_path.exists():
		results["sections"] = json.loads(out_path.read_text()).get("sections", {})
		for sec in sections:
			results["sections"].pop(sec, None)
			results["sections"].pop(f"{sec}_ref", None)

	def save():
		results["seconds"] = round(time.time() - t0)
		out_path.write_text(json.dumps(results, indent=0) + "\n")

	def record(section, name, aggs, ref):
		for v, sp in splits.items():
			s = summarize(name, aggs[v], ref[v] if ref else None, blind[v], len(sp.changes))
			results["sections"].setdefault(section, {}).setdefault(v, []).append(s)
			if v == next(iter(splits)):
				print(f"  {section:9s} {name:44s} gain {fmt(s['gain'])} {fmtci(s['gain_ci'])} "
				      f"vs ref {fmt(s['diff'])} {fmtci(s['diff_ci'])}  @20 {fmt(s['at20'])} @25 {fmt(s['at25'])}",
				      file=sys.stderr)

	ref = averaged(REF_FAMILIES)
	if "families" in sections:
		record("families", f"reference ({args.ref})", ref, None)
		for fam in sorted(REF_FAMILIES):
			record("families", f"without {fam}", averaged(REF_FAMILIES - {fam}), ref)
		record("families", "with noise (negative control)", averaged(REF_FAMILIES | {"noise"}), ref)
		base = averaged(BASE)
		record("families", "base only (base rates, edited)", base, ref)
		for fam in sorted((set(FAMILIES) | {"noise"}) - BASE):
			record("families", f"base + {fam}", averaged(BASE | {fam}), base)
		save()
	if "spend" in sections:
		record("spend", "same threshold for every patch", ref, None)
		record("spend", f"small patches (<= {SMALL} lines) get nothing extra", averaged(REF_FAMILIES, gate=True), ref)
		record("spend", "spend by risk (alpha 0.5)", averaged(REF_FAMILIES, alpha=0.5), ref)
		record("spend", "spend by risk (alpha 1)", averaged(REF_FAMILIES, alpha=1.0), ref)
		record("spend", "spend by risk (alpha 1) and the size gate", averaged(REF_FAMILIES, alpha=1.0, gate=True), ref)
		save()
	if "selector" in sections:
		record("selector", "model alone", ref, None)
		record("selector", "principles first (hybrid)", averaged(REF_FAMILIES, seed_principles=True), ref)
		record("selector", "overlap discount", averaged(REF_FAMILIES, redundancy="max"), ref)
		save()
	if "models" in sections:
		record("models", f"reference ({args.ref})", ref, None)
		for kind, title in (("gbdt", "boosted trees"), ("linear", "logistic"), ("lgbm-rank", "LambdaMART ranker")):
			if kind != args.ref:
				record("models", title, averaged(REF_FAMILIES, kind=kind), ref)
		save()
	if "candidate" in sections:
		# E4: a curve over the size of the blind base; matched to RATES' length by sampling n
		ns = [0, 1, 2, 3, 4, 6, 8, 10, 13, 16, 20, 25, 30, 40]
		record("candidate", "reference", ref, None)
		simple = None
		for title, kw in (("simple candidate", {}),
				  ("+ must rules, edited scripts whole", {"use_must": True}),
				  ("+ must rules, edited tests only (ONLY=)", {"use_must": True, "edited": "only"}),
				  ("+ must rules, edited tests left to subtest-change", {"use_must": True, "edited": "none"})):
			aggs = aggregate([[[Simple(f.order, n, **kw).predict(c["input"])["text"] for c in f.evals]
					   for n in ns] for f in fits])
			# E4 is against the reference; E6's variants are against the simple candidate
			base_ref = ref if simple is None else simple
			for v, sp in splits.items():
				s_ = summarize(title, aggs[v], base_ref[v], blind[v], len(sp.changes))
				results["sections"]["candidate"].setdefault(v, []).append(s_)
				print(f"  candidate {title:48s} gain {fmt(s_['gain'])} {fmtci(s_['gain_ci'])} "
				      f"vs {'ref' if simple is None else 'simple'} {fmt(s_['diff'])} {fmtci(s_['diff_ci'])}  "
				      f"@20 {fmt(s_['at20'])} @25 {fmt(s_['at25'])}  [{v}]", file=sys.stderr)
			if simple is None:
				simple = aggs
		save()
	if "risk" in sections:
		# E5: how well the patch risk ranks population patches
		from sklearn.metrics import roc_auc_score
		pts = [(c, f.risk(c["input"])) for f in fits for c in f.evals]
		for v, sp in splits.items():
			keep = {id(c) for c in sp.cases}
			rows = [(c["change"], r, int(c["faulty"])) for c, r in pts if id(c) in keep and c["sampled"]]
			changes = sorted({ch for ch, _, _ in rows})

			def stats(sample):
				ys = [y for _, _, y in sample]
				rs = [r for _, r, _ in sample]
				if not (0 < sum(ys) < len(ys)):
					return None
				order = sorted(range(len(rs)), key=lambda i: rs[i])
				k = len(order) // 5
				base = sum(ys) / len(ys)
				low = sum(ys[i] for i in order[:k]) / k / base
				high = sum(ys[i] for i in order[-k:]) / k / base
				return roc_auc_score(ys, rs), high, low
			by = defaultdict(list)
			for row in rows:
				by[row[0]].append(row)
			rng = np.random.default_rng(7)
			boots = []
			for _ in range(BOOT):
				sample = [r for ch in rng.choice(changes, len(changes)) for r in by[ch]]
				st = stats(sample)
				if st:
					boots.append(st)
			auc, high, low = stats(rows)
			ci = lambda i: [float(np.percentile([b[i] for b in boots], 2.5)), float(np.percentile([b[i] for b in boots], 97.5))]  # noqa: E731
			results["sections"].setdefault("risk", {})[v] = {
				"patches": len(rows), "faulty": sum(y for _, _, y in rows), "auc": auc, "auc_ci": ci(0),
				"riskiest_fifth_lift": high, "riskiest_ci": ci(1), "safest_fifth_lift": low, "safest_ci": ci(2)}
			print(f"  risk [{v}] {len(rows)} patches, {sum(y for _, _, y in rows)} faulty: AUC {auc:.3f} "
			      f"({ci(0)[0]:.3f} to {ci(0)[1]:.3f}); riskiest fifth {high:.2f}x ({ci(1)[0]:.2f} to {ci(1)[1]:.2f}); "
			      f"safest fifth {low:.2f}x ({ci(2)[0]:.2f} to {ci(2)[1]:.2f})", file=sys.stderr)
		save()
	if "rules" in sections:
		rules = load()

		def principles_aggs(rs):
			p = Principles(rs)
			return aggregate([[[p.predict(c["input"])["text"] for c in f.evals]] for f in fits])
		ref_p = principles_aggs(rules)
		for v, sp in splits.items():
			results["sections"].setdefault("rules_ref", {})[v] = summarize("all rules", ref_p[v], None, blind[v],
										      len(sp.changes))
		for i, r in enumerate(rules):
			aggs = principles_aggs(rules[:i] + rules[i + 1:])
			for v, sp in splits.items():
				a, b = ref_p[v][0], aggs[v][0]
				rng = np.random.default_rng(7)
				d = []
				for _ in range(BOOT):
					w = np.bincount(rng.integers(0, len(sp.changes), len(sp.changes)),
							minlength=len(sp.changes)).astype(float)
					ra, rb = (a * w[:, None]).sum(0), (b * w[:, None]).sum(0)
					if ra[1] and rb[1]:
						d.append(ra[0] / ra[1] - rb[0] / rb[1])
				sa, sb = a.sum(0), b.sum(0)
				results["sections"].setdefault("rules", {}).setdefault(v, []).append({
					"name": r["name"], "bugs_lost": sa[0] / sa[1] - sb[0] / sb[1],
					"bugs_lost_ci": [float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))] if d else None,
					"cost_saved": sa[2] / sa[3] - sb[2] / sb[3]})
		save()
	save()
	print(f"done in {results['seconds']} s: {out_path}", file=sys.stderr)


def fmt(v):
	return "  n/a" if v is None else f"{100 * v:+5.1f}"


def fmtci(ci):
	return "" if not ci else f"({100 * ci[0]:+.1f} to {100 * ci[1]:+.1f})"


if __name__ == "__main__":
	main()
