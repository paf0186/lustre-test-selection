"""A predictor from principles (principles.toml): which parts of Lustre a patch
changes, and what tests and configurations exercise those parts.  Nothing in it
is learned from the training set, so it says something about code with no
failure history."""
from __future__ import annotations

import functools
import tomllib
from collections import defaultdict
from pathlib import Path

from .baselines import edited_scripts, lines_for

RULES = Path(__file__).resolve().parent / "principles.toml"


@functools.cache
def _load(path: Path) -> tuple[dict, ...]:
	return tuple(tomllib.loads(path.read_text())["rule"])


def load(path: Path = RULES) -> list[dict]:
	return list(_load(path))


NO_TEST_KINDS = {"NO_CODE_CHANGE", "NO_CHANGE"}


def needs_no_testing(inp: dict) -> bool:
	"""Documentation only, or a patchset Gerrit says changes no code."""
	files = inp.get("files") or []
	if (inp.get("gerrit_kind") or "") in NO_TEST_KINDS:
		return True
	return bool(files) and all(f.startswith(("Documentation/", "lustre/doc/", "contrib/")) or
				   f.endswith((".md", ".txt")) or f in ("LICENSE", "README", "ChangeLog")
				   for f in files)


def must(inp: dict, rules: list[dict] | None = None) -> tuple[dict[str, set[str]], list[str]]:
	"""The test sets that belong to the code areas the patch changes: they run
	whatever else is selected (the rules' `must` lists)."""
	from collections import defaultdict
	picks: dict[str, set[str]] = defaultdict(set)
	why = []
	if needs_no_testing(inp):
		return picks, why
	for rule in rules if rules is not None else load():
		if not rule.get("must"):
			continue
		hit = [f for f in inp.get("files") or [] if matches(rule, f)]
		if hit:
			for cls in rule.get("classes", ["ldiskfs-dne"]):
				picks[cls].update(rule["must"])
			why.append(f"{rule['name']} must run {', '.join(rule['must'])}: {hit[0]}")
	return picks, why


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

	def narrow(self, inp: dict) -> bool:
		"""Every file matches a narrow rule and no other."""
		files = inp.get("files") or []
		return bool(files) and all(
			any(matches(r, f) for r in self.rules if r.get("narrow"))
			and not any(matches(r, f) for r in self.rules if not r.get("narrow")) for f in files)

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


class OnBlindBase(Principles):
	"""The first `n` tests of the patch-blind order for every patch, unless the
	patch is narrow, plus what the principles add."""

	def __init__(self, order: list[dict], n: int, rules: list[dict] | None = None):
		super().__init__(rules)
		self.order, self.n = order, n
		self.name = f"principles-on-blind-{n}"
		self.title = f"+ principles on the blind base, {n} sets"
		self.about = (f"the first {n} test sets of the patch-blind order, skipped for a patch whose "
			      "files are all narrow (documentation, tests, LNet, lfs), plus the principles' selection")

	def predict(self, inp):
		from classes import CLASSES
		picks, groups, why = self.select(inp)
		if not self.narrow(inp):
			for step in self.order[:self.n]:
				if step.get("group"):
					groups.add(step["group"])
				else:
					picks[step["cls"]].add(step["script"])
		return {"text": "\n".join(["trivial"] + lines_for(picks, CLASSES, groups)), "why": why, "meta": {}}
