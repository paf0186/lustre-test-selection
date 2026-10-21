"""How likely a patch is to have a bug any test would catch, from the patch alone:
its size, Gerrit's kind of patchset, how much of the tree it spans, whether it is
only tests or documentation, and what CI said about the change's earlier
patchsets.  Selection spends more on a riskier patch.

Trained on the random sample of all patchsets (weight 1) and the faulty
patchsets outside it, weighted down by the sampling fraction, so the chances
are the population's, not the dataset's."""
from __future__ import annotations

import math

KINDS = ("REWORK", "TRIVIAL_REBASE", "NO_CODE_CHANGE", "NO_CHANGE", "TRIVIAL_REBASE_WITH_MESSAGE_UPDATE")


def changed_lines(inp: dict) -> int:
	return sum(1 for ln in (inp.get("diff") or "").splitlines() if ln[:1] in "+-" and ln[:3] not in ("+++", "---"))


def patch_features(inp: dict) -> list[float]:
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
		[float(kind == k) for k in KINDS]


class Risk:
	def __init__(self, fraction: float = 0.12):
		self.fraction = fraction

	def fit(self, cases: list[dict]):
		from sklearn.linear_model import LogisticRegression
		from sklearn.pipeline import make_pipeline
		from sklearn.preprocessing import StandardScaler
		X, y, w = [], [], []
		for c in cases:
			if not (c["sampled"] or c["faulty"]):
				continue
			X.append(patch_features(c["input"]))
			y.append(int(c["faulty"]))
			w.append(1.0 if c["sampled"] else self.fraction)
		self.clf = make_pipeline(StandardScaler(), LogisticRegression(C=0.5, max_iter=2000))
		self.clf.fit(X, y, logisticregression__sample_weight=w)
		self.mean = sum(self.clf.predict_proba(X)[:, 1] * w) / sum(w)
		return self

	def __call__(self, inp: dict) -> float:
		return float(self.clf.predict_proba([patch_features(inp)])[0, 1])
