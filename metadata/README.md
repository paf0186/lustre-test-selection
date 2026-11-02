# Subtest metadata

Fills in a header like those of change 69743 (`README-ltsh.md`) for every subtest
in `lustre/tests`, without moving any test.  The results are sidecar files in
`data/metadata/headers/<suite>.yaml`, so no test run is needed to produce them.

| Step | Tool | What it does |
|---|---|---|
| 1 | `extract.py` | code, helpers, history, CI duration; requires, start and end from the code |
| 2 | `generate.py` | a model's answer for the fields that need judgment, cached per subtest |
| 3 | `validate.py` | every answer against the facts of step 1 and the fixed lists |
| 4 | `reconcile.py` | merges two readers, queues what they disagree on for a third |
| 5 | `skipcheck.py` | compares `requires` with the skips CI recorded |

```bash
LUSTRE=~/lustre_checkouts/master_checkouts/9
python3 metadata/extract.py --lustre $LUSTRE --rev origin/master -o data/metadata/extract.jsonl
python3 metadata/generate.py data/metadata/extract.jsonl --model terra --sample 50
python3 metadata/generate.py data/metadata/extract.jsonl --model sonnet --sample 50
python3 metadata/generate.py data/metadata/extract.jsonl --model terra --report
python3 metadata/validate.py data/metadata/extract.jsonl data/metadata/terra --summary
python3 metadata/reconcile.py data/metadata/extract.jsonl data/metadata/terra data/metadata/sonnet
python3 metadata/generate.py data/metadata/extract.jsonl --model opus --only data/metadata/queue.json
python3 metadata/reconcile.py data/metadata/extract.jsonl data/metadata/terra data/metadata/sonnet \
	--adjudicator data/metadata/opus
python3 -m pytest metadata/test_metadata.py
```

`generate.py` needs no API key: it runs `claude -p`, and Codex through
`~/astra/run.sh` with `ASTRA_EFFORT=low`.

## Parallel safety

`schema.parallel_safe()` is the rule.  A subtest may run beside others only if
`modifies:` and `observes:` are both present and both empty.  `modifies:` is
what it changes that a neighbour could see, and any entry rules it out:
`fail_loc` and `tunables` are listed apart, but both are settings.  `observes:`
is shared state it measures: stats and counters, grant, free space, debug logs.
A missing list means unknown, not safe.  Changes to its own `$DIR/$tdir` count
for neither.  A name in `$DIR` that is not its own `$tfile` or `$tdir` is
`modifies: fixed_path`, which `extract.py` finds and no reader can overrule, and
a subtest inherits the evidence of every subtest and suite helper it reaches.
A helper is any function no `run_test` runs, `test_default_quota` included.  A
helper called only on some paths still counts, which errs towards not sharing.

`isolation:` is what a subtest needs to itself: `share` is nothing, `client`
is a client of its own because all it touches is client state
(`schema.CLIENT_SCOPED`), and `cluster` is everything.

`parallel:` is the old `.ltsh` field for the framework's `PARALLEL` mode, which
is not used.  It stays as a hint and decides nothing here.

`parallel_fix:` is one line naming a small edit that would make a subtest safe,
such as scanning `$DIR/$tdir` rather than all of `$DIR`.  It is kept only when
every entry stopping the subtest is a matter of scope (`schema.FIXABLE`), so a
`fail_loc` or timing subtest never carries one.  It is a suggestion from one
reader at most and is not compared.

`observes`, `mdsnodes`, `ossnodes` and `requires: feature` are asked for on
change 69743 and not in it yet, and `parallel_fix` is not yet asked for
(`schema.PROPOSED`).

## Nodes

`mdscount` and `ostcount` count targets, not hosts, and say nothing about nodes:
any number of targets can share one host.  `clientcount` is a real node count.
Absent a node field, one host will do.

Node fields state what `FAILURE_MODE=HARD` needs, since that is the mode that
matters.  HARD powers off a whole host, with every target and client on it, so:

- `failover:` lists what the subtest fails: `mds`, `ost`, `mgs`, `any` (a server
  named only through a variable) or `client`.  `extract.py` finds it, helpers
  included, leaving out a subtest that sets `FAILURE_MODE=SOFT` itself.
