"""Signals read from caches other tools fill: the LLM's reading of each patch
(eval/llm.py, paid once per patchset)."""
from __future__ import annotations

import json
from pathlib import Path

from . import signal

ROOT = Path(__file__).resolve().parent.parent
LLM = ROOT / "data" / "llm"


def llm_answer(patch: dict) -> dict | None:
	key = patch.get("key")
	if not key:
		return None
	f = LLM / (key.replace("/", "-") + ".json")
	try:
		d = json.loads(f.read_text())
	except (OSError, ValueError):
		return None
	return d if "mechanisms" in d else None


@signal("llm_overall", kind="llm", cost="dollars", needs=("llm-cache",),
	doc="The LLM's 0 to 3 rating of the chance the patch has a bug (-1: not read).")
def llm_overall(p):
	a = llm_answer(p)
	if not a:
		return -1.0
	return float(a.get("overall", 0)), f"LLM rates it {a.get('overall')}/3: {a.get('why') or ''}"[:160]


@signal("llm_breadth", kind="llm", cost="dollars", needs=("llm-cache",),
	doc="How many mechanisms the LLM rates likely (2) or certain (3) to break (-1: not read).")
def llm_breadth(p):
	a = llm_answer(p)
	if not a:
		return -1.0
	strong = [m for m, v in a["mechanisms"].items() if v >= 2]
	return float(len(strong)), f"LLM: at risk {', '.join(strong[:5])}" if strong else None
