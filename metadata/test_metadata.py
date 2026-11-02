import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract
import generate
import reconcile
import schema
import skipcheck
import validate

VOCAB = {"lod", "pool", "ptlrpc", "mgc", "dne", "migrate"}
ALIASES = {"pltrpc": "ptlrpc"}


def lines(text):
	return text.strip("\n").split("\n")


def record(**facts):
	base = {"requires": {}, "unparsed_skips": [], "evidence": {"modifies": [], "observes": []}}
	return {"suite": "sanity", "id": "27I", "summary": "root layout",
		"facts": {**base, **facts},
		"history": [{"hash": "d8b08f467de9", "subject": 'LU-12055 lod: "root" layout', "body": ""}]}


def header(**over):
	good = {"summary": "root layout", "commit": ["d8b08f467de9 ('LU-12055 lod: 'root' layout')"],
		"description": "Checks the root layout.", "keywords": ["lod", "pool"],
		"modifies": [], "observes": []}
	return {**good, **over}


def errors(rec, head):
	return [(f, m) for level, f, m in validate.check(rec, head, VOCAB, ALIASES) if level == "error"]


@pytest.mark.parametrize("code,want", [
	('\t[[ $OSTCOUNT -lt 2 ]] && skip_env "needs >= 2 OSTs"', {"ostcount": 2}),
	('\t(( MDSCOUNT >= 2 )) || skip "needs >= 2 MDTs"', {"mdscount": 2}),
	('\t[ $MDSCOUNT -lt 4 ] && skip "needs"', {"mdscount": 4}),
	('\t[[ $MDS1_VERSION -gt $(version_code 2.12.52) ]] ||\n\t\tskip "Need"', {"mds1_version": "> 2.12.52"}),
	('\t[ $MDS1_VERSION -lt $(version_code 2.11.52) ] &&\n\t\tskip "Need"', {"mds1_version": "2.11.52"}),
	('\t(( OST1_VERSION >= $(version_code 2.15.58.5) )) || skip "x"', {"ost1_version": "2.15.58.5"}),
	('\t[[ "$ost1_FSTYPE" == ldiskfs ]] || skip "ldiskfs only test"', {"ost1_fstype": ["ldiskfs"]}),
	('\t[ "$mds1_FSTYPE" != "ldiskfs" ] && skip "ldiskfs only"', {"mds1_fstype": ["ldiskfs"]}),
	('\t[[ "$mds1_FSTYPE" == zfs ]] && skip "not on zfs"', {"mds1_fstype": ["ldiskfs"]}),
	('\tcombined_mgs_mds || skip "needs combined"', {"mgs": "combined"}),
	('\tcombined_mgs_mds && skip "needs separate"', {"mgs": "separate"}),
])
def test_skip_checks_become_requires(code, want):
	assert extract.analyse(lines(code))["requires"] == want


def test_parallel_and_slow_are_facts():
	facts = extract.analyse(lines('\t[ $PARALLEL == "yes" ] && skip "x"\n\t[ "$SLOW" = "no" ] && skip "y"'))
	assert facts["parallel"] is False and facts["slow"] is True and not facts["unparsed_skips"]


def test_other_skips_stay_unparsed():
	facts = extract.analyse(lines('\tfeature_x || skip "a message nobody has classified"'))
	assert facts["requires"] == {} and len(facts["unparsed_skips"]) == 1


def test_a_skip_inside_a_condition_is_not_a_requirement():
	code = '\tif [[ $x == 1 ]]; then\n\t\t[[ $OSTCOUNT -lt 2 ]] && skip "y"\n\tfi'
	facts = extract.analyse(lines(code))
	assert facts["requires"] == {} and facts["unparsed_skips"]


def test_own_cleanup_and_reformat_are_the_states():
	facts = extract.analyse(lines('\tlocal x\n\tcleanup\n\tstart_ost\n\treformat_and_config'))
	assert (facts["start"], facts["end"]) == ("stopped", "formatted")
	facts = extract.analyse(lines('\treformat_and_config\n\tstart_mds\n\tcleanup || error "x"'))
	assert (facts["start"], facts["end"]) == ("formatted", "stopped")


