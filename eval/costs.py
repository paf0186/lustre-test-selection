#!/usr/bin/env python3
"""The cost model (design 2.5), from the harvested sessions: eval/costs.json.

  overhead_s        a session's node provisioning and Lustre initialization
  suite_class_s     mean seconds of each script in each class (finished runs)
  suite_s           the same over every class, for a pair never seen
  group_suites      the scripts each review group runs (in most of its sessions)
  group_class       the class each review group runs in
  matrix            the (script, class) pairs the review groups ran on at least half the
                    full review builds (those running review-dne-part-1) of the last
                    MATRIX_DAYS: what the blind base may pick from; not custom, full or
                    interop sessions, nor a group Autotest runs only now and then
  matrix_share      each pair's share of those builds; matrix_groups, the groups that pass
  session_s         mean seconds of a whole session per group
  runs_per_build    how often each script runs in each class per review build today, and
  secs_per_build    the session seconds it takes per build; the sessions that run whatever
                    is selected (review-ldiskfs, subtest-change) are keyed by group, not class
"""
from __future__ import annotations

import argparse
import datetime as dt
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


SINCE = "2026-03-20"   # today's matrix: older sessions ran a different one
MATRIX_DAYS = 30


def build(db: sqlite3.Connection, since: str = SINCE) -> dict:
	db.row_factory = sqlite3.Row
	cls, group, build_of = {}, {}, {}
	per_build = Counter()
	secs_build = Counter()
	subtests = defaultdict(list)
	sess_s = defaultdict(list)
	last = db.execute("SELECT max(day) FROM session WHERE job = 'lustre-reviews'").fetchone()[0]
	recent_from = (dt.date.fromisoformat(last[:10]) - dt.timedelta(days=MATRIX_DAYS)).isoformat()
	recent = set()
	for s in db.execute("SELECT * FROM session WHERE job = 'lustre-reviews' AND day >= ?", (since,)):
		if s["day"] >= recent_from:
			recent.add(s["id"])
		cls[s["id"]] = class_of(dict(s))
		group[s["id"]] = s["test_group"]
		build_of[s["id"]] = s["build"]
		if s["status"] in ("PASS", "FAIL") and s["duration"]:
			sess_s[s["test_group"]].append(s["duration"])
	per = defaultdict(list)
	infra = defaultdict(list)
	runs = defaultdict(Counter)
	pair_builds: dict[tuple, set] = defaultdict(set)
	group_builds: dict[str, set] = defaultdict(set)
	sessions_of = Counter()
	seen = set()
	for sid, suite, status, dur, total in db.execute(
			"SELECT session_id, suite, status, duration, sub_total FROM test_set"):
		if sid not in cls:
			continue
		g = group[sid]
		always = g == "review-ldiskfs" or (g or "").endswith("subtest-change")
		if sid in recent and (g or "").startswith("review") and not g.endswith("subtest-change") and cls[sid]:
			pair_builds[(suite, cls[sid])].add(build_of[sid])
			group_builds[g].add(build_of[sid])
		if sid in recent:
			if sid not in seen:
				seen.add(sid)
				sessions_of[g] += 1
			runs[g][suite] += 1
		if total and status in ("PASS", "FAIL"):
			subtests[suite].append(total)
		if suite not in INFRA and (cls[sid] or always):
			# The sessions that run whatever is selected are their own rows.
			where = g if always else cls[sid]
			per_build[(suite, where)] += 1
			secs_build[(suite, where)] += dur or 0
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
	group_suites = {g: sorted(s for s, n in runs[g].items() if s not in INFRA and n >= 0.5 * sessions_of[g])
			for g in sorted(runs) if g and g.startswith("review") and sessions_of[g] >= 20}
	group_cls = {g: c.most_common(1)[0][0] for g, c in sorted(group_class.items())}
	fb = group_builds.get("review-dne-part-1", set())
	full = len(fb) or 1
	share = {f"{s}|{c}": len(b & fb) / full for (s, c), b in pair_builds.items() if s not in INFRA}
	return {
		"overhead_s": round(sum(mean(infra[s]) for s in INFRA if infra[s])),
		"suite_class_s": {f"{s}|{c}": round(mean(ds)) for (s, c), ds in sorted(per.items()) if len(ds) >= 3},
		"suite_s": {s: round(mean(ds)) for s, ds in sorted(by_suite.items())},
		"group_suites": group_suites,
		"group_class": group_cls,
		"matrix": sorted(k for k, v in share.items() if v >= 0.5),
		"matrix_share": {k: round(v, 3) for k, v in sorted(share.items())},
		"matrix_groups": sorted(g for g, b in group_builds.items() if len(b & fb) >= 0.5 * full),
		"matrix_since": recent_from,
		"session_s": {g: round(mean(ds)) for g, ds in sorted(sess_s.items()) if g and len(ds) >= 20},
		"since": since,
		"suite_subtests": {s: round(mean(v)) for s, v in sorted(subtests.items()) if len(v) >= 3},
		"review_builds": db.execute("SELECT count(*) FROM build WHERE job = 'lustre-reviews' AND first_day >= ?",
					    (since,)).fetchone()[0],
		"runs_per_build": {f"{s}|{c}": round(n / max(1, len(set(build_of.values()))), 3)
				   for (s, c), n in sorted(per_build.items())},
		"secs_per_build": {f"{s}|{c}": round(n / max(1, len(set(build_of.values()))))
				   for (s, c), n in sorted(secs_build.items())},
	}


def main() -> None:
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("--db", default=str(HERE.parent / "data" / "failures.db"))
	ap.add_argument("--out", default=str(HERE / "costs.json"))
	ap.add_argument("--since", default=SINCE, help="sessions from this day: today's matrix")
	args = ap.parse_args()
	d = build(sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=60), args.since)
	Path(args.out).write_text(json.dumps(d, indent=1, sort_keys=True) + "\n")
	print(f"overhead {d['overhead_s']} s, {len(d['suite_class_s'])} script/class means, "
	      f"{len(d['group_suites'])} groups", file=sys.stderr)


if __name__ == "__main__":
	main()
