"""Fit any target from any signals; score and explain one patch; evaluate.

    s = Scorer("faulty", ["lines_changed", "areas", "component_fault_rate"]).fit(train_cases)
    s.score(patch)    -> {"score": 0.18, "percentile": 0.91, "reasons": [...], "signals": {...}}

Logistic regression on standardized columns: calibrated to the population (cases
outside the random sample are weighted by the sampling fraction), and each
score's reasons are exact (a column's contribution to the log-odds), joined
with the signals' own reasons."""
from __future__ import annotations

import numpy as np

from . import columns, compute, registry
from .targets import TARGETS


class Scorer:
	def __init__(self, target: str, signals: list[str] | None = None, fraction: float = 0.12, C: float = 0.5):
		self.target, self.fraction, self.C = target, fraction, C
		reg = registry()
		self.signals = signals or [n for n, s in reg.items() if s.cost != "gpu"]
		self.label = TARGETS[target][0]

	def _values(self, patch, change=None):
		return compute(patch, self.signals, self.fitted, change)

	def fit(self, cases: list[dict]) -> "Scorer":
		from sklearn.linear_model import LogisticRegression
		from sklearn.preprocessing import StandardScaler
		reg = registry()
		use = [c for c in cases if (c["sampled"] or c["faulty"]) and self.label(c) is not None]
		self.fitted = {n: reg[n].learned().fit(use) for n in self.signals if reg[n].learned}
		rows = [columns(self._values(c["input"], c["change"])) for c in use]
		self.cols = sorted({k for r in rows for k in r})
		X = np.array([[r.get(k, 0.0) for k in self.cols] for r in rows])
		y = np.array([self.label(c) for c in use])
		w = np.array([1.0 if c["sampled"] else self.fraction for c in use])
		self.scaler = StandardScaler().fit(X)
		self.clf = LogisticRegression(C=self.C, max_iter=3000).fit(self.scaler.transform(X), y, sample_weight=w)
		pop = self.clf.predict_proba(self.scaler.transform(X[w == 1.0]))[:, 1]
		self.population = np.sort(pop)
		self.base = float((y * w).sum() / w.sum())
		return self

	def _x(self, patch, change=None):
		vals = self._values(patch, change)
		row = columns(vals)
		raw = [row.get(k, 0.0) for k in self.cols]
		self._raw = dict(zip(self.cols, raw))
		return vals, self.scaler.transform([raw])[0]

	def predict(self, patch: dict) -> float:
		return float(self.clf.predict_proba([self._x(patch)[1]])[0, 1])

	def score(self, patch: dict, top: int = 5) -> dict:
		vals, x = self._x(patch)
		p = float(self.clf.predict_proba([x])[0, 1])
		contrib = sorted(zip(self.cols, x * self.clf.coef_[0]), key=lambda kv: -kv[1])
		reasons = []
		for col, c in contrib:
			if c <= 0.05 or len(reasons) >= top:
				break
			if not self._raw.get(col):
				# a feature the patch lacks lowers the odds of others; it is no reason
				continue
			sig = col.split(".")[0]
			why = vals[sig].reason if sig in vals else None
			reasons.append({"column": col, "weight": round(float(c), 2), "why": why})
		return {"target": self.target, "score": p,
			"percentile": float(np.searchsorted(self.population, p) / max(1, len(self.population))),
			"base_rate": self.base, "reasons": reasons,
			"signals": {n: {"columns": v.columns, "reason": v.reason} for n, v in vals.items()}}


def evaluate(cases: list[dict], target: str, signals: list[str], folds, boot: int = 1000, seed: int = 7) -> dict:
	"""Fit on each fold's training cases, score its evaluation cases (population
	patches only), and report AUC and the riskiest and safest fifths' lift over
	the base rate, bootstrapped over changes."""
	from collections import defaultdict

	from sklearn.metrics import roc_auc_score
	label = TARGETS[target][0]
	rows = []
	for train, evals in folds:
		s = Scorer(target, signals).fit(train)
		rows += [(c["change"], s.predict(c["input"]), label(c)) for c in evals
			 if c["sampled"] and label(c) is not None]

	def stats(sample):
		ys = [y for _, _, y in sample]
		ps = [p for _, p, _ in sample]
		if not 0 < sum(ys) < len(ys):
			return None
		order = sorted(range(len(ps)), key=lambda i: ps[i])
		k = max(1, len(order) // 5)
		base = sum(ys) / len(ys)
		return (roc_auc_score(ys, ps), sum(ys[i] for i in order[-k:]) / k / base,
			sum(ys[i] for i in order[:k]) / k / base)
	by = defaultdict(list)
	for r in rows:
		by[r[0]].append(r)
	changes = sorted(by)
	rng = np.random.default_rng(seed)
	boots = [b for b in (stats([r for ch in rng.choice(changes, len(changes)) for r in by[ch]])
			     for _ in range(boot)) if b]
	point = stats(rows)
	ci = lambda i: [float(np.percentile([b[i] for b in boots], 2.5)), float(np.percentile([b[i] for b in boots], 97.5))]  # noqa: E731
	return {"target": target, "signals": signals, "patches": len(rows), "positives": sum(y for _, _, y in rows),
		"auc": point[0] if point else None, "auc_ci": ci(0) if boots else None,
		"riskiest_fifth": point[1] if point else None, "riskiest_ci": ci(1) if boots else None,
		"safest_fifth": point[2] if point else None, "safest_ci": ci(2) if boots else None,
		"_rows": rows}


def paired_auc_diff(a: dict, b: dict, boot: int = 1000, seed: int = 7):
	"""AUC(a) - AUC(b) on the same patches, bootstrapped over changes."""
	from collections import defaultdict

	from sklearn.metrics import roc_auc_score
	pa = {(ch, i): (p, y) for i, (ch, p, y) in enumerate(a["_rows"])}
	pb = {(ch, i): (p, y) for i, (ch, p, y) in enumerate(b["_rows"])}
	keys = sorted(set(pa) & set(pb))
	by = defaultdict(list)
	for k in keys:
		by[k[0]].append(k)
	changes = sorted(by)
	rng = np.random.default_rng(seed)

	def auc(src, ks):
		ys = [src[k][1] for k in ks]
		return roc_auc_score(ys, [src[k][0] for k in ks]) if 0 < sum(ys) < len(ys) else None
	d = []
	for _ in range(boot):
		ks = [k for ch in rng.choice(changes, len(changes)) for k in by[ch]]
		x, y = auc(pa, ks), auc(pb, ks)
		if x is not None and y is not None:
			d.append(x - y)
	full = (auc(pa, keys) or 0) - (auc(pb, keys) or 0)
	return full, [float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))]
