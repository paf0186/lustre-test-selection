#!/usr/bin/env python3
"""What CI policy changes would cost in patch faults hidden from review
(plans/test-time in lustre_design_docs: P1, P2, P3, P5).

  eval/policy_cost.py --start 2026-07-19 --end 2026-10-18 -o policy-cost.json

Bugs are the eval's (cases.jsonl: grouped by bugs.py, the patch's own, series
inheritance dropped).  A session catches a bug when one of its subtests failed
there; it ran the bug when it ran one of the bug's suites.

P1  review-dne* groups on one server distro per build (or per change); also one
    server filesystem per build for the suites both ldiskfs and ZFS parts run
P1s the doubled review-dne groups (DOUBLED) each fixed to one of the two server
    distros they run, the older or the newer, every build: all 512 assignments
P3  review-ldiskfs on one client distro per build (or per change)
P2  no testing of NO_CODE_CHANGE and TRIVIAL_REBASE* patchsets
P5  a retest re-runs only the test sets that did not pass

For P1 and P3 the distro is drawn uniformly from those the build ran, either
independently per group or once for the whole build; the expected miss is the
chance the draw avoids every distro that caught the bug, given nothing outside
the rotated groups caught it.  Rotation by change keeps the draw for all the
change's patchsets; by build, each patchset draws again.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.request
from collections import defaultdict
from math import prod
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import harvest  # noqa: E402

INFRA = {"node-provisioning", "lustre-initialization"}
REBASE = {"TRIVIAL_REBASE", "TRIVIAL_REBASE_WITH_MESSAGE_UPDATE"}
# The review-dne groups that run two server distros on every build (2026-10).
DOUBLED = ("review-dne-part-1", "review-dne-part-4", "review-dne-part-6", "review-dne-part-8",
	   "review-dne-zfs-part-1", "review-dne-zfs-part-2", "review-dne-zfs-part-3", "review-dne-zfs-part-4",
	   "review-dne-zfs-part-6")


def load(db_path, cases_path):
	cases = [c for c in map(json.loads, open(cases_path)) if c["faulty"]]
	want = {c["change"] for c in cases}
	db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
	db.row_factory = sqlite3.Row
	sessions = defaultdict(list)                # change -> sessions
	for s in db.execute("SELECT id, change, patchset, build, test_group, test_name, server_distro, client_distro, "
			    "server_fs, status, submission, day FROM session WHERE job = 'lustre-reviews'"):
		if s["change"] in want:
			s = dict(s)
			sessions[s["change"]].append(s)
	sids = {s["id"] for ss in sessions.values() for s in ss}
	sets = defaultdict(list)
	for t in db.execute("SELECT session_id, suite, status, failed FROM test_set"):
		if t[0] in sids and t[1] not in INFRA:
			sets[t[0]].append({"suite": t[1], "status": t[2], "failed": set(json.loads(t[3])) if t[3] else set()})
	fails = defaultdict(set)                    # session -> (suite, subtest) of patch faults
	for f in db.execute("SELECT session_id, suite, subtest FROM failure"):
		if f[0] in sids:
			fails[f[0]].add((f[1], f[2]))
	kind = {(r[0], r[1]): r[2] for r in db.execute("SELECT change, patchset, gerrit_kind FROM patchset")}
	series = {(r[0], r[1]): r[2] for r in db.execute("SELECT change, patchset, series_size FROM patchset")}
	return cases, sessions, sets, fails, kind, series


class Change:
	"""One change's sessions, and which of them ran and caught a set of subtests."""

	def __init__(self, ss, sets, fails):
		self.ss, self.sets, self.fails = ss, sets, fails

	def caught(self, s, subs):
		if self.fails[s["id"]] & subs:
			return True
		return any((x["suite"], st) in subs for x in self.sets[s["id"]] for st in x["failed"])

	def ran(self, s, suites):
		return any(x["suite"] in suites for x in self.sets[s["id"]])


def rotation_miss(sessions, ch, subs, suites, rotated, attr):
	"""Chance one bug is missed when the rotated groups run one value of attr:
	(independent per group, one draw for all).  None when something outside the
	rotated groups caught it, or the rotated groups never ran it."""
	ran, got = defaultdict(set), defaultdict(set)
	for s in sessions:
		hit = ch.caught(s, subs)
		if not rotated(s["test_group"]):
			if hit:
				return None
			continue
		if ch.ran(s, suites) or hit:
			ran[s["test_group"]].add(s[attr])
			if hit:
				got[s["test_group"]].add(s[attr])
	if not any(got.values()):
		return None
	indep = prod(1 - len(got[g]) / len(ran[g]) for g in ran)
	every = set().union(*ran.values())
	caught_by = set().union(*got.values())
	one = 1 - len(caught_by) / len(every)
	return {"independent": indep, "one_draw": one, "values_ran": len(every), "values_caught": len(caught_by),
		"reliability": None}


