#!/usr/bin/env python3
"""LLM feature extraction (design 3.3): an LLM reads a patch and says which
mechanisms of Lustre it puts at risk, from a fixed vocabulary.  The learned model
takes those as features beside the principles and history, so the LLM adds
judgement about indirect effects without choosing tests itself.

  eval/llm.py --keys 64955/94,65094/33      # a few patchsets
  eval/llm.py --sample 20 --split train     # a seeded sample
  eval/llm.py --all                         # every case (cached; reruns are free)

Each answer is cached in data/llm/<change>-<patchset>.json with its cost in
dollars, tokens and seconds, so the price of the LLM stage is measured per patch.
Runs `claude -p` (Claude Code, headless).
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import random
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = ROOT / "data" / "llm"
MODEL = "claude-haiku-5-5"
MAX_DIFF = 40000

# mechanism: what it covers (shown to the LLM)
MECHANISMS = {
	"posix-namespace": "create, unlink, rename, link, readdir, permissions, xattrs, ACLs",
	"striped-dirs-dne": "remote and striped directories, cross-MDT operations, migration of directories",
	"data-io": "buffered read/write, page cache, writeback, truncate, fallocate, readahead",
	"direct-io": "O_DIRECT and unaligned direct I/O paths (used by lfs migrate and mirror resync)",
	"mmap": "mmap, page faults, splice",
	"client-locking": "LDLM locks between clients, lock cancel, blocking ASTs, lock conversion",
	"layouts": "file layouts: striping, PFL, setstripe, layout swap, lfs migrate",
	"mirrors-flr": "FLR mirrors, resync, stale mirrors",
	"erasure-coding": "erasure coded layouts",
	"dom": "data on MDT",
	"pools": "OST pools and their config log updates",
	"config-mount": "config logs, MGS/MGC, mount, format, tunables (lctl set_param/conf_param)",
	"recovery-replay": "server restart, replay, resend, eviction, imports, failover",
	"object-storage": "OST objects: precreate, destroy, orphan cleanup, grants, OSP",
	"quota": "quota accounting and enforcement, project quota",
	"lfsck-scrub": "LFSCK, OI scrub, consistency of FIDs and layouts on disk",
	"hsm-pcc": "HSM archive/restore, coordinator, copytool, persistent client cache",
	"changelog": "changelogs and their consumers",
	"security-nodemap": "GSS/Kerberos, shared keys, nodemaps, identity upcalls, SELinux",
	"encryption": "client file encryption",
	"compression": "client data compression",
	"lnet-network": "LNet: networks, routing, peers, discovery, selftest",
	"ldiskfs-backend": "the ldiskfs OSD and its kernel patches",
	"zfs-backend": "the ZFS OSD",
	"kernel-compat": "builds and compatibility with other kernels, distros or architectures (ARM, newest kernels)",
	"userspace-tools": "lfs, lctl, llapi, other tools",
	"test-framework": "test-framework helpers used by many suites",
	"memory-lifetime": "object lifetimes, refcounts, teardown and memory use (crashes far from the change)",
}

PROMPT = """You are a Lustre file system expert deciding what testing a patch needs.
Read the commit message and diff below. For each mechanism in the list, rate how
likely this patch is to break something in it: 0 no, 1 possible, 2 likely,
3 almost certain. Think about indirect effects (a direct I/O change also affects
lfs migrate and mirror resync; a refcount change can crash far from the change).
Also rate the overall chance the patch has a bug any test would catch (0 to 3).

Mechanisms:
{mechanisms}

Answer with only a JSON object, no prose:
{{"mechanisms": {{"<name>": <0-3>, ...}}, "overall": <0-3>, "why": "<one sentence>"}}
List only mechanisms rated 1 or more.

Commit message:
{message}

Diff (may be truncated):
{diff}
"""


def ask(case: dict, model: str = MODEL) -> dict:
	inp = case["input"]
	diff = inp.get("diff") or ""
	if len(diff) > MAX_DIFF:
		diff = diff[:MAX_DIFF] + "\n[... truncated ...]\n"
	prompt = PROMPT.format(mechanisms="\n".join(f"- {k}: {v}" for k, v in MECHANISMS.items()),
			       message=inp.get("message") or inp.get("subject") or "", diff=diff)
	t0 = time.time()
	r = subprocess.run(["claude", "-p", "--model", model, "--output-format", "json"],
			   input=prompt, capture_output=True, text=True, timeout=300)
	secs = time.time() - t0
	out = {"key": case["key"], "model": model, "seconds": round(secs, 1)}
	try:
		env = json.loads(r.stdout)
		out["dollars"] = env.get("total_cost_usd")
		out["usage"] = env.get("usage")
		text = env.get("result") or ""
		text = text[text.find("{"):text.rfind("}") + 1]
		ans = json.loads(text)
		out["mechanisms"] = {k: int(v) for k, v in (ans.get("mechanisms") or {}).items() if k in MECHANISMS}
		out["overall"] = int(ans.get("overall") or 0)
		out["why"] = ans.get("why")
	except (ValueError, TypeError, AttributeError) as e:
		out["error"] = f"{type(e).__name__}: {e}; stdout={r.stdout[:300]!r} stderr={r.stderr[:300]!r}"
	return out


def cached(key: str) -> dict | None:
	f = CACHE / (key.replace("/", "-") + ".json")
	return json.loads(f.read_text()) if f.exists() else None


def run(cases: list[dict], workers: int, model: str) -> None:
	CACHE.mkdir(parents=True, exist_ok=True)
	todo = [c for c in cases if cached(c["key"]) is None or "error" in cached(c["key"])]
	spent = secs = 0.0
	with concurrent.futures.ThreadPoolExecutor(workers) as ex:
		for i, out in enumerate(ex.map(lambda c: ask(c, model), todo), 1):
			(CACHE / (out["key"].replace("/", "-") + ".json")).write_text(json.dumps(out) + "\n")
			spent += out.get("dollars") or 0
			secs += out["seconds"]
			if i % 10 == 0 or i == len(todo):
				print(f"  {i}/{len(todo)}: ${spent:.2f}, {secs / i:.1f} s each"
				      f"{', error: ' + out['error'][:120] if 'error' in out else ''}", file=sys.stderr)


def main() -> None:
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("--cases", default=str(ROOT / "data" / "cases.jsonl"))
	ap.add_argument("--keys")
	ap.add_argument("--sample", type=int)
	ap.add_argument("--split", choices=["train", "test"])
	ap.add_argument("--all", action="store_true")
	ap.add_argument("--workers", type=int, default=6)
	ap.add_argument("--model", default=MODEL)
	args = ap.parse_args()
	cases = [json.loads(x) for x in open(args.cases)]
	if args.split:
		cases = [c for c in cases if c["split"] == args.split]
	if args.keys:
		want = set(args.keys.split(","))
		cases = [c for c in cases if c["key"] in want]
	elif args.sample:
		rng = random.Random(20261020)
		cases = rng.sample(cases, min(args.sample, len(cases)))
	elif not args.all:
		ap.error("--keys, --sample or --all")
	run(cases, args.workers, args.model)


if __name__ == "__main__":
	main()
