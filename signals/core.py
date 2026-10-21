"""Built-in signals: the patch's size and shape, what CI said about the change
before, and learned component fault rates."""
from __future__ import annotations

import math
from collections import defaultdict

from . import Learned, learned, signal

KINDS = ("REWORK", "TRIVIAL_REBASE", "NO_CODE_CHANGE", "NO_CHANGE", "TRIVIAL_REBASE_WITH_MESSAGE_UPDATE")


def changed_lines(patch: dict) -> int:
	return sum(1 for ln in (patch.get("diff") or "").splitlines() if ln[:1] in "+-" and ln[:3] not in ("+++", "---"))


def components(patch: dict) -> set[str]:
	return {"/".join(f.split("/")[:2]) for f in patch.get("files") or []}


@signal("lines_changed", doc="Lines added or removed by the diff (log scale).")
def lines_changed(p):
	n = changed_lines(p)
	return math.log1p(n), f"{n} lines changed"


@signal("files_changed", doc="Files the patch changes (log scale).")
def files_changed(p):
	n = len(p.get("files") or [])
	return math.log1p(n), f"{n} files"


@signal("components", doc="Top-level components it spans, e.g. lustre/llite (log scale).")
def n_components(p):
	c = components(p)
	return math.log1p(len(c)), f"{len(c)} components: {', '.join(sorted(c)[:4])}"


@signal("functions_changed", needs=("tree",), doc="C functions whose bodies the patch changes (log scale).")
def functions_changed(p):
	f = p.get("functions") or []
	return math.log1p(len(f)), f"{len(f)} functions changed"


@signal("tests_only", doc="Changes only test scripts.")
def tests_only(p):
	f = p.get("files") or []
	v = bool(f) and all(x.startswith("lustre/tests/") for x in f)
	return float(v), "test scripts only" if v else None


@signal("docs_only", doc="Changes only documentation or contrib.")
def docs_only(p):
	f = p.get("files") or []
	v = bool(f) and all(x.startswith(("Documentation/", "contrib/")) or x.endswith((".md", ".txt")) for x in f)
	return float(v), "documentation only" if v else None


@signal("gerrit_kind", doc="Gerrit's kind of patchset: rework, trivial rebase, message only, no change.")
def gerrit_kind(p):
	k = p.get("gerrit_kind") or ""
	return {x.lower(): float(k == x) for x in KINDS}, f"Gerrit kind {k.lower() or 'unknown'}"


@signal("change_history", needs=("rainshadow",),
	doc="What CI caught on this change's earlier patchsets: how many were faulty, and the last.")
def change_history(p):
	h = p.get("history") or {}
	n = h.get("faulty_patchsets", 0)
	last = bool(h.get("last_caught"))
	return ({"faulty_patchsets": math.log1p(n), "last_faulty": float(last)},
		f"{n} earlier patchset(s) had faults" + (", the last one too" if last else "") if n else None)


@learned
class ComponentFaultRate(Learned):
	"""Each component's fault rate among the population's patches that touched it,
	smoothed toward the base rate: the patch's riskiest and mean component."""
	name = "component_fault_rate"
	doc = "Past fault rate of the components the patch touches (riskiest, and mean)."

	def __init__(self, fraction: float = 0.12, smooth: float = 5.0):
		self.fraction, self.smooth = fraction, smooth

	def fit(self, cases):
		use = [c for c in cases if c["sampled"] or c["faulty"]]
		w = [1.0 if c["sampled"] else self.fraction for c in use]
		self.base = sum(wi * c["faulty"] for wi, c in zip(w, use)) / max(1e-9, sum(w))
		self.tot = defaultdict(lambda: [0.0, 0.0])
		self.own = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
		for c, wi in zip(use, w):
			for comp in components(c["input"]):
				self.tot[comp][0] += wi
				self.tot[comp][1] += wi * c["faulty"]
				self.own[c["change"]][comp][0] += wi
				self.own[c["change"]][comp][1] += wi * c["faulty"]
		return self

	def value(self, p, change=None):
		rates = []
		for comp in components(p):
			n, k = self.tot.get(comp, (0.0, 0.0))
			if change is not None and comp in self.own.get(change, {}):
				dn, dk = self.own[change][comp]
				n, k = n - dn, k - dk
			rates.append(((k + self.smooth * self.base) / (n + self.smooth), comp))
		if not rates:
			return {"max": self.base, "mean": self.base}
		top = max(rates)
		return ({"max": top[0], "mean": sum(r for r, _ in rates) / len(rates)},
			f"{top[1]} has a past fault rate of {top[0]:.0%} (base {self.base:.0%})")


@signal("noise", doc="Pure noise from the patch's key: the negative control; never in a default set.")
def noise(p):
	import hashlib
	h = hashlib.sha256((p.get("key") or "").encode()).digest()
	return int.from_bytes(h[:8], "big") / 2 ** 64