def test_fail_loc_is_listed_apart_from_tunables():
	code = '\tdo_facet ost1 $LCTL set_param fail_loc=0x131e'
	assert extract.analyse(lines(code))["evidence"]["modifies"] == ["fail_loc"]
	code += '\n\tdo_facet ost1 $LCTL set_param obdfilter.*.sync=1'
	assert extract.analyse(lines(code))["evidence"]["modifies"] == ["fail_loc", "tunables"]


@pytest.mark.parametrize("code,want", [
	('\t$LCTL set_param fail_loc=0x131e', "client_fail_loc"),
	('\tdo_facet mds1 $LCTL set_param fail_loc=0x131e', "fail_loc"),
	('\tdo_facet mds1 \\\n\t\t$LCTL set_param fail_loc=0x131e', "fail_loc"),
	('\t$LCTL set_param llite.*.max_cached_mb=10', "client_tunables"),
	('\t$LCTL set_param osc.*.max_dirty_mb=10', "client_tunables"),
	('\tdo_nodes $list $LCTL set_param osc.*.max_dirty_mb=10', "tunables"),
	('\t$LCTL set_param at_max=10', "tunables"),
	('\tcancel_lru_locks osc', "client_cache"),
	('\tfill_ost 0', "space"),
	('\tlustre_rmmod', "host"),
	('\techo 1 > /proc/sys/vm/x', "host"),
])
def test_scoped_modifies(code, want):
	assert want in extract.analyse(lines(code))["evidence"]["modifies"]
	others = {"client_fail_loc": "fail_loc", "fail_loc": "client_fail_loc",
		  "client_tunables": "tunables", "tunables": "client_tunables"}
	assert others.get(want) not in extract.analyse(lines(code))["evidence"]["modifies"]


def test_waiting_for_quiet_is_observed():
	assert "quiesced" in extract.analyse(lines('\twait_delete_completed'))["evidence"]["observes"]


@pytest.mark.parametrize("head,want", [
	({"modifies": [], "observes": []}, "share"),
	({"modifies": ["client_tunables"], "observes": ["cache"]}, "client"),
	({"modifies": ["client_tunables", "fail_loc"], "observes": []}, "cluster"),
	({"modifies": []}, None),
])
def test_isolation(head, want):
	assert schema.isolation(head) == want


@pytest.mark.parametrize("head,hard,soft", [
	({}, 1, 1),
	({"requires": {"mdscount": 4, "ostcount": 8}}, 1, 1),
	({"requires": {"remote": ["mds"]}, "failover": ["mds"]}, 2, 1),
	({"requires": {"remote": ["mds"], "mdsnodes": 2}, "failover": ["mds"]}, 3, 1),
	({"requires": {"mdsnodes": 2}}, 2, 2),
	({"requires": {"clientcount": 2}}, 2, 2),
	({"requires": {"clientcount": 2, "remote": ["ost"]}}, 3, 3),
	({"requires": {"failure_mode": "soft"}, "failover": ["mds"]}, None, 1),
	({"requires": {"failure_mode": "hard"}, "failover": ["mds"]}, 1, None),
])
def test_min_vms(head, hard, soft):
	assert schema.min_vms(head) == hard and schema.min_vms(head, "soft") == soft


def test_a_suite_runs_on_its_largest_subtest_with_a_floor():
	remote = {"failover": ["mds"], "requires": {"remote": ["mds"], "clientcount": 2}}
	assert schema.suite_vms([{}, {"requires": {}}]) == schema.VM_FLOOR == 2
	assert schema.suite_vms([{}, remote]) == 3
	assert schema.suite_vms([{}], floor=1) == 1
	assert schema.suite_vms([{"requires": {"failure_mode": "soft", "nodecount": 3}}]) == 3


def test_skip_messages_become_feature_and_config():
	code = '\t$(lctl foo) || skip "Server does not support PCC-RO"\n\t$x || skip "Need tcp NETTYPE"'
	req = extract.analyse(lines(code))["requires"]
	assert req == {"feature": ["pcc_ro"], "config": ["tcp"]}


def test_suite_facts_leave_helpers_out():
	head = lines("""
MOUNT_2=${MOUNT_2:-"yes"}
(( OSTCOUNT >= 2 )) || skip_env "needs >= 2 OSTs"
helper() {
	(( MDSCOUNT >= 4 )) || skip "needs >= 4 MDTs"
}
""")
	assert extract.suite_facts(head) == {"requires": {"ostcount": 2}, "unparsed_skips": [], "mounts": 2}


