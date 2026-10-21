#!/usr/bin/env python3
"""Draw the results page from the results log (design 2.7): eval/results/index.html.

  eval/report.py            # also run by eval/score.py after every scoring run

Everything on the page comes from eval/results/log.jsonl (the latest result of
each predictor and split, and every round of a climb) and eval/manifest.json.
A new graph is one function over the log, added to SECTIONS.
"""
from __future__ import annotations

import html
import json
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "results" / "log.jsonl"
OUT = HERE / "results" / "index.html"
MANIFEST = HERE / "manifest.json"
TARGETS = [(0.90, 0.25), (0.90, 0.10), (0.95, 0.10)]
CURVES = {"ranked": "blind", "oracle": "oracle", "principles-on-blind": "onblind", "model": "model",
	  "model-llm": "modelllm", "hybrid": "hybrid"}
CURVE_LABELS = {"ranked": "patch-blind, best value first", "oracle": "oracle (the ceiling)",
		"principles-on-blind": "principles on a blind base", "model": "learned model",
		"model-llm": "learned model with LLM reading", "hybrid": "hybrid: principles first, the model adds"}
CURVE_LABEL_AT = {
	"ranked": lambda c: next(((a, b - 0.04) for a, b in c if a >= 0.62), c[-1]),
	"oracle": lambda c: (max(c, key=lambda p: p[1])[0] + 0.005, 0.17),
	"principles-on-blind": lambda c: (c[-1][0] + 0.01, c[-1][1] - 0.035),
	"model": lambda c: next(((a + 0.01, b - 0.05) for a, b in c if a >= 0.15), c[-1]),
	"model-llm": lambda c: next(((a + 0.01, b - 0.13) for a, b in c if a >= 0.42), c[-1]),
	"hybrid": lambda c: c[-1],
}
FAMILIES = {"full": "today", "trivial": "trivial", "edited": "edited", "table": "table"}


def esc(x) -> str:
	return html.escape(str(x))


def pct(x, digits=0) -> str:
	return "n/a" if x is None else f"{100 * x:.{digits}f}%"


def latest(log: list[dict]) -> "OrderedDict[tuple, dict]":
	out: OrderedDict[tuple, dict] = OrderedDict()
	for r in log:
		out[(r["predictor"], r["split"])] = r
	return out


