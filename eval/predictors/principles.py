"""A predictor from principles (principles.toml): which parts of Lustre a patch
changes, and what tests and configurations exercise those parts.  Nothing in it
is learned from the training set, so it says something about code with no
failure history."""
from __future__ import annotations

import tomllib
from collections import defaultdict
from pathlib import Path

from .baselines import edited_scripts, lines_for

RULES = Path(__file__).resolve().parent / "principles.toml"


def load(path: Path = RULES) -> list[dict]:
	return tomllib.loads(path.read_text())["rule"]


def matches(rule: dict, path: str) -> bool:
	return any(path.startswith(p) for p in rule.get("paths", ())) or \
		any(c in path for c in rule.get("contains", ()))


class Principles:
	name = "principles"
	title = "+ principles"
	about = ("trivial and edited scripts, plus the suites and configurations that exercise each part "
		 "of Lustre the patch changes (eval/predictors/principles.toml); nothing learned")

	def __init__(self, rules: list[dict] | None = None):
		self.rules = rules if rules is not None else load()

	def fit(self, cases):
		return self

	def select(self, inp: dict) -> tuple[dict[str, set[str]], set[str], list[str]]:
		picks: dict[str, set[str]] = defaultdict(set)
		groups: set[str] = set()
		why = []
		for s in edited_scripts(inp):
			picks["ldiskfs-dne"].add(s)
		for rule in self.rules:
			hit = [f for f in inp.get("files") or [] if matches(rule, f)]
			if not hit:
				continue
			for cls in rule.get("classes", ["ldiskfs-dne"]):
				picks[cls].update(rule["scripts"])
			if rule.get("group"):
				groups.add(rule["group"])
			why.append(f"{rule['name']}: {hit[0]}{' ...' if len(hit) > 1 else ''}")
		return picks, groups, why

	def predict(self, inp):
		from classes import CLASSES
		picks, groups, why = self.select(inp)
		return {"text": "\n".join(["trivial"] + lines_for(picks, CLASSES, groups)), "why": why, "meta": {}}


class PrinciplesAndHistory(Principles):
	"""The principles, plus what the directory history adds."""

	def __init__(self, table, rules: list[dict] | None = None):
		super().__init__(rules)
		self.table = table
		self.name = f"principles+{table.name}"
		self.title = f"+ principles and directory history, {table.share:.0%}"
		self.about = ("the principles' selection, plus what the directory history adds at "
			      f"{table.share:.0%}")

	def fit(self, cases):
		self.table.fit(cases)
		return self

	def predict(self, inp):
		from classes import CLASSES
		picks, groups, why = self.select(inp)
		for cls, scripts in self.table.picks(inp).items():
			picks[cls] |= scripts
		return {"text": "\n".join(["trivial"] + lines_for(picks, CLASSES, groups)), "why": why, "meta": {}}
