# Miss audit: could reading the patch have predicted the catching test?

Sample: `misses_sample.jsonl`, 40 misses (caught chance < 0.5) and 10 hits
(>= 0.9) of the principles on the 6-set blind base, training set, one bug per
change. Per-row judgements are in `misses.jsonl` (with `reasoning`, `rule`,
`catching_config_matters`, `hit_reason`, and an `auditor_note` where the
merged verdict was qualified). Each row's diff and at least one failure log
were read; Rainshadow timed out for a few changes (rows 5, 38, 44, 48), where
the judgement rests on the case's evidence and the test source.

## Counts

Misses (40):

| predictable | n | rows |
|---|---|---|
| eval-artifact | 21 | 0, 7, 8, 9, 16, 17, 18, 19, 23, 24, 25, 26, 30, 31, 32, 33, 34, 35, 36, 37, 39 |
| intermittent | 10 | 2, 10, 11, 14, 21, 22, 27, 28, 29, 38 |
| rule | 6 | 3, 6, 12, 13, 15, 20 |
| unlikely | 2 | 4, 5 |
| reading | 1 | 1 |

Of the six `rule` rows only about four would actually turn into catches:
13 and 20 cleanly; 3 catches the root bug through ost-pools, not the graded
sanity-flr unit; 12 is a weak, inferred link with evidence [3,1]; 15 picks
the right script, but the bug showed only in zfs-dne 1 of 3 (0 of 2 in
ldiskfs-dne); 6 is right, but no zfs-dne session ran in that build, so the
grader cannot credit it.

Hits (10): 7 `rule`, 3 `reading`. By reason caught:

| reason | rows |
|---|---|
| right rule or the edited script, related to the failure | 40, 43, 45, 48 |
| always-run sessions (a rule would also have caught it) | 41, 46 |
| always-run zfs subtest-change session only | 44 |
| blind base (sanityn), no rule knows the mechanism | 42 |
| luck: a rule fired on an unrelated hunk | 47 (mds rule on a fail_loc hook; the cause was LFSCK), 49 (rpc-recovery on pinger.c; the cause was obdclass device teardown) |

So 6 of 10 hits are for the right reason, 4 by luck or the base.

Labels: clearly wrong for 3 misses (35: an ambient ARM sanity-sec cascade
hitting every change at the time; 36: a ZFS `zpool trim` race in
`wait_delete_completed`; 22: a known ~2% flake of sanity 230x, LU-20829,
exposed by subtest-change repeat mode). Four more are the series' bug but
not this diff's (8, 33, 30, 26); 2, 27 and 28 are likewise inherited from a
lower trash change. Unsure: 5, 15, 29, 32, 38, 44.

## Where the misses come from in the whole training set

The same predictor on all 1,351 training bugs (expected 56.1% caught),
the missed mass split mechanically:

| why missed | bugs | missed mass |
|---|---|---|
| catching script not selected in any class | 240 | 240.0 |
| script selected, wrong class (non-easy) | 82 | 81.5 |
| script selected, wrong class (easy: an edited script) | 47 | 45.6 |
| script selected in the catching class, f/r < 0.5 | 104 | 71.5 |
| empty evidence (only always-run sessions saw it) | 70 | 56.1 |
| evidence only in classes we cannot request | 42 | 41.4 |

Not-selected scripts, most often: sanity-quota 40, sanity-hsm 27, sanity-flr
23, replay-single 21, sanity-scrub 18, ost-pools 16, sanity-lfsck 13,
sanity-pcc 12, replay-vbr 9. Wrong-class evidence: zfs-dne 75, SELinux/SSK
30, ldiskfs-ubuntu 24, IPv6 14, ldiskfs-arm 14, zfs 8.
Of 575 bugs under 0.5, 195 carry the `series` tag, and 56 are crash-only
misses in a case where another crash was caught (an upper bound on what
crash-signature merging would fold away).

## Proposed rules (mechanism level, deduplicated)

1. **An edited script runs where it does not skip.** Changes to
   `sanity-selinux.sh`, or to code that handles security contexts (`secctx`,
   `selinux`, `security.*` xattr prepack), need sanity-selinux in
   ldiskfs-dne-selinux-ssk (testgroup=review-dne-selinux-ssk-part-2), because
   the script skips unless SELinux is enforcing, which only that group
   provides. Generalise: map each configuration-bound script to its
   configuration (sanity-selinux to the SELinux group, sanity-krb5 to
   kerberos=true, sanity-lsnapshot to zfs) for edited scripts too, not just
   for path rules. Row 13.
