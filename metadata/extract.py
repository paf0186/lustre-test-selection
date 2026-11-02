#!/usr/bin/env python3
"""Everything about a subtest that needs no model: its code, helpers, history,
run_test summary, CI duration, and the facts its skip checks and first and last
statements give.  The result is what generate.py hands a model and what
validate.py checks the answer against.

  metadata/extract.py --lustre ~/lustre-release --rev master -o data/metadata/extract.jsonl
  metadata/extract.py --lustre ~/lustre-release --suite sanity --ids 27I,230i -o -
  metadata/extract.py --lustre ~/lustre-release --stats

start and end describe a subtest as if moved to a .ltsh file: a cleanup or
reformat in its first or last statement belongs to the header, so 151a, which
begins with cleanup and ends with reformat_and_config, is start stopped and end
formatted.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

# every skip message, normalised, classified once (skip_key); see README
SKIPS = json.loads((Path(__file__).resolve().parent / "skips.json").read_text(encoding="utf-8"))

RUN_TEST = re.compile(r'''^run_test(?:_must_fail|_should_fail)?\s+(\S+)\s+(?:"((?:[^"\\]|\\.)*)"|'([^']*)'|(.*))''')
TEST_FUNC = re.compile(r'^test_([0-9]+[A-Za-z]*)\s*\(\)')
FUNC = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)\s*\(\)')
TRAILER = re.compile(r'(?m)^(Signed-off-by|Reviewed-on|Reviewed-by|Tested-by|Change-Id|'
		     r'Lustre-commit|Lustre-change|Test-Parameters|Assisted-by):.*\n')

COUNTS = {"MDSCOUNT": "mdscount", "OSTCOUNT": "ostcount", "CLIENTCOUNT": "clientcount"}
VERSIONS = {"MDS1_VERSION": "mds1_version", "OST1_VERSION": "ost1_version",
	    "MGS_VERSION": "mgs_version", "CLIENT_VERSION": "client_version"}
OPS = {"-lt": "<", "-le": "<=", "-gt": ">", "-ge": ">=", "-eq": "==", "-ne": "!=",
       "<": "<", "<=": "<=", ">": ">", ">=": ">=", "==": "==", "!=": "!="}
NEGATE = {"<": ">=", "<=": ">", ">": "<=", ">=": "<", "==": "!=", "!=": "=="}

STOP = re.compile(r'^(cleanup|stopall|cleanup_nocli)\b')
FORMAT = re.compile(r'^(reformat|reformat_and_config|formatall)\b')
UP = re.compile(r'^(setup|setupall)\b')
FAIL_CALL = re.compile(r'(?:^|[|&;(]\s*)(?:fail|fail_nodf|facet_failover|shutdown_facet|reboot_facet)\s+'
		       r'(?:--fsck\s+)?([^\s;|&)]+)')
FAIL_CLIENT = re.compile(r'\b(fail_clients|shutdown_client)\b')
FORCED_SOFT = re.compile(r'''^\s*FAILURE_MODE=["']?SOFT\b''')
ONE_MODE = re.compile(r'not functional with FAILURE_MODE\s*=\s*(HARD|SOFT)')
# what can show a subtest cares which host a target is on.  mdscount and ostcount
# do not: any number of targets can share a host
HOSTS = re.compile(r'\b(facet_host|facet_active_host|facets_on_host|mdts_nodes|osts_nodes|tgts_nodes|'
		   r'remote_(mds|ost|mgs))\b|\b(same|diff\w*|separate) (hosts?|nodes?)\b')

# (pattern, name): evidence in the code of what it changes or reads
REMOTE_CMD = r'\bdo_(facet|nodes?|rpc_nodes)\b'
CLIENT_PARAM = r'\b(llite|osc|mdc|lov|lmv|ldlm)\.'
FIXED_PATH = r'\$\{?(?:DIR|MOUNT)[12]?\}?/(?!\.lustre\b)(\*|[\w.-]+(?![\w.$-]))'
MODIFIES = [(rf'^(?!.*{REMOTE_CMD}).*\bset_param\b.*\bfail_(loc|val)=', "client_fail_loc"),
	    (rf'{REMOTE_CMD}.*\bfail_(loc|val)\b|^(?!.*\bset_param\b).*\bfail_(loc|val)\b', "fail_loc"),
	    (r'\bset_param\b.*\b-P\b|\bconf_param\b', "config"),
	    (rf'^(?!.*{REMOTE_CMD}).*\bset_param\b(?!.*\bfail_(loc|val)=).*{CLIENT_PARAM}', "client_tunables"),
	    (rf'{REMOTE_CMD}.*\bset_param\b(?!.*\bfail_(loc|val)=)|'
	     rf'^(?!.*{REMOTE_CMD})(?!.*{CLIENT_PARAM}).*\bset_param\b(?!.*\bfail_(loc|val)=)', "tunables"),
	    (r'\bpool_(add|new|add_targets)\b', "pools"),
	    (r'\b(cancel_lru_locks|drop_caches)\b', "client_cache"),
	    (r'\b(fill_ost|fill_mdt|exhaust_\w+)\b|\bdd\b.*count=[0-9]{5,}|\bcreatemany\b.*[0-9]{6,}', "space"),
	    (r'\b(load_modules|unload_modules|lustre_rmmod|rmmod|modprobe|iptables|setenforce|useradd|swapoff)\b|'
	     r'\bsysctl -w\b|\btc qdisc\b|> */(etc|proc/sys)/|\bsysrq\b|\blctl (network|panic)\b', "host"),
	    (r'\bchangelog_register\b', "changelog"), (r'\bnodemap\b', "nodemap"),
	    (r'\b(reformat|reformat_and_config|formatall)\b', "format"),
	    (r'\b(setup|cleanup|setupall|stopall|umount|mount_client|umount_client|'
	     r'remount_client|facet_failover|replay_barrier)\b', "mount"),
	    (r'\bsetquota\b', "quota"), (FIXED_PATH, "fixed_path")]
OBSERVES = [(r'\bget_param\b.*\b(stats|rpc_stats|md_stats)\b|\bcalc_stats\b|\bcheck_stats\b', "stats"),
	    (r'\bcur_grant\b|\bgrant_\w+', "grant"),
	    (r'(\$LFS|\blfs) df\b', "free_space"),
	    (r'\bdmesg\b', "console"), (r'\$LCTL (dk|debug_kernel)\b', "debug_log"),
	    (r'\bchangelog_dump\b|\bchangelog_users\b|\$LFS changelog\b', "changelog"),
	    (r'\blru_size\b|\block_count\b', "lock_count"),
	    (r'\bcached_mb\b|\bosc_cached\b|\bread_ahead_stats\b', "cache"),
	    (r'(\bfind|\bls|\bdu)\b[^|;]*\$\{?(DIR|MOUNT)[12]?\}?/?(?=["\s)|;]|$)', "namespace"),
	    (r'\b(run_mdtest|mdtest|mdsrate|obdfilter_survey|ost_survey)\b', "timing"),
	    (r'\b(wait_delete_completed|wait_destroy_complete|wait_mds_ost_sync|sync_all_data)\b', "quiesced")]


def git(repo, *args):
	return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
			      errors="replace", check=False).stdout


def join_skip(lines, i):
	"""The test and skip of the statement holding skip line i, on one line."""
	first = i
	while first > 0 and lines[first - 1].rstrip().endswith(("||", "&&", "\\")):
		first -= 1
	return " ".join(x.strip() for x in lines[first:i + 1])


def parse_skip(stmt, facts):
	"""Record what skip statement stmt requires in facts.  False if it says
	something no header field can."""
	sep = re.search(r'(\|\||&&)\s*skip(_env)?\b', stmt)
	if not sep:
		return False
	cond, wanted = stmt[:sep.start()], sep.group(1) == "||"
	req = facts["requires"]
	if re.search(r'\bPARALLEL\b', cond):
		facts["parallel"] = False
		return True
	if re.search(r'\bSLOW\b', cond):
		facts["slow"] = True
		return True
	found = False
	# every configuration we test has a shell on its servers
	if re.search(r'\bremote_\w+_nodsh\b', cond):
		return True
	# a host is powered off whole in HARD mode, and headers state what HARD needs
	if re.search(r'\bFAILURE_MODE\b', stmt) and re.search(r'\bfacet_host\b|\bMDTs?\b', stmt):
		req["mdsnodes"] = 2
		return True
	if re.search(r'\bin local mode\b', stmt) or re.search(r'\blocal_mode\b', cond):
		req.setdefault("remote", []).append("any")
		return True
	remote = re.search(r'\bremote_(mds|ost|mgs)\b', cond)
	if remote and wanted:
		req.setdefault("remote", []).append(remote.group(1))
		return True
	num = re.search(r'\$?\b(MDSCOUNT|OSTCOUNT|CLIENTCOUNT)\b"?\s*(-lt|-le|-ge|-gt|<=|>=|<|>)\s*"?([0-9]+)', cond)
	if num:
		op = OPS[num.group(2)]
		n = int(num.group(3))
		need = {"<": n, "<=": n + 1, ">=": n, ">": n + 1}[op if wanted else NEGATE[op]]
		# "skip unless COUNT < n" is a maximum, not a minimum
		if (op if wanted else NEGATE[op]) in (">=", ">"):
			req[COUNTS[num.group(1)]] = need
			found = True
	for var, op, ver in re.findall(r'\$?\b(MDS1_VERSION|OST1_VERSION|MGS_VERSION|CLIENT_VERSION)\b"?\s*'
				       r'(-lt|-le|-ge|-gt|-eq|-ne|<=|>=|==|!=|<|>)\s*"?\$\(version_code\s+([^)\s]+)\)', cond):
		op = OPS[op] if wanted else NEGATE[OPS[op]]
		req[VERSIONS[var]] = ver if op == ">=" else f"{op} {ver}"
		found = True
	for facet, eq, kind in re.findall(r'\$\{?(mds1_|ost1_|)FSTYPE\}?"?\s*(==|!=|=)\s*"?(ldiskfs|zfs)\b', cond):
		is_kind = (eq != "!=") == wanted
		value = kind if is_kind else ("zfs" if kind == "ldiskfs" else "ldiskfs")
		req[f"{facet}fstype" if facet else "fstype"] = [value]
		found = True
	if re.search(r'\bcombined_mgs_mds\b', cond):
		neg = re.search(r'!\s*combined_mgs_mds', cond) is not None
		req["mgs"] = "combined" if neg != wanted else "separate"
		found = True
	tool = re.search(r'(?:which|command -v)\s+([\w.-]+)', cond)
	if tool and wanted:
		req.setdefault("tools", []).append(tool.group(1))
		found = True
	return found


def skip_key(stmt):
	"""A skip message as skips.json has it."""
	m = re.search(r'skip(?:_env)?\s+"?([^"]{0,70})', stmt)
	text = (m.group(1) if m else stmt[:70]).strip().lower()
	return re.sub(r'\s+', ' ', re.sub(r'\$\{?\w+\}?|[0-9][0-9.]*', '#', text))


def classify_skip(stmt, facts):
	"""Record a skip that parse_skip could not read, if skips.json knows it."""
	entry = SKIPS.get(skip_key(stmt))
	kind, name = (entry["kind"], entry["name"]) if entry else (None, None)
	if not name or kind not in ("feature", "config", "tool"):
		return False
	req = facts["requires"]
	if name in ("ldiskfs", "zfs"):
		req["fstype"] = [name]
	else:
		values = req.setdefault({"tool": "tools"}.get(kind, kind), [])
		if name not in values:
			values.append(name)
	return True


def logical(body):
	"""Lines without comments, with those continued by a backslash joined."""
	out, partial = [], ""
	for line in body:
		if line.lstrip().startswith("#"):
			continue
		if line.rstrip().endswith("\\"):
			partial += line.rstrip()[:-1] + " "
		else:
			out.append(partial + line)
			partial = ""
	return out


def statements(body):
	"""Top-level statements of a function body: lines at one tab, without
	locals and comments."""
	return [x.strip() for x in body
		if re.match(r'\t\S', x) and not re.match(r'\t(local\b|#|}|fi\b|done\b)', x)]


def state_hints(body, facts):
	stmts = [x for x in statements(body) if not re.search(r'\bskip(_env)?\b|^\[|^\(\(', x)]
	if not stmts:
		return
	first, last = stmts[0], stmts[-1]
	if STOP.match(first):
		facts["start"] = "stopped"
	elif FORMAT.match(first):
		facts["start"] = "formatted"
	if FORMAT.match(last):
		facts["end"] = "formatted"
	elif STOP.match(last):
		facts["end"] = "stopped"
	elif UP.match(last):
		facts["end"] = "mounted"


def failed(lines):
	"""What these lines fail over: mds, ost, mgs, any, client."""
	found = set()
	for line in lines:
		if line.lstrip().startswith("#"):
			continue
		found |= {"client"} if FAIL_CLIENT.search(line) else set()
		for arg in FAIL_CALL.findall(line.strip()):
			for facet in arg.lower().split(","):
				role = re.search(r'mds|mdt|ost|mgs', facet)
				found.add({"mdt": "mds"}.get(role.group(), role.group()) if role else "any")
	return found


def failover_count(body):
	"""Failovers the subtest's own lines ask for, a guide to its time in HARD mode."""
	return sum(len(FAIL_CALL.findall(x.strip())) for x in body if not x.lstrip().startswith("#"))


