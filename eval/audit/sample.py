#!/usr/bin/env python3
"""Draw the label audit's stratified sample of patch faults (seeded, repeatable)."""
import json, random, sqlite3, sys

STRATA = [
	# name, count, WHERE over failure f (and ps: per-patchset flags)
	("changes-test", 10, "f.kind = 'patch-changes-test'"),
	("suspect-solo", 15, "f.kind = 'patch-suspect' AND f.series_changes = 0 AND COALESCE(f.retest_status,'') != 'PASS'"),
	("suspect-series", 10, "f.kind = 'patch-suspect' AND f.series_changes > 0"),
	("suspect-retest-pass", 10, "f.kind = 'patch-suspect' AND f.retest_status = 'PASS'"),
	("suspect-generic", 5, "f.kind = 'patch-suspect' AND f.status IN ('CRASH', 'TIMEOUT')"),
]

def main(db="data/failures.db", seed=20261020):
	conn = sqlite3.connect(db)
	conn.row_factory = sqlite3.Row
	rng = random.Random(seed)
	seen = set()
	out = []
	for name, n, where in STRATA:
		rows = conn.execute(f"""SELECT f.* FROM failure f
			WHERE f.class != 'ambient' AND {where} ORDER BY f.id""").fetchall()
		rng.shuffle(rows)
		for r in rows:
			ps = (r["change"], r["patchset"])
			if ps in seen:
				continue
			seen.add(ps)
			units = conn.execute("""SELECT suite, subtest, sig, COUNT(*) n
				FROM failure WHERE change = ? AND patchset = ? AND class != 'ambient'
				GROUP BY suite, subtest, sig ORDER BY suite, subtest""", ps).fetchall()
			out.append({"stratum": name, "fault": dict(r),
				"patchset_units": [dict(u) for u in units]})
			if sum(o["stratum"] == name for o in out) >= n:
				break
	for o in out:
		print(json.dumps(o))

if __name__ == "__main__":
	main(*sys.argv[1:])
