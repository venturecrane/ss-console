"""The decision recorder: what ``_relay_draft`` and ``_evaluate_tool_call`` did,
call by call, in a form a split of either function must reproduce exactly.

FROZEN for the split PRs (wave 4, B and C). The corpus they are held to is
recorded by this file, so it changes only in a PR that changes no decision code,
and ``golden_manifest.py`` pins its normaliser.

HOW. ``sys.monitoring`` sees each call of the two functions start and end, in any
module instance a test loaded (``load_plugin`` re-executes the plugin each time).
While one runs, every name in its ``tables.Decision`` is wrapped where it LIVES
(the module, object or class that holds it), not in the decision's globals, so a
helper moved to a sibling module still reaches the same wrapper. A call is
recorded with its arguments and result when it is direct (no wrapped call is on
the stack), or when it is an effect (``edges``, recorded at any depth: the audit
clients, the broker socket, the stores, the Graph broker). Each record also has
the call's inputs, its inputs again on return (the wall replays an approved
payload over ``args`` in place), the value it returned, and every register that
changed (``registers``, snapshotted before and after).

NORMALISED, NOT MASKED. A value that differs between runs (a uuid, a ULID, a
timestamp, a temp path, a clock float) becomes a pseudonym numbered by first
sight in its test or case, so the same id stays the same token everywhere in that
record and a commit under the wrong key shows as a different token. Ids a test
wrote itself are kept as written.

ORDER. Before each test or case the registers in ``resets`` are reset in place,
so a record never depends on what ran before it (``test_order_independence``).
"""

from __future__ import annotations

import dataclasses
import enum
import functools
import hashlib
import json
import re
import sqlite3
import sys
import threading
import types
from collections import OrderedDict, deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.decisions import tables

OVERLAY = Path(__file__).resolve().parents[2]
#: sys.monitoring tool ids: 0-2 and 5 are claimed by convention (debugger,
#: coverage, profiler, optimizer); the recorder takes 3, the reach check 4.
TOOL_ID = 3
_M = sys.monitoring
_EV = _M.events
_BRANCH = getattr(_EV, "BRANCH_LEFT", 0) | getattr(_EV, "BRANCH_RIGHT", 0)

# --------------------------------------------------------------------------
# Normalising
# --------------------------------------------------------------------------

_TMP = re.compile(r"(?:/private)?/(?:var/folders/[^/\s'\"]+/[^/\s'\"]+/T|tmp)/[^\s'\",)]*")
#: Values written as a short token numbered by first sight in the test or case
#: (``digest#1``, ``ulid#2``): the same value is the same token everywhere in
#: that record, so a row keyed under the wrong id still shows. A ULID carries
#: the clock. A hex digest or a random hex id (``sender_key``, ``body_digest``,
#: ``token_hex``) is recorded as a token rather than written out, because a
#: committed hex string beside a ``key`` field reads as a leaked credential to
#: the repo's secret scan (gitleaks generic-api-key) and blocks every PR; the
#: words a digest is taken of are recorded in the call arguments themselves.
_VOLATILE = (
    ("ulid", re.compile(r"\b[0-9A-HJKMNP-TV-Z]{26}\b")),
    ("digest", re.compile(r"\b[0-9a-fA-F]{16,}\b")),
)
#: A wall-clock time carries no identity, and two calls a microsecond apart may
#: or may not share one, so it is masked rather than numbered.
_TIME = re.compile(r"\b\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d)?")
_LONG = 400
#: A long string or bytes value is recorded as its length and the first nine
#: hex characters of its sha256: enough to show a change, and too short for
#: the secret scan's generic rule (ten or more) to read as a credential.
_DEPTH = 7
_LOCK_TYPES = (
    type(threading.Lock()),
    type(threading.RLock()),
    threading.Condition,
    threading.Event,
)
_CALLABLES = (
    types.FunctionType,
    types.MethodType,
    types.BuiltinFunctionType,
    types.BuiltinMethodType,
    functools.partial,
)


