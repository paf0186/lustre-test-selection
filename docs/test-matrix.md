# The Lustre test matrix

What runs against a patch, how, and in which variants. This is the map to have
before asking which testing is beneficial for which patches.

<!-- generated-on: 2026-10-20 (window 2026-09-21 to 2026-10-20, 30 days; suites over 180 days) -->

The tables are generated from Whamcloud's Maloo (through Rainshadow) and the
lustre-release tree; the prose is by hand. To refresh or change anything:

```bash
./matrix_doc.py --lustre ~/lustre-release        # ask Rainshadow, rewrite the tables
./matrix_doc.py --offline --lustre ~/lustre-release   # redo them from docs/data/*.json
rainshadow matrix --days 30 --markdown            # the raw tables, any window, any job
```

Everything below that says "observed" is read from what ran. What defines a
group (which suites, which node layout) is Autotest's configuration, which is
not in lustre-release, so it is inferred from what ran and marked as such.

## From a patch to a result

1. A patchset is uploaded to Gerrit. Jenkins job `lustre-reviews` builds it
   (usually one build per patchset) and hands the build to Autotest.
2. Autotest runs **sessions** on VM clusters (test hosts named `onyx-NNNvmNNN`).
   A session is one **test group** on one node set, for one build. Results go to
   Maloo.
3. A session runs its **suites** in order. Every session starts with
   `node-provisioning` (about 19 minutes) and `lustre-initialization` (about 4),
   which are overhead and the commonest infrastructure failures. Then come the
   real suites (`sanity`, `replay-single`, ...), each a Maloo **test set** made
   of **subtests** (`test_42a`).
4. Each suite is run by `auster` (observed in `lustre/tests/auster`) from
   `lustre/tests`.

A patchset gets about 31 sessions on average, 87 in the example we looked at,
sometimes 280 or more. Branch jobs run far more per build (the tables below).

## How a suite is run

From the tree (`lustre/tests`):

- `auster [-s] [-f cfg] [-g groupfile] suite [--only LIST] [--except LIST]` runs
  suites. `-g` takes a group file; the tree ships only `test-groups/regression`
  and `test-groups/regression-mpi`. `acceptance-small.sh` runs
  the same list. The `review-*` groups that CI runs are not in the tree.
- `--only` and `--except` pick subtests; this is how `Test-Parameters`
  `env=ONLY="36 39"` narrows a run. `SLOW=yes` (`auster -s`) adds the slow tests.
- The cluster layout comes from environment: `MDSCOUNT`, `MDTCOUNT`, `OSTCOUNT`,
  `CLIENTCOUNT`, `FSTYPE`. The tree's defaults (`cfg/local.sh`) are one MDS, two
  OSTs, ldiskfs, `SLOW=no`; CI groups override them. Tests skip themselves when
  the layout does not suit them, so the same suite runs a different number of
  subtests in different variants (see "Skips" below).

## Kinds of run

<!-- BEGIN generated:jobs -->
| Job | Sessions | Builds | Sessions/build | Enforcing | Developer-test | Mean session (h) |
|---|---|---|---|---|---|---|
| lustre-reviews | 50,919 | 1,631 | 31 | 45,111 | 567 | 4.9 |
| lustre-master | 1,269 | 5 | 254 | 1,269 | 0 | 10.0 |
| lustre-master-next | 847 | 3 | 282 | 847 | 0 | 10.5 |
| e2fsprogs-reviews | 80 | 12 | 7 | 80 | 0 | 5.1 |
<!-- END generated:jobs -->

**Review groups** (`review-*`) are the standing matrix for every patchset. The
tree's regression suites are split into numbered parts (`review-dne-part-1` ...
`part-9`, and `-zfs-part-N` for ZFS) so that a part fits in a session of a few
hours. Those are what most of the 31 sessions are.

**`review-dne-subtest-change` and `review-dne-zfs-subtest-change`** run only the
subtests a patch changed. Observed: in our dataset every patchset that had these
sessions changed something under `lustre/tests`, and 93% of the patchsets that
changed `lustre/tests` had them. One session per changed suite-chunk, so a patch
that touches many tests gets many (21 in the example).

**`custom`** sessions are what a commit's `Test-Parameters:` line asks for: a
test list, a layout, a version pair. They carry the odd variants (`o2ib`, `wbcfs`,
aarch64 clients, old server or client versions).

**`full-*`** groups are the long-form matrix (the `lustre-master` set: much more
per group, 10 to 20 hours each). On reviews they appear a handful of times a
month.

**Branch jobs** (`lustre-master`, `lustre-master-next`) run the full matrix on
the landing branch, plus things reviews never run: upgrade and downgrade
(`clean-*`, `rolling-*`) and failover. One build is hundreds of sessions.

