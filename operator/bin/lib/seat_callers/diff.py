"""Name each difference between two caller tables, row by row, field by field."""

from __future__ import annotations

from typing import Any


def _rows(doc: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    out = {}
    for concept, body in doc.get("concepts", {}).items():
        for row in body.get("rows", []):
            out[(concept, row["file"], row["symbol"])] = row
    return out


def differences(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    found: list[str] = []
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
