#!/usr/bin/env python3
"""Load the patch faults Rainshadow knows into SQLite.

  harvest.py fetch --days 180            # ask Rainshadow, month by month
  harvest.py fetch --start 2026-03-20 --end 2026-09-19 --fill
  harvest.py load faults.json            # a saved `rainshadow patch-faults` output
  harvest.py sessions                    # every session of the dataset's changes, with test sets
  harvest.py patches                     # each patchset's commit message and diff, from Gerrit
  harvest.py sample --start 2026-03-20 --end 2026-10-19 --fraction 0.12
                                         # a random sample of all review patchsets, faulty or not
  harvest.py stats

Rainshadow does the work (`rainshadow patch-faults`, one pass over its evidence
per day); this keeps the answer in a database, with the uploader's address
replaced by a hash.  `--fill` has Rainshadow read from Gerrit what the patches
changed, where it does not have it yet.  `sessions` adds what ran on each change
and how each suite went; `patches` reads the patch text from Gerrit itself, with
email addresses removed.  Standard library only.

Login: RAINSHADOW_URL / RAINSHADOW_USER / RAINSHADOW_PASSWORD in the
environment or in ~/.config/rainshadow/cli.env (the Rainshadow CLI's file).
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import concurrent.futures
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE / "data" / "failures.db"
USER_AGENT = "lustre-test-selection/0.2 (+https://github.com/paf0186/lustre-test-selection)"
CHUNK_DAYS = 31
GERRIT = "https://review.whamcloud.com"
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
SESSION_COLS = ("id", "change", "patchset", "job", "build", "test_group", "status", "enforcing",
		"client_distro", "server_fs", "duration", "submission", "test_name", "server_distro",
		"arch", "reason", "day", "clients", "mds_nodes", "mdts", "oss_nodes", "osts")


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

	def call(self, path: str, params: dict | None = None, body: dict | None = None) -> dict:
		qs = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
		req = urllib.request.Request(
			f"{self.base}/api/v1{path}" + (f"?{qs}" if qs else ""),
			data=None if body is None else json.dumps(body).encode(),
			headers={"User-Agent": USER_AGENT, "X-Requested-With": "rainshadow",
				 "Content-Type": "application/json"})
		if self.auth:
			req.add_header("Authorization", self.auth)
		for attempt in range(5):
			try:
				with urllib.request.urlopen(req, timeout=300) as r:
					return json.load(r)
			except urllib.error.HTTPError as e:
				if e.code in (400, 401, 403, 404):
					raise SystemExit(f"Rainshadow said {e.code} for {path}: {e.read()[:300]!r}")
				err = e
			except (urllib.error.URLError, ConnectionError) as e:
				err = e
			time.sleep(min(2 ** attempt, 30))
		raise SystemExit(f"Rainshadow did not answer {path}: {err}")


def who(email: str | None) -> str | None:
	"""A stable token for an uploader; the address itself stays out of the dataset."""
	return hashlib.sha256(email.lower().encode()).hexdigest()[:12] if email else None


def connect(path: Path) -> sqlite3.Connection:
	path.parent.mkdir(parents=True, exist_ok=True)
	db = sqlite3.connect(path, timeout=60)
	db.executescript((HERE / "schema.sql").read_text())
	have = {r[1] for r in db.execute("PRAGMA table_info(session)")}
	for col in SESSION_COLS:
		if col not in have:
			db.execute(f"ALTER TABLE session ADD COLUMN {col} "
				   f"{'INTEGER' if col in ('clients', 'mds_nodes', 'mdts', 'oss_nodes', 'osts') else 'TEXT'}")
	return db


def put_session(db: sqlite3.Connection, s: dict) -> None:
	"""Insert or update one session; fields the answer lacks keep what was there."""
	row = {k: s.get(k) for k in SESSION_COLS}
	for k in ("clients", "mds_nodes", "mdts", "oss_nodes", "osts"):
		row[k] = (s.get("nodes") or {}).get(k)
	cols = [k for k in SESSION_COLS if row[k] is not None or k in ("id", "change")]
	db.execute(f"INSERT INTO session ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
		   f"ON CONFLICT(id) DO UPDATE SET {','.join(f'{k}=excluded.{k}' for k in cols if k != 'id')}",
		   [row[k] for k in cols])


def store(db: sqlite3.Connection, d: dict) -> dict:
	"""Add one `patch-faults` answer (faults, patchsets, sessions) to the database."""
	uploader = {(f["change"], f["patchset"]): who(f.get("pusher")) for f in d["faults"]}
	for p in d.get("patchsets", []):
		files = p.get("files")
		db.execute("INSERT OR REPLACE INTO patchset VALUES (?,?,?,?,?,?,?,?,?,?,?)",
			   (p["change"], p["patchset"], uploader.get((p["change"], p["patchset"])),
			    json.dumps(files) if files is not None else None,
			    len(files) if files is not None else None,
			    json.dumps(p["scripts"]) if p.get("scripts") else None, p.get("test_parameters"),
			    p.get("kind"), p.get("series"), p.get("series_size"), p.get("subject")))
	for s in d.get("sessions", []):
		put_session(db, s)
	for f in d["faults"]:
		el = f.get("elsewhere") or {}
		db.execute("INSERT OR REPLACE INTO failure VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
			   (f["id"], f["change"], f["patchset"], f["suite"], f["subtest"], f.get("status"),
			    f.get("error"), f.get("sig"), f.get("day"), f.get("job"), f.get("test_group"),
			    f.get("distro"), f.get("fs"), f.get("session_id"), f.get("test_set_id"),
			    f.get("enforcing"), f["kind"], f.get("class"), f.get("count"),
			    el.get("other_changes"), el.get("series_changes"), f.get("retest_status"),
			    json.dumps(f["patch_changes"]) if f.get("patch_changes") else None))
	db.commit()
	return {"faults": len(d["faults"]), "patchsets": len(d.get("patchsets", [])),
		"sessions": len(d.get("sessions", []))}


def fetch(args, db) -> None:
	cl = Client()
	end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
	start = dt.date.fromisoformat(args.start) if args.start else end - dt.timedelta(days=args.days - 1)
	if args.fill:
		stalled = 0
		while True:
			r = cl.call("/sync/patches", body={"start": start.isoformat(), "end": end.isoformat(),
							    "weak": args.weak or None, "limit": 300, "seconds": 90})
			print(f"  patches unread: {r['missing_after']} of {r['patchsets']}", file=sys.stderr)
			stalled = stalled + 1 if r["missing_after"] >= r["missing_before"] else 0
			if r["missing_after"] == 0 or stalled >= 3:
				break
	day = start
	while day <= end:
		stop = min(day + dt.timedelta(days=CHUNK_DAYS - 1), end)
		d = cl.call("/export/patch-faults", {"start": day.isoformat(), "end": stop.isoformat(),
						      "sessions": 1, "weak": 1 if args.weak else None})
		print(f"  {day}..{stop}: {store(db, d)}", file=sys.stderr)
		day = stop + dt.timedelta(days=1)


def sessions(args, db) -> None:
	"""Every session of the dataset's changes, with its node layout and test sets."""
	cl = Client()
	changes = args.change or [c for (c,) in db.execute(
		"SELECT DISTINCT p.change FROM patchset p LEFT JOIN change_harvest h ON h.change = p.change "
		"WHERE h.sessions_at IS NULL OR ? ORDER BY 1", (args.all,))]
	now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
	n_sess = n_sets = 0
	for i in range(0, len(changes), 20):
		chunk = changes[i:i + 20]
		after = 0
		while after is not None:
			d = cl.call("/export/sessions", {"change": ",".join(map(str, chunk)),
							  "after": after, "limit": 2000})
			for s in d["rows"]:
				put_session(db, s)
				db.execute("DELETE FROM test_set WHERE session_id = ?", (s["id"],))
				for t in s["test_sets"]:
					db.execute("INSERT OR REPLACE INTO test_set VALUES (?,?,?,?,?,?,?,?,?)",
						   (t["id"], s["id"], t["suite"], t["status"], t["duration"],
						    t["sub_total"], t["sub_passed"], t["sub_failed"], t["sub_skipped"]))
					n_sets += 1
			n_sess += len(d["rows"])
			after = d["next_after"]
		db.executemany("INSERT OR REPLACE INTO change_harvest VALUES (?,?)", [(c, now) for c in chunk])
		db.commit()
		print(f"  changes {i + len(chunk)}/{len(changes)}: {n_sess} sessions, {n_sets} test sets",
		      file=sys.stderr)


