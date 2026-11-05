#!/usr/bin/env python3
"""The subtest stack: every subtest in every configuration, ranked greedily by
the bugs not yet caught it catches per second of its own run time.  Writes
SUBTEST_STACK.md, docs/subtest-stack.html and docs/subtest-curve.svg.

  eval/subtests.py --fetch      # subtest timings from Rainshadow (seconds), then the stack
  eval/subtests.py              # the stack again, from the timings already fetched

Bugs are the eval's, built from data/failures.db by eval/cases.py's code
(--cases reads a cases file instead), each change counting once.  A subtest
in a configuration catches a bug with the chance it failed with it in the
sessions of that configuration that ran its script on the patchset: a session
caught it when the subtest failed there, however that failure was triaged, as
eval/cases.py credits a script.  Whiteout as in the
test-set order (eval/predictors/ranked.py): what a pick catches no longer
counts for the picks after it.

Run time is Rainshadow's per-subtest timings over the same window, from the
review groups of lustre-reviews, each group's runs split over the
configurations its sessions ran in.  A subtest with no timing of its own in a
configuration is priced at its time in the others, and failing that at its
script's time over its subtest count; both are marked.
"""
from __future__ import annotations

import argparse
import datetime as dt
import heapq
import html
import json
import math
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from classes import CLASSES, class_of  # noqa: E402

FULL = "2024-10-01"
RECENT = "2026-03-20"           # today's matrix (eval/costs.py)
INFRA = {"node-provisioning", "lustre-initialization"}
TIMINGS = ROOT / "data" / "subtest-timings.json"
TOP = 200                       # rows of the stack in the Markdown
WORST = 60                      # rows of the costly end
PAGE_ROWS = 1000                # rows of the stack on the page
NOISE = 0.02                    # a group's share of a configuration below this is a stray session
POISSON0 = -math.log(0.05)      # 95% upper bound on a count when none were seen


def last_day(db: sqlite3.Connection) -> str:
	return db.execute("SELECT max(day) FROM session WHERE job = 'lustre-reviews'").fetchone()[0][:10]


def fetch(db: sqlite3.Connection, out: Path) -> dict:
	"""Per-subtest timings per test group of lustre-reviews, for each window and
	for the last 30 days (how often CI runs each subtest today)."""
	sys.path.insert(0, str(ROOT))
	from harvest import Client
	cl = Client()
	end = last_day(db)
	today = dt.date.today()
	windows = {"full": (FULL, end), "recent": (RECENT, end),
		   "last30": ((today - dt.timedelta(days=29)).isoformat(), today.isoformat())}
	d = {"fetched": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "windows": {}}
	for name, (a, b) in windows.items():
		t0 = time.time()
		r = cl.call("/timings/subtests", {"start": a, "end": b, "job": "lustre-reviews", "by": "group"})
		d["windows"][name] = {"start": a, "end": b, "pending": r.get("pending"),
				      "rows": [[x["test_group"], x["suite"], x["subtest"], x["runs"], x["dur_sum"]]
					       for x in r["rows"]]}
		print(f"  timings {name} {a}..{b}: {len(r['rows'])} rows, {time.time() - t0:.1f} s", file=sys.stderr)
	out.write_text(json.dumps(d) + "\n")
	return d


def group_shares(db: sqlite3.Connection, start: str) -> dict[str, dict[str, float]]:
	"""Each review group's sessions per configuration, as shares."""
	n: dict[str, Counter] = defaultdict(Counter)
	db.row_factory = sqlite3.Row
	for s in db.execute("SELECT test_group, test_name, server_fs, client_distro, mdts, osts FROM session "
			    "WHERE job = 'lustre-reviews' AND day >= ? AND test_group LIKE 'review%'", (start,)):
		c = class_of(dict(s))
		if c in CLASSES:
			n[s["test_group"]][c] += 1
	out = {}
	for g, cs in n.items():
		tot = sum(cs.values())
		out[g] = {c: k / tot for c, k in cs.items() if k / tot >= NOISE}
	return out


def by_class(rows: list, shares: dict) -> dict[tuple, list[float]]:
	"""(suite, subtest, configuration) -> [runs, seconds], over the review
	matrix's groups."""
	out: dict[tuple, list[float]] = defaultdict(lambda: [0.0, 0.0])
	for g, suite, sub, runs, secs in rows:
		# The subtest-change groups repeat each edited test dozens of times: what
		# a patch asks for, not what the subtest costs in the matrix.
		if suite in INFRA or not g.startswith("review") or g.endswith("subtest-change"):
			continue
		for c, sh in shares.get(g, {}).items():
			t = out[(suite, sub, c)]
			t[0] += runs * sh
			t[1] += secs * sh
	return out


def faulty_cases(db: sqlite3.Connection, path: Path | None) -> list[dict]:
	"""The eval's faulty cases: from a cases file, or built from the database by
	eval/cases.py's own code, without the patch text it does not need here."""
	if path:
		with path.open() as f:
			found = [json.loads(line) for line in f if '"faulty": true' in line]
	else:
		from cases import CUTOFF, EVERY, SAMPLE_FRACTION, build_cases
		found = [c for c in build_cases(db, CUTOFF, SAMPLE_FRACTION, EVERY, None) if c["faulty"]]
	return [{"change": c["change"], "patchset": c["patchset"], "day": c["day"],
		 "bugs": [[tuple(u[:2]) for u in b["units"]] for b in c["bugs"]]} for c in found]


