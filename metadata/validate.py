#!/usr/bin/env python3
"""Check headers against what extract.py found, so that a wrong answer is a
finding rather than a silent entry.  Errors must be fixed or adjudicated;
warnings are worth a look but can stand.

  metadata/validate.py data/metadata/extract.jsonl data/metadata/terra/
  metadata/validate.py data/metadata/extract.jsonl data/metadata/terra/ --summary
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import keywords as kw  # noqa: E402
import schema  # noqa: E402

COMMIT = re.compile(r"^([0-9a-f]{12}) \('(.*)'\)$")
FEATURE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,29}$")


def load_answer(text):
	"""The YAML in a model's answer, as (header, error)."""
	m = re.search(r"```ya?ml\n(.*?)```", text, re.S)
	try:
		header = yaml.safe_load(m.group(1) if m else text)
	except yaml.YAMLError as e:
		return None, f"not YAML: {str(e).splitlines()[0]}"
	if not isinstance(header, dict):
		return None, "not a YAML mapping"
	return header, None


def repair(rec, header, vocab, aliases):
	"""An answer with what the code settles put right, so that a slip there is
	not worth a third reader: what extract.py found stands, whatever was said."""
	facts = rec["facts"]
	for key, want in (("parallel", False), ("slow", True)):
		if key in facts:
			header[key] = want
	given = header.get("requires")
	if isinstance(given, dict) or given is None:
		kept = {k: v for k, v in (given or {}).items() if k in schema.READER_REQUIRES}
		if not reconcile_hosts(facts):
			kept = {k: v for k, v in kept.items() if k not in ("mdsnodes", "ossnodes")}
		kept.update(facts["requires"])
		header.pop("requires", None)
		if kept:
			header["requires"] = kept
	roles = set(facts.get("failover") or []) - {"client"}
	for key in ("modifies", "observes"):
		if isinstance(header.get(key), list):
			found = set(facts["evidence"].get(key, []))
			keep = {x for x in map(str, header[key]) if x not in schema.MECHANICAL}
			header[key] = sorted(keep | found | ({"recovery"} if roles and key == "modifies" else set()))
	if isinstance(header.get("keywords"), list):
		words = {aliases.get(w, w) for w in header["keywords"]}
		header["keywords"] = sorted(words & vocab) or header["keywords"]
	subjects = {c["hash"]: c["subject"].replace('"', "'") for c in rec["history"]}
	if isinstance(header.get("commit"), list):
		header["commit"] = [f"{str(c).split()[0]} ('{subjects[str(c).split()[0]]}')"
				    if str(c).split()[0] in subjects else c for c in header["commit"]]
	return header


def reconcile_hosts(facts):
	"""Whether anything in the code can call for a node count."""
	return bool(facts.get("hosts") or facts.get("failover"))


def version(value):
	"""(op, dotted) of a requires version."""
	m = re.match(r"^(<=|>=|==|!=|<|>)?\s*(v?[0-9][\w.-]*)$", str(value).strip())
	return (m.group(1) or ">=", m.group(2)) if m else None


def same_require(key, want, have):
	if key.endswith("_version"):
		return version(want) == version(have)
	if key.endswith("fstype") or key == "tools":
		return set(want) == set(have if isinstance(have, list) else [have])
	return want == have


