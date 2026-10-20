"""Group a patchset's faults into bugs: every bug must be caught, so the many
failures one bug causes are merged first (docs: test_selection_design.md 2.4).

1. One unit per script, subtest and error signature.
2. Units of different scripts merge when they share a signature that names what
   went wrong.  A generic one (a crash, a timeout, "returned 1") does not merge.
3. A cascade, faults over most of the scripts the build ran, is one bug.
4. Everything else stays apart.
"""
from __future__ import annotations

import re
from collections import defaultdict

CASCADE_SHARE = 0.5     # of the scripts the patchset's sessions ran
CASCADE_MIN = 4         # scripts

GENERIC = [re.compile(p) for p in (
	r"^$",
	r"^test_\w+ (returned|failed with) N$",
	r"crashed during",
	r"^Timeout occurred",
	r"^Autotest time out",
	r"^Fail to cleanup the env!$",
	r"^failed to remove /mnt/lustre",       # a leftover after another failure
	r"^remove sub-test dirs failed",
)]


def generic(sig: str | None, status: str | None) -> bool:
	if status in ("CRASH", "TIMEOUT"):
		return True
	return any(p.search(sig or "") for p in GENERIC)


def group(faults: list[dict], scripts_run: set[str]) -> list[dict]:
	"""Bugs of one patchset.  `faults` are its failure rows (suite, subtest, sig,
	status, ...); `scripts_run` the suites its sessions ran.  Each bug has its
	units ((suite, subtest, sig) keys), its scripts and why it was merged."""
	units: dict[tuple, list[dict]] = defaultdict(list)
	for f in faults:
		units[(f["suite"], f["subtest"], f.get("sig") or "")].append(f)
	scripts = {k[0] for k in units}
	ignore = {"node-provisioning", "lustre-initialization"}
	ran = (scripts_run - ignore) or scripts
	if len(scripts) >= CASCADE_MIN and len(scripts) >= CASCADE_SHARE * len(ran):
		return [{"units": sorted(units), "scripts": sorted(scripts), "why": "cascade"}]

	parent = {k: k for k in units}

	def find(k):
		while parent[k] != k:
			parent[k] = parent[parent[k]]
			k = parent[k]
		return k

	by_sig: dict[str, list[tuple]] = defaultdict(list)
	for k, fs in units.items():
		if not generic(k[2], fs[0].get("status")):
			by_sig[k[2]].append(k)
	for ks in by_sig.values():
		for k in ks[1:]:
			parent[find(k)] = find(ks[0])
	out: dict[tuple, list[tuple]] = defaultdict(list)
	for k in units:
		out[find(k)].append(k)
	return [{"units": sorted(ks), "scripts": sorted({k[0] for k in ks}),
		 "why": "signature" if len({k[0] for k in ks}) > 1 else "unit"}
		for ks in sorted(out.values())]