def failover_needs(facts, body, helpers, only_mode):
	"""Record what is failed over, and what that needs of the hosts in HARD
	mode: a failed server cannot be the host the subtest runs on."""
	req = facts["requires"]
	nodes = node_helpers(body)
	if nodes:
		req["nodecount"] = nodes
	if only_mode:
		req["failure_mode"] = only_mode
	roles = failed(body) | failed(x for src in helpers.values() for x in src.split("\n"))
	if any(FORCED_SOFT.search(x) for x in body):
		roles = set()
	if roles:
		facts["failover"] = sorted(roles)
		facts["failovers"] = failover_count(body)
	if only_mode == "soft":
		return
	if roles - {"client"}:
		req["remote"] = sorted(set(req.get("remote", [])) | (roles - {"client"}))
	if "client" in roles:
		req["clientcount"] = max(req.get("clientcount", 0), 2)


def node_helpers(body):
	"""Nodes that sanity-lnet's setup helpers need, or 0; they skip when short."""
	nodes = 0
	for line in body:
		router = re.search(r'\bsetup_router_test\b(.*)', line)
		if router:
			r = re.search(r'-r\s*(\d+)', router.group(1))
			p = re.search(r'-p\s*(\d+)', router.group(1))
			nodes = max(nodes, 1 + int(r.group(1) if r else 1) + int(p.group(1) if p else 1))
		elif re.search(r'\bsetup_health_test\b', line):
			nodes = max(nodes, 2)
	return nodes