def split_patch(text: str) -> tuple[str, str]:
	"""(commit message, diff) of a `git format-patch` text, email addresses removed."""
	text = EMAIL.sub("<email>", text)
	head, sep, diff = text.partition("\ndiff --git ")
	lines = head.splitlines()
	start = next((i for i, x in enumerate(lines) if x.startswith("Subject:")), 0)
	msg = lines[start:]
	if msg and msg[0].startswith("Subject:"):
		msg[0] = re.sub(r"^Subject:\s*(\[PATCH[^]]*\]\s*)?", "", msg[0])
	end = next((i for i, x in enumerate(msg) if x == "---"), len(msg))
	return "\n".join(msg[:end]).strip() + "\n", ("diff --git " + diff) if sep else ""


def gerrit_patch(change: int, patchset: int) -> str:
	req = urllib.request.Request(f"{GERRIT}/changes/{change}/revisions/{patchset}/patch",
				     headers={"User-Agent": USER_AGENT})
	for attempt in range(4):
		try:
			with urllib.request.urlopen(req, timeout=60) as r:
				return base64.b64decode(r.read()).decode("utf-8", "replace")
		except urllib.error.HTTPError as e:
			if e.code == 404:
				raise
			err = e
		except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
			err = e
		time.sleep(2 ** attempt)
	raise err