def frontier(points: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
	"""Points no other point beats on both cost (lower) and catch (higher)."""
	pts = sorted(points)
	out, best = [], -1.0
	for x, y, n in pts:
		if y > best:
			out.append((x, y, n))
			best = y
	return out


def chart(rows: list[dict], key: str, title: str, w=640, h=420, small=False,
	  x_max: float = 1.0, y_min: float = 0.0) -> str:
	"""Share of today's catches (y) against share of today's cost (x), over
	[0, x_max] by [y_min, 1]."""
	pad_l, pad_b, pad_t, pad_r = (46, 36, 14, 14) if small else (56, 46, 18, 18)
	pw, ph = w - pad_l - pad_r, h - pad_t - pad_b
	X = lambda v: pad_l + pw * min(max(v, 0), x_max) / x_max  # noqa: E731
	Y = lambda v: pad_t + ph * (1 - (min(max(v, y_min), 1) - y_min) / (1 - y_min))  # noqa: E731
	s = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{esc(title)}" class="chart">']
	xt = [t for t in (0, 0.1, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.75, 1) if t <= x_max]
	if x_max == 1.0:
		xt = [0, 0.1, 0.25, 0.5, 0.75, 1]
	for gx in xt:
		s.append(f'<line x1="{X(gx):.1f}" y1="{Y(y_min):.1f}" x2="{X(gx):.1f}" y2="{Y(1):.1f}" class="grid"/>')
		s.append(f'<text x="{X(gx):.1f}" y="{Y(y_min) + 16:.1f}" class="tick" text-anchor="middle">{int(gx * 100)}%</text>')
	yt = [t for t in (0, 0.25, 0.4, 0.5, 0.6, 0.7, 0.75, 0.8, 0.9, 1) if t >= y_min]
	if y_min == 0:
		yt = [0, 0.25, 0.5, 0.75, 0.9, 1]
	for gy in yt:
		s.append(f'<line x1="{X(0):.1f}" y1="{Y(gy):.1f}" x2="{X(x_max):.1f}" y2="{Y(gy):.1f}" class="grid"/>')
		s.append(f'<text x="{X(0) - 6:.1f}" y="{Y(gy) + 4:.1f}" class="tick" text-anchor="end">{int(gy * 100)}%</text>')
	if not small:
		for i, (ty, tx) in enumerate(TARGETS):
			s.append(f'<rect x="{X(0):.1f}" y="{Y(1):.1f}" width="{X(tx) - X(0):.1f}" height="{Y(ty) - Y(1):.1f}" '
				 f'class="target t{i}"/>')
		s.append(f'<text x="{X(0.25) - 4:.1f}" y="{Y(0.9) - 6:.1f}" class="tlabel" text-anchor="end">90% at 25%</text>')
		s.append(f'<text x="{X(0.10) - 4:.1f}" y="{Y(0.95) - 6:.1f}" class="tlabel" text-anchor="end">95/10</text>')
		s.append(f'<text x="{X(x_max / 2):.1f}" y="{h - 6}" class="axis" text-anchor="middle">share of today\'s test cost</text>')
		s.append(f'<text transform="translate(14 {Y((1 + y_min) / 2):.1f}) rotate(-90)" class="axis" text-anchor="middle">'
			 f'share of today\'s bugs caught</text>')
	for r in rows:
		cls_name = CURVES.get(r.get("predictor"))
		if not cls_name or not r.get("curve"):
			continue
		cpts = sorted((p["cost_share"], p.get(key)) for p in r["curve"]
			      if p.get("cost_share") is not None and p.get(key) is not None)
		if len(cpts) > 1:
			s.append(f'<polyline data-series="{cls_name}" class="{cls_name}{" train" if r["split"] == "train" else ""}" '
				 'points="' + " ".join(f"{X(a):.1f},{Y(b):.1f}" for a, b in cpts if a <= x_max * 1.02) + '"/>')
	rows = [r for r in rows if r.get("predictor") not in CURVES]
	pts = []
	pos = {}
	for r in rows:
		x, y = r.get("cost_share"), r.get(key)
		if x is not None and y is not None:
			pos[(r["predictor"], r["split"])] = (X(x), Y(y))
	for (name, split), (px, py) in pos.items():
		if split == "train" and (name, "test") in pos:
			tx, ty = pos[(name, "test")]
			s.append(f'<line x1="{px:.1f}" y1="{py:.1f}" x2="{tx:.1f}" y2="{ty:.1f}" class="link train" data-series="points"/>')
	for r in rows:
		x, y = r.get("cost_share"), r.get(key)
		if x is None or y is None or x > x_max:
			continue
		fam = FAMILIES.get(r["predictor"].split("-")[0], "other")
		hollow = r["split"] == "train"
		ci = r.get("ci", {})
		if not small and key == "recall_vs_full" and not hollow:
			if "cost_share" in ci:
				a, b = ci["cost_share"]
				s.append(f'<line x1="{X(a):.1f}" y1="{Y(y):.1f}" x2="{X(b):.1f}" y2="{Y(y):.1f}" class="ci {fam}" data-series="points"/>')
			if "recall_vs_full" in ci:
				a, b = ci["recall_vs_full"]
				s.append(f'<line x1="{X(x):.1f}" y1="{Y(a):.1f}" x2="{X(x):.1f}" y2="{Y(b):.1f}" class="ci {fam}" data-series="points"/>')
		title = r.get("title") or r["predictor"]
		s.append(f'<circle data-series="points" cx="{X(x):.1f}" cy="{Y(y):.1f}" r="{3.5 if small else 5}" '
			 f'class="pt {fam}{" hollow train" if hollow else ""}"><title>{esc(title)} '
			 f'({"training set" if hollow else "test set"}): {pct(y)} of today\'s bugs at '
			 f'{pct(x)} of its cost</title></circle>')
		if not hollow:
			pts.append((x, y, title))
	if not small:
		placed: list[tuple[float, float, float, float]] = []
		for x, y, n in sorted(pts, key=lambda p: -p[1]):
			wtxt = 6.6 * len(n)
			cx, cy = X(x), Y(y)
			for dx, dy, anchor in ((9, 4, "start"), (-9, 4, "end"), (9, -10, "start"), (9, 17, "start"),
					       (-9, -10, "end"), (-9, 17, "end"), (9, 30, "start"), (-9, 30, "end")):
				x0 = cx + dx if anchor == "start" else cx + dx - wtxt
				box = (x0, cy + dy - 10, x0 + wtxt, cy + dy + 3)
				if box[0] < pad_l or box[2] > w - 2:
					continue
				if not any(box[0] < q[2] and q[0] < box[2] and box[1] < q[3] and q[1] < box[3] for q in placed):
					break
			placed.append(box)
			s.append(f'<text x="{cx + dx:.1f}" y="{cy + dy:.1f}" class="plabel" data-series="points" text-anchor="{anchor}">'
				 f'{esc(n)}</text>')
	s.append("</svg>")
	return "\n".join(s)


SERIES = [  # data-series, swatch colour, legend text
	("blind", "today", "patch-blind baseline: the same tests for every patch, best value first"),
	("oracle", "edited", "oracle: each patch's own cheapest catches, the ceiling"),
	("onblind", "other", "principles on a blind base"),
	("model", "table", "learned model: principles and history as features"),
	("modelllm", "fg", "the same with an LLM's reading of each patch as features too"),
	("hybrid", "accent", "hybrid: principles first, then the model's additions"),
	("points", "muted", "single selections (trivial, edited scripts, principles, directory history)"),
]


def gain_chart(rows: list[dict], w=640, h=320, x_max=0.6, split="test") -> str:
	"""Each test-set curve and point minus the patch-blind curve at the same cost:
	how far above the null line a selection is."""
	blind = next((r for r in rows if r["predictor"] == "ranked" and r["split"] == split), None)
	if not blind:
		return ""
	bp = sorted((p["cost_share"], p["recall_vs_full"]) for p in blind["curve"]
		    if p.get("cost_share") is not None and p.get("recall_vs_full") is not None)

	def base(x):
		lo = max(((a, b) for a, b in bp if a <= x), default=bp[0])
		hi = min(((a, b) for a, b in bp if a >= x), default=bp[-1])
		if hi[0] == lo[0]:
			return lo[1]
		return lo[1] + (hi[1] - lo[1]) * (x - lo[0]) / (hi[0] - lo[0])
	y_lo, y_hi = -0.15, 0.25
	pad_l, pad_b, pad_t, pad_r = 56, 46, 18, 18
	pw, ph = w - pad_l - pad_r, h - pad_t - pad_b
	X = lambda v: pad_l + pw * min(max(v, 0), x_max) / x_max  # noqa: E731
	Y = lambda v: pad_t + ph * (1 - (min(max(v, y_lo), y_hi) - y_lo) / (y_hi - y_lo))  # noqa: E731
	s = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="gain over the patch-blind baseline" class="chart">']
	for gx in (0, 0.1, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6):
		s.append(f'<line x1="{X(gx):.1f}" y1="{Y(y_lo):.1f}" x2="{X(gx):.1f}" y2="{Y(y_hi):.1f}" class="grid"/>')
		s.append(f'<text x="{X(gx):.1f}" y="{Y(y_lo) + 16:.1f}" class="tick" text-anchor="middle">{int(gx * 100)}%</text>')
	for gy in (-0.15, -0.1, -0.05, 0, 0.05, 0.1, 0.15, 0.2, 0.25):
		s.append(f'<line x1="{X(0):.1f}" y1="{Y(gy):.1f}" x2="{X(x_max):.1f}" y2="{Y(gy):.1f}" '
			 f'class="{"zero" if gy == 0 else "grid"}"/>')
		s.append(f'<text x="{X(0) - 6:.1f}" y="{Y(gy) + 4:.1f}" class="tick" text-anchor="end">{gy * 100:+.0f}</text>')
	s.append(f'<text x="{X(x_max) - 4:.1f}" y="{Y(0) - 6:.1f}" class="clabel blind" text-anchor="end">patch-blind</text>')
	s.append(f'<text x="{X(x_max / 2):.1f}" y="{h - 6}" class="axis" text-anchor="middle">share of today\'s test cost</text>')
	s.append(f'<text transform="translate(14 {Y(0.05):.1f}) rotate(-90)" class="axis" text-anchor="middle">'
		 'points above patch-blind</text>')
	lo_x = bp[0][0]
	for r in rows:
		if r["split"] != split or r["predictor"] in ("ranked", "oracle", "value", "full"):
			continue
		if r.get("curve"):
			ser = CURVES.get(r["predictor"])
			pts = sorted((p["cost_share"], p["recall_vs_full"] - base(p["cost_share"])) for p in r["curve"]
				     if p.get("cost_share") is not None and p.get("recall_vs_full") is not None
				     and lo_x <= p["cost_share"] <= x_max)
			if len(pts) > 1:
				s.append(f'<polyline data-series="{ser}" class="{ser}" points="'
					 + " ".join(f"{X(a):.1f},{Y(b):.1f}" for a, b in pts) + '"/>')
		elif r.get("cost_share") is not None and lo_x <= r["cost_share"] <= x_max:
			d = r["recall_vs_full"] - base(r["cost_share"])
			fam = FAMILIES.get(r["predictor"].split("-")[0], "other")
			title = r.get("title") or r["predictor"]
			s.append(f'<circle data-series="points" cx="{X(r["cost_share"]):.1f}" cy="{Y(d):.1f}" r="5" '
				 f'class="pt {fam}"><title>{esc(title)}: {d * 100:+.0f} points at {pct(r["cost_share"])}</title></circle>')
			s.append(f'<text data-series="points" x="{X(r["cost_share"]) + 8:.1f}" y="{Y(d) + 4:.1f}" '
				 f'class="plabel">{esc(title)}</text>')
	s.append("</svg>")
	return "\n".join(s)


def table(rows: list[dict]) -> str:
	head = ("Predictor", "Split", "Bugs vs today, per change", "Per bug", "Hard bugs", "All bugs caught",
		"Test cost", "Session h / build", "LLM $ / patch")
	out = ["<div class='scroll'><table><thead><tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr></thead><tbody>"]
	for r in rows:
		llm = r.get("llm") or {}
		per = (llm.get("dollars", 0) / r["sampled"]) if llm.get("dollars") and r.get("sampled") else None
		ci = r.get("ci", {}).get("recall_vs_full")
		vs = pct(r.get("recall_vs_full")) + (f" <span class='ci'>({pct(ci[0])} to {pct(ci[1])})</span>" if ci else "")
		out.append(f"<tr class='{r['split']}'><td>{esc(r.get('title') or r['predictor'])}</td><td>{'training set' if r['split'] == 'train' else 'test set'}</td>"
			   f"<td class='n'>{vs}</td><td class='n'>{pct(r.get('bug_recall_vs_full'))}</td>"
			   f"<td class='n'>{pct(r.get('hard_change_vs_full'))}</td><td class='n'>{pct(r.get('all_caught'))}</td>"
			   f"<td class='n'>{pct(r.get('cost_share'))}</td>"
			   f"<td class='n'>{r.get('hours_per_build', 0):.0f}</td>"
			   f"<td class='n'>{'' if per is None else f'{per:.3f}'}</td></tr>")
	out.append("</tbody></table></div>")
	return "\n".join(out)


def blind_table(r: dict | None) -> str:
	"""The ranked order: what to add first if every patch got the same tests."""
	if not r:
		return "<p class='muted'>Not scored yet.</p>"
	head = ("Step", "Adds", "Configuration", "Test cost", "Today's bugs caught", "Hard bugs")
	out = ["<p class='lede'>What every patch would run if the tests were chosen without reading it: "
	       "scripts and configurations ranked on the training set by bugs caught per second of session "
	       "time, scored here on the test set. A patch-aware selection is worth having only to the left "
	       "of this line.</p><div class='scroll'><table><thead><tr>"
	       + "".join(f"<th>{h}</th>" for h in head) + "</tr></thead><tbody>"]
	for p in r["curve"]:
		pick = p["pick"]
		if pick == "trivial":
			adds, cls = "trivial", "ldiskfs (review-ldiskfs)"
		elif pick.startswith("group:"):
			adds, cls = pick[6:], "its own group"
		else:
			adds, cls = pick.split("|")
		out.append(f"<tr><td class='n'>{p['n']}</td><td>{esc(adds)}</td><td>{esc(cls)}</td>"
			   f"<td class='n'>{pct(p['cost_share'])}</td><td class='n'>{pct(p['recall_vs_full'])}</td>"
			   f"<td class='n'>{pct(p.get('hard_change_vs_full'))}</td></tr>")
	out.append("</tbody></table></div>")
	return "\n".join(out)


MIN_HOURS = 0.25     # per build: less runs too rarely in the review matrix to judge


def costs_builds() -> int:
	try:
		return json.loads((HERE / "costs.json").read_text()).get("review_builds", 0)
	except OSError:
		return 0


def value_table(v: dict | None, builds: int) -> str:
	"""Every test set, by bugs caught per session hour: one number."""
	if not v:
		return "<p class='muted'>Not scored yet.</p>"
	rows = [r for r in v["rows"] if r["hours_per_build"] >= MIN_HOURS]
	out = [f"<p class='lede'>Bugs each test set caught over the {builds:,} review builds of the window, "
	       "per 1,000 session hours it ran. Sets under "
	       f"{MIN_HOURS:g} session hours per build are left out: too rare to judge.</p>"
	       "<div class='scroll'><table><thead><tr><th>Test set</th><th>Configuration</th>"
	       "<th>Session h / build</th><th>Bugs caught</th><th>Bugs per 1,000 h</th></tr></thead><tbody>"]
	for r in rows:
		script, cls = r["key"].split("|")
		if cls == "review-ldiskfs":
			cls = "review-ldiskfs (trivial)"
		elif cls.endswith("subtest-change"):
			cls = f"{cls} (edited subtests)"
		out.append(f"<tr><td>{esc(script)}</td><td>{esc(cls)}</td>"
			   f"<td class='n'>{r['hours_per_build']:.1f}</td><td class='n'>{r['bugs']}</td>"
			   f"<td class='n'><b>{r['per_1000h']:.2f}</b></td></tr>")
	out.append("</tbody></table></div>")
	return "\n".join(out)


def wowy_section() -> str:
	"""With or without you: each part of the stack switched off, or on alone."""
	f = HERE / "results" / "wowy.json"
	if not f.exists():
		return "<p class='muted'>Not run yet: <code>.venv/bin/python eval/wowy.py</code>.</p>"
	d = json.loads(f.read_text())
	pp = lambda v: "n/a" if v is None else f"{100 * v:+.1f}"  # noqa: E731
	ci = lambda c: "" if not c else f" <span class='ci'>({100 * c[0]:+.1f} to {100 * c[1]:+.1f})</span>"  # noqa: E731

	def verdict(r, without):
		c = r.get("diff_ci")
		if not c:
			return ""
		if without:
			return "keep" if c[1] < 0 else ("hurts" if c[0] > 0 else "unclear")
		return "helps" if c[0] > 0 else ("hurts" if c[1] < 0 else "unclear")
	out = [f"<p class='lede'>Each part of the stack switched off from the full model (\"without\"), or added "
	       "alone to a base of base rates and edited scripts (\"base +\"). Gain is the mean, over "
	       f"{int(d['grid'][0] * 100)} to {int(d['grid'][-1] * 100)}% cost, of the share of today's bugs caught "
	       "minus the patch-blind line's, in points; the difference is paired against the reference and "
	       f"bootstrapped over changes. {len(d['seeds'])} training seeds each. A part is worth keeping only if "
	       "removing it costs points with the interval clear of zero.</p>"]
	for sec, title in (("families", "Signals"), ("selector", "Selector"), ("models", "Model")):
		rows = d["sections"].get(sec, {})
		if not rows:
			continue
		recent = {r["name"]: r for r in rows.get("recent", [])}
		out.append(f"<h2>{title}</h2><div class='scroll'><table><thead><tr><th>Variant</th>"
			   "<th>Gain over blind</th><th>Against the reference</th><th>Verdict</th>"
			   "<th>at 20%</th><th>at 25%</th><th>Recent: gain</th></tr></thead><tbody>")
		for r in rows.get("test", []):
			v = verdict(r, r["name"].startswith("without"))
			rc = recent.get(r["name"], {})
			out.append(f"<tr class='{v}'><td>{esc(r['name'])}</td><td class='n'>{pp(r['gain'])}{ci(r['gain_ci'])}</td>"
				   f"<td class='n'>{pp(r['diff'])}{ci(r['diff_ci'])}</td><td>{v}</td>"
				   f"<td class='n'>{pp(r['at20'])}</td><td class='n'>{pp(r['at25'])}</td>"
				   f"<td class='n'>{pp(rc.get('gain'))}</td></tr>")
		out.append("</tbody></table></div>")
	rules = d["sections"].get("rules", {}).get("test", [])
	if rules:
		rules = sorted(rules, key=lambda r: -r["bugs_lost"])
		out.append("<h2>Each principle, removed</h2><p class='muted'>Bugs the principles alone would lose, and "
			   "cost they would save, without each rule; test set.</p><div class='scroll'><table><thead><tr>"
			   "<th>Rule</th><th>Bugs lost</th><th>Cost saved</th></tr></thead><tbody>")
		for r in rules:
			out.append(f"<tr><td>{esc(r['name'])}</td><td class='n'>{pp(r['bugs_lost'])}{ci(r['bugs_lost_ci'])}</td>"
				   f"<td class='n'>{pp(r['cost_saved'])}</td></tr>")
		out.append("</tbody></table></div>")
	return "\n".join(out)


def headline(test: list[dict], manifest: dict) -> str:
	cases = manifest.get("cases", [])
	n_test = sum(c["split"] == "test" and c["faulty"] for c in cases)
	n_recent = sum(c["split"] == "test" and c["faulty"] and c.get("recent") for c in cases)
	bugs = sum(c["bugs"] for c in cases if c["split"] == "test")
	full = next((r for r in test if r["predictor"] == "full"), None)
	cands = [r for r in test if r["predictor"] not in ("full",) and not r.get("curve")
		 and r.get("recall_vs_full") is not None]
	lines = []
	for ty, tx in TARGETS:
		ok = [r for r in cands if r["recall_vs_full"] >= ty and r["cost_share"] <= tx]
		if ok:
			b = max(ok, key=lambda r: r["recall_vs_full"])
			lines.append(f"<li><b>{int(ty * 100)}% at {int(tx * 100)}%:</b> met by {esc(b.get('title') or b['predictor'])}, "
				     f"{pct(b['recall_vs_full'])} at {pct(b['cost_share'])}.</li>")
		else:
			under = [r for r in cands if r["cost_share"] <= tx]
			b = max(under, key=lambda r: r["recall_vs_full"]) if under else None
			lines.append(f"<li><b>{int(ty * 100)}% at {int(tx * 100)}%:</b> not met yet."
				     + (f" Best within the cost: {esc(b.get('title') or b['predictor'])}, {pct(b['recall_vs_full'])} of "
					f"today's bugs at {pct(b['cost_share'])}." if b else "") + "</li>")
	hours = f"{full['actual_hours_per_build']:.0f}" if full else "?"
	every = manifest.get("every")
	held = (f"every {every}{'rd' if every == 3 else 'th'} month of the period held out" if every
		else f"changes first failing on or after {esc(manifest.get('cutoff', '?'))}")
	return (f"<p class='lede'>Backtest on <b>{n_test}</b> faulty patchsets the predictors never saw "
		f"({held}; {n_recent} of them after {esc(manifest.get('cutoff', '?'))}), holding <b>{bugs}</b> bugs. "
		f"Today every patch runs the whole review matrix, about <b>{hours}</b> session hours per build. "
		f"\"Share of today's bugs\" is what each selection would catch of what today's matrix catches, "
		f"with every change counting once.</p>"
		f"<ul class='targets'>{''.join(lines)}</ul>")


def climb(log: list[dict]) -> str:
	rounds = [r for r in log if str(r.get("label", "")).startswith("round")]
	if not rounds:
		return "<p class='muted'>No hillclimb rounds yet. Each round's train and test scores will be drawn here.</p>"
	return "<p class='muted'>" + esc(f"{len(rounds)} round results logged.") + "</p>"


CSS = """
:root { --bg: #f6f7f5; --panel: #ffffff; --fg: #1d2421; --muted: #5d6b65; --grid: #dfe4e1;
  --accent: #0f6e5c; --today: #8a4b12; --trivial: #6b6f76; --edited: #3b5bab; --table: #0f6e5c; --other: #7a3e8f;
  --target: #0f6e5c14; --target2: #0f6e5c22;
  --display: "Archivo", "Helvetica Neue", Arial, sans-serif; --body: "IBM Plex Sans", "Helvetica Neue", Arial, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, Menlo, monospace; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg: #121614; --panel: #1a201d; --fg: #e4ebe7;
  --muted: #9aa8a1; --grid: #2c3531; --accent: #4fc2a6; --today: #e0a060; --trivial: #a4a9b1; --edited: #8aa4f0;
  --table: #4fc2a6; --other: #c792dd; --target: #4fc2a614; --target2: #4fc2a626; color-scheme: dark } }
:root[data-theme="dark"] { --bg: #121614; --panel: #1a201d; --fg: #e4ebe7; --muted: #9aa8a1; --grid: #2c3531;
  --accent: #4fc2a6; --today: #e0a060; --trivial: #a4a9b1; --edited: #8aa4f0; --table: #4fc2a6; --other: #c792dd;
  --target: #4fc2a614; --target2: #4fc2a626; color-scheme: dark }
body { background: var(--bg); color: var(--fg); font: 15px/1.55 var(--body); }
main { max-width: 1040px; margin: 0 auto; padding-block: 28px 56px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));
  display: grid; gap: 28px; }
h1 { font: 700 2rem/1.15 var(--display); letter-spacing: -0.01em; margin: 0; text-wrap: balance; }
h2 { font: 600 1.2rem/1.3 var(--display); margin: 0 0 10px; text-wrap: balance; }
.eyebrow { font: 500 0.75rem var(--mono); letter-spacing: 0.08em; text-transform: uppercase; color: var(--accent); }
.lede { max-width: 70ch; margin: 0; }
.targets { margin: 0; padding-left: 20px; display: grid; gap: 4px; }
section { background: var(--panel); border: 1px solid var(--grid); border-radius: 6px; padding: 18px; min-width: 0; }
.chart { width: 100%; height: auto; max-width: 100%; display: block; }
.chart .grid { stroke: var(--grid); stroke-width: 1; }
.chart .tick, .chart .axis, .chart .tlabel, .chart .plabel { fill: var(--muted); font: 11px var(--mono); }
.chart .axis { font-size: 12px; }
.chart .plabel { fill: var(--fg); paint-order: stroke; stroke: var(--panel); stroke-width: 4px; stroke-linejoin: round; }
.chart .target { fill: var(--target); stroke: none; } .chart .target.t1, .chart .target.t2 { fill: var(--target2); }
.chart .pt { stroke-width: 2; } .chart .pt.hollow { fill: var(--panel) !important; }
.chart .today { fill: var(--today); stroke: var(--today); } .chart .trivial { fill: var(--trivial); stroke: var(--trivial); }
.chart .edited { fill: var(--edited); stroke: var(--edited); } .chart .table { fill: var(--table); stroke: var(--table); }
.chart .other { fill: var(--other); stroke: var(--other); }
.chart .ci { stroke-width: 1.5; opacity: 0.55; fill: none; }
.chart .blind { fill: none; stroke: var(--today); stroke-width: 2; }
.chart .blind.train { stroke-dasharray: 2 4; stroke-width: 1.5; opacity: 0.8; }
.chart .oracle { fill: none; stroke: var(--edited); stroke-width: 2; }
.chart .oracle.train { stroke-dasharray: 2 4; stroke-width: 1.5; opacity: 0.8; }
.chart .onblind { fill: none; stroke: var(--other); stroke-width: 2; }
.chart .onblind.train { stroke-dasharray: 2 4; stroke-width: 1.5; opacity: 0.8; }
.chart .model { fill: none; stroke: var(--table); stroke-width: 2.5; }
.chart .model.train { stroke-dasharray: 2 4; stroke-width: 1.5; opacity: 0.8; }
.chart .clabel.model { fill: var(--table); }
.chart .modelllm { fill: none; stroke: var(--fg); stroke-width: 2.5; }
.chart .modelllm.train { stroke-dasharray: 2 4; stroke-width: 1.5; opacity: 0.8; }
.chart .clabel.modelllm { fill: var(--fg); }
.chart .hybrid { fill: none; stroke: var(--accent); stroke-width: 3; stroke-dasharray: 6 2; }
.chart .hybrid.train { stroke-dasharray: 2 4; stroke-width: 1.5; opacity: 0.8; }
.chart .clabel { font: 11px var(--mono); paint-order: stroke; stroke: var(--panel); stroke-width: 4px; }
.chart .clabel.blind { fill: var(--today); } .chart .clabel.oracle { fill: var(--edited); }
.chart .clabel.onblind { fill: var(--other); }
.chart .link { stroke: var(--muted); stroke-width: 1; stroke-dasharray: 1 3; opacity: 0.7; }
.chart .frontier { fill: none; stroke: var(--accent); stroke-width: 1.5; stroke-dasharray: 4 3; }
.smalls { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px; }
.smalls figure { margin: 0; min-width: 0; } .smalls figcaption { font: 600 0.85rem var(--display); margin-bottom: 4px; }
.legend { display: flex; flex-wrap: wrap; gap: 14px; font: 12px var(--mono); color: var(--muted); margin-top: 8px; }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; vertical-align: -1px; }
.legend i.line.solid { background: var(--fg); } .legend i.line.dotted { background: none;
  border-top: 2px dotted var(--fg); height: 0; }
.toggle { display: inline-flex; gap: 6px; align-items: center; font: 13px var(--body); margin-top: 10px;
  cursor: pointer; }
main:not(.show-train) .train { display: none; }
.legend i.dot { background: var(--fg); }
.toggles { display: flex; flex-wrap: wrap; gap: 6px 16px; margin-top: 10px; }
.toggles .toggle { margin-top: 0; font-size: 12px; }
.toggles i.swatch { display: inline-block; width: 16px; height: 3px; vertical-align: 3px; }
.chart .zero { stroke: var(--today); stroke-width: 1.5; } .legend i.line { width: 16px; height: 2px; border-radius: 0; background: var(--today); vertical-align: 3px; } .legend i.ring { border: 2px solid var(--fg); width: 6px; height: 6px; }
dl.key { display: grid; grid-template-columns: max-content 1fr; gap: 4px 14px; margin: 14px 0 0; font-size: 13px; }
dl.key dt { font-weight: 600; } dl.key dd { margin: 0; color: var(--muted); min-width: 0; }
@media (max-width: 560px) { dl.key { grid-template-columns: 1fr; } dl.key dd { margin-bottom: 6px; } }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { padding: 6px 10px; border-bottom: 1px solid var(--grid); text-align: left; white-space: nowrap; }
th { font: 600 0.72rem var(--mono); text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); }
td.n { font-variant-numeric: tabular-nums; font-family: var(--mono); }
tr.train td { color: var(--muted); } tr.poor td { color: var(--today); }
tr.keep td:nth-child(4), tr.helps td:nth-child(4) { color: var(--table); font-weight: 600; }
tr.hurts td:nth-child(4) { color: var(--today); font-weight: 600; } .ci { color: var(--muted); font-size: 11px; }
.muted { color: var(--muted); } code { font: 0.9em var(--mono); }
ul.notes { margin: 0; padding-left: 20px; display: grid; gap: 6px; max-width: 80ch; }
"""

NOTES = [
	"Bugs: a patchset's patch faults grouped by error signature across scripts; a failure over most of "
	"the scripts its build ran counts as one cascade; bugs no selection could tell apart (same scripts, "
	"same configurations) count as one. The label audit (eval/audit) checks the grouping and the labels.",
	"Each change counts once: a change whose patchsets fail hundreds of tests weighs no more than one "
	"that fails one. The per-bug figure is in the table.",
	"A selection catches a bug when it runs a script in a configuration where that script failed with "
	"the bug in the patchset's own build, with the chance the build showed (failed runs over runs). "
	"A configuration where the script never ran counts as not caught.",
	"Trivial (review-ldiskfs) and the subtest-change sessions run for every selection and are charged to it.",
	"Cost is session time on a random sample of all review patchsets: 23 minutes of setup per added "
	"session plus each script's mean run time in its configuration.",
	"Single-MDT, Ubuntu and SLES client configurations are asked for with keys no custom session has "
	"used yet; SELinux, shared-key and IPv6 configurations can only be asked for as whole groups.",
]


def main() -> None:
	log = [json.loads(x) for x in LOG.open()] if LOG.exists() else []
	manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
	last = latest(log)
	test = [r for (p, s), r in last.items() if s == "test"]
	recent = [r for (p, s), r in last.items() if s == "recent"]
	train = [r for (p, s), r in last.items() if s == "train"]
	blind = next((r for r in test if r["predictor"] == "ranked"), None)
	test = [r for r in test if r["predictor"] not in ("oracle", "principles-on-blind")] + \
		[r for r in test if r["predictor"] in ("oracle", "principles-on-blind")]
	val = last.get(("value", "all"))
	both = test + train
	legend = ("<div class='legend'><span><i class='dot'></i><i class='line solid'></i>test set: filled points, "
		  "solid lines (the results)</span>"
		  "<span class='train'><i class='ring'></i><i class='line dotted'></i>training set: hollow points, "
		  "dotted lines (the data the rules were fitted to; the gap to the test set is overfitting and drift)"
		  "</span><span>whiskers: 95% interval</span></div>"
		  "<div class='toggles'><label class='toggle'><input type='checkbox' id='show-train'> training set</label>"
		  + "".join(f"<label class='toggle'><input type='checkbox' data-toggle='{ser}' checked>"
			    f"<i class='swatch' style='background:var(--{col})'></i>{esc(text)}</label>"
			    for ser, col, text in SERIES) + "</div>")
	seen = set()
	key_rows = []
	for r in test:
		if r["predictor"] in seen:
			continue
		seen.add(r["predictor"])
		key_rows.append(f"<dt>{esc(r.get('title') or r['predictor'])}</dt><dd>{esc(r.get('about') or '')}</dd>")
	legend += f"<dl class='key'>{''.join(key_rows)}</dl>"
	smalls = "".join(f"<figure><figcaption>{t}</figcaption>{chart(both, k, t, 320, 240, True)}</figure>"
			 for k, t in (("easy_vs_full", "Easy bugs (in tests the patch edits)"),
				      ("hard_change_vs_full", "Hard bugs"), ("series_vs_full", "Bugs also on the series"),
				      ("all_caught", "Patchsets with every bug caught")))
	page = f"""<title>Lustre Test Selection</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@600;700&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;600&display=swap">
<style>{CSS}</style>
<main id="page">
<header style="display:grid;gap:8px">
<div class="eyebrow">Lustre CI: trivial plus predicted Test-Parameters</div>
<h1>How much testing does a patch need?</h1>
{headline(test, manifest)}
</header>
<section><h2>Bugs caught against test cost</h2>
<p class="muted">Cost up to 60% and bugs from 40% shown; today's matrix is 100% at 100%.</p>
{chart(both, "recall_vs_full", "bugs caught against cost", 720, 480, x_max=0.6, y_min=0.4)}
{legend}</section>
<section><h2>Gain over the patch-blind baseline</h2>
<p class="muted">Each selection's share of today's bugs minus what the same tests for every patch catch at
the same cost, on the test set. Above zero, reading the patch is paying for itself.</p>
{gain_chart(test)}
<h2>Going forward: the test months after {esc(manifest.get("cutoff", "?"))}</h2>
<p class="muted">The same, on the held-out months after the cutoff only: predicting later patches from earlier
ones, and after the LLM's training data.</p>
{gain_chart(recent, split="recent")}</section>
<section><h2>What each part is worth</h2>{wowy_section()}</section>
<section><h2>By kind of bug</h2><div class="smalls">{smalls}</div></section>
<section><h2>Every predictor</h2>{table([r for r in test + train if not r.get("curve") and r["predictor"] != "value"])}</section>
<section><h2>Bugs per session hour, by test set</h2>{value_table(val, costs_builds())}</section>
<section><h2>The patch-blind baseline</h2>{blind_table(blind)}</section>
<section><h2>The climb</h2>{climb(log)}</section>
<section><h2>How this is measured</h2><ul class="notes">{''.join(f'<li>{esc(n)}</li>' for n in NOTES)}</ul>
<p class="muted">Regenerate: <code>harvest.py fetch|sessions|patches|sample</code>, then
<code>eval/cases.py</code>, <code>eval/costs.py</code>, <code>eval/score.py</code> (which redraws this page).
Results log: <code>eval/results/log.jsonl</code>.</p></section>
</main>
<script>
(function () {{
  var box = document.getElementById("show-train"), page = document.getElementById("page");
  function apply() {{ page.classList.toggle("show-train", box.checked); }}
  try {{ box.checked = localStorage.getItem("show-train") === "1"; }} catch (e) {{}}
  box.addEventListener("change", function () {{
    apply();
    try {{ localStorage.setItem("show-train", box.checked ? "1" : "0"); }} catch (e) {{}}
  }});
  apply();
  document.querySelectorAll("[data-toggle]").forEach(function (cb) {{
    cb.addEventListener("change", function () {{
      document.querySelectorAll('[data-series="' + cb.dataset.toggle + '"]').forEach(function (el) {{
        el.style.display = cb.checked ? "" : "none";
      }});
    }});
  }});
}})();
</script>
"""
	OUT.parent.mkdir(parents=True, exist_ok=True)
	OUT.write_text(page)


if __name__ == "__main__":
	main()
