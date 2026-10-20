#!/usr/bin/env python3
"""The cost model (design 2.5), from the harvested sessions: eval/costs.json.

  overhead_s        a session's node provisioning and Lustre initialization
  suite_class_s     mean seconds of each script in each class (finished runs)
  suite_s           the same over every class, for a pair never seen
  group_suites      the scripts each review group runs (in most of its sessions)
  group_class       the class each review group runs in
  session_s         mean seconds of a whole session per group
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from classes import class_of  # noqa: E402

INFRA = ("node-provisioning", "lustre-initialization")


def build(db: sqlite3.Connection) -> dict:
	db.row_factory = sqlite3.Row
	cls, group = {}, {}
	sess_s = defaultdict(list)
	for s in db.execute("SELECT * FROM session WHERE job = 'lustre-reviews'"):
		cls[s["id"]] = class_of(dict(s))
		group[s["id"]] = s["test_group"]
		if s["status"] in ("PASS", "FAIL") and s["duration"]:
			sess_s[s["test_group"]].append(s["duration"])
	per = defaultdict(list)
	infra = defaultdict(list)
	runs = defaultdict(Counter)
	sessions_of = Counter()
	seen = set()
	for sid, suite, status, dur in db.execute("SELECT session_id, suite, status, duration FROM test_set"):
		if sid not in cls:
			continue
		g = group[sid]
		if sid not in seen:
			seen.add(sid)
			sessions_of[g] += 1
		runs[g][suite] += 1
		if not dur or status not in ("PASS", "FAIL"):
			continue
		if suite in INFRA:
			infra[suite].append(dur)
		elif cls[sid]:
			per[(suite, cls[sid])].append(dur)
	by_suite = defaultdict(list)
	for (suite, _), ds in per.items():
		by_suite[suite] += ds
	group_class = {}
	for sid, g in group.items():
		if g and cls.get(sid):
			group_class.setdefault(g, Counter())[cls[sid]] += 1
	return {
		"overhead_s": round(sum(mean(infra[s]) for s in INFRA if infra[s])),
		"suite_class_s": {f"{s}|{c}": round(mean(ds)) for (s, c), ds in sorted(per.items()) if len(ds) >= 3},
		"suite_s": {s: round(mean(ds)) for s, ds in sorted(by_suite.items())},
		"group_suites": {g: sorted(s for s, n in runs[g].items() if s not in INFRA and n >= 0.5 * sessions_of[g])
				 for g in sorted(runs) if g and g.startswith("review") and sessions_of[g] >= 20},
		"group_class": {g: c.most_common(1)[0][0] for g, c in sorted(group_class.items())},
		"session_s": {g: round(mean(ds)) for g, ds in sorted(sess_s.items()) if g and len(ds) >= 20},
	}


def main() -> None:
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("--db", default=str(HERE.parent / "data" / "failures.db"))
	ap.add_argument("--out", default=str(HERE / "costs.json"))
	args = ap.parse_args()
	d = build(sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=60))
	Path(args.out).write_text(json.dumps(d, indent=1, sort_keys=True) + "\n")
	print(f"overhead {d['overhead_s']} s, {len(d['suite_class_s'])} script/class means, "
	      f"{len(d['group_suites'])} groups", file=sys.stderr)


if __name__ == "__main__":
	main()
