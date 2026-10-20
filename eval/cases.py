#!/usr/bin/env python3
"""Build the eval's cases from the harvested database (design 2.1 to 2.8).

  eval/cases.py                 # data/cases.jsonl and eval/manifest.json
  eval/cases.py --cutoff 2026-08-01

A case is a patchset: every faulty patchset, and every patchset of the random
sample, faulty or not.  It holds what the predictor may see (`input`) and what
the grader needs (`bugs`, `always`, `cost`), kept apart:

  input   subject, commit message, diff, files, test functions touched per script,
          Test-Parameters, Gerrit kind, series, branch
  bugs    the patchset's faults grouped into bugs (bugs.py), each with its
          evidence: for every (script, class) that ran in its build, r sessions
          ran it and f of them failed with the bug
  always  per bug, the chance the sessions that run whatever is predicted
          (trivial's review-ldiskfs, the subtest-change groups) catch it
  cost    the session seconds the patchset's build actually spent, and what
          trivial and the subtest-change sessions cost of it

data/cases.jsonl is rebuilt from data/failures.db and is not committed (it holds
diffs); eval/manifest.json, which is, lists each case's split and tags.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from bugs import group  # noqa: E402
from classes import class_of  # noqa: E402

ROOT = HERE.parent
INFRA = {"node-provisioning", "lustre-initialization"}
CUTOFF = "2026-08-01"       # after the LLM's training cutoff (design 2.8)
SAMPLE_FRACTION = 0.12


def always_run(group_name: str | None) -> bool:
	"""Sessions that run whatever is predicted: trivial's, and the edited tests'."""
	g = group_name or ""
	return g == "review-ldiskfs" or g.endswith("subtest-change")


def build_cases(db: sqlite3.Connection, cutoff: str, fraction: float):
	db.row_factory = sqlite3.Row
	faults = defaultdict(list)
	for f in db.execute("SELECT * FROM failure WHERE class != 'ambient'"):
		faults[(f["change"], f["patchset"])].append(dict(f))
	sample = {(r[0], r[1]) for r in db.execute("SELECT change, patchset FROM sample WHERE rank < ?",
						    (fraction,))}
	keys = sorted(set(faults) | sample)
	patch = {(r["change"], r["patchset"]): dict(r) for r in db.execute("SELECT * FROM patchset")}
	text = {(r["change"], r["patchset"]): dict(r) for r in db.execute("SELECT * FROM patch_text")}
	branch = {(r[0], r[1]): r[2] for r in db.execute(
		"SELECT change, patchset, branch FROM build WHERE branch IS NOT NULL")}
	want = {k[0] for k in keys}
	sessions = defaultdict(list)
	for s in db.execute("SELECT * FROM session WHERE job = 'lustre-reviews'"):
		if s["change"] in want:
			sessions[(s["change"], s["patchset"])].append(dict(s))
	sids = {s["id"] for k in keys for s in sessions.get(k, [])}
	sets = defaultdict(list)
	for t in db.execute("SELECT session_id, suite, status, duration FROM test_set"):
		if t[0] in sids:
			sets[t[0]].append({"suite": t[1], "status": t[2], "duration": t[3]})

	first_day = {}
	for (c, p), fs in faults.items():
		d = min(f["day"] for f in fs if f["day"])
		first_day[c] = min(first_day.get(c, d), d)
	for k in keys:
		for s in sessions.get(k, []):
			d = (s.get("day") or s.get("submission") or "")[:10]
			if d and k[0] not in first_day:
				first_day[k[0]] = d

	for k in keys:
		ss = sessions.get(k, [])
		if not ss:
			continue
		p = patch.get(k, {})
		t = text.get(k, {})
		scripts_run = {x["suite"] for s in ss for x in sets.get(s["id"], [])}
		bugs = group(faults.get(k, []), scripts_run)
		evidence_ran = defaultdict(set)         # (script, class) -> sessions that ran it
		always_ran = defaultdict(set)           # (script, group) -> always-run sessions
		cls = {}
		for s in ss:
			c = class_of(s)
			cls[s["id"]] = c
			for x in sets.get(s["id"], []):
				if x["suite"] in INFRA:
					continue
				if always_run(s["test_group"]):
					always_ran[(x["suite"], s["test_group"])].add(s["id"])
				elif c:
					evidence_ran[(x["suite"], c)].add(s["id"])
		by_unit = defaultdict(list)
		for f in faults.get(k, []):
			by_unit[(f["suite"], f["subtest"], f.get("sig") or "")].append(f)
		out_bugs = []
		for i, b in enumerate(bugs):
			failed = defaultdict(set)
			afailed = defaultdict(set)
			fs = [f for u in b["units"] for f in by_unit[tuple(u)]]
			for f in fs:
				s = next((x for x in ss if x["id"] == f["session_id"]), None)
				if s is None:
					continue
				if always_run(s["test_group"]):
					afailed[(f["suite"], s["test_group"])].add(s["id"])
				elif cls.get(s["id"]):
					failed[(f["suite"], cls[s["id"]])].add(s["id"])
			ev = {f"{sc}|{c}": [len(evidence_ran[(sc, c)] | failed[(sc, c)]), len(failed[(sc, c)])]
			      for (sc, c) in failed}
			miss = 1.0
			for (sc, g), fails in afailed.items():
				r = len(always_ran[(sc, g)] | fails)
				miss *= 1 - len(fails) / r
			out_bugs.append({
				"id": i, "units": [list(u) for u in b["units"]], "scripts": b["scripts"],
				"why": b["why"], "evidence": ev, "always": round(1 - miss, 4),
				"easy": any(f["kind"] == "patch-changes-test" for f in fs),
				"series": any((f.get("series_changes") or 0) > 0 for f in fs),
				"statuses": sorted({f.get("status") or "" for f in fs}),
			})
		# One session per (build, Autotest name): retests are today's cost too,
		# but a prediction is compared with one run of each session.
		first = {}
		for s in sorted(ss, key=lambda s: s.get("submission") or ""):
			first.setdefault((s["build"], s["test_name"] or s["test_group"]), s)
		builds = defaultdict(list)
		for s in first.values():
			builds[s["build"]].append(s)
		main_build = max(builds.values(), key=len)
		cost = {"actual": sum(s["duration"] or 0 for s in main_build),
			"trivial": sum(s["duration"] or 0 for s in main_build if s["test_group"] == "review-ldiskfs"),
			"subtest_change": sum(s["duration"] or 0 for s in main_build
					      if (s["test_group"] or "").endswith("subtest-change")),
			"sessions": len(main_build)}
		day = first_day.get(k[0], "")
		yield {
			"key": f"{k[0]}/{k[1]}", "change": k[0], "patchset": k[1], "day": day,
			"split": "test" if day >= cutoff else "train",
			"faulty": bool(faults.get(k)), "sampled": k in sample,
			"input": {
				"subject": p.get("subject"), "message": t.get("message"), "diff": t.get("diff"),
				"files": json.loads(p["files"]) if p.get("files") else None,
				"scripts": json.loads(p["scripts"]) if p.get("scripts") else None,
				"test_parameters": p.get("test_parameters"), "gerrit_kind": p.get("gerrit_kind"),
				"series": p.get("series"), "series_size": p.get("series_size"),
				"branch": branch.get(k),
			},
			"bugs": out_bugs, "cost": cost,
			"groups_run": sorted({s["test_group"] for s in main_build if s["test_group"]}),
		}


def main() -> None:
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("--db", default=str(ROOT / "data" / "failures.db"))
	ap.add_argument("--out", default=str(ROOT / "data" / "cases.jsonl"))
	ap.add_argument("--manifest", default=str(HERE / "manifest.json"))
	ap.add_argument("--cutoff", default=CUTOFF)
	ap.add_argument("--fraction", type=float, default=SAMPLE_FRACTION)
	args = ap.parse_args()
	db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=60)
	manifest = []
	with open(args.out, "w") as out:
		for c in build_cases(db, args.cutoff, args.fraction):
			out.write(json.dumps(c) + "\n")
			manifest.append({"key": c["key"], "split": c["split"], "faulty": c["faulty"],
					 "sampled": c["sampled"], "bugs": len(c["bugs"]),
					 "hard_bugs": sum(not b["easy"] for b in c["bugs"]),
					 "has_diff": bool(c["input"]["diff"])})
	Path(args.manifest).write_text(json.dumps({"cutoff": args.cutoff, "fraction": args.fraction,
						   "cases": manifest}, indent=0) + "\n")
	n = len(manifest)
	print(f"{n} cases: {sum(m['faulty'] for m in manifest)} faulty, {sum(m['sampled'] for m in manifest)} "
	      f"sampled, {sum(m['split'] == 'test' for m in manifest)} test; "
	      f"{sum(m['bugs'] for m in manifest)} bugs, {sum(m['hard_bugs'] for m in manifest)} hard", file=sys.stderr)


if __name__ == "__main__":
	main()