2. **Erasure coding needs ZFS.** Changes to `lustre/ec/`, the EC resync path
   in liblustreapi, or `sanity-ec.sh` need sanity-ec in zfs-dne as well as
   ldiskfs-dne, because parity resync punches holes and computes coverage
   from SEEK_DATA/SEEK_HOLE, which the ZFS OSD implements differently (it
   returns EOPNOTSUPP for the parity punch). The tests-only rule currently
   adds nothing beyond ldiskfs-dne for an edited sanity-ec. Rows 6, 20; row
   45's other bug (60c to 60h) is the same.
3. **MGC belongs with config and mount.** Add `lustre/mgc/` to the
   config-mount rule: changes to MGC config log and config lock handling need
   conf-sanity in ldiskfs-dne, because conf-sanity is the suite that restarts
   servers and checks that config logs and parameters are reapplied. Row 15
   (does not rescue it: the failure was zfs-dne 1/3), and hit 43's notes.
4. **Live config log updates need ost-pools.** Changes to MGS/MGC
   configuration log distribution (`lustre/mgs/`, `lustre/mgc/`, config
   llog locks, imperative recovery) need ost-pools in ldiskfs-dne, because
   pool_new/pool_add are the routine runtime config log updates every mounted
   client must pick up. Row 3 (the root bug; its graded unit was in
   sanity-flr).
5. **Server-local storage needs LFSCK and scrub.** Changes to
   server-local on-disk objects (`local_oid_storage`, local config llog
   copies in `lustre/mgc/mgc_request_server.c`) need sanity-lfsck and
   sanity-scrub in ldiskfs-dne, because LFSCK and OI scrub walk those
   objects and restart MDTs mid-scan. Row 12 (weak), and row 4 through rule 6.
6. **Series union.** A change with unmerged parents in its Gerrit series
   also gets its parents' rule selections (at least their server-side
   rules). Rows 4, 30, 26, and 8/33 at the culprit parent. This is policy,
   not a path rule, but it is the largest single lever after grading fixes:
   34% of sub-0.5 bugs are series-tagged.

Smaller, from notes on rows we did not count as `rule`:

- The lfsck rule should add sanity in ldiskfs-dne (sanity 60g and others run
  namespace LFSCK on striped and remote dirs). Hit 47 was luck without it.
- Nodemap changes (`lustre/ptlrpc/nodemap*`) need sanity-quota in
  ldiskfs-dne, because sanity-quota's squash, tenant and admin nodemap tests
  use `wait_nm_sync`. That is where 8 and 33's culprit parent failed.
- The quota rule should cover zfs-dne too when the patch also touches
  osd-zfs (row 31).
- Client direct I/O changes (`rw26.c`, `osc_dio_submit`, `cl_dio_aio`) need
  sanity-flr and sanity-pfl, because `lfs migrate` and `mirror resync` copy
  data with O_DIRECT (row 24's root bug).

## What the `reading` cases have in common

Rows 1 (miss), and hits 42, 44, 49. In each, the diff names a mechanism
whose risk lives somewhere the touched paths do not point:

- A client-side default that switches on a server-side feature implemented
  per backend (row 1: hole detection by default; the OFD serves it with
  SEEK_HOLE/SEEK_DATA, which fails with ENXIO on ZFS). The LLM needs to know
  which features are OSD-backed and differ between ldiskfs and ZFS.
- Moving teardown between lifecycle stages (row 49: NID stats hash destroy
  moved from `class_cleanup()` to `class_free_dev()`), which puts devices
  that are attached but fail setup at risk. The LLM needs the device
  lifecycle and which suites inject setup failure (conf-sanity, replay-single
  61d, mmp).
- A test helper that changes server-wide state but remounts only one client
  (row 42 and miss 16: `gssiam_setup`), which breaks every later
  multi-client suite in the same session. The LLM needs the session ordering
  of suites.
- A test whose threshold is backend-sensitive (row 44: a kmalloc-64 slab
  delta on the OSS, fragile on ZFS).

What an LLM stage needs to see: the commit message, the diff with function
context, a short map of mechanism to suite (which suites restart MDTs,
inject setup failures, use a second client, depend on SEEK_HOLE), and the
per-backend differences. Even so, this sample gives it little to do: one
miss in 40 is `reading`.

## What the `unlikely` ones look like

- Row 5: a strncpy to strscpy sweep (selftest, mdd migrate xattr list,
  fid2path, MGS llog, nodemap names) and a striped `mkdir` EIO in sanity-pfl
  test_14, 1 of 3 in each DNE class. None of the changed functions is on that
  path; the label itself is doubtful.
- Row 4: the child adds MGS-follower imperative recovery; the sanity-lfsck
  test_8 crash came from the parent's per-cld local storage. Nothing in the
  child's diff points there; only the series union (rule 6) recovers it.

Both are the indirect kind the task describes: a fault far from the diff,
often inherited, often intermittent.

## Eval artifacts to fix (in order of size)

1. **One root bug split per landing script.** A crash, hang or cascade lands
   in whichever test runs next, and each landing is graded as its own bug.
   Rows 0, 9, 19, 23, 24, 26, 34, 39 (and 17, a CRASH in most sessions that
   shows as TIMEOUT on Ubuntu). In nearly all of them the same root bug was
   caught elsewhere in the selection or by trivial. Fixes: merge CRASH units
   by the crash signature Rainshadow already extracts (`crash_sig`, for
   example `osd_trans_exec_op ASSERTION`, `class_free_dev ...
   rhashtable_free_and_destroy`, `ll_tiny_write_end ASSERTION(clpage)`);
   merge TIMEOUT units by hang stack (umount in `ll_kill_super`); merge a
   CRASH and a TIMEOUT of the same subtest in the same build.
2. **Random parameters in error signatures.** `mkdir -H <hash>` failures
   carry the randomly chosen hash name (crush, crush2, all_char, fnv_1a_64),
   so one deterministic bug becomes several 1-in-3 units (row 7: replay-single
   3/3 split into 1/3 units; row 5 too; case 66233/1 has over a dozen such bugs). Strip
   hash names in signature normalisation.
3. **Series inheritance.** A parent's bug is charged to every child (rows
   30, 26, 8, 33, 4, and 2/27/28). Either credit the child with its parents'
   selections (rule 6) or report series bugs only on the change that
   introduced them.
