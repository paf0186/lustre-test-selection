# lustre-test-selection

A dataset of Lustre CI test failures that were seen on Gerrit patchsets, and
the harvester that builds it. The aim is to learn which tests a patch should
run: for each failure we keep what the patch changed (files, test functions
touched, `Test-Parameters`) next to which test failed, and whether that failure
was the patch's doing or CI noise.

Source: [Rainshadow](https://github.com/lustre-tools/rainshadow), a mirror of
Whamcloud's Maloo test results. Rainshadow needs a login, so the dataset is
built by whoever has one; this repo holds the harvester and schema.

## Tables (`schema.sql`)

| table | one row per |
|---|---|
| `patchset` | Gerrit (change, patchset) with a failure: files changed, test functions touched per script, `Test-Parameters` |
| `session` | failed test session on that patchset: test group, distro, filesystem, enforcing, its retest and the retest's status |
| `failure` | failed test (suite, subtest): status, error, plus Rainshadow's verdict |

Views: `patch_caused` (verdict `patch-changes-test` or `patch-suspect`, the
positive examples) and `unrelated` (known flaky or infrastructure).

Uploaders are stored as a hash of their address, not the address.

### Verdicts

Rainshadow's judgement of a failure, from how it fails elsewhere:

| verdict | meaning |
|---|---|
| `patch-changes-test` | the patch edits this test's function |
| `patch-suspect` | fails only on this uploader's changes, or this change's series |
| `covered`, `known`, `unmatched`, `ambient-no-ticket` | fails on branch runs or for other uploaders: not the patch's |
| `seen-before`, `new` | rare; only this patch in the last 30 days |
| `infra`, `infra-active` | test infrastructure |

`elsewhere` counts (`branch_fails`, `other_uploaders`, `other_changes`,
`series_changes`) are the evidence behind the verdict. A verdict is a heuristic,
not ground truth: `patch-suspect` in particular mixes real regressions with
unlucky flaky failures on a small uploader's changes.

## Harvesting

Needs only the Python standard library and a Rainshadow login in
`~/.config/rainshadow/cli.env` (`RAINSHADOW_URL`, `RAINSHADOW_USER`,
`RAINSHADOW_PASSWORD`).

```bash
./harvest.py discover --days 30      # failures on patchsets, test by test
./harvest.py triage --sample 2000    # patch contents and verdicts for 2000 random patchsets
./harvest.py stats
```

The database is `data/failures.db` (gitignored). Both stages record what they
finished and resume where they stopped. `triage` costs a few seconds a
patchset, so `--sample` bounds it; a flood from one bad patch can put thousands
of failures on a handful of patchsets, so count patchsets, not failures.

Rainshadow can also page everything directly: `/api/v1/export/failures` and
`/api/v1/export/patchsets` (added in Rainshadow's `export.py`), once deployed.