def load_bugs(db: sqlite3.Connection, path: Path | None = None) -> list[dict]:
	"""Every bug of the faulty cases, with the chance each (suite, subtest,
	configuration) caught it."""
	cases = faulty_cases(db, path)
	db.row_factory = None
	db.execute("CREATE TEMP TABLE k (change INTEGER, patchset INTEGER, PRIMARY KEY (change, patchset))")
	db.executemany("INSERT OR IGNORE INTO k VALUES (?, ?)", [(c["change"], c["patchset"]) for c in cases])
	db.row_factory = sqlite3.Row
	sessions: dict[tuple, list[str]] = defaultdict(list)
	cls = {}
	for s in db.execute("SELECT s.* FROM session s JOIN k ON k.change = s.change AND k.patchset = s.patchset "
			    "WHERE s.job = 'lustre-reviews'"):
		# A developer's custom session runs their own lists and environment: as in
		# eval/cases.py, it is evidence for nothing.
		c = None if s["test_group"] == "custom" else class_of(dict(s))
		if c:
			cls[s["id"]] = c
			sessions[(s["change"], s["patchset"])].append(s["id"])
	db.row_factory = None
	db.execute("CREATE TEMP TABLE sid (id TEXT PRIMARY KEY)")
	db.executemany("INSERT INTO sid VALUES (?)", [(x,) for x in cls])
	sets: dict[str, list[tuple]] = defaultdict(list)
	for sid, suite, failed in db.execute("SELECT t.session_id, t.suite, t.failed FROM test_set t "
					     "JOIN sid ON sid.id = t.session_id"):
		if suite not in INFRA:
			sets[sid].append((suite, set(json.loads(failed)) if failed else set()))
	rows: dict[tuple, list[tuple]] = defaultdict(list)
	for ch, ps, sid, suite, sub in db.execute(
			"SELECT f.change, f.patchset, f.session_id, f.suite, f.subtest FROM failure f "
			"JOIN k ON k.change = f.change AND k.patchset = f.patchset WHERE f.class != 'ambient'"):
		if sid in cls:
			rows[(ch, ps)].append((sid, suite, sub))
	out = []
	for c in cases:
		key = (c["change"], c["patchset"])
		ss = sessions.get(key, [])
		for units in c["bugs"]:
			# "(suite crash)" and the like: the script failed in no subtest of its own.
			subs = {u for u in units if not u[1].startswith("(") and u[0] not in INFRA}
			suites = {u[0] for u in subs}
			ran: dict[tuple, set] = defaultdict(set)
			failed: dict[tuple, set] = defaultdict(set)
			for sid in ss:
				for suite, bad in sets.get(sid, ()):
					if suite in suites:
						ran[(suite, cls[sid])].add(sid)
						for st in bad:
							if (suite, st) in subs:
								failed[(suite, st, cls[sid])].add(sid)
			for sid, suite, sub in rows.get(key, ()):
				if (suite, sub) in subs:
					failed[(suite, sub, cls[sid])].add(sid)
			ev = {k: len(v) / len(ran[(k[0], k[2])] | v) for k, v in failed.items()}
			out.append({"change": c["change"], "day": c["day"], "ev": ev,
				    "suite_only": not subs})
	return out


def stack(bugs: list[dict], timing: dict, last30: dict, costs: dict, start: str, end: str = "9") -> dict:
	"""The greedy order over every (suite, subtest, configuration) with a timing
	or a catch in the window, its bugs from start up to end."""
	bugs = [b for b in bugs if start <= b["day"] < end]
	per_change = Counter(b["change"] for b in bugs)
	w = [1 / per_change[b["change"]] for b in bugs]
	total = sum(w)
	by_cand: dict[tuple, list[tuple]] = defaultdict(list)
	for i, b in enumerate(bugs):
		for k, p in b["ev"].items():
			by_cand[k].append((i, p))
	per_sub: dict[tuple, list[float]] = defaultdict(lambda: [0.0, 0.0])
	for (suite, sub, _), (runs, secs) in timing.items():
		per_sub[(suite, sub)][0] += runs
		per_sub[(suite, sub)][1] += secs

	def price(k):
		runs, secs = timing.get(k, (0, 0))
		if runs >= 1:
			return secs / runs, ""
		runs, secs = per_sub.get(k[:2], (0, 0))
		if runs >= 1:
			return secs / runs, "other"
		n = costs.get("suite_subtests", {}).get(k[0])
		s = costs["suite_class_s"].get(f"{k[0]}|{k[2]}", costs["suite_s"].get(k[0]))
		return (s / n if s and n else 60.0), "share"

	cands = {k for k, (runs, _) in timing.items() if runs >= 1} | set(by_cand)
	info = {k: price(k) for k in cands}
	secs = {k: max(1.0, v[0]) for k, v in info.items()}
	only = defaultdict(float)
	caught = defaultdict(float)
	for k, hits in by_cand.items():
		for i, p in hits:
			caught[k] += w[i] * p
			if len(bugs[i]["ev"]) == 1:
				only[k] += w[i] * p
	miss = [1.0] * len(bugs)

	def gain(k):
		return sum(w[i] * miss[i] * p for i, p in by_cand.get(k, ()))

	# Lazy greedy: a candidate's gain only falls as others are picked, so a stale
	# gain is an upper bound and the top of the heap need only be re-checked.
	heap = [(-gain(k) / secs[k], k) for k in by_cand if k in cands]
	heapq.heapify(heap)
	order = []
	while heap:
		v, k = heapq.heappop(heap)
		g = gain(k)
		if g <= 0:
			continue
		if heap and g / secs[k] < -heap[0][0] - 1e-15:
			heapq.heappush(heap, (-g / secs[k], k))
			continue
		for i, p in by_cand[k]:
			miss[i] *= 1 - p
		order.append((k, g))
	picked = {k for k, _ in order}
	rest = sorted((k for k in cands if k not in picked), key=lambda k: (secs[k], k))
	order += [(k, 0.0) for k in rest]
	all_secs = sum(secs.values())
	ci30 = {k: secs[k] * last30.get(k, (0, 0))[0] for k in cands}
	all_ci = sum(ci30.values()) or 1
	rows = []
	cum_g = cum_s = cum_ci = 0.0
	for rank, (k, g) in enumerate(order, 1):
		cum_g += g
		cum_s += secs[k]
		cum_ci += ci30[k]
		hours = timing.get(k, (0, 0))[1] / 3600
		rows.append({"rank": rank, "suite": k[0], "subtest": k[1], "cls": k[2], "secs": secs[k],
			     "priced": info[k][1], "runs30": last30.get(k, (0, 0))[0], "adds": g,
			     "cum_bugs": cum_g / total, "cum_time": cum_s / all_secs, "cum_s": cum_s, "cum_ci": cum_ci / all_ci,
			     "caught": caught[k], "only": only[k], "hours": hours,
			     "per_1000h": 1000 * caught[k] / hours if hours >= 1 else None,
			     "hi_only": 1000 * POISSON0 / hours if hours >= 0.01 else None})
	return {"start": start, "rows": rows, "total": total, "bugs": len(bugs), "changes": len(per_change),
		"suite_only": sum(wi for wi, b in zip(w, bugs) if b["suite_only"]) / total,
		"no_catch": sum(wi for wi, b in zip(w, bugs) if not b["ev"] and not b["suite_only"]) / total,
		"ceiling": cum_g / total, "all_secs": all_secs, "all_ci30": all_ci}