def test_depends():
	bodies = {"34a": lines('\ttouch $DIR/f34'), "34b": lines('\tstat $DIR/$tfile'),
		  "34d": lines('\ttest_34b\n\tstat $DIR/f34')}
	order = list(bodies)
	names = {k: {m for x in v for m in extract.re.findall(extract.FIXED_PATH, x)} for k, v in bodies.items()}
	assert extract.depends("34d", order, bodies, names) == ["34a", "34b"]
	assert extract.depends("34a", order, bodies, names) == []


def test_counters_are_evidence_of_observes():
	code = '\t$LCTL get_param -n osc.*.stats\n\t$LFS df $MOUNT'
	assert extract.analyse(lines(code))["evidence"]["observes"] == ["free_space", "stats"]


@pytest.mark.parametrize("code,key,name", [
	('\t$LFS find $DIR -type f', "observes", "namespace"),
	('\tlfs find $MOUNT | wc -l', "observes", "namespace"),
	('\tfind $DIR/ -name x', "observes", "namespace"),
	('\trun_mdtest lookup-single', "observes", "timing"),
	('\tdd if=/dev/zero of=$DIR/f34 bs=4k', "modifies", "fixed_path"),
	('\trm -rf $DIR/*', "modifies", "fixed_path"),
])
def test_scope_evidence(code, key, name):
	assert name in extract.analyse(lines(code))["evidence"][key]


@pytest.mark.parametrize("code", [
	'\t$LFS find $DIR/$tdir -type f',
	'\tdd if=/dev/zero of=$DIR/$tfile',
	'\ttouch $DIR/${tdir}/x $DIR/$tfile.2',
	'\ttouch $DIR/d$testnum $DIR/f$tfile',
	'\tcat $MOUNT/.lustre/fid/x',
])
def test_own_paths_are_not_scope_evidence(code):
	found = extract.analyse(lines(code))["evidence"]
	assert not {"namespace", "fixed_path"} & set(found["modifies"] + found["observes"])


def test_evidence_of_a_called_subtest_is_inherited():
	bodies = {"34a": lines('\ttouch $DIR/f34'), "34d": lines('\ttest_34a\n\tstat $DIR/$tfile')}
	assert extract.inherited("34d", bodies, {})["modifies"] == ["fixed_path"]
	assert extract.called(lines('\t# test_34a\n\ttest_mkdir $DIR/$tdir')) == set()


def test_evidence_of_a_helper_is_inherited():
	helpers = {"outer": "outer() {\n\tinner\n}",
		   "inner": "inner() {\n\tdo_facet mds1 $LCTL set_param fail_loc=0x1\n}"}
	assert "fail_loc" in extract.inherited("1", {"1": lines('\touter')}, helpers)["modifies"]
	assert extract.inherited("1", {"1": lines('\t# outer\n\ttrue')}, helpers)["modifies"] == []


def test_a_helper_passes_on_the_subtests_it_calls():
	bodies = {"34a": lines('\ttouch $DIR/f34'), "34d": lines('\tdo_34')}
	helpers = {"do_34": "do_34() {\n\ttest_34a\n}"}
	assert extract.inherited("34d", bodies, helpers)["modifies"] == ["fixed_path"]


def test_reached_is_nearest_first_and_ends_on_a_cycle():
	helpers = {"far": "far() {\n\tnear\n}", "near": "near() {\n\tfar\n}", "off": "off() {\n}"}
	assert list(extract.reached("\tnear", helpers)) == ["near", "far"]


def test_helpers_named_test_are_helpers():
	text = ("test_default_quota() {\n\ttrue\n}\ntest_17sub() {\n\ttrue\n}\n"
		"other() {\n\ttrue\n}\ntest_61() {\n\ttest_default_quota\n}\nrun_test 61 \"quota\"\n")
	_, subtests, helpers = extract.parse_suite(text)
	assert [s[0] for s in subtests] == ["61"]
	assert set(helpers) == {"test_default_quota", "test_17sub", "other"}


