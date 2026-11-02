#!/usr/bin/env python3
"""Merge two readers' headers into data/metadata/headers/<suite>.yaml, and list
the subtests a third reader must settle.

Lists are merged, since for modifies and observes the cautious answer is the
union and the validator has already removed what is off the list.  The fields in
schema.AGREE must match, leaving out feature names, which are free text.  If only
one reader's header has an error, the other's stands, with the first's lists still
added.  A subtest goes in the queue otherwise; an answer from the adjudicator,
when given, must be free of errors and replaces both.

  metadata/reconcile.py data/metadata/extract.jsonl data/metadata/terra data/metadata/sonnet \
	--adjudicator data/metadata/opus --queue data/metadata/queue.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import keywords as kw  # noqa: E402
import schema  # noqa: E402
import validate  # noqa: E402

ROOT = HERE.parent


def read(directory, rec, vocab, aliases):
	"""(header, [errors]) of one reader, header None if it has no answer."""
	path = Path(directory) / f"{rec['suite']}_{rec['id']}.txt"
	if not path.exists():
		return None, ["no answer"]
	header, error = validate.load_answer(path.read_text(encoding="utf-8"))
	if error:
		return None, [error]
	validate.repair(rec, header, vocab, aliases)
	issues = validate.check(rec, header, vocab, aliases)
	return header, [f"{f}: {m}" for level, f, m in issues if level == "error"]


def normal(key, header):
	value = header.get(key)
	if key == "requires":
		value = {k: v for k, v in (value or {}).items() if k not in ("reason", "feature")}
		return {k: (sorted(v) if isinstance(v, list) else str(v)) for k, v in value.items()}
	if key == "start":
		return value or "mounted"
	return value


def merge(rec, first, second):
	"""first with the lists of second added, leaving out what is off the list."""
	out = dict(first)
	allowed = {"modifies": schema.MODIFIES, "observes": schema.OBSERVES}
	for key in ("keywords", "modifies", "observes"):
		theirs = set(second.get(key) or [])
		out[key] = sorted(set(first.get(key) or []) | (theirs & allowed[key] if key in allowed else theirs))
	for key in ("parallel", "slow", "parallel_fix"):
		if key not in out and key in second:
			out[key] = second[key]
	commits = {str(c).split()[0]: c for c in (first.get("commit") or []) + (second.get("commit") or [])
		   if str(c).split()[0] in {h["hash"] for h in rec["history"]}}
	order = [c["hash"] for c in rec["history"]]
	if commits:
		out["commit"] = [commits[h] for h in order if h in commits]
	requires = dict(second.get("requires") or {})
	requires.update(first.get("requires") or {})
	if requires:
		out["requires"] = requires
	return out


def finish(rec, header):
	"""The header as stored: with the CI duration, and what follows from it."""
	header = dict(header)
	if "duration" in rec["facts"]:
		header["duration"] = rec["facts"]["duration"]
	if "failover" in rec["facts"]:
		header["failover"] = rec["facts"]["failover"]
	for key in ("depends", "failovers"):
		if key in rec["facts"]:
			header[key] = rec["facts"][key]
	if rec["facts"]["unparsed_skips"]:
		header["unparsed"] = len(rec["facts"]["unparsed_skips"])
	header["parallel_safe"] = schema.parallel_safe(header)
	header["isolation"] = schema.isolation(header)
	if (rec.get("suite_facts") or {}).get("own_mounts"):
		header["parallel_safe"], header["isolation"] = False, "cluster"
	header["min_vms"] = schema.min_vms(header)
	header["min_vms_soft"] = schema.min_vms(header, "soft")
	if not schema.parallel_fixable(header):
		header.pop("parallel_fix", None)
	return header


def reconcile(rec, first, second, tiebreak, vocab, aliases):
	"""(header, reasons): a header if settled, reasons if it must be queued."""
	a, a_err = read(first, rec, vocab, aliases)
	b, b_err = read(second, rec, vocab, aliases)
	reasons = [f"first {x}" for x in a_err] + [f"second {x}" for x in b_err]
	if a is not None and b is not None and bool(a_err) != bool(b_err):
		clean, other = (b, a) if a_err else (a, b)
		return finish(rec, merge(rec, clean, {k: v for k, v in other.items() if k != "requires"})), []
	if a is not None and b is not None:
		reasons += [f"{k}: {normal(k, a)} or {normal(k, b)}" for k in schema.AGREE
			    if normal(k, a) != normal(k, b)]
	if not reasons:
		return finish(rec, merge(rec, a, b)), []
	if tiebreak:
		c, c_err = read(tiebreak, rec, vocab, aliases)
		if c is not None and not c_err:
			return finish(rec, c), []
		reasons += [f"adjudicator {x}" for x in c_err]
	return None, reasons


def main():
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("extract")
	ap.add_argument("first")
	ap.add_argument("second")
	ap.add_argument("--adjudicator")
	ap.add_argument("--queue", default=str(ROOT / "data" / "metadata" / "queue.json"))
	ap.add_argument("--out", default=str(ROOT / "data" / "metadata" / "headers"))
	ap.add_argument("--vms", default=str(ROOT / "data" / "metadata" / "vms.json"))
	args = ap.parse_args()
	vocab, aliases = kw.load()
	with open(args.extract, encoding="utf-8") as f:
		records = [json.loads(x) for x in f]
	by_suite = defaultdict(dict)
	suite_wide = {rec["suite"]: rec.get("suite_facts") for rec in records}
	queue = []
	safe = Counter()
	sizes = defaultdict(list)
	for rec in records:
		header, reasons = reconcile(rec, args.first, args.second, args.adjudicator, vocab, aliases)
		sizes[rec["suite"]].append(header or {"requires": rec["facts"]["requires"],
						      "failover": rec["facts"].get("failover")})
		if header is None:
			if not all(r.endswith(" no answer") for r in reasons):
				queue.append({"suite": rec["suite"], "id": rec["id"], "reasons": reasons})
			continue
		by_suite[rec["suite"]][rec["id"]] = header
		safe[str(header["parallel_safe"])] += 1
	out = Path(args.out)
	out.mkdir(parents=True, exist_ok=True)
	for suite, headers in by_suite.items():
		with open(out / f"{suite}.yaml", "w", encoding="utf-8") as f:
			yaml.safe_dump({"suite": suite_wide[suite], "subtests": headers}, f,
				       sort_keys=False, width=88)
	Path(args.queue).parent.mkdir(parents=True, exist_ok=True)
	with open(args.queue, "w", encoding="utf-8") as f:
		json.dump(queue, f, indent=1)
	with open(args.vms, "w", encoding="utf-8") as f:
		json.dump({suite: schema.suite_vms(x) for suite, x in sorted(sizes.items())}, f, indent=1)
	print(json.dumps({"settled": sum(len(x) for x in by_suite.values()), "queued": len(queue),
			  "parallel_safe": safe}))


if __name__ == "__main__":
	main()
