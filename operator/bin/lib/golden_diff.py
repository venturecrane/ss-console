"""How a golden is compared: the dotted paths where two JSON documents differ,
and the reviewed list of paths each golden may change.

One module for every golden set in the tree that uses ``allowed-changes.json``
(the job host matrix, the broker ledger goldens, the runner-to-broker wire
snapshot) and for the integrity job that regenerates them from the merge-base
tree (``operator/bin/goldens/golden-integrity.py``). Stdlib only: the runner's
venv, the broker's tests and the CI job all import it.

``allowed-changes.json`` maps a golden's name to the dotted paths (list indexes
as numbers) that may differ from it, plus a ``_why`` string. An edit to it is a
reviewed change: the PR that changes a behaviour names the path it changes.
A path ending in ``.*`` allows everything under it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ALLOWED = "allowed-changes.json"


def diffs(a: Any, b: Any, path: str = "") -> list[str]:
    """Every dotted path where ``a`` and ``b`` differ (a key on one side only,
    a list of another length, or an unequal leaf)."""
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[str] = []
        for k in sorted(set(a) | set(b), key=str):
            sub = f"{path}.{k}" if path else str(k)
            out += [sub] if (k in a) != (k in b) else diffs(a[k], b[k], sub)
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in diffs(x, y, f"{path}.{i}" if path else str(i))]
    return [] if a == b else [path or "<root>"]


def load_allowed(golden_dir: Path) -> dict[str, list[str]]:
    p = Path(golden_dir) / ALLOWED
    if not p.is_file():
        return {}
    doc = json.loads(p.read_text(encoding="utf-8"))
    return {k: list(v) for k, v in doc.items() if k != "_why"}


def unexplained(diff_paths: list[str], allowed: list[str]) -> list[str]:
    """The differences no allowed path covers."""
    out = []
    for d in diff_paths:
        if d in allowed or any(a.endswith(".*") and (d == a[:-2] or d.startswith(a[:-1])) for a in allowed):
            continue
        out.append(d)
    return out


def compare_dirs(want_dir: Path, got_dir: Path, skip: tuple[str, ...] = ()) -> dict[str, list[str]]:
    """Golden dir against a regenerated one: per file, the differences that
    ``want_dir/allowed-changes.json`` (or, for a ledger lane dir, the set's one
    level up) does not allow. A file present on one side only is reported as
    ``<file>``. ``skip`` names input fixtures that are not regenerated."""
    allowed = load_allowed(want_dir)
    if not allowed and (Path(want_dir).parent / ALLOWED).is_file():
        lane = Path(want_dir).name
        allowed = {
            k.split("/", 1)[1]: v for k, v in load_allowed(Path(want_dir).parent).items() if k.startswith(f"{lane}/")
        }
    out: dict[str, list[str]] = {}
    ignore = {ALLOWED, *skip}
    want = {p.name for p in Path(want_dir).glob("*.json") if p.name not in ignore}
    got = {p.name for p in Path(got_dir).glob("*.json") if p.name not in ignore}
    for name in sorted(want | got):
        key = name[: -len(".json")]
        if name not in want or name not in got:
            if "<file>" not in allowed.get(key, []):
                out[key] = ["<file>"]
            continue
        a = json.loads((Path(want_dir) / name).read_text(encoding="utf-8"))
        b = json.loads((Path(got_dir) / name).read_text(encoding="utf-8"))
        left = unexplained(diffs(a, b), allowed.get(key, []))
        if left:
            out[key] = left
    return out


__all__ = ["ALLOWED", "compare_dirs", "diffs", "load_allowed", "unexplained"]
