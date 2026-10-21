"""The patch-blind baseline: the same tests for every patch, the most cost-effective
first.  Ranked greedily on the training set: each step adds the script and
configuration that catches the most bugs not yet caught per second it adds (its
run time, plus a session's setup when its configuration is not running yet).
Every prefix of the order is a selection; together they trace the curve a
patch-aware predictor has to beat."""
from __future__ import annotations

from collections import defaultdict

from .baselines import lines_for


def candidates(costs: dict, classes: dict, matrix_only: bool = True) -> dict[str, dict]:
	"""(script, class) pairs that keys can ask for, and whole groups for the rest;
	only pairs the review matrix runs, unless matrix_only is off."""
	out = {}
	matrix = set(costs.get("matrix") or ()) if matrix_only else None
	for key, secs in costs["suite_class_s"].items():
		script, cls = key.split("|")
		if matrix is not None and key not in matrix:
			continue
		if cls in classes and classes[cls][0] is not None:
			out[key] = {"script": script, "cls": cls, "secs": secs, "keys": [key]}
	for group, suites in costs["group_suites"].items():
		cls = costs["group_class"].get(group)
		if cls in classes and classes[cls][0] is None and suites:
			out[f"group:{group}"] = {"group": group, "cls": cls,
						 "secs": costs["session_s"].get(group, 0),
						 "keys": [f"{s}|{cls}" for s in suites]}
	return out


def rank(cases: list[dict], costs: dict, classes: dict, matrix_only: bool = True) -> list[dict]:
	"""The greedy order, with each step's gain on the given (training) cases."""
	per_change = defaultdict(int)
	for c in cases:
		if c["faulty"]:
			per_change[c["change"]] += len(c["bugs"])
	miss = {}                                   # (case, bug) -> chance still missed
	weight = {}
	by_key = defaultdict(list)                  # evidence key -> [(case, bug, f/r)]
	for ci, c in enumerate(cases):
		if not c["faulty"]:
			continue
		for bi, b in enumerate(c["bugs"]):
			miss[(ci, bi)] = 1 - b["always"]
			weight[(ci, bi)] = 1 / per_change[c["change"]]
			for k, (r, f) in b["evidence"].items():
				if r:
					by_key[k].append((ci, bi, f / r))
	total = sum(weight.values())
	cands = candidates(costs, classes, matrix_only)
	running: set[str] = set()                   # classes with a session already
	order = []
	while cands:
		best, best_v = None, 0.0
		for name, cand in cands.items():
			gain = 0.0
			for k in cand["keys"]:
				for ci, bi, p in by_key.get(k, ()):
					gain += weight[(ci, bi)] * miss[(ci, bi)] * p
			secs = cand["secs"] + (0 if cand.get("group") or cand["cls"] in running
					       else costs["overhead_s"])
			v = gain / secs if secs else 0
			if v > best_v:
				best, best_v = name, v
		if best is None:
			break
		cand = cands.pop(best)
		gained = 0.0
		for k in cand["keys"]:
			for ci, bi, p in by_key.get(k, ()):
				gained += weight[(ci, bi)] * miss[(ci, bi)] * p
				miss[(ci, bi)] *= 1 - p
		if not cand.get("group"):
			running.add(cand["cls"])
		order.append({"pick": best, **{k: v for k, v in cand.items() if k != "keys"},
			      "gain": gained / total})
	return order


def prediction(order: list[dict], n: int, classes: dict) -> str:
	picks: dict[str, set[str]] = defaultdict(set)
	groups = set()
	for step in order[:n]:
		if step.get("group"):
			groups.add(step["group"])
		else:
			picks[step["cls"]].add(step["script"])
	return "\n".join(["trivial"] + lines_for(picks, classes, groups))
