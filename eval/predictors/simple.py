"""The simple candidate (EXPERIMENTS.md, E4 and E6): only the parts that held
up, and the principled links that hold whatever the data says.

- A patch that needs no testing (documentation only, or no code change) gets
  `trivial` alone.
- The test sets that belong to the code areas the patch changes (the `must`
  lists of principles.toml) always run.
- The scripts the patch edits run; when the patch changes only test functions
  (no helpers, nothing outside lustre/tests), just those tests run, with ONLY=
  (edited="only"), or nothing beyond Autotest's own subtest-change sessions,
  which already run them (edited="none").
- The first n test sets of the patch-blind order.
- Every test set that caught a bug on an earlier patchset of the same change."""
from __future__ import annotations

from collections import defaultdict

from .baselines import TEST_SCRIPT, edited_scripts, lines_for
from .principles import must, needs_no_testing

FRAMEWORK = ("lustre/tests/test-framework.sh", "lustre/tests/cfg/", "lustre/tests/functions.sh")


def edited_tests_only(inp: dict) -> dict[str, list[str]] | None:
	"""{script: tests} when the patch changes nothing but test functions of test
	scripts; None otherwise."""
	files = inp.get("files") or []
	if not files or not all(TEST_SCRIPT.match(f) and not f.startswith(FRAMEWORK) for f in files):
		return None
	out = {}
	for script, ch in (inp.get("scripts") or {}).items():
		if ch.get("helpers") or not ch.get("tests"):
			return None
		out[script] = sorted(ch["tests"])
	return out or None


class Simple:
	def __init__(self, order: list[dict], n: int, *, edited: str = "whole", use_must: bool = False):
		self.order, self.n, self.edited, self.use_must = order, n, edited, use_must
		self.name = f"simple-{n}"

	def predict(self, inp: dict) -> dict:
		from classes import CLASSES
		if needs_no_testing(inp):
			return {"text": "trivial", "why": ["needs no testing"], "meta": {}}
		picks: dict[str, set[str]] = defaultdict(set)
		groups: set[str] = set()
		extra, why = [], []
		only = edited_tests_only(inp) if self.edited != "whole" else None
		if only:
			if self.edited == "only":
				for script, tests in sorted(only.items()):
					extra.append(f'testlist={script} env=ONLY="{" ".join(t.removeprefix("test_") for t in tests)}"')
				why.append(f"edits only tests: {', '.join(f'{s} {len(t)}' for s, t in only.items())}")
		else:
			for s in edited_scripts(inp):
				picks["ldiskfs-dne"].add(s)
		if self.use_must:
			mp, mw = must(inp)
			for cls, scripts in mp.items():
				picks[cls] |= scripts
			why += mw
		for step in self.order[:self.n]:
			if step.get("group"):
				groups.add(step["group"])
			else:
				picks[step["cls"]].add(step["script"])
		for key in (inp.get("history") or {}).get("caught", []):
			script, cls = key.split("|", 1)
			picks[cls].add(script)
		return {"text": "\n".join(["trivial"] + lines_for(picks, CLASSES, groups) + extra), "why": why, "meta": {}}