def distro_specific(runs_on) -> bool:
	"""The bug failed significantly more on one value than on the rest (one-sided
	Fisher exact test, p < 0.05): a property of that distro, not chance."""
	from scipy.stats import fisher_exact
	if len(runs_on) < 2:
		return False
	v, (r, f) = max(runs_on.items(), key=lambda kv: kv[1][1] / kv[1][0] if kv[1][0] else 0)
	ro = sum(x[0] for k, x in runs_on.items() if k != v)
	fo = sum(x[1] for k, x in runs_on.items() if k != v)
	if not f or not ro:
		return False
	return bool(fisher_exact([[f, r - f], [fo, ro - fo]], alternative="greater")[1] < 0.05)


def clusters(change_bugs):
	"""The same bug across a change's patchsets: bugs sharing a subtest."""
	parent = list(range(len(change_bugs)))

	def find(i):
		while parent[i] != i:
			parent[i] = parent[parent[i]]
			i = parent[i]
		return i
	owner = {}
	for i, (_, b) in enumerate(change_bugs):
		for u in b["units"]:
			k = (u[0], u[1])
			if k in owner:
				parent[find(i)] = find(owner[k])
			else:
				owner[k] = i
	out = defaultdict(list)
	for i, cb in enumerate(change_bugs):
		out[find(i)].append(cb)
	return list(out.values())


def rotation(cases, sessions, sets, fails, start, end, rotated, attr):
	"""P1 / P3: per bug at its patchset, and per change under both rotations."""
	by_change = defaultdict(list)
	for c in cases:
		by_change[c["change"]].extend((c, b) for b in c["bugs"])
	rows = []
	for change, cbs in by_change.items():
		ch = Change(sessions.get(change, []), sets, fails)
		for cl in clusters(cbs):
			first = min(cl, key=lambda cb: cb[0]["patchset"])
			days = [s["day"] for s in ch.ss if s["patchset"] == first[0]["patchset"] and s["day"]]
			day = min(days) if days else first[0]["day"]
			if not (start <= day <= end):
				continue
			subs = {(u[0], u[1]) for _, b in cl for u in b["units"]}
			suites = {u[0] for _, b in cl for u in b["units"]}
			per_ps = {}
			for c, _ in cl:
				ss = [s for s in ch.ss if s["patchset"] == c["patchset"]]
				per_ps[c["patchset"]] = rotation_miss(ss, ch, subs, suites, rotated, attr)
			if all(m is None for m in per_ps.values()):
				exposed = None
			else:
				exposed = {p: m for p, m in per_ps.items() if m}
			# The same draw for every patchset: the change's union of evidence.
			whole = rotation_miss(ch.ss, ch, subs, suites, rotated, attr)
			fails_on = {s[attr] for s in ch.ss if rotated(s["test_group"]) and ch.caught(s, subs)}
			# How the bug failed per value, on the patchsets that carried it.
			runs_on = defaultdict(lambda: [0, 0])
			carried = set(per_ps)
			for s in ch.ss:
				if s["patchset"] in carried and rotated(s["test_group"]) and (ch.ran(s, suites) or ch.caught(s, subs)):
					runs_on[s[attr]][0] += 1
					runs_on[s[attr]][1] += ch.caught(s, subs)
			rel = max((f / r for v, (r, f) in runs_on.items() if v in fails_on and r), default=0)
			specific = distro_specific(runs_on)
			by_build = None
			if exposed is not None and len(exposed) == len(per_ps):
				by_build = {k: prod(m[k] for m in exposed.values()) for k in ("independent", "one_draw")}
			rows.append({"change": change, "patchset": first[0]["patchset"], "day": day,
				     "units": sorted({f"{u[0]} {u[1]}" for _, b in cl for u in b["units"]})[:4],
				     "patchsets": sorted(per_ps), "exposed": bool(exposed),
				     "first_ps_miss": per_ps[first[0]["patchset"]],
				     "change_miss": {k: whole[k] for k in ("independent", "one_draw")} if whole else None,
				     "by_build_miss": by_build, "reliability": round(rel, 3), "specific": specific,
				     "runs_on": {v or "": rf for v, rf in runs_on.items()},
				     "values_failed": sorted(v for v in fails_on if v)})
	return rows


