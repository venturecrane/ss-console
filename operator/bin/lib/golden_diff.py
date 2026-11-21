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

An entry excuses a difference only in the change that adds it. A golden test
reads its list through ``reviewed``, which drops every entry the merge-base
tree already carried (another change's leftover, reviewed for that change):
a later change at the same path, made without rewriting the golden, then
fails instead of passing on the old entry.
"""

from __future__ import annotations

import functools
import json
import os
import subprocess
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


#: A commit (or ref) to take as the merge-base instead of
#: ``git merge-base origin/main HEAD``.
BASE_ENV = "GOLDENS_BASE"


class BaseUnknownError(RuntimeError):
    """An allowed list names a path and the merge-base cannot be found, so
    which entries this change added cannot be told."""


def this_change(allowed: dict[str, list[str]], base_allowed: dict[str, list[str]]) -> dict[str, list[str]]:
    """``allowed`` less every entry ``base_allowed`` already carried
    (``inherited``), every key kept: what this change itself names."""
    old = inherited(allowed, base_allowed)
    return {name: [p for p in paths if p not in set(old.get(name, []))] for name, paths in allowed.items()}


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """``git`` in ``cwd``'s checkout. A hook's ``GIT_DIR``/``GIT_INDEX_FILE``
    are dropped so the checkout is found from ``cwd``, never inherited."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=False)  # noqa: S603 - a fixed git argv over this repo


def _unknown(why: str) -> BaseUnknownError:
    return BaseUnknownError(
        f"{why}. An allowed list names a path, and only the merge-base tells this change's entries from "
        f"another's leftover. Fetch main with history (git fetch origin main; git fetch --unshallow if the clone "
        f"is shallow) or set {BASE_ENV}=<merge-base sha>."
    )


@functools.cache
def _base(top: str) -> str:
    """The merge-base commit for the checkout at ``top``, once per process."""
    want = os.environ.get(BASE_ENV) or ""
    if not want:
        got = _git(Path(top), "merge-base", "origin/main", "HEAD")
        if got.returncode != 0 or not got.stdout.strip():
            raise _unknown(f"git merge-base origin/main HEAD failed in {top}: {got.stderr.strip()}")
        want = got.stdout.strip()
    got = _git(Path(top), "rev-parse", "--verify", "--quiet", f"{want}^{{commit}}")
    if got.returncode != 0:
        raise _unknown(f"{BASE_ENV}={want} is not a commit in {top}")
    return got.stdout.strip()


@functools.cache
def _reviewed(path: str) -> dict[str, list[str]]:
    p = Path(path)
    allowed = load_allowed(p.parent)
    if not any(allowed.values()):
        return allowed  # nothing to excuse: no base needed
    top = _git(p.parent, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        raise _unknown(f"{p.parent} is not in a git checkout")
    root = Path(top.stdout.strip()).resolve()
    return this_change(allowed, _base_allowed(root, _base(str(root)), p.resolve().relative_to(root).as_posix()))


def _base_allowed(root: Path, base: str, rel: str) -> dict[str, list[str]]:
    """The list at ``rel`` in ``base``: ``{}`` only when the base tree has no
    such path. A path the base has but cannot be read (a missing or corrupt
    object, a partial clone) raises: read as absent, every leftover entry
    would count as this change's."""
    listed = _git(root, "ls-tree", "--name-only", base, "--", rel)
    if listed.returncode != 0:
        raise _unknown(f"git ls-tree {base} -- {rel} failed: {listed.stderr.strip()}")
    if not listed.stdout.strip():
        return {}
    shown = _git(root, "show", f"{base}:{rel}")
    if shown.returncode != 0:
        raise _unknown(f"the merge-base has {rel} but git show cannot read it: {shown.stderr.strip()}")
    return parse_allowed(json.loads(shown.stdout), f"merge-base {rel}")


def reviewed(golden_dir: Path) -> dict[str, list[str]]:
    """``golden_dir``'s allowed list as a golden test must read it: only the
    entries this change added (``this_change`` against the merge-base's copy;
    a list the base has no copy of is all this change's). The base is
    ``$GOLDENS_BASE`` or ``git merge-base origin/main HEAD``; when a list names
    a path and the base cannot be found, ``BaseUnknownError`` (never every
    entry taken as new). Cached per process: one ``git show`` per list."""
    return {k: list(v) for k, v in _reviewed(str(Path(golden_dir).resolve() / ALLOWED)).items()}


def forget_base() -> None:
    """Drop the cached merge-base and lists (a test that changes the base)."""
    _reviewed.cache_clear()
    _base.cache_clear()


__all__ = [
    "ALLOWED",
    "FILE",
    "AllowedListError",
    "BASE_ENV",
    "BaseUnknownError",
    "diffs",
    "forget_base",
    "goldens",
    "inherited",
    "load_allowed",
    "losses",
    "parse_allowed",
    "read_golden",
    "reviewed",
    "stale",
    "this_change",
    "tree_diffs",
    "unallowed",
    "unexplained",
]