def test_suite_follows_helpers_for_evidence_and_failover(monkeypatch):
	text = ("test_sub() {\n\tdeep\n}\ndeep() {\n\tfail ost1\n"
		"\tdo_facet mds1 $LCTL set_param fail_loc=0x1\n}\n"
		"test_1() {\n\ttest_sub\n}\nrun_test 1 \"wrapper\"\n")
	monkeypatch.setattr(extract, "git", lambda *args: text)
	rec = extract.extract_suite("r", "HEAD", "lustre/tests/x.sh", None, False, False)[0]
	assert list(rec["helpers"]) == ["test_sub", "deep"]
	assert "fail_loc" in rec["facts"]["evidence"]["modifies"]
	assert rec["facts"]["failover"] == ["ost"]


def test_targets_say_nothing_about_hosts():
	code = '\t(( MDSCOUNT >= 2 )) || skip "needs >= 2 MDTs"\n\tfail mds2'
	assert extract.analyse(lines(code))["hosts"] is False


@pytest.mark.parametrize("code,roles", [
	('\tfail mds1', {"mds"}),
	('\tfail $SINGLEMDS', {"mds"}),
	('\tfail mds1,ost1', {"mds", "ost"}),
	('\tfacet_failover --fsck ost1', {"ost"}),
	('\treplay_barrier mds1 && fail_nodf mgs', {"mgs"}),
	('\tfail $facet', {"any"}),
	('\tfail_clients 1', {"client"}),
	('\tfail_abort mds1', set()),
	('\t# fail mds1', set()),
	('\treplay_barrier mds1', set()),
	('\terror "fail mds1"', set()),
])
def test_failed_roles(code, roles):
	assert extract.failed(lines(code)) == roles


def test_failover_needs_the_server_off_the_clients_host():
	facts = {"requires": {}}
	extract.failover_needs(facts, lines('\tfail mds1'), {}, None)
	assert facts == {"requires": {"remote": ["mds"]}, "failover": ["mds"], "failovers": 1}


def test_client_failover_needs_a_second_client():
	facts = {"requires": {}}
	extract.failover_needs(facts, lines('\tfail_clients 1'), {}, None)
	assert facts["requires"] == {"clientcount": 2}


def test_a_helpers_failover_counts():
	facts = {"requires": {}}
	extract.failover_needs(facts, lines('\tdo_thing'), {"do_thing": "do_thing() {\n\tfail ost1\n}"}, None)
	assert facts["failover"] == ["ost"]


def test_forced_soft_has_no_hard_needs():
	facts = {"requires": {}}
	extract.failover_needs(facts, lines('\tFAILURE_MODE="SOFT"\n\tfail mds1'), {}, None)
	assert facts == {"requires": {}}


def test_soft_only_suite_has_no_hard_needs():
	facts = {"requires": {}}
	extract.failover_needs(facts, lines('\tfail mds1'), {}, "soft")
	assert facts == {"requires": {"failure_mode": "soft"}, "failover": ["mds"], "failovers": 1}


def test_hard_mode_needs_are_node_counts():
	code = """
	([ $FAILURE_MODE == "HARD" ] && [ "$(facet_host mds1)" == "$(facet_host mds2)" ]) &&
		skip "MDTs needs to be on diff hosts for HARD fail mode" &&
		return 0
"""
	facts = extract.analyse(lines(code))
	assert facts["requires"] == {"mdsnodes": 2} and facts["hosts"] is True


def test_remote_server_is_a_requirement():
	code = '\tremote_ost || skip "local OST"'
	assert extract.analyse(lines(code))["requires"] == {"remote": ["ost"]}


def test_nodes_need_host_evidence(tmp_path):
	rec = record()
	assert ("requires", "mdsnodes: the code never looks at hosts or fails a server") in errors(
		rec, header(requires={"mdsnodes": 2}))
	assert not errors(record(failover=["mds"]), header(requires={"mdsnodes": 2}))
	assert not errors(record(hosts=True), header(requires={"mdsnodes": 2}))
	first, second = tmp_path / "a", tmp_path / "b"
	write(first, rec, header(requires={"mdsnodes": 2}))
	write(second, rec, header())
	got, why = reconcile.reconcile(rec, first, second, None, VOCAB, ALIASES)
	assert why == [] and "requires" not in got or not got["requires"]


def test_nodsh_is_not_an_unparsed_skip():
	code = '\tremote_mds_nodsh && skip "remote MDS with nodsh" && return'
	assert extract.analyse(lines(code))["unparsed_skips"] == []