def fs_miss(ss, ch, subs, suites, rotated, specific):
	"""One server filesystem per build for the suites the rotated groups run on
	both (ldiskfs and ZFS parts), alone and with one server distro per group:
	(fs only, fs and distro).  None as in rotation_miss."""
	events, ran = [], defaultdict(set)
	fs_of_suite = defaultdict(set)
	for s in ss:
		hit = ch.caught(s, subs)
		if not rotated(s["test_group"]):
			if hit:
				return None
			continue
		for x in ch.sets[s["id"]]:
			fs_of_suite[x["suite"]].add(s["server_fs"])
		if ch.ran(s, suites) or hit:
			ran[s["test_group"]].add(s["server_distro"])
		if hit:
			hs = {x["suite"] for x in ch.sets[s["id"]] if any((x["suite"], st) in subs for st in x["failed"])}
			hs |= {a for a, _ in ch.fails[s["id"]] & subs}
			for suite in hs:
				events.append((s["test_group"], s["server_fs"], s["server_distro"], suite))
	if not events:
		return None
	fss = sorted({e[1] for e in events} | {f for e in events for f in fs_of_suite[e[3]]})
	distros = set().union(*ran.values())
	only, both = 0.0, 0.0
	for f in fss:
		live = [e for e in events if e[1] == f or len(fs_of_suite[e[3]]) < 2]
		only += 0.0 if live else 1.0
		if specific:
			both += sum(not any(e[2] == d for e in live) for d in distros) / len(distros)
		else:
			got = defaultdict(set)
			for g, _, d, _ in live:
				got[g].add(d)
			both += prod(1 - len(got[g]) / len(ran[g]) for g in ran)
	return {"fs": only / len(fss), "fs_distro": both / len(fss)}


def fs_rotation(rows, cases, sessions, sets, fails, rotated):
	"""Adds fs and fs_distro misses to P1's rows (first patchset, by build, by change)."""
	by_change = defaultdict(list)
	for c in cases:
		by_change[c["change"]].extend((c, b) for b in c["bugs"])
	index = {(r["change"], r["patchset"], tuple(r["units"])): r for r in rows}
	for change, cbs in by_change.items():
		ch = Change(sessions.get(change, []), sets, fails)
		for cl in clusters(cbs):
			first = min(cl, key=lambda cb: cb[0]["patchset"])
			units = sorted({f"{u[0]} {u[1]}" for _, b in cl for u in b["units"]})[:4]
			r = index.get((change, first[0]["patchset"], tuple(units)))
			if r is None:
				continue
			subs = {(u[0], u[1]) for _, b in cl for u in b["units"]}
			suites = {u[0] for _, b in cl for u in b["units"]}
			per = {}
			for c, _ in cl:
				ss = [s for s in ch.ss if s["patchset"] == c["patchset"]]
				per[c["patchset"]] = fs_miss(ss, ch, subs, suites, rotated, r["specific"])
			r["fs_first_ps"] = per[first[0]["patchset"]]
			r["fs_by_build"] = ({k: prod(m[k] for m in per.values()) for k in ("fs", "fs_distro")}
					    if all(per.values()) else None)
			r["fs_by_change"] = fs_miss(ch.ss, ch, subs, suites, rotated, r["specific"])


def version(d):
	return tuple(int(x) for x in "".join(c for c in (d or "") if c.isdigit() or c == ".").split(".") if x)


