import json
import sqlite3
from pathlib import Path

import harvest


def db(tmp_path):
	return harvest.connect(tmp_path / "t.db")


TRIAGE = {
	"uploader": "Someone@Example.com",
	"patch": {"files": ["lustre/llite/file.c", "lustre/tests/sanity.sh"],
		  "scripts": {"sanity": {"tests": ["test_42a"], "helpers": []}},
		  "test_parameters": "testlist=sanity"},
	"summary": {"sessions": 10, "passed": 9},
	"failed": [{
		"id": "s1", "trigger_job": "lustre-reviews", "trigger_build": 7, "test_group": "review-ldiskfs",
		"status": "FAIL", "enforcing": 1, "client_distro": "RHEL 9.7", "server_fs": "ldiskfs",
		"duration": 100, "submission": "2026-10-01T00:00:00Z", "retest_of": None,
		"retest": {"id": "s2", "status": "PASS"},
		"failures": [{"id": "f1", "suite": "sanity", "subtest": "test_42a", "status": "FAIL",
			      "error": "boom", "sig": "boom", "test_set_id": "ts1", "job": "lustre-reviews",
			      "verdict": "patch-changes-test", "why": "w", "class": "patch",
			      "patch_changes": {"test": True}, "tickets": [],
			      "elsewhere": {"branch_fails": 0, "other_uploaders": 0, "other_changes": 0,
					    "series_changes": 0},
			      "tested": {"branch": "master"}}]}],
}


def test_triage_fills_and_adds_rows(tmp_path):
	d = db(tmp_path)
	d.execute("INSERT INTO failure(id, change, patchset, suite, subtest) VALUES ('f1', 5, 2, 'sanity', 'test_42a')")
	assert harvest.apply_triage(d, 5, 2, TRIAGE) == 1
	row = d.execute("SELECT verdict, class, enforcing, patch_changes FROM failure WHERE id = 'f1'").fetchone()
	assert row[:3] == ("patch-changes-test", "patch", 1)
	assert json.loads(row[3]) == {"test": True}
	ps = d.execute("SELECT uploader, n_files, sessions FROM patchset").fetchone()
	assert ps[1:] == (2, 10) and "example" not in ps[0] and len(ps[0]) == 12
	assert d.execute("SELECT retest_id, retest_status FROM session").fetchone() == ("s2", "PASS")
	assert d.execute("SELECT count(*) FROM patch_caused").fetchone()[0] == 1


def test_triage_inserts_failure_discovery_missed(tmp_path):
	d = db(tmp_path)
	harvest.apply_triage(d, 5, 2, TRIAGE)
	assert d.execute("SELECT suite, subtest, branch FROM failure").fetchone() == ("sanity", "test_42a", "master")
