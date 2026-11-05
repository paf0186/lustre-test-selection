#!/usr/bin/env python3
"""What dropping the sanity suite from some of the configurations that run it
would cost in patch faults hidden from review (plans/test-time SANITY-RUNS.md).

  eval/sanity_runs.py --start 2025-10-19 --end 2026-10-18 -o sanity-runs.json

A sanity configuration is a review group, with the client distro family for
review-ldiskfs (RHEL 8, 9, 10, SLES) and the older or newer server distro of the
build for review-dne-part-1 and review-dne-zfs-part-1.  A bug is exposed when a
cuttable sanity run caught it and nothing else did on the patchsets that carried
it: no other suite, and no sanity run in a group not listed here (subtest-change,
custom, other branches' groups).

A bug that failed significantly more in one configuration's sanity runs than in
the rest (one-sided Fisher exact test, p < 0.05) is a property of that
configuration: hidden when every configuration that caught it is dropped.  The
rest are intermittent: each sanity run of a patchset is one more roll, so
dropping m of n runs hides it with the chance that the n - m kept runs all miss, a new
roll per patchset.  Over a year whose matrix changed (review-ldiskfs ran two to six
clients, part-1 three distros for some months), a roll is restated in today's
matrix of one run per configuration: the run's chance is the bug's observed
failure rate over the cuttable sanity runs.  A bug failing significantly more on
ZFS than ldiskfs, and caught only on ZFS, is hidden only when both ZFS
configurations go; one failing significantly more with DNE (several MDTs) and
caught only there, only when every DNE configuration goes.  The bound treats every bug as a property of where it failed.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from policy_cost import ROOT, Change, clusters, distro_specific, load, merged, version  # noqa: E402

# Sanity hours per 30 days per configuration (lustre-reviews, 2026-10-27 to 11-02,
# from the CI test-time session's export of sessions).
HOURS = {"ldiskfs RHEL 8": 23500 / 4, "ldiskfs RHEL 9": 23500 / 4, "ldiskfs RHEL 10": 23500 / 4,
	 "ldiskfs SLES": 23500 / 4, "part-1 older": 15800 / 2, "part-1 newer": 15800 / 2,
	 "zfs-part-1 older": 16200 / 2, "zfs-part-1 newer": 16200 / 2, "selinux-ssk": 8100, "ipv6": 7800,
	 "ubuntu": 6700, "arm": 6000}
SIMPLE = {"review-dne-selinux-ssk-part-1": "selinux-ssk", "review-dne-ipv6": "ipv6",
	  "review-ldiskfs-ubuntu": "ubuntu", "review-ldiskfs-arm": "arm"}
ROLE = {"review-dne-part-1": "part-1", "review-dne-zfs-part-1": "zfs-part-1"}
ZFS = {"zfs-part-1 older", "zfs-part-1 newer"}
DNE = ZFS | {"part-1 older", "part-1 newer", "selinux-ssk", "ipv6"}


def config(s: dict, roles: dict) -> str | None:
	g = s["test_group"] or ""
	if g in SIMPLE:
		return SIMPLE[g]
	if g == "review-ldiskfs":
		d = s["client_distro"] or ""
		if d.startswith("SLES"):
			return "ldiskfs SLES"
		if d.startswith("RHEL"):
			return f"ldiskfs RHEL {version(d)[0]}"
		return None
	if g in ROLE:
		older = min(roles[g], key=version)
		return f"{ROLE[g]} {'older' if s['server_distro'] == older else 'newer'}"
	return None


def bugs(cases, sessions, sets, fails, start, end):
	by_change = defaultdict(list)
	for c in cases:
		by_change[c["change"]].extend((c, b) for b in c["bugs"])
	out = []
	for change, cbs in by_change.items():
		ch = Change(sessions.get(change, []), sets, fails)
		for cl in clusters(cbs):
			first = min(cl, key=lambda cb: cb[0]["patchset"])
			days = [s["day"] for s in ch.ss if s["patchset"] == first[0]["patchset"] and s["day"]]
			day = min(days) if days else first[0]["day"]
			if not (start <= day <= end):
				continue
			units = {(u[0], u[1]) for _, b in cl for u in b["units"]}
			san = {u for u in units if u[0] == "sanity"}
			if not san:
				continue
			rest = units - san
			other = False                       # caught by anything that stays
			per_ps = []                         # (n runs, k caught, {config: [runs, caught]})
			caught_in = set()
			runs_on = defaultdict(lambda: [0, 0])
			for ps in sorted({c["patchset"] for c, _ in cl}):
				ss = [s for s in ch.ss if s["patchset"] == ps]
				roles = defaultdict(set)
				for s in ss:
					if s["test_group"] in ROLE:
						roles[s["test_group"]].add(s["server_distro"])
				cfg = defaultdict(lambda: [0, 0])
				for s in ss:
					if rest and ch.caught(s, rest):
						other = True
					hit = ch.caught(s, san)
					k = config(s, roles)
					if k is None:
						other |= hit
						continue
					if hit or ch.ran(s, {"sanity"}):
						cfg[k][0] += 1
						cfg[k][1] += hit
						runs_on[k][0] += 1
						runs_on[k][1] += hit
						if hit:
							caught_in.add(k)
				per_ps.append(dict(cfg))
			if not caught_in:
				continue
			fs = defaultdict(lambda: [0, 0])
			for k, (r, f) in runs_on.items():
				fs["zfs" if k in ZFS else "ldiskfs"][0] += r
				fs["zfs" if k in ZFS else "ldiskfs"][1] += f
			n = sum(r for r, _ in runs_on.values())
			k = sum(f for _, f in runs_on.values())
			fs_spec = distro_specific(fs)
			dne = defaultdict(lambda: [0, 0])
			for c, (r, f) in runs_on.items():
				dne[c in DNE][0] += r
				dne[c in DNE][1] += f
			kind = ("fs" if fs_spec and set(caught_in) <= ZFS else
				"dne" if distro_specific(dne) and set(caught_in) <= DNE else
				"config" if distro_specific(runs_on) else "roll")
			out.append({"change": change, "day": day, "kind": kind, "p": k / n, "units": sorted(f"{a} {b}" for a, b in units)[:4],
				    "other": other, "caught_in": sorted(caught_in), "per_ps": per_ps,
				    "runs_on": dict(runs_on), "fs_specific": fs_spec, "fs_runs": dict(fs)})
	return out


def hidden(b: dict, drop: set) -> tuple[float, bool]:
	"""(expected miss, bound miss) of one bug when sanity is dropped from drop,
	in today's matrix of one sanity run per configuration."""
	if b["other"]:
		return 0.0, False
	lost = set(b["caught_in"]) <= drop
	if b["kind"] == "fs":
		return float(ZFS <= drop), lost
	if b["kind"] == "dne":
		return float(DNE <= drop), lost
	if b["kind"] == "config":
		return float(lost), lost
	# One more roll: a run's chance p, each patchset that carried the bug a new
	# set of runs; the chance today's matrix less drop misses it, given that the
	# whole of today's matrix catches it.
	p, ps = b["p"], len(b["per_ps"])
	full = 1 - (1 - p) ** (len(HOURS) * ps)
	if not full:
		return 0.0, lost
	return ((1 - p) ** ((len(HOURS) - len(drop)) * ps) - (1 - p) ** (len(HOURS) * ps)) / full, lost