def static(rows, cases, sessions, sets, fails):
	"""P1 as a fixed distro per doubled group.  Adds to P1's rows, per bug: the
	(group, role) pairs that caught it on some patchset carrying it, role 0 the
	older distro that group ran in that build and 1 the newer; and the by-build
	miss of a uniform draw in the doubled groups only (the intermittent figure).
	None when a session outside the doubled groups caught it."""
	by_change = defaultdict(list)
	for c in cases:
		by_change[c["change"]].extend((c, b) for b in c["bugs"])
	index = {(r["change"], r["patchset"], tuple(r["units"])): r for r in rows}
	for change, cbs in by_change.items():
		ch = Change(sessions.get(change, []), sets, fails)
		for cl in clusters(cbs):
			first = min(cl, key=lambda cb: cb[0]["patchset"])
			units = sorted({f"{u[0]} {u[1]}" for _, b in cl for u in b["units"]})[:4]
			r = index.get((change, first[0]["patchset"], tuple(units)))
			if r is None:
				continue
			subs = {(u[0], u[1]) for _, b in cl for u in b["units"]}
			suites = {u[0] for _, b in cl for u in b["units"]}
			catchers, miss, outside = set(), 1.0, False
			for ps in {c["patchset"] for c, _ in cl}:
				ss = [s for s in ch.ss if s["patchset"] == ps]
				group_d, ran, got = defaultdict(set), defaultdict(set), defaultdict(set)
				for s in ss:
					hit = ch.caught(s, subs)
					if s["test_group"] not in DOUBLED:
						outside |= hit
						continue
					group_d[s["test_group"]].add(s["server_distro"])
					if ch.ran(s, suites) or hit:
						ran[s["test_group"]].add(s["server_distro"])
					if hit:
						got[s["test_group"]].add(s["server_distro"])
				for g, ds in got.items():
					ordered = sorted(group_d[g], key=version)
					for d in ds:
						if d == ordered[0]:
							catchers.add((g, 0))
						if d == ordered[-1]:
							catchers.add((g, 1))
				miss *= prod(1 - len(got[g]) / len(ran[g]) for g in ran)
			r["static"] = None if outside or not catchers and miss >= 1 else {
				"catchers": sorted([g, k] for g, k in catchers), "by_build": miss}


def static_cost(rows, status, assign):
	"""Expected bugs hidden by one assignment ({group: 0 or 1}): distro-specific
	bugs when no kept (group, role) caught them; the rest at the uniform-draw
	figure; and the bound treating every bug as distro specific."""
	out = {"hidden": 0.0, "merged": 0.0, "specific": 0.0, "intermittent": 0.0, "bound": 0.0, "bound_merged": 0.0}
	for r in rows:
		x = r.get("static")
		if not x:
			continue
		lost = not any(assign[g] == k for g, k in x["catchers"])
		m = float(lost) if r["specific"] else x["by_build"]
		merged = status.get(str(r["change"]), status.get(r["change"])) == "MERGED"
		out["hidden"] += m
		out["specific" if r["specific"] else "intermittent"] += m
		out["merged"] += m * merged
		out["bound"] += lost
		out["bound_merged"] += lost * merged
	return out


def rebases(cases, sessions, sets, fails, kind, series, start, end):
	"""P2: bugs first seen on a patchset whose code (or own diff) did not change,
	where the previous patchset ran the bug's suites and they passed."""
	by_change = defaultdict(list)
	for c in cases:
		by_change[c["change"]].extend((c, b) for b in c["bugs"])
	rows = []
	for change, cbs in by_change.items():
		ch = Change(sessions.get(change, []), sets, fails)
		pss = sorted({s["patchset"] for s in ch.ss})
		for cl in clusters(cbs):
			subs = {(u[0], u[1]) for _, b in cl for u in b["units"]}
			suites = {u[0] for _, b in cl for u in b["units"]}
			hit_ps = sorted({s["patchset"] for s in ch.ss if ch.caught(s, subs)})
			if not hit_ps:
				continue
			p = hit_ps[0]
			k = kind.get((change, p))
			if k not in REBASE and k != "NO_CODE_CHANGE":
				continue
			days = [s["day"] for s in ch.ss if s["patchset"] == p and s["day"]]
			if not days or not (start <= min(days) <= end):
				continue
			prev = [q for q in pss if q < p]
			if not prev:
				continue
			q = prev[-1]
			prev_ran = [s for s in ch.ss if s["patchset"] == q and ch.ran(s, suites)]
			if not prev_ran:
				continue
			later = [x for x in hit_ps if x > p]
			later_full = [x for x in later if kind.get((change, x)) not in REBASE | {"NO_CODE_CHANGE"}]
			rows.append({"change": change, "patchset": p, "day": min(days), "kind": k, "prev": q,
				     "series_size": series.get((change, p)),
				     "units": sorted(f"{a} {b}" for a, b in subs)[:4],
				     "later": later, "later_full": later_full,
				     "fail_sessions": sum(ch.caught(s, subs) for s in ch.ss if s["patchset"] == p),
				     "ran_sessions": sum(ch.ran(s, suites) for s in ch.ss if s["patchset"] == p)})
	return rows


