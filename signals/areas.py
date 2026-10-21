"""The sensitive-areas signal: one column per area of areas.toml."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

from . import signal

TABLE = Path(__file__).resolve().parent / "areas.toml"


def load(path: Path = TABLE) -> list[dict]:
	areas = tomllib.loads(path.read_text())["area"]
	for a in areas:
		a["_lines"] = [re.compile(p, re.M) for p in a.get("lines", [])]
	return areas


AREAS = load()


def changed_text(patch: dict) -> str:
	return "\n".join(ln[1:] for ln in (patch.get("diff") or "").splitlines()
			 if ln[:1] in "+-" and ln[:3] not in ("+++", "---"))


def hits(patch: dict) -> dict[str, str]:
	"""Area name: what matched it."""
	files = patch.get("files") or []
	text = None
	out = {}
	for a in AREAS:
		f = next((x for x in files if any(x.startswith(p) for p in a.get("paths", []))
			  or any(c in x for c in a.get("contains", []))), None)
		if f:
			out[a["name"]] = f
			continue
		if a["_lines"]:
			text = changed_text(patch) if text is None else text
			m = next((r.search(text) for r in a["_lines"] if r.search(text)), None)
			if m:
				out[a["name"]] = m.group(0).strip()[:40]
	return out


@signal("areas", doc="Sensitive areas the patch touches (signals/areas.toml), one column each.")
def areas(p):
	h = hits(p)
	return ({a["name"]: float(a["name"] in h) for a in AREAS},
		"; ".join(f"{n} ({w})" for n, w in h.items()) or None)


@signal("sensitive_count", doc="How many sensitive areas the patch touches.")
def sensitive_count(p):
	n = len(hits(p))
	return float(n), f"{n} sensitive area(s)" if n else None
