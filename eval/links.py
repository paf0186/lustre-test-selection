#!/usr/bin/env python3
"""Static links from code to tests, read from a lustre-release tree: which test
scripts exercise what a patch changes, by principle rather than by history.

- Fail points: code checks OBD_FAIL_*/CFS_FAIL_* points, and tests set them
  with `fail_loc=0x...` (or name them).  A patch that touches the code around a
  fail point needs the scripts that set it.
- Words: BM25 between the identifiers a patch's changed lines use and each test
  script's text (the functions, parameters and tools its tests call).

  eval/links.py --tree ~/lustre-release      # builds data/links.json
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "links.json"
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
DEFINE = re.compile(r"#define\s+((?:OBD|CFS)_FAIL_[A-Z0-9_]+)\s+(0x[0-9a-fA-F]+)")
FAIL_LOC = re.compile(r"fail_loc=(0x[0-9a-fA-F]+)")
FAIL_NAME = re.compile(r"\b((?:OBD|CFS)_FAIL_[A-Z0-9_]+)\b")
# Shell and test framework words that say nothing about what a test exercises.
STOP = {"echo", "then", "else", "done", "error", "local", "return", "true", "false", "skip", "test",
	"tfile", "tdir", "DIR", "MOUNT", "LCTL", "LFS", "facet", "do_facet", "do_node", "stack_trap",
	"wait", "sleep", "grep", "awk", "cat", "rm", "mkdir", "touch", "dd", "run_test", "local",
	"test_mkdir", "mkdir_on_mdt0", "count", "file", "files", "dir", "dirs", "value", "result",
	"null", "dev", "zero", "while", "case", "esac", "shift", "printf", "export", "unset", "this", "that",
	"with", "from", "should", "failed", "fail", "check", "size", "name", "line", "list", "info", "debug"}


def mask(v: str) -> int:
	return int(v, 16) & 0xffff


def words(text: str) -> list[str]:
	return [w for w in IDENT.findall(text) if w not in STOP and not w.isupper() or w.startswith(("OBD_FAIL", "CFS_FAIL"))]


TESTFN = re.compile(r"^(test_[0-9A-Za-z_]+)\s*\(\)\s*\{?", re.M)
CFUNC = re.compile(r"^(?:static\s+)?(?:inline\s+)?[A-Za-z_][\w\s\*]*?\b([A-Za-z_]\w*)\s*\([^;]*$", re.M)
HUNK_FN = re.compile(r"^@@[^@]*@@\s*(?:.*?\b)?([A-Za-z_]\w*)\s*\(", re.M)


def test_functions(text: str) -> list[tuple[str, str]]:
	"""(name, body) of each test_* function of a script."""
	marks = [(m.start(), m.group(1)) for m in TESTFN.finditer(text)]
	out = []
	for i, (pos, name) in enumerate(marks):
		end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
		out.append((name, text[pos:end]))
	return out


def function_fail_points(tree: Path, fail_value: dict[str, int]) -> dict[str, list[int]]:
	"""Each C function's fail points: name -> values (a name defined in several
	files gets the union)."""
	out: dict[str, set[int]] = defaultdict(set)
	for c in list((tree / "lustre").rglob("*.c")) + list((tree / "lnet").rglob("*.c")) + \
			list((tree / "libcfs").rglob("*.c")):
		text = c.read_text(errors="replace")
		starts = [(m.start(), m.group(1)) for m in CFUNC.finditer(text)]
		for i, (pos, name) in enumerate(starts):
			end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
			for n in FAIL_NAME.findall(text[pos:end]):
				if n in fail_value:
					out[name].add(fail_value[n])
	return {k: sorted(v) for k, v in out.items()}


_FILES: dict[str, tuple[list[str], list[tuple[int, str]]]] = {}


def _file(tree: Path, path: str):
	if path not in _FILES:
		f = tree / path
		lines = f.read_text(errors="replace").splitlines() if f.is_file() else []
		text = "\n".join(lines)
		starts = []
		for m in CFUNC.finditer(text):
			starts.append((text.count("\n", 0, m.start()), m.group(1)))
		_FILES[path] = (lines, starts)
	return _FILES[path]


def changed_functions(diff: str, tree: Path) -> list[str]:
	"""The C functions a patch changes: each hunk's old lines found in the tree's
	copy of the file, then the function around them.  Gerrit's patches carry no
	function names in their hunk headers."""
	out = set()
	path, hunk = None, []

	def flush():
		if not path or not path.endswith((".c", ".h")) or not hunk:
			return
		lines, starts = _file(tree, path)
		if not lines:
			return
		index = {}
		for i, ln in enumerate(lines):
			index.setdefault(ln.strip(), []).append(i)
		for ln in hunk:
			t = ln.strip()
			if len(t) < 12 or len(index.get(t, ())) != 1:
				continue
			at = index[t][0]
			name = None
			for start, fn in starts:
				if start > at:
					break
				name = fn
			if name:
				out.add(name)
			break

	for ln in diff.splitlines():
		if ln.startswith("diff --git "):
			flush()
			path, hunk = ln.split(" b/", 1)[-1], []
		elif ln.startswith("@@"):
			flush()
			hunk = []
		elif ln[:1] in (" ", "-") and not ln.startswith("---"):
			hunk.append(ln[1:])
	flush()
	return sorted(out)


def build(tree: Path) -> dict:
	fail_value = {}
	for h in list((tree / "lustre/include").glob("*.h")) + list((tree / "libcfs/include").rglob("*.h")):
		for name, val in DEFINE.findall(h.read_text(errors="replace")):
			fail_value[name] = mask(val)
	scripts = {}
	docs = []                                   # (script, test, Counter): one per test function
	for sh in sorted((tree / "lustre/tests").glob("*.sh")):
		text = sh.read_text(errors="replace")
		vals = {mask(v) for v in FAIL_LOC.findall(text)}
		vals |= {fail_value[n] for n in FAIL_NAME.findall(text) if n in fail_value}
		scripts[sh.stem] = {"fail": sorted(v for v in vals if v)}
		for name, body in test_functions(text):
			docs.append((sh.stem, name, Counter(words(body))))
	n = len(docs)
	df = Counter(w for _, _, c in docs for w in c)
	idf = {w: math.log(1 + (n - d + 0.5) / (d + 0.5)) for w, d in df.items()}
	avg = sum(sum(c.values()) for _, _, c in docs) / max(1, n)
	return {"fail_value": fail_value, "idf": idf, "avg_len": avg, "scripts": scripts,
		"func_fail": function_fail_points(tree, fail_value),
		"docs": [{"script": s, "test": t, "len": sum(c.values()), "tf": dict(c)} for s, t, c in docs]}


class Links:
	def __init__(self, path: Path = OUT):
		d = json.loads(path.read_text())
		self.fail_value, self.idf, self.avg = d["fail_value"], d["idf"], d["avg_len"]
		self.scripts, self.docs, self.func_fail = d["scripts"], d["docs"], d["func_fail"]
		self.fail_users = defaultdict(set)
		for name, s in self.scripts.items():
			for v in s["fail"]:
				self.fail_users[v].add(name)
		self._cache: dict[str, dict] = {}

	def for_patch(self, inp: dict) -> dict[str, dict[str, float]]:
		"""Per script: fail points of the patch it sets, and BM25 match (0..1, of the best)."""
		key = inp.get("key") or str(id(inp))
		if key in self._cache:
			return self._cache[key]
		changed = "\n".join(ln[1:] for ln in (inp.get("diff") or "").splitlines()
				    if ln[:1] in "+-" and ln[:3] not in ("+++", "---"))
		hunks = "\n".join(ln for ln in (inp.get("diff") or "").splitlines() if ln.startswith("@@"))
		q = Counter(w for w in words(changed + "\n" + hunks) if w in self.idf)
		fails = {self.fail_value[n] for n in FAIL_NAME.findall(changed + hunks) if n in self.fail_value}
		for fn in inp.get("functions") or HUNK_FN.findall(hunks):
			fails.update(self.func_fail.get(fn, ()))
		k1, b = 1.2, 0.75
		best: dict[str, list[float]] = defaultdict(list)
		for doc in self.docs:
			tf, ln = doc["tf"], doc["len"] or 1
			score = 0.0
			for w, qn in q.items():
				f = tf.get(w)
				if f:
					score += self.idf[w] * f * (k1 + 1) / (f + k1 * (1 - b + b * ln / self.avg)) * min(qn, 3)
			best[doc["script"]].append(score)
		# a script is as close as its three best-matching tests
		bm = {name: sum(sorted(v, reverse=True)[:3]) / 3 for name, v in best.items()}
		top = max(bm.values(), default=0) or 1
		out = {name: {"fail": float(sum(1 for v in fails if name in self.fail_users[v])),
			      "bm25": bm.get(name, 0) / top} for name in self.scripts}
		self._cache[key] = out
		return out


def main() -> None:
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("--tree", default=str(Path.home() / "lustre-release"))
	ap.add_argument("--out", default=str(OUT))
	args = ap.parse_args()
	d = build(Path(args.tree).expanduser())
	Path(args.out).write_text(json.dumps(d))
	used = sum(1 for s in d["scripts"].values() if s["fail"])
	print(f"{len(d['fail_value'])} fail points, {len(d['scripts'])} scripts ({used} set fail points), "
	      f"{len(d['idf'])} words")


if __name__ == "__main__":
	main()
