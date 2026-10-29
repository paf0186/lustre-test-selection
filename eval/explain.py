#!/usr/bin/env python3
"""How test selection works, written from what testsel.py actually uses: the
trained model (model/), the cost model (eval/costs.json), the mandatory rules
(principles.toml) and the held-out cases.  Writes HOW_IT_WORKS.md,
docs/cost-catch.svg and docs/how-it-works.html.

  ./testsel.py train && python3 eval/explain.py
"""
from __future__ import annotations

import argparse
import math
import html
import json
import pickle
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
from classes import CLASSES  # noqa: E402
from grade import full, grade, parse, script_cost  # noqa: E402
from predictors import oracle, ranked  # noqa: E402
from predictors.principles import load as load_rules  # noqa: E402
from predictors.principles import NO_TEST_KINDS, must, needs_no_testing  # noqa: E402
from predictors.simple import Simple, edited_tests_only  # noqa: E402
from score import metrics  # noqa: E402

TIERS = ((0.85, "high"), (0.5, "medium"), (0.0, "low"))
SHOW = 30                                       # rows of the order to list past the cut


def short(cls: str) -> str:
	return CLASSES[cls][2].split(" (")[0].split(":")[0]


def point(cases, texts, costs) -> tuple[float, float]:
	m = metrics([(c, grade(c, t, costs), full(c)) for c, t in zip(cases, texts)])
	return m["cost_share"], m["recall_vs_full"]


def curves(cases, costs, policy, n_max):
	"""Held out: the order fitted on the training months, measured on the test months."""
	train = [c for c in cases if c["split"] == "train"]
	test = [c for c in cases if c["split"] == "test"]
	order = ranked.rank(train, costs, CLASSES)
	ns = list(range(0, n_max + 1))
	blind = [point(test, [ranked.prediction(order, n, CLASSES)] * len(test), costs) for n in ns]
	sel = [point(test, [Simple(order, n, **policy).predict(c["input"])["text"] for c in test], costs) for n in ns]
	orc = [point(test, [oracle.selection(c, costs, CLASSES, r) for c in test], costs) for r in oracle.RATES]
	return {"blind": blind, "selection": sel, "oracle": sorted(orc), "ns": ns,
		"test_cases": len(test), "test_changes": len({c["change"] for c in test}),
		"test_bugs": sum(len(c["bugs"]) for c in test if c["faulty"])}


def order_table(order, n, cases, costs):
	"""The shipped order, each prefix's cost share on the population and its catch
	on the history it was ranked on."""
	sampled = [c for c in cases if c["sampled"]]
	actual = sum(c["cost"]["actual"] for c in sampled)
	fulls = [full(c) for c in cases]
	m = metrics([(c, grade(c, "trivial", costs), f) for c, f in zip(cases, fulls)])
	rows = [{"k": 0, "set": "trivial and subtest-change sessions alone", "cls": "", "minutes": None, "today": None,
		 "new_session": False, "gain": None,
		 "share": sum(grade(c, "trivial", costs)["cost_s"] for c in sampled) / actual, "catch": m["recall_vs_full"]}]
	running = set()
	for k in range(1, min(len(order), n + SHOW) + 1):
		step = order[k - 1]
		text = ranked.prediction(order, k, CLASSES)
		share = sum(grade(c, text, costs)["cost_s"] for c in sampled) / actual
		m = metrics([(c, grade(c, text, costs), f) for c, f in zip(cases, fulls)])
		new = not step.get("group") and step["cls"] not in running
		if not step.get("group"):
			running.add(step["cls"])
		rows.append({"k": k, "set": step.get("script") or f"testgroup={step['group']}", "cls": step["cls"],
			     "minutes": step["secs"] / 60, "new_session": new or bool(step.get("group")),
			     "gain": step["gain"], "share": share, "catch": m["recall_vs_full"],
			     "today": (costs["matrix_share"].get(f"{step['script']}|{step['cls']}", 0.0) if not step.get("group")
				       else (1.0 if step["group"] in costs.get("matrix_groups", []) else 0.0))})
	return rows


def examples(cases, costs, policy, order, n):
	"""One held-out patch of each kind the rules treat differently."""
	test = sorted((c for c in cases if c["split"] == "test" and c["sampled"]), key=lambda c: c["key"])
	def docs(i):
		return needs_no_testing(i) and (i.get("gerrit_kind") or "") not in NO_TEST_KINDS

	kinds = (("documentation only", docs),
		 ("only the commit message changed", lambda i: (i.get("gerrit_kind") or "") in NO_TEST_KINDS),
		 ("changes only test functions", lambda i: not needs_no_testing(i) and edited_tests_only(i)),
		 ("changes HSM code", lambda i: any(f.startswith(("lustre/mdt/mdt_hsm", "lustre/llite/hsm",
								   "lustre/utils/lhsmtool"))
						     for f in i.get("files") or [])),
		 ("changes code with a must list", lambda i: must(i)[1] and not i["history"]["caught"]),
		 ("caught on an earlier patchset", lambda i: i["history"]["faulty_patchsets"] >= 2))
	out = []
	for title, pick in kinds:
		c = next((c for c in test if pick(c["input"])), None)
		if c is None:
			continue
		sel = Simple(order, n, **policy).predict(c["input"])
		g = grade(c, sel["text"], costs)
		subject = c["input"].get("subject") or (c["input"].get("message") or "").strip().split("\n")[0]
		out.append({"title": title, "key": c["key"], "subject": subject, "kind": c["input"].get("gerrit_kind"),
			    "files": c["input"].get("files") or [], "text": sel["text"], "why": sel["why"],
			    "history": c["input"]["history"], "cost_h": g["cost_s"] / 3600,
			    "always_h": ((c["cost"]["trivial"] or costs["session_s"].get("review-ldiskfs", 0)) +
					 c["cost"]["subtest_change"]) / 3600,
			    "actual_h": c["cost"]["actual"] / 3600})
	return out


def line_costs(text, costs):
	_, lines = parse(text, costs)
	out = []
	for ln in lines:
		secs = costs["overhead_s"] + sum(script_cost(s, ln.cls, costs, ln.only) for s in ln.scripts)
		out.append((ln, secs))
	return out


