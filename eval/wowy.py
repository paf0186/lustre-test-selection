#!/usr/bin/env python3
"""WOWY, with or without you: what each part of the stack is worth.

Every part is switched off from the full stack (without) and switched on alone
over a small base (with), and each variant is scored against the patch-blind
line: the mean gain in today's bugs caught across a range of costs.  The
difference from the reference is bootstrapped over changes, paired (both
variants on the same resampled changes), so the interval is on the difference
itself.  Model variants are trained with several seeds.

  .venv/bin/python eval/wowy.py                 # everything; writes eval/results/wowy.json
  .venv/bin/python eval/wowy.py --only families # one section: families, selector, rules, models

The feature matrix is built once; a variant only picks its columns.
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

ROOT = HERE.parent
OUT = HERE / "results" / "wowy.json"
RATES = [1.0, 0.4, 0.25, 0.15, 0.1, 0.07, 0.05, 0.035, 0.025, 0.015, 0.01]
GRID = [0.15, 0.175, 0.2, 0.225, 0.25, 0.275, 0.3, 0.35, 0.4]
BOOT = 1000
SEEDS = (1, 2, 3)
BASE = {"base", "edited"}


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


def curve(aggs: list[np.ndarray], weights: np.ndarray | None = None) -> list[tuple[float, float]]:
	"""(cost share, share of today's bugs) per operating point, on resampled changes."""
	pts = []
	for a in aggs:
		s = a.sum(0) if weights is None else (a * weights[:, None]).sum(0)
		if s[1] and s[3]:
			pts.append((s[2] / s[3], s[0] / s[1]))
	return sorted(pts)


def interp(pts: list[tuple[float, float]], x: float) -> float | None:
	if not pts or x < pts[0][0] or x > pts[-1][0]:
		return None
	for (a, b), (c, d) in zip(pts, pts[1:]):
		if a <= x <= c:
			return b if c == a else b + (d - b) * (x - a) / (c - a)
	return pts[-1][1]


def gain(pts, blind) -> tuple[float | None, dict]:
	vals = {}
	for x in GRID:
		v, b = interp(pts, x), interp(blind, x)
		if v is not None and b is not None:
			vals[x] = v - b
	return (sum(vals.values()) / len(vals) if vals else None), vals


def summarize(name, aggs, ref_aggs, blind_aggs, n_changes, rng_seed=7) -> dict:
	"""Mean gain over blind on the grid, at 20% and 25%, and the paired difference
	from the reference, bootstrapped over changes."""
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
		"diff": float(np.mean(diffs)) if diffs else None, "diff_ci": ci(diffs),
		"curve": curve(aggs)}


