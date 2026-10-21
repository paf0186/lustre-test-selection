#!/usr/bin/env python3
"""Test selection for one Gerrit change: the Test-Parameters to run on top of
`trivial`, and how risky the patch is, each with its reasons.

  ./testsel.py train                  # model/: blind order, budget, risk scorer, from data/cases.jsonl
  ./testsel.py 64955                  # the change's current patchset
  ./testsel.py 64955 -r 94 --json

Selection is the simple candidate (eval/predictors/simple.py, EXPERIMENTS.md E4
and E6): `trivial`; the test sets belonging to the code areas the patch changes
(must); the edited scripts, or just the edited tests when only tests change; the
first n test sets of the patch-blind order (n set by the budget); and whatever
caught a bug on this change's earlier patchsets.  Risk is signals' `faulty`
scorer, calibrated to the population, with the signals that held up.

Reads Gerrit (anonymously) and Rainshadow (~/.config/rainshadow/cli.env).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pickle
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "eval"))
sys.path.insert(0, str(HERE))
import harvest  # noqa: E402
from classes import CLASSES, class_of  # noqa: E402
from links import changed_functions  # noqa: E402
from predictors import ranked  # noqa: E402
from predictors.simple import Simple  # noqa: E402

MODEL = HERE / "model"
TIERS = ((0.85, "high"), (0.5, "medium"), (0.0, "low"))
RISK_SIGNALS_OFF = {"noise", "llm_overall", "llm_breadth"}   # unproven in the lockbox (E5); cost money


def gerrit(path: str) -> dict:
	req = urllib.request.Request(f"{harvest.GERRIT}{path}", headers={"User-Agent": harvest.USER_AGENT})
	with urllib.request.urlopen(req, timeout=60) as r:
		return json.loads(r.read().decode().split("\n", 1)[1])


def train(args) -> None:
	from grade import grade

	from signals import registry
	from signals.score import Scorer
	cases = [json.loads(x) for x in open(args.cases)]
	costs = json.loads((HERE / "eval" / "costs.json").read_text())
	order = ranked.rank(cases, costs, CLASSES)
	sampled = [c for c in cases if c["sampled"]]
	n = 0
	for k in range(len(order) + 1):
		text = ranked.prediction(order, k, CLASSES)
		share = sum(grade(c, text, costs)["cost_s"] for c in sampled) / sum(c["cost"]["actual"] for c in sampled)
		if share > args.budget:
			break
		n = k
	signals = [s for s in registry() if s not in RISK_SIGNALS_OFF and registry()[s].cost != "gpu"]
	risk = Scorer("faulty", signals).fit(cases)
	MODEL.mkdir(exist_ok=True)
	(MODEL / "order.json").write_text(json.dumps(order) + "\n")
	with open(MODEL / "risk.pkl", "wb") as f:
		pickle.dump(risk, f)
	meta = {"trained": dt.date.today().isoformat(), "cases": len(cases), "budget": args.budget, "n": n,
		"risk_signals": signals}
	(MODEL / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
	print(f"order of {len(order)} test sets, base n={n} for a {args.budget:.0%} budget; risk on "
	      f"{len(signals)} signals; {len(cases)} cases", file=sys.stderr)


def patch_input(change: int, patchset: int | None, tree: Path) -> dict:
	cl = harvest.Client()
	if patchset is None:
		patchset = gerrit(f"/changes/{change}/?o=CURRENT_REVISION")["revisions"]
		patchset = next(iter(patchset.values()))["_number"]
	cl.call("/sync/patches", body={"keys": [[change, patchset]], "limit": 5, "seconds": 60})
	rows = cl.call("/export/patchsets", body={"keys": [[change, patchset]]})["rows"]
	p = rows[0] if rows else {}
	message, diff = harvest.split_patch(harvest.gerrit_patch(change, patchset))
	# what CI caught on the change's earlier patchsets, over the last half year
	# (Rainshadow answers at most two months a request)
	faults = []
	end = dt.date.today()
	for _ in range(3):
		start = end - dt.timedelta(days=60)
		faults += cl.call("/export/patch-faults", {"start": start.isoformat(), "end": end.isoformat(),
							   "change": change, "patches": 0})["faults"]
		end = start - dt.timedelta(days=1)
	earlier: dict[int, set[str]] = {}
	for f in faults:
		if f["patchset"] < patchset:
			c = class_of({"test_group": f.get("test_group"), "server_fs": f.get("fs"),
				      "client_distro": f.get("distro"), "test_name": ""})
			if c and f.get("test_group") != "custom":
				earlier.setdefault(f["patchset"], set()).add(f"{f['suite']}|{c}")
	history = {"caught": sorted(set().union(*earlier.values())) if earlier else [],
		   "last_caught": sorted(earlier[max(earlier)]) if earlier else [], "faulty_patchsets": len(earlier)}
	return {"key": f"{change}/{patchset}", "subject": p.get("subject"), "message": message, "diff": diff,
		"files": p.get("files"), "scripts": p.get("scripts"), "test_parameters": p.get("test_parameters"),
		"gerrit_kind": p.get("kind"), "series": p.get("series"), "series_size": p.get("series_size"),
		"functions": changed_functions(diff, tree), "history": history}


def select(args) -> None:
	meta = json.loads((MODEL / "meta.json").read_text())
	order = json.loads((MODEL / "order.json").read_text())
	with open(MODEL / "risk.pkl", "rb") as f:
		risk = pickle.load(f)
	inp = patch_input(args.change, args.patchset, Path(args.tree).expanduser())
	sel = Simple(order, meta["n"], edited=args.edited, use_must=True).predict(inp)
	r = risk.score(inp)
	tier = next(name for cut, name in TIERS if r["percentile"] >= cut)
	out = {"change": args.change, "patch": inp["key"], "subject": inp["subject"],
	       "test_parameters": ["Test-Parameters: " + ln for ln in sel["text"].splitlines()],
	       "why": sel["why"] + [f"the first {meta['n']} test sets of the patch-blind order "
				    f"({meta['budget']:.0%} budget)"] +
	       ([f"caught on earlier patchsets: {', '.join(inp['history']['caught'])}"]
		if inp["history"]["caught"] else []),
	       "risk": {"score": round(r["score"], 3), "percentile": round(r["percentile"], 2), "tier": tier,
			"base_rate": round(r["base_rate"], 3),
			"reasons": list(dict.fromkeys(x["why"] or x["column"] for x in r["reasons"])),
			"sensitive_areas": r["signals"].get("areas", {}).get("reason")},
	       "model": meta}
	if args.json:
		print(json.dumps(out, indent=1))
		return
	print(f"{out['patch']}  {out['subject'] or ''}")
	for ln in out["test_parameters"]:
		print(f"  {ln}")
	print("why:")
	for w in out["why"]:
		print(f"  - {w}")
	rk = out["risk"]
	print(f"risk: {rk['tier']} ({rk['score']:.0%} chance of a bug CI would catch; base {rk['base_rate']:.0%}; "
	      f"riskier than {rk['percentile']:.0%} of patches)")
	for w in rk["reasons"]:
		print(f"  - {w}")
	if rk["sensitive_areas"]:
		print(f"  sensitive areas: {rk['sensitive_areas']}")


def main() -> None:
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("change", help="a Gerrit change number, or 'train'")
	ap.add_argument("-r", "--patchset", type=int)
	ap.add_argument("--json", action="store_true")
	ap.add_argument("--edited", choices=["only", "none", "whole"], default="only")
	ap.add_argument("--tree", default=str(Path.home() / "lustre-release"))
	ap.add_argument("--cases", default=str(HERE / "data" / "cases.jsonl"))
	ap.add_argument("--budget", type=float, default=0.25, help="train: the blind base's share of today's cost")
	args = ap.parse_args()
	if args.change == "train":
		train(args)
	else:
		args.change = int(args.change)
		select(args)


if __name__ == "__main__":
	main()