def analyse(body):
	"""The facts in subtest code, given as a list of lines without the
	function line or the run_test line."""
	facts = {"requires": {}, "unparsed_skips": [], "evidence": {}, "hosts": False}
	for i, line in enumerate(body):
		if not re.search(r'\bskip(_env)?\b', line) or line.lstrip().startswith("#"):
			continue
		stmt = join_skip(body, i)
		indent = len(body[i]) - len(body[i].lstrip("\t"))
		top = indent == 1 or (indent == 2 and body[i - 1].rstrip().endswith(("||", "&&")))
		if not (top and parse_skip(stmt, facts)) and not classify_skip(stmt, facts):
			facts["unparsed_skips"].append(stmt[:160])
	state_hints(body, facts)
	code = logical(body)
	facts["hosts"] = any(HOSTS.search(x) for x in code)
	for key, table in (("modifies", MODIFIES), ("observes", OBSERVES)):
		found = {name for pat, name in table if any(re.search(pat, x) for x in code)}
		facts["evidence"][key] = sorted(found)
	return facts


def parse_suite(text):
	"""[(id, summary, first line, last line)] and {helper: source} of a suite.
	A helper is any function that no run_test runs, test_ prefix or not."""
	lines = text.split("\n")
	starts = {}
	helpers = {}
	subtests = []
	for i, line in enumerate(lines):
		m = TEST_FUNC.match(line)
		if m:
			starts[m.group(1)] = i
		m = FUNC.match(line)
		if m:
			j = i
			while j < len(lines) and lines[j] != "}":
				j += 1
			helpers[m.group(1)] = "\n".join(lines[i:j + 1])
			continue
		m = RUN_TEST.match(line)
		if m and m.group(1) in starts and i > starts[m.group(1)]:
			summary = next((x for x in m.groups()[1:] if x is not None), "")
			subtests.append((m.group(1), summary, starts[m.group(1)], i))
	for sub in subtests:
		helpers.pop(f"test_{sub[0]}", None)
	return lines, subtests, helpers


