"""Name each difference between two caller tables, row by row, field by field.

A row is keyed by concept, file and enclosing symbol (never by line), and its
``calls`` are compared as ``<file> <call text>``: the ``:<line>`` each call
carries in the table is a hint for a reader, never compared. Any edit that
moves a site up or down a file (a peer's merge above it) is not a difference;
a changed call, a call added or gone, or any changed behaviour is.
"""

from __future__ import annotations

import copy
import re
from typing import Any

#: ``operator/x.py:224 yaml.safe_load(...)`` -> ``operator/x.py yaml.safe_load(...)``.
_CALL_LINE = re.compile(r"^(?P<file>\S+?):\d+ ")
#: ``<module>:12`` -> ``<module>``: a module- or script-level site's symbol.
_SYMBOL_LINE = re.compile(r"^(?P<kind><[a-z]+>):\d+$")


def call_text(call: str) -> str:
    """A ``calls`` entry without its line number."""
    return _CALL_LINE.sub(r"\g<file> ", call)


def symbol_key(symbol: str) -> str:
    return _SYMBOL_LINE.sub(r"\g<kind>", symbol)


def comparable(doc: dict[str, Any]) -> dict[str, Any]:
    """The table with every line number taken out of what is compared."""
    out = copy.deepcopy(doc)
    for body in out.get("concepts", {}).values():
        for row in body.get("rows", []):
            row["symbol"] = symbol_key(row["symbol"])
            row["calls"] = sorted(call_text(c) for c in row.get("calls", []))
        body["rows"] = sorted(body.get("rows", []), key=lambda r: (r["file"], r["symbol"]))
    return out


def _rows(doc: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for concept, body in doc.get("concepts", {}).items():
        for row in body.get("rows", []):
            key = (concept, row["file"], row["symbol"])
            n = 1
            while key in out:  # two module-level sites in one file: keyed in order
                n += 1
                key = (concept, row["file"], f"{row['symbol']}#{n}")
            out[key] = row
    return out


def differences(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    found: list[str] = []
    old, new = comparable(old), comparable(new)
    a, b = _rows(old), _rows(new)
    for key in sorted(a.keys() - b.keys()):
        found.append(f"gone: {key[0]} {key[1]} {key[2]}")
    for key in sorted(b.keys() - a.keys()):
        found.append(f"new: {key[0]} {key[1]} {key[2]}")
    for key in sorted(a.keys() & b.keys()):
        ra, rb = a[key], b[key]
        for field in sorted(ra.keys() | rb.keys()):
            va, vb = ra.get(field), rb.get(field)
            if va == vb:
                continue
            if isinstance(va, dict) and isinstance(vb, dict):
                for sub in sorted(va.keys() | vb.keys()):
                    if va.get(sub) != vb.get(sub):
                        found.append(
                            f"changed: {key[0]} {key[1]} {key[2]} {field}.{sub}: {va.get(sub)!r} -> {vb.get(sub)!r}"
                        )
            else:
                found.append(f"changed: {key[0]} {key[1]} {key[2]} {field}: {va!r} -> {vb!r}")
    for concept in sorted(old.get("concepts", {}).keys() | new.get("concepts", {}).keys()):
        fa = old.get("concepts", {}).get(concept, {}).get("files")
        fb = new.get("concepts", {}).get(concept, {}).get("files")
        if fa != fb:
            found.append(f"changed: {concept} file list")
    if not found and old != new:
        found.append("changed: table header or summary")
    return found
