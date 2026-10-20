#!/usr/bin/env python3
"""Sample the training set's bugs a selection misses (and some it catches), for a
person or agent to judge whether reading the patch could have predicted them.

  eval/audit/miss_sample.py [--base 6] [--misses 40] [--hits 10] > eval/audit/misses_sample.jsonl

The selection is the principles on the first --base sets of the patch-blind order.
Training set only: the test set stays unread."""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from classes import CLASSES  # noqa: E402
from grade import grade  # noqa: E402
from predictors import ranked  # noqa: E402
from predictors.principles import OnBlindBase  # noqa: E402


def main() -> None:
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("--cases", default=str(HERE.parent.parent / "data" / "cases.jsonl"))
	ap.add_argument("--base", type=int, default=6)
	ap.add_argument("--misses", type=int, default=40)
	ap.add_argument("--hits", type=int, default=10)
	ap.add_argument("--seed", type=int, default=20261020)
	args = ap.parse_args()
	cases = [json.loads(x) for x in open(args.cases)]
	costs = json.loads((HERE.parent / "costs.json").read_text())
	train = [c for c in cases if c["split"] == "train"]
	pred = OnBlindBase(ranked.rank(train, costs, CLASSES), args.base)
	miss, hit = [], []
	for c in train:
		if not c["faulty"]:
			continue
		p = pred.predict(c["input"])
		g = grade(c, p["text"], costs)
		for b, chance in zip(c["bugs"], g["bugs"]):
			row = {"key": c["key"], "change": c["change"], "patchset": c["patchset"],
			       "subject": c["input"]["subject"], "files": (c["input"]["files"] or [])[:40],
			       "n_files": len(c["input"]["files"] or []), "selection": p["text"], "why": p.get("why"),
			       "narrow": pred.narrow(c["input"]), "caught_chance": chance,
			       "bug": {"units": b["units"][:12], "n_units": len(b["units"]), "scripts": b["scripts"],
				       "easy": b["easy"], "series": b["series"], "statuses": b["statuses"],
				       "evidence": b["evidence"], "always": b["always"]}}
			(miss if chance < 0.5 else hit if chance >= 0.9 else []).append(row)
	rng = random.Random(args.seed)
	# one bug per change, so a change with many bugs does not fill the sample
	for rows, n, tag in ((miss, args.misses, "miss"), (hit, args.hits, "hit")):
		rng.shuffle(rows)
		seen = set()
		for r in rows:
			if r["change"] in seen:
				continue
			seen.add(r["change"])
			print(json.dumps({"kind": tag, **r}))
			n -= 1
			if not n:
				break


if __name__ == "__main__":
	main()