class Normal:
    """One record scope's normaliser: values to JSON, volatile ones to pseudonyms."""

    def __init__(self) -> None:
        import datetime as dt

        self._names: dict[tuple[str, Any], str] = {}
        self._counts: dict[str, int] = {}
        # A test that writes today's date (``date.today()``) into a body would
        # otherwise record a corpus that fails tomorrow. A literal date that
        # happens to be today reads as ``<today>`` on that one day too.
        self._today = {dt.date.today().isoformat(), dt.datetime.now(dt.UTC).date().isoformat()}

    def alias(self, kind: str, raw: Any) -> str:
        key = (kind, raw)
        token = self._names.get(key)
        if token is None:
            n = self._counts.get(kind, 0) + 1
            self._counts[kind] = n
            token = self._names[key] = f"{kind}#{n}"
        return token

    def text(self, s: str) -> Any:
        s = _TMP.sub(lambda m: "<tmp>/" + m.group(0).rstrip("/").rsplit("/", 1)[-1], s)
        s = _TIME.sub("<time>", s)
        for today in self._today:
            s = s.replace(today, "<today>")
        for kind, pattern in _VOLATILE:
            s = pattern.sub(lambda m, k=kind: self.alias(k, m.group(0)), s)
        if len(s) > _LONG:
            return {"$str": len(s), "sha": hashlib.sha256(s.encode()).hexdigest()[:9]}
        return s

    def number(self, x: float) -> Any:
        # A float with sub-millisecond digits or a clock's magnitude is a time.
        if x != x or abs(x) >= 1e5 or round(x, 3) != x:
            return "<clock>"
        return x

    def value(self, v: Any, *, expand: bool = False, depth: int = 0) -> Any:
        if depth > _DEPTH:
            return "<...>"
        if isinstance(v, enum.Enum):
            return _enum(v)
        if v is None or isinstance(v, bool | int):
            return v
        if isinstance(v, float):
            return self.number(v)
        if isinstance(v, str):
            return self.text(v)
        return self._compound(v, expand, depth + 1)

    def _compound(self, v: Any, expand: bool, depth: int) -> Any:
        sub = lambda x: self.value(x, expand=expand, depth=depth)  # noqa: E731
        if isinstance(v, bytes | bytearray):
            return {"$bytes": len(v), "sha": hashlib.sha256(bytes(v)).hexdigest()[:9]}
        if isinstance(v, BaseException):
            return {"$raised": type(v).__name__, "message": self.text(str(v))}
        if isinstance(v, OrderedDict):
            return {"$ordered": [[sub(k), sub(x)] for k, x in v.items()]}
        if isinstance(v, dict):
            items = [(self._key(k), sub(x)) for k, x in v.items()]
            return dict(sorted(items, key=lambda kv: kv[0]))
        if isinstance(v, list | tuple | deque):
            return [sub(x) for x in v]
        if isinstance(v, set | frozenset):
            return sorted((sub(x) for x in v), key=lambda x: json.dumps(x, sort_keys=True))
        if dataclasses.is_dataclass(v) and not isinstance(v, type):
            fields = {f.name: getattr(v, f.name, None) for f in dataclasses.fields(v)}
            return {"$": type(v).__name__, **self._attrs(fields, sub)}
        if isinstance(v, threading.local):
            return {"$": "local", **self._attrs(vars(v), sub)}
        return self._opaque(v, expand, sub)

    def _opaque(self, v: Any, expand: bool, sub: Callable[[Any], Any]) -> Any:
        if isinstance(v, (*_LOCK_TYPES, threading.Thread)):
            return "<lock>"
        if isinstance(v, sqlite3.Connection):
            return "<sqlite>"
        if isinstance(v, types.ModuleType):
            return f"<module {v.__name__.rsplit('.', 1)[-1]}>"
        if isinstance(v, Path):
            return self.text(str(v))
        if isinstance(v, type):
            return f"<class {v.__qualname__}>"
        if isinstance(v, _CALLABLES):
            return f"<fn {getattr(v, '__qualname__', type(v).__name__)}>"
        if expand and hasattr(v, "__dict__"):
            return {"$": type(v).__name__, **self._attrs(vars(v), sub)}
        return f"<{type(v).__name__}>"

    def _attrs(self, attrs: dict[str, Any], sub: Callable[[Any], Any]) -> dict[str, Any]:
        out = {}
        for k, x in attrs.items():
            if isinstance(x, _LOCK_TYPES):
                continue
            out[str(k)] = sub(x)
        return dict(sorted(out.items()))

    def _key(self, k: Any) -> str:
        v = self.value(k)
        return v if isinstance(v, str) else json.dumps(v, sort_keys=True)


def _enum(v: enum.Enum) -> str:
    return f"{type(v).__name__}.{v.name}"


