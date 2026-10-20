#!/usr/bin/env python3
"""What configuration each Test-Parameters key set actually got.

Matches every custom session to the line of its patchset's Test-Parameters that
asked for it (same build, same scripts) and counts, per set of configuration keys,
the classes and layouts the sessions ran with.  A class translation is verified
when its keys gave that class.

  eval/check_classes.py [--db data/failures.db]
"""
from __future__ import annotations

import argparse
import shlex
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from classes import class_of  # noqa: E402

INFRA = {"node-provisioning", "lustre-initialization"}
SELECTION = {"testlist", "env", "envdefinitions", "trivial", "fortestonly", "optional",
	     "signofftest", "austeroptions", "resumeaftercrash", "timeoutreprovision"}


def parse_line(line: str) -> dict[str, str]:
	"""One Test-Parameters line as {key: value}; flags have value 'true'."""
	out = {}
	try:
		toks = shlex.split(line.replace("\\\n", " "))
	except ValueError:
		toks = line.split()
	for t in toks:
		k, eq, v = t.partition("=")
		out[k.lower()] = v if eq else "true"
	return out


def lines_of(tp: str | None) -> list[dict[str, str]]:
	return [parse_line(x) for x in (tp or "").replace("\\\n", " ").splitlines() if x.strip()]


def main() -> None:
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("--db", default=str(Path(__file__).resolve().parent.parent / "data" / "failures.db"))
	args = ap.parse_args()
	db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=60)
	db.row_factory = sqlite3.Row
	tps = {(r["change"], r["patchset"]): r["test_parameters"] for r in db.execute(
		"SELECT change, patchset, test_parameters FROM patchset WHERE test_parameters IS NOT NULL")}
	suites = defaultdict(set)
	for sid, suite in db.execute("SELECT t.session_id, t.suite FROM test_set t JOIN session s "
				     "ON s.id = t.session_id WHERE s.test_group = 'custom'"):
		if suite not in INFRA:
			suites[sid].add(suite)
	got: dict[str, Counter] = defaultdict(Counter)
	for s in db.execute("SELECT * FROM session WHERE test_group = 'custom' AND mdts IS NOT NULL"):
		lines = [ln for ln in lines_of(tps.get((s["change"], s["patchset"])))
			 if "testgroup" not in ln and "testlist" in ln
			 and set(ln["testlist"].split(",")) == suites.get(s["id"])]
		keysets = {" ".join(f"{k}={v}" for k, v in sorted(ln.items()) if k not in SELECTION) for ln in lines}
		if len(keysets) != 1:
			continue
		layout = f"{s['clients']}c {s['mds_nodes']}/{s['mdts']} {s['oss_nodes']}/{s['osts']}"
		got[keysets.pop() or "(none)"][(class_of(dict(s)), s["server_fs"], layout, s["test_name"].split("-custom")[0]
						 if s["test_name"] else "")] += 1
	for keys, c in sorted(got.items(), key=lambda kv: -sum(kv[1].values())):
		print(f"{keys}: {sum(c.values())} sessions")
		for (cls, fs, layout, name), n in c.most_common(4):
			print(f"    {n:4d}  {cls}  {fs}  {layout}  {name}")


if __name__ == "__main__":
	main()
