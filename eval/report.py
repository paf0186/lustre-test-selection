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


def chart(rows: list[dict], key: str, title: str, w=640, h=420, small=False) -> str:
	"""Share of today's catches (y) against share of today's cost (x)."""
	pad_l, pad_b, pad_t, pad_r = (46, 36, 14, 14) if small else (56, 46, 18, 18)
	pw, ph = w - pad_l - pad_r, h - pad_t - pad_b
	X = lambda v: pad_l + pw * min(max(v, 0), 1)  # noqa: E731
	Y = lambda v: pad_t + ph * (1 - min(max(v, 0), 1))  # noqa: E731
	s = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{esc(title)}" class="chart">']
	for gx in (0, 0.1, 0.25, 0.5, 0.75, 1):
		s.append(f'<line x1="{X(gx):.1f}" y1="{Y(0):.1f}" x2="{X(gx):.1f}" y2="{Y(1):.1f}" class="grid"/>')
		s.append(f'<text x="{X(gx):.1f}" y="{Y(0) + 16:.1f}" class="tick" text-anchor="middle">{int(gx * 100)}%</text>')
	for gy in (0, 0.25, 0.5, 0.75, 0.9, 1):
		s.append(f'<line x1="{X(0):.1f}" y1="{Y(gy):.1f}" x2="{X(1):.1f}" y2="{Y(gy):.1f}" class="grid"/>')
		s.append(f'<text x="{X(0) - 6:.1f}" y="{Y(gy) + 4:.1f}" class="tick" text-anchor="end">{int(gy * 100)}%</text>')
	if not small:
		for i, (ty, tx) in enumerate(TARGETS):
			s.append(f'<rect x="{X(0):.1f}" y="{Y(1):.1f}" width="{X(tx) - X(0):.1f}" height="{Y(ty) - Y(1):.1f}" '
				 f'class="target t{i}"/>')
		s.append(f'<text x="{X(0.25) - 4:.1f}" y="{Y(0.9) - 6:.1f}" class="tlabel" text-anchor="end">90% at 25%</text>')
		s.append(f'<text x="{X(0.10) - 4:.1f}" y="{Y(0.95) - 6:.1f}" class="tlabel" text-anchor="end">95/10</text>')
		s.append(f'<text x="{X(0.5):.1f}" y="{h - 6}" class="axis" text-anchor="middle">share of today\'s test cost</text>')
		s.append(f'<text transform="translate(14 {Y(0.5):.1f}) rotate(-90)" class="axis" text-anchor="middle">'
			 f'share of today\'s bugs caught</text>')
	pts = []
	for r in rows:
		x, y = r.get("cost_share"), r.get(key)
		if x is None or y is None:
			continue
		fam = FAMILIES.get(r["predictor"].split("-")[0], "other")
		hollow = r["split"] == "train"
		ci = r.get("ci", {})
		if not small and key == "recall_vs_full" and not hollow:
			if "cost_share" in ci:
				a, b = ci["cost_share"]
				s.append(f'<line x1="{X(a):.1f}" y1="{Y(y):.1f}" x2="{X(b):.1f}" y2="{Y(y):.1f}" class="ci {fam}"/>')
			if "recall_vs_full" in ci:
				a, b = ci["recall_vs_full"]
				s.append(f'<line x1="{X(x):.1f}" y1="{Y(a):.1f}" x2="{X(x):.1f}" y2="{Y(b):.1f}" class="ci {fam}"/>')
		s.append(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="{3.5 if small else 5}" '
			 f'class="pt {fam}{" hollow" if hollow else ""}"><title>{esc(r["predictor"])} ({r["split"]}): '
			 f'{pct(y)} at {pct(x)}</title></circle>')
		if not hollow:
			pts.append((x, y, r["predictor"]))
	fr = frontier(pts)
	if len(fr) > 1:
		s.append('<polyline class="frontier" points="' + " ".join(f"{X(a):.1f},{Y(b):.1f}" for a, b, _ in fr) + '"/>')
	if not small:
		last_y = None
		for x, y, n in sorted(fr, key=lambda p: -p[1]):
			ly = Y(y) + 4
			if last_y is not None and ly - last_y < 14:
				ly = last_y + 14
			last_y = ly
			left = X(x) > pad_l + 0.8 * pw
			s.append(f'<text x="{X(x) + (-9 if left else 9):.1f}" y="{ly:.1f}" class="plabel" '
				 f'text-anchor="{"end" if left else "start"}">{esc("today" if n == "full" else n)}</text>')
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
		out.append(f"<tr class='{r['split']}'><td>{esc(r['predictor'])}</td><td>{r['split']}</td>"
			   f"<td class='n'>{vs}</td><td class='n'>{pct(r.get('bug_recall_vs_full'))}</td>"
			   f"<td class='n'>{pct(r.get('hard_change_vs_full'))}</td><td class='n'>{pct(r.get('all_caught'))}</td>"
			   f"<td class='n'>{pct(r.get('cost_share'))}</td>"
			   f"<td class='n'>{r.get('hours_per_build', 0):.0f}</td>"
			   f"<td class='n'>{'' if per is None else f'{per:.3f}'}</td></tr>")
	out.append("</tbody></table></div>")
	return "\n".join(out)


