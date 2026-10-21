"""Patch signals from the command line.

  python3 -m signals list                         # signals and targets
  python3 -m signals score 64955/94               # a case's signals, scores and reasons (JSON)
  python3 -m signals evaluate --target faulty     # AUC and lift, cross-validated
  python3 -m signals evaluate --target faulty --mode lockbox --signals lines_changed,areas
  python3 -m signals wowy --target faulty         # each signal without, and the noise control

Trains on data/cases.jsonl's training months; `lockbox` scores the held-out test
months, `cv` two folds of the training months (decisions are made on cv).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import registry
from .score import Scorer, evaluate, paired_auc_diff
from .targets import TARGETS

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OFF = {"noise"}


def month(day: str) -> int:
	return int(day[:4]) * 12 + int(day[5:7])


def load(path):
	return [json.loads(x) for x in open(path)]


def folds(cases, mode):
	train = [c for c in cases if c["split"] == "train"]
	if mode == "lockbox":
		return [(train, [c for c in cases if c["split"] == "test"])]
	return [([c for c in train if month(c["day"]) % 2 != f], [c for c in train if month(c["day"]) % 2 == f])
		for f in (0, 1)]


def default_signals():
	return [n for n, s in registry().items() if n not in DEFAULT_OFF and s.cost != "gpu"]


def fmt(r):
	ci = lambda c: f"({c[0]:.3f} to {c[1]:.3f})" if c else ""  # noqa: E731
	return (f"AUC {r['auc']:.3f} {ci(r['auc_ci'])}; riskiest fifth {r['riskiest_fifth']:.2f}x "
		f"{ci(r['riskiest_ci'])}; safest fifth {r['safest_fifth']:.2f}x {ci(r['safest_ci'])}; "
		f"{r['patches']} patches, {r['positives']} positive")


def main() -> None:
	ap = argparse.ArgumentParser(prog="signals", description=__doc__,
				     formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("--cases", default=str(ROOT / "data" / "cases.jsonl"))
	sub = ap.add_subparsers(dest="cmd", required=True)
	sub.add_parser("list")
	sc = sub.add_parser("score")
	sc.add_argument("key", help="a case key, CHANGE/PATCHSET")
	sc.add_argument("--target", action="append")
	for name in ("evaluate", "wowy"):
		p = sub.add_parser(name)
		p.add_argument("--target", default="faulty", choices=sorted(TARGETS))
		p.add_argument("--signals", help="comma separated (default: all but gpu and noise)")
		p.add_argument("--mode", choices=["cv", "lockbox"], default="cv")
	args = ap.parse_args()

	if args.cmd == "list":
		for n, s in sorted(registry().items()):
			print(f"{n:22s} {s.kind:9s} {s.cost:7s} {','.join(s.needs) or '-':22s} {s.doc}")
		print()
		for n, (_, doc) in sorted(TARGETS.items()):
			print(f"target {n:15s} {doc}")
		return
	cases = load(args.cases)
	if args.cmd == "score":
		case = next(c for c in cases if c["key"] == args.key)
		train = [c for c in cases if c["split"] == "train" and c["change"] != case["change"]]
		out = {"key": args.key}
		for t in args.target or ["faulty"]:
			r = Scorer(t, default_signals()).fit(train).score(case["input"])
			out.setdefault("signals", r.pop("signals"))
			out[t] = r
		print(json.dumps(out, indent=1))
		return
	sigs = args.signals.split(",") if args.signals else default_signals()
	fs = folds(cases, args.mode)
	ref = evaluate(cases, args.target, sigs, fs)
	print(f"{args.target} [{args.mode}] all ({len(sigs)} signals): {fmt(ref)}", file=sys.stderr)
	if args.cmd == "wowy":
		for s in sigs + ["+noise"]:
			use = sigs + ["noise"] if s == "+noise" else [x for x in sigs if x != s]
			r = evaluate(cases, args.target, use, fs)
			d, ci = paired_auc_diff(r, ref)
			verdict = ("keep" if ci[1] < 0 else "remove (it hurts)" if ci[0] > 0 else "unclear") \
				if s != "+noise" else ("ok" if ci[0] <= 0 <= ci[1] else "NOISE CONTROL FAILED")
			label = "with noise (control)" if s == "+noise" else f"without {s}"
			print(f"  {label:32s} AUC {r['auc']:.3f}  change {d:+.3f} ({ci[0]:+.3f} to {ci[1]:+.3f})  {verdict}",
			      file=sys.stderr)


if __name__ == "__main__":
	main()