def retests(cases, sessions, sets, fails, start, end):
	"""P5: bugs a build caught only in a retest's test set that had passed in the
	failed run it repeated."""
	rows = []
	for c in cases:
		ss = [s for s in sessions.get(c["change"], []) if s["patchset"] == c["patchset"]]
		days = [s["day"] for s in ss if s["day"]]
		if not days or not (start <= min(days) <= end):
			continue
		ch = Change(ss, sets, fails)
		by_name = defaultdict(list)
		parallel = []
		for s in ss:
			g = s["test_group"] or ""
			# These run many sessions of one name at once, none a retest of another.
			if g == "custom" or g.endswith("subtest-change"):
				parallel.append(s)
			else:
				by_name[(s["build"], s["test_name"] or g)].append(s)
		for b in c["bugs"]:
			subs = {(u[0], u[1]) for u in b["units"]}
			kept = any(ch.caught(s, subs) for s in parallel)
			hidden = False
			for group in by_name.values():
				group.sort(key=lambda s: s["submission"] or "")
				orig = group[0]
				if ch.caught(orig, subs) or orig["status"] != "FAIL":
					kept = kept or any(ch.caught(s, subs) for s in group)
					continue
				passed = {x["suite"] for x in sets[orig["id"]] if x["status"] == "PASS"}
				for s in group[1:]:
					if not ch.caught(s, subs):
						continue
					hit = {x["suite"] for x in sets[s["id"]] if any((x["suite"], st) in subs for st in x["failed"])}
					hit |= {a for a, _ in ch.fails[s["id"]] & subs}
					if hit - passed:
						kept = True
					else:
						hidden = True
			if hidden and not kept:
				rows.append({"change": c["change"], "patchset": c["patchset"], "day": min(days),
					     "units": sorted(f"{u[0]} {u[1]}" for u in b["units"])[:4]})
	return rows


def merged(changes: set[int]) -> dict[int, str]:
	"""Gerrit status of each change (MERGED, ABANDONED, NEW), anonymously."""
	out = {}
	todo = sorted(changes)
	for i in range(0, len(todo), 40):
		q = "+OR+".join(f"change:{c}" for c in todo[i:i + 40])
		req = urllib.request.Request(f"{harvest.GERRIT}/changes/?q={q}&n=100",
					     headers={"User-Agent": harvest.USER_AGENT})
		with urllib.request.urlopen(req, timeout=60) as r:
			for ch in json.loads(r.read().decode().split("\n", 1)[1]):
				out[ch["_number"]] = ch["status"]
	return out


def main() -> None:
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("--db", default=str(ROOT / "data" / "failures.db"))
	ap.add_argument("--cases", default=str(ROOT / "data" / "cases.jsonl"))
	ap.add_argument("--start", required=True)
	ap.add_argument("--end", required=True)
	ap.add_argument("-o", "--out", required=True)
	args = ap.parse_args()
	cases, sessions, sets, fails, kind, series = load(args.db, args.cases)
	p1 = rotation(cases, sessions, sets, fails, args.start, args.end,
		      lambda g: (g or "").startswith("review-dne"), "server_distro")
	p3 = rotation(cases, sessions, sets, fails, args.start, args.end,
		      lambda g: g == "review-ldiskfs", "client_distro")
	fs_rotation(p1, cases, sessions, sets, fails, lambda g: (g or "").startswith("review-dne"))
	static(p1, cases, sessions, sets, fails)
	p2 = rebases(cases, sessions, sets, fails, kind, series, args.start, args.end)
	p5 = retests(cases, sessions, sets, fails, args.start, args.end)
	bugs = 0
	for c in cases:
		ss = [s for s in sessions.get(c["change"], []) if s["patchset"] == c["patchset"]]
		days = [s["day"] for s in ss if s["day"]]
		if days and args.start <= min(days) <= args.end:
			bugs += len(c["bugs"])
	need = {r["change"] for rows in (p1, p3) for r in rows if r["exposed"] or r.get("static")} | {r["change"] for r in p2 + p5}
	status = merged(need)
	Path(args.out).write_text(json.dumps({"start": args.start, "end": args.end, "bugs": bugs,
					      "faulty_patchsets": sum(1 for c in cases), "p1": p1, "p3": p3,
					      "p2": p2, "p5": p5, "status": status}, indent=0) + "\n")
	print(f"{bugs} bugs; P1 {sum(r['exposed'] for r in p1)} exposed; P3 {sum(r['exposed'] for r in p3)}; "
	      f"P2 {len(p2)}; P5 {len(p5)}", file=sys.stderr)


if __name__ == "__main__":
	main()