def risk_table(meta):
	with open(ROOT / "model" / "risk.pkl", "rb") as f:
		risk = pickle.load(f)
	coef = sorted(zip(risk.cols, risk.clf.coef_[0]), key=lambda kv: -abs(kv[1]))
	from signals import registry
	reg = registry()
	return {"base": risk.base, "coef": [(c, float(w), (reg[c.split(".")[0]].doc or "").strip().split("\n")[0]
				     if c.split(".")[0] in reg else "") for c, w in coef if abs(w) >= 0.01],
		"signals": meta["risk_signals"],
		"cuts": {name: float(risk.population[min(len(risk.population) - 1, int(cut * len(risk.population)))])
			 for cut, name in TIERS}}


LADDER = (("candidate", "simple candidate", "E4", "trivial, edited scripts whole, the blind base, all history"),
	  ("candidate", "+ must rules, edited tests only (ONLY=)", "E6", "+ mandatory test sets; ONLY= for edited tests"),
	  ("history", "history from the last faulty patchset", "E7", "history from the last faulty patchset only"),
	  ("tests", "+ both", "E8", "test-only patches skip the base; test scripts are no area's code"))


def evidence():
	"""Each adopted step's gain over the blind line, in points."""
	got = {}
	for mode, path in (("cv", "wowy.json"), ("test", "wowy-lockbox.json")):
		p = HERE / "results" / path
		if p.exists():
			secs = json.loads(p.read_text())["sections"]
			for sec, name, _, _ in LADDER:
				for row in secs.get(sec, {}).get(mode, []):
					if row["name"] == name and row.get("gain") is not None:
						got.setdefault(name, {})[mode] = (100 * row["gain"], 100 * row["gain_ci"][0],
										   100 * row["gain_ci"][1])
	return [(e, what, got.get(name, {})) for _, name, e, what in LADDER]


def efficacy(cases, costs):
	"""Every test set as it runs today: its hours, the bugs it caught and how
	many per 1,000 session hours, with a 95% upper bound on that rate."""
	from scipy.stats import chi2
	from score import value
	v = value(cases, costs, "", "")
	rows = []
	for r in v["rows"]:
		script, where = r["key"].split("|", 1)
		window_h = r["hours_per_build"] * costs["review_builds"]
		if window_h < 1:
			continue
		hi = chi2.ppf(0.95, 2 * (r["bugs"] + 1)) / 2
		rows.append({"script": script, "where": where, "always": where.startswith("review"),
			     "today": costs["matrix_share"].get(r["key"]) if not where.startswith("review") else 1.0,
			     "hours_build": r["hours_per_build"], "share_hours": r["share_hours"], "bugs": r["bugs"],
			     "share_bugs": r["share_bugs"], "share_only": r["share_only"],
			     "per_1000h": r["per_1000h"] or 0.0, "per_1000h_hi": 1000 * hi / window_h})
	rows.sort(key=lambda r: (r["per_1000h_hi"], r["per_1000h"]))
	return {"rows": rows, "changes": v["changes"], "since": costs["since"], "builds": costs["review_builds"]}


# -- the chart -------------------------------------------------------------------

def svg_chart(cv, op, themed: bool) -> str:
	W, H, L, R, T, B = 760, 470, 64, 20, 20, 92
	far = max(x for k in ("blind", "selection") for x, _ in cv[k])
	x0, x1, y0, y1 = 0.0, min(1.0, max(0.4, math.ceil((far + 0.03) * 20) / 20)), 0.4, 1.0
	X = lambda v: L + (min(v, x1) - x0) / (x1 - x0) * (W - L - R)          # noqa: E731
	Y = lambda v: T + (y1 - max(v, y0)) / (y1 - y0) * (H - T - B)          # noqa: E731
	col = ({"fg": "var(--ink)", "mute": "var(--mute)", "grid": "var(--rule)", "blind": "var(--blind)",
		"sel": "var(--accent)", "orc": "var(--oracle)", "goal": "var(--goal)", "bg": "var(--paper)"} if themed else
	       {"fg": "#1d2430", "mute": "#5d6675", "grid": "#d9dde3", "blind": "#8a93a3", "sel": "#0b6e8a",
		"orc": "#b5651d", "goal": "#2e7d32", "bg": "#ffffff"})
	o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" role="img" '
	     f'aria-label="Bugs caught against cost, held-out months" font-family="system-ui, sans-serif" font-size="12">']
	if not themed:
		o.append(f'<rect width="{W}" height="{H}" fill="{col["bg"]}"/>')
	for v in [i / 20 for i in range(int(round(x1 * 20)) + 1)]:
		o.append(f'<line x1="{X(v):.1f}" y1="{T}" x2="{X(v):.1f}" y2="{H - B}" stroke="{col["grid"]}"/>')
		o.append(f'<text x="{X(v):.1f}" y="{H - B + 16}" text-anchor="middle" fill="{col["mute"]}">{v:.0%}</text>')
	for v in (0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0):
		o.append(f'<line x1="{L}" y1="{Y(v):.1f}" x2="{W - R}" y2="{Y(v):.1f}" stroke="{col["grid"]}"/>')
		o.append(f'<text x="{L - 8}" y="{Y(v) + 4:.1f}" text-anchor="end" fill="{col["mute"]}">{v:.0%}</text>')
	o.append(f'<text x="{(L + W - R) / 2}" y="{H - B + 36}" text-anchor="middle" fill="{col["fg"]}">'
		 'session time, share of what each patchset runs today</text>')
	o.append(f'<text transform="translate(16 {(T + H - B) / 2}) rotate(-90)" text-anchor="middle" '
		 f'fill="{col["fg"]}">bugs caught, share of what today\'s testing catches</text>')

	def path(pts, color, width):
		pts = [p for p in pts if p[0] <= x1 + 0.05]
		d = " ".join(f"{'M' if i == 0 else 'L'}{X(x):.1f},{Y(y):.1f}" for i, (x, y) in enumerate(pts))
		return f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linejoin="round"/>'
	o.append(path(cv["oracle"], col["orc"], 2))
	o.append(path(cv["blind"], col["blind"], 2.5))
	o.append(path(cv["selection"], col["sel"], 3))
	for gx, gy in ((0.25, 0.9), (0.1, 0.9)):
		o.append(f'<circle cx="{X(gx):.1f}" cy="{Y(gy):.1f}" r="6" fill="none" stroke="{col["goal"]}" '
			 'stroke-width="2"/>')
		o.append(f'<text x="{X(gx) + 9:.1f}" y="{Y(gy) - 8:.1f}" fill="{col["goal"]}">goal {gy:.0%} at {gx:.0%}</text>')
	sx, sy = op
	o.append(f'<circle cx="{X(sx):.1f}" cy="{Y(sy):.1f}" r="5.5" fill="{col["sel"]}"/>')
	o.append(f'<text x="{X(sx) + 9:.1f}" y="{Y(sy) + 16:.1f}" fill="{col["sel"]}" font-weight="600">'
		 f'shipped: {sy:.0%} at {sx:.0%}</text>')
	ly = H - 34
	for i, (label, c, w) in enumerate((("this selection", col["sel"], 3), ("patch-blind base alone", col["blind"], 2.5),
					   ("oracle (knows the bugs)", col["orc"], 2))):
		lx = L + i * 220
		o.append(f'<line x1="{lx}" y1="{ly}" x2="{lx + 26}" y2="{ly}" stroke="{c}" stroke-width="{w}"/>')
		o.append(f'<text x="{lx + 32}" y="{ly + 4}" fill="{col["fg"]}">{label}</text>')
	o.append(f'<circle cx="{L + 6}" cy="{ly + 20}" r="5" fill="none" stroke="{col["goal"]}" stroke-width="2"/>')
	o.append(f'<text x="{L + 32}" y="{ly + 24}" fill="{col["fg"]}">the project\'s goals; today\'s testing '
		 'is 100% at 100%, off the chart to the right</text>')
	o.append("</svg>")
	return "\n".join(o)