<!-- BEGIN generated:branch -->
| Job | Family of groups | Groups | Sessions | Sessions per build | Mean h |
|---|---|---|---|---|---|
| lustre-master | full | 3 | 615 | 123.0 | 12.6 |
| lustre-master | full-dne | 3 | 56 | 11.2 | 16.4 |
| lustre-master | failover | 6 | 56 | 11.2 | 13.8 |
| lustre-master | full-dne-zfs | 3 | 45 | 9.0 | 16.9 |
| lustre-master | full-dne-ipv6 | 3 | 45 | 9.0 | 16.2 |
| lustre-master | full-zfs | 4 | 53 | 10.6 | 12.9 |
| lustre-master | rolling-upgrade | 10 | 153 | 30.6 | 3.9 |
| lustre-master | rolling-downgrade | 8 | 126 | 25.2 | 3.2 |
| lustre-master | clean-upgrade | 4 | 80 | 16.0 | 0.4 |
| lustre-master | clean-downgrade | 2 | 40 | 8.0 | 0.3 |
| lustre-master-next | full | 3 | 426 | 142.0 | 11.5 |
| lustre-master-next | full-dne-zfs | 3 | 30 | 10.0 | 29.8 |
| lustre-master-next | full-zfs | 4 | 34 | 11.3 | 23.7 |
| lustre-master-next | full-dne | 3 | 36 | 12.0 | 16.0 |
| lustre-master-next | failover | 6 | 34 | 11.3 | 16.2 |
| lustre-master-next | full-dne-ipv6 | 3 | 30 | 10.0 | 15.2 |
| lustre-master-next | rolling-upgrade | 10 | 109 | 36.3 | 3.7 |
| lustre-master-next | rolling-downgrade | 8 | 88 | 29.3 | 3.1 |
| lustre-master-next | clean-upgrade | 4 | 40 | 13.3 | 0.5 |
| lustre-master-next | clean-downgrade | 2 | 20 | 6.7 | 0.4 |
<!-- END generated:branch -->

## The variant dimensions

Observed from sessions, nodes and `Test-Parameters`.

