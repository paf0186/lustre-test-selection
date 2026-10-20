#!/usr/bin/env python3
"""Regenerate the generated tables of docs/test-matrix.md.

  ./matrix_doc.py                                  # ask Rainshadow, rewrite the tables
  ./matrix_doc.py --days 30 --long-days 180 --lustre ~/lustre-release
  ./matrix_doc.py --offline                        # redo the tables from docs/data/*.json

The narrative in the document is written by hand; the blocks between
`<!-- BEGIN generated:NAME -->` and `<!-- END generated:NAME -->` are replaced.
The data comes from `rainshadow matrix` (saved under docs/data/, so a rerun
without the server gives the same tables) and from the lustre-release tree for
which suites exist.  Standard library only.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OVERHEAD = ("node-provisioning", "lustre-initialization")
FAMILIES = ("full-dne-ipv6", "full-dne-zfs", "full-dne", "full-zfs", "full", "rolling-upgrade", "rolling-downgrade",
	    "clean-upgrade", "clean-downgrade", "failover", "review-dne-selinux-ssk", "review-dne-zfs", "review-dne",
	    "review-ldiskfs", "review")


def rainshadow_matrix(days: int, jobs: list[str] | None = None) -> dict:
	cmd = ["rainshadow", "matrix", "--days", str(days)] + [x for j in jobs or [] for x in ("--job", j)]
	out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
	return json.loads(out)


def hours(s) -> str:
	return f"{(s or 0) / 3600:.1f}"


def table(head: list[str], rows: list[list]) -> str:
	return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
			 + ["| " + " | ".join(str(c) for c in r) + " |" for r in rows])


def topo(t) -> str:
	return "-" if not t else f"{t['clients']}c {t['mds_nodes']}mds/{t['mdts']}mdt {t['oss_nodes']}oss/{t['osts']}ost"


def cfg_summary(g) -> str:
	fs = sorted({c["server_fs"] for c in g["configs"] if c["server_fs"]})
	cl = collections.Counter()
	for c in g["configs"]:
		cl[c["client_distro"]] += c["sessions"]
	top = ", ".join(f"{k} ({v})" for k, v in cl.most_common(4))
	nets = sorted({f"{c['server_network']}" for c in g["configs"] if c["server_network"]})
	arch = sorted({c["client_arch"] or c["arch"] for c in g["configs"]})
	return f"{'/'.join(fs)} {'/'.join(nets)} {'/'.join(a for a in arch if a)}; clients {top}"


def suites_of(g, minimum: float = 0.05) -> str:
	out = []
	for s in g["suites"]:
		if s["suite"] in OVERHEAD or s["runs_per_session"] < minimum:
			continue
		rps = "" if s["runs_per_session"] >= 0.95 else f" x{s['runs_per_session']}"
		out.append(f"{s['suite']}{rps} ({(s['mean_s'] or 0) / 60:.0f})")
	return ", ".join(out)


def blk_jobs(m: dict, ctx) -> str:
	return table(["Job", "Sessions", "Builds", "Sessions/build", "Enforcing", "Developer-test", "Mean session (h)"],
		     [[j["job"], f"{j['sessions']:,}", f"{j['builds']:,}", f"{j['sessions'] / j['builds']:.0f}",
		       f"{j['enforcing']:,}", f"{j['developer_test']:,}", hours(j["mean_s"])] for j in m["jobs"]])


def blk_review_groups(m: dict, ctx) -> str:
	rows = []
	for g in m["groups"]:
		if g["job"] != "lustre-reviews":
			continue
		pb = g.get("per_build") or {}
		rows.append([g["test_group"], g["kind"], f"{(g['share_of_builds'] or 0) * 100:.0f}%",
			     f"{pb.get('sessions', '-')} / {pb.get('client_distros', '-')}", hours(g["mean_s"]),
			     topo(g.get("topology")), cfg_summary(g), suites_of(g)])
	return table(["Group", "Kind", "Builds", "Sessions / distros per build", "Mean h", "Nodes (clients, MDS nodes/MDTs, OSS nodes/OSTs)",
		      "Filesystem, network, arch; client distros (sessions)", "Suites (runs per session, mean minutes)"], rows)


def blk_suites(m: dict, ctx) -> str:
	rows = []
	for e in m["suites"]:
		if e["job"] != "lustre-reviews" or e["suite"] in OVERHEAD:
			continue
		gs = sorted(e["groups"], key=lambda x: -x["sessions"] * x["runs_per_session"])
		names = ", ".join(x["test_group"].replace("review-", "") for x in gs[:7]) + (f", +{len(gs) - 7}" if len(gs) > 7 else "")
		sub = max((x["subtests"] or 0) for x in e["groups"])
		rows.append([e["suite"], f"{e['total_s'] / 3600:,.0f}", len(gs), f"{sub:.0f}", names])
	return table(["Suite", "Hours in the period", "Groups", "Subtests (most in a group)", "Groups that run it"], rows)


def family(name: str) -> str:
	for f in FAMILIES:
		if name == f or name.startswith(f + "-"):
			return f
	return name


def blk_branch(m: dict, ctx) -> str:
	rows = []
	for job in ("lustre-master", "lustre-master-next"):
		fam = collections.defaultdict(lambda: [0, 0, set()])
		builds = next((j["builds"] for j in m["jobs"] if j["job"] == job), 0)
		for g in m["groups"]:
			if g["job"] != job:
				continue
			f = family(g["test_group"])
			fam[f][0] += g["sessions"]
			fam[f][1] += g["total_s"] or 0
			fam[f][2].add(g["test_group"])
		for f, (n, tot, names) in sorted(fam.items(), key=lambda kv: -kv[1][1]):
			rows.append([job, f, len(names), n, f"{n / max(builds, 1):.1f}", hours(tot / max(n, 1))])
	return table(["Job", "Family of groups", "Groups", "Sessions", "Sessions per build", "Mean h"], rows)


def blk_tree(m: dict, ctx) -> str:
	root = ctx.get("lustre")
	if not root:
		return "_(run with --lustre PATH to list the suites in the tree)_"
	tests = Path(root).expanduser() / "lustre" / "tests"
	intree = {f.stem for f in tests.glob("*.sh") if "run_test" in f.read_text(errors="ignore")
		  and "init_test_env" in f.read_text(errors="ignore") and f.stem != "test-framework"}
	long = ctx["long"]
	ran = collections.defaultdict(set)
	for e in long["suites"]:
		ran[e["job"]].add(e["suite"])
	allran = set().union(*ran.values()) if ran else set()
	regression = (tests / "test-groups" / "regression").read_text().split()
	mpi = (tests / "test-groups" / "regression-mpi").read_text().split()
	lines = [f"Over {ctx['long_days']} days.", "",
		 f"- In-tree suites (`lustre/tests/*.sh` that call `run_test`): {len(intree)}.",
		 f"- In the tree's `regression` group ({len(regression)}) and `regression-mpi` ({len(mpi)}): all but "
		 f"{', '.join(sorted(set(regression + mpi) - allran)) or 'none'} ran in some job.",
		 f"- In the tree but never run in the period: {', '.join(sorted(intree - allran)) or 'none'}.",
		 f"- Run in reviews though not in `regression`/`regression-mpi`: "
		 f"{', '.join(sorted(ran['lustre-reviews'] - set(regression) - set(mpi) - set(OVERHEAD)))}.",
		 f"- Run only on the branch jobs (never in reviews): "
		 f"{', '.join(sorted((ran['lustre-master'] | ran['lustre-master-next']) - ran['lustre-reviews']))}."]
	return "\n".join(lines)


def blk_testparams(m: dict, ctx) -> str:
	db = ctx.get("db")
	if not db or not Path(db).exists():
		return "_(run with --db data/failures.db to count the Test-Parameters keys used)_"
	import sqlite3
	keys = collections.Counter()
	vals = collections.defaultdict(collections.Counter)
	n = with_tp = 0
	for (tp,) in sqlite3.connect(db).execute("SELECT test_parameters FROM patchset"):
		n += 1
		if not tp:
			continue
		with_tp += 1
		seen = set()
		pairs = set()
		for line in tp.splitlines():
			for k, v in re.findall(r'(\w+)=("[^"]*"|\S+)', line):
				seen.add(k)
				if k != "env":
					pairs.add((k, v))
			for w in re.findall(r"(?<![=\w])(trivial|fortestonly|forbuildonly)\b", line):
				seen.add(w)
		keys.update(seen)
		for k, v in pairs:
			vals[k][v] += 1
	rows = [[k, c, ", ".join(f"{v} ({x})" for v, x in vals[k].most_common(5))] for k, c in keys.most_common(24)]
	return (f"Among the {n:,} patchsets in the dataset ({with_tp:,} with a `Test-Parameters:` line), "
		f"patchsets using each key:\n\n" + table(["Key", "Patchsets", "Commonest values (patchsets)"], rows))


def history(end: str, months: int) -> dict:
	"""Share of lustre-reviews builds that ran each group, per 30-day window, oldest first."""
	out = {}
	e = dt.date.fromisoformat(end)
	for i in reversed(range(months)):
		we = e - dt.timedelta(days=30 * i)
		ws = we - dt.timedelta(days=29)
		cmd = ["rainshadow", "matrix", "--start", ws.isoformat(), "--end", we.isoformat(), "--job", "lustre-reviews"]
		d = json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout)
		out[ws.isoformat()] = {"builds": d["jobs"][0]["builds"] if d["jobs"] else 0,
				       "share": {g["test_group"]: g["share_of_builds"] for g in d["groups"]},
				       "sessions": {g["test_group"]: g["sessions"] for g in d["groups"]}}
	return out


def blk_history(m: dict, ctx) -> str:
	h = ctx["history"]
	wins = list(h)
	groups = collections.Counter()
	for w in wins:
		for g, n in h[w]["sessions"].items():
			if g.startswith(("review", "custom")):
				groups[g] += n
	order = [g for g, _ in groups.most_common()
		 if max((h[w]["share"].get(g) or 0) for w in wins) >= 0.01]
	rows = [[g] + [f"{(h[w]['share'].get(g) or 0) * 100:.0f}%" for w in wins] for g in order]
	rows.append(["_builds in the window_"] + [f"{h[w]['builds']:,}" for w in wins])
	return table(["Group (share of builds that ran it)"] + wins, rows)


BLOCKS = {"jobs": blk_jobs, "review_groups": blk_review_groups, "suites": blk_suites,
	  "branch": blk_branch, "history": blk_history, "tree": blk_tree, "testparams": blk_testparams}


def main() -> None:
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("--days", type=int, default=30, help="window for the group and suite tables")
	ap.add_argument("--long-days", type=int, default=180, help="window for which suites ever run")
	ap.add_argument("--lustre", help="a lustre-release checkout, for the suites in the tree")
	ap.add_argument("--db", default=str(HERE / "data" / "failures.db"), help="dataset, for Test-Parameters")
	ap.add_argument("--doc", default=str(HERE / "docs" / "test-matrix.md"))
	ap.add_argument("--data", default=str(HERE / "docs" / "data"))
	ap.add_argument("--months", type=int, default=7, help="30-day windows in the history table")
	ap.add_argument("--offline", action="store_true", help="use the saved docs/data/matrix-*.json")
	args = ap.parse_args()
	data = Path(args.data)
	data.mkdir(parents=True, exist_ok=True)
	short, long = data / f"matrix-{args.days}d.json", data / f"matrix-{args.long_days}d.json"
	if args.offline:
		m, lg = json.loads(short.read_text()), json.loads(long.read_text())
	else:
		m, lg = rainshadow_matrix(args.days), rainshadow_matrix(args.long_days)
		short.write_text(json.dumps(m, indent=1, sort_keys=True) + "\n")
		long.write_text(json.dumps(lg, indent=1, sort_keys=True) + "\n")
	hist = data / "history.json"
	if args.offline:
		h = json.loads(hist.read_text())
	else:
		h = history(m["end"], args.months)
		hist.write_text(json.dumps(h, indent=1, sort_keys=True) + "\n")
	ctx = {"history": h, "lustre": args.lustre, "db": args.db, "long": lg, "long_days": args.long_days}
	doc = Path(args.doc)
	text = doc.read_text()
	for name, fn in BLOCKS.items():
		pat = re.compile(rf"(<!-- BEGIN generated:{name} -->\n).*?(<!-- END generated:{name} -->)", re.S)
		if not pat.search(text):
			sys.exit(f"{doc} has no generated:{name} block")
		body = fn(m, ctx)
		text = pat.sub(lambda mo: mo.group(1) + body + "\n" + mo.group(2), text)
	text = re.sub(r"<!-- generated-on:.*?-->", f"<!-- generated-on: {m['end']} (window {m['start']} to {m['end']}, "
		      f"{args.days} days; suites over {args.long_days} days) -->", text)
	doc.write_text(text)
	print(f"wrote {doc}", file=sys.stderr)


if __name__ == "__main__":
	main()
