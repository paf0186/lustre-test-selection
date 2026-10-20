"""The baselines (design 2.6).  A predictor has `fit(train_cases)` and
`predict(input) -> {"text": Test-Parameters text, "meta": {...}}`; it sees only a
case's `input`, never its bugs."""
from __future__ import annotations

import os
import re
from collections import Counter, defaultdict

TEST_SCRIPT = re.compile(r"^lustre/tests/([\w-]+)\.sh$")
NOT_SCRIPTS = {"test-framework", "functions", "sanity-lnet-ref"}


def edited_scripts(inp: dict) -> list[str]:
	"""Test scripts whose tests or helpers the patch changes."""
	out = set((inp.get("scripts") or {}).keys())
	for f in inp.get("files") or []:
		m = TEST_SCRIPT.match(f)
		if m and m.group(1) not in NOT_SCRIPTS:
			out.add(m.group(1))
	return sorted(out - NOT_SCRIPTS)


def lines_for(picks: dict[str, set[str]], classes: dict, groups: set[str] = frozenset()) -> list[str]:
	"""One Test-Parameters line per class, scripts sharing it; a script that needs
	keys of its own (Kerberos) gets its own line; a whole group where asked."""
	from classes import SCRIPT_KEYS
	out = []
	for cls in sorted(picks):
		keys = classes[cls][0]
		if keys is None or not picks[cls]:
			continue
		plain = sorted(s for s in picks[cls] if s not in SCRIPT_KEYS)
		if plain:
			out.append(" ".join(x for x in (keys, "testlist=" + ",".join(plain)) if x))
		for s in sorted(picks[cls] & SCRIPT_KEYS.keys()):
			out.append(" ".join(x for x in (keys, SCRIPT_KEYS[s], "testlist=" + s) if x))
	out += [f"testgroup={g}" for g in sorted(groups)]
	return out


class Trivial:
	name = "trivial"
	title = "trivial only"
	about = "Test-Parameters: trivial and nothing else: review-ldiskfs (sanity, lnet-selftest)"

	def fit(self, cases):
		return self

	def predict(self, inp):
		return {"text": "trivial", "meta": {}}


class Edited:
	"""Trivial, plus every script the patch edits, in DNE ldiskfs."""
	name = "edited"
	title = "+ edited test scripts"
	about = "trivial, plus every test script the patch changes, run once in DNE ldiskfs"

	def fit(self, cases):
		return self

	def predict(self, inp):
		s = edited_scripts(inp)
		return {"text": "trivial" + (f"\ntestlist={','.join(s)}" if s else ""), "meta": {}}


class Table:
	"""Trivial, plus edited scripts, plus what files like these broke in train:
	for each directory the patch changes, the (script, class) pairs that caught a
	bug of a train patchset changing that directory, in at least `share` of them."""

	def __init__(self, share: float = 0.2, min_cases: int = 3):
		self.share, self.min_cases = share, min_cases
		self.name = f"table-{share:g}"
		self.title = f"+ directory history, {share:.0%}"
		self.about = (f"trivial and edited scripts, plus each script and configuration that caught a bug "
			      f"on at least {share:.0%} of the training set's patches changing the same directory")

	def fit(self, cases):
		self.seen = Counter()
		self.hits = defaultdict(Counter)
		for c in cases:
			if not c["faulty"]:
				continue
			dirs = {os.path.dirname(f) for f in c["input"]["files"] or []}
			caught = {k for b in c["bugs"] for k, (r, f) in b["evidence"].items() if f}
			for d in dirs:
				self.seen[d] += 1
				for k in caught:
					self.hits[d][k] += 1
		return self

	def predict(self, inp):
		from classes import CLASSES
		return {"text": "\n".join(["trivial"] + lines_for(self.picks(inp), CLASSES)), "meta": {}}

	def picks(self, inp) -> dict[str, set[str]]:
		picks: dict[str, set[str]] = defaultdict(set)
		for s in edited_scripts(inp):
			picks["ldiskfs-dne"].add(s)
		for d in {os.path.dirname(f) for f in inp.get("files") or []}:
			n = self.seen.get(d, 0)
			if n < self.min_cases:
				continue
			for k, h in self.hits[d].items():
				if h / n >= self.share:
					script, cls = k.split("|")
					picks[cls].add(script)
		return picks
