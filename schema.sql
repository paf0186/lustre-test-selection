-- lustre-test-selection: Lustre CI test failures that were the fault of a Gerrit patchset.
--
-- One row per faulty test (a "failure"), per patchset that had one, and per test
-- session that ran on those changes.  Only failures judged the patch's own are
-- kept (see README.md); flaky and infrastructure failures are not.

CREATE TABLE IF NOT EXISTS patchset (
	change         INTEGER NOT NULL,
	patchset       INTEGER NOT NULL,
	uploader       TEXT,       -- hash of the uploader's address, not the address
	files          TEXT,       -- JSON list of the files the patchset changes (NULL: not read)
	n_files        INTEGER,
	scripts        TEXT,       -- JSON {script: {tests: [...], helpers: [...]}}: test functions touched
	test_parameters TEXT,      -- the commit message's Test-Parameters lines
	gerrit_kind    TEXT,       -- REWORK, TRIVIAL_REBASE ... what changed from the patchset before
	series         INTEGER,    -- lowest change number of the Gerrit series
	series_size    INTEGER,
	subject        TEXT,
	PRIMARY KEY (change, patchset)
) WITHOUT ROWID;

-- Every session of those changes, passed ones too: what ran on the patch.
CREATE TABLE IF NOT EXISTS session (
	id             TEXT PRIMARY KEY,
	change         INTEGER NOT NULL,
	patchset       INTEGER,
	job            TEXT,
	build          INTEGER,
	test_group     TEXT,       -- review-ldiskfs, review-dne-zfs-part-4, custom ...
	status         TEXT,       -- PASS, FAIL, ...
	enforcing      INTEGER,    -- 1 if a failure in it blocks landing
	client_distro  TEXT,
	server_fs      TEXT,
	duration       INTEGER,
	submission     TEXT
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
	test_group     TEXT,
	distro         TEXT,
	fs             TEXT,
	session_id     TEXT,
	test_set_id    TEXT,
	enforcing      INTEGER,
	kind           TEXT,               -- patch-changes-test | patch-suspect | unproven
	class          TEXT,               -- patch, single ... (never ambient or infra)
	count          INTEGER,            -- times the test failed in the 30 days around it
	other_changes  INTEGER,            -- other changes it failed on: the patch's own series
	series_changes INTEGER,            -- of which other changes of this change's series
	retest_status  TEXT,               -- PASS: the failure did not repeat on retest
	patch_changes  TEXT                -- JSON: what of the test the patch touches
);
CREATE INDEX IF NOT EXISTS failure_ps ON failure(change, patchset);
CREATE INDEX IF NOT EXISTS failure_test ON failure(suite, subtest);

-- How well each fault is corroborated.  A patch that breaks everything fails many
-- tests in many sessions, so weight by change when counting examples.
CREATE VIEW IF NOT EXISTS fault_evidence AS
	SELECT f.*,
		(SELECT count(DISTINCT session_id) FROM failure x
		  WHERE x.change = f.change AND x.patchset = f.patchset
		    AND x.suite = f.suite AND x.subtest = f.subtest) AS sessions_failed,
		(SELECT count(DISTINCT patchset) FROM failure x
		  WHERE x.change = f.change AND x.suite = f.suite AND x.subtest = f.subtest) AS patchsets_failed
	FROM failure f;

-- Which test groups caught which faults, against how often they ran on those
-- patches: the first cut at what testing is beneficial.
CREATE VIEW IF NOT EXISTS group_catches AS
	SELECT s.test_group,
		count(DISTINCT s.id) AS sessions,
		count(DISTINCT CASE WHEN s.status != 'PASS' THEN s.id END) AS failed_sessions,
		count(DISTINCT f.change || '/' || f.suite || '/' || f.subtest) AS faults_caught
	FROM session s LEFT JOIN failure f ON f.session_id = s.id
	GROUP BY s.test_group;
