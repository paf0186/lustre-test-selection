"""Targets: the questions signals answer, each a label for a case.

A target is a function of a case (eval/cases.py) returning 1, 0, or None (no
label: the case is left out).  Register a new one with @target; the scorer and
the evaluation harness take it by name.

Labels must be about the population, not the dataset: cases outside the random
sample are kept only with weight `fraction` (see Scorer)."""
from __future__ import annotations

from typing import Callable

TARGETS: dict[str, tuple[Callable[[dict], int | None], str]] = {}


def target(name: str, doc: str):
	def deco(fn):
		TARGETS[name] = (fn, doc)
		return fn
	return deco


@target("faulty", "The patch had a bug CI caught (Rainshadow's patch faults, grouped into bugs).")
def faulty(case: dict) -> int | None:
	return int(bool(case["faulty"]))


@target("many_bugs", "The patch had two or more distinct bugs CI caught.")
def many_bugs(case: dict) -> int | None:
	return int(len(case["bugs"]) >= 2)


@target("hard_bug", "The patch had a bug in a test it does not edit: harder to foresee.")
def hard_bug(case: dict) -> int | None:
	return int(any(not b["easy"] for b in case["bugs"]))
