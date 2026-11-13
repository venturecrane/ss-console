"""Shared helpers for the wave 2 kit goldens (paid calls, firm config, text rules).

A golden here is a JSON document the real code produced over a fixed input; a
test regenerates it and compares. Three things every family needs live here:

* ``plain``: any value as plain JSON, deterministically (no object addresses,
  no set order), so two runs of the same code give the same bytes.
* ``diffs``: the dotted paths where two documents differ, so a difference can be
  allowed by name in ``allowed-changes.json`` and every other difference fails.
* ``write_or_compare``: the one switch. ``JOBS_WRITE_KIT_GOLDENS=1`` rewrites
  the golden (then read the diff); otherwise any difference outside the allowed
  list fails, naming the paths.
"""

from __future__ import annotations

import base64
import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Sequence

GOLDENS = Path(__file__).resolve().parent / "goldens"
WRITE_ENV = "JOBS_WRITE_KIT_GOLDENS"
_ADDRESS = re.compile(r" at 0x[0-9a-f]+")


def writing() -> bool:
    return bool(os.environ.get(WRITE_ENV))


def plain(x: Any, _depth: int = 0) -> Any:
    """``x`` as plain JSON values. Dataclasses and plain objects become maps
    with a ``__type`` key; sets are sorted; bytes become their length and
    sha256 (never the bytes); dates their ISO form."""
    if _depth > 40:
        return "<deep>"
    if x is None or isinstance(x, (bool, int, str)):
        return x
    if isinstance(x, float):
        return round(x, 9)
    if isinstance(x, (bytes, bytearray)):
        return {"__bytes": len(x), "sha256": hashlib.sha256(bytes(x)).hexdigest()}
    if isinstance(x, (dt.date, dt.datetime)):
        return {"__date": x.isoformat()}
    if isinstance(x, Path):
        return {"__path": str(x)}
    if isinstance(x, re.Pattern):
        return {"__regex": x.pattern, "flags": int(x.flags)}
    if isinstance(x, dict):
        return {str(k): plain(v, _depth + 1) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [plain(v, _depth + 1) for v in x]
    if isinstance(x, (set, frozenset)):
        return {"__set": sorted((plain(v, _depth + 1) for v in x), key=lambda v: json.dumps(v, sort_keys=True))}
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        out = {"__type": type(x).__name__}
        for f in dataclasses.fields(x):
            out[f.name] = plain(getattr(x, f.name), _depth + 1)
        return out
    if callable(x):
        return {"__callable": getattr(x, "__qualname__", type(x).__name__)}
    attrs: dict[str, Any] = {}
    for k in sorted(dir(x)):
        if k.startswith("_"):
            continue
        try:
            v = getattr(x, k)
        except Exception:  # noqa: BLE001 - a property that raises is reported as such
            attrs[k] = "<raises>"
            continue
        if callable(v):
            continue
        attrs[k] = plain(v, _depth + 1)
    if attrs:
        return {"__type": type(x).__name__, **attrs}
    return _ADDRESS.sub("", repr(x))


def canon(x: Any) -> str:
    return json.dumps(plain(x), sort_keys=True, ensure_ascii=False)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def diffs(a: Any, b: Any, path: str = "") -> list[str]:
    """Dotted paths (list indexes as numbers) where ``a`` and ``b`` differ."""
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[str] = []
        for k in sorted(set(a) | set(b)):
            sub = f"{path}.{k}" if path else str(k)
            out += [sub] if (k in a) != (k in b) else diffs(a[k], b[k], sub)
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in diffs(x, y, f"{path}.{i}" if path else str(i))]
    return [] if a == b else [path or "<root>"]


def allowed(family: str) -> dict[str, list[str]]:
    return json.loads((GOLDENS / family / "allowed-changes.json").read_text(encoding="utf-8"))


def dump(doc: Any) -> str:
    return json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def compare(path: Path, got: Any, allow: Sequence[str] = ()) -> list[str]:
    """The paths where ``got`` differs from the golden at ``path`` and that
    ``allow`` does not name (empty means equal). Never writes."""
    got = json.loads(json.dumps(got, sort_keys=True))
    want = json.loads(path.read_text(encoding="utf-8"))
    return [d for d in diffs(want, got) if d not in allow]


def write_or_compare(path: Path, got: Any, allow: list[str]) -> list[str]:
    """Rewrite ``path`` under the write switch; then ``compare``."""
    if writing():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump(json.loads(json.dumps(got, sort_keys=True))), encoding="utf-8")
    return compare(path, got, allow)


def read_jsonl(path: Path) -> list[Any]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def compare_jsonl(path: Path, lines: list[Any], allow: Sequence[str] = ()) -> list[str]:
    """The JSONL form of ``compare``: one event per line; paths are ``<line>.<key...>``."""
    lines = [json.loads(json.dumps(x, sort_keys=True)) for x in lines]
    return [d for d in diffs(read_jsonl(path), lines) if d not in allow]


def write_or_compare_jsonl(path: Path, lines: list[Any], allow: list[str]) -> list[str]:
    """Rewrite ``path`` under the write switch; then ``compare_jsonl``."""
    if writing():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(json.dumps(x, sort_keys=True, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8"
        )
    return compare_jsonl(path, lines, allow)


def wire_writes(writes: Sequence[tuple[str, str, Any]]) -> list[dict[str, Any]]:
    """Each recorded vendor write as its method, path and the exact JSON text
    the client sends (httpx's encoding: the keys in the order the code built
    them, no spaces), so a body golden pins the bytes on the wire."""
    return [
        {"method": m, "path": p, "wire": json.dumps(b, ensure_ascii=False, separators=(",", ":"), allow_nan=False)}
        for m, p, b in writes
    ]


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def scrub_tmp(x: Any, tmp: Path) -> Any:
    """``x`` with the test's temp dir written ``<TMP>`` in every string."""
    if isinstance(x, str):
        return x.replace(str(tmp), "<TMP>")
    if isinstance(x, dict):
        return {k: scrub_tmp(v, tmp) for k, v in x.items()}
    if isinstance(x, list):
        return [scrub_tmp(v, tmp) for v in x]
    return x