def test_quoted_counts_are_parsed():
	code = '\t[ "$OSTCOUNT" -lt "2" ] && skip "needs >= 2 OSTs"'
	assert extract.analyse(lines(code))["requires"] == {"ostcount": 2}


def test_run_test_summary_quoting():
	text = "test_1() {\n}\nrun_test 1 'single'\ntest_2() {\n}\nrun_test 2 \"say \\\"x\\\"\"\n"
	_, subtests, _ = extract.parse_suite(text)
	assert [(s[0], s[1]) for s in subtests] == [("1", "single"), ("2", 'say \\"x\\"')]


@pytest.mark.parametrize("head,safe", [
	({"modifies": [], "observes": []}, True),
	({"modifies": ["fail_loc"], "observes": []}, False),
	({"modifies": ["tunables"], "observes": []}, False),
	({"modifies": [], "observes": ["stats"]}, False),
	({"modifies": []}, None),
	({"observes": []}, None),
	({}, None),
	({"modifies": [], "observes": [], "parallel": False}, True),
])
def test_parallel_safe(head, safe):
	assert schema.parallel_safe(head) is safe


def test_good_header_is_clean():
	assert errors(record(), header()) == []


@pytest.mark.parametrize("over,field", [
	({"keywords": "lod pool"}, "keywords"),
	({"keywords": ["pltrpc"]}, "keywords"),
	({"keywords": ["bogus"]}, "keywords"),
	({"requires": {"reason": "none"}}, "requires"),
	({"requires": {"mdscount": 1}}, "requires"),
	({"requires": {"mdsnodes": 1}}, "requires"),
	({"requires": {"nonsense": 1}}, "requires"),
	({"description": "don’t"}, "text"),
	({"summary": "other"}, "summary"),
	({"start": "sideways"}, "start"),
	({"modifies": ["bogus"]}, "modifies"),
	({"commit": ["cafef00dcafe ('x')"]}, "commit"),
	({"commit": [{"d8b08f467de9 ('LU-12055 lod": "x"}]}, "commit"),
	({"commit": []}, "commit"),
	({"bogus": 1}, "bogus"),
])
def test_bad_headers(over, field):
	assert field in [f for f, _ in errors(record(), header(**over))]


def test_missing_lists_are_not_none():
	head = header()
	del head["observes"]
	assert ("observes", "missing: an empty list says none, a missing one says unknown") in errors(record(), head)


def test_requires_must_match_the_skip_checks():
	rec = record(requires={"ostcount": 2, "mds1_version": "> 2.12.52", "ost1_fstype": ["ldiskfs"]})
	good = header(requires={"ostcount": 2, "mds1_version": ">2.12.52", "ost1_fstype": "ldiskfs"})
	assert errors(rec, good) == []
	assert errors(rec, header(requires={"ostcount": 2, "mds1_version": "2.12.52", "ost1_fstype": ["ldiskfs"]}))
	assert errors(rec, header())


def test_states_must_match_the_subtests_own_statements():
	rec = record(start="stopped", end="formatted")
	assert errors(rec, header(start="stopped", end="formatted")) == []
	assert {f for f, _ in errors(rec, header())} == {"start", "end"}


def test_evidence_must_be_listed():
	rec = record(evidence={"modifies": ["fail_loc"], "observes": ["stats"]})
	assert {f for f, _ in errors(rec, header())} == {"modifies", "observes"}


def test_parallel_fact_must_be_kept():
	assert "parallel" in [f for f, _ in errors(record(parallel=False), header())]


def test_load_answer_finds_the_block():
	head, error = validate.load_answer("text\n```yaml\na: 1\n```\n")
	assert head == {"a": 1} and error is None
	assert validate.load_answer("a: [")[1]


def write(directory, rec, head):
	import yaml
	directory.mkdir(exist_ok=True)
	(directory / f"{rec['suite']}_{rec['id']}.txt").write_text("```yaml\n" + yaml.safe_dump(head) + "```\n")