def cost(bs, drop, status):
	out = {"hidden": 0.0, "property": 0.0, "roll": 0.0, "merged": 0.0, "bound": 0, "bound_merged": 0}
	for b in bs:
		m, lost = hidden(b, drop)
		mg = status.get(b["change"]) == "MERGED"
		out["hidden"] += m
		out["roll" if b["kind"] == "roll" else "property"] += m
		out["merged"] += m * mg
		out["bound"] += lost
		out["bound_merged"] += lost * mg
	return out


def main() -> None:
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("--db", default=str(ROOT / "data" / "failures.db"))
	ap.add_argument("--cases", default=str(ROOT / "data" / "cases.jsonl"))
	ap.add_argument("--start", required=True)
	ap.add_argument("--end", required=True)
	ap.add_argument("-o", "--out", required=True)
	args = ap.parse_args()
	cases, sessions, sets, fails, _, _ = load(args.db, args.cases)
	bs = bugs(cases, sessions, sets, fails, args.start, args.end)
	status = merged({b["change"] for b in bs})
	cfgs = sorted(HOURS)
	report = {"start": args.start, "end": args.end, "bugs": len(bs), "per_config": {}, "greedy": [],
		  "candidates": {}, "fs": {}}
	for c in cfgs:
		only = [b for b in bs if b["caught_in"] == [c]]
		alone = [b for b in only if not b["other"]]
		report["per_config"][c] = {
			"hours_30d": HOURS[c], "only_sanity_catch": len(only),
			"only_merged": sum(status.get(b["change"]) == "MERGED" for b in only),
			"no_other_catch": len(alone), "no_other_merged": sum(status.get(b["change"]) == "MERGED" for b in alone),
			"property": sum(b["kind"] != "roll" for b in alone), **{k: round(v, 2) for k, v in cost(bs, {c}, status).items()}}
	drop: set = set()
	while len(drop) < len(cfgs):
		best = min((c for c in cfgs if c not in drop),
			   key=lambda c: (cost(bs, drop | {c}, status)["hidden"], -HOURS[c]))
		drop.add(best)
		x = cost(bs, drop, status)
		report["greedy"].append({"drop": best, "hours_saved_30d": sum(HOURS[c] for c in drop),
					 **{k: round(v, 2) for k, v in x.items()}})
	cands = {}
	for keep in ("ldiskfs RHEL 8", "ldiskfs RHEL 9", "ldiskfs RHEL 10"):
		cands[f"review-ldiskfs keeps {keep[8:]} and SLES"] = {c for c in cfgs if c.startswith("ldiskfs RHEL") and c != keep}
	for a, b in itertools.product(("older", "newer"), repeat=2):
		cands[f"part-1 keeps {a}, zfs-part-1 keeps {b}"] = {f"part-1 {'newer' if a == 'older' else 'older'}",
								     f"zfs-part-1 {'newer' if b == 'older' else 'older'}"}
	for name, d in cands.items():
		report["candidates"][name] = {"drop": sorted(d), "hours_saved_30d": sum(HOURS[c] for c in d),
					      **{k: round(v, 2) for k, v in cost(bs, d, status).items()}}
	zf = [b for b in bs if set(b["caught_in"]) <= ZFS and b["fs_runs"].get("ldiskfs", [0])[0]]
	lf = [b for b in bs if not set(b["caught_in"]) & ZFS and b["fs_runs"].get("zfs", [0])[0]]
	for name, sel in (("zfs_only", zf), ("ldiskfs_only", lf)):
		report["fs"][name] = {"bugs": len(sel), "no_other_catch": sum(not b["other"] for b in sel),
				      "fs_specific": sum(b["fs_specific"] for b in sel),
				      "fs_specific_no_other": sum(b["fs_specific"] and not b["other"] for b in sel),
				      "merged_no_other": sum(not b["other"] and status.get(b["change"]) == "MERGED" for b in sel),
				      "examples": [(b["change"], b["units"][:2], b["fs_runs"]) for b in sel
						   if b["fs_specific"] and not b["other"]][:8]}
	report["bugs_list"] = bs
	report["status"] = status
	Path(args.out).write_text(json.dumps(report, indent=0) + "\n")
	print(json.dumps({k: v for k, v in report.items() if k not in ("bugs_list", "status")}, indent=1))


if __name__ == "__main__":
	main()
