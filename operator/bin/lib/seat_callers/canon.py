"""A stable, content-free digest of whatever a site returned.

The table records digests, never values: a returned customer.yaml block is
client configuration. `canon` turns a value into JSON-able data with sorted
keys and normalised paths (the run's scratch root, the repo root and the home
directory become placeholders), so the same behaviour digests the same on any
machine; `shape` is the coarse class the mode labels are derived from.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import enum
import hashlib
import json
import types
from pathlib import PurePath
from typing import Any

MAX_DEPTH = 12


class Normaliser:
    """Replace machine-specific prefixes in strings with fixed placeholders."""

    def __init__(self, pairs: list[tuple[str, str]]) -> None:
        # Longest prefix first, so a scratch root under the home dir wins.
        self.pairs = sorted((p for p in pairs if p[0]), key=lambda p: -len(p[0]))

    def __call__(self, text: str) -> str:
        for prefix, token in self.pairs:
            text = text.replace(prefix, token)
        return text


def _canon_mapping(value: dict, norm: Normaliser, depth: int, seen: set[int]) -> Any:
    items = [
        (json.dumps(canon(k, norm, depth + 1, seen), sort_keys=True), canon(v, norm, depth + 1, seen))
        for k, v in value.items()
    ]
    return {"map": sorted(items, key=lambda kv: kv[0])}


def _canon_object(value: Any, norm: Normaliser, depth: int, seen: set[int]) -> Any:
    name = type(value).__name__
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        fields = {f.name: getattr(value, f.name, None) for f in dataclasses.fields(value)}
        return {"dataclass": name, "fields": canon(fields, norm, depth + 1, seen)}
    attrs = getattr(value, "__dict__", None)
    if isinstance(attrs, dict):
        return {"object": name, "vars": canon(dict(attrs), norm, depth + 1, seen)}
    return {"object": name}


def _canon_scalar(value: Any, norm: Normaliser) -> tuple[bool, Any]:
    """(handled, canonical) for the leaf types."""
    if value is None or isinstance(value, bool | int):
        return True, value
    if isinstance(value, float):
        return True, {"float": repr(value)}
    if isinstance(value, str):
        return True, norm(value)
    if isinstance(value, bytes | bytearray):
        return True, {"bytes": hashlib.sha256(bytes(value)).hexdigest()[:16], "len": len(value)}
    if isinstance(value, PurePath):
        return True, {"path": norm(str(value))}
    if isinstance(value, enum.Enum):
        return True, {"enum": f"{type(value).__name__}.{value.name}"}
    if isinstance(value, _dt.datetime | _dt.date | _dt.time | _dt.timedelta):
        return True, {type(value).__name__: str(value)}
    if isinstance(value, types.FunctionType | types.BuiltinFunctionType | types.MethodType | type | types.ModuleType):
        return True, {"callable": type(value).__name__}
    if isinstance(value, BaseException):
        return True, {"exception": type(value).__name__}
    return False, None


def canon(value: Any, norm: Normaliser, depth: int = 0, seen: set[int] | None = None) -> Any:
    """JSON-able, order-independent form of `value`."""
    seen = set() if seen is None else seen
    handled, out = _canon_scalar(value, norm)
    if handled:
        return out
    if depth > MAX_DEPTH:
        return {"truncated": type(value).__name__}
    if id(value) in seen:
        return {"cycle": type(value).__name__}
    seen = seen | {id(value)}
    if isinstance(value, dict):
        return _canon_mapping(value, norm, depth, seen)
    if isinstance(value, list | tuple):
        return {
            type(value).__name__ if isinstance(value, tuple) else "list": [
                canon(v, norm, depth + 1, seen) for v in value
            ]
        }
    if isinstance(value, set | frozenset):
        parts = sorted(json.dumps(canon(v, norm, depth + 1, seen), sort_keys=True) for v in value)
        return {"set": parts}
    return _canon_object(value, norm, depth, seen)


def digest(value: Any, norm: Normaliser) -> str:
    text = json.dumps(canon(value, norm), sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def shape(value: Any) -> str:
    """none | false | empty | value, with the type: the class the mode labels read."""
    kind = type(value).__name__
    if value is None:
        return "none"
    if value is False:
        return "false"
    try:
        empty = isinstance(value, str | bytes | dict | list | tuple | set | frozenset) and len(value) == 0
    except TypeError:
        empty = False
    return f"empty:{kind}" if empty else f"value:{kind}"