def test_reconcile_merges_lists_and_queues_disagreement(tmp_path):
	rec = record()
	first, second = tmp_path / "a", tmp_path / "b"
	write(first, rec, header(modifies=["pools"], keywords=["lod"]))
	write(second, rec, header(modifies=["fail_loc"], keywords=["pool"]))
	got, why = reconcile.reconcile(rec, first, second, None, VOCAB, ALIASES)
	assert why == [] and got["modifies"] == ["fail_loc", "pools"] and got["parallel_safe"] is False
	write(second, rec, header(end="stopped"))
	got, why = reconcile.reconcile(rec, first, second, None, VOCAB, ALIASES)
	assert got is None and why == ["end: None or stopped"]


def test_reconcile_takes_a_clean_adjudicator(tmp_path):
	rec = record()
	first, second, third = tmp_path / "a", tmp_path / "b", tmp_path / "c"
	write(first, rec, header(end="stopped"))
	write(second, rec, header())
	write(third, rec, header(end="stopped"))
	got, why = reconcile.reconcile(rec, first, second, third, VOCAB, ALIASES)
	assert why == [] and got["end"] == "stopped"


def test_reconcile_never_keeps_a_header_with_errors(tmp_path):
	rec = record()
	first, second = tmp_path / "a", tmp_path / "b"
	write(first, rec, header(keywords=["bogus"]))
	write(second, rec, header(keywords=["bogus"]))
	got, why = reconcile.reconcile(rec, first, second, None, VOCAB, ALIASES)
	assert got is None and "bogus" in why[0]


def test_reconcile_takes_the_clean_reader_and_keeps_the_others_lists(tmp_path):
	rec = record()
	first, second = tmp_path / "a", tmp_path / "b"
	write(first, rec, header(keywords=["bogus"], modifies=["pools", "nonsense"], end="stopped"))
	write(second, rec, header())
	got, why = reconcile.reconcile(rec, first, second, None, VOCAB, ALIASES)
	assert why == [] and got["modifies"] == ["pools"] and "end" not in got


def test_a_bad_readers_requires_are_not_kept(tmp_path):
	rec = record()
	first, second = tmp_path / "a", tmp_path / "b"
	write(first, rec, header(requires={"clientcount": 2}))
	write(second, rec, header())
	got, why = reconcile.reconcile(rec, first, second, None, VOCAB, ALIASES)
	assert why == [] and not got.get("requires")


def test_requires_from_the_code_stand_and_readers_add_only_nodes(tmp_path):
	rec = record(requires={"feature": ["pcc_ro"], "ostcount": 2})
	first, second = tmp_path / "a", tmp_path / "b"
	write(first, rec, header(requires={"feature": ["dio_rw"], "OSTCOUNT": 2}))
	write(second, rec, header())
	got, why = reconcile.reconcile(rec, first, second, None, VOCAB, ALIASES)
	assert why == [] and got["requires"] == {"feature": ["pcc_ro"], "ostcount": 2}


def test_derived_fields_are_stored(tmp_path):
	rec = record(requires={"remote": ["mds"]}, failover=["mds"], failovers=3, depends=["1a"],
		     unparsed_skips=["x"])
	first, second = tmp_path / "a", tmp_path / "b"
	write(first, rec, header())
	write(second, rec, header())
	got, why = reconcile.reconcile(rec, first, second, None, VOCAB, ALIASES)
	assert why == [] and (got["min_vms"], got["min_vms_soft"], got["isolation"]) == (2, 1, "cluster")
	assert got["failover"] == ["mds"] and got["modifies"] == ["recovery"]
	assert got["depends"] == ["1a"] and got["unparsed"] == 1 and got["failovers"] == 3


def test_parallel_and_slow_follow_the_facts_or_either_reader(tmp_path):
	rec = record(slow=True)
	first, second = tmp_path / "a", tmp_path / "b"
	write(first, rec, header())
	write(second, rec, header(parallel=False))
	got, why = reconcile.reconcile(rec, first, second, None, VOCAB, ALIASES)
	assert why == [] and got["slow"] is True and got["parallel"] is False
	assert got["parallel_safe"] is True


def test_fixed_path_comes_from_the_code_not_the_reader(tmp_path):
	rec = record(evidence={"modifies": ["fixed_path"], "observes": []})
	first, second = tmp_path / "a", tmp_path / "b"
	write(first, rec, header())
	write(second, rec, header())
	got, _ = reconcile.reconcile(rec, first, second, None, VOCAB, ALIASES)
	assert got["modifies"] == ["fixed_path"] and got["parallel_safe"] is False
	rec = record()
	write(first, rec, header(modifies=["fixed_path"]))
	write(second, rec, header(modifies=["fixed_path"]))
	got, _ = reconcile.reconcile(rec, first, second, None, VOCAB, ALIASES)
	assert got["modifies"] == [] and got["parallel_safe"] is True