def history(repo, rev, path, sid):
	out = git(repo, "log", "--no-patch", "--format=%x01%H%x00%s%x00%b", "-L",
		  f"/^test_{sid}\\s*()/,/^run_test.* {sid} /:{path}", rev)
	commits = []
	for chunk in out.split("\x01")[1:]:
		sha, subject, msg = (chunk.split("\x00") + ["", ""])[:3]
		commits.append({"hash": sha[:12], "subject": subject, "body": TRAILER.sub("", msg).strip()})
	return commits[::-1]	# oldest first


def durations(suite, days=30):
	"""{subtest id: mean seconds in CI} from Rainshadow, empty if it is not running."""
	out = subprocess.run(["rainshadow", "timings", "tests", "--suite", suite, "--days", str(days),
			      "--limit", "5000"], capture_output=True, text=True, check=False)
	try:
		rows = json.loads(out.stdout)["rows"]
	except (ValueError, KeyError):
		return {}
	return {r["subtest"].removeprefix("test_"): r["mean_s"] for r in rows}


def suites(repo, rev):
	names = git(repo, "ls-tree", "--name-only", rev, "lustre/tests/").split()
	return [x for x in names if x.endswith(".sh")]


def called(body):
	"""Subtests whose functions this body runs."""
	code = [x for x in body if not x.lstrip().startswith("#")]
	return set(re.findall(r'\btest_([0-9]+[A-Za-z]*)\b', "\n".join(code)))