def headline(test: list[dict], manifest: dict) -> str:
	cases = manifest.get("cases", [])
	n_test = sum(c["split"] == "test" and c["faulty"] for c in cases)
	bugs = sum(c["bugs"] for c in cases if c["split"] == "test")
	full = next((r for r in test if r["predictor"] == "full"), None)
	cands = [r for r in test if r["predictor"] != "full" and r.get("recall_vs_full") is not None]
	lines = []
	for ty, tx in TARGETS:
		ok = [r for r in cands if r["recall_vs_full"] >= ty and r["cost_share"] <= tx]
		if ok:
			b = max(ok, key=lambda r: r["recall_vs_full"])
			lines.append(f"<li><b>{int(ty * 100)}% at {int(tx * 100)}%:</b> met by {esc(b['predictor'])}, "
				     f"{pct(b['recall_vs_full'])} at {pct(b['cost_share'])}.</li>")
		else:
			under = [r for r in cands if r["cost_share"] <= tx]
			b = max(under, key=lambda r: r["recall_vs_full"]) if under else None
			lines.append(f"<li><b>{int(ty * 100)}% at {int(tx * 100)}%:</b> not met yet."
				     + (f" Best within the cost: {esc(b['predictor'])}, {pct(b['recall_vs_full'])} of "
					f"today's bugs at {pct(b['cost_share'])}." if b else "") + "</li>")
	hours = f"{full['actual_hours_per_build']:.0f}" if full else "?"
	return (f"<p class='lede'>Backtest on <b>{n_test}</b> faulty patchsets the predictors never saw "
		f"(changes first failing on or after {esc(manifest.get('cutoff', '?'))}), holding <b>{bugs}</b> bugs. "
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
.chart .axis { font-size: 12px; } .chart .plabel { fill: var(--fg); }
.chart .target { fill: var(--target); stroke: none; } .chart .target.t1, .chart .target.t2 { fill: var(--target2); }
.chart .pt { stroke-width: 2; } .chart .pt.hollow { fill: var(--panel) !important; }
.chart .today { fill: var(--today); stroke: var(--today); } .chart .trivial { fill: var(--trivial); stroke: var(--trivial); }
.chart .edited { fill: var(--edited); stroke: var(--edited); } .chart .table { fill: var(--table); stroke: var(--table); }
.chart .other { fill: var(--other); stroke: var(--other); }
.chart .ci { stroke-width: 1.5; opacity: 0.55; fill: none; }
.chart .frontier { fill: none; stroke: var(--accent); stroke-width: 1.5; stroke-dasharray: 4 3; }
.smalls { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px; }
.smalls figure { margin: 0; min-width: 0; } .smalls figcaption { font: 600 0.85rem var(--display); margin-bottom: 4px; }
.legend { display: flex; flex-wrap: wrap; gap: 14px; font: 12px var(--mono); color: var(--muted); margin-top: 8px; }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; vertical-align: -1px; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { padding: 6px 10px; border-bottom: 1px solid var(--grid); text-align: left; white-space: nowrap; }
th { font: 600 0.72rem var(--mono); text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); }
td.n { font-variant-numeric: tabular-nums; font-family: var(--mono); }
tr.train td { color: var(--muted); } .ci { color: var(--muted); font-size: 11px; }
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
	train = [r for (p, s), r in last.items() if s == "train"]
	both = test + train
	legend = ("<div class='legend'>" + "".join(f"<span><i style='background:var(--{c})'></i>{n}</span>" for n, c in
		  (("today's matrix", "today"), ("trivial", "trivial"), ("edited scripts", "edited"),
		   ("learned table", "table"))) + "<span>filled: test, hollow: train</span></div>")
	smalls = "".join(f"<figure><figcaption>{t}</figcaption>{chart(both, k, t, 320, 240, True)}</figure>"
			 for k, t in (("easy_vs_full", "Easy bugs (in tests the patch edits)"),
				      ("hard_change_vs_full", "Hard bugs"), ("series_vs_full", "Bugs also on the series"),
				      ("all_caught", "Patchsets with every bug caught")))
	page = f"""<title>Lustre Test Selection</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@600;700&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;600&display=swap">
<style>{CSS}</style>
<main>
<header style="display:grid;gap:8px">
<div class="eyebrow">Lustre CI: trivial plus predicted Test-Parameters</div>
<h1>How much testing does a patch need?</h1>
{headline(test, manifest)}
</header>
<section><h2>Bugs caught against test cost, on unseen patches</h2>
{chart(both, "recall_vs_full", "bugs caught against cost")}
{legend}</section>
<section><h2>By kind of bug</h2><div class="smalls">{smalls}</div></section>
<section><h2>Every predictor</h2>{table(test + train)}</section>
<section><h2>The climb</h2>{climb(log)}</section>
<section><h2>How this is measured</h2><ul class="notes">{''.join(f'<li>{esc(n)}</li>' for n in NOTES)}</ul>
<p class="muted">Regenerate: <code>harvest.py fetch|sessions|patches|sample</code>, then
<code>eval/cases.py</code>, <code>eval/costs.py</code>, <code>eval/score.py</code> (which redraws this page).
Results log: <code>eval/results/log.jsonl</code>.</p></section>
</main>
"""
	OUT.parent.mkdir(parents=True, exist_ok=True)
	OUT.write_text(page)


if __name__ == "__main__":
	main()
