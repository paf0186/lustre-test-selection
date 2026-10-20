#!/usr/bin/env python3
"""Load the patch faults Rainshadow knows into SQLite.

  harvest.py fetch --days 180            # ask Rainshadow, month by month
  harvest.py fetch --start 2026-03-20 --end 2026-09-19 --fill
  harvest.py load faults.json            # a saved `rainshadow patch-faults` output
  harvest.py stats

Rainshadow does the work (`rainshadow patch-faults`, one pass over its evidence
per day); this keeps the answer in a database, with the uploader's address
replaced by a hash.  `--fill` has Rainshadow read from Gerrit what the patches
changed, where it does not have it yet.  Standard library only.

Login: RAINSHADOW_URL / RAINSHADOW_USER / RAINSHADOW_PASSWORD in the
environment or in ~/.config/rainshadow/cli.env (the Rainshadow CLI's file).
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
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
	return db


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
		db.execute("INSERT OR REPLACE INTO session VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
			   (s["id"], s["change"], s.get("patchset"), s.get("job"), s.get("build"),
			    s.get("test_group"), s.get("status"), s.get("enforcing"), s.get("client_distro"),
			    s.get("server_fs"), s.get("duration"), s.get("submission")))
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


def load(args, db) -> None:
	print(store(db, json.load(open(args.file))), file=sys.stderr)


def stats(args, db) -> None:
	q = lambda s: db.execute(s).fetchall()  # noqa: E731
	print("faults:", q("SELECT count(*) FROM failure")[0][0],
	      " patchsets:", q("SELECT count(*) FROM patchset")[0][0],
	      " changes:", q("SELECT count(DISTINCT change) FROM failure")[0][0],
	      " with patch contents:", q("SELECT count(*) FROM patchset WHERE files IS NOT NULL")[0][0],
	      " sessions:", q("SELECT count(*) FROM session")[0][0])
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
	sub.add_parser("stats")
	args = ap.parse_args()
	db = connect(args.db)
	{"fetch": fetch, "load": load, "stats": stats}[args.cmd](args, db)


if __name__ == "__main__":
	main()