def reached(text, helpers):
	"""{helper: source} of those text runs, nearest first, through helpers too."""
	found = {}
	queue = [text]
	for src in queue:
		code = "\n".join(x for x in src.split("\n") if not x.lstrip().startswith("#"))
		for name in re.findall(r'[A-Za-z_]\w*', code):
			if name in helpers and name not in found:
				found[name] = helpers[name]
				queue.append(helpers[name])
	return found


def inherited(sid, bodies, helpers, seen=None):
	"""Evidence of a subtest with that of the helpers and subtests it calls."""
	seen = (seen or set()) | {sid}
	code = list(bodies[sid])
	for src in reached("\n".join(code), helpers).values():
		code += src.split("\n")[1:-1]
	evidence = analyse(code)["evidence"]
	for other in sorted(called(code) & set(bodies) - seen):
		more = inherited(other, bodies, helpers, seen)
		evidence = {k: sorted(set(evidence[k]) | set(more[k])) for k in evidence}
	return evidence


def suite_facts(head):
	"""What a suite asks of a cluster before its first subtest, outside its
	helper functions."""
	facts = {"requires": {}, "unparsed_skips": []}
	mode = ONE_MODE.search("\n".join(head))
	if mode:
		facts["requires"]["failure_mode"] = {"HARD": "soft", "SOFT": "hard"}[mode.group(1)]
	if re.search(r'^MOUNT_2=\$\{MOUNT_2:-"?yes', "\n".join(head), re.M):
		facts["mounts"] = 2
	if re.search(r'^(reformat\w*|stopall|cleanupall)\b', "\n".join(head), re.M):
		facts["own_mounts"] = True
	inside = False
	for i, line in enumerate(head):
		inside = (inside or bool(FUNC.match(line))) and line != "}"
		if inside or not re.search(r'\bskip(_env)?\b', line) or line.lstrip().startswith("#"):
			continue
		stmt = join_skip(head, i)
		if not parse_skip(stmt, facts) and not classify_skip(stmt, facts):
			facts["unparsed_skips"].append(stmt[:160])
	return facts


