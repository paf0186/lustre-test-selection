"""The simple candidate (EXPERIMENTS.md, E4): only the parts that held up.

`trivial`, the scripts the patch edits, the first n test sets of the patch-blind
order, and every test set that caught a bug on an earlier patchset of the same
change (what CI already said about this change)."""
from __future__ import annotations

from collections import defaultdict

from .baselines import edited_scripts, lines_for


class Simple:
	def __init__(self, order: list[dict], n: int):
		self.order, self.n = order, n
		self.name = f"simple-{n}"
		self.title = "simple: blind base, edited scripts, what caught this change before"

	def picks(self, inp: dict) -> tuple[dict[str, set[str]], set[str]]:
		picks: dict[str, set[str]] = defaultdict(set)
		groups: set[str] = set()
		for s in edited_scripts(inp):
			picks["ldiskfs-dne"].add(s)
		for step in self.order[:self.n]:
			if step.get("group"):
				groups.add(step["group"])
			else:
				picks[step["cls"]].add(step["script"])
		for key in (inp.get("history") or {}).get("caught", []):
			script, cls = key.split("|", 1)
			picks[cls].add(script)
		return picks, groups

	def predict(self, inp: dict) -> dict:
		from classes import CLASSES
		picks, groups = self.picks(inp)
		return {"text": "\n".join(["trivial"] + lines_for(picks, CLASSES, groups)), "meta": {}}
