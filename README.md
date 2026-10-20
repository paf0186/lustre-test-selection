# lustre-test-selection

A dataset of Lustre CI test failures that were seen on Gerrit patchsets, and
the harvester that builds it. The aim is to learn which tests a patch should
run: for each failure we keep what the patch changed (files, test functions
touched, `Test-Parameters`) next to which test failed, and whether that failure
was the patch's doing or CI noise.

Source: [Rainshadow](https://github.com/lustre-tools/rainshadow), a mirror of
Whamcloud's Maloo test results. Rainshadow needs a login, so the dataset is
built by whoever has one; this repo holds the harvester and schema.

## Test matrix

[docs/test-matrix.md](docs/test-matrix.md) maps what runs against a patch: jobs,
test groups, node layouts, variants, suites, and how the matrix changed over time.
Its tables are generated and the prose is by hand:

```bash
./matrix_doc.py --lustre ~/lustre-release          # ask Rainshadow, rewrite the tables
./matrix_doc.py --offline --lustre ~/lustre-release  # redo them from docs/data/*.json
rainshadow matrix --days 30 --markdown             # the raw tables for any window or job
```

## What is kept

Only failures that are the patch's own fault, caught by testing. Flaky,
ambient and infrastructure failures are dropped. A failure is kept when
Rainshadow's verdict is

- `patch-changes-test`: the patch edits the failing test's function, or
- `patch-suspect`: the test fails only on this uploader's changes or this
  change's series,

and the test is not ambient (Rainshadow class `ambient`) and did not fail on a
branch run or for another uploader in the 30 days before (`branch_fails` and
`other_uploaders` are 0). The harvester judges each failure as of its own date,
so old failures are judged on the evidence that existed then.

This is Rainshadow's heuristic, not ground truth. Use the `fault_evidence`
view to see how well each is corroborated: `sessions_failed` (it failed in
several configurations), `patchsets_failed` (it persisted across patchsets of
the change) and `retest_status` (`FAIL` means it failed again on retest).
A patch that breaks everything fails many tests in many sessions, so weight
examples by change, not by row.

## Tables (`schema.sql`)

| table | one row per |
|---|---|
| `patchset` | Gerrit (change, patchset) with a fault: files changed, test functions touched per script, `Test-Parameters` |
| `session` | failed test session holding a fault: test group, distro, filesystem, enforcing, its retest and the retest's status |
| `failure` | the faulty test (suite, subtest): status, error, Rainshadow's verdict and the evidence behind it |

Uploaders are stored as a hash of their address, not the address.

## Harvesting

Rainshadow does the work: `rainshadow patch-faults` judges every failure of a
period from one pass over its evidence per day (seconds for a month, with what
each patch changed and every session that ran on those changes).
`harvest.py` keeps the answer in `data/failures.db` (gitignored). Needs only the
Python standard library and a Rainshadow login in `~/.config/rainshadow/cli.env`.

```bash
./harvest.py fetch --days 180 --fill       # --fill: Rainshadow reads missing patch contents from Gerrit
./harvest.py stats
rainshadow patch-faults --days 30 --sessions > faults.json && ./harvest.py load faults.json
```

Rainshadow keeps a patchset's contents (files, test functions, `Test-Parameters`)
only for the last two weeks of failures; `--fill` has it read the rest from Gerrit.
The group-level view is `group_catches`: for each test group, the sessions that ran
on these patches and the faults they caught.