| Dimension | Values | Where it shows |
|---|---|---|
| Server filesystem | `ldiskfs`, `zfs`; `wbcfs` through `Test-Parameters` | group names (`-zfs-`), `server_fs` |
| Metadata layout | non-DNE: 1 MDS node with 1 MDT, 1 OSS node (7 OSTs on ldiskfs, 2 on ZFS), 2 clients. DNE: 2 MDS nodes with 2 MDTs each (4 MDTs), 1 OSS node with 8 OSTs, 2 clients | group names (`-dne-`), node services; `mdscount=2 mdtcount=4` in `Test-Parameters` |
| Client OS and architecture | RHEL 8.10, 9.x, 10.x, Rocky 9.8 (aarch64), SLES 15.7, Ubuntu 24.04 and 26.04 | `client_distro`, `client_architecture` |
| Server OS | RHEL 8.10 for the compatibility groups (`review-ldiskfs*`, `-ubuntu`, `-arm`), the client's own RHEL for the DNE groups | `server_distro` |
| Network | `tcp` in every Maloo record of a review group, including `review-dne-ipv6` (Maloo's network field does not show IPv6); `o2ib` only in `custom` | `server_network` |
| Security | `review-dne-selinux-ssk-*` (SELinux and shared-secret keys), `kerberos=true` in `Test-Parameters` | group names |
| Interop | `serverversion=` and `clientversion=` (2.15, 2.16, 2.17) in `Test-Parameters`; no review group does this by default | `Test-Parameters` |
| Client count and OSS count | `clientcount=2`, `osscount=` in `Test-Parameters` | `Test-Parameters` |
| Slow tests | `SLOW=yes` through `Test-Parameters` `env=` | `Test-Parameters` |
| Upgrade, downgrade, failover | branch jobs only | group names |

Inferred, not read from a configuration: `review-ldiskfs`, `-ubuntu` and `-arm`
are client compatibility checks (many client OSes against a RHEL 8.10 server,
running `sanity`, `sanity-lnet`, `lnet-selftest` and `sanity-sec`), while the
numbered `review-dne-*` parts are the depth (many suites on one OS pair).

## Review groups

How to read it: "Builds" is the share of review builds that ran the group;
"distros per build" is how many different client distros the group ran on in
one build. Most groups run on one or two distro pairs per build, not all of them,
so a given (group, distro) pair does not run for every patchset.

<!-- BEGIN generated:review_groups -->
| Group | Kind | Builds | Sessions / distros per build | Mean h | Nodes (clients, MDS nodes/MDTs, OSS nodes/OSTs) | Filesystem, network, arch; client distros (sessions) | Suites (runs per session, mean minutes) |
|---|---|---|---|---|---|---|---|
| review-ldiskfs | review | 67% | 4.14 / 4.1 | 4.9 | 2c 1mds/1mdt 1oss/7ost | ldiskfs tcp x86_64; clients RHEL 10.1 (1106), RHEL 8.10 (1102), RHEL 9.8 (1097), SLES 15.7 (1097) | sanity (235), sanity-lnet (31), lnet-selftest (6) |
| review-dne-part-4 | review | 67% | 2.4 / 2.33 | 5.9 | 2c 2mds/4mdt 1oss/8ost | ldiskfs tcp x86_64; clients RHEL 9.7 (1123), RHEL 10.1 (1112), RHEL 9.8 (224), RHEL 10.2 (157) | sanity-quota (163), insanity (60), sanity-hsm (50), sanity-flr (21), sanity-dom (17), replay-ost-single (13), mmp (10) |
| review-dne-zfs-part-1 | review | 67% | 2.26 / 2.13 | 5.4 | 2c 2mds/4mdt 1oss/8ost | zfs tcp x86_64; clients RHEL 9.8 (1203), RHEL 10.1 (1115), RHEL 10.2 (154), RHEL 8.10 (4) | sanity (305) |
| review-dne-zfs-part-4 | review | 67% | 2.22 / 2.12 | 6.2 | 2c 2mds/4mdt 1oss/8ost | zfs tcp x86_64; clients RHEL 10.1 (1151), RHEL 9.8 (1123), RHEL 10.2 (147), RHEL 8.10 (4) | sanity-quota (183), insanity (60), sanity-hsm (50), sanity-flr (21), sanity-dom (17), replay-ost-single (16), mmp (2) |
| review-dne-part-8 | review | 69% | 2.14 / 2.13 | 1.9 | 2c 2mds/4mdt 1oss/8ost | ldiskfs tcp x86_64; clients RHEL 10.1 (1126), RHEL 9.8 (1119), RHEL 10.2 (154), RHEL 8.10 (5) | replay-dual (52), replay-vbr (39) |
| review-dne-part-1 | review | 67% | 2.18 / 2.14 | 5.5 | 2c 2mds/4mdt 1oss/8ost | ldiskfs tcp x86_64; clients RHEL 10.1 (1114), RHEL 9.8 (1105), RHEL 10.2 (163), RHEL 8.10 (4) | sanity (314) |
| review-dne-zfs-part-2 | review | 67% | 2.17 / 2.11 | 6.1 | 2c 2mds/4mdt 1oss/8ost | zfs tcp x86_64; clients RHEL 10.1 (1123), RHEL 9.8 (1110), RHEL 10.2 (148), RHEL 8.10 (4) | sanity-sec (123), sanity-lfsck (55), replay-dual (49), sanity-pfl (41), sanity-lnet (35), pjdfstest (25), runtests (9), lnet-selftest (6), mds-survey (5) |
| review-dne-part-6 | review | 68% | 2.15 / 2.13 | 3.8 | 2c 2mds/4mdt 1oss/8ost | ldiskfs tcp x86_64; clients RHEL 10.1 (1110), RHEL 9.8 (1104), RHEL 10.2 (157), RHEL 8.10 (4) | replay-single (169), ost-pools (40) |
| review-dne-zfs-part-6 | review | 68% | 2.14 / 2.12 | 3.6 | 2c 2mds/4mdt 1oss/8ost | zfs tcp x86_64; clients RHEL 10.1 (1113), RHEL 9.8 (1100), RHEL 10.2 (150), RHEL 8.10 (4) | replay-single (158), ost-pools (37) |
| review-dne-subtest-change | subtest-change | 54% | 2.63 / 1.0 | 0.9 | 2c 2mds/4mdt 1oss/8ost | ldiskfs tcp x86_64; clients RHEL 9.7 (2310), RHEL 8.10 (3) | sanity x0.31 (28), conf-sanity x0.2 (28), sanity-ec x0.08 (29), sanityn x0.07 (31), sanity-sec x0.05 (29) |
| review-dne-zfs-subtest-change | subtest-change | 54% | 2.62 / 1.0 | 0.8 | 2c 2mds/4mdt 1oss/8ost | zfs tcp x86_64; clients RHEL 9.7 (2309), RHEL 8.10 (3) | sanity x0.31 (27), conf-sanity x0.2 (19), sanity-ec x0.08 (28), sanityn x0.07 (26), sanity-sec x0.05 (26) |
| review-dne-zfs-part-3 | review | 66% | 2.13 / 2.11 | 8.3 | 2c 2mds/4mdt 1oss/8ost | zfs tcp x86_64; clients RHEL 9.8 (1072), RHEL 10.1 (1067), RHEL 10.2 (147), RHEL 8.10 (4) | conf-sanity (477) |
| review-dne-zfs-part-5 | review | 68% | 1.55 / 1.48 | 4.3 | 2c 2mds/4mdt 1oss/8ost | zfs tcp x86_64; clients RHEL 9.7 (1181), RHEL 9.8 (208), RHEL 10.1 (174), RHEL 10.2 (148) | sanityn (109), recovery-small (109), lustre-rsync-test (15) |
| review-dne-part-5 | review | 67% | 1.55 / 1.51 | 4.5 | 2c 2mds/4mdt 1oss/8ost | ldiskfs tcp x86_64; clients RHEL 9.7 (1137), RHEL 9.8 (224), RHEL 10.1 (186), RHEL 10.2 (159) | sanityn (127), recovery-small (106), lustre-rsync-test (16) |
| review-dne-part-2 | review | 67% | 1.56 / 1.53 | 6.2 | 2c 2mds/4mdt 1oss/8ost | ldiskfs tcp aarch64/x86_64; clients RHEL 9.7 (1119), RHEL 9.8 (226), RHEL 10.1 (197), RHEL 10.2 (159) | sanity-sec (120), sanity-lfsck (60), replay-dual (50), sanity-pfl (41), sanity-lnet (34), pjdfstest (23), runtests (10), lnet-selftest (6), mds-survey (4) |
| review-dne-part-7 | review | 68% | 1.31 / 1.3 | 3.5 | 2c 2mds/4mdt 1oss/8ost | ldiskfs tcp x86_64; clients RHEL 9.8 (1103), RHEL 10.1 (182), RHEL 10.2 (158), RHEL 8.10 (4) | sanity-scrub (72), sanity-pcc (68), large-scale (26), sanity-ec (25) |
| review-dne-zfs-part-7 | review | 68% | 1.3 / 1.28 | 3.0 | 2c 2mds/4mdt 1oss/8ost | zfs tcp x86_64; clients RHEL 9.8 (1127), RHEL 10.1 (171), RHEL 10.2 (143), RHEL 8.10 (4) | sanity-pcc (65), sanity-scrub (43), large-scale (26), sanity-ec (25) |
| review-dne-ipv6 | review | 66% | 1.3 / 1.28 | 5.9 | 2c 2mds/4mdt 1oss/8ost | ldiskfs tcp x86_64; clients RHEL 9.8 (1103), RHEL 10.1 (168), RHEL 10.2 (138) | sanity (297), sanity-lnet (35) |
| review-dne-selinux-ssk-part-2 | review | 68% | 1.25 / 1.23 | 4.6 | 2c 2mds/4mdt 1oss/8ost | ldiskfs tcp x86_64; clients RHEL 8.10 (1132), RHEL 9.8 (131), RHEL 10.2 (128) | sanity-sec (132), recovery-small (102), sanity-selinux (14) |
| review-dne-selinux-ssk-part-1 | review | 68% | 1.25 / 1.23 | 5.1 | 2c 2mds/4mdt 1oss/8ost | ldiskfs tcp x86_64; clients RHEL 8.10 (1120), RHEL 9.8 (132), RHEL 10.2 (131) | sanity (278) |
| review-dne-part-3 | review | 64% | 1.32 / 1.31 | 11.6 | 2c 2mds/4mdt 1oss/8ost | ldiskfs tcp x86_64; clients RHEL 9.8 (1044), RHEL 10.1 (181), RHEL 10.2 (149), RHEL 8.10 (4) | conf-sanity (679) |
| review-ldiskfs-ubuntu | review | 66% | 1.17 / 1.12 | 7.5 | 2c 1mds/1mdt 1oss/7ost | ldiskfs tcp x86_64; clients Ubuntu 24.04 (1130), Ubuntu 26.04 (126) | sanity (276), sanity-sec (110), sanity-lnet (37), lnet-selftest (6) |
| review-ldiskfs-arm | review | 66% | 1.15 / 1.0 | 7.1 | 2c 1mds/1mdt 1oss/7ost | ldiskfs tcp aarch64; clients Rocky 9.8 (1243) | sanity (280), sanity-sec (89), sanity-lnet (31), lnet-selftest (6) |
| review-zfs | review | 67% | 1.01 / 1.0 | 4.5 | 2c 1mds/1mdt 1oss/2ost | zfs tcp x86_64; clients RHEL 8.10 (1107), RHEL 9.7 (1) | sanity-quota (116), replay-single (81), insanity (25), replay-ost-single (14), sanity-flr (12), sanity-lsnapshot (4) |
| custom | custom | 38% | 1.76 / 1.09 | 2.2 | 2c 2mds/4mdt 1oss/8ost | ldiskfs/wbcfs/zfs o2ib/tcp aarch64/x86_64; clients RHEL 9.7 (971), RHEL 10.1 (45), RHEL 9.4 (19), RHEL 8.10 (17) | conf-sanity x0.15 (252), sanity x0.3 (114), sanityn x0.07 (103), sanity-quota x0.06 (116), sanity-sec x0.07 (57), sanity-lnet x0.07 (32), sanity-flr x0.05 (33), sanity-ec x0.05 (23) |
| review-ldiskfs-dne | review | 28% | 2.02 / 2.0 | 5.6 | 2c 2mds/4mdt 1oss/8ost | ldiskfs tcp x86_64; clients RHEL 8.10 (464), RHEL 9.8 (461) | sanity (282), sanity-lnet (32), lnet-selftest (6) |
| review-ldiskfs-dne-arm | review | 28% | 1.06 / 1.0 | 6.7 | 2c 2mds/4mdt 1oss/8ost | ldiskfs tcp aarch64; clients Rocky 9.8 (479) | sanity (353), sanity-lnet (33), lnet-selftest (7) |
| review-dne-part-9 | review | 9% | 1.96 / 1.95 | 6.0 | 2c 2mds/4mdt 1oss/8ost | ldiskfs tcp x86_64; clients RHEL 9.8 (141), RHEL 10.2 (138) | performance-sanity (123), racer (86), parallel-scale (44), parallel-scale-nfsv4 (39), parallel-scale-nfsv3 (35), sanity-benchmark (11), obdfilter-survey (4) |
| full-dne-part-1 | full | 0% | 1.0 / 1.0 | 12.9 | 2c 2mds/4mdt 1oss/7ost | ldiskfs tcp x86_64; clients RHEL 10.2 (2), RHEL 8.10 (2), RHEL 9.8 (2), SLES 15.7 (2) | replay-single (160), recovery-small (106), parallel-scale (74), sanity-scrub (68), insanity (57), sanity-lfsck (53), replay-dual (46), ost-pools (36), sanity-lnet (31), large-scale (22), pjdfstest (20), sanity-flr (19), sanity-dom (15), replay-ost-single (12), runtests (10), mmp (9), obdfilter-survey (4), mds-survey (4), sanity-lsnapshot (0) |
| full-dne-part-2 | full | 0% | 1.0 / 1.0 | 12.2 | 2c 2mds/4mdt 1oss/7ost | ldiskfs tcp x86_64; clients RHEL 10.2 (2), RHEL 8.10 (2), RHEL 9.8 (2), SLES 15.7 (2) | sanity (259), sanity-quota (143), sanity-sec (111), sanity-pcc (59), replay-vbr (48), sanity-hsm (44), lustre-rsync-test (40), lnet-selftest (6), parallel-scale-nfsv3 (5) |
| full-dne-part-3 | full | 0% | 1.0 / 1.0 | 15.3 | 2c 2mds/4mdt 1oss/7ost | ldiskfs tcp x86_64; clients RHEL 10.2 (2), RHEL 8.10 (2), RHEL 9.8 (2), SLES 15.7 (2) | conf-sanity (614), sanityn (121), performance-sanity (81), sanity-pfl (36), parallel-scale-nfsv4 (26), sanity-benchmark (15) |
| full-dne-zfs-part-1 | full | 0% | 1.0 / 1.0 | 11.9 | 2c 2mds/4mdt 1oss/7ost | zfs tcp x86_64; clients RHEL 10.2 (2), RHEL 8.10 (2), RHEL 9.8 (2) | replay-single (153), recovery-small (107), insanity (57), sanity-lfsck (51), replay-dual (43), sanity-scrub (41), ost-pools (34), sanity-lnet (32), sanity-dom (30), parallel-scale (27), large-scale (23), pjdfstest (22), sanity-flr (20), replay-ost-single (14), runtests (9), sanity-lsnapshot (8), mds-survey (5), mmp (1), obdfilter-survey (1) |
| full-dne-zfs-part-2 | full | 0% | 1.0 / 1.0 | 11.6 | 2c 2mds/4mdt 1oss/7ost | zfs tcp x86_64; clients RHEL 10.2 (2), RHEL 8.10 (2), RHEL 9.8 (2) | sanity (248), sanity-quota (156), sanity-sec (106), sanity-pcc (60), sanity-hsm (44), replay-vbr (37), lustre-rsync-test (15), parallel-scale-nfsv3 (6), lnet-selftest (6) |
| full-dne-zfs-part-3 | full | 0% | 1.0 / 1.0 | 10.2 | 2c 2mds/4mdt 1oss/7ost | zfs tcp x86_64; clients RHEL 10.2 (2), RHEL 8.10 (2), RHEL 9.8 (2) | conf-sanity (307), sanityn (101), performance-sanity (83), parallel-scale-nfsv4 (39), sanity-pfl (37), sanity-benchmark (12) |
| full-dkms | full | 0% | 1.0 / 1.0 | 9.2 | 2c 1mds/1mdt 1oss/7ost | ldiskfs tcp x86_64; clients RHEL 8.10 (4) | recovery-small (92), replay-single (86), parallel-scale (47), replay-dual (35), sanity-lfsck (31), sanity-scrub (31), ost-pools (31), sanity-lnet (28), insanity (27), large-scale (26), pjdfstest (18), sanity-flr (18), sanity-dom (14), replay-ost-single (11), mmp (7), runtests (7), obdfilter-survey (2), mds-survey (1), sanity-lsnapshot (0) |
<!-- END generated:review_groups -->

## Suites

Which groups run each suite, and what it costs. `sanity` alone is run by many
groups in different variants (ldiskfs, ZFS, IPv6, SELinux and SSK, the
compatibility groups), so it dominates the hours.

<!-- BEGIN generated:suites -->
| Suite | Hours in the period | Groups | Subtests (most in a group) | Groups that run it |
|---|---|---|---|---|
| sanity | 75,484 | 14 | 1100 | ldiskfs, dne-zfs-part-1, dne-part-1, dne-ipv6, dne-selinux-ssk-part-1, ldiskfs-ubuntu, ldiskfs-arm, +7 |
| conf-sanity | 34,606 | 7 | 252 | dne-zfs-part-3, dne-part-3, dne-subtest-change, dne-zfs-subtest-change, custom, full-dne-part-3, full-dne-zfs-part-3 |
| sanity-quota | 16,729 | 8 | 133 | dne-part-4, dne-zfs-part-4, zfs, custom, dne-subtest-change, dne-zfs-subtest-change, full-dne-part-2, +1 |
| sanity-sec | 15,572 | 10 | 196 | dne-zfs-part-2, dne-part-2, dne-selinux-ssk-part-2, ldiskfs-ubuntu, ldiskfs-arm, dne-subtest-change, dne-zfs-subtest-change, +3 |
| replay-single | 14,401 | 9 | 197 | dne-part-6, dne-zfs-part-6, zfs, dne-subtest-change, dne-zfs-subtest-change, custom, full-dne-part-1, +2 |
| recovery-small | 8,541 | 9 | 136 | dne-zfs-part-5, dne-part-5, dne-selinux-ssk-part-2, custom, dne-subtest-change, dne-zfs-subtest-change, full-dne-part-1, +2 |
| sanity-lnet | 7,783 | 14 | 268 | ldiskfs, dne-zfs-part-2, dne-part-2, dne-ipv6, ldiskfs-ubuntu, ldiskfs-arm, ldiskfs-dne, +7 |
| sanityn | 7,016 | 7 | 251 | dne-zfs-part-5, dne-part-5, dne-subtest-change, dne-zfs-subtest-change, custom, full-dne-part-3, full-dne-zfs-part-3 |
| insanity | 5,472 | 6 | 17 | dne-part-4, dne-zfs-part-4, zfs, full-dne-part-1, full-dne-zfs-part-1, full-dkms |
| replay-dual | 5,445 | 9 | 1148 | dne-part-8, dne-zfs-part-2, dne-part-2, custom, full-dne-part-1, full-dne-zfs-part-1, full-dkms, +2 |
| sanity-hsm | 4,215 | 7 | 549 | dne-part-4, dne-zfs-part-4, dne-subtest-change, dne-zfs-subtest-change, custom, full-dne-part-2, full-dne-zfs-part-2 |
| sanity-lfsck | 3,975 | 8 | 90 | dne-zfs-part-2, dne-part-2, dne-subtest-change, dne-zfs-subtest-change, custom, full-dne-part-1, full-dne-zfs-part-1, +1 |
| sanity-pcc | 3,226 | 7 | 207 | dne-zfs-part-7, dne-part-7, dne-subtest-change, dne-zfs-subtest-change, custom, full-dne-part-2, full-dne-zfs-part-2 |
| ost-pools | 3,057 | 8 | 59 | dne-part-6, dne-zfs-part-6, dne-subtest-change, dne-zfs-subtest-change, full-dne-part-1, full-dne-zfs-part-1, full-dkms, +1 |
| sanity-pfl | 2,871 | 7 | 813 | dne-zfs-part-2, dne-part-2, dne-subtest-change, dne-zfs-subtest-change, custom, full-dne-part-3, full-dne-zfs-part-3 |
| sanity-scrub | 2,785 | 7 | 36 | dne-zfs-part-7, dne-part-7, full-dne-part-1, full-dne-zfs-part-1, full-dkms, dne-subtest-change, dne-zfs-subtest-change |
| sanity-flr | 2,078 | 9 | 633 | dne-part-4, dne-zfs-part-4, zfs, custom, dne-subtest-change, dne-zfs-subtest-change, full-dne-part-1, +2 |
| pjdfstest | 1,625 | 6 | 234 | dne-zfs-part-2, dne-part-2, full-dne-part-1, full-dne-zfs-part-1, full-dkms, custom |
| replay-vbr | 1,544 | 5 | 46 | dne-part-8, full-dne-part-2, full-dne-zfs-part-2, dne-subtest-change, dne-zfs-subtest-change |
| replay-ost-single | 1,489 | 8 | 65 | dne-part-4, dne-zfs-part-4, zfs, full-dne-part-1, full-dne-zfs-part-1, full-dkms, dne-subtest-change, +1 |
| sanity-dom | 1,410 | 8 | 422 | dne-part-4, dne-zfs-part-4, custom, full-dne-part-1, full-dne-zfs-part-1, full-dkms, dne-subtest-change, +1 |
| sanity-ec | 1,398 | 5 | 578 | dne-part-7, dne-zfs-part-7, dne-subtest-change, dne-zfs-subtest-change, custom |
| large-scale | 1,263 | 5 | 3 | dne-zfs-part-7, dne-part-7, full-dne-part-1, full-dne-zfs-part-1, full-dkms |
| lnet-selftest | 1,236 | 12 | 33 | ldiskfs, dne-zfs-part-2, dne-part-2, ldiskfs-ubuntu, ldiskfs-arm, ldiskfs-dne, ldiskfs-dne-arm, +5 |
| lustre-rsync-test | 883 | 4 | 17 | dne-zfs-part-5, dne-part-5, full-dne-part-2, full-dne-zfs-part-2 |
| runtests | 664 | 5 | 3 | dne-zfs-part-2, dne-part-2, full-dne-part-1, full-dne-zfs-part-1, full-dkms |
| performance-sanity | 585 | 3 | 10 | dne-part-9, full-dne-part-3, full-dne-zfs-part-3 |
| mmp | 509 | 5 | 11 | dne-part-4, dne-zfs-part-4, full-dne-part-1, full-dne-zfs-part-1, full-dkms |
| racer | 423 | 4 | 3 | dne-part-9, custom, dne-subtest-change, dne-zfs-subtest-change |
| mds-survey | 318 | 5 | 4 | dne-zfs-part-2, dne-part-2, full-dne-part-1, full-dne-zfs-part-1, full-dkms |
| sanity-selinux | 316 | 4 | 16 | dne-selinux-ssk-part-2, dne-subtest-change, dne-zfs-subtest-change, custom |
| parallel-scale | 217 | 5 | 23 | dne-part-9, full-dne-part-1, full-dne-zfs-part-1, full-dkms, custom |
| parallel-scale-nfsv4 | 189 | 3 | 10 | dne-part-9, full-dne-part-3, full-dne-zfs-part-3 |
| parallel-scale-nfsv3 | 162 | 3 | 10 | dne-part-9, full-dne-part-2, full-dne-zfs-part-2 |
| sanity-zero | 97 | 3 | 754 | dne-subtest-change, dne-zfs-subtest-change, custom |
| sanity-lsnapshot | 65 | 4 | 9 | zfs, full-dne-part-1, full-dne-zfs-part-1, full-dkms |
| sanity-benchmark | 64 | 4 | 7 | dne-part-9, custom, full-dne-part-3, full-dne-zfs-part-3 |
| sanity-ns | 42 | 3 | 794 | dne-subtest-change, dne-zfs-subtest-change, custom |
| obdfilter-survey | 22 | 6 | 259 | dne-part-9, full-dne-part-1, full-dne-zfs-part-1, full-dkms, dne-subtest-change, dne-zfs-subtest-change |
| sanity-krb5 | 17 | 3 | 24 | custom, dne-subtest-change, dne-zfs-subtest-change |
| sanity-fsx-xfs | 9 | 3 | 328 | dne-subtest-change, dne-zfs-subtest-change, custom |
| node-reset | 5 | 13 | 1 | ldiskfs, dne-part-8, dne-zfs-part-2, dne-part-6, dne-zfs-part-6, dne-part-2, dne-part-7, +6 |
| llapi_layout_test | 0 | 1 | 0 | custom |
<!-- END generated:suites -->

### In the tree and run

<!-- BEGIN generated:tree -->
Over 180 days.

- In-tree suites (`lustre/tests/*.sh` that call `run_test`): 49.
- In the tree's `regression` group (27) and `regression-mpi` (3): all but sgpdd-survey ran in some job.
- In the tree but never run in the period: dne_sanity, large-lun, lfsck-performance, parallel-scale-cifs, parallel-scale-nfs, recovery-oss-scale, replay-single-lmv, scrub-performance, sgpdd-survey.
- Run in reviews though not in `regression`/`regression-mpi`: corrupt-fault, dom-performance, lfru-performance, llapi_layout_test, mds-survey, node-reset, parallel-scale-nfsv3, parallel-scale-nfsv4, pjdfstest, sanity-compr, sanity-dom, sanity-ec, sanity-flr, sanity-fsx-xfs, sanity-krb5, sanity-ldlm-pool, sanity-ns, sanity-selinux, sanity-zero.
- Run only on the branch jobs (never in reviews): file_creation, file_creation_validation, recovery-double-scale, recovery-mds-scale, recovery-random-scale.
<!-- END generated:tree -->

## The matrix changes over time

Which groups run, and on how many builds, is not constant. Groups are added,
retired and moved between triggers, so a group's absence from a patchset is
not always a decision about that patchset.

<!-- BEGIN generated:history -->
| Group (share of builds that ran it) | 2026-03-25 | 2026-04-24 | 2026-05-24 | 2026-06-23 | 2026-07-23 | 2026-08-22 | 2026-09-21 |
|---|---|---|---|---|---|---|---|
| review-ldiskfs | 62% | 59% | 62% | 46% | 64% | 59% | 67% |
| review-dne-zfs-subtest-change | 34% | 39% | 38% | 40% | 42% | 50% | 54% |
| review-dne-subtest-change | 34% | 39% | 38% | 40% | 42% | 50% | 54% |
| review-dne-part-8 | 65% | 61% | 64% | 48% | 66% | 61% | 69% |
| review-dne-part-4 | 62% | 58% | 62% | 46% | 64% | 59% | 67% |
| review-dne-part-1 | 63% | 59% | 62% | 46% | 64% | 59% | 67% |
| review-dne-part-6 | 63% | 59% | 63% | 47% | 64% | 60% | 68% |
| review-dne-zfs-part-1 | 62% | 59% | 62% | 46% | 64% | 59% | 67% |
| review-dne-zfs-part-4 | 62% | 58% | 62% | 46% | 64% | 58% | 67% |
| review-dne-zfs-part-2 | 62% | 59% | 62% | 46% | 64% | 59% | 67% |
| review-dne-zfs-part-6 | 63% | 59% | 63% | 47% | 65% | 60% | 68% |
| review-dne-zfs-part-3 | 61% | 58% | 61% | 45% | 62% | 58% | 66% |
| review-dne-part-5 | 62% | 59% | 62% | 47% | 64% | 59% | 67% |
| review-dne-part-2 | 62% | 58% | 62% | 46% | 64% | 59% | 67% |
| review-dne-part-7 | 64% | 60% | 63% | 48% | 65% | 60% | 68% |
| review-dne-part-3 | 60% | 56% | 57% | 44% | 60% | 57% | 64% |
| review-dne-zfs-part-5 | 62% | 58% | 62% | 47% | 64% | 60% | 68% |
| review-dne-zfs-part-7 | 64% | 60% | 64% | 47% | 65% | 61% | 68% |
| review-ldiskfs-ubuntu | 59% | 58% | 59% | 46% | 62% | 58% | 66% |
| review-dne-ipv6 | 30% | 41% | 36% | 32% | 26% | 33% | 66% |
| review-ldiskfs-dne | 28% | 30% | 33% | 43% | 30% | 34% | 28% |
| review-dne-selinux-ssk-part-2 | 60% | 59% | 61% | 47% | 65% | 61% | 68% |
| review-dne-selinux-ssk-part-1 | 59% | 58% | 61% | 46% | 65% | 60% | 68% |
| review-zfs | 62% | 58% | 62% | 46% | 64% | 59% | 67% |
| custom | 25% | 31% | 24% | 32% | 33% | 40% | 38% |
| review-ldiskfs-arm | 31% | 34% | 27% | 33% | 63% | 58% | 66% |
| review-dne-part-9 | 36% | 36% | 28% | 27% | 19% | 4% | 9% |
| review-ldiskfs-dne-arm | 27% | 29% | 31% | 43% | 30% | 34% | 28% |
| _builds in the window_ | 926 | 845 | 1,032 | 891 | 1,333 | 1,594 | 1,631 |
<!-- END generated:history -->

## How developers steer it: `Test-Parameters`

`Test-Parameters:` lines in the commit message add `custom` sessions (and select
groups with `testgroup=`). `trivial` skips most testing; `fortestonly` and
`forbuildonly` select what the build does.

<!-- BEGIN generated:testparams -->
Among the 1,030 patchsets in the dataset (518 with a `Test-Parameters:` line), patchsets using each key:

| Key | Patchsets | Commonest values (patchsets) |
|---|---|---|
| testlist | 429 | sanity-ec (100), sanity (99), conf-sanity (60), sanityn (47), sanity-sec (36) |
| trivial | 195 |  |
| env | 191 |  |
| serverversion | 81 | 2.17 (35), 2.15 (21), 2.16 (19), 2.15.6 (6), 2.16.0 (4) |
| clientversion | 58 | 2.17 (31), 2.16 (12), 2.15 (11), 2.15.6 (5) |
| fortestonly | 58 |  |
| mdtcount | 52 | 4 (45), 24 (6), 6 (1), 10 (1), 12 (1) |
| fstype | 38 | zfs (36), ldiskfs (6), wbcfs (1) |
| mdscount | 24 | 2 (18), 6 (6), 4 (2), 3 (1), 1 (1) |
| ostcount | 23 | 8 (17), 4 (4), 3 (2), 1 (1), 12 (1) |
| clientdistro | 21 | el10.1 (19), el10.0 (19), rocky9.8 (2) |
| testgroup | 20 | review-dne-selinux-ssk-part-2 (20), review-dne-selinux-ssk-part-1 (10) |
| kerberos | 17 | true (17) |
| clientcount | 5 | 2 (5) |
| envdefinitions | 3 | ONLY=77ba (2), SLOW=yes,ENABLE_QUOTA=yes (1) |
| osscount | 3 | 2 (2), 4 (1) |
| serverdistro | 3 | el10.2 (3), el10.1 (3), el9.8 (2), el9.7 (2), el10.0 (1) |
| ONLY_REPEAT | 2 | 5 (2) |
| clientarch | 2 | aarch64 (2) |
| standalonemgs | 1 | true (1) |
| combinedmdsmgs | 1 | false (1) |
<!-- END generated:testparams -->

## Skips

A suite's subtest counts differ by variant because tests skip themselves.
`sanity` runs about 1,100 subtests and skips roughly 167 on ldiskfs compatibility
groups, 87 on ZFS part 1, 36 on the DNE groups: coverage of a suite depends on
the variant, not only on the suite. Per-group skips are in `docs/data/matrix-*.json`
(`groups[].suites[].skipped`).

## What this means for the analysis

- The unit of testing is the **(group, configuration)**, not the suite. Two
  groups that both run `sanity` see different subtests, a different node layout,
  a different filesystem.
- A great deal is redundant by construction (the same suite in many variants),
  and the redundancy is the thing to measure: which variant catches a fault that
  the others do not.
- The matrix moved during the period (above), so "this group did not catch it"
  needs "was this group running?" for that patch's date.
- Open: what makes a build run `ldiskfs-dne`, `part-9` or `custom` (a rule on
  the files touched, a schedule, or both). Our dataset is biased toward
  failing patches and cannot settle it; a sample of all patchsets could. The
  group definitions live in Autotest's configuration, which would settle it
  directly.
