"""Patch signals: named measurements of a Gerrit patch, each with a reason, for
any question we ask about patches (has a bug CI will catch, touches something
sensitive, deserves another AI review pass, ...).

A signal is registered once and used by every target:

    @signal("lines_changed", kind="rule", cost="free",
            doc="Lines added or removed by the diff (log scale).")
    def lines_changed(patch):
        n = ...
        return math.log1p(n), f"{n} lines changed"

It returns a value (a number, or a dict of numbers for a family of columns) and,
optionally, a reason.  A learned signal is a class with fit(cases) and
value(patch); see `Learned`.  Rule tables (sensitive areas) live in TOML next to
this file and are read by their signal.

    signals.compute(patch)                 # every signal: {name: Value}
    signals.compute(patch, ["areas", ...]) # some
    signals.registry()                     # what exists, with kind, cost, needs

`patch` is the dict a case carries as `input` (eval/cases.py): key, subject,
message, diff, files, functions, scripts, test_parameters, gerrit_kind, series,
history.  Targets (signals/targets.py) say what a patch's label is; the scorer
(signals/score.py) fits any target from any signals and explains each score.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

KINDS = ("rule", "learned", "llm", "embedding")
COSTS = ("free", "data", "dollars", "gpu")


@dataclass
class Spec:
	name: str
	kind: str
	cost: str
	doc: str
	needs: tuple[str, ...] = ()
	fn: Callable[..., Any] | None = None
	learned: type | None = None


@dataclass
class Value:
	"""A signal's columns for one patch, and why."""
	columns: dict[str, float]
	reason: str | None = None
	extra: dict[str, Any] = field(default_factory=dict)


_REGISTRY: dict[str, Spec] = {}


def signal(name: str, *, kind: str = "rule", cost: str = "free", doc: str = "",
	   needs: tuple[str, ...] = ()):
	"""Register a function `fn(patch) -> value | (value, reason)` as a signal."""
	assert kind in KINDS and cost in COSTS, (kind, cost)

	def deco(fn):
		_REGISTRY[name] = Spec(name, kind, cost, doc or (fn.__doc__ or "").strip(), needs, fn=fn)
		return fn
	return deco


class Learned:
	"""Base of a signal fitted on training cases: `fit(cases)` then `value(patch,
	change=None)`, where `change` is the patch's own change, left out of what it
	learned so a training row never sees its own label."""
	name = ""
	kind = "learned"
	cost = "data"
	doc = ""
	needs: tuple[str, ...] = ()

	def fit(self, cases: list[dict]) -> "Learned":
		return self

	def value(self, patch: dict, change: int | None = None):
		raise NotImplementedError


def learned(cls: type) -> type:
	"""Register a Learned subclass as a signal."""
	_REGISTRY[cls.name] = Spec(cls.name, cls.kind, cls.cost, cls.doc or (cls.__doc__ or "").strip(),
				   tuple(cls.needs), learned=cls)
	return cls


def registry() -> dict[str, Spec]:
	_load()
	return dict(_REGISTRY)


def _as_value(out) -> Value:
	reason = None
	if isinstance(out, tuple):
		out, reason = out
	if isinstance(out, Value):
		return out
	if isinstance(out, dict):
		return Value({k: float(v) for k, v in out.items()}, reason)
	return Value({"": float(out)}, reason)


def compute(patch: dict, names: list[str] | None = None, fitted: dict[str, Learned] | None = None,
	    change: int | None = None) -> dict[str, Value]:
	"""Every (or the named) signal of a patch.  Learned signals need `fitted`
	instances (signals.score.Scorer fits them); unfitted ones are skipped."""
	_load()
	out = {}
	for name in names or list(_REGISTRY):
		spec = _REGISTRY[name]
		if spec.fn is not None:
			out[name] = _as_value(spec.fn(patch))
		elif fitted and name in fitted:
			out[name] = _as_value(fitted[name].value(patch, change))
	return out


def columns(values: dict[str, Value]) -> dict[str, float]:
	"""Flatten signal values into model columns, named signal or signal.part."""
	return {(n if not k else f"{n}.{k}"): v for n, val in values.items() for k, v in val.columns.items()}


_LOADED = False


def _load() -> None:
	global _LOADED
	if not _LOADED:
		_LOADED = True
		from . import core, areas, external  # noqa: F401  (they register themselves)
