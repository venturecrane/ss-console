"""Shared machinery for the vendored-copy sync gates.

Several modules under ``operator/`` have one canonical source and byte-identical
copies stamped into skill directories, because the scheduler stages a skill dir
alone and a stdlib-only ``pre_run.py`` cannot ``pip install`` its way to a
shared package. Each such module has a sync test. Until 2026-09-11 every one of
those tests carried a hand-maintained tuple naming the skills that hold a copy,
which is a second thing to remember: a copy stamped into a new skill and never
added to the tuple was pinned by nothing, and the two ``broker_writer.py``
twins showed what that buys (133 lines each, differing by one docstring word,
gated by no test at all; code review 2026-09-10, Architecture 5).

The gates now DISCOVER copies. Two ways, both here so each test is a few lines:

* :func:`discover_copies` globs ``skills/*/<name>`` for a module that is vendored
  under its own filename (``escalation_ledger.py``, ``routing.py``,
  ``broker_writer.py``). A tenth copy is under the gate the moment it exists.
* :func:`discover_stamps` finds the files that share a FUNCTION BODY with a
  template, for a module vendored under a different filename (the empty-seat
  gate is stamped as ``pre_run.py``). Six bespoke ``pre_run.py`` files share no
  body with the template and are not stamps; the eight stamps share every
  body; a stamp that was edited still shares most of them and is caught, where
  a name-only glob would have called it bespoke.

:func:`assert_byte_identical` is the common assertion, with a FLOOR: a
discovery that finds fewer copies than the known count fails, so a glob that
silently matched nothing cannot pass green.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

OPERATOR_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = OPERATOR_ROOT / "skills"


def discover_copies(name: str, *, exclude: Path | None = None, skills_dir: Path = SKILLS_DIR) -> list[Path]:
    """Every ``skills/*/<name>``, sorted, minus the canonical when it lives there too."""
    found = sorted(skills_dir.glob(f"*/{name}"))
    if exclude is not None:
        exclude = exclude.resolve()
        found = [p for p in found if p.resolve() != exclude]
    return found


def function_body_hashes(path: Path) -> dict[str, str]:
    """Top-level function name -> hash of its source with the name stripped.

    Stripping the name means a renamed copy still matches; hashing the source
    segment (not the AST dump) means a one-token edit no longer matches, which
    is the point: a body that matches IS the template's body.
    """
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            segment = ast.get_source_segment(src, node) or ""
            segment = segment.replace(node.name, "<name>", 1)
            out[node.name] = hashlib.sha256(segment.encode("utf-8")).hexdigest()
    return out


def discover_stamps(template: Path, stamped_as: str, *, skills_dir: Path = SKILLS_DIR) -> list[Path]:
    """Every ``skills/*/<stamped_as>`` that shares at least one function body with ``template``."""
    template_bodies = set(function_body_hashes(template).values())
    stamps: list[Path] = []
    for candidate in sorted(skills_dir.glob(f"*/{stamped_as}")):
        if template_bodies & set(function_body_hashes(candidate).values()):
            stamps.append(candidate)
    return stamps


def _display(path: Path) -> str:
    """Repo-relative when the path is under the operator tree, else as given (test fixtures)."""
    try:
        return str(path.relative_to(OPERATOR_ROOT))
    except ValueError:
        return str(path)


def assert_byte_identical(canonical: Path, copies: list[Path], *, floor: int, restamp_hint: str) -> None:
    """Every copy equals the canonical byte-for-byte, and there are at least ``floor`` of them."""
    assert canonical.is_file(), f"canonical missing at {canonical}"
    assert len(copies) >= floor, (
        f"expected at least {floor} vendored copies of {canonical.name}; discovered {len(copies)}: "
        f"{[_display(p) for p in copies]}. A skill that stopped carrying the copy "
        f"lowers the floor in the same change, on purpose, so the count never drifts down unnoticed."
    )
    canonical_bytes = canonical.read_bytes()
    drifted = [_display(p) for p in copies if p.read_bytes() != canonical_bytes]
    assert not drifted, f"{canonical.name} drifted from {_display(canonical)}: {drifted}. {restamp_hint}"