# -- the document --------------------------------------------------------------------

def build(args) -> dict:
	cases = [json.loads(x) for x in open(args.cases)]
	costs = json.loads((HERE / "costs.json").read_text())
	meta = json.loads((ROOT / "model" / "meta.json").read_text())
	order = json.loads((ROOT / "model" / "order.json").read_text())
	policy = meta["policy"]
	n = meta["n"]
	sampled = [c for c in cases if c["sampled"]]
	cv = curves(cases, costs, policy, max(30, n + 10))
	op = cv["selection"][n]
	return {"meta": meta, "policy": policy, "n": n, "costs": costs, "order": order,
		"rows": order_table(order, n, cases, costs), "cv": cv, "op": op,
		"today_h": sum(c["cost"]["actual"] for c in sampled) / len(sampled) / 3600,
		"trivial_h": costs["session_s"].get("review-ldiskfs", 0) / 3600,
		"rules": [r for r in load_rules() if r.get("must")], "narrow": [r for r in load_rules() if r.get("narrow")],
		"examples": examples(cases, costs, policy, order, n), "efficacy": efficacy(cases, costs), "risk": risk_table(meta), "evidence": evidence(),
		"base_text": ranked.prediction(order, n, CLASSES), "population": len(sampled)}


def rules_text(d) -> list[tuple[str, str]]:
	"""The selection rules as testsel.py applies them, from the trained policy."""
	pol, n, budget = d["policy"], d["n"], d["meta"]["budget"]
	cap = pol.get("history_cap")
	out = [("Needs no testing", "documentation only (`Documentation/`, `lustre/doc/`, `contrib/`, `*.md`, `*.txt`, "
		"`README`, `LICENSE`, `ChangeLog`), or a patchset whose code Gerrit says is unchanged from the last one "
		"(only the commit message changed; the code was tested already): `trivial` alone, nothing else.")]
	if pol.get("use_must"):
		out.append(("Mandatory test sets", "each code area in the table below has test sets that belong to it.  A "
			    "patch touching the area runs them, whatever the history or the budget says." +
			    ("  Test scripts are not an area's code (`sanity-hsm.sh` does not make a patch an HSM patch): "
			     "the next rule covers them." if pol.get("must_skips_tests") else "")))
	out.append(("Edited tests", "a patch that changes only test functions of test scripts (no helpers, not "
		    "`test-framework.sh`, `cfg/` or `functions.sh`) runs just those tests: "
		    "`testlist=<script> env=ONLY=\"<tests>\"`.  Any other change to a test script runs the whole script."
		    if pol.get("edited") == "only" else "the scripts the patch edits run whole."))
	out.append(("The patch-blind base", f"the first {n} test sets of the order below, the same for every patch: the "
		    f"most that keeps `trivial` plus the base within {budget:.0%} of today's session time." +
		    ("  Skipped when the patch changes only test scripts: it cannot break Lustre's code, only "
		     "those scripts." if pol.get("tests_skip_base") else "")))
	out.append(("History", "every test set that caught a bug on " +
		    ("an earlier patchset of the same change" if cap is None else
		     f"the last {'' if cap == 1 else f'{cap} '}faulty patchset{'s' if cap != 1 else ''} of the same change")
		    + " runs again." + ("  Older history re-runs tests for bugs since fixed (E7)." if cap else "")))
	return out


def inline(text: str) -> str:
	parts = text.split("`")
	return "".join(f"<code>{html.escape(x)}</code>" if i % 2 else html.escape(x) for i, x in enumerate(parts))


