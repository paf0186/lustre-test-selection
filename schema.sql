-- lustre-test-selection: Lustre CI failures that were seen on Gerrit patchsets.
--
-- One row per failed test (a "failure"), one per patchset it was seen on, and
-- one per test session.  The triage columns are Rainshadow's own judgement of
-- each failure (see README.md).

CREATE TABLE IF NOT EXISTS patchset (
	change         INTEGER NOT NULL,
	patchset       INTEGER NOT NULL,
	uploader       TEXT,       -- hash of the uploader's address, not the address
	files          TEXT,       -- JSON list of the files the patchset changes
	n_files        INTEGER,
	scripts        TEXT,       -- JSON {script: {tests: [...], helpers: [...]}}: test functions touched
	test_parameters TEXT,      -- the commit message's Test-Parameters lines
	sessions       INTEGER,    -- test sessions Maloo ran for it
	sessions_passed INTEGER,
	triaged_at     TEXT,
	PRIMARY KEY (change, patchset)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS session (
	id             TEXT PRIMARY KEY,
	change         INTEGER NOT NULL,
	patchset       INTEGER NOT NULL,
	job            TEXT,
	build          INTEGER,
	test_group     TEXT,       -- review-ldiskfs, review-dne-zfs-part-4, custom ...
	status         TEXT,
	enforcing      INTEGER,    -- 1 if a failure in it blocks landing
	client_distro  TEXT,
	server_fs      TEXT,
	duration       INTEGER,
	submission     TEXT,
	retest_of      TEXT,       -- session this one re-ran
	retest_id      TEXT,       -- the session that re-ran this one
	retest_status  TEXT        -- PASS: the failure did not repeat
);
CREATE INDEX IF NOT EXISTS session_ps ON session(change, patchset);

CREATE TABLE IF NOT EXISTS failure (
	id             TEXT PRIMARY KEY,   -- Maloo's id of the failed subtest
	change         INTEGER NOT NULL,
	patchset       INTEGER NOT NULL,
	suite          TEXT NOT NULL,
	subtest        TEXT NOT NULL,
	status         TEXT,               -- FAIL, TIMEOUT, CRASH ...
	error          TEXT,
	sig            TEXT,               -- the error with ids and numbers masked
	day            TEXT,
	job            TEXT,
	branch         TEXT,               -- code branch under test
	test_group     TEXT,
	distro         TEXT,
	fs             TEXT,
	session_id     TEXT,
	test_set_id    TEXT,
	enforcing      INTEGER,
	tickets        TEXT,               -- JSON list of linked ticket keys
	-- Rainshadow's triage, once the patchset has been triaged:
	verdict        TEXT,               -- see README.md
	why            TEXT,
	class          TEXT,               -- patch, ambient, infra ...
	patch_changes  TEXT,               -- JSON: patch touches this test / its helpers
	branch_fails   INTEGER,            -- failures of this test on branch runs, 30 days
	other_uploaders INTEGER,           -- other uploaders it failed for, 30 days
	other_changes  INTEGER,            -- other changes it failed on, 30 days
	series_changes INTEGER             -- other changes of this change's series it failed on
);
CREATE INDEX IF NOT EXISTS failure_ps ON failure(change, patchset);
CREATE INDEX IF NOT EXISTS failure_test ON failure(suite, subtest);

-- Failures Rainshadow judges the patch's own: the patch changes the test, or the
-- test fails only on this uploader's changes or this change's series.  The
-- positive examples for test selection.
CREATE VIEW IF NOT EXISTS patch_caused AS
	SELECT * FROM failure WHERE verdict IN ('patch-changes-test', 'patch-suspect');

-- Failures it judges unrelated: the same test fails on branch runs or for
-- other uploaders, or the failure is infrastructure.
CREATE VIEW IF NOT EXISTS unrelated AS
	SELECT * FROM failure WHERE verdict IN
		('covered', 'known', 'unmatched', 'ambient-no-ticket', 'infra', 'infra-active');

CREATE TABLE IF NOT EXISTS harvest (
	key            TEXT PRIMARY KEY,   -- test:<suite>/<subtest> or ps:<change>/<patchset>
	done_at        TEXT NOT NULL,
	detail         TEXT
);