- `requires: remote:` lists servers that cannot share the subtest's host.  Each
  failed server is on it, and so are `remote_mds` style skip checks.
- `requires: mdsnodes:` or `ossnodes:` is for a server that must stay up while
  another of its kind fails.  Skip checks on hosts in HARD mode give `mdsnodes`,
  and a reader may add either if the subtest fails a server or looks at hosts.
- `requires: failure_mode:` is a whole suite that works in only one mode.

In SOFT mode a failover is a stop and start on the same host, so a subtest with
`failover:` can ignore these node fields.  One with no `failover:` cannot: its
node fields are not about failover.

`min_vms` and `min_vms_soft` are the fewest VMs each mode allows, worked out from
these (`schema.min_vms`), and are empty for a mode the subtest cannot run in.
They set no floor.

A suite stays whole and runs on `schema.suite_vms`: what its largest subtest
needs in HARD mode, and at least `schema.VM_FLOOR`, which is two.  One VM sends
all traffic over `0@lo` and lets a server `fail_loc` hit the client too.
`reconcile.py` writes the size of every suite to `data/metadata/vms.json`, from
the headers where there are any and the facts of `extract.py` otherwise.

## Clusters and placement

`requires: config:` is what a cluster must be set up with, and `feature:` a
capability of its Lustre.  Both come from `skips.json`, which classifies every
skip message once, so names match across subtests and no reader invents one:
rebuild it when new messages appear.  `unparsed:` counts the skips left over, so
a scheduler knows such a subtest may still skip.

`depends:` lists subtests this one calls, and the latest earlier ones sharing a
fixed name.  `failovers:` counts failovers in its own lines, as a guide to its
time under HARD.  A suite's own needs are in the `suite:` block of its header
file and are merged into each subtest's `requires`.

### Trial, November 2026

100 subtests on ltvm, master 7183418602, ldiskfs. Each ran on a 5-VM control
and on its claimed minimum size. Results are in
`data/metadata/run/trial-results/`.

| Claim | Run on | Result |
|---|---|---|
| 1 VM, SOFT | 97 subtests | 91 agree with control, 2 skipped where control ran, 4 not comparable |
| 2 VMs, HARD | 15 subtests | 15 pass |
| 3 VMs, HARD | 5 subtests | 4 pass, 100c hangs on 5 VMs as well |
| `share` | 17 at once, looping | about 3,100 passes, 0 failures |
| `client` | 7 on each of 2 clients, looping | 364 passes, 0 failures |
| `cluster` | 15 at once, looping | 8 fail, which shows the method detects collisions |

A wrong minimum size shows up as a skip, not a failure, so a scheduler must
treat a skip that control does not have as a fault.  ltvm puts mds1 and mds2 on
one host when a node gets two MDTs, and subtests that want them apart then skip
on HARD: give the first node one MDT.  A concurrent runner cannot use
`PARALLEL=yes`, which skips 292 subtests in sanity.sh, and must mount `$MOUNT2`
itself for suites that want it.  Suites that reformat at startup never share.

### Suite sizes, November 2026

Master 7183418602, with a two VM floor and each suite kept whole, against
lustre-reviews of 20 to 26 October (4.8 VMs per elapsed hour).  Sizes come from
the facts of `extract.py`, plus headers for the 100 subtests that have them, so
a reader can still raise one.

| VMs | Suites | Elapsed time |
|---|---|---|
| 2 | 27 | 87.0% |
| 3 | replay-single, replay-dual, large-scale | 9.5% |
| 4 | sanity-lnet | 3.4% |

That is 55% fewer VM-hours in the suites sized, 90% of the total, or 50% of the
total if the rest stays as it is.  Moving the subtests that need more than two
VMs into their own group would make it 57%.

## start and end

These describe a subtest as if moved to a `.ltsh` file.  A `cleanup` or reformat
in its first or last statement belongs to the header, so a subtest that begins
with `cleanup` is `start: stopped` even though it runs today from a mounted
filesystem.  `extract.py` finds these, and a model may not overrule them.