def check(rec, header, vocab=None, aliases=None):
	"""[(level, field, message)] for one header."""
	if vocab is None:
		vocab, aliases = kw.load()
	out = []

	def err(field, msg):
		out.append(("error", field, msg))

	def warn(field, msg):
		out.append(("warning", field, msg))

	facts = rec["facts"]
	for key in header:
		if key not in schema.FIELDS:
			err(key, "unknown field")
	for key in schema.REQUIRED - set(header):
		err(key, "missing: an empty list says none, a missing one says unknown")
	text = json.dumps(header, ensure_ascii=False, default=str)
	if not text.isascii():
		err("text", "not ASCII: " + "".join(sorted({c for c in text if ord(c) > 127})))

	if "summary" in header and str(header["summary"]) != rec["summary"]:
		err("summary", f"run_test says {rec['summary']!r}")

	known = {c["hash"]: c["subject"] for c in rec["history"]}
	commits = header.get("commit") or []
	if not isinstance(commits, list):
		err("commit", "not a list")
		commits = []
	order = list(known)
	last = -1
	for entry in commits:
		m = COMMIT.match(str(entry)) if isinstance(entry, str) else None
		if not m:
			err("commit", f"not \"<hash> ('<subject>')\": {entry!r}")
		elif m.group(1) not in known:
			err("commit", f"{m.group(1)} is not in the subtest's history")
		else:
			if m.group(2) != known[m.group(1)].replace('"', "'"):
				err("commit", f"{m.group(1)} subject is {known[m.group(1)]!r}")
			at = order.index(m.group(1))
			if at < last:
				warn("commit", "not oldest first")
			last = at
	if known and not commits:
		err("commit", "empty, but the subtest has history")

	words = header.get("keywords")
	if words is not None and not isinstance(words, list):
		err("keywords", f"not a list: {words!r}")
		words = []
	for word in words or []:
		if word in aliases:
			err("keywords", f"{word} should be {aliases[word]}")
		elif word not in vocab:
			err("keywords", f"{word} is not on the list")
	if isinstance(words, list) and "keywords" in header and not words:
		err("keywords", "empty")

	requires = header.get("requires") or {}
	if not isinstance(requires, dict):
		err("requires", "not a mapping")
		requires = {}
	for key in requires:
		if key not in schema.REQUIRES:
			err("requires", f"unknown field {key}")
	if set(requires) == {"reason"}:
		err("requires", "a reason with nothing to check")
	for key, want in facts["requires"].items():
		if key not in requires:
			err("requires", f"{key} missing, the skip checks say {want}")
		elif not same_require(key, want, requires[key]):
			err("requires", f"{key} is {requires[key]}, the skip checks say {want}")
	for key in ("mdscount", "ostcount", "clientcount"):
		if key in requires and key not in facts["requires"]:
			if not facts["unparsed_skips"] and requires[key] == 1:
				err("requires", f"{key}: 1 says nothing")
			elif not facts["unparsed_skips"]:
				err("requires", f"{key} has no skip check behind it")
	for key in ("mdsnodes", "ossnodes"):
		value = requires.get(key)
		if value is not None and (not isinstance(value, int) or value < 2):
			err("requires", f"{key} must be 2 or more")
		elif value is not None and key not in facts["requires"] and not reconcile_hosts(facts):
			err("requires", f"{key}: the code never looks at hosts or fails a server")
	for key, value in requires.items():
		if isinstance(value, dict):
			err("requires", f"{key} is a mapping")
	for name in requires.get("feature") or []:
		if not FEATURE.match(str(name)):
			err("requires", f"bad feature name {name!r}")
	if facts["unparsed_skips"] and not set(requires) - set(facts["requires"]) - {"reason"}:
		warn("requires", f"{len(facts['unparsed_skips'])} skips not expressed: "
		     f"{facts['unparsed_skips'][0]}")

	for key in ("start", "end"):
		if key in header and header[key] not in schema.STATES:
			err(key, f"bad state {header[key]!r}")
		have = header.get(key, "mounted" if key == "start" else None)
		if key in facts and have != facts[key]:
			err(key, f"is {have}, the subtest's own statements say {facts[key]}")

	for key, table in (("modifies", schema.MODIFIES), ("observes", schema.OBSERVES)):
		value = header.get(key)
		if value is None:
			continue
		if not isinstance(value, list):
			err(key, f"not a list: {value!r}")
			continue
		for item in value:
			if item not in table:
				err(key, f"{item} is not on the list")
		for item in facts["evidence"][key]:
			if item not in value and item not in schema.MECHANICAL:
				err(key, f"{item} missing, the code shows it")

	for key, want in (("parallel", False), ("slow", True)):
		if key in facts and header.get(key) != want:
			err(key, f"the skip checks say {key}: {str(want).lower()}")
	if facts.get("parallel") is None and header.get("parallel") is False and schema.parallel_safe(
			{**header, "parallel": None}) is True:
		warn("parallel", "false, but it declares no shared state")

	fix = header.get("parallel_fix")
	if fix is not None:
		if not isinstance(fix, str) or "\n" in fix.strip() or len(fix) > 120:
			err("parallel_fix", "not one short line")
		elif not schema.parallel_fixable(header):
			warn("parallel_fix", "given, but more than scope stops it")

	description = str(header.get("description") or "")
	if not description:
		warn("description", "missing")
	elif len(description.split("\n")) > 8:
		warn("description", "more than 8 lines")
	return out


def main():
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("extract")
	ap.add_argument("answers", help="directory of <suite>_<id>.txt answers")
	ap.add_argument("--summary", action="store_true", help="counts only")
	args = ap.parse_args()
	vocab, aliases = kw.load()
	counts = Counter()
	ok = bad = missing = 0
	with open(args.extract, encoding="utf-8") as f:
		records = [json.loads(x) for x in f]
	for rec in records:
		path = Path(args.answers) / f"{rec['suite']}_{rec['id']}.txt"
		if not path.exists():
			missing += 1
			continue
		header, error = load_answer(path.read_text(encoding="utf-8"))
		issues = [("error", "yaml", error)] if error else check(
			rec, repair(rec, header, vocab, aliases), vocab, aliases)
		bad += any(x[0] == "error" for x in issues)
		ok += not any(x[0] == "error" for x in issues)
		for level, field, msg in issues:
			counts[f"{level} {field}"] += 1
			if not args.summary:
				print(f"{rec['suite']} {rec['id']}: {level}: {field}: {msg}")
	print(json.dumps({"clean": ok, "with errors": bad, "no answer": missing, "issues": counts}))
	sys.exit(1 if bad else 0)


if __name__ == "__main__":
	main()
