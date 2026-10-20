"""The oracle: for each patch, knowing its bugs, the tests that catch them for the
least session time.  No predictor can do better, so its curve is the ceiling and
the patch-blind baseline the floor: what reading patches can win lies between.

For each patch, tests are added greedily by bugs caught per hour, while the next
is worth at least `rate`; a clean patch gets nothing beyond trivial.  Sweeping
`rate` traces the curve."""
from __future__ import annotations

from collections import defaultdict

from .baselines import lines_for

RATES = [1e9, 2.0, 1.0, 0.5, 0.3, 0.2, 0.1, 0.05, 0.02, 0.01, 0.005, 0.001, 0.0]


def selection(case: dict, costs: dict, classes: dict, rate: float) -> str:
	gains = defaultdict(list)                   # key -> [(bug, chance)]
	miss = {}
	for i, b in enumerate(case["bugs"]):
		miss[i] = 1 - b["always"]
		for k, (r, f) in b["evidence"].items():
			if r and f:
				gains[k].append((i, f / r))
	group_of = {}
	for g, cls in costs["group_class"].items():
		if classes.get(cls, (None,))[0] is None and g in costs["group_suites"]:
			for s in costs["group_suites"][g]:
				group_of.setdefault(f"{s}|{cls}", g)
	picks: dict[str, set[str]] = defaultdict(set)
	groups: set[str] = set()
	running: set[str] = set()
	while gains:
		best, best_v = None, -1.0
		for k, gs in gains.items():
			script, cls = k.split("|")
			if cls not in classes or (classes[cls][0] is None and k not in group_of):
				continue
			if k in group_of:
				g = group_of[k]
				whole = costs["session_s"].get(g) or costs["overhead_s"] + sum(
					costs["suite_class_s"].get(f"{x}|{cls}", costs["suite_s"].get(x, 1800))
					for x in costs["group_suites"][g])
				secs = 0 if g in groups else whole
			else:
				secs = costs["suite_class_s"].get(k, costs["suite_s"].get(script, 1800)) \
					+ (0 if cls in running else costs["overhead_s"])
			gain = sum(miss[i] * p for i, p in gs)
			v = gain / (secs / 3600) if secs else float("inf")
			if v > best_v:
				best, best_v = k, v
		if best is None or best_v < rate:
			break
		for i, p in gains.pop(best):
			miss[i] *= 1 - p
		script, cls = best.split("|")
		if best in group_of:
			groups.add(group_of[best])
		else:
			picks[cls].add(script)
			running.add(cls)
	return "\n".join(["trivial"] + lines_for(picks, classes, groups))