def depends(sid, order, bodies, names):
	"""Subtests this one calls, and the latest earlier ones sharing a fixed name."""
	earlier = order[:order.index(sid)]
	sharing = [x for x in earlier if names[x] & names[sid]][-5:]
	return sorted((called(bodies[sid]) & set(bodies) - {sid}) | set(sharing))


def body_of(lines, first, last):
	return lines[first + 1:last - 1] if lines[last - 1] == "}" else lines[first + 1:last]


def extract_suite(repo, rev, path, ids=None, with_history=True, with_times=True):
	suite = os.path.basename(path)[:-3]
	lines, subtests, helpers = parse_suite(git(repo, "show", f"{rev}:{path}"))
	bodies = {x[0]: body_of(lines, x[2], x[3]) for x in subtests}
	suite_wide = suite_facts(lines[:subtests[0][2]] if subtests else [])
	only_mode = suite_wide["requires"].get("failure_mode")
	order = [x[0] for x in subtests]
	names = {sid: {m for x in logical(bodies[sid]) for m in re.findall(FIXED_PATH, x)} - {"*"}
		 for sid in order}
	sources = {x[0]: "\n".join(lines[x[2]:x[3] + 1]) for x in subtests}
	if ids:
		subtests = [x for x in subtests if x[0] in ids]
	times = durations(suite) if subtests and with_times else {}

	def one(item):
		sid, summary, first, last = item
		body = bodies[sid]
		text = sources[sid]
		used = reached(text, helpers)
		used.update({f"test_{x}": sources[x] for x in called(body) & set(sources) - {sid}})
		facts = analyse(body)
		facts["evidence"] = inherited(sid, bodies, helpers)
		for key, value in suite_wide["requires"].items():
			facts["requires"].setdefault(key, value)
		failover_needs(facts, body, used, only_mode)
		needed = depends(sid, order, bodies, names)
		if needed:
			facts["depends"] = needed
		if sid in times:
			facts["duration"] = times[sid]
		return {"suite": suite, "id": sid, "path": path, "line": first + 1, "summary": summary,
			"code": text, "helpers": used, "facts": facts, "suite_facts": suite_wide,
			"history": history(repo, rev, path, sid) if with_history else []}

	with concurrent.futures.ThreadPoolExecutor(8) as pool:
		return list(pool.map(one, subtests))


def main():
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("--lustre", default=os.path.expanduser("~/lustre-release"))
	ap.add_argument("--rev", default="HEAD")
	ap.add_argument("--suite", help="one suite, e.g. sanity")
	ap.add_argument("--ids", help="comma separated subtests")
	ap.add_argument("-o", "--output", default="-")
	ap.add_argument("--no-times", action="store_true", help="do not ask Rainshadow")
	ap.add_argument("--stats", action="store_true", help="counts only, no history")
	args = ap.parse_args()

	paths = suites(args.lustre, args.rev)
	if args.suite:
		paths = [x for x in paths if os.path.basename(x) == f"{args.suite}.sh"]
	ids = set(args.ids.split(",")) if args.ids else None
	out = sys.stdout if args.output == "-" else open(args.output, "w", encoding="utf-8")
	stats = Counter()
	for path in paths:
		for rec in extract_suite(args.lustre, args.rev, path, ids, not args.stats,
					 not (args.no_times or args.stats)):
			stats["subtests"] += 1
			facts = rec["facts"]
			stats["with requires"] += bool(facts["requires"])
			stats["with unparsed skips"] += bool(facts["unparsed_skips"])
			stats["with start"] += "start" in facts
			stats["with end"] += "end" in facts
			stats["with failover"] += "failover" in facts
			stats["needs hosts for HARD"] += bool({"mdsnodes", "remote"} & set(facts["requires"]))
			stats["with feature or config"] += bool({"feature", "config"} & set(facts["requires"]))
			stats["with depends"] += "depends" in facts
			stats["no evidence of change or read"] += not any(facts["evidence"].values())
			if not args.stats:
				print(json.dumps(rec), file=out)
	print(json.dumps(stats), file=sys.stderr)


if __name__ == "__main__":
	main()