def held_out(order: list[dict], bugs: list[dict], start: str) -> list[tuple]:
	"""An order ranked on the bugs before start, measured on the bugs after:
	(time so far, share of the later bugs caught) at each step."""
	bugs = [b for b in bugs if b["day"] >= start]
	per_change = Counter(b["change"] for b in bugs)
	w = [1 / per_change[b["change"]] for b in bugs]
	total = sum(w)
	by_cand: dict[tuple, list[tuple]] = defaultdict(list)
	for i, b in enumerate(bugs):
		for k, p in b["ev"].items():
			by_cand[k].append((i, p))
	miss = [1.0] * len(bugs)
	caught = 0.0
	out = []
	for x in order:
		for i, p in by_cand.get((x["suite"], x["subtest"], x["cls"]), ()):
			caught += w[i] * miss[i] * p
			miss[i] *= 1 - p
		out.append((x["cum_time"], caught / total))
	return out


def at(st: dict, n: int) -> dict:
	r = st["rows"][min(n, len(st["rows"])) - 1]
	return {"n": n, "bugs": r["cum_bugs"], "time": r["cum_time"], "ci": r["cum_ci"], "hours": r["cum_s"] / 3600}


def costly(st: dict) -> list[dict]:
	"""The most CI time that never caught a bug no other subtest caught too."""
	rows = [r for r in st["rows"] if r["only"] == 0 and r["hours"] >= 1]
	return sorted(rows, key=lambda r: -r["hours"])


def build(args) -> dict:
	t0 = time.time()
	db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=60)
	tm = fetch(db, TIMINGS) if args.fetch or not TIMINGS.exists() else json.loads(TIMINGS.read_text())
	t1 = time.time()
	costs = json.loads((HERE / "costs.json").read_text())
	bugs = load_bugs(db, Path(args.cases) if args.cases else None)
	t2 = time.time()
	out = {"fetched": tm["fetched"], "times": {}}
	recent_shares = group_shares(db, RECENT)
	last30 = by_class(tm["windows"]["last30"]["rows"], recent_shares)
	for name, start in (("full", FULL), ("recent", RECENT)):
		win = tm["windows"][name]
		timing = by_class(win["rows"], group_shares(db, start))
		st = stack(bugs, timing, last30, costs, start)
		st["end"] = win["end"]
		st["pending"] = win.get("pending")
		out[name] = st
	train = stack(bugs, by_class(tm["windows"]["full"]["rows"], group_shares(db, FULL)), last30, costs, FULL, RECENT)
	out["held"] = held_out(train["rows"], bugs, RECENT)
	# The same measure for an order that knows nothing of bugs: cheapest first.
	cheap, cum = [], 0.0
	for x in sorted(train["rows"], key=lambda x: (x["secs"], x["suite"], x["subtest"], x["cls"])):
		cum += x["secs"]
		cheap.append({"suite": x["suite"], "subtest": x["subtest"], "cls": x["cls"], "cum_time": cum / train["all_secs"]})
	out["cheap"] = held_out(cheap, bugs, RECENT)
	changes: dict[tuple, set] = defaultdict(set)
	for b in bugs:
		for k in b["ev"]:
			changes[k].add(b["change"])
	per_change = Counter(b["change"] for b in bugs)
	out["spread"] = {"catchers": len(changes), "one_change": sum(len(v) == 1 for v in changes.values()) / len(changes),
			 "several": sum(1 / per_change[b["change"]] for b in bugs if len(b["ev"]) >= 2) / len(per_change)}
	out["last30"] = tm["windows"]["last30"]
	out["times"] = {"timings": t1 - t0, "bugs": t2 - t1, "stack": time.time() - t2}
	return out


# -- the documents -----------------------------------------------------------------

def label(r: dict) -> str:
	return {"": "", "other": "*", "share": "**"}[r["priced"]]


def fmt_secs(s: float) -> str:
	return f"{s:,.0f}" if s >= 10 else f"{s:.1f}"


def pct(v: float) -> str:
	"""A share, with two significant figures when it is small."""
	if v >= 0.1 or v == 0:
		return f"{v:.1%}"
	return f"{100 * v:.{max(1, 1 - math.floor(math.log10(100 * v)))}f}%"


def compare(d: dict) -> dict:
	f, r = d["full"]["rows"], d["recent"]["rows"]
	key = lambda x: (x["suite"], x["subtest"], x["cls"])   # noqa: E731
	ff = {key(x): x for x in f}
	rr = {key(x): x for x in r}
	top = lambda rows, n: [key(x) for x in rows[:n] if x["adds"] > 0]   # noqa: E731
	f50, r50, f200, r200 = set(top(f, 50)), set(top(r, 50)), set(top(f, 200)), set(top(r, 200))
	new = [rr[k] for k in top(r, 50) if k not in f200]
	faded = [ff[k] for k in top(f, 50) if k not in r200]
	cls_share = {}
	for name, rows in (("full", f), ("recent", r)):
		c = defaultdict(float)
		for x in rows:
			c[x["cls"]] += x["adds"]
		tot = sum(c.values()) or 1
		cls_share[name] = {k: v / tot for k, v in c.items()}
	return {"same50": len(f50 & r50), "same200": len(f200 & r200), "new": new, "faded": faded,
		"rank_of": {k: x["rank"] for k, x in ff.items()}, "cls": cls_share}


