import harvest

ANSWER = {
	"faults": [{"id": "f1", "change": 5, "patchset": 2, "suite": "sanity", "subtest": "test_42a",
		    "status": "FAIL", "error": "boom", "sig": "boom", "day": "2026-10-01", "job": "lustre-reviews",
		    "test_group": "review-ldiskfs", "distro": "RHEL 9.7", "fs": "ldiskfs", "session_id": "s1",
		    "test_set_id": "ts1", "enforcing": 1, "pusher": "Someone@Example.com",
		    "kind": "patch-changes-test", "class": "patch", "count": 3, "retest_status": "FAIL",
		    "elsewhere": {"branch_fails": 0, "other_uploaders": 0, "other_changes": 1, "series_changes": 1},
		    "patch_changes": {"test": True}}],
	"patchsets": [{"change": 5, "patchset": 2, "files": ["lustre/llite/file.c", "lustre/tests/sanity.sh"],
		       "scripts": {"sanity": {"tests": ["test_42a"], "helpers": []}},
		       "test_parameters": "testlist=sanity", "kind": "REWORK", "series": 5, "series_size": 1,
		       "subject": "LU-1 llite: x"}],
	"sessions": [{"id": "s1", "change": 5, "patchset": 2, "job": "lustre-reviews", "build": 7,
		      "test_group": "review-ldiskfs", "status": "FAIL", "enforcing": 1,
		      "client_distro": "RHEL 9.7", "server_fs": "ldiskfs", "duration": 100,
		      "submission": "2026-10-01T00:00:00Z"},
		     {"id": "s2", "change": 5, "patchset": 2, "job": "lustre-reviews", "build": 7,
		      "test_group": "review-zfs", "status": "PASS", "enforcing": 1,
		      "client_distro": "RHEL 9.7", "server_fs": "zfs", "duration": 100,
		      "submission": "2026-10-01T00:00:00Z"}],
}


def test_store_keeps_faults_patch_and_all_sessions(tmp_path):
	d = harvest.connect(tmp_path / "t.db")
	assert harvest.store(d, ANSWER) == {"faults": 1, "patchsets": 1, "sessions": 2}
	ps = d.execute("SELECT uploader, n_files, gerrit_kind FROM patchset").fetchone()
	assert ps[1:] == (2, "REWORK") and len(ps[0]) == 12 and "example" not in ps[0]
	assert d.execute("SELECT sessions_failed, patchsets_failed FROM fault_evidence").fetchone() == (1, 1)
	assert dict((g, c) for g, s, fs, c in d.execute("SELECT * FROM group_catches")) \
		== {"review-ldiskfs": 1, "review-zfs": 0}


def test_store_is_repeatable(tmp_path):
	d = harvest.connect(tmp_path / "t.db")
	harvest.store(d, ANSWER)
	harvest.store(d, ANSWER)
	assert d.execute("SELECT count(*) FROM failure").fetchone()[0] == 1