def patches(args, db) -> None:
	"""Each patchset's commit message and diff, from Gerrit, where not read yet."""
	todo = db.execute("SELECT p.change, p.patchset FROM patchset p LEFT JOIN patch_text t "
			  "ON t.change = p.change AND t.patchset = p.patchset "
			  "WHERE t.fetched_at IS NULL OR (t.error IS NOT NULL AND ?) ORDER BY 1, 2",
			  (args.retry,)).fetchall()
	now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

	def one(key):
		try:
			return key, split_patch(gerrit_patch(*key)), None
		except Exception as e:  # noqa: BLE001
			return key, ("", ""), f"{type(e).__name__}: {e}"[:300]

	done = errors = 0
	with concurrent.futures.ThreadPoolExecutor(args.workers) as ex:
		for (c, p), (msg, diff), err in ex.map(one, todo):
			db.execute("INSERT OR REPLACE INTO patch_text VALUES (?,?,?,?,?,?)",
				   (c, p, msg or None, diff or None, now, err))
			done += 1
			errors += err is not None
			if done % 100 == 0 or done == len(todo):
				db.commit()
				print(f"  patches {done}/{len(todo)}, {errors} failed", file=sys.stderr)
	db.commit()


def rank(change: int, patchset: int, seed: str) -> float:
	"""A key's place in [0, 1), the same on every run with the same seed."""
	h = hashlib.sha256(f"{seed}:{change}/{patchset}".encode()).digest()
	return int.from_bytes(h[:8], "big") / 2 ** 64


def sample(args, db) -> None:
	"""Every build of the range, then the patchsets ranked below the fraction: their
	contents (Rainshadow reads them from Gerrit), their sessions and patch text."""
	cl = Client()
	cur: list | None = ["", 0]
	n = 0
	while cur is not None:
		d = cl.call("/export/builds", {"start": args.start, "end": args.end, "job": args.job,
					      "after_job": cur[0], "after_build": cur[1], "limit": 2000})
		for b in d["rows"]:
			db.execute("INSERT OR REPLACE INTO build VALUES (?,?,?,?,?,?,?,?,?)",
				   (b["job"], b["build"], b["change"], b["patchset"], b["branch"], b["first_day"],
				    b["sessions"], b["failed"], b["duration"]))
		n += len(d["rows"])
		cur = d["next_after"]
	keys = db.execute("SELECT DISTINCT change, patchset FROM build WHERE job = ? AND change IS NOT NULL "
			  "AND patchset IS NOT NULL AND first_day BETWEEN ? AND ?",
			  (args.job, args.start, args.end)).fetchall()
	db.executemany("INSERT OR IGNORE INTO sample VALUES (?,?,?)",
		       [(c, p, rank(c, p, args.seed)) for c, p in keys])
	db.commit()
	drawn = db.execute("SELECT change, patchset FROM sample WHERE rank < ? ORDER BY 1, 2",
			   (args.fraction,)).fetchall()
	print(f"  {n} builds, {len(keys)} patchsets, {len(drawn)} drawn", file=sys.stderr)
	for i in range(0, len(drawn), 500):
		chunk = [list(k) for k in drawn[i:i + 500]]
		stalled = 0
		while True:
			r = cl.call("/sync/patches", body={"keys": chunk, "limit": 300, "seconds": 70})
			stalled = stalled + 1 if r["missing_after"] >= r["missing_before"] else 0
			if r["missing_after"] == 0 or stalled >= 3:
				break
		print(f"  patch contents {i + len(chunk)}/{len(drawn)}: {r['missing_after']} unread",
		      file=sys.stderr)
	for i in range(0, len(drawn), 300):
		chunk = drawn[i:i + 300]
		d = cl.call("/export/patchsets", body={"keys": [list(k) for k in chunk], "limit": 2000})
		for p in d["rows"]:
			files = p.get("files")
			db.execute("INSERT INTO patchset (change, patchset, files, n_files, scripts, test_parameters, "
				   "gerrit_kind, series, series_size, subject) VALUES (?,?,?,?,?,?,?,?,?,?) "
				   "ON CONFLICT(change, patchset) DO NOTHING",
				   (p["change"], p["patchset"], json.dumps(files) if files is not None else None,
				    len(files) if files is not None else None,
				    json.dumps(p["scripts"]) if p.get("scripts") else None, p.get("test_parameters"),
				    p.get("kind"), p.get("series"), p.get("series_size"), p.get("subject")))
	db.commit()
	if not args.no_sessions:
		sessions(argparse.Namespace(change=None, all=False), db)
	if not args.no_patches:
		patches(argparse.Namespace(workers=4, retry=False), db)