def share_at(curve: list[tuple], t: float) -> float:
	"""The share of bugs a curve has caught by time share t."""
	return next((y for x, y in curve if x >= t - 1e-12), curve[-1][1] if curve else 0.0)


def held_text(d: dict) -> str:
	h, c, sp = d["held"], d["cheap"], d["spread"]
	pts = ", ".join(f"the top {n:,}{' catch' if n == 50 else ''} {h[min(n, len(h)) - 1][1]:.0%}"
			for n in (50, 500, 1000, 2000))
	cmp_ = ", ".join(f"{share_at(h, t):.0%} against {share_at(c, t):.0%} at {t:.0%} of the time"
			 for t in (0.01, 0.05, 0.1, 0.25))
	return (f"Both tables are in-sample: each stack is ranked on the bugs it is measured on, which flatters it.  "
		f"Ranked on the bugs before {RECENT} and measured on the bugs since (the dashed line in the chart), "
		f"{pts} of the later bugs, where the recent window's own top 500 catch "
		f"{at(d['recent'], 500)['bugs']:.0%}.  A subtest's past catches say little about its next ones: of the "
		f"{sp['catchers']:,} subtests in their configurations that ever caught a bug, {sp['one_change']:.0%} "
		f"caught bugs of one change only.  And {sp['several']:.0%} of bugs can be caught in more than one place "
		"(the same subtest in another configuration, or other subtests the patch broke too), so the cheapest of "
		"them will do: past the head, an order that knows nothing of bugs, cheapest subtest first (the dotted "
		f"line), does better on the later bugs, {cmp_}.  The head of the stack is a guide to where catches have "
		"come from, not a list to run.")


def ceiling_text(st: dict) -> str:
	return (f"Running every subtest once in every configuration catches {st['ceiling']:.0%} of the bugs.  Of "
		f"the rest, {st['no_catch']:.0%} failed in no review session (only in developers' custom sessions or "
		f"interop runs, which the eval credits to no configuration either), {st['suite_only']:.0%} failed only "
		"as a whole script (a crash or timeout outside any subtest, which no one subtest owns), and the "
		"remainder failed in only some of the sessions that ran them, so one run catches them only that "
		"often.")