4. **Subtest-change-only evidence.** When the patch requested no custom
   session for the script, only Autotest's subtest-change sessions ran it
   (rows 18, 25, 37, 6, 38). They run edited tests repeatedly, in ldiskfs and
   zfs, and `always` divides by every one-subtest session even though only
   the one running the failing subtest could fail (row 20: 1 of 118). Some
   failures appear only on a repeat iteration (18, 25, 22), which a
   predicted single pass would never reproduce. Count `always` only over
   subtest-change sessions that ran the failing subtest; tag repeat-only
   failures and do not charge them to selection.
5. **Ambient failures labelled as the patch's.** Rows 35 (ARM sanity-sec
   broken fleet-wide), 36 (ZFS zpool trim race), 22 (LU-20829 flake). The
   patch-suspect verdict saw them on one uploader's changes only; drop them.
6. **Interop sessions inside a class.** Row 32's units came from a custom
   session with 2.15.6 clients, which the class mapper puts in ldiskfs-dne.
   Exclude sessions with clientversion/serverversion keys from the classes,
   or give interop its own class.
7. **Denominators that include runs which never reached the test.** Row 31
   (a deterministic proc-to-debugfs breakage of sanity-quota 4a) shows
   ldiskfs-dne [3,1]; row 16's ldiskfs run aborted in sanityn setup from the
   same cascade and was counted as passing. Count r only over sessions that
   reached the subtest, and treat a setup abort in a suite as failing it.

## Judgement

Of the 40 sampled misses:

- **Grading, not selection: about half (21).** Most are the same root bug
  already caught by another unit, by trivial, or by the selection itself,
  plus mislabels and repeat-only failures. Fixing the grader (crash and hang
  merging, hash-name normalisation, series attribution, subtest-change
  accounting) should raise the measured catch rate with no change to the
  predictor.
- **Reproduction: a quarter (10).** The right script ran in the right class
  and the bug showed one time in three or four. More selection does not
  help; repeating the suite, or SLOW and ONLY_REPEAT where the patch asks,
  would.
- **Rules: about 10 to 15% (4 to 6 rows)**, mostly configuration (ZFS for
  EC, the SELinux group for sanity-selinux) and series union, plus MGC and
  local-storage mappings.
- **An LLM reading the diff: under 5% (1 row)** in the misses; it would
  mostly fix hits that are now luck (47, 49, 42).
- **Not at all: about 5% (2 rows).**

The population table qualifies the sample: 240 bugs where the catching
script was never selected is the biggest bucket, and the scripts there
(sanity-quota, sanity-hsm, sanity-flr, replay-single, sanity-scrub,
ost-pools, sanity-lfsck, sanity-pcc) are where rules 4 to 6 and the smaller
suggestions aim. The sample says many of those are landing-site splits of a
bug caught elsewhere, so measure again after the grading fixes before
writing many more rules.
