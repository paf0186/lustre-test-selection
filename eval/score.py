#!/usr/bin/env python3
"""Score predictors on the eval (design 2.6) and log the results.

  eval/score.py                         # the baselines, train and test
  eval/score.py --predictor table-0.2 --split test
  eval/score.py --no-log                # print only

Catch rates are over faulty cases, cost over the random sample's cases.  Each
result is one line of eval/results/log.jsonl, from which eval/report.py draws
the page; the page is redrawn at the end of every run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from grade import full, grade  # noqa: E402
from predictors.baselines import Edited, Table, Trivial  # noqa: E402
from predictors.principles import Principles, PrinciplesAndHistory  # noqa: E402

ROOT = HERE.parent
LOG = HERE / "results" / "log.jsonl"
BOOT = 1000


def predictors() -> dict:
	out = {"full": None}
	for p in (Trivial(), Edited(), Table(0.5), Table(0.3), Table(0.2), Table(0.1), Table(0.05),
		  Principles(), PrinciplesAndHistory(Table(0.3)), PrinciplesAndHistory(Table(0.1))):
		out[p.name] = p
	return out


def load(path: Path) -> list[dict]:
	return [json.loads(x) for x in path.open()]


def metrics(rows: list[dict]) -> dict:
	"""rows: (case, grade, full grade) for one split."""
	m = defaultdict(float)
	per_change = defaultdict(int)
	for case, g, f in rows:
		if case["faulty"]:
			per_change[case["change"]] += g["n_bugs"]
	for case, g, f in rows:
		if case["faulty"] and g["n_bugs"]:
			w = 1 / per_change[case["change"]]           # each change counts once
			m["w_caught"] += w * g["caught"]
			m["w_full"] += w * f["caught"]
			m["w_hard_caught"] += w * sum(p for b, p in zip(case["bugs"], g["bugs"]) if not b["easy"])
			m["w_hard_full"] += w * sum(p for b, p in zip(case["bugs"], f["bugs"]) if not b["easy"])
			m["faulty"] += 1
			m["bugs"] += g["n_bugs"]
			m["caught"] += g["caught"]
			m["full_caught"] += f["caught"]
			m["all_caught"] += g["all_caught"]
			m["any_caught"] += g["any_caught"]
			for b, p, pf in zip(case["bugs"], g["bugs"], f["bugs"]):
				for t in ("easy" if b["easy"] else "hard",) + (("series",) if b["series"] else ()):
					m[f"{t}_bugs"] += 1
					m[f"{t}_caught"] += p
					m[f"{t}_full"] += pf
		if case["sampled"]:
			m["sampled"] += 1
			m["cost_s"] += g["cost_s"]
			m["actual_s"] += g["actual_s"]
	out = {"faulty": int(m["faulty"]), "bugs": int(m["bugs"]), "sampled": int(m["sampled"])}
	if m["bugs"]:
		out["recall"] = m["caught"] / m["bugs"]
		out["bug_recall_vs_full"] = m["caught"] / m["full_caught"] if m["full_caught"] else None
		out["recall_vs_full"] = m["w_caught"] / m["w_full"] if m["w_full"] else None
		out["hard_change_vs_full"] = m["w_hard_caught"] / m["w_hard_full"] if m["w_hard_full"] else None
		out["all_caught"] = m["all_caught"] / m["faulty"]
		out["any_caught"] = m["any_caught"] / m["faulty"]
		for t in ("easy", "hard", "series"):
			if m[f"{t}_bugs"]:
				out[f"{t}_bugs"] = int(m[f"{t}_bugs"])
				out[f"{t}_vs_full"] = m[f"{t}_caught"] / m[f"{t}_full"] if m[f"{t}_full"] else None
	if m["actual_s"]:
		out["cost_share"] = m["cost_s"] / m["actual_s"]
		out["hours_per_build"] = m["cost_s"] / m["sampled"] / 3600
		out["actual_hours_per_build"] = m["actual_s"] / m["sampled"] / 3600
	return out


def bootstrap(rows: list[dict], keys=("recall_vs_full", "bug_recall_vs_full", "all_caught", "cost_share"),
	      seed: int = 1) -> dict:
	"""95% intervals, resampling changes (a change's patchsets are not independent)."""
	by_change = defaultdict(list)
	for r in rows:
		by_change[r[0]["change"]].append(r)
	changes = sorted(by_change)
	rng = random.Random(seed)
	vals = defaultdict(list)
	for _ in range(BOOT):
		pick = [r for c in rng.choices(changes, k=len(changes)) for r in by_change[c]]
		m = metrics(pick)
		for k in keys:
			if m.get(k) is not None:
				vals[k].append(m[k])
	out = {}
	for k, v in vals.items():
		v.sort()
		out[k] = [v[int(0.025 * len(v))], v[int(0.975 * len(v)) - 1]]
	return out


def run(name, pred, cases, costs, split):
	train = [c for c in cases if c["split"] == "train"]
	t0 = time.time()
	if pred is not None:
		pred.fit(train)
	rows, meta = [], defaultdict(float)
	for c in cases:
		if split != "all" and c["split"] != split:
			continue
		f = full(c)
		if pred is None:
			g = f
		else:
			p = pred.predict(c["input"])
			for k, v in (p.get("meta") or {}).items():
				if isinstance(v, (int, float)):
					meta[k] += v
			g = grade(c, p["text"], costs)
		rows.append((c, g, f))
	m = metrics(rows)
	m["ci"] = bootstrap(rows)
	m["llm"] = dict(meta)
	m["seconds"] = round(time.time() - t0, 1)
	return m


def curve(cases, costs, label, when) -> list[dict]:
	"""The patch-blind baseline: rank on the training set, then score every
	prefix of the order on both sets, until it costs more than today."""
	from classes import CLASSES
	from predictors import ranked
	train = [c for c in cases if c["split"] == "train"]
	order = ranked.rank(train, costs, CLASSES)
	out = []
	for split in ("train", "test"):
		rows_all = [c for c in cases if c["split"] == split]
		fulls = [full(c) for c in rows_all]
		points = []
		for n in range(len(order) + 1):
			text = ranked.prediction(order, n, CLASSES)
			rows = [(c, grade(c, text, costs), f) for c, f in zip(rows_all, fulls)]
			m = metrics(rows)
			points.append({"n": n, "cost_share": m.get("cost_share"), "recall_vs_full": m.get("recall_vs_full"),
				       "hard_change_vs_full": m.get("hard_change_vs_full"),
				       "series_vs_full": m.get("series_vs_full"), "easy_vs_full": m.get("easy_vs_full"),
				       "all_caught": m.get("all_caught"), "pick": order[n - 1]["pick"] if n else "trivial"})
			if (m.get("cost_share") or 0) > 1.0:
				break
		out.append({"when": when, "label": label, "predictor": "ranked", "split": split, "curve": points,
			    "title": "patch-blind, best value first",
			    "about": "the same tests for every patch: scripts and configurations ranked on the training "
				     "set by bugs caught per second, added in that order",
			    "order": [{k: v for k, v in st.items()} for st in order]})
	return out


def value(cases, costs, when, label) -> dict:
	"""Each script in each configuration on its own: what it costs per build today,
	how many of the patches' bugs it caught (each change counting once) and how
	many only it caught, over every faulty case (a description of today's matrix,
	not a predictor: no split)."""
	per_change = defaultdict(int)
	for c in cases:
		if c["faulty"]:
			per_change[c["change"]] += len(c["bugs"])
	caught = defaultdict(float)
	only = defaultdict(float)
	bugs = defaultdict(float)
	for c in cases:
		if not c["faulty"]:
			continue
		w = 1 / per_change[c["change"]]
		for b in c["bugs"]:
			hit = [k for k, (r, f) in list(b["evidence"].items()) + list(b.get("always_evidence", {}).items()) if f]
			for k in hit:
				caught[k] += w
				bugs[k] += 1
			if len(hit) == 1:
				only[hit[0]] += w
	changes = len(per_change)
	rows = []
	total_h = sum(costs["secs_per_build"].values()) / 3600
	for k, runs in costs["runs_per_build"].items():
		hours = costs["secs_per_build"].get(k, 0) / 3600
		share_b, share_h = caught[k] / changes, hours / total_h
		rows.append({"key": k, "runs_per_build": runs, "hours_per_build": round(hours, 2),
			     "share_hours": round(share_h, 4), "bugs": int(bugs[k]),
			     "share_bugs": round(share_b, 4), "share_only": round(only[k] / changes, 4),
			     "value": round(share_b / share_h, 2) if share_h else None})
	rows.sort(key=lambda r: -(r["value"] or 0))
	return {"when": when, "label": label, "predictor": "value", "split": "all", "changes": changes,
		"rows": rows}


def main() -> None:
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("--cases", default=str(ROOT / "data" / "cases.jsonl"))
	ap.add_argument("--costs", default=str(HERE / "costs.json"))
	ap.add_argument("--predictor", action="append", help="name (default: every baseline)")
	ap.add_argument("--split", action="append", choices=["train", "test", "all"])
	ap.add_argument("--label", default="baseline", help="what this run is: a round, a version")
	ap.add_argument("--no-log", action="store_true")
	args = ap.parse_args()
	cases = load(Path(args.cases))
	costs = json.loads(Path(args.costs).read_text())
	preds = predictors()
	names = args.predictor or list(preds)
	when = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
	for name in names:
		for split in args.split or ["train", "test"]:
			m = run(name, preds[name], cases, costs, split)
			p = preds[name]
			rec = {"when": when, "label": args.label, "predictor": name, "split": split,
			       "title": getattr(p, "title", "today's matrix") if p else "today's matrix",
			       "about": getattr(p, "about", "") if p else "every review session Autotest runs today",
			       **m}
			print(json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in rec.items()
					  if k not in ("ci", "llm")}))
			if not args.no_log:
				LOG.parent.mkdir(parents=True, exist_ok=True)
				with LOG.open("a") as f:
					f.write(json.dumps(rec) + "\n")
	if not args.predictor:
		v = value(cases, costs, when, args.label)
		if not args.no_log:
			with LOG.open("a") as f:
				f.write(json.dumps(v) + "\n")
		for rec in curve(cases, costs, args.label, when):
			pts = rec["curve"]
			print(json.dumps({"predictor": "ranked", "split": rec["split"], "steps": len(pts) - 1,
					  "at": {f"{p['cost_share']:.2f}": round(p["recall_vs_full"], 3) for p in pts[::8]}}))
			if not args.no_log:
				with LOG.open("a") as f:
					f.write(json.dumps(rec) + "\n")
	if not args.no_log and (HERE / "report.py").exists():
		subprocess.run([sys.executable, str(HERE / "report.py")], check=False)


if __name__ == "__main__":
	main()
