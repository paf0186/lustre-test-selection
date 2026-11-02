#!/usr/bin/env python3
"""What a subtest's metadata header may hold.

Follows lustre/tests/README-ltsh.md in change 69743, plus the fields asked for
on that change and not yet in it (PROPOSED).  A header is plain YAML here, read
with a real parser, with commit entries quoted.
"""
from __future__ import annotations

# filled by a model, or checked against what extract.py found
FIELDS = {"summary", "commit", "description", "keywords", "requires", "start", "end",
	  "modifies", "observes", "failover", "parallel", "parallel_fix", "slow", "jira", "duration",
	  "depends", "failovers", "isolation", "min_vms", "min_vms_soft", "unparsed"}
REQUIRED = {"summary", "keywords", "modifies", "observes"}
REQUIRES = {"mdscount", "ostcount", "clientcount", "mds1_version", "ost1_version",
	    "mgs_version", "client_version", "fstype", "mds1_fstype", "ost1_fstype",
	    "mgs", "tools", "free_space", "free_inodes", "mdt_free_space", "mdt_free_inodes",
	    "ost_free_space", "ost_free_inodes", "reason",
	    "mdsnodes", "ossnodes", "remote", "failure_mode", "feature", "config", "nodecount"}
PROPOSED = {"mdsnodes", "ossnodes", "remote", "nodecount", "failure_mode", "feature", "config", "observes",
	    "failover", "parallel_fix", "depends", "isolation", "min_vms"}
# all a reader may put in requires; the rest comes from the skip checks
READER_REQUIRES = {"mdsnodes", "ossnodes", "clientcount", "reason"}
STATES = {"mounted", "unmounted", "stopped", "formatted"}
FSTYPES = {"ldiskfs", "zfs"}
# what a subtest fails; any is a server the code names only through a variable
FAILOVER = {"mds", "ost", "mgs", "any", "client"}
FAILURE_MODES = {"soft", "hard"}
# fewest VMs a suite gets: on one VM all traffic is 0@lo and a server fail_loc hits the client
VM_FLOOR = 2

# global state a subtest changes; an empty list says it changes none.  fail_loc
# and tunables are both settings, listed apart because a fail_loc also changes
# what every other subtest's RPCs do
MODIFIES = {"fail_loc", "mount", "format", "config", "tunables", "pools", "layout",
	    "quota", "changelog", "nodemap", "debug", "recovery", "lnet", "hsm", "fixed_path",
	    "client_fail_loc", "client_tunables", "client_cache", "space", "host"}
# shared state it reads, so that a neighbour's activity can change its result
OBSERVES = {"stats", "grant", "free_space", "free_inodes", "debug_log", "changelog",
	    "object_count", "lock_count", "cache", "console", "quota", "namespace", "timing",
	    "quiesced"}
# found in the code, never left to a reader
MECHANICAL = {"fixed_path"}
# state of one client: a subtest with only these needs its own client, not its own servers
CLIENT_SCOPED = {"client_fail_loc", "client_tunables", "client_cache", "cache", "lock_count"}
# all a parallel_fix may remove: scope a subtest can narrow to its own files
FIXABLE = {"fixed_path", "namespace", "layout", "pools"}

# fields two readers must agree on; other lists are merged, descriptions are not compared
AGREE = ("requires", "start", "end")


def parallel_safe(header):
	"""Whether a subtest may run beside others: True only if it declares that
	it changes no global state and measures none.  A missing list is unknown,
	so None.  parallel: only controls the framework's unused PARALLEL mode and
	has no say here."""
	changes, reads = header.get("modifies"), header.get("observes")
	if changes or reads:
		return False
	return None if changes is None or reads is None else True


def parallel_fixable(header):
	"""Whether a parallel_fix can apply: it is not safe now, and all that stops
	it is scope."""
	if parallel_safe(header) is not False:
		return False
	return set(header.get("modifies") or []) | set(header.get("observes") or []) <= FIXABLE


def isolation(header):
	"""What a subtest needs to itself: nothing (share), a client (client), or a
	whole cluster (cluster).  None if unknown."""
	safe = parallel_safe(header)
	if safe is None:
		return None
	if safe:
		return "share"
	blockers = set(header.get("modifies") or []) | set(header.get("observes") or [])
	return "client" if blockers <= CLIENT_SCOPED else "cluster"


def min_vms(header, mode="hard"):
	"""The fewest VMs a subtest can run on, before any floor a scheduler sets.
	None if it does not work in this mode.  In SOFT mode a failover is a stop
	and start, so a subtest with failover: can share its hosts."""
	req = header.get("requires") or {}
	if req.get("failure_mode", mode) != mode:
		return None
	clients = int(req.get("clientcount") or 1)
	floor = int(req.get("nodecount") or 1)
	if mode == "soft" and header.get("failover"):
		return max(floor, clients + ("any" in (req.get("remote") or [])))
	servers = max(int(req.get("mdsnodes") or 1), int(req.get("ossnodes") or 1))
	return max(floor, clients + servers if req.get("remote") else max(clients, servers))


def suite_vms(headers, floor=VM_FLOOR):
	"""The VMs a whole suite runs on: what its largest subtest needs in HARD
	mode, or in SOFT if it cannot run in HARD, and at least floor."""
	return max([floor] + [min_vms(x) or min_vms(x, "soft") or 1 for x in headers])
