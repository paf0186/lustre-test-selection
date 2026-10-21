"""How likely a patch is to have a bug any test would catch, from the patch alone:
its size, Gerrit's kind of patchset, how much of the tree it spans, whether it is
only tests or documentation, and what CI said about the change's earlier
patchsets.  Selection spends more on a riskier patch.

Trained on the random sample of all patchsets (weight 1) and the faulty
patchsets outside it, weighted down by the sampling fraction, so the chances
are the population's, not the dataset's."""
from __future__ import annotations

import math
from collections import defaultdict

KINDS = ("REWORK", "TRIVIAL_REBASE", "NO_CODE_CHANGE", "NO_CHANGE", "TRIVIAL_REBASE_WITH_MESSAGE_UPDATE")


def changed_lines(inp: dict) -> int:
	return sum(1 for ln in (inp.get("diff") or "").splitlines() if ln[:1] in "+-" and ln[:3] not in ("+++", "---"))


NAMES = ["lines changed (log)", "files (log)", "components (log)", "functions changed (log)", "tests only",
	 "docs only", "earlier faulty patchsets (log)", "last patchset had a fault"] + \
	[f"kind {k.lower().replace('_', ' ')}" for k in KINDS] + \
	["riskiest component's past fault rate", "mean component fault rate"]


def components(inp: dict) -> set[str]:
	return {"/".join(f.split("/")[:2]) for f in inp.get("files") or []}


def patch_features(inp: dict, comp_rate=None) -> list[float]:
	files = inp.get("files") or []
	comps = {"/".join(f.split("/")[:2]) for f in files}
	hist = inp.get("history") or {}
	kind = inp.get("gerrit_kind") or ""
	return [math.log1p(changed_lines(inp)), math.log1p(len(files)), math.log1p(len(comps)),
		math.log1p(len(inp.get("functions") or [])),
		float(bool(files) and all(f.startswith("lustre/tests/") for f in files)),
		float(bool(files) and all(f.startswith(("Documentation/", "contrib/")) or f.endswith((".md", ".txt"))
					   for f in files)),
		math.log1p(hist.get("faulty_patchsets", 0)), float(bool(hist.get("last_caught")))] + \
		[float(kind == k) for k in KINDS] + \
		(list(comp_rate(inp)) if comp_rate else [0.0, 0.0])


class Risk:
	def __init__(self, fraction: float = 0.12):
		self.fraction = fraction

	def _comp_rates(self, cases):
		"""Each component's fault rate among the population's patches touching it,
		smoothed toward the base rate; a change's own patches left out of its rows."""
		tot = defaultdict(lambda: [0.0, 0.0])
		own = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
		for c in cases:
			w = 1.0 if c["sampled"] else self.fraction
			for comp in components(c["input"]):
				tot[comp][0] += w
				tot[comp][1] += w * c["faulty"]
				own[c["change"]][comp][0] += w
				own[c["change"]][comp][1] += w * c["faulty"]
		self.tot, self.own = dict(tot), {k: dict(v) for k, v in own.items()}

	def comp_rate(self, inp, change=None):
		rates = []
		for comp in components(inp):
			n, k = self.tot.get(comp, (0.0, 0.0))
			if change is not None and comp in self.own.get(change, {}):
				dn, dk = self.own[change][comp]
				n, k = n - dn, k - dk
			rates.append((k + 5 * self.base) / (n + 5))
		return (max(rates), sum(rates) / len(rates)) if rates else (self.base, self.base)

	def fit(self, cases: list[dict]):
		from sklearn.linear_model import LogisticRegression
		from sklearn.pipeline import make_pipeline
		from sklearn.preprocessing import StandardScaler
		use = [c for c in cases if c["sampled"] or c["faulty"]]
		w = [1.0 if c["sampled"] else self.fraction for c in use]
		self.base = sum(wi * c["faulty"] for wi, c in zip(w, use)) / sum(w)
		self._comp_rates(use)
		X = [patch_features(c["input"], lambda inp, ch=c["change"]: self.comp_rate(inp, ch)) for c in use]
		y = [int(c["faulty"]) for c in use]
		self.clf = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=2000))
		self.clf.fit(X, y, logisticregression__sample_weight=w)
		self.mean = sum(self.clf.predict_proba(X)[:, 1] * w) / sum(w)
		return self

	def __call__(self, inp: dict) -> float:
		return float(self.clf.predict_proba([patch_features(inp, self.comp_rate)])[0, 1])

	def explain(self, inp: dict, top: int = 4) -> list[tuple[str, float]]:
		"""The features that raise this patch's risk most, as (name, contribution
		to the log-odds over an average patch)."""
		sc, lr = self.clf[0], self.clf[-1]
		x = sc.transform([patch_features(inp, self.comp_rate)])[0]
		parts = sorted(zip(NAMES, x * lr.coef_[0]), key=lambda p: -p[1])
		return [(n, round(float(v), 2)) for n, v in parts[:top] if v > 0]