def main() -> None:
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("--cases", default=str(ROOT / "data" / "cases.jsonl"))
	ap.add_argument("--only", choices=["families", "selector", "rules", "models"], action="append")
	ap.add_argument("--seeds", type=int, default=len(SEEDS))
	args = ap.parse_args()
	sections = set(args.only or ["families", "selector", "rules", "models"])
	t0 = time.time()
	cases = [json.loads(x) for x in open(args.cases)]
	costs = json.loads((HERE / "costs.json").read_text())
	train = [c for c in cases if c["split"] == "train"]
	splits = {"test": Split([c for c in cases if c["split"] == "test"], costs),
		  "recent": Split([c for c in cases if c["split"] == "test" and c.get("recent")], costs)}
	order = ranked.rank(train, costs, CLASSES)
	blind = {k: [sp.aggregate([ranked.prediction(order, n, CLASSES)] * len(sp.cases))
		     for n in range(0, len(order) + 1, 1)][:60] for k, sp in splits.items()}

	# the full feature matrix, once: training rows (each change left out of its own
	# history) and every (case, test set) pair of the evaluation sets
	M = Model(costs, CLASSES, families=FAMILIES).fit(train)
	names = list(M.cands)
	tags = [f for f, _ in M.tagged(train[0]["input"], names[0], train[0]["change"])]
	col = {fam: [i for i, f in enumerate(tags) if f == fam] for fam in FAMILIES}
	Xtr = np.array([[v for _, v in M.tagged(c["input"], n, c["change"])] for c, n, _ in M.rows])
	ytr = np.array([y for _, _, y in M.rows])
	per = defaultdict(int)
	for c, _, _ in M.rows:
		per[c["change"]] += 1
	wtr = np.array([1.0 / per[c["change"]] for c, _, _ in M.rows])
	wtr *= len(wtr) / wtr.sum()
	Xev = {k: np.array([[v for _, v in M.tagged(c["input"], n)] for c in sp.cases for n in names])
	       for k, sp in splits.items()}
	print(f"features: {Xtr.shape[0]} training rows, {Xev['test'].shape[0]} test pairs, "
	      f"{time.time() - t0:.0f} s", file=sys.stderr)

	def run_model(families: set[str], seed: int, kind="gbdt", seed_principles=False, redundancy="none"):
		cols = sorted(i for f in families for i in col[f])
		if kind == "gbdt":
			from sklearn.ensemble import HistGradientBoostingClassifier
			clf = HistGradientBoostingClassifier(max_depth=4, learning_rate=0.05, max_iter=250,
							     l2_regularization=1.0, min_samples_leaf=40, random_state=seed)
			clf.fit(Xtr[:, cols], ytr, sample_weight=wtr)
			prob = lambda X: clf.predict_proba(X[:, cols])[:, 1]  # noqa: E731
		elif kind == "lgbm-rank":
			import lightgbm as lgb
			groups, keys = [], []
			for c, _, _ in M.rows:
				if not keys or keys[-1] != id(c):
					keys.append(id(c))
					groups.append(0)
				groups[-1] += 1
			rk = lgb.LGBMRanker(n_estimators=300, learning_rate=0.05, num_leaves=15, min_child_samples=40,
					    random_state=seed, verbose=-1)
			rk.fit(Xtr[:, cols], ytr, group=groups)
			raw = lambda X: rk.predict(X[:, cols])  # noqa: E731
			# a ranker's scores are not chances: map them to the training rows' catch rate
			from sklearn.isotonic import IsotonicRegression
			iso = IsotonicRegression(out_of_bounds="clip").fit(raw(Xtr), ytr)
			prob = lambda X: iso.predict(raw(X))  # noqa: E731
		else:
			from sklearn.linear_model import LogisticRegression
			from sklearn.preprocessing import StandardScaler
			sc = StandardScaler().fit(Xtr[:, cols])
			lr = LogisticRegression(C=0.3, max_iter=3000).fit(sc.transform(Xtr[:, cols]), ytr, sample_weight=wtr)
			prob = lambda X: lr.predict_proba(sc.transform(X[:, cols]))[:, 1]  # noqa: E731
		out = {}
		for k, sp in splits.items():
			p = prob(Xev[k]).reshape(len(sp.cases), len(names))
			scores = [dict(zip(names, row)) for row in p]
			out[k] = [sp.aggregate([M.select(c["input"], rate, sc_, redundancy=redundancy, seed=seed_principles)
						for c, sc_ in zip(sp.cases, scores)]) for rate in RATES]
		return out

	def averaged(families, kind="gbdt", **kw):
		runs = [run_model(families, s, kind, **kw) for s in SEEDS[:args.seeds]]
		return {k: [sum(r[k][i] for r in runs) / len(runs) for i in range(len(RATES))] for k in splits}

	results: dict = {"grid": GRID, "seeds": SEEDS[:args.seeds], "sections": {}}

	def record(section, name, aggs, ref):
		for k, sp in splits.items():
			s = summarize(name, aggs[k], ref[k] if ref else None, blind[k], len(sp.changes))
			results["sections"].setdefault(section, {}).setdefault(k, []).append(s)
			if k == "test":
				print(f"  {section:9s} {name:34s} gain {fmt(s['gain'])} {fmtci(s['gain_ci'])} "
				      f"vs ref {fmt(s['diff'])} {fmtci(s['diff_ci'])}  @20 {fmt(s['at20'])} @25 {fmt(s['at25'])}",
				      file=sys.stderr)

	full_set = set(FAMILIES)
	ref = averaged(full_set)
	if "families" in sections:
		record("families", "all families", ref, None)
		for fam in sorted(FAMILIES):
			record("families", f"without {fam}", averaged(full_set - {fam}), ref)
		base = averaged(BASE)
		record("families", "base only (base rates, edited)", base, ref)
		for fam in sorted(FAMILIES - BASE):
			record("families", f"base + {fam}", averaged(BASE | {fam}), base)
	if "selector" in sections:
		record("selector", "model alone", ref, None)
		record("selector", "principles first (hybrid)", averaged(full_set, seed_principles=True), ref)
		record("selector", "overlap discount", averaged(full_set, redundancy="max"), ref)
	if "models" in sections:
		record("models", "boosted trees", ref, None)
		record("models", "logistic", averaged(full_set, kind="linear"), ref)
		record("models", "LambdaMART ranker", averaged(full_set, kind="lgbm-rank"), ref)
	if "rules" in sections:
		rules = load()
		def principles_aggs(rs):
			p = Principles(rs)
			return {k: [sp.aggregate([p.predict(c["input"])["text"] for c in sp.cases])] for k, sp in splits.items()}
		ref_p = principles_aggs(rules)
		results["sections"]["rules_ref"] = {k: summarize("all rules", ref_p[k], None, blind[k], len(sp.changes))
						     for k, sp in splits.items()}
		for i, r in enumerate(rules):
			aggs = principles_aggs(rules[:i] + rules[i + 1:])
			for k, sp in splits.items():
				# one operating point: the change in bugs caught and in cost, paired
				a, b = ref_p[k][0], aggs[0]
				rng = np.random.default_rng(7)
				d = []
				for _ in range(BOOT):
					w = np.bincount(rng.integers(0, len(sp.changes), len(sp.changes)),
							minlength=len(sp.changes)).astype(float)
					ra, rb = (a * w[:, None]).sum(0), (b * w[:, None]).sum(0)
					if ra[1] and rb[1]:
						d.append(ra[0] / ra[1] - rb[0] / rb[1])
				sa, sb = a.sum(0), b.sum(0)
				results["sections"].setdefault("rules", {}).setdefault(k, []).append({
					"name": r["name"], "bugs_lost": sa[0] / sa[1] - sb[0] / sb[1],
					"bugs_lost_ci": [float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))],
					"cost_saved": sa[2] / sa[3] - sb[2] / sb[3]})
	results["seconds"] = round(time.time() - t0)
	OUT.write_text(json.dumps(results, indent=0) + "\n")
	print(f"done in {results['seconds']} s: {OUT}", file=sys.stderr)


def fmt(v):
	return "  n/a" if v is None else f"{100 * v:+5.1f}"


def fmtci(ci):
	return "" if not ci else f"({100 * ci[0]:+.1f} to {100 * ci[1]:+.1f})"


if __name__ == "__main__":
	main()
