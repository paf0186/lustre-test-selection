# Label audit: is each fault really the patch's?

50 faults, stratified by Rainshadow's reason for blaming the patch, each read
by hand from the diff and the logs (`labels.part-*.jsonl`, sampled by
`sample.py`).

| stratum | patch | not the patch | precision |
|---|---|---|---|
| suspect-solo (fails only on this uploader's changes) | 15 | 0 | 100% |
| suspect-series (fails only in this change's series) | 10 | 0 | 100% |
| suspect-generic (a crash or timeout) | 5 (1 likely) | 0 | 100% |
| changes-test (the patch changes the failing test) | 9 | 1 | 90% |
| suspect-retest-pass (a retest of the session passed) | 2 | 8 | 20% |

The retest-pass stratum failed: known flaky tests (sanity-krb5 under
LU-20537), the osd-zfs OOM of LU-20276, an unusable OST, a race in the test.
Rainshadow now doubts such a failure unless the change failed the same way on
two or more patchsets, or the suite runs too rarely to tell (`doubtful`, its
confinement doubt).  Re-harvested, all eight of the stratum's wrong faults are
gone and both of its right ones remain.  The one wrong changes-test fault
(64853/1, sanity test_907a) is an injected write error reaching dd, a test
bug the patch exposed; it stays, since a patch changing a test owns that
test's failures.
