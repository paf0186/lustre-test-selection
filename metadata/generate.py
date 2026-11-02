#!/usr/bin/env python3
"""Ask a model for the header of each subtest in extract.jsonl.  One call per
subtest, with the facts extract.py found given as settled.  Answers are cached
in data/metadata/<model>/, with usage beside them, so a rerun costs only what
is missing and the price of a run is measured.

  metadata/generate.py data/metadata/extract.jsonl --model terra --sample 12
  metadata/generate.py data/metadata/extract.jsonl --model sonnet --suite sanity
  metadata/generate.py data/metadata/extract.jsonl --model sonnet --report
  metadata/generate.py data/metadata/extract.jsonl --model opus --only queue.json

Runs `claude -p`, or Codex through ~/astra/run.sh with ASTRA_EFFORT=low.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import keywords as kw  # noqa: E402
import schema  # noqa: E402

ROOT = HERE.parent
CACHE = ROOT / "data" / "metadata"
MODELS = {"sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5", "terra": "gpt-6.1-terra"}
# dollars per million tokens: input, cached input, output (Terra as reported, not from OpenAI's page)
PRICES = {"sonnet": (2.0, 0.2, 10.0), "opus": (4.0, 0.2, 20.0), "terra": (1.0, 0.05, 5.0)}
MAX_HELPERS = 6000

RULES = """You write metadata headers for Lustre test subtests. Reply with one fenced yaml
block and nothing else. Do not use tools. Use only facts in the input, and ASCII only.
Never put a backslash in a string: an apostrophe inside double quotes needs no escape.

Fields:
- summary: exactly as given.
- commit: the commits from the history that added the subtest or changed what it
  tests, oldest first, leaving out mechanical changes and a commit and its revert.
  Each entry is one double-quoted string: "<12-char hash> ('<subject>')", with double
  quotes in the subject changed to single quotes.
- description: 2 to 4 lines on what is checked and what bug or feature it guards.
- keywords: a list from the keyword list only, at least one module and one feature.
- requires: everything from the skip checks is added for you, including features,
  configuration, tools, target counts and versions. Give only what follows, and leave
  the field out if there is none. mdscount and ostcount count targets, and any number
  of targets can share one host, so they say nothing about hosts. State what HARD
  failover needs: it powers off a whole host, with every target and client on it.
  Most subtests need no node field. Add mdsnodes or ossnodes (2 or more) only if a
  server of that kind must stay up while another is failed, or the code cannot work
  with them on one host, and either "hosts" is true or "failover" is in the facts. Add
  clientcount only if it needs more than one client node, not just a second mount.
  Add reason only beside one of these, to say why.
- start, end: keep the values in the facts. Otherwise give them only if not mounted,
  and then only as mounted, unmounted, stopped or formatted.
- modifies: every kind of state it changes that another subtest running at the same
  time could see. A setting is state: fail_loc and tunables are separate entries,
  and each one rules out running beside other subtests. Changes confined to files
  and directories under its own $DIR/$tdir are not state: restriping its own
  directory is not layout, but a layout on $MOUNT is. Use [] if there are none.
  Anything in the evidence in the facts is added for you, so name what it missed.
  Use the client_ entries when the change is on the client running the subtest, and
  the plain ones when it is on a server. space means it uses much of the filesystem,
  host means it changes the OS, modules or kernel state of a node.
- observes: every kind of shared state whose value it measures or depends on, so that
  a neighbour's activity could change its result: stats and counters, grant, free
  space, object or lock counts, cache contents, debug logs, console messages. Use
  namespace if it scans or lists all of $DIR or $MOUNT, and timing if its result is
  a rate or an elapsed time, or its load would slow a neighbour. Files under its own
  $DIR/$tdir do not count. quiesced means it waits for the whole filesystem to go
  idle, as wait_delete_completed does. Use [] if there are none.
- parallel_fix: one line, under 100 characters, naming a small edit to this subtest
  that keeps what it tests and removes every entry in modifies and observes, such as
  "scan $DIR/$tdir, not all of $DIR" or "use $tfile, not a fixed name". Give it only
  if the entries are all in: {fixable}.
- parallel: false, and slow: true, if the facts say so.
Leave out any field with nothing to say except modifies and observes, which are always
present. Do not give duration.

