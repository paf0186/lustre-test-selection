#!/usr/bin/env python3
"""Compare headers with what CI did.  A subtest that was skipped in a run with no
requires, slow or exception to explain it is missing a requirement; one that ran
although its header rules that configuration out has a wrong requirement.

Rainshadow gives each subtest's status but not its skip reason, so this reports
candidates to read, not errors.  Sessions are named with --set <test set id>:<config>,
where config is a comma list such as fstype=zfs,mdscount=1,ostcount=2.

  metadata/skipcheck.py data/metadata/headers/sanity.yaml --set ff2c26cc-...:fstype=ldiskfs,mdscount=1
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

import yaml


def statuses(test_set):
	out = subprocess.run(["rainshadow", "subtests", test_set, "--all"], capture_output=True,
			     text=True, check=True).stdout
	return {s["name"].removeprefix("test_"): s["status"] for s in json.loads(out)["subtests"]}


def excluded(header, config):
	"""Why config rules this subtest out, or None."""
	req = header.get("requires") or {}
	for key in ("mdscount", "ostcount", "clientcount"):
		if key in req and key in config and int(config[key]) < req[key]:
			return f"{key} {config[key]} < {req[key]}"
	for key in ("fstype", "mds1_fstype", "ost1_fstype"):
		have = config.get(key, config.get("fstype"))
		want = req.get(key)
		if want and have and have not in want:
			return f"{key} {have} not in {want}"
	return None


def compare(headers, seen, config):
	"""[(subtest, finding)] for one test set."""
	out = []
	for sid, header in headers.items():
		status = seen.get(sid)
		if status is None:
			continue
		why = excluded(header, config)
		if status == "SKIP" and not why and not header.get("slow") and not (header.get("requires") or {}):
			out.append((sid, "skipped, and its header has no requirement"))
		elif status in ("PASS", "FAIL") and why:
			out.append((sid, f"ran, but its header excludes this: {why}"))
	return out


def main():
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("headers")
	ap.add_argument("--set", action="append", default=[], dest="sets", help="test set id:config")
	args = ap.parse_args()
	with open(args.headers, encoding="utf-8") as f:
		headers = yaml.safe_load(f)["subtests"]
	found = 0
	for item in args.sets:
		test_set, _, conf = item.partition(":")
		config = dict(x.split("=", 1) for x in conf.split(",") if x)
		for sid, finding in compare(headers, statuses(test_set), config):
			print(f"{test_set[:8]} {sid}: {finding}")
			found += 1
	sys.exit(1 if found else 0)


if __name__ == "__main__":
	main()