def md(d) -> str:
	meta, costs, n = d["meta"], d["costs"], d["n"]
	o = []
	w = o.append
	w("# How test selection works\n")
	w(f"Generated by `eval/explain.py` from the model trained {meta['trained']} on {meta['cases']:,} patchsets. "
	  "Every number below is computed, not typed; rerun `./testsel.py train && python3 eval/explain.py` "
	  "to refresh it.\n")
	w("For a patchset, `testsel.py <change>` writes `Test-Parameters:` lines for the commit message.  They "
	  "start with `trivial`, which replaces the full review matrix with one small session, and add custom "
	  "sessions for the test sets below.  Autotest still runs its subtest-change sessions for any test the "
	  "patch edits.\n")
	w("## The rules, in order\n")
	for i, (title, text) in enumerate(rules_text(d), 1):
		w(f"{i}. **{title}**: {text}")
	w("")
	w("Everything is a union: a test set picked by two rules runs once.  Test sets in one configuration "
	  "share a session.\n")

	w("## How well it does\n")
	cv = d["cv"]
	sx, sy = d["op"]
	w(f"On the held-out months ({cv['test_cases']:,} patchsets of {cv['test_changes']:,} changes, "
	  f"{cv['test_bugs']:,} bugs; the order fitted on the other months), the shipped selection catches "
	  f"**{sy:.0%} of the bugs today's testing catches, for {sx:.0%} of its session time**.\n")
	w("![Bugs caught against cost](docs/cost-catch.svg)\n")
	floor = d["rows"][0]
	w(f"`trivial` and Autotest's subtest-change sessions alone cost {floor['share']:.0%} of today's time and catch "
	  f"{floor['catch']:.0%} of the bugs: every selection starts there, the oracle included, which is why its line "
	  "rises straight up from that point.\n")
	w("Bugs are counted per change, every bug of a patchset (not one per patchset).  A bug's chance of "
	  "being caught by a test set is how often that test set failed with it when it ran.  The oracle knows "
	  "each patch's bugs and picks the cheapest tests that catch them: no predictor can beat it.\n")
	ev = d["evidence"]
	if ev:
		w("What each adopted step adds, as gain over the patch-blind line (points of bugs caught, "
		  "averaged over 15 to 40% of cost; 95% intervals over changes).  Each row includes the ones above it:\n")
		w("| step (cumulative) | cross-validation | held-out months |")
		w("|---|---|---|")
		for e, what, r in ev:
			cells = [f"{x[0]:+.1f} ({x[1]:+.1f} to {x[2]:+.1f})" if (x := r.get(m)) else "" for m in ("cv", "test")]
			w(f"| {e}: {what} | {cells[0]} | {cells[1]} |")
		w("\nThe registrations and results are in [eval/EXPERIMENTS.md](eval/EXPERIMENTS.md).\n")

	w("## Mandatory test sets\n")
	w("From `eval/predictors/principles.toml`.  A file matches an area by path prefix, or by a substring "
	  "(`contains`).  These run in a DNE ldiskfs custom session unless the rule names other "
	  "configurations.\n")
	w("| area | files | runs | why |")
	w("|---|---|---|---|")
	for r in d["rules"]:
		files = ", ".join([f"`{p}`" for p in r.get("paths", [])] + [f"`*{c}*`" for c in r.get("contains", [])])
		where = ", ".join(r.get("classes", ["ldiskfs-dne"]))
		w(f"| {r['name']} | {files} | {', '.join(r['must'])} ({where}) | {r['why']} |")
	w("\nExempt: a patch that needs no testing (rule 1).\n")

	w("## The cost model\n")
	w(f"Session time is what the eval counts, from finished review sessions since {costs['since']}:\n")
	w(f"- **Session setup**: {costs['overhead_s'] / 60:.0f} minutes per session (node provisioning plus "
	  "Lustre initialization), paid once per configuration the selection uses.")
	w("- **A test set**: the mean time of that script in that configuration.  An ONLY= line costs the "
	  "script's time times the share of its subtests asked for.")
	w(f"- **Always run**: the `trivial` session ({d['trivial_h']:.1f} hours) and Autotest's subtest-change "
	  "sessions, which run when a patch edits tests.")
	w(f"- **Today**: every session the patchset's build actually ran; {d['today_h']:.1f} hours on average "
	  f"over the {d['population']:,} randomly sampled patchsets.")
	w(f"- **Budget**: the blind base is cut where trivial plus the base would pass {meta['budget']:.0%} "
	  "of today's time on those patchsets.  Mandatory sets, edited tests and history come on top, so a "
	  "patch's own cost varies.\n")
	w("The patch-blind base, priced:\n")
	w("```")
	total = d["trivial_h"] * 3600
	w(f"{'trivial':14s} {d['trivial_h'] * 60:6.0f} min")
	for ln, secs in line_costs(d["base_text"], costs):
		total += secs
		label = " ".join(ln.scripts) if ln.scripts else f"testgroup={ln.group}"
		w(f"{(ln.cls or ''):14s} {secs / 60:6.0f} min  {label}")
	w(f"{'total':14s} {total / 60:6.0f} min  ({total / 3600:.1f} h; today {d['today_h']:.1f} h)")
	w("```\n")
	w("Configurations and the keys that ask for them:\n")
	w("| configuration | Test-Parameters keys | what it is |")
	w("|---|---|---|")
	for k, (keys, _, desc) in CLASSES.items():
		w(f"| {k} | {('`' + keys + '`') if keys else ('(default)' if keys == '' else 'testgroup= only')} | {desc} |")
	w("")

	w("## The order test sets are added in\n")
	w("The patch-blind base ranked greedily: each step adds the test set that catches the most bugs not yet "
	  "caught per second it adds (its run time, plus a session's setup when its configuration has none "
	  "yet).  Only test sets that ran on at least half the full review builds of the last 30 days are candidates (*runs today*); a group Autotest runs only now and then, such as review-dne-part-9, is out.  "
	  f"Ranked on all {meta['cases']:,} patchsets.  **The line marks the budget: the first {n} run on every "
	  "patch.**\n")
	w("| # | test set | configuration | minutes | runs today | adds | cost so far | bugs so far |")
	w("|---|---|---|---|---|---|---|---|")
	for r in d["rows"]:
		if r["k"] == 0:
			w(f"| 0 | *{r['set']}* | | | | | {r['share']:.0%} | {r['catch']:.0%} |")
			continue
		mins = f"{r['minutes']:.0f}" + (" + setup" if r["new_session"] and not r["set"].startswith("testgroup") else "")
		w(f"| {r['k']} | {r['set']} | {r['cls']} | {mins} | {r['today']:.0%} | {r['gain']:.1%} | {r['share']:.0%} | "
		  f"{r['catch']:.0%} |")
		if r["k"] == n:
			w("| | **budget: the base stops here** | | | | | | |")
	w("\n*runs today*: the share of full review builds (those running review-dne-part-1) in the last 30 days "
	  "that ran it; a test set needs 50% to be a candidate.  *adds*: the share of the history's bugs this step catches that earlier steps did not.  *cost so far* "
	  "is on the random sample; *bugs so far* on the history it was ranked on, so it flatters the order "
	  "(the chart above is held out).\n")

	ef = d["efficacy"]
	w("## Efficacy of each test set\n")
	w(f"Every test set as review builds run it today, sorted by *at most* (least effective, with evidence enough to "
	  "say so, first): bugs caught per 1,000 session "
	  f"hours, over the {ef['changes']:,} faulty changes and {ef['builds']:,} review builds since {ef['since']}.  "
	  "*at most* is the 95% upper bound on that rate: a low bound means a test set is poor for its time, "
	  "not just unlucky.  *bugs* counts bugs it failed on; *only it* is the share of all changes' bugs no other "
	  "test set caught.  Rows whose configuration is a group (`review-ldiskfs`, `*-subtest-change`) are the "
	  "always-run sessions.  *runs today* is the share of full review builds (those running review-dne-part-1) of the "
	  "last 30 days; review-ldiskfs-dne and its aarch64 twin run on other builds, so they show 0%.  The page version "
	  "sorts by any column.\n")
	w("| test set | where | runs today | hours per build | share of hours | bugs | share of bugs | only it | "
	  "per 1,000 h | at most |")
	w("|---|---|---|---|---|---|---|---|---|---|")
	for r in ef["rows"]:
		today = f"{r['today']:.0%}" if r["today"] is not None else "0%"
		w(f"| {r['script']} | {r['where']} | {today} | {r['hours_build']:.2f} | {r['share_hours']:.1%} | {r['bugs']} | "
		  f"{r['share_bugs']:.1%} | {r['share_only']:.1%} | {r['per_1000h']:.1f} | {r['per_1000h_hi']:.1f} |")
	w("")
	w("## Examples\n")
	w("Held-out patchsets, one of each kind the rules treat differently.\n")
	for e in d["examples"]:
		w(f"### {e['title']}: {e['key']}\n")
		w(f"{e['subject']}  ")
		files = e["files"]
		w(f"Files: {', '.join(f'`{f}`' for f in files[:6])}{f' and {len(files) - 6} more' if len(files) > 6 else ''}  ")
		if e["history"]["faulty_patchsets"]:
			w(f"Earlier faulty patchsets: {e['history']['faulty_patchsets']}  ")
		w("")
		w("```")
		for ln in e["text"].splitlines():
			w(f"Test-Parameters: {ln}")
		w("```")
		for why in e["why"]:
			w(f"- {why}")
		w(f"\nCost: {e['cost_h']:.1f} hours, of which {e['always_h']:.1f} are the trivial and subtest-change "
		  f"sessions as they ran on that build.  Today's run: {e['actual_h']:.1f} hours.\n")

	w("## Patch risk\n")
	rk = d["risk"]
	w(f"`testsel.py` also scores how likely the patch is to carry a bug CI would catch: a logistic model on "
	  f"patch signals (`signals/`), fitted to the population (base rate {rk['base']:.0%}).  Tiers by "
	  "percentile of the population: high from the 85th (a score of "
	  f"{rk['cuts']['high']:.0%}), medium from the 50th ({rk['cuts']['medium']:.0%}), low below.  "
	  "Risk does not change the selection (spending by risk lost in E2); it is for routing review.\n")
	w("Weights, on standardized signals (positive raises the risk):\n")
	w("| signal | weight | what it measures |")
	w("|---|---|---|")
	for c, wt, doc in rk["coef"]:
		w(f"| {c} | {wt:+.2f} | {doc} |")
	w("")
	return "\n".join(o)


