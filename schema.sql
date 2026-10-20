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
	submission     TEXT,
	-- from `rainshadow export-sessions` (NULL until `harvest.py sessions` ran):
	test_name      TEXT,       -- Autotest's name: job, distro, arch and group
	server_distro  TEXT,
	arch           TEXT,
	reason         TEXT,       -- landing, or a developer's own test
	day            TEXT,
	clients        INTEGER,    -- node layout: client nodes, MDS nodes and MDTs, OSS nodes and OSTs
	mds_nodes      INTEGER,
	mdts           INTEGER,
	oss_nodes      INTEGER,
	osts           INTEGER
);
CREATE INDEX IF NOT EXISTS session_ps ON session(change, patchset);
CREATE INDEX IF NOT EXISTS session_build ON session(job, build);

-- Every test set (suite run) of those sessions: a script that ran and passed in a
-- configuration is evidence too.
CREATE TABLE IF NOT EXISTS test_set (
	id             TEXT PRIMARY KEY,
	session_id     TEXT NOT NULL,
	suite          TEXT,
	status         TEXT,
	duration       INTEGER,
	sub_total      INTEGER,
	sub_passed     INTEGER,
	sub_failed     INTEGER,
	sub_skipped    INTEGER
);
CREATE INDEX IF NOT EXISTS test_set_session ON test_set(session_id);
CREATE INDEX IF NOT EXISTS test_set_suite ON test_set(suite, status);

-- When each change's sessions were last read whole from Rainshadow.
CREATE TABLE IF NOT EXISTS change_harvest (
	change         INTEGER PRIMARY KEY,
	sessions_at    TEXT
);

-- The patch as Gerrit gives it (commit message and diff), email addresses removed.
CREATE TABLE IF NOT EXISTS patch_text (
	change         INTEGER NOT NULL,
	patchset       INTEGER NOT NULL,
	message        TEXT,       -- the commit message
	diff           TEXT,       -- the diff, from the first "diff --git"
	fetched_at     TEXT,
	error          TEXT,       -- why Gerrit gave nothing
	PRIMARY KEY (change, patchset)
) WITHOUT ROWID;

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

-- Every build in the harvested window (rainshadow export-builds): the population.
CREATE TABLE IF NOT EXISTS build (
	job            TEXT NOT NULL,
	build          INTEGER NOT NULL,
	change         INTEGER,
	patchset       INTEGER,
	branch         TEXT,
	first_day      TEXT,
	sessions       INTEGER,
	failed         INTEGER,    -- sessions that failed, for any reason
	duration       INTEGER,    -- session seconds, all sessions
	PRIMARY KEY (job, build)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS build_ps ON build(change, patchset);

-- The random sample of all review patchsets, faulty or not, that cost is measured
-- on.  rank is a seeded hash of the key, so the draw is stable as data is added.
CREATE TABLE IF NOT EXISTS sample (
	change         INTEGER NOT NULL,
	patchset       INTEGER NOT NULL,
	rank           REAL NOT NULL,      -- uniform in [0, 1): in the sample when below the fraction
	PRIMARY KEY (change, patchset)
) WITHOUT ROWID;
