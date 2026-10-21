#!/usr/bin/env python3
"""Build the eval's cases from the harvested database (design 2.1 to 2.8).

  eval/cases.py                 # data/cases.jsonl and eval/manifest.json
  eval/cases.py --every 3        # hold out every third month (the default)
  eval/cases.py --every 0 --cutoff 2026-08-01   # hold out only what follows the cutoff

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
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from bugs import group, signature  # noqa: E402
from classes import CLASSES, class_of  # noqa: E402

CLASS_KEYS = {k: v[0] for k, v in CLASSES.items()}

ROOT = HERE.parent
INFRA = {"node-provisioning", "lustre-initialization"}
CUTOFF = "2026-08-01"       # after the LLM's training cutoff (design 2.8)
EVERY = 3                   # hold out every third month


def held_out(day: str, latest: str, every: int, cutoff: str) -> bool:
	"""Test months: every `every`th calendar month counting back from the latest,
	so the test set spans the whole period rather than only what is new now (a
	feature being written draws its own failures); with every 0, the months after
	the cutoff."""
	if not every:
		return day >= cutoff
	m = int(day[:4]) * 12 + int(day[5:7])
	last = int(latest[:4]) * 12 + int(latest[5:7])
	return (last - m) % every == 0
SAMPLE_FRACTION = 0.12


def always_run(group_name: str | None) -> bool:
	"""Sessions that run whatever is predicted: trivial's, and the edited tests'."""
	g = group_name or ""
	return g == "review-ldiskfs" or g.endswith("subtest-change")


def merge_same_catch(bugs: list[dict]) -> list[dict]:
	"""Bugs of the same scripts that failed in exactly the same configurations are
	caught by exactly the same selections: no prediction can tell them apart, so
	they count as one (a script that broke fails hundreds of subtests)."""
	groups: dict[tuple, list[dict]] = {}
	for b in bugs:
		groups.setdefault((tuple(b["scripts"]), tuple(sorted(b["evidence"])), b["always"] > 0), []).append(b)
	out = []
	for bs in groups.values():
		if len(bs) == 1:
			out.append(bs[0])
			continue
		ev = {}
		for k in bs[0]["evidence"]:
			ev[k] = max((b["evidence"][k] for b in bs), key=lambda rf: rf[1] / rf[0] if rf[0] else 0)
		aev = {}
		for b in bs:
			for k, rf in b.get("always_evidence", {}).items():
				if k not in aev or rf[1] / rf[0] > aev[k][1] / aev[k][0]:
					aev[k] = rf
		out.append({**bs[0], "units": [u for b in bs for u in b["units"]], "evidence": ev,
			    "always_evidence": aev,
			    "always": max(b["always"] for b in bs), "why": "same-catch",
			    "easy": any(b["easy"] for b in bs), "series": any(b["series"] for b in bs),
			    "statuses": sorted({x for b in bs for x in b["statuses"]}), "merged": len(bs)})
	for i, b in enumerate(out):
		b["id"] = i
	return out


def build_cases(db: sqlite3.Connection, cutoff: str, fraction: float, every: int = EVERY):
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

	# What CI already said about the change's earlier patchsets: known when the
	# next one is uploaded, so the predictor may use it.
	sess_class = {}
	for kk, ss0 in sessions.items():
		for s0 in ss0:
			sess_class[s0["id"]] = class_of(s0)
	caught_at = defaultdict(lambda: defaultdict(set))       # change -> patchset -> keys
	for (c0, p0), fs in faults.items():
		for f in fs:
			c1 = sess_class.get(f["session_id"])
			if c1:
				caught_at[c0][p0].add(f"{f['suite']}|{c1}")
	lifetimes: dict[int, dict[tuple, set]] = defaultdict(lambda: defaultdict(set))
	for (c0, p0), fs in faults.items():
		for f in fs:
			lifetimes[c0][(f["suite"], f["subtest"], signature(f))].add(p0)
	inherited = Counter()
	latest = max(first_day.values())
	for k in keys:
		ss = sessions.get(k, [])
		if not ss:
			continue
		earlier = {p0: v for p0, v in caught_at.get(k[0], {}).items() if p0 < k[1]}
		history = {"caught": sorted(set().union(*earlier.values())) if earlier else [],
			   "last_caught": sorted(earlier[max(earlier)]) if earlier else [],
			   "faulty_patchsets": len(earlier)}
		p = patch.get(k, {})
		t = text.get(k, {})
		scripts_run = {x["suite"] for s in ss for x in sets.get(s["id"], [])}
		# A bug the series inherited from another change is that change's: it is
		# counted on the change that introduced it, not on every one built on it.
		mine = []
		for f in faults.get(k, []):
			origin = f.get("series_origin")
			if isinstance(origin, str):
				try:
					origin = json.loads(origin)
				except ValueError:
					origin = None
			if origin and origin.get("change") not in (None, k[0]):
				inherited[k] += 1
				continue
			mine.append(f)
		bugs = group(mine, scripts_run,
			     {u: frozenset(v) for u, v in lifetimes.get(k[0], {}).items()})
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
		for f in mine:
			by_unit[(f["suite"], f["subtest"], signature(f))].append(f)
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
			aev = {}
			for (sc, g), fails in afailed.items():
				r = len(always_ran[(sc, g)] | fails)
				if g.endswith("subtest-change"):
					# Each such session runs some of the edited subtests, and every one
					# of them runs again on the next build: the bug is missed only if
					# all of them miss it.
					miss *= (1 - len(fails) / r) ** r
				else:
					miss *= 1 - len(fails) / r
				aev[f"{sc}|{g}"] = [r, len(fails)]
			out_bugs.append({
				"id": i, "units": [list(u) for u in b["units"]], "scripts": b["scripts"],
				"why": b["why"], "evidence": ev, "always": round(1 - miss, 4), "always_evidence": aev,
				"easy": any(f["kind"] == "patch-changes-test" for f in fs),
				"series": any((f.get("series_changes") or 0) > 0 for f in fs),
				"statuses": sorted({f.get("status") or "" for f in fs}),
			})
		out_bugs = merge_same_catch(out_bugs)
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
		# Every test set the build ran: a test set that ran and caught nothing is a
		# negative example; one that never ran says nothing.
		ran = set()
		for s in main_build:
			c = cls.get(s["id"])
			if not c or always_run(s["test_group"]):
				continue
			if CLASS_KEYS.get(c) is None:
				ran.add(f"group:{s['test_group']}")
				continue
			for x in sets.get(s["id"], []):
				if x["suite"] not in INFRA:
					ran.add(f"{x['suite']}|{c}")
		day = first_day.get(k[0], "")
		yield {
			"key": f"{k[0]}/{k[1]}", "change": k[0], "patchset": k[1], "day": day,
			"split": "test" if held_out(day, latest, every, cutoff) else "train",
			"recent": day >= cutoff,
			"faulty": bool(out_bugs), "sampled": k in sample, "inherited": inherited[k],
			"input": {
				"key": f"{k[0]}/{k[1]}", "subject": p.get("subject"), "message": t.get("message"), "diff": t.get("diff"),
				"files": json.loads(p["files"]) if p.get("files") else None,
				"scripts": json.loads(p["scripts"]) if p.get("scripts") else None,
				"test_parameters": p.get("test_parameters"), "gerrit_kind": p.get("gerrit_kind"),
				"series": p.get("series"), "series_size": p.get("series_size"),
				"branch": branch.get(k), "history": history,
			},
			"bugs": out_bugs, "cost": cost,
			"groups_run": sorted({s["test_group"] for s in main_build if s["test_group"]}),
			"ran": sorted(ran),
		}


def main() -> None:
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("--db", default=str(ROOT / "data" / "failures.db"))
	ap.add_argument("--out", default=str(ROOT / "data" / "cases.jsonl"))
	ap.add_argument("--manifest", default=str(HERE / "manifest.json"))
	ap.add_argument("--cutoff", default=CUTOFF)
	ap.add_argument("--every", type=int, default=EVERY, help="hold out every Nth month; 0: after the cutoff")
	ap.add_argument("--fraction", type=float, default=SAMPLE_FRACTION)
	args = ap.parse_args()
	db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=60)
	manifest = []
	with open(args.out, "w") as out:
		for c in build_cases(db, args.cutoff, args.fraction, args.every):
			out.write(json.dumps(c) + "\n")
			manifest.append({"key": c["key"], "split": c["split"], "recent": c["recent"], "faulty": c["faulty"],
					 "sampled": c["sampled"], "bugs": len(c["bugs"]),
					 "hard_bugs": sum(not b["easy"] for b in c["bugs"]),
					 "has_diff": bool(c["input"]["diff"])})
	Path(args.manifest).write_text(json.dumps({"cutoff": args.cutoff, "every": args.every,
						   "fraction": args.fraction,
						   "cases": manifest}, indent=0) + "\n")
	n = len(manifest)
	print(f"{n} cases: {sum(m['faulty'] for m in manifest)} faulty, {sum(m['sampled'] for m in manifest)} "
	      f"sampled, {sum(m['split'] == 'test' for m in manifest)} test; "
	      f"{sum(m['bugs'] for m in manifest)} bugs, {sum(m['hard_bugs'] for m in manifest)} hard", file=sys.stderr)


if __name__ == "__main__":
	main()
