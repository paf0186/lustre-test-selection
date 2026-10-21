#!/usr/bin/env python3
"""The learning curve (EXPERIMENTS.md, E3): does more history help?

Trains the reference model on the training months of the last N months only and
scores each on the same evaluation set, the test months from --eval-since on, as
gain over the patch-blind line (itself ranked on the same N months).

  .venv/bin/python eval/learning.py --months 6 --months 12 --months 18 --months 24
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from classes import CLASSES  # noqa: E402
from predictors import ranked  # noqa: E402
from wowy import REF_FAMILIES, SEEDS, Fold, Split, month, summarize  # noqa: E402

ROOT = HERE.parent


def main() -> None:
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("--cases", default=str(ROOT / "data" / "cases.jsonl"))
	ap.add_argument("--months", type=int, action="append")
	ap.add_argument("--eval-since", default="2026-03-20")
	ap.add_argument("--ref", default="lgbm-rank")
	args = ap.parse_args()
	cases = [json.loads(x) for x in open(args.cases)]
	costs = json.loads((HERE / "costs.json").read_text())
	evals = [c for c in cases if c["split"] == "test" and c["day"] >= args.eval_since]
	last = max(month(c["day"]) for c in cases)
	split = Split(evals, costs)
	out = []
	for n in sorted(args.months or [6, 12, 18, 24]):
		train = [c for c in cases if c["split"] == "train" and last - month(c["day"]) < n]
		f = Fold(train, evals, costs)
		blind = [split.aggregate([ranked.prediction(f.order, k, CLASSES)] * len(evals)) for k in range(61)]
		runs = [[split.aggregate(row) for row in f.texts(REF_FAMILIES, args.ref, s)] for s in SEEDS]
		aggs = [sum(r[i] for r in runs) / len(runs) for i in range(len(runs[0]))]
		s = summarize(f"{n} months", aggs, None, blind, len(split.changes))
		s.update({"months": n, "train_cases": len(train), "train_faulty": sum(c["faulty"] for c in train)})
		out.append(s)
		print(f"  {n:3d} months: {len(train)} training cases ({s['train_faulty']} faulty), gain over blind "
		      f"{100 * s['gain']:+.1f} ({100 * s['gain_ci'][0]:+.1f} to {100 * s['gain_ci'][1]:+.1f})", file=sys.stderr)
	(HERE / "results" / "learning.json").write_text(json.dumps(out, indent=0) + "\n")


if __name__ == "__main__":
	main()
