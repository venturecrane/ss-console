"""How a golden is compared: the dotted paths where two JSON documents differ,
and the reviewed list of paths each golden may change.

One module for every golden set in the tree that uses ``allowed-changes.json``
(the job host matrix, the broker ledger goldens, the runner-to-broker wire
snapshot, the kit goldens, the seat-config caller table) and for the integrity
job that regenerates them from the merge-base tree
(``operator/bin/goldens/golden-integrity.py``). Stdlib only: the runner's
venv, the broker's tests and the CI job all import it.

``allowed-changes.json`` maps a golden's name (its path under the set's root,
less the extension) to the dotted paths (list indexes as numbers) that may
differ from it, plus a ``_why`` string. ``<file>`` allows the file itself to
appear or go. An edit to it is a reviewed change: the PR that changes a
behaviour names each path it changes. Every entry names one exact path: a
wildcard is refused, because an entry that covers a subtree lets a later
change inside it through unreviewed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

ALLOWED = "allowed-changes.json"
FILE = "<file>"
#: The extensions a golden is stored as (JSONL: one document per line).
SUFFIXES = (".json", ".jsonl")


class AllowedListError(ValueError):
    """An allowed-changes file that is not a map of name to exact paths."""


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


def losses(old: Any, new: Any, path: str = "") -> list[str]:
    """The paths where ``new`` does not keep what ``old`` held: a key or a list
    item gone, or a value changed. A key or a trailing list item only ``new``
    has is an addition and is not reported (a new cell, case or step)."""
    if isinstance(old, dict) and isinstance(new, dict):
        out: list[str] = []
        for k in sorted(old, key=str):
            sub = f"{path}.{k}" if path else str(k)
            out += [sub] if k not in new else losses(old[k], new[k], sub)
        return out
    if isinstance(old, list) and isinstance(new, list):
        out = []
        for i, x in enumerate(old):
            sub = f"{path}.{i}" if path else str(i)
            out += [sub] if i >= len(new) else losses(x, new[i], sub)
        return out
    return [] if old == new else [path or "<root>"]


def parse_allowed(doc: Any, where: str = ALLOWED) -> dict[str, list[str]]:
    """An allowed-changes document as name -> exact paths; refuses any other shape."""
    if not isinstance(doc, dict):
        raise AllowedListError(f"{where}: not a map")
    out: dict[str, list[str]] = {}
    for k, v in doc.items():
        if k == "_why":
            continue
        if not isinstance(v, list) or not all(isinstance(p, str) and p for p in v):
            raise AllowedListError(f"{where}: {k} is not a list of paths")
        wild = [p for p in v if "*" in p]
        if wild:
            raise AllowedListError(f"{where}: {k} names a wildcard {wild}; name each exact path instead")
        out[str(k)] = list(v)
    return out


def load_allowed(golden_dir: Path) -> dict[str, list[str]]:
    p = Path(golden_dir) / ALLOWED
    if not p.is_file():
        return {}
    return parse_allowed(json.loads(p.read_text(encoding="utf-8")), str(p))


def unexplained(diff_paths: list[str], allowed: list[str]) -> list[str]:
    """The differences no allowed path names (exact paths only)."""
    names = set(allowed)
    return [d for d in diff_paths if d not in names]


def read_golden(path: Path) -> Any:
    text = Path(path).read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    return json.loads(text)


def goldens(root: Path, pattern: str = "**/*", skip: tuple[str, ...] = ()) -> dict[str, Path]:
    """Golden name (path under ``root`` less its extension) -> file, for every
    ``.json``/``.jsonl`` under ``root`` matching ``pattern``; allowed-changes
    files, lock files and ``skip`` names are not goldens."""
    root = Path(root)
    if not root.is_dir():
        return {}
    out: dict[str, Path] = {}
    for p in sorted(root.glob(pattern)):
        if p.is_file() and p.suffix in SUFFIXES and p.name != ALLOWED and p.name not in skip:
            out[p.relative_to(root).with_suffix("").as_posix()] = p
    return out


def tree_diffs(
    want: dict[str, Path],
    got: dict[str, Path],
    *,
    only_losses: bool = False,
    view: Callable[[Any], Any] | None = None,
) -> dict[str, list[str]]:
    """Per golden name, every path where ``got`` differs from ``want`` (or, with
    ``only_losses``, every path ``got`` does not keep). A file on one side only
    is ``<file>``, except a file only ``got`` has when only losses count.
    ``view`` is the set's own comparison form of a document (the caller table
    takes line numbers out), applied to both sides."""
    out: dict[str, list[str]] = {}
    for name in sorted(set(want) | set(got)):
        if name not in got or name not in want:
            if name in want or not only_losses:
                out[name] = [FILE]
            continue
        a, b = read_golden(want[name]), read_golden(got[name])
        if view is not None:
            a, b = view(a), view(b)
        found = losses(a, b) if only_losses else diffs(a, b)
        if found:
            out[name] = found
    return out


def unallowed(found: dict[str, list[str]], allowed: dict[str, list[str]]) -> dict[str, list[str]]:
    """The differences in ``found`` that ``allowed`` does not name."""
    out: dict[str, list[str]] = {}
    for name, paths in found.items():
        left = unexplained(paths, allowed.get(name, []))
        if left:
            out[name] = left
    return out


def stale(allowed: dict[str, list[str]], *found: dict[str, list[str]]) -> dict[str, list[str]]:
    """Allowed entries that name no difference anywhere in ``found``: an entry
    that no longer explains anything must be removed, or it waits to let a
    later change through unreviewed."""
    seen = {(name, p) for f in found for name, paths in f.items() for p in paths}
    out: dict[str, list[str]] = {}
    for name, paths in allowed.items():
        left = [p for p in paths if (name, p) not in seen]
        if left:
            out[name] = left
    return out


def inherited(allowed: dict[str, list[str]], base_allowed: dict[str, list[str]]) -> dict[str, list[str]]:
    """Allowed entries the merge-base already carried: they were reviewed for
    the change that merged them and must be cleared, never reused."""
    out: dict[str, list[str]] = {}
    for name, paths in allowed.items():
        left = [p for p in paths if p in set(base_allowed.get(name, []))]
        if left:
            out[name] = left
    return out


def compare_dirs(want_dir: Path, got_dir: Path, skip: tuple[str, ...] = ()) -> dict[str, list[str]]:
    """Golden dir against a regenerated one (top-level files): per file, the
    differences that ``want_dir/allowed-changes.json`` (or, for a ledger lane
    dir, the set's one level up) does not allow. A file present on one side
    only is reported as ``<file>``. ``skip`` names input fixtures that are not
    regenerated."""
    allowed = load_allowed(want_dir)
    if not allowed and (Path(want_dir).parent / ALLOWED).is_file():
        lane = Path(want_dir).name
        allowed = {
            k.split("/", 1)[1]: v for k, v in load_allowed(Path(want_dir).parent).items() if k.startswith(f"{lane}/")
        }
    found = tree_diffs(goldens(want_dir, "*", skip), goldens(got_dir, "*", skip))
    return unallowed(found, allowed)


__all__ = [
    "ALLOWED",
    "FILE",
    "AllowedListError",
    "compare_dirs",
    "diffs",
    "goldens",
    "inherited",
    "load_allowed",
    "losses",
    "parse_allowed",
    "read_golden",
    "stale",
    "tree_diffs",
    "unallowed",
    "unexplained",
]