# --------------------------------------------------------------------------
# Resolving names and wrapping them where they live
# --------------------------------------------------------------------------

_MISSING = object()


def resolve(name: str, globs: dict[str, Any]) -> tuple[Any, str] | None:
    """``(holder, attribute)`` for a dotted name, or None when any link is absent."""
    if name.startswith("@"):
        mod_name, _, rest = name[1:].rpartition(".")
        parts = rest.split(".")
        holder: Any = sys.modules.get(mod_name)
        while holder is None and "." in mod_name:
            mod_name, _, head = mod_name.rpartition(".")
            parts.insert(0, head)
            holder = sys.modules.get(mod_name)
        if holder is None:
            return None
    else:
        parts = name.split(".")
        holder = globs.get(parts[0], _MISSING)
        if holder is _MISSING:
            return None
        if len(parts) == 1:
            return (globs, parts[0])
        parts = parts[1:]
    for p in parts[:-1]:
        holder = getattr(holder, p, _MISSING)
        if holder is _MISSING or holder is None:
            return None
    return (holder, parts[-1]) if hasattr(holder, parts[-1]) else None


class _Installed:
    """Refcounted wrappers, so concurrent decisions share one set."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.count: dict[tuple[int, str], int] = {}
        self.undo: dict[tuple[int, str], Callable[[], None]] = {}
        self.failed: set[str] = set()

    def add(self, name: str, holder: Any, attr: str) -> tuple[int, str] | None:
        key = (id(holder), attr)
        with self.lock:
            if key in self.count:
                self.count[key] += 1
                return key
            value = holder.get(attr) if isinstance(holder, dict) else getattr(holder, attr, None)
            if value is None or not callable(value) or hasattr(value, "__decisions_wrapper__"):
                return None
            try:
                self.undo[key] = _patch(name, holder, attr)
            except (AttributeError, TypeError):
                self.failed.add(name)
                return None
            self.count[key] = 1
            return key

    def drop(self, keys: list[tuple[int, str]]) -> None:
        with self.lock:
            for key in reversed(keys):
                self.count[key] -= 1
                if self.count[key] == 0:
                    del self.count[key]
                    self.undo.pop(key)()


INSTALLED = _Installed()


def _patch(name: str, holder: Any, attr: str) -> Callable[[], None]:
    if isinstance(holder, dict):  # a module's globals: the decision's own names
        old = holder[attr]
        holder[attr] = _wrapper(name, old, False)
        return lambda: holder.__setitem__(attr, old)
    if isinstance(holder, type):
        raw = next((c.__dict__[attr] for c in holder.__mro__ if attr in c.__dict__), None)
        own = attr in holder.__dict__
        if isinstance(raw, staticmethod | classmethod):
            new: Any = staticmethod(_wrapper(name, getattr(holder, attr), False))
        else:
            new = _wrapper(name, raw, True)
        setattr(holder, attr, new)
        return (lambda: setattr(holder, attr, raw)) if own else (lambda: delattr(holder, attr))
    own = not isinstance(holder, types.ModuleType) and attr in getattr(holder, "__dict__", {})
    old = getattr(holder, attr)
    setattr(holder, attr, _wrapper(name, old, False))
    if isinstance(holder, types.ModuleType) or own:
        return lambda: setattr(holder, attr, old)
    return lambda: delattr(holder, attr)


def _wrapper(name: str, orig: Callable[..., Any], method: bool) -> Callable[..., Any]:
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        rec = _current()
        if rec is None:
            return orig(*args, **kwargs)
        head, rest = (args[:1], args[1:]) if method else ((), args)
        return rec.call(name, orig, head, rest, kwargs)

    wrapped.__decisions_wrapper__ = name  # type: ignore[attr-defined]
    return wrapped


#: (sys.modules size, the function, its aliases): held strongly, because a
#: cache keyed by id() hands a dead function's aliases to the next object that
#: reuses its address.
_ALIASES: dict[int, tuple[int, Any, list[tuple[Any, str]]]] = {}


def aliases(fn: Any) -> list[tuple[Any, str]]:
    """Every overlay module attribute bound to ``fn`` (``from x import y as z``)."""
    cached = _ALIASES.get(id(fn))
    if cached is not None and cached[1] is fn and cached[0] == len(sys.modules):
        return cached[2]
    hit = []
    root = str(OVERLAY)
    for mod in list(sys.modules.values()):
        path = getattr(mod, "__file__", None) or ""
        if not path.startswith(root) or "/tests/" in path:
            continue
        for k, v in list(vars(mod).items()):
            if v is fn:
                hit.append((mod, k))
    _ALIASES[id(fn)] = (len(sys.modules), fn, hit)
    return hit


# --------------------------------------------------------------------------
# Registers
# --------------------------------------------------------------------------


def register_value(name: str, globs: dict[str, Any]) -> Any:
    hit = resolve(name, globs)
    if hit is None:
        return _MISSING
    holder, attr = hit
    return holder[attr] if isinstance(holder, dict) else getattr(holder, attr)


def reset_in_place(obj: Any) -> None:
    """A register back to a fresh instance's state, without rebinding it (a test
    module that imported the name keeps the same object)."""
    if isinstance(obj, dict | list | set | deque):
        obj.clear()
        return
    if isinstance(obj, threading.local):
        obj.__dict__.clear()
        return
    fresh = type(obj)()
    obj.__dict__.clear()
    obj.__dict__.update(fresh.__dict__)


_SEEN_GLOBALS: dict[int, dict[str, Any]] = {}


def _module_globals() -> list[dict[str, Any]]:
    out = dict(_SEEN_GLOBALS)
    for d in tables.DECISIONS:
        for mod in list(sys.modules.values()):
            if (getattr(mod, "__file__", "") or "").endswith(d.file):
                out[id(vars(mod))] = vars(mod)
    return list(out.values())


def reset_registers() -> None:
    """Every ``resets`` register of every decision module instance, and the shared ones."""
    seen: set[int] = set()
    for globs in _module_globals():
        for d in tables.DECISIONS:
            if not (globs.get("__file__", "") or "").endswith(d.file):
                continue
            for name in d.resets:
                obj = register_value(name, globs)
                if obj is not _MISSING and obj is not None and id(obj) not in seen:
                    seen.add(id(obj))
                    reset_in_place(obj)
    for name in tables.SHARED_REGISTERS:
        obj = register_value(name, {})
        if obj is not _MISSING and obj is not None and id(obj) not in seen:
            seen.add(id(obj))
            reset_in_place(obj)


# --------------------------------------------------------------------------
# One call of a decision
# --------------------------------------------------------------------------


@dataclasses.dataclass
class Control:
    """A synthetic case's levers: a callee's result forced, or a raise injected
    at the n-th direct call (counted from 0)."""

    force: dict[str, Callable[[tuple, dict], Any]] = dataclasses.field(default_factory=dict)
    raise_at: tuple[int, ...] = ()
    raise_with: Callable[[], BaseException] | None = None


class Recording:
    def __init__(self, scope: Scope, decision: tables.Decision, frame: types.FrameType) -> None:
        self.scope = scope
        self.d = decision
        self.globs = frame.f_globals
        self.code = frame.f_code
        self.thread = threading.get_ident()
        self.n = scope.normal
        self.events: list[dict[str, Any]] = []
        self.depth = 0
        self.direct = 0
        self.inputs = dict(frame.f_locals)
        self.inputs_before = self.n.value(self.inputs)
        self.before = self._snapshot()
        self.keys: list[tuple[int, str]] = []
        self._wrap_names = set(decision.wrap) | {n for n, _ in decision.local}
        self._edge_names = set(decision.edges) | set(tables.SHARED_EDGES)
        self._install()

    def _install(self) -> None:
        for name in self.d.wrap + tuple(self.d.edges):
            self._add(name, name)
        for name, home in self.d.local:
            self._add(name, home)
        for name in tables.SHARED_EDGES:
            hit = resolve(name, {})
            if hit is None:
                continue
            self._add_hit(name, hit)
            fn = getattr(hit[0], hit[1])
            for holder, attr in aliases(fn):
                if holder is not hit[0]:
                    key = INSTALLED.add(name, holder, attr)
                    if key:
                        self.keys.append(key)

    def _add(self, name: str, home: str) -> None:
        hit = resolve(home, self.globs)
        if hit is not None:
            self._add_hit(name, hit)

    def _add_hit(self, name: str, hit: tuple[Any, str]) -> None:
        key = INSTALLED.add(name, *hit)
        if key:
            self.keys.append(key)

    def _snapshot(self) -> dict[str, Any]:
        out = {}
        for name in self.d.registers:
            obj = register_value(name, self.globs)
            if obj is not _MISSING:
                out[name] = self.n.value(obj, expand=True)
        return out

    def call(self, name: str, orig: Any, head: tuple, args: tuple, kwargs: dict) -> Any:
        direct = self.depth == 0 and name in self._wrap_names
        ev: dict[str, Any] | None = None
        control: tuple[str, Any] | None = None
        if direct or name in self._edge_names:
            ev = {"call" if direct else "edge": name}
            if not direct:
                ev["depth"] = self.depth
            ev["args"] = self.n.value(list(args))
            if kwargs:
                ev["kwargs"] = self.n.value(kwargs)
            self.events.append(ev)
        if direct:
            where = _site(sys._getframe(2))
            self.scope.sites.add(where)
            self.scope.direct_sites.append(where)
            control = self.scope.control_for(name, self.direct, args, kwargs)
            self.direct += 1
        self.depth += 1
        try:
            if control is not None and control[0] == "raise":
                raise control[1]
            result = control[1] if control is not None else orig(*head, *args, **kwargs)
        except BaseException as exc:
            if ev is not None:
                ev["raised"] = self.n.value(exc)
                if control is not None:
                    ev["controlled"] = control[0]
            raise
        finally:
            self.depth -= 1
        if ev is not None:
            ev["result"] = self.n.value(result)
            if control is not None:
                ev["controlled"] = control[0]
        return result

    def finish(self, returned: Any = None, raised: BaseException | None = None) -> dict[str, Any]:
        INSTALLED.drop(self.keys)
        after = self._snapshot()
        changed = {
            k: delta(self.before.get(k), v) for k, v in after.items() if v != self.before.get(k)
        }
        out: dict[str, Any] = {"decision": self.d.key, "inputs": self.inputs_before}
        inputs_after = self.n.value(self.inputs)
        if inputs_after != self.inputs_before:
            out["inputs_after"] = inputs_after
        out["events"] = self.events
        out["registers"] = changed
        if raised is not None:
            out["raised"] = self.n.value(raised)
        else:
            out["returned"] = self.n.value(returned)
        return intern(out)


def delta(before: Any, after: Any) -> Any:
    """What a register's snapshot changed: per key, recursively, so a record
    carries the entry a call wrote rather than the whole register twice."""
    if isinstance(before, dict) and isinstance(after, dict):
        if "$ordered" in before and "$ordered" in after:
            b = {json.dumps(k, sort_keys=True): v for k, v in before["$ordered"]}
            a = {json.dumps(k, sort_keys=True): v for k, v in after["$ordered"]}
            out = delta(b, a)
            common = [k for k in a if k in b]
            if common != [k for k in b if k in a]:
                out["$order"] = list(a)
            return out
        out: dict[str, Any] = {}
        for k in sorted(set(before) | set(after)):
            if k not in after:
                out[k] = {"gone": before[k]}
            elif k not in before:
                out[k] = {"new": after[k]}
            elif before[k] != after[k]:
                out[k] = delta(before[k], after[k])
        return out
    return {"before": before, "after": after}


#: A compound value this long, seen twice in one record, is written once.
_INTERN_AT = 80


def intern(doc: dict[str, Any]) -> dict[str, Any]:
    """Each repeated compound value (an origin, a draft's args) written once,
    under ``values``, and referenced as ``{"$ref": n}``: lossless, and what keeps
    the corpus a few megabytes instead of tens."""
    counts: dict[str, int] = {}

    def scan(v: Any) -> None:
        if isinstance(v, dict | list):
            text = json.dumps(v, sort_keys=True)
            if len(text) >= _INTERN_AT:
                counts[text] = counts.get(text, 0) + 1
            for x in v.values() if isinstance(v, dict) else v:
                scan(x)

    scan(doc)
    index: dict[str, int] = {}
    values: list[Any] = []

    def rewrite(v: Any) -> Any:
        if not isinstance(v, dict | list):
            return v
        text = json.dumps(v, sort_keys=True)
        if counts.get(text, 0) >= 2:
            if text not in index:
                index[text] = len(values)
                values.append(None)
                values[index[text]] = children(v)
            return {"$ref": index[text]}
        return children(v)

    def children(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: rewrite(x) for k, x in sorted(v.items())}
        return [rewrite(x) for x in v]

    body = {k: rewrite(v) for k, v in doc.items()}
    if values:
        body["values"] = values
    return body


def _site(frame: types.FrameType) -> tuple[str, int, int]:
    code = frame.f_code
    pos = _positions(code)[frame.f_lasti // 2]
    return (_rel(code.co_filename), pos[1] or 0, pos[3] or 0)


_POS: dict[int, tuple[types.CodeType, list[tuple[Any, ...]]]] = {}


def _positions(code: types.CodeType) -> list[tuple[Any, ...]]:
    hit = _POS.get(id(code))
    if hit is None or hit[0] is not code:
        hit = _POS[id(code)] = (code, list(code.co_positions()))
    return hit[1]


@functools.lru_cache(maxsize=4096)
def _rel(path: str) -> str:
    try:
        return Path(path).resolve().relative_to(OVERLAY).as_posix()
    except ValueError:
        return path


# --------------------------------------------------------------------------
# A scope: one test, or one synthetic case
# --------------------------------------------------------------------------


class Scope:
    def __init__(self, control: Control | None = None, *, coverage: bool = False) -> None:
        self.normal = Normal()
        self.control = control or Control()
        self.timeline: list[dict[str, Any]] = []
        self.sites: set[tuple[str, int, int]] = set()
        #: Where each direct call came from, in call order (never in a golden:
        #: a split moves the sites, not the calls).
        self.direct_sites: list[tuple[str, int, int]] = []
        self.raised_sites: set[tuple[str, int, int]] = set()
        self.coverage = coverage
        self.decisions = 0
        self.lock = threading.Lock()

    def control_for(
        self, name: str, index: int, args: tuple, kwargs: dict
    ) -> tuple[str, Any] | None:
        c = self.control
        if index in c.raise_at and c.raise_with is not None:
            self.raised_sites.add(_site(sys._getframe(3)))
            return ("raise", c.raise_with())
        factory = c.force.get(name)
        if factory is None:
            return None
        value = factory(args, kwargs)
        if isinstance(value, BaseException):
            return ("raise", value)
        return ("force", value)


@contextmanager
def deterministic(seed: str) -> Iterator[None]:
    """``os.urandom`` (and what draws on it: ``secrets``, ``uuid4``, the random
    module) as a stream fixed by ``seed``, so a random id is the same every run."""
    import os
    import random

    counter = [0]

    def urandom(n: int) -> bytes:
        out = b""
        while len(out) < n:
            counter[0] += 1
            out += hashlib.sha256(f"{seed}:{counter[0]}".encode()).digest()
        return out[:n]

    saved = (os.urandom, getattr(random, "_urandom", None), random.getstate())
    os.urandom = urandom
    if saved[1] is not None:
        random._urandom = urandom  # type: ignore[attr-defined]
    random.seed(seed)
    try:
        yield
    finally:
        os.urandom = saved[0]
        if saved[1] is not None:
            random._urandom = saved[1]  # type: ignore[attr-defined]
        random.setstate(saved[2])


def reset_test_counters(module: Any) -> None:
    """A test module's own ``itertools.count`` ids restart for each test, so an id
    a test drew from it does not depend on how many tests ran before."""
    import itertools

    for name, value in list(vars(module).items()):
        if isinstance(value, itertools.count):
            setattr(module, name, itertools.count())


class _State:
    scope: Scope | None = None
    stacks: dict[int, list[Recording]] = {}
    #: id(code) -> (code, what it is): a dict keyed by the code object itself
    #: would hash its bytecode on every frame the PY_UNWIND callback sees.
    targets: dict[int, tuple[types.CodeType, tables.Decision | str]] = {}
    started = False


def _current() -> Recording | None:
    stack = _State.stacks.get(threading.get_ident())
    return stack[-1] if stack else None


def _match(code: types.CodeType) -> tables.Decision | str | None:
    known = _State.targets.get(id(code))
    if known is not None and known[0] is code:
        return known[1]
    for d in tables.DECISIONS:
        if code.co_name == d.name and code.co_filename.endswith(d.file):
            _State.targets[id(code)] = (code, d)
            return d
    for name, file in tables.HOOKS:
        if code.co_name == name and code.co_filename.endswith(file):
            _State.targets[id(code)] = (code, name)
            return name
    return None


def _on_start(code: types.CodeType, offset: int) -> Any:
    hit = _match(code)
    if hit is None:
        return _M.DISABLE
    events = _EV.PY_RETURN
    scope = _State.scope
    if isinstance(hit, tables.Decision):
        frame = sys._getframe(1)
        _SEEN_GLOBALS[id(frame.f_globals)] = frame.f_globals
        if scope is not None:
            if scope.coverage:
                events |= _EV.LINE | _BRANCH
                _cover_helpers(hit, frame.f_globals)
            with scope.lock:
                scope.decisions += 1
            stack = _State.stacks.setdefault(threading.get_ident(), [])
            stack.append(Recording(scope, hit, frame))
    _M.set_local_events(TOOL_ID, code, _M.get_local_events(TOOL_ID, code) | events)
    return None


def _close(code: types.CodeType, returned: Any, raised: BaseException | None) -> None:
    known = _State.targets.get(id(code))
    scope = _State.scope
    if known is None or known[0] is not code or scope is None:
        return
    hit = known[1]
    if isinstance(hit, str):
        if raised is None:
            entry = {"hook": hit, "returned": scope.normal.value(returned)}
            with scope.lock:
                scope.timeline.append(entry)
        return
    stack = _State.stacks.get(threading.get_ident()) or []
    if stack and stack[-1].code is code:
        rec = stack.pop()
        entry = rec.finish(returned, raised)
        with scope.lock:
            scope.timeline.append(entry)


def _on_return(code: types.CodeType, offset: int, value: Any) -> Any:
    _close(code, value, None)


def _on_unwind(code: types.CodeType, offset: int, exc: BaseException) -> Any:
    if id(code) in _State.targets:
        _close(code, None, exc)


#: Lines and branch directions reached by any scope in this process. Each is
#: recorded once and its event then disabled at that location, so coverage
#: costs one callback per line, not one per line executed.
COVERAGE: dict[str, set[tuple[str, int, int]] | set[tuple[str, int]]] = {
    "lines": set(),
    "branches": set(),
}


def _on_line(code: types.CodeType, line: int) -> Any:
    scope = _State.scope
    if scope is None or not scope.coverage:
        return None
    COVERAGE["lines"].add((_rel(code.co_filename), line))  # type: ignore[arg-type]
    return _M.DISABLE


def _on_branch(code: types.CodeType, offset: int, dest: int) -> Any:
    scope = _State.scope
    if scope is None or not scope.coverage:
        return None
    COVERAGE["branches"].add((_rel(code.co_filename), offset, dest))  # type: ignore[arg-type]
    return _M.DISABLE


_COVERED: dict[int, types.CodeType] = {}


def _cover_helpers(decision: tables.Decision, globs: dict[str, Any]) -> None:
    from tests.decisions import census

    fn = globs.get(decision.name)
    target = getattr(fn, "__wrapped__", fn)
    if not isinstance(target, types.FunctionType):
        return
    for helper in census.walk(decision, target).functions[1:]:
        code = helper.__code__
        if _COVERED.get(id(code)) is not code:
            _COVERED[id(code)] = code
            _M.set_local_events(TOOL_ID, code, _EV.LINE | _BRANCH)


def start() -> None:
    """Turn the recorder on for this process (idempotent)."""
    if _State.started:
        return
    _M.use_tool_id(TOOL_ID, "smd-decision-corpus")
    _M.register_callback(TOOL_ID, _EV.PY_START, _on_start)
    _M.register_callback(TOOL_ID, _EV.PY_RETURN, _on_return)
    _M.register_callback(TOOL_ID, _EV.PY_UNWIND, _on_unwind)
    _M.register_callback(TOOL_ID, _EV.LINE, _on_line)
    if _BRANCH:
        _M.register_callback(TOOL_ID, _EV.BRANCH_LEFT, _on_branch)
        _M.register_callback(TOOL_ID, _EV.BRANCH_RIGHT, _on_branch)
    _M.set_events(TOOL_ID, _EV.PY_START | _EV.PY_UNWIND)
    _State.started = True
    import atexit

    atexit.register(stop)


def stop() -> None:
    """Turn the recorder off (the plugin does at session end, before interpreter
    shutdown can call a callback whose module globals are gone)."""
    if not _State.started:
        return
    _M.set_events(TOOL_ID, 0)
    for code, _hit in list(_State.targets.values()):
        _M.set_local_events(TOOL_ID, code, 0)
    _M.free_tool_id(TOOL_ID)
    _State.started = False


@contextmanager
def scope(control: Control | None = None, *, coverage: bool = False) -> Iterator[Scope]:
    """Record every decision call made inside the block, into one ``Scope``."""
    start()
    s = Scope(control, coverage=coverage)
    previous = _State.scope
    _State.scope = s
    try:
        yield s
    finally:
        _State.scope = previous
        for stack in _State.stacks.values():
            while stack:
                INSTALLED.drop(stack.pop().keys)


# --------------------------------------------------------------------------
# The pytest plugin: replay the existing tests under the recorder
# --------------------------------------------------------------------------
#
#   SMD_DECISIONS_OUT=<dir> python -m pytest -p tests.decisions.recorder <files>
#
# writes <dir>/existing/<test file stem>.json ({nodeid: {outcome, timeline}})
# for every test that reached either function, and <dir>/coverage.json.

_RUN: dict[str, Any] = {"by_file": {}, "scope": None, "coverage": Scope()}


def _active() -> bool:
    import os

    return bool(os.environ.get("SMD_DECISIONS_OUT"))


def pytest_configure(config: Any) -> None:
    if _active():
        start()


def pytest_collection_modifyitems(session: Any, config: Any, items: list[Any]) -> None:
    import os

    if _active() and os.environ.get("SMD_DECISIONS_ORDER") == "reverse":
        items.reverse()


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: Any) -> None:
    if not _active():
        return
    reset_registers()
    reset_test_counters(item.module)
    s = Scope(coverage=True)
    _RUN["scope"] = s
    _RUN["random"] = deterministic(item.nodeid)
    _RUN["random"].__enter__()
    _State.scope = s


def pytest_runtest_makereport(item: Any, call: Any) -> None:
    if not _active() or call.when != "teardown":
        return
    s: Scope | None = _RUN["scope"]
    _State.scope = None
    _RUN["scope"] = None
    if _RUN.get("random") is not None:
        _RUN.pop("random").__exit__(None, None, None)
    if s is None or not s.decisions:
        return
    cov: Scope = _RUN["coverage"]
    cov.sites |= s.sites
    _RUN["by_file"].setdefault(Path(str(item.path)).stem, {})[item.nodeid] = {
        "outcome": "passed",
        "timeline": s.timeline,
    }


def pytest_runtest_logreport(report: Any) -> None:
    if _active() and report.failed:
        _RUN.setdefault("failed", set()).add(report.nodeid)


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    if not _active():
        return
    import os

    out = Path(os.environ["SMD_DECISIONS_OUT"])
    failed = _RUN.get("failed", set())
    reached = set()
    for stem, tests in _RUN["by_file"].items():
        for nodeid, rec in tests.items():
            reached.add(nodeid)
            if nodeid in failed:
                rec["outcome"] = "failed"
        write_json(out / "existing" / f"{stem}.json", dict(sorted(tests.items())))
    cov: Scope = _RUN["coverage"]
    write_json(out / "coverage.json", coverage_doc(cov))
    # A selected file's test that never reached a decision is not in the corpus,
    # and on the integrity job's base tree (operator/ alone) one that reads a
    # file outside operator/ fails there; its failure is reported, not recorded.
    write_json(
        out / "failures.json",
        {"reached": sorted(failed & reached), "unreached": sorted(failed - reached)},
    )
    stop()


def coverage_doc(s: Scope) -> dict[str, Any]:
    return {
        "sites": sorted(list(x) for x in s.sites),
        "raised_sites": sorted(list(x) for x in s.raised_sites),
        "branches": sorted(list(x) for x in COVERAGE["branches"]),
        "lines": sorted(list(x) for x in COVERAGE["lines"]),
    }


def write_json(path: Path, doc: Any) -> None:
    """A golden as JSON with one top-level entry per line (a test, a case), so a
    change shows in a diff as the lines of the records it touched."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(doc, dict) and doc:
        lines = [
            f"{json.dumps(k)}: {json.dumps(v, sort_keys=True)}" for k, v in sorted(doc.items())
        ]
        text = "{\n" + ",\n".join(lines) + "\n}\n"
    else:
        text = json.dumps(doc, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")


__all__ = [
    "INSTALLED",
    "Control",
    "Normal",
    "Scope",
    "coverage_doc",
    "reset_registers",
    "resolve",
    "scope",
    "start",
    "write_json",
]
