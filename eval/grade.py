"""Grade a prediction against a case (design 2.4, 2.5).

A prediction is Test-Parameters text, as a developer would write it: `trivial`,
then one line per class with the scripts to run in it.  It is parsed back into
(script, class) pairs; a line Autotest would not run as meant counts for nothing.

The grade is a pure function of the case, the prediction and the cost model.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

from classes import SCRIPT_KEYS

FLAGS = {"trivial", "fortestonly", "forbuildonly", "optional", "kerberos", "iscsi", "failover"}


@dataclass
class Line:
	cls: str | None
	scripts: list[str]
	keys: dict[str, str] = field(default_factory=dict)
	group: str | None = None
	only: set[str] | None = None        # env=ONLY=...: just these subtests (test_NN names)


ONLY = re.compile(r"(?:^|,)\s*ONLY=(\"[^\"]*\"|'[^']*'|[^,]*)")


def only_of(keys: dict[str, str]) -> set[str] | None:
	env = keys.get("env") or keys.get("envdefinitions") or ""
	m = ONLY.search(env)
	if not m:
		return None
	return {f"test_{t}" for t in m.group(1).strip("\"'").replace(",", " ").split() if t}


def parse_keys(text: str) -> dict[str, str]:
	try:
		toks = shlex.split(text)
	except ValueError:
		toks = text.split()
	out = {}
	for t in toks:
		k, eq, v = t.partition("=")
		out[k.lower()] = v if eq else "true"
	return out


def line_class(keys: dict[str, str]) -> str | None:
	"""The class a custom session with these keys runs in (eval/check_classes.py
	shows which of these are verified), or None if it is none of ours."""
	fs = keys.get("fstype", keys.get("mdtfstype", "ldiskfs")).lower()
	if fs not in ("ldiskfs", "zfs"):
		return None
	mdt = int(keys.get("mdtcount", "4" if keys.get("mdscount", "2") != "1" else "1") or 4)
	dne = mdt > 1
	base = f"{fs}{'-dne' if dne else ''}"
	arch = keys.get("clientarch", "x86_64")
	distro = keys.get("clientdistro", "")
	if arch == "aarch64":
		return f"{base}-arm" if fs == "ldiskfs" else None
	if distro.startswith("ubuntu") or distro.startswith("sles"):
		return f"ldiskfs-{distro[:6].rstrip('0123456789')}" if base == "ldiskfs" else None
	return base


def parse(text: str, costs: dict) -> tuple[bool, list[Line]]:
	"""(trivial asked for, the lines that add sessions)."""
	trivial = False
	lines: list[Line] = []
	for raw in (text or "").replace("\\\n", " ").splitlines():
		raw = raw.strip()
		if raw.lower().startswith("test-parameters:"):
			raw = raw.split(":", 1)[1].strip()
		if not raw:
			continue
		keys = parse_keys(raw)
		if keys.pop("trivial", None):
			trivial = True
		scripts = [s for s in keys.pop("testlist", "").split(",") if s]
		group = keys.pop("testgroup", None)
		if group:
			if group not in costs["group_suites"]:
				continue
			scripts = costs["group_suites"][group] + [s for s in scripts if s not in costs["group_suites"][group]]
			lines.append(Line(costs["group_class"].get(group), scripts, keys, group))
		elif scripts:
			lines.append(Line(line_class(keys), scripts, keys, only=only_of(keys)))
	return trivial, lines


def script_cost(script: str, cls: str | None, costs: dict, only: set[str] | None = None) -> float:
	secs = costs["suite_class_s"].get(f"{script}|{cls}", costs["suite_s"].get(script, 1800))
	if only:
		# its share of the script: the subtests asked for, of the script's usual count
		secs *= min(1.0, len(only) / max(1, costs.get("suite_subtests", {}).get(script, 50)))
	return secs


def grade(case: dict, prediction: str, costs: dict) -> dict:
	"""Per bug, the chance the prediction catches it; and what it costs."""
	trivial, lines = parse(prediction, costs)
	if not trivial:
		# Without trivial the default matrix runs too: lines only add cost.
		out = full(case)
		out["cost_s"] += sum(costs["overhead_s"] + sum(script_cost(s, ln.cls, costs) for s in ln.scripts)
				     for ln in lines)
		out["lines"] = len(lines)
		return out
	picked: dict[str, set[str] | None] = {}       # key -> the subtests run (None: all)
	for ln in lines:
		if ln.cls is None:
			continue
		for s in ln.scripts:
			need = SCRIPT_KEYS.get(s)
			if need and parse_keys(need).items() - ln.keys.items():
				continue
			k = f"{s}|{ln.cls}"
			if ln.only is None or picked.get(k, set()) is None:
				picked[k] = None
			else:
				picked[k] = (picked.get(k) or set()) | ln.only
	bugs = []
	for b in case["bugs"]:
		miss = 1 - b["always"]
		subs = {u[1] for u in b.get("units", [])}
		for k, (r, f) in b["evidence"].items():
			if k in picked and r and (picked[k] is None or picked[k] & subs):
				miss *= 1 - f / r
		bugs.append(round(1 - miss, 4))
	c = case["cost"]
	trivial_s = c["trivial"] or costs["session_s"].get("review-ldiskfs", 0)
	cost = trivial_s + c["subtest_change"]
	for ln in lines:
		cost += costs["overhead_s"] + sum(script_cost(s, ln.cls, costs, ln.only) for s in ln.scripts)
	all_caught = 1.0
	for p in bugs:
		all_caught *= p
	none = 1.0
	for p in bugs:
		none *= 1 - p
	return {"bugs": bugs, "caught": sum(bugs), "n_bugs": len(bugs),
		"all_caught": all_caught if bugs else None, "any_caught": 1 - none if bugs else None,
		"cost_s": cost, "actual_s": c["actual"], "trivial": trivial, "lines": len(lines)}


def full(case: dict) -> dict:
	"""Today's matrix: every (script, class) that ran, at the build's own cost."""
	bugs = []
	for b in case["bugs"]:
		miss = 1 - b["always"]
		for r, f in b["evidence"].values():
			if r:
				miss *= 1 - f / r
		bugs.append(round(1 - miss, 4))
	all_caught = 1.0
	none = 1.0
	for p in bugs:
		all_caught *= p
		none *= 1 - p
	return {"bugs": bugs, "caught": sum(bugs), "n_bugs": len(bugs),
		"all_caught": all_caught if bugs else None, "any_caught": 1 - none if bugs else None,
		"cost_s": case["cost"]["actual"], "actual_s": case["cost"]["actual"], "trivial": False,
		"lines": None}