def page(d, chart: str, body_md: str) -> str:
	"""The HTML page: the same content, laid out to read."""
	meta, n, cv = d["meta"], d["n"], d["cv"]
	sx, sy = d["op"]
	esc = html.escape
	o = []
	w = o.append
	w('<title>How Test Selection Works</title>')
	w('<meta name="claude:viewport-fit-sides" content="cover">')
	w('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600&'
	  'family=IBM+Plex+Mono:wght@400;500&family=Fraunces:opsz,wght@9..144,600&display=swap">')
	w("""<style>
/* Layout: one reading column, wide tables scroll in place; a lab notebook for a CI matrix. */
:root{--paper:#fbfbf8;--ink:#1d2430;--mute:#5d6675;--rule:#dfe2e6;--panel:#f1f3f2;--accent:#0b6e8a;
--blind:#8a93a3;--oracle:#b5651d;--goal:#2e7d32;--cut:#c0392b;
--display:"Fraunces",Georgia,serif;--body:"IBM Plex Sans",system-ui,sans-serif;--mono:"IBM Plex Mono",ui-monospace,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--paper:#14181d;--ink:#e3e7ec;--mute:#9aa3ad;
--rule:#2c333b;--panel:#1b2026;--accent:#4fb3cf;--blind:#7d8794;--oracle:#e0a060;--goal:#6cc070;--cut:#ef7a6c;color-scheme:dark}}
:root[data-theme="dark"]{--paper:#14181d;--ink:#e3e7ec;--mute:#9aa3ad;--rule:#2c333b;--panel:#1b2026;--accent:#4fb3cf;
--blind:#7d8794;--oracle:#e0a060;--goal:#6cc070;--cut:#ef7a6c;color-scheme:dark}
body{background:var(--paper);color:var(--ink);font:15px/1.6 var(--body);padding-left:max(16px,env(safe-area-inset-left,0px));
padding-right:max(16px,env(safe-area-inset-right,0px));padding-block:32px 64px}
main{max-width:980px;margin:0 auto;display:grid;grid-template-columns:minmax(0,1fr);gap:28px}
.prose{max-width:68ch}
h1{font:600 2.2rem/1.15 var(--display);margin:0;text-wrap:balance}
h2{font:600 1.45rem/1.2 var(--display);margin:0 0 8px;text-wrap:balance}
h3{font:600 1rem/1.3 var(--body);margin:0 0 4px}
p{margin:0 0 10px}
.lede{color:var(--mute)}
.big{display:flex;flex-wrap:wrap;gap:12px 40px;align-items:baseline}
.big b{font:600 2.4rem/1 var(--display);color:var(--accent);font-variant-numeric:tabular-nums}
.big span{color:var(--mute);font-size:.9rem}
ol.steps{margin:0 0 12px;padding-left:1.4em;display:grid;gap:8px;max-width:72ch}
code,pre{font-family:var(--mono);font-size:.86em}
pre{background:var(--panel);border:1px solid var(--rule);border-radius:6px;padding:10px 12px;overflow-x:auto;margin:6px 0}
.scroll{overflow-x:auto;border:1px solid var(--rule);border-radius:6px}
table{border-collapse:collapse;width:100%;font-size:.88rem;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--rule);vertical-align:top}
th{font-weight:600;color:var(--mute);font-size:.78rem;letter-spacing:.04em;text-transform:uppercase;background:var(--panel)}
td.num{text-align:right;white-space:nowrap}
tr.cut td{border-bottom:2px solid var(--cut)}
tr.after td{color:var(--mute)}
.bar{display:inline-block;height:8px;border-radius:2px;background:var(--accent);vertical-align:middle;margin-right:6px}
.chip{display:inline-block;font-size:.75rem;padding:1px 7px;border-radius:9px;border:1px solid var(--rule);color:var(--mute)}
.chart svg{width:100%;height:auto;display:block}
.ex{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,440px),1fr));gap:16px}
.ex>div{min-width:0;border:1px solid var(--rule);border-radius:6px;padding:12px 14px}
.ex ul{margin:6px 0 0;padding-left:1.2em;font-size:.88rem}
.small{font-size:.85rem;color:var(--mute)}
a{color:var(--accent)}
table.sortable th{cursor:pointer;user-select:none;white-space:nowrap}
table.sortable th:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
table.sortable th.sorted{color:var(--ink)}
table.sortable th.asc::after{content:" \\25B2";font-size:.7em}
table.sortable th.desc::after{content:" \\25BC";font-size:.7em}
.filters{font-size:.88rem;margin:6px 0 10px}
#ef td:nth-child(-n+2){white-space:nowrap}
</style>""")
	w("<main>")
	w('<header class="prose"><h1>How test selection works</h1>')
	w(f'<p class="lede">What <code>testsel.py</code> asks CI to run for a Lustre patchset, and why. Generated from '
	  f'the model trained {esc(meta["trained"])} on {meta["cases"]:,} patchsets; every number here is computed.</p></header>')
	w('<section class="big">')
	w(f'<div><b>{sy:.0%}</b><br><span>of the bugs today\'s testing catches</span></div>')
	w(f'<div><b>{sx:.0%}</b><br><span>of today\'s session time</span></div>')
	w(f'<div><b>{n}</b><br><span>test sets in the patch-blind base</span></div>')
	w(f'<div><b>{len(d["rules"])}</b><br><span>code areas with mandatory test sets</span></div>')
	w("</section>")
	w(f'<p class="small">Held out: {cv["test_cases"]:,} patchsets of {cv["test_changes"]:,} changes in the test months, '
	  f'{cv["test_bugs"]:,} bugs, with the order fitted on the other months.</p>')

	w('<section><h2>The rules, in order</h2><ol class="steps">')
	for title, text in rules_text(d):
		w(f"<li><b>{esc(title)}.</b> {inline(text[0].upper() + text[1:])}</li>")
	w("</ol><p class=\"small\">A test set picked twice runs once; test sets in one configuration share a session. "
	  "<code>trivial</code> always runs, and Autotest still runs its subtest-change sessions for edited tests.</p></section>")

	w('<section><h2>Bugs caught against cost</h2>')
	w(f'<div class="chart">{chart}</div>')
	floor = d["rows"][0]
	w(f'<p class="prose"><code>trivial</code> and Autotest\'s subtest-change sessions alone cost {floor["share"]:.0%} '
	  f'of today\'s time and catch {floor["catch"]:.0%} of the bugs. Every selection starts there, the oracle included, '
	  "which is why its line rises straight up from that point.</p>")
	w('<p class="small prose">Bugs count per change, every bug of a patchset. A test set catches a bug with the '
	  "chance it failed with it when it ran. The oracle knows each patch's bugs and picks the cheapest tests that "
	  "catch them; nothing can beat it.</p>")
	ev = d["evidence"]
	if ev:
		w('<h3>What each step adds</h3><p class="small">Gain over the patch-blind line, in points of bugs caught, '
		  'averaged over 15 to 40% of cost; 95% intervals over changes. Each row includes the ones above it. '
		  'Registrations and results: <code>eval/EXPERIMENTS.md</code>.</p><div class="scroll"><table><tr><th>step (cumulative)</th>'
		  '<th>cross-validation</th><th>held-out months</th></tr>')
		for e, what, r in ev:
			cells = [f"{x[0]:+.1f} <span class=\"small\">({x[1]:+.1f} to {x[2]:+.1f})</span>" if (x := r.get(m)) else ""
				 for m in ("cv", "test")]
			w(f"<tr><td><b>{e}</b> {esc(what)}</td><td class=\"num\">{cells[0]}</td>"
			  f"<td class=\"num\">{cells[1]}</td></tr>")
		w("</table></div>")
	w("</section>")

	w('<section><h2>Mandatory test sets</h2><p class="small prose">From <code>eval/predictors/principles.toml</code>. '
	  "A file matches by path prefix, or by substring. They run in a DNE ldiskfs custom session unless the rule names "
	  "other configurations. Exempt only when the patch needs no testing.</p>")
	w('<div class="scroll"><table><tr><th>area</th><th>files</th><th>runs</th><th>why</th></tr>')
	for r in d["rules"]:
		files = "<br>".join([f"<code>{esc(p)}</code>" for p in r.get("paths", [])] +
				    [f"<code>*{esc(c)}*</code>" for c in r.get("contains", [])])
		where = ", ".join(r.get("classes", ["ldiskfs-dne"]))
		w(f"<tr><td>{esc(r['name'])}</td><td>{files}</td><td><b>{esc(', '.join(r['must']))}</b><br>"
		  f"<span class=\"chip\">{esc(where)}</span></td><td>{esc(r['why'])}</td></tr>")
	w("</table></div></section>")

	costs = d["costs"]
	w('<section><h2>The cost model</h2><ul class="prose">')
	w(f"<li><b>Session setup:</b> {costs['overhead_s'] / 60:.0f} minutes per session (node provisioning and Lustre "
	  "initialization), paid once per configuration a selection uses.</li>")
	w("<li><b>A test set:</b> the mean time of that script in that configuration, from finished review sessions since "
	  f"{esc(costs['since'])}. An ONLY= line costs that times the share of the script's subtests it asks for.</li>")
	w(f"<li><b>Always run:</b> the <code>trivial</code> session ({d['trivial_h']:.1f} hours) and Autotest's "
	  "subtest-change sessions when a patch edits tests.</li>")
	w(f"<li><b>Today:</b> every session the patchset's build ran: {d['today_h']:.1f} hours on average over "
	  f"{d['population']:,} randomly sampled patchsets.</li>")
	w(f"<li><b>Budget:</b> the base is cut where <code>trivial</code> plus the base would pass "
	  f"{meta['budget']:.0%} of today's time. Mandatory sets, edited tests and history come on top.</li></ul>")
	w("<h3>The patch-blind base, priced</h3><div class=\"scroll\"><table><tr><th>configuration</th><th>test sets</th>"
	  "<th>minutes</th></tr>")
	total = d["trivial_h"] * 3600
	w(f"<tr><td>review-ldiskfs</td><td><code>trivial</code></td><td class=\"num\">{d['trivial_h'] * 60:.0f}</td></tr>")
	for ln, secs in line_costs(d["base_text"], costs):
		total += secs
		label = " ".join(ln.scripts) if ln.scripts else f"testgroup={ln.group}"
		w(f"<tr><td>{esc(ln.cls or '')}</td><td>{esc(label)}</td><td class=\"num\">{secs / 60:.0f}</td></tr>")
	w(f"<tr><td><b>total</b></td><td class=\"small\">today: {d['today_h']:.1f} hours</td>"
	  f"<td class=\"num\"><b>{total / 60:.0f}</b> ({total / 3600:.1f} h)</td></tr></table></div>")
	w("<h3>Configurations</h3><div class=\"scroll\"><table><tr><th>configuration</th><th>Test-Parameters keys</th>"
	  "<th>what it is</th></tr>")
	for k, (keys, _, desc) in CLASSES.items():
		kk = f"<code>{esc(keys)}</code>" if keys else ("(default)" if keys == "" else "testgroup= only")
		w(f"<tr><td>{esc(k)}</td><td>{kk}</td><td>{esc(desc)}</td></tr>")
	w("</table></div></section>")

	w('<section><h2>The order test sets are added in</h2><p class="small prose">Ranked greedily: each step adds the '
	  "test set catching the most bugs not yet caught per second it adds (its run time, plus a session's setup when "
	  "its configuration has none yet). Candidates are the test sets that ran on at least half the full review builds of the "
	  "last 30 days (<i>runs today</i>); a group Autotest runs only now and then, such as review-dne-part-9, is out. "
	  f"<b>The red line is the budget: the first {n} run on every patch.</b> <i>Adds</i> is the share of the history's "
	  "bugs a step catches that earlier steps did not; <i>bugs so far</i> is on the history it was ranked on, so it "
	  "flatters the order. The chart above is held out.</p>")
	w('<div class="scroll"><table><tr><th>#</th><th>test set</th><th>configuration</th><th>minutes</th><th>runs today</th><th>adds</th>'
	  "<th>cost so far</th><th>bugs so far</th></tr>")
	top = max(r["gain"] or 0 for r in d["rows"]) or 1
	for r in d["rows"]:
		if r["k"] == 0:
			w(f"<tr><td class=\"num\">0</td><td colspan=\"5\"><i>{esc(r['set'])}</i></td>"
			  f"<td class=\"num\">{r['share']:.0%}</td><td class=\"num\">{r['catch']:.0%}</td></tr>")
			continue
		cls = "cut" if r["k"] == n else ("after" if r["k"] > n else "")
		setup = " <span class=\"chip\">+ setup</span>" if r["new_session"] and not r["set"].startswith("testgroup") else ""
		w(f"<tr class=\"{cls}\"><td class=\"num\">{r['k']}</td><td>{esc(r['set'])}</td><td>{esc(r['cls'])}</td>"
		  f"<td class=\"num\">{r['minutes']:.0f}{setup}</td><td class=\"num\">{r['today']:.0%}</td>"
		  f"<td class=\"num\"><span class=\"bar\" style=\"width:{60 * r['gain'] / top:.0f}px\"></span>{r['gain']:.1%}</td>"
		  f"<td class=\"num\">{r['share']:.0%}</td><td class=\"num\">{r['catch']:.0%}</td></tr>")
	w("</table></div></section>")

	ef = d["efficacy"]
	w('<section id="efficacy"><h2>Efficacy of each test set</h2><p class="small prose">Every test set as review builds '
	  f'run it today: bugs caught per 1,000 session hours, over the {ef["changes"]:,} faulty changes and '
	  f'{ef["builds"]:,} review builds since {esc(ef["since"])}. <b>Click a column to sort</b>. It opens sorted '
	  "by <i>at most</i>, so the least effective test sets with evidence enough to say so come first. <i>At most</i> is the 95% upper bound on the rate: sorted by it, the top rows are test sets "
	  "that are poor for their time with confidence, not just unlucky. <i>Only it</i> is the share of all changes' "
	  "bugs no other test set caught. Rows marked <span class=\"chip\">always</span> are the sessions every patch runs "
	  "(<code>trivial</code> and subtest-change).</p>")
	w('<div class="filters"><label><input type="checkbox" id="ef-matrix" checked> only the review matrix and always-run '
	  'sessions</label></div>')
	w('<div class="scroll"><table class="sortable" id="ef"><thead><tr>'
	  '<th data-k="s">test set</th><th data-k="s">where</th><th data-k="n">runs today</th>'
	  '<th data-k="n">hours per build</th><th data-k="n">share of hours</th><th data-k="n">bugs</th>'
	  '<th data-k="n">share of bugs</th><th data-k="n">only it</th><th data-k="n">per 1,000 h</th>'
	  '<th data-k="n" class="sorted asc">at most</th></tr></thead><tbody>')
	for r in ef["rows"]:
		today = r["today"] or 0.0
		inm = r["always"] or today >= 0.5
		chip = ' <span class="chip">always</span>' if r["always"] else ""
		w(f'<tr data-m="{int(inm)}"><td>{esc(r["script"])}</td><td>{esc(r["where"])}{chip}</td>'
		  f'<td class="num" data-v="{today:.3f}">{today:.0%}</td>'
		  f'<td class="num" data-v="{r["hours_build"]}">{r["hours_build"]:.2f}</td>'
		  f'<td class="num" data-v="{r["share_hours"]}">{r["share_hours"]:.1%}</td>'
		  f'<td class="num" data-v="{r["bugs"]}">{r["bugs"]}</td>'
		  f'<td class="num" data-v="{r["share_bugs"]}">{r["share_bugs"]:.1%}</td>'
		  f'<td class="num" data-v="{r["share_only"]}">{r["share_only"]:.1%}</td>'
		  f'<td class="num" data-v="{r["per_1000h"]}"><b>{r["per_1000h"]:.1f}</b></td>'
		  f'<td class="num" data-v="{r["per_1000h_hi"]:.3f}">{r["per_1000h_hi"]:.1f}</td></tr>')
	w("</tbody></table></div></section>")
	w("""<script>
(function () {
  var t = document.getElementById("ef"), box = document.getElementById("ef-matrix");
  if (!t) return;
  var ths = t.tHead.rows[0].cells;
  function val(row, i, num) {
    var c = row.cells[i];
    return num ? parseFloat(c.getAttribute("data-v")) : c.textContent.toLowerCase();
  }
  for (var i = 0; i < ths.length; i++) (function (i) {
    var th = ths[i];
    th.tabIndex = 0;
    function sort() {
      var asc = !(th.classList.contains("sorted") && th.classList.contains("asc"));
      for (var j = 0; j < ths.length; j++) ths[j].classList.remove("sorted", "asc", "desc");
      th.classList.add("sorted", asc ? "asc" : "desc");
      var num = th.getAttribute("data-k") === "n", body = t.tBodies[0];
      var rows = Array.prototype.slice.call(body.rows);
      rows.sort(function (a, b) {
        var x = val(a, i, num), y = val(b, i, num);
        return (x < y ? -1 : x > y ? 1 : 0) * (asc ? 1 : -1);
      });
      rows.forEach(function (r) { body.appendChild(r); });
    }
    th.addEventListener("click", sort);
    th.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sort(); } });
  })(i);
  function filter() {
    Array.prototype.forEach.call(t.tBodies[0].rows, function (r) {
      r.hidden = box.checked && r.getAttribute("data-m") !== "1";
    });
  }
  box.addEventListener("change", filter);
  filter();
})();
</script>""")
	w('<section><h2>Examples</h2><p class="small">Held-out patchsets, one of each kind the rules treat differently.</p>'
	  '<div class="ex">')
	for e in d["examples"]:
		files = e["files"]
		more = f" and {len(files) - 4} more" if len(files) > 4 else ""
		w(f"<div><h3>{esc(e['title'])}</h3><p class=\"small\"><code>{esc(e['key'])}</code> {esc(e['subject'])}<br>"
		  f"{esc(', '.join(files[:4]))}{more}</p>")
		w("<pre>" + esc("\n".join(f"Test-Parameters: {ln}" for ln in e["text"].splitlines())) + "</pre>")
		if e["why"]:
			w("<ul>" + "".join(f"<li>{esc(x)}</li>" for x in e["why"]) + "</ul>")
		w(f"<p class=\"small\">{e['cost_h']:.1f} hours, {e['always_h']:.1f} of them the trivial and subtest-change "
		  f"sessions as they ran on that build. Today's run took {e['actual_h']:.1f}.</p></div>")
	w("</div></section>")

	rk = d["risk"]
	w('<section><h2>Patch risk</h2><p class="prose">A separate score: how likely the patch is to carry a bug CI would '
	  f"catch. A logistic model on patch signals (<code>signals/</code>), fitted to the population (base rate "
	  f"{rk['base']:.0%}). <b>High</b> from the 85th percentile of patches (a score of {rk['cuts']['high']:.0%}), "
	  f"<b>medium</b> from the 50th ({rk['cuts']['medium']:.0%}), <b>low</b> below. It does not change the selection; "
	  "it is for routing review.</p>")
	w('<div class="scroll"><table><tr><th>signal</th><th>weight</th><th>what it measures</th></tr>')
	for c, wt, doc in rk["coef"]:
		w(f"<tr><td><code>{esc(c)}</code></td><td class=\"num\">{wt:+.2f}</td><td>{esc(doc)}</td></tr>")
	w("</table></div><p class=\"small\">Weights on standardized signals; positive raises the risk.</p></section>")
	w("</main>")
	return "\n".join(o)


def main() -> None:
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("--cases", default=str(ROOT / "data" / "cases.jsonl"))
	args = ap.parse_args()
	d = build(args)
	(ROOT / "docs").mkdir(exist_ok=True)
	(ROOT / "docs" / "cost-catch.svg").write_text(svg_chart(d["cv"], d["op"], themed=False) + "\n")
	body = md(d)
	(ROOT / "HOW_IT_WORKS.md").write_text(body + "\n")
	(ROOT / "docs" / "how-it-works.html").write_text(page(d, svg_chart(d["cv"], d["op"], themed=True), body) + "\n")
	sx, sy = d["op"]
	print(f"HOW_IT_WORKS.md, docs/: held out {sy:.1%} of bugs at {sx:.1%} of cost (n={d['n']})", file=sys.stderr)


if __name__ == "__main__":
	main()
