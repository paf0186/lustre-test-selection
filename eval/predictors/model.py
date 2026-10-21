"""A learned predictor in the manner of Facebook's and Mozilla's predictive test
selection: one model scores every (patch, test set) pair with the chance the test
set catches a bug of the patch, and each patch gets the test sets worth their
session time.

Features mix principle and history, so the model learns how far to trust each:
the principles' own choice; co-failure history of the patch's components and
directories with the test set (computed leaving the patch's own change out, so
no row sees its own label); path words shared with the script name; the test
set's base rate; the patch's size and shape; and the configuration against the
code it touches.  Training rows are test sets that ran in a build of the
training set, caught or not, clean patches included."""
from __future__ import annotations

import math
import os
import re
from collections import Counter, defaultdict

from .baselines import edited_scripts, lines_for
from .principles import Principles

M = 5.0                 # smoothing toward the base rate, in patches
WORD = re.compile(r"[a-z0-9]+")
STOP = {"sanity", "test", "tests", "single", "small", "lustre", "c", "h", "sh", "the"}
AREAS = {
	"zfs": ("osd-zfs", "zfs"), "ldiskfs": ("osd-ldiskfs", "ldiskfs/", "kernel_patches"),
	"compat": ("autoconf", "compat", "lustre.spec", "debian/"), "sec": ("gss", "/sec", "nodemap", "sptlrpc"),
	"lnet": ("lnet/",), "tests": ("lustre/tests/",), "utils": ("lustre/utils/",),
	"dne": ("lmv/", "lod/", "mdt/", "mdd/", "osp/"), "client": ("llite/", "osc/", "mdc/", "lov/"),
	"recovery": ("ptlrpc/", "target/", "ldlm/"),
}


# The suites (or configurations) that exercise each mechanism the LLM rates.
MECH_SCRIPTS = {
	"posix-namespace": {"sanity", "sanityn", "pjdfstest"}, "striped-dirs-dne": {"sanity", "sanityn"},
	"data-io": {"sanity", "sanityn"}, "direct-io": {"sanity", "sanity-flr", "sanity-pfl"},
	"mmap": {"sanity", "sanityn"}, "client-locking": {"sanityn", "recovery-small"},
	"layouts": {"sanity", "sanity-pfl", "sanity-flr"}, "mirrors-flr": {"sanity-flr"},
	"erasure-coding": {"sanity-ec"}, "dom": {"sanity-dom"}, "pools": {"ost-pools"},
	"config-mount": {"conf-sanity"},
	"recovery-replay": {"recovery-small", "replay-single", "replay-dual", "replay-ost-single", "replay-vbr", "insanity"},
	"object-storage": {"sanity", "replay-ost-single"}, "quota": {"sanity-quota"},
	"lfsck-scrub": {"sanity-lfsck", "sanity-scrub"}, "hsm-pcc": {"sanity-hsm", "sanity-pcc"},
	"changelog": {"sanity", "lustre-rsync-test"}, "security-nodemap": {"sanity-sec", "sanity-krb5", "sanity-selinux"},
	"encryption": {"sanity-sec"}, "compression": {"sanity-compr"}, "lnet-network": {"sanity-lnet", "lnet-selftest"},
	"userspace-tools": {"sanity"},
}
MECH_CLASSES = {"ldiskfs-backend": ("ldiskfs",), "zfs-backend": ("zfs",), "kernel-compat": ("arm", "ubuntu", "sles"),
		"security-nodemap": ("selinux",)}
MECH_ALL = ("test-framework", "memory-lifetime")


def llm_answer(key: str | None) -> dict | None:
	import json
	from pathlib import Path
	if not key:
		return None
	f = Path(__file__).resolve().parents[2] / "data" / "llm" / (key.replace("/", "-") + ".json")
	try:
		d = json.loads(f.read_text())
	except (OSError, ValueError):
		return None
	return d if "mechanisms" in d else None


def component(path: str) -> str:
	parts = path.split("/")
	return "/".join(parts[:2]) if len(parts) > 2 else parts[0]


def words(text: str) -> set[str]:
	return {w for w in WORD.findall(text.lower()) if len(w) >= 3 and w not in STOP}


def candidates(costs: dict, classes: dict) -> dict[str, dict]:
	from .ranked import candidates as ranked_candidates
	return ranked_candidates(costs, classes)


def label(case: dict, cand: dict) -> int:
	keys = set(cand["keys"])
	return int(any(f and k in keys for b in case["bugs"] for k, (r, f) in b["evidence"].items()))