modifies values: {modifies}
observes values: {observes}
keyword list: {keywords}
"""


def system_prompt():
	vocab, _ = kw.load()
	return RULES.format(modifies=", ".join(sorted(schema.MODIFIES - schema.MECHANICAL)),
			    observes=", ".join(sorted(schema.OBSERVES)),
			    fixable=", ".join(sorted(schema.FIXABLE)),
			    keywords=", ".join(sorted(vocab)))


def user_prompt(rec):
	helpers = ""
	for src in rec["helpers"].values():
		if len(helpers) < MAX_HELPERS:
			helpers += src[:2500] + "\n\n"
	history = "\n\n".join(f"{c['hash']} {c['subject']}\n{c['body']}" for c in rec["history"])
	facts = {k: v for k, v in rec["facts"].items() if k != "duration"}
	return (f"Suite: {rec['suite']}\nSubtest: {rec['id']}\nSummary: {rec['summary']}\n\n"
		f"## Facts\n{json.dumps(facts)}\n\n## Subtest\n{rec['code']}\n\n"
		f"## Suite helpers it calls\n{helpers}\n## History\n{history}\n")


def ask_claude(model, system, prompt):
	start = time.time()
	# cwd outside any project, or claude reads CLAUDE.md into every call
	with tempfile.TemporaryDirectory() as tmp:
		out = subprocess.run(["claude", "-p", "--model", MODELS[model], "--effort", "low",
				      "--output-format", "json", "--tools", "", "--no-session-persistence",
				      "--system-prompt", system], input=prompt, capture_output=True,
				     text=True, timeout=300, check=False, cwd=tmp)
	data = json.loads(out.stdout)
	if data.get("is_error"):
		raise RuntimeError(data.get("result"))
	u = data["usage"]
	cached = u.get("cache_read_input_tokens", 0)
	return data["result"], {"input": u["input_tokens"] + u.get("cache_creation_input_tokens", 0) + cached,
				"cached": cached, "output": u["output_tokens"],
				"dollars": data.get("total_cost_usd"), "seconds": time.time() - start}


def ask_codex(model, system, prompt):
	start = time.time()
	with tempfile.TemporaryDirectory() as tmp:
		path = Path(tmp)
		(path / "prompt.md").write_text(f"{system}\n{prompt}", encoding="utf-8")
		env = {**os.environ, "ASTRA_MODEL": MODELS[model], "ASTRA_EFFORT": "low",
		       "ASTRA_SANDBOX": "read-only"}
		subprocess.run([os.path.expanduser("~/astra/run.sh"), tmp, tmp, str(path / "prompt.md"), "280"],
			       capture_output=True, text=True, env=env, check=False)
		status = (path / "status").read_text(encoding="utf-8")
		if " DONE " not in status:
			raise RuntimeError(status[:200])
		text = (path / "final.md").read_text(encoding="utf-8")
		sid = re.search(r"session=(\S+)", status).group(1)
	usage = {}
	for f in Path.home().glob(f".codex/sessions/*/*/*/*{sid}.jsonl"):
		found = re.findall(r'"total_token_usage":(\{[^}]*\})', f.read_text(encoding="utf-8"))
		usage = json.loads(found[-1]) if found else {}
	return text, {"input": usage.get("input_tokens", 0), "cached": usage.get("cached_input_tokens", 0),
		      "output": usage.get("output_tokens", 0), "dollars": None, "seconds": time.time() - start}


def list_price(model, usage):
	fresh, cached, out = PRICES[model]
	return ((usage["input"] - usage["cached"]) * fresh + usage["cached"] * cached
		+ usage["output"] * out) / 1e6


def run_one(model, system, rec, force=False):
	name = f"{rec['suite']}_{rec['id']}"
	path = CACHE / model / f"{name}.txt"
	if path.exists() and not force:
		return "cached"
	ask = ask_codex if model == "terra" else ask_claude
	try:
		text, usage = ask(model, system, user_prompt(rec))
	except (RuntimeError, ValueError, KeyError, OSError, subprocess.SubprocessError) as e:
		print(f"{name}: {e}", file=sys.stderr)
		return "failed"
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(text, encoding="utf-8")
	path.with_suffix(".json").write_text(json.dumps(usage), encoding="utf-8")
	return "asked"


def report(model):
	runs = [json.loads(p.read_text()) for p in (CACHE / model).glob("*.json")]
	if not runs:
		return {"answers": 0}
	n = len(runs)
	return {"answers": n, "mean_input": sum(r["input"] for r in runs) // n,
		"mean_cached": sum(r["cached"] for r in runs) // n, "mean_output": sum(r["output"] for r in runs) // n,
		"mean_seconds": round(sum(r["seconds"] for r in runs) / n, 1),
		"list_dollars_each": round(sum(list_price(model, r) for r in runs) / n, 4)}


def main():
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("extract")
	ap.add_argument("--model", choices=MODELS, required=True)
	ap.add_argument("--suite")
	ap.add_argument("--ids", help="comma separated suite:id")
	ap.add_argument("--only", help="queue.json from reconcile.py")
	ap.add_argument("--sample", type=int, help="this many, seeded")
	ap.add_argument("--workers", type=int, default=4)
	ap.add_argument("--force", action="store_true", help="ask again")
	ap.add_argument("--report", action="store_true", help="what the cached answers cost")
	args = ap.parse_args()
	if args.report:
		print(json.dumps(report(args.model)))
		return
	with open(args.extract, encoding="utf-8") as f:
		records = [json.loads(x) for x in f]
	if args.suite:
		records = [r for r in records if r["suite"] == args.suite]
	if args.ids:
		ids = set(args.ids.split(","))
		records = [r for r in records if f"{r['suite']}:{r['id']}" in ids]
	if args.only:
		with open(args.only, encoding="utf-8") as f:
			ids = {f"{q['suite']}:{q['id']}" for q in json.load(f)}
		records = [r for r in records if f"{r['suite']}:{r['id']}" in ids]
	if args.sample:
		records = random.Random(1).sample(records, min(args.sample, len(records)))
	system = system_prompt()
	with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
		results = list(pool.map(lambda r: run_one(args.model, system, r, args.force), records))
	print(json.dumps({k: results.count(k) for k in sorted(set(results))} | {"report": report(args.model)}))


if __name__ == "__main__":
	main()