@pytest.mark.parametrize("over,kept", [
	({"observes": ["namespace"]}, True),
	({"modifies": ["fixed_path"], "observes": ["namespace"]}, True),
	({"modifies": ["fail_loc"], "observes": ["namespace"]}, False),
	({"observes": ["timing"]}, False),
	({}, False),
])
def test_parallel_fix_only_for_scope(tmp_path, over, kept):
	rec = record()
	first, second = tmp_path / "a", tmp_path / "b"
	write(first, rec, header(parallel_fix="scan $DIR/$tdir, not all of $DIR", **over))
	write(second, rec, header(**over))
	got, why = reconcile.reconcile(rec, first, second, None, VOCAB, ALIASES)
	assert why == [] and ("parallel_fix" in got) is kept


def test_parallel_fix_is_one_line():
	assert ("parallel_fix", "not one short line") in errors(
		record(), header(observes=["namespace"], parallel_fix="a\nb\nc"))


def test_reconcile_maps_and_drops_off_list_keywords(tmp_path):
	rec = record()
	first, second = tmp_path / "a", tmp_path / "b"
	write(first, rec, header(keywords=["lod", "bogus", "pltrpc"]))
	write(second, rec, header())
	got, why = reconcile.reconcile(rec, first, second, None, VOCAB, ALIASES)
	assert why == [] and got["keywords"] == ["lod", "pool", "ptlrpc"]


def test_reconcile_repairs_a_miscopied_subject(tmp_path):
	rec = record()
	first, second = tmp_path / "a", tmp_path / "b"
	write(first, rec, header(commit=["d8b08f467de9 ('LU-12055 lod: root layout')"]))
	write(second, rec, header())
	got, why = reconcile.reconcile(rec, first, second, None, VOCAB, ALIASES)
	assert why == [] and got["commit"] == ["d8b08f467de9 ('LU-12055 lod: 'root' layout')"]


def test_skipcheck():
	headers = {"1": {"requires": {"mdscount": 2}}, "2": {}, "3": {"requires": {"fstype": ["zfs"]}}, "4": {}}
	seen = {"1": "SKIP", "2": "SKIP", "3": "PASS", "4": "PASS"}
	got = skipcheck.compare(headers, seen, {"mdscount": "1", "fstype": "ldiskfs"})
	assert [x[0] for x in got] == ["2", "3"]


def test_prompt_holds_the_facts_and_lists():
	rec = {**record(requires={"ostcount": 2}), "code": "test_27I() {\n}", "helpers": {}}
	assert '"ostcount": 2' in generate.user_prompt(rec)
	assert "fail_loc" in generate.RULES and "{modifies}" in generate.RULES


def test_list_price():
	assert generate.list_price("terra", {"input": 1000000, "cached": 0, "output": 0}) == 1.0


def test_suite_that_reformats_never_shares():
	assert extract.suite_facts(["reformat_and_config"])["own_mounts"] is True
	assert "own_mounts" not in extract.suite_facts(["check_and_setup_lustre"])
	rec = {"facts": {"unparsed_skips": []}, "suite_facts": {"own_mounts": True}}
	header = reconcile.finish(rec, {"modifies": [], "observes": []})
	assert header["parallel_safe"] is False and header["isolation"] == "cluster"


def test_helpers_that_need_nodes():
	assert extract.node_helpers(["setup_router_test -m"]) == 3
	assert extract.node_helpers(["setup_router_test -p 2 -r 2"]) == 5
	assert extract.node_helpers(["setup_health_test false"]) == 2
	assert extract.node_helpers(["true"]) == 0
	assert schema.min_vms({"requires": {"nodecount": 3}}) == 3


def test_soft_failover_keeps_a_remote_client():
	header = {"failover": ["mds"], "requires": {"remote": ["any", "mds"]}}
	assert schema.min_vms(header, "soft") == 2
	assert schema.min_vms({"failover": ["mds"], "requires": {"remote": ["mds"]}}, "soft") == 1