def load(args, db) -> None:
	print(store(db, json.load(open(args.file))), file=sys.stderr)


def stats(args, db) -> None:
	q = lambda s: db.execute(s).fetchall()  # noqa: E731
	print("faults:", q("SELECT count(*) FROM failure")[0][0],
	      " patchsets:", q("SELECT count(*) FROM patchset")[0][0],
	      " changes:", q("SELECT count(DISTINCT change) FROM failure")[0][0],
	      " with patch contents:", q("SELECT count(*) FROM patchset WHERE files IS NOT NULL")[0][0],
	      " sessions:", q("SELECT count(*) FROM session")[0][0],
	      " test sets:", q("SELECT count(*) FROM test_set")[0][0],
	      " patch texts:", q("SELECT count(*) FROM patch_text WHERE error IS NULL")[0][0],
	      " builds:", q("SELECT count(*) FROM build")[0][0],
	      " sampled:", q("SELECT count(*) FROM sample WHERE rank < 0.12")[0][0])
	for v, n in q("SELECT kind, count(*) FROM failure GROUP BY 1 ORDER BY 2 DESC"):
		print(f"  {n:7d}  {v}")


def main() -> None:
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("--db", type=Path, default=DEFAULT_DB)
	sub = ap.add_subparsers(dest="cmd", required=True)
	f = sub.add_parser("fetch", help="ask Rainshadow for the patch faults of a range")
	f.add_argument("--days", type=int, default=30)
	f.add_argument("--start")
	f.add_argument("--end")
	f.add_argument("--weak", action="store_true", help="also failures seen once, here only")
	f.add_argument("--fill", action="store_true", help="have Rainshadow read missing patch contents from Gerrit")
	ld = sub.add_parser("load", help="load a saved `rainshadow patch-faults` answer")
	ld.add_argument("file")
	se = sub.add_parser("sessions", help="every session of the dataset's changes, with test sets")
	se.add_argument("--change", type=int, action="append", help="only this change (repeatable)")
	se.add_argument("--all", action="store_true", help="re-read changes already read")
	pa = sub.add_parser("patches", help="each patchset's commit message and diff, from Gerrit")
	pa.add_argument("--workers", type=int, default=4)
	pa.add_argument("--retry", action="store_true", help="retry the ones Gerrit failed")
	sm = sub.add_parser("sample", help="a random sample of all review patchsets, faulty or not")
	sm.add_argument("--start", required=True)
	sm.add_argument("--end", required=True)
	sm.add_argument("--job", default="lustre-reviews")
	sm.add_argument("--fraction", type=float, default=0.12, help="share of patchsets drawn")
	sm.add_argument("--seed", default="lustre-test-selection")
	sm.add_argument("--no-sessions", action="store_true")
	sm.add_argument("--no-patches", action="store_true")
	sub.add_parser("stats")
	args = ap.parse_args()
	db = connect(args.db)
	{"fetch": fetch, "load": load, "sessions": sessions, "patches": patches, "sample": sample,
	 "stats": stats}[args.cmd](args, db)


if __name__ == "__main__":
	main()
