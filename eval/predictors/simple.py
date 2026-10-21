"""The simple candidate (EXPERIMENTS.md, E4 and E6): only the parts that held
up, and the principled links that hold whatever the data says.

- A patch that needs no testing (documentation only, or no code change) gets
  `trivial` alone.
- The test sets that belong to the code areas the patch changes (the `must`
  lists of principles.toml) always run; a test script is not code of an area
  (must_skips_tests, E8).
- The scripts the patch edits run; when the patch changes only test functions
  (no helpers, nothing outside lustre/tests), just those tests run, with ONLY=
  (edited="only"), or nothing beyond Autotest's own subtest-change sessions,
  which already run them (edited="none").
- The first n test sets of the patch-blind order, unless the patch changes
  only test scripts (tests_skip_base, E8).
- Every test set that caught a bug on an earlier patchset of the same change,
  or on the last few faulty ones (history_cap, E7)."""
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


def test_scripts_only(inp: dict) -> bool:
	"""The patch changes test scripts and nothing else (not the framework)."""
	files = inp.get("files") or []
	return bool(files) and all(TEST_SCRIPT.match(f) and not f.startswith(FRAMEWORK) for f in files)


def caught(history: dict, cap: int | None = None) -> list[str]:
	"""What caught a bug on the change's earlier patchsets: all of them, or the
	last `cap` faulty ones."""
	if cap is None:
		return history.get("caught", [])
	if cap <= 0:
		return []
	return sorted({k for _, keys in history.get("by_patchset", [])[-cap:] for k in keys})


class Simple:
	def __init__(self, order: list[dict], n: int, *, edited: str = "whole", use_must: bool = False,
		     history_cap: int | None = None, tests_skip_base: bool = False, must_skips_tests: bool = False):
		self.order, self.n, self.edited, self.use_must = order, n, edited, use_must
		self.history_cap = history_cap
		self.tests_skip_base, self.must_skips_tests = tests_skip_base, must_skips_tests
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
			code = inp
			if self.must_skips_tests:
				code = dict(inp, files=[f for f in inp.get("files") or [] if not TEST_SCRIPT.match(f)])
			mp, mw = must(code) if code.get("files") else ({}, [])
			for cls, scripts in mp.items():
				picks[cls] |= scripts
			why += mw
		base = self.n
		if self.tests_skip_base and test_scripts_only(inp):
			base = 0
			why.append("changes only test scripts: no patch-blind base")
		for step in self.order[:base]:
			if step.get("group"):
				groups.add(step["group"])
			else:
				picks[step["cls"]].add(step["script"])
		if base:
			why.append(f"the first {base} test sets of the patch-blind order")
		history = inp.get("history") or {}
		keys = caught(history, self.history_cap)
		for key in keys:
			script, cls = key.split("|", 1)
			picks[cls].add(script)
		if keys:
			last = self.history_cap is not None and history.get("faulty_patchsets", 0) > self.history_cap
			why.append(f"caught a bug on {'the last faulty' if last and self.history_cap == 1 else 'an earlier'} "
				   f"patchset: {', '.join(keys)}" +
				   (f" ({history['faulty_patchsets']} faulty patchsets in all)" if last else ""))
		return {"text": "\n".join(["trivial"] + lines_for(picks, CLASSES, groups) + extra), "why": why, "meta": {}}