def md(d: dict) -> str:
	o = []
	w = o.append
	f, r = d["full"], d["recent"]
	cmp_ = compare(d)
	w("# The subtest stack\n")
	w(f"Generated by `eval/subtests.py` from the eval's bugs (`eval/cases.py`, on `data/failures.db`) and "
	  f"Rainshadow's per-subtest timings (fetched {d['fetched'][:10]}).  Every number below is computed; rerun "
	  "`eval/subtests.py --fetch` to refresh it.  The page version, sortable and with the full stack: "
	  "[docs/subtest-stack.html](docs/subtest-stack.html).\n")
	w("Every subtest in every configuration, ranked greedily: each step adds the subtest that catches the most "
	  "bugs not yet caught per second of its own run time.  A bug one pick catches no longer counts for the "
	  "picks after it.  This is the test-set order of [HOW_IT_WORKS.md](HOW_IT_WORKS.md) one level down: "
	  "what each subtest is worth for its own time.\n")
	w("![Bugs caught against time](docs/subtest-curve.svg)\n")
	w("## How it is counted\n")
	w("- **Bugs** are the eval's (`eval/bugs.py`, `eval/cases.py`): a patchset's faults grouped into bugs, a "
	  "series' inherited bugs left to the change that brought them, and each change counting once (a change "
	  "with three bugs gives each a third).  Shares of bugs are of all of them.")
	w("- **A catch**: a subtest in a configuration catches a bug when it failed with it in a session of that "
	  "configuration on the patchset, however that failure was triaged (as `eval/cases.py` credits a script).  "
	  "Its chance of catching it is the share of the sessions running its script there in which it failed; "
	  "nearly all are 1.  Developers' custom sessions count for nothing, as in the eval.")
	w("- **Whiteout**, as in the test-set order: a pick lowers each bug's chance of still being missed, and the "
	  "picks after it are credited only with what is left.")
	w("- **Time** is Rainshadow's per-subtest timings (`rainshadow timings tests --by-group`) over the same "
	  "window, from lustre-reviews' review groups, each group's runs split over the configurations its sessions "
	  "ran in.  The subtest-change groups are left out of the time: they repeat each edited test dozens of times "
	  "for that patch.  A subtest's time is its own run: not its script's setup and cleanup, nor a session's.  "
	  "Running a few subtests alone (`ONLY=`) still pays those, so the head of the stack is cheaper here than "
	  "it would be in CI.")
	w("")
	w("## What it shows\n")
	for st, name in ((f, "Full history"), (r, "Since the matrix changed")):
		w(f"**{name}** ({st['start']} to {st['end']}): {st['bugs']:,} bugs of {st['changes']:,} changes, "
		  f"{len(st['rows']):,} subtests in their configurations.\n")
		w("| top | share of bugs | minutes, one run of each | share of the time | share of CI's subtest hours today |")
		w("|---|---|---|---|---|")
		for n in (10, 50, 100, 200, 500, 1000, 2000):
			a = at(st, n)
			w(f"| {n} | {a['bugs']:.1%} | {60 * a['hours']:,.1f} | {pct(a['time'])} | {pct(a['ci'])} |")
		w(f"| all {len(st['rows']):,} | {st['ceiling']:.1%} | {st['all_secs'] / 60:,.0f} | 100% | 100% |")
		w("")
	w(held_text(d) + "\n")
	w(ceiling_text(f) + "\n")
	w("*Minutes, one run of each*: the stack so far, each subtest run once.  *Share of the time*: that against "
	  "one run of every subtest in every configuration.  *Share of CI's subtest hours today* weighs each "
	  "subtest by how often CI ran it in the last 30 days.\n")
	w("## Configurations\n")
	w("A subtest is ranked per configuration: the eval's classes (`eval/classes.py`), which are what "
	  "`Test-Parameters` can ask for.  The same test catches different bugs on ZFS and ldiskfs, with one MDT "
	  "and with four, and costs differently in each (DNE runs the remote directory paths, ZFS the OSD's "
	  "own), so pooling them would hide where a catch comes from and price it wrongly.  Distro is not a "
	  "configuration except where the eval makes it one (Ubuntu and SLES clients, aarch64).  Share of the "
	  "stack's catches per configuration:\n")
	w("| configuration | full history | since " + RECENT + " |")
	w("|---|---|---|")
	for c in sorted(cmp_["cls"]["full"], key=lambda c: -cmp_["cls"]["full"][c]):
		w(f"| {c} | {cmp_['cls']['full'][c]:.1%} | {cmp_['cls']['recent'].get(c, 0):.1%} |")
	w("")
	w(f"## The top {TOP}, full history\n")
	w(table_md(f["rows"][:TOP]))
	w(COLUMNS)
	w(f"## Since {RECENT}\n")
	w(f"The same stack over the bugs and timings since the review matrix changed.  {cmp_['same50']} of the "
	  f"full history's top 50 are in its top 50, {cmp_['same200']} of the top 200.  The top {min(50, TOP)}:\n")
	w(table_md(r["rows"][:50]))
	if cmp_["new"]:
		w("New near the top since the matrix changed (in the recent top 50, not in the full history's top 200; "
		  "full-history rank in brackets): " + ", ".join(
			f"{x['suite']} {x['subtest']} ({x['cls']}, {cmp_['rank_of'].get((x['suite'], x['subtest'], x['cls']), '-')})"
			for x in cmp_["new"]) + ".\n")
	if cmp_["faded"]:
		w("Fallen away (in the full history's top 50, not in the recent top 200): " + ", ".join(
			f"{x['suite']} {x['subtest']} ({x['cls']})" for x in cmp_["faded"]) + ".\n")
	w("## Costly, and catching nothing new\n")
	w(f"The subtests that took the most CI time from {f['start']} to {f['end']} and never caught a bug that "
	  "no other subtest caught too.  *at most* is the 95% upper bound on the rate of bugs only it would "
	  "catch, per 1,000 hours of its running: low means it is poor for its time with confidence, not just "
	  "unlucky.  *caught* is the bugs it did catch, which others caught as well.  These are where to look "
	  "for test time to cut.\n")
	w("| suite | subtest | configuration | hours | hours per 30 days | runs per 30 days | mean s | caught | "
	  "at most per 1,000 h |")
	w("|---|---|---|---|---|---|---|---|---|")
	for x in costly(f)[:WORST]:
		w(f"| {x['suite']} | {x['subtest']} | {x['cls']} | {x['hours']:,.0f} | "
		  f"{x['secs'] * x['runs30'] / 3600:,.0f} | {x['runs30']:,.0f} | {fmt_secs(x['secs'])}{label(x)} | "
		  f"{x['caught']:.1f} | {x['hi_only']:.2f} |")
	w("")
	w("## Rebuilding it\n")
	t = d["times"]
	w("```bash\neval/subtests.py --fetch     # timings from Rainshadow, then the stack\n"
	  "eval/subtests.py             # the stack from the timings already fetched\n```\n")
	w(f"The last run took {sum(t.values()):.0f} s: {t['timings']:.1f} s for the timings, {t['bugs']:.0f} s for "
	  f"the bugs and their subtests, {t['stack']:.0f} s for both stacks and the documents.  The bugs are built "
	  "from the database each time, so fresh failures need only the harvest first (`./harvest.py fetch "
	  "--days 30 && ./harvest.py sessions`, about 20 s for a month; see README.md).\n")
	return "\n".join(o)


COLUMNS = ("*mean s*: seconds per run in that configuration (`*` priced at its time in other configurations, "
	   "`**` at its script's time over its subtest count: no timing of its own).  *runs per 30 days*: how "
	   "often CI ran it in the last 30 days.  *adds*: bugs it catches that no subtest above it caught, each "
	   "change counting once (a change with three bugs gives each a third).  *caught*: every bug it caught, "
	   "whatever caught it too; *only it*: bugs no other subtest or configuration caught.  *bugs per "
	   "1,000 h*: *caught* over the hours CI spent running it in the window (blank under an hour).\n")


def table_md(rows: list[dict]) -> str:
	o = ["| # | suite | subtest | configuration | mean s | runs per 30 days | adds | bugs so far | time so far | "
	     "caught | only it | bugs per 1,000 h |",
	     "|---|---|---|---|---|---|---|---|---|---|---|---|"]
	for x in rows:
		eff = f"{x['per_1000h']:.1f}" if x["per_1000h"] is not None else ""
		o.append(f"| {x['rank']} | {x['suite']} | {x['subtest']} | {x['cls']} | {fmt_secs(x['secs'])}{label(x)} | "
			 f"{x['runs30']:,.0f} | {x['adds']:.2f} | {x['cum_bugs']:.1%} | {pct(x['cum_time'])} | "
			 f"{x['caught']:.2f} | {x['only']:.2f} | {eff} |")
	return "\n".join(o) + "\n"


