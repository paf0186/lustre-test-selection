import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bugs import generic, group  # noqa: E402
from classes import class_of  # noqa: E402
from grade import grade, line_class, parse  # noqa: E402

COSTS = {"overhead_s": 1400, "suite_class_s": {"sanity|ldiskfs-dne": 14000, "sanity-sec|zfs-dne": 2000},
	 "suite_s": {"sanity": 14000, "sanity-sec": 2000, "sanity-krb5": 900},
	 "group_suites": {"review-dne-selinux-ssk-part-2": ["recovery-small", "sanity-sec", "sanity-selinux"]},
	 "group_class": {"review-dne-selinux-ssk-part-2": "ldiskfs-dne-selinux-ssk"},
	 "session_s": {"review-ldiskfs": 16000}}

CASE = {"bugs": [
	{"evidence": {"sanity|ldiskfs-dne": [4, 2], "sanity|zfs-dne": [3, 3]}, "always": 0.0},
	{"evidence": {"sanity-sec|zfs-dne": [2, 1]}, "always": 0.5},
	{"evidence": {"sanity-krb5|ldiskfs-dne": [1, 1]}, "always": 0.0},
], "cost": {"actual": 100000, "trivial": 15000, "subtest_change": 3000}}


def test_line_classes_follow_what_autotest_ran():
	assert line_class({}) == "ldiskfs-dne"                     # a custom session's default
	assert line_class({"fstype": "zfs"}) == "zfs-dne"
	assert line_class({"mdscount": "1"}) == "ldiskfs"
	assert line_class({"fstype": "zfs", "mdscount": "1", "mdtcount": "1"}) == "zfs"
	assert line_class({"clientarch": "aarch64"}) == "ldiskfs-dne-arm"
	assert line_class({"clientdistro": "ubuntu2404", "mdscount": "1"}) == "ldiskfs-ubuntu"
	assert line_class({"fstype": "wbcfs"}) is None


def test_parse_reads_trivial_lines_and_groups():
	triv, lines = parse("Test-Parameters: trivial\ntestlist=sanity,sanityn\n"
			    "testgroup=review-dne-selinux-ssk-part-2 testlist=sanity-quota\nfstype=zfs\n", COSTS)
	assert triv
	assert [(ln.cls, ln.scripts) for ln in lines] == [
		("ldiskfs-dne", ["sanity", "sanityn"]),
		("ldiskfs-dne-selinux-ssk", ["recovery-small", "sanity-sec", "sanity-selinux", "sanity-quota"])]


def test_grade_catches_by_failed_over_ran_and_counts_cost():
	g = grade(CASE, "trivial\ntestlist=sanity", COSTS)
	assert g["bugs"] == [0.5, 0.5, 0.0]                          # 2 of 4; only the always-run half
	assert g["cost_s"] == 15000 + 3000 + 1400 + 14000
	g = grade(CASE, "trivial\ntestlist=sanity\nfstype=zfs testlist=sanity,sanity-sec", COSTS)
	assert g["bugs"] == [1.0, 0.75, 0.0]
	assert round(g["all_caught"], 4) == 0.0


def test_a_script_needing_keys_counts_only_with_them():
	assert grade(CASE, "trivial\ntestlist=sanity-krb5", COSTS)["bugs"][2] == 0.0
	assert grade(CASE, "trivial\nkerberos=true testlist=sanity-krb5", COSTS)["bugs"][2] == 1.0


def test_without_trivial_the_whole_matrix_runs_too():
	g = grade(CASE, "testlist=sanity", COSTS)
	assert g["bugs"][0] == 1.0 and g["cost_s"] == 100000 + 1400 + 14000


def test_bugs_merge_on_specific_signatures_and_collapse_cascades():
	f = lambda suite, sub, sig, st="FAIL": {"suite": suite, "subtest": sub, "sig": sig, "status": st}  # noqa: E731
	bugs = group([f("sanity", "test_1", "mgc lock leak (N != N)"), f("sanityn", "test_2", "mgc lock leak (N != N)"),
		      f("sanity", "test_3", "test_N returned N"), f("conf-sanity", "test_4", "test_N returned N")],
		     {"sanity", "sanityn", "conf-sanity", "a", "b", "c", "d", "e"})
	assert sorted(b["scripts"] for b in bugs) == [["conf-sanity"], ["sanity"], ["sanity", "sanityn"]]
	casc = group([f(s, "test_1", "x" + s) for s in ("a", "b", "c", "d")], {"a", "b", "c", "d", "e"})
	assert len(casc) == 1 and casc[0]["why"] == "cascade"
	assert generic("", "CRASH") and not generic("mount failed", "FAIL")


def test_sessions_map_to_classes():
	s = {"test_group": "review-ldiskfs-arm", "server_fs": "ldiskfs", "mdts": 1, "osts": 7,
	     "test_name": "lustre-reviews-el8.10-x86_64_lustre-reviews-rocky9.5-aarch64-review-ldiskfs-arm"}
	assert class_of(s) == "ldiskfs-arm"
	assert class_of({**s, "test_group": "review-dne-zfs-part-1", "server_fs": "zfs", "mdts": 4,
			 "test_name": "lustre-reviews-el9.7-x86_64-review-dne-zfs-part-1"}) == "zfs-dne"
	assert class_of({"test_group": "review-ldiskfs", "server_fs": "ldiskfs", "mdts": 1, "osts": 7,
			 "test_name": "lustre-reviews-el8.10-x86_64_lustre-reviews-sles15sp5-x86_64-review-ldiskfs"}) \
		== "ldiskfs-sles"
