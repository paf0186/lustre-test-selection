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

## Patch signals

`signals/` measures patches for any question we ask of them: will it have a bug
CI catches, does it touch something sensitive, does it deserve another AI review
pass.  A signal is a named measurement with a reason (`@signal` for a rule,
`Learned` for one fitted on history, an LLM or embedding cache for the rest);
sensitive areas are a table, `signals/areas.toml`.  A target is a label
(`signals/targets.py`).  Any target is fitted from any signals by one scorer
(logistic, calibrated to the population, with each score's reasons) and judged
by one harness: cross-validation on the training months, the held-out months as
a lockbox, every signal with and without, and a noise signal as the control.

```bash
python3 -m signals list                          # signals and targets
python3 -m signals score 64955/94                # one patch: signals, scores, reasons
python3 -m signals evaluate --target faulty      # AUC, riskiest and safest fifths
python3 -m signals wowy --target faulty          # what each signal is worth
```

Add a signal: one decorated function in `signals/core.py` (or a module of its
own, imported in `signals/__init__.py`), or an entry in `areas.toml`; then run
`wowy` to see whether it earns its place.

## The eval

An eval for predicting, per patch, which test scripts and configurations to run on
top of `Test-Parameters: trivial`: the plan is `test_selection_design.md` in the
design docs (project test-selection).  Every faulty patchset, and a random sample
of all review patchsets for cost, is a case; its patch faults are grouped into
bugs, and a prediction catches a bug when it runs a script in a configuration
where that script failed with the bug in the patchset's own build.

```bash
./harvest.py sessions && ./harvest.py patches     # what ran, and the patch text
./harvest.py sample --start 2026-03-20 --end 2026-10-19
eval/cases.py          # data/cases.jsonl (not committed) and eval/manifest.json
eval/costs.py          # eval/costs.json: setup and script run times per configuration
eval/score.py          # scores predictors, logs eval/results/log.jsonl, redraws the page
eval/check_classes.py  # which Test-Parameters keys gave which configuration
```

`eval/results/index.html` is drawn only from the results log (`eval/report.py`).
`eval/classes.py` holds the configuration classes and the keys that ask for them;
`eval/bugs.py` the bug grouping; `eval/grade.py` the grader;
`eval/predictors/` the predictors, which see only a case's `input`.

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