class Model:
	def __init__(self, costs: dict, classes: dict, kind: str = "gbdt", llm: bool = False):
		self.costs, self.classes, self.kind, self.use_llm = costs, classes, kind, llm
		self.cands = candidates(costs, classes)
		self.principles = Principles()
		self.name = f"model-{kind}{'-llm' if llm else ''}"
		self.title = ("learned model" if kind == "gbdt" else "learned model (linear)") + \
			(" with LLM reading" if llm else "")
		self.about = ("one model scores every patch and test set pair, from the principles, co-failure "
			      "history, shared path words, base rates and the patch's shape; each patch gets the test "
			      "sets worth their session time")

	# -- history ------------------------------------------------------------------

	def _units(self, inp: dict) -> dict[str, set[str]]:
		files = inp.get("files") or []
		return {"comp": {component(f) for f in files}, "dir": {os.path.dirname(f) for f in files}}

	def fit(self, cases: list[dict]):
		self.base = Counter()               # cand -> [ran, caught]
		self.base_n = Counter()
		self.hist = defaultdict(Counter)    # (level, unit, cand) -> ran / caught
		self.by_change = defaultdict(lambda: defaultdict(Counter))
		self.base_change = defaultdict(Counter)     # (change, cand) -> ran / caught
		rows = []
		for c in cases:
			ran = set(c.get("ran") or [])
			units = self._units(c["input"])
			for name, cand in self.cands.items():
				if not (set(cand["keys"]) & ran or name in ran):
					continue
				y = label(c, cand)
				self.base_n[name] += 1
				self.base[name] += y
				self.base_change[(c["change"], name)]["ran"] += 1
				self.base_change[(c["change"], name)]["caught"] += y
				for lvl, us in units.items():
					for u in us:
						h = self.hist[(lvl, u, name)]
						h["ran"] += 1
						h["caught"] += y
						ch = self.by_change[c["change"]][(lvl, u, name)]
						ch["ran"] += 1
						ch["caught"] += y
				rows.append((c, name, y))
		# How often A catches what B catches, where both ran: a candidate is worth
		# only what the sets already chosen would not catch anyway.
		caught_by: dict[int, set[str]] = defaultdict(set)
		ran_by: dict[int, set[str]] = defaultdict(set)
		for i, (c, name, y) in enumerate(rows):
			ran_by[id(c)].add(name)
			if y:
				caught_by[id(c)].add(name)
		both = Counter()
		alone = Counter()
		for key, got in caught_by.items():
			ran = ran_by[key]
			for b in got:
				for a in ran:
					if a != b:
						alone[(a, b)] += 1
						both[(a, b)] += a in got
		self.overlap = {k: both[k] / n for k, n in alone.items() if n >= 3}
		X = [self.features(c["input"], name, c["change"]) for c, name, _ in rows]
		y = [r[2] for r in rows]
		if self.kind == "gbdt":
			from sklearn.ensemble import HistGradientBoostingClassifier
			self.clf = HistGradientBoostingClassifier(max_depth=4, learning_rate=0.05, max_iter=250,
								  l2_regularization=1.0, min_samples_leaf=40,
								  random_state=1)
		else:
			from sklearn.linear_model import LogisticRegression
			from sklearn.pipeline import make_pipeline
			from sklearn.preprocessing import StandardScaler
			self.clf = make_pipeline(StandardScaler(), LogisticRegression(C=0.3, max_iter=2000))
		# Each change counts once, as in the score: a change with many patchsets
		# and many bugs would otherwise teach the model about itself.
		per_change = Counter(c["change"] for c, _, _ in rows)
		w = [1.0 / per_change[c["change"]] * len(rows) / len(per_change) for c, _, _ in rows]
		if self.kind == "gbdt":
			self.clf.fit(X, y, sample_weight=w)
		else:
			self.clf.fit(X, y, logisticregression__sample_weight=w)
		self.n_rows, self.n_pos = len(y), sum(y)
		return self

	def _rate(self, key: tuple, name: str, change) -> tuple[float, int]:
		h = self.hist.get(key)
		ran, caught = (h["ran"], h["caught"]) if h else (0, 0)
		if change is not None and change in self.by_change:
			own = self.by_change[change].get(key)
			if own:
				ran, caught = ran - own["ran"], caught - own["caught"]
		prior = self._prior(name, change)
		return (caught + M * prior) / (ran + M), ran

	def _prior(self, name: str, change=None) -> float:
		n, k = self.base_n[name], self.base[name]
		own = self.base_change.get((change, name))
		if own:
			n, k = n - own["ran"], k - own["caught"]
		return (k + 1) / (n + 50)

	# -- features -----------------------------------------------------------------

	def features(self, inp: dict, name: str, change=None) -> list[float]:
		cand = self.cands[name]
		script = cand.get("script") or ""
		cls = cand["cls"]
		files = inp.get("files") or []
		units = self._units(inp)
		picks, groups, _ = self._principles(inp)
		if cand.get("group"):
			p_exact = float(cand["group"] in groups)
			p_any = p_exact
		else:
			p_exact = float(script in picks.get(cls, ()))
			p_any = float(any(script in v for v in picks.values()))
		rates = {lvl: [self._rate((lvl, u, name), name, change) for u in us] for lvl, us in units.items()}
		fw = set().union(*(words(f) for f in files)) if files else set()
		sw = words(script or cand.get("group", ""))
		overlap = float(any(a.startswith(b) or b.startswith(a) for a in fw for b in sw))
		msg = (inp.get("message") or "") + " " + (inp.get("test_parameters") or "")
		mentioned = float(bool(script) and re.search(rf"\b{re.escape(script)}\b", msg) is not None)
		diff = inp.get("diff") or ""
		changed = sum(1 for ln in diff.splitlines() if ln[:1] in "+-" and ln[:3] not in ("+++", "---"))
		area = {a: float(any(any(m in f for m in marks) for f in files)) for a, marks in AREAS.items()}
		out = [
			p_exact, p_any,
			float(script in edited_scripts(inp)),
			overlap, mentioned,
			math.log(self._prior(name, change) + 1e-6),
			max((r for r, _ in rates["comp"]), default=0), sum(r for r, _ in rates["comp"]) / max(1, len(rates["comp"])),
			max((r for r, _ in rates["dir"]), default=0),
			math.log1p(max((n for _, n in rates["dir"]), default=0)),
			math.log1p(len(files)), math.log1p(changed), float(len(units["comp"])),
			float(self.principles.narrow(inp)),
			math.log1p(self.costs["suite_class_s"].get(f"{script}|{cls}", 0) if script else 0),
		]
		hist = inp.get("history") or {}
		keys = set(cand["keys"])
		out += [float(bool(keys & set(hist.get("caught", ())))), float(bool(keys & set(hist.get("last_caught", ())))),
			float(bool(script) and any(k.split("|")[0] == script for k in hist.get("caught", ()))),
			math.log1p(hist.get("faulty_patchsets", 0))]
		ans = llm_answer(inp.get("key")) if self.use_llm else None
		if ans:
			mech = ans["mechanisms"]
			name_s = script or ""
			grp = cand.get("group") or ""
			by_script = max((v for m, v in mech.items() if name_s in MECH_SCRIPTS.get(m, ())
					 or (grp and any(x in grp for x in ("selinux", "ssk")) and m == "security-nodemap")),
					default=0)
			by_class = max((v for m, v in mech.items() if any(t in cls for t in MECH_CLASSES.get(m, ()))),
				       default=0)
			broad = max((mech.get(m, 0) for m in MECH_ALL), default=0)
			out += [1.0, float(by_script), float(by_class), float(broad), float(ans.get("overall", 0)),
				float(sum(1 for v in mech.values() if v >= 2))]
		else:
			out += [0.0, -1.0, -1.0, -1.0, -1.0, -1.0]
		out += list(area.values())
		out += [float(cls == c) for c in sorted(self.classes)]
		return out

	def _principles(self, inp: dict):
		key = id(inp)
		if getattr(self, "_pkey", None) != key:
			self._pkey, self._pval = key, self.principles.select(inp)
		return self._pval

	# -- selection ----------------------------------------------------------------

	def scores(self, inp: dict) -> dict[str, float]:
		names = list(self.cands)
		X = [self.features(inp, n) for n in names]
		return dict(zip(names, self.clf.predict_proba(X)[:, 1]))

	def select(self, inp: dict, rate: float, scores: dict[str, float] | None = None,
		   redundancy: str = "max") -> str:
		"""Test sets added by predicted catches per session hour while worth `rate`;
		a candidate counts only for what the chosen sets would not also catch."""
		scores = scores if scores is not None else self.scores(inp)
		picks: dict[str, set[str]] = defaultdict(set)
		groups: set[str] = set()
		running: set[str] = set()
		left = dict(scores)
		chosen: list[str] = []
		while left:
			best, best_v = None, -1.0
			for n, p in left.items():
				if chosen and redundancy != "none":
					ov = [self.overlap.get((a, n), 0.0) for a in chosen]
					if redundancy == "max":
						p = p * (1 - max(ov))
					else:
						for o in ov:
							p *= 1 - o
				cand = self.cands[n]
				if cand.get("group"):
					secs = cand["secs"] or self.costs["overhead_s"]
				else:
					secs = cand["secs"] + (0 if cand["cls"] in running else self.costs["overhead_s"])
				v = p / (secs / 3600)
				if v > best_v:
					best, best_v = n, v
			if best_v < rate:
				break
			cand = self.cands[best]
			del left[best]
			chosen.append(best)
			if cand.get("group"):
				groups.add(cand["group"])
			else:
				picks[cand["cls"]].add(cand["script"])
				running.add(cand["cls"])
		for s in edited_scripts(inp):
			picks["ldiskfs-dne"].add(s)
		return "\n".join(["trivial"] + lines_for(picks, self.classes, groups))
