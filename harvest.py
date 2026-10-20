#!/usr/bin/env python3
"""Harvest CI failures seen on Gerrit patchsets from Rainshadow into SQLite.

  harvest.py discover --days 30     # which patchsets had failures
  harvest.py triage                 # fetch each patchset's patch and verdicts
  harvest.py stats

Stage 1 asks Rainshadow for every failure of every test that failed in the
window and keeps those made on a Gerrit change.  Stage 2 asks for the triage of
each (change, patchset): what the patch changes, each session, and Rainshadow's
verdict on every failure.  Both stages record what they finished in `harvest`,
so an interrupted run resumes.  Standard library only.

Login: RAINSHADOW_URL / RAINSHADOW_USER / RAINSHADOW_PASSWORD in the
environment or in ~/.config/rainshadow/cli.env (the Rainshadow CLI's file).
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import os
import random
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE / "data" / "failures.db"
PAGE = 500
USER_AGENT = "lustre-test-selection/0.1 (+https://github.com/paf0186/lustre-test-selection)"


def load_env() -> dict[str, str]:
	env = {}
	f = Path(os.environ.get("RAINSHADOW_ENV_FILE", "~/.config/rainshadow/cli.env")).expanduser()
	if f.exists():
		for line in f.read_text().splitlines():
			line = line.strip()
			if line and not line.startswith("#") and "=" in line:
				k, v = line.split("=", 1)
				env[k.strip()] = v.strip().strip("'\"")
	for k in ("RAINSHADOW_URL", "RAINSHADOW_USER", "RAINSHADOW_PASSWORD"):
		if os.environ.get(k):
			env[k] = os.environ[k]
	return env


class Client:
	def __init__(self) -> None:
		env = load_env()
		self.base = env.get("RAINSHADOW_URL", "http://localhost:8116").rstrip("/")
		self.auth = None
		if env.get("RAINSHADOW_USER"):
			tok = f"{env['RAINSHADOW_USER']}:{env.get('RAINSHADOW_PASSWORD', '')}"
			self.auth = "Basic " + base64.b64encode(tok.encode()).decode()

	def get(self, path: str, **params) -> dict:
		qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
		req = urllib.request.Request(f"{self.base}/api/v1{path}" + (f"?{qs}" if qs else ""))
		req.add_header("User-Agent", USER_AGENT)    # Cloudflare refuses urllib's own
		req.add_header("X-Requested-With", "rainshadow")
		if self.auth:
			req.add_header("Authorization", self.auth)
		for attempt in range(5):
			try:
				with urllib.request.urlopen(req, timeout=180) as r:
					return json.load(r)
			except urllib.error.HTTPError as e:
				if e.code in (401, 403, 404):
					raise
				err = e
			except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
				err = e
			time.sleep(2 ** attempt)
		raise err


def who(email: str | None) -> str | None:
	"""A stable token for an uploader; the address itself stays out of the dataset."""
	return hashlib.sha256(email.lower().encode()).hexdigest()[:12] if email else None


def connect(path: Path) -> sqlite3.Connection:
	path.parent.mkdir(parents=True, exist_ok=True)
	db = sqlite3.connect(path, timeout=60)
	db.executescript((HERE / "schema.sql").read_text())
	return db


def is_done(db, key: str) -> bool:
	return db.execute("SELECT 1 FROM harvest WHERE key = ?", (key,)).fetchone() is not None


def mark_done(db, key: str, detail: str = "") -> None:
	db.execute("INSERT OR REPLACE INTO harvest VALUES (?, ?, ?)",
		   (key, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), detail))


# -- stage 1 -----------------------------------------------------------------

def test_failures(cl: Client, suite: str, subtest: str, start: str, end: str) -> list[dict]:
	"""Every failure of one test in the range, paged."""
	out, offset = [], 0
	while True:
		d = cl.get(f"/triage/tests/{urllib.parse.quote(suite, safe='')}/{urllib.parse.quote(subtest, safe='')}",
			   start=start, end=end, limit=PAGE, offset=offset)
		out += d["recent"]
		offset = d.get("next_offset")
		if not offset:
			return out


def failure_row(suite: str, subtest: str, r: dict) -> tuple:
	return (r["id"], r["change"], r["patchset"], suite, subtest, r.get("status"), r.get("error"),
		r.get("day"), r.get("job"), r.get("branch"), r.get("test_group"), r.get("distro"),
		r.get("fs"), r.get("session_id"), r.get("test_set_id"),
		json.dumps(sorted({t["ticket"] for t in r.get("tickets") or []})))


def discover(args, db) -> None:
	cl = Client()
	end = args.end or dt.date.today().isoformat()
	start = args.start or (dt.date.fromisoformat(end) - dt.timedelta(days=args.days)).isoformat()
	tests = [t for t in cl.get("/tractability", start=start, end=end)["tests"] if t.get("kind", "test") == "test"]
	if args.limit_tests:
		tests = tests[:args.limit_tests]
	print(f"{len(tests)} tests failed {start}..{end}", file=sys.stderr)
	todo = [t for t in tests if not is_done(db, f"test:{t['suite']}/{t['subtest']}@{start}..{end}")]

	def one(t):
		return t, test_failures(cl, t["suite"], t["subtest"], start, end)

	n = 0
	with cf.ThreadPoolExecutor(args.workers) as ex:
		for i, fut in enumerate(cf.as_completed([ex.submit(one, t) for t in todo]), 1):
			t, rows = fut.result()
			rows = [r for r in rows if r.get("change") and r.get("patchset") and r.get("source") == "maloo"]
			db.executemany(
				"INSERT OR IGNORE INTO failure(id, change, patchset, suite, subtest, status, error, day, "
				"job, branch, test_group, distro, fs, session_id, test_set_id, tickets) "
				"VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
				[failure_row(t["suite"], t["subtest"], r) for r in rows])
			mark_done(db, f"test:{t['suite']}/{t['subtest']}@{start}..{end}", str(len(rows)))
			db.commit()
			n += len(rows)
			if i % 100 == 0:
				print(f"  {i}/{len(todo)} tests, {n} failures", file=sys.stderr)
	print(f"discovered {n} new failure rows", file=sys.stderr)


# -- stage 2 -----------------------------------------------------------------

def apply_triage(db, change: int, patchset: int, d: dict) -> int:
	failed = d.get("failed") or []
	summ = d.get("summary") or {}
	p = d.get("patch") or {}
	files = p.get("files")
	db.execute(
		"INSERT OR REPLACE INTO patchset VALUES (?,?,?,?,?,?,?,?,?,?)",
		(change, patchset, who(d.get("uploader")),
		 json.dumps(files) if files is not None else None, len(files) if files is not None else None,
		 json.dumps(p["scripts"]) if p.get("scripts") else None, p.get("test_parameters"),
		 summ.get("sessions"), summ.get("passed"),
		 dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")))
	n = 0
	for s in failed:
		rt, ro = s.get("retest") or {}, s.get("retest_of") or {}
		db.execute("INSERT OR REPLACE INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
			   (s["id"], change, patchset, s.get("trigger_job"), s.get("trigger_build"),
			    s.get("test_group"), s.get("status"), s.get("enforcing"), s.get("client_distro"),
			    s.get("server_fs"), s.get("duration"), s.get("submission"), ro.get("id"), rt.get("id"),
			    rt.get("status")))
		for f in s.get("failures") or []:
			el = f.get("elsewhere") or {}
			tested = f.get("tested") or {}
			vals = (f["verdict"], f.get("why"), f.get("class"),
				json.dumps(f["patch_changes"]) if f.get("patch_changes") else None,
				el.get("branch_fails"), el.get("other_uploaders"), el.get("other_changes"),
				el.get("series_changes"), s.get("enforcing"))
			cur = db.execute(
				"UPDATE failure SET verdict=?, why=?, class=?, patch_changes=?, branch_fails=?, "
				"other_uploaders=?, other_changes=?, series_changes=?, enforcing=? WHERE id = ?",
				vals + (f["id"],))
			if not cur.rowcount:       # a failure stage 1 did not list
				db.execute(
					"INSERT INTO failure(id, change, patchset, suite, subtest, status, error, sig, day, "
					"job, branch, test_group, distro, fs, session_id, test_set_id, tickets, verdict, why, "
					"class, patch_changes, branch_fails, other_uploaders, other_changes, series_changes, "
					"enforcing) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
					(f["id"], change, patchset, f["suite"], f["subtest"], f.get("status"), f.get("error"),
					 f.get("sig"), s.get("search_date"), f.get("job"), tested.get("branch"),
					 s.get("test_group"), s.get("client_distro"), s.get("server_fs"), s["id"],
					 f.get("test_set_id"),
					 json.dumps(sorted({t["ticket"] for t in f.get("tickets") or []})),) + vals)
			else:
				db.execute("UPDATE failure SET sig = COALESCE(sig, ?) WHERE id = ?", (f.get("sig"), f["id"]))
			n += 1
	return n


def triage(args, db) -> None:
	cl = Client()
	todo = [(c, p) for c, p in db.execute("SELECT DISTINCT change, patchset FROM failure ORDER BY change, patchset")
		if not is_done(db, f"ps:{c}/{p}")]
	if args.sample and args.sample < len(todo):
		todo = sorted(random.Random(args.seed).sample(todo, args.sample))
	print(f"{len(todo)} patchsets to triage", file=sys.stderr)

	def one(cp):
		c, p = cp
		try:
			return cp, cl.get(f"/triage/changes/{c}", patchset=p), None
		except urllib.error.HTTPError as e:
			return cp, None, f"HTTP {e.code}"

	n = 0
	with cf.ThreadPoolExecutor(args.workers) as ex:
		for i, fut in enumerate(cf.as_completed([ex.submit(one, cp) for cp in todo]), 1):
			(c, p), d, err = fut.result()
			if d is not None:
				n += apply_triage(db, c, p, d)
			mark_done(db, f"ps:{c}/{p}", err or "ok")
			db.commit()
			if i % 50 == 0:
				print(f"  {i}/{len(todo)} patchsets", file=sys.stderr)
	print(f"triaged {len(todo)} patchsets, {n} failures", file=sys.stderr)


def stats(args, db) -> None:
	q = lambda s: db.execute(s).fetchall()  # noqa: E731
	print("failures:", q("SELECT count(*) FROM failure")[0][0],
	      " patchsets:", q("SELECT count(*) FROM (SELECT DISTINCT change, patchset FROM failure)")[0][0],
	      " changes:", q("SELECT count(DISTINCT change) FROM failure")[0][0],
	      " triaged patchsets:", q("SELECT count(*) FROM patchset")[0][0])
	print("by verdict:")
	for v, n in q("SELECT coalesce(verdict, '(not triaged)'), count(*) FROM failure GROUP BY 1 ORDER BY 2 DESC"):
		print(f"  {n:7d}  {v}")


def main() -> None:
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("--db", type=Path, default=DEFAULT_DB)
	ap.add_argument("--workers", type=int, default=4)
	sub = ap.add_subparsers(dest="cmd", required=True)
	d = sub.add_parser("discover", help="stage 1: failures on patchsets, test by test")
	d.add_argument("--days", type=int, default=30)
	d.add_argument("--start")
	d.add_argument("--end")
	d.add_argument("--limit-tests", type=int, default=0)
	t = sub.add_parser("triage", help="stage 2: each patchset's patch and verdicts")
	t.add_argument("--sample", type=int, default=0, help="triage only this many patchsets, chosen at random")
	t.add_argument("--seed", type=int, default=1)
	sub.add_parser("stats")
	args = ap.parse_args()
	db = connect(args.db)
	{"discover": discover, "triage": triage, "stats": stats}[args.cmd](args, db)


if __name__ == "__main__":
	main()