def svg_curve(d: dict, themed: bool) -> str:
	"""Cumulative share of bugs against cumulative share of time, time on a log
	scale: the stack's head is where nearly everything happens."""
	W, H, L, R, T, B = 760, 470, 64, 24, 20, 104
	lo = 1e-5
	X = lambda v: L + (math.log10(max(v, lo)) - math.log10(lo)) / -math.log10(lo) * (W - L - R)   # noqa: E731
	Y = lambda v: T + (1 - v) * (H - T - B)          # noqa: E731
	col = ({"fg": "var(--ink)", "mute": "var(--mute)", "grid": "var(--rule)", "full": "var(--accent)",
		"recent": "var(--oracle)", "bg": "var(--paper)"} if themed else
	       {"fg": "#1d2430", "mute": "#5d6675", "grid": "#d9dde3", "full": "#0b6e8a", "recent": "#b5651d",
		"bg": "#ffffff"})
	o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" role="img" '
	     'aria-label="Share of bugs caught against share of test time, subtests in stack order" '
	     'font-family="system-ui, sans-serif" font-size="12">']
	if not themed:
		o.append(f'<rect width="{W}" height="{H}" fill="{col["bg"]}"/>')
	for e in range(-5, 1):
		v = 10 ** e
		o.append(f'<line x1="{X(v):.1f}" y1="{T}" x2="{X(v):.1f}" y2="{H - B}" stroke="{col["grid"]}"/>')
		o.append(f'<text x="{X(v):.1f}" y="{H - B + 16}" text-anchor="middle" fill="{col["mute"]}">'
			 f'{v * 100:g}%</text>')
	for v in (0, 0.2, 0.4, 0.6, 0.8, 1.0):
		o.append(f'<line x1="{L}" y1="{Y(v):.1f}" x2="{W - R}" y2="{Y(v):.1f}" stroke="{col["grid"]}"/>')
		o.append(f'<text x="{L - 8}" y="{Y(v) + 4:.1f}" text-anchor="end" fill="{col["mute"]}">{v:.0%}</text>')
	o.append(f'<text x="{(L + W - R) / 2}" y="{H - B + 36}" text-anchor="middle" fill="{col["fg"]}">'
		 'test time, share of one run of every subtest in every configuration (log scale)</text>')
	o.append(f'<text transform="translate(16 {(T + H - B) / 2}) rotate(-90)" text-anchor="middle" '
		 f'fill="{col["fg"]}">bugs caught, share of all bugs</text>')
	for name, c in (("full", col["full"]), ("recent", col["recent"])):
		rows = d[name]["rows"]
		pts, last = [], None
		for x in rows:
			px, py = X(x["cum_time"]), Y(x["cum_bugs"])
			if last is None or abs(px - last[0]) + abs(py - last[1]) >= 0.7:
				pts.append((px, py))
				last = (px, py)
		pts.append((X(rows[-1]["cum_time"]), Y(rows[-1]["cum_bugs"])))
		path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(pts))
		o.append(f'<path d="{path}" fill="none" stroke="{c}" stroke-width="2.5" stroke-linejoin="round"/>')
		for n in (50, 500):
			a = at(d[name], n)
			o.append(f'<circle cx="{X(a["time"]):.1f}" cy="{Y(a["bugs"]):.1f}" r="4" fill="{c}"/>')
			if name == "full":
				o.append(f'<text x="{X(a["time"]) + 8:.1f}" y="{Y(a["bugs"]) + 16:.1f}" '
					 f'fill="{col["fg"]}">top {n}: {a["bugs"]:.0%} at {pct(a["time"])}</text>')
	pts, last = [], None
	for tx, ty in d["held"]:
		px, py = X(tx), Y(ty)
		if last is None or abs(px - last[0]) + abs(py - last[1]) >= 0.7:
			pts.append((px, py))
			last = (px, py)
	path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(pts))
	o.append(f'<path d="{path}" fill="none" stroke="{col["recent"]}" stroke-width="2" stroke-dasharray="6 4" '
		 'stroke-linejoin="round"/>')
	pts, last = [], None
	for tx, ty in d["cheap"]:
		px, py = X(tx), Y(ty)
		if last is None or abs(px - last[0]) + abs(py - last[1]) >= 0.7:
			pts.append((px, py))
			last = (px, py)
	path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(pts))
	o.append(f'<path d="{path}" fill="none" stroke="{col["mute"]}" stroke-width="2" stroke-dasharray="2 3" '
		 'stroke-linejoin="round"/>')
	legend = ((f"full history, from {d['full']['start']}", col["full"], ""),
		  (f"since {d['recent']['start']}", col["recent"], ""),
		  (f"ranked before {RECENT}, measured since", col["recent"], ' stroke-dasharray="6 4"'),
		  ("cheapest first, measured since", col["mute"], ' stroke-dasharray="2 3"'))
	for i, (lab, c, dash) in enumerate(legend):
		lx, ly = L + (i % 2) * 300, H - 34 + (i // 2) * 20
		o.append(f'<line x1="{lx}" y1="{ly}" x2="{lx + 26}" y2="{ly}" stroke="{c}" stroke-width="2.5"{dash}/>')
		o.append(f'<text x="{lx + 32}" y="{ly + 4}" fill="{col["fg"]}">{html.escape(lab)}</text>')
	o.append("</svg>")
	return "\n".join(o)


def stack_table(tid: str, rows: list[dict]) -> str:
	esc = html.escape
	o = [f'<div class="scroll"><table class="sortable" id="{tid}"><thead><tr>'
	     '<th data-k="n" class="sorted asc">#</th><th data-k="s">suite</th><th data-k="s">subtest</th>'
	     '<th data-k="s">configuration</th><th data-k="n">mean s</th><th data-k="n">runs per 30 days</th>'
	     '<th data-k="n">adds</th><th data-k="n">bugs so far</th><th data-k="n">time so far</th>'
	     '<th data-k="n">caught</th><th data-k="n">only it</th><th data-k="n">bugs per 1,000 h</th>'
	     '</tr></thead><tbody>']
	top = max((x["adds"] for x in rows), default=0) or 1
	for x in rows:
		eff = x["per_1000h"] if x["per_1000h"] is not None else -1
		o.append(f'<tr data-m="{int(x["runs30"] >= 1)}"><td class="num" data-v="{x["rank"]}">{x["rank"]}</td>'
			 f'<td>{esc(x["suite"])}</td><td>{esc(x["subtest"])}</td><td>{esc(x["cls"])}</td>'
			 f'<td class="num" data-v="{x["secs"]:.1f}">{fmt_secs(x["secs"])}{label(x)}</td>'
			 f'<td class="num" data-v="{x["runs30"]:.1f}">{x["runs30"]:,.0f}</td>'
			 f'<td class="num" data-v="{x["adds"]:.4f}"><span class="bar" style="width:{50 * x["adds"] / top:.0f}px">'
			 f'</span>{x["adds"]:.2f}</td>'
			 f'<td class="num" data-v="{x["cum_bugs"]:.5f}">{x["cum_bugs"]:.1%}</td>'
			 f'<td class="num" data-v="{x["cum_time"]:.8f}">{pct(x["cum_time"])}</td>'
			 f'<td class="num" data-v="{x["caught"]:.4f}">{x["caught"]:.2f}</td>'
			 f'<td class="num" data-v="{x["only"]:.4f}">{x["only"]:.2f}</td>'
			 f'<td class="num" data-v="{eff:.3f}">{"" if eff < 0 else f"{eff:.1f}"}</td></tr>')
	o.append("</tbody></table></div>")
	return "\n".join(o)


def page(d: dict, chart: str) -> str:
	from explain import FONTS, STYLE, sort_script
	esc = html.escape
	f, r = d["full"], d["recent"]
	cmp_ = compare(d)
	a50, a500 = at(f, 50), at(f, 500)
	o = []
	w = o.append
	w("<title>Subtest Stack</title>")
	w('<meta name="claude:viewport-fit-sides" content="cover">')
	w(FONTS)
	w(STYLE)
	w("<style>#st td:nth-child(3),#rt td:nth-child(3),#cw td:nth-child(2){white-space:nowrap}</style>")
	w("<main>")
	w('<header class="prose"><h1>The subtest stack</h1>')
	w(f'<p class="lede">Every Lustre subtest in every configuration, ranked by the bugs not yet caught it catches '
	  f'per second of its own run time: {len(f["rows"]):,} of them. From the eval\'s bugs, {f["bugs"]:,} of '
	  f'{f["changes"]:,} changes from '
	  f'{esc(f["start"])} to {esc(f["end"])}, and Rainshadow\'s per-subtest timings (fetched {esc(d["fetched"][:10])}). '
	  'Generated by <code>eval/subtests.py</code>; every number is computed.</p></header>')
	w('<section class="big">')
	w(f'<div><b>{a50["bugs"]:.0%}</b><br><span>of the bugs: the top 50 subtests, {pct(a50["time"])} of the time'
	  '</span></div>')
	w(f'<div><b>{a500["bugs"]:.0%}</b><br><span>the top 500, {pct(a500["time"])} of the time</span></div>')
	w(f'<div><b>{d["held"][min(500, len(d["held"])) - 1][1]:.0%}</b><br><span>of later bugs: a top 500 ranked on the '
	  f'bugs before {RECENT}</span></div>')
	w(f'<div><b>{f["ceiling"]:.0%}</b><br><span>every subtest once, in every configuration</span></div>')
	w("</section>")
	w('<p class="small prose"><a href="how-it-works.html">How test selection works</a> ranks whole test sets; '
	  'this ranks the subtests inside them, without session setup: what each is worth for its own time.</p>')

	w('<section><h2>Bugs caught against time</h2>')
	w(f'<div class="chart">{chart}</div>')
	w(f'<p class="prose">{esc(held_text(d))}</p>')
	w(f'<p class="prose">{esc(ceiling_text(f))}</p>')
	w('<div class="scroll"><table><tr><th>top</th><th>bugs, full history</th><th>minutes</th><th>time</th>'
	  f'<th>CI hours today</th><th>bugs since {RECENT}</th><th>minutes</th><th>time</th><th>CI hours today</th></tr>')
	for n in (10, 50, 100, 200, 500, 1000, 2000):
		x, y = at(f, n), at(r, n)
		w(f'<tr><td class="num">{n}</td><td class="num">{x["bugs"]:.1%}</td><td class="num">{60 * x["hours"]:,.1f}</td>'
		  f'<td class="num">{pct(x["time"])}</td><td class="num">{pct(x["ci"])}</td><td class="num">{y["bugs"]:.1%}</td>'
		  f'<td class="num">{60 * y["hours"]:,.1f}</td><td class="num">{pct(y["time"])}</td>'
		  f'<td class="num">{pct(y["ci"])}</td></tr>')
	w('</table></div><p class="small prose"><i>Minutes</i>: the stack so far, each subtest run once. '
	  '<i>Time</i> is one run of each subtest so far against one run of every '
	  'subtest in every configuration; <i>CI hours today</i> weighs each by how often CI ran it in the last 30 '
	  'days.</p></section>')

	w('<section><h2>Configurations</h2><p class="prose">A subtest is ranked per configuration, the eval\'s classes '
	  '(<code>eval/classes.py</code>): what <code>Test-Parameters</code> can ask for. A test catches different bugs '
	  'on ZFS and ldiskfs, with one MDT and with four, and costs differently in each, so pooling them would hide '
	  'where a catch comes from and price it wrongly.</p>')
	w(f'<div class="scroll"><table><tr><th>configuration</th><th>share of catches, full history</th>'
	  f'<th>since {RECENT}</th></tr>')
	for c in sorted(cmp_["cls"]["full"], key=lambda c: -cmp_["cls"]["full"][c]):
		w(f'<tr><td>{esc(c)}</td><td class="num">{cmp_["cls"]["full"][c]:.1%}</td>'
		  f'<td class="num">{cmp_["cls"]["recent"].get(c, 0):.1%}</td></tr>')
	w("</table></div></section>")

	w(f'<section><h2>The stack, full history</h2><p class="small prose">The first {PAGE_ROWS:,} of '
	  f'{len(f["rows"]):,}. <b>Click a column to sort</b>; it opens in stack order. {esc(COLUMNS.strip())}</p>')
	w('<div class="filters"><label><input type="checkbox" id="st-today"> only subtests CI ran in the last 30 days'
	  '</label></div>')
	w(stack_table("st", f["rows"][:PAGE_ROWS]))
	w(sort_script("st", "st-today"))
	w("</section>")

	w(f'<section><h2>Since {RECENT}</h2><p class="small prose">The same stack over the bugs and timings since the '
	  f'review matrix changed. {cmp_["same50"]} of the full history\'s top 50 are in its top 50, '
	  f'{cmp_["same200"]} of the top 200.</p>')
	if cmp_["new"]:
		w('<p class="small prose">New near the top (recent top 50, not in the full history\'s top 200): ' + ", ".join(
			esc(f"{x['suite']} {x['subtest']} ({x['cls']})") for x in cmp_["new"]) + ".</p>")
	if cmp_["faded"]:
		w('<p class="small prose">Fallen away (full history\'s top 50, not in the recent top 200): ' + ", ".join(
			esc(f"{x['suite']} {x['subtest']} ({x['cls']})") for x in cmp_["faded"]) + ".</p>")
	w('<div class="filters"><label><input type="checkbox" id="rt-today"> only subtests CI ran in the last 30 days'
	  '</label></div>')
	w(stack_table("rt", r["rows"][:TOP]))
	w(sort_script("rt", "rt-today"))
	w("</section>")

	w('<section><h2>Costly, and catching nothing new</h2><p class="small prose">The subtests that took the most CI '
	  f'time from {esc(f["start"])} to {esc(f["end"])} and never caught a bug no other subtest caught too. '
	  '<i>At most</i> is the 95% upper bound on the rate of bugs only it would catch, per 1,000 hours of its '
	  'running: low means poor for its time with confidence, not just unlucky. <i>Caught</i> is the bugs it did '
	  'catch, all caught elsewhere as well.</p>')
	w('<div class="filters"><label><input type="checkbox" id="cw-none"> only those that never caught a bug'
	  '</label></div>')
	w('<div class="scroll"><table class="sortable" id="cw"><thead><tr><th data-k="s">suite</th>'
	  '<th data-k="s">subtest</th><th data-k="s">configuration</th><th data-k="n" class="sorted desc">hours</th>'
	  '<th data-k="n">hours per 30 days</th><th data-k="n">runs per 30 days</th><th data-k="n">mean s</th>'
	  '<th data-k="n">caught</th><th data-k="n">at most per 1,000 h</th></tr></thead><tbody>')
	for x in costly(f)[:PAGE_ROWS // 2]:
		h30 = x["secs"] * x["runs30"] / 3600
		w(f'<tr data-m="{int(x["caught"] == 0)}"><td>{esc(x["suite"])}</td><td>{esc(x["subtest"])}</td>'
		  f'<td>{esc(x["cls"])}</td><td class="num" data-v="{x["hours"]:.1f}">{x["hours"]:,.0f}</td>'
		  f'<td class="num" data-v="{h30:.2f}">{h30:,.0f}</td>'
		  f'<td class="num" data-v="{x["runs30"]:.1f}">{x["runs30"]:,.0f}</td>'
		  f'<td class="num" data-v="{x["secs"]:.1f}">{fmt_secs(x["secs"])}{label(x)}</td>'
		  f'<td class="num" data-v="{x["caught"]:.3f}">{x["caught"]:.1f}</td>'
		  f'<td class="num" data-v="{x["hi_only"]:.4f}">{x["hi_only"]:.2f}</td></tr>')
	w("</tbody></table></div>")
	w(sort_script("cw", "cw-none"))
	w("</section>")
	t = d["times"]
	w(f'<section class="prose small"><p>Rebuild: <code>eval/subtests.py --fetch</code>. The last run took '
	  f'{sum(t.values()):.0f} s ({t["timings"]:.1f} s for the timings, {t["bugs"]:.0f} s for the bugs, '
	  f'{t["stack"]:.0f} s for the stacks and documents).</p></section>')
	w("</main>")
	return "\n".join(o)


def main() -> None:
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("--db", default=str(ROOT / "data" / "failures.db"))
	ap.add_argument("--cases", help="read the bugs from this cases file (eval/cases.py) instead of the database")
	ap.add_argument("--fetch", action="store_true", help="fetch the timings from Rainshadow first")
	ap.add_argument("--json", help="also write both stacks here")
	args = ap.parse_args()
	t0 = time.time()
	d = build(args)
	t1 = time.time()
	(ROOT / "docs").mkdir(exist_ok=True)
	(ROOT / "docs" / "subtest-curve.svg").write_text(svg_curve(d, themed=False) + "\n")
	d["times"]["stack"] += time.time() - t1
	(ROOT / "SUBTEST_STACK.md").write_text(md(d) + "\n")
	(ROOT / "docs" / "subtest-stack.html").write_text(page(d, svg_curve(d, themed=True)) + "\n")
	if args.json:
		Path(args.json).write_text(json.dumps({k: d[k] for k in ("full", "recent", "fetched")}) + "\n")
	f = d["full"]
	print(f"SUBTEST_STACK.md, docs/: top 50 {at(f, 50)['bugs']:.1%} of bugs at {pct(at(f, 50)['time'])} of time, "
	      f"top 500 {at(f, 500)['bugs']:.1%} at {at(f, 500)['time']:.1%}; {time.time() - t0:.1f} s", file=sys.stderr)


if __name__ == "__main__":
	main()
