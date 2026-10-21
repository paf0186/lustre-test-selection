#!/usr/bin/env python3
"""Embeddings: a patch against every test function, by meaning rather than by
shared words.  Each test function of each script and each patch (commit
message, changed functions, diff) is embedded with a code-aware embedding model;
a (patch, script) pair is as close as the script's best-matching tests.

  .venv/bin/python eval/embed.py tests --tree ~/lustre-release
  .venv/bin/python eval/embed.py patches          # every case, cached by key

Needs the project virtualenv (torch, sentence-transformers) and a GPU for speed.
Writes data/emb/tests.npy (+ tests.json) and data/emb/patches/<key>.npy.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EMB = ROOT / "data" / "emb"
MODEL = "Qwen/Qwen3-Embedding-0.6B"
QUERY = ("Instruct: Given a change to the Lustre file system source, retrieve the tests that exercise "
	 "the changed code\nQuery: ")
MAX_CHARS = 12000


def model():
	from sentence_transformers import SentenceTransformer
	return SentenceTransformer(MODEL, device="cuda", model_kwargs={"torch_dtype": "float16"})


def tests(args) -> None:
	import numpy as np
	sys.path.insert(0, str(HERE))
	from links import test_functions
	tree = Path(args.tree).expanduser()
	docs, index = [], []
	for sh in sorted((tree / "lustre/tests").glob("*.sh")):
		for name, body in test_functions(sh.read_text(errors="replace")):
			docs.append(f"{sh.stem} {name}\n{body[:MAX_CHARS]}")
			index.append([sh.stem, name])
	m = model()
	t0 = time.time()
	v = m.encode(docs, batch_size=16, normalize_embeddings=True, show_progress_bar=False)
	EMB.mkdir(parents=True, exist_ok=True)
	np.save(EMB / "tests.npy", v.astype("float16"))
	(EMB / "tests.json").write_text(json.dumps(index))
	print(f"{len(docs)} tests embedded in {time.time() - t0:.0f} s", file=sys.stderr)


def patch_text(inp: dict) -> str:
	fns = ", ".join(inp.get("functions") or [])
	diff = inp.get("diff") or ""
	return (f"{QUERY}{inp.get('message') or inp.get('subject') or ''}\n"
		f"Changed functions: {fns}\n{diff}")[:MAX_CHARS]


def patches(args) -> None:
	import numpy as np
	cases = [json.loads(x) for x in open(args.cases)]
	out = EMB / "patches"
	out.mkdir(parents=True, exist_ok=True)
	todo = [c for c in cases if not (out / (c["key"].replace("/", "-") + ".npy")).exists()]
	if not todo:
		print("nothing to do", file=sys.stderr)
		return
	m = model()
	t0 = time.time()
	for i in range(0, len(todo), 32):
		chunk = todo[i:i + 32]
		v = m.encode([patch_text(c["input"]) for c in chunk], batch_size=8, normalize_embeddings=True,
			     show_progress_bar=False)
		for c, row in zip(chunk, v):
			np.save(out / (c["key"].replace("/", "-") + ".npy"), row.astype("float16"))
		if (i // 32) % 10 == 0:
			print(f"  {i + len(chunk)}/{len(todo)} in {time.time() - t0:.0f} s", file=sys.stderr)
	print(f"{len(todo)} patches embedded in {time.time() - t0:.0f} s", file=sys.stderr)


class Closeness:
	"""(patch, script) closeness from cached embeddings."""

	def __init__(self):
		import numpy as np
		self.np = np
		self.tests = np.load(EMB / "tests.npy").astype("float32")
		index = json.loads((EMB / "tests.json").read_text())
		self.rows: dict[str, list[int]] = {}
		for i, (script, _) in enumerate(index):
			self.rows.setdefault(script, []).append(i)
		self._cache: dict[str, dict[str, tuple[float, float]]] = {}

	def for_patch(self, key: str | None) -> dict[str, tuple[float, float]] | None:
		if not key:
			return None
		if key in self._cache:
			return self._cache[key]
		f = EMB / "patches" / (key.replace("/", "-") + ".npy")
		if not f.exists():
			return None
		sims = self.tests @ self.np.load(f).astype("float32")
		out = {}
		for script, rows in self.rows.items():
			s = self.np.sort(sims[rows])[::-1]
			out[script] = (float(s[0]), float(s[:3].mean()))
		self._cache[key] = out
		return out


def main() -> None:
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	sub = ap.add_subparsers(dest="cmd", required=True)
	t = sub.add_parser("tests")
	t.add_argument("--tree", default=str(Path.home() / "lustre-release"))
	p = sub.add_parser("patches")
	p.add_argument("--cases", default=str(ROOT / "data" / "cases.jsonl"))
	args = ap.parse_args()
	{"tests": tests, "patches": patches}[args.cmd](args)


if __name__ == "__main__":
	main()
