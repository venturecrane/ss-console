"""The byte-identity assertion for a module copied outside the skills tree.

Copies inside skill folders are written by operator/bin/gen-skill-shared.py from
operator/contracts/skill-shared-modules.yaml and checked by
operator/bin/tests/test_skill_shared_generation.py (2026-11-10). Until then this
module also discovered skill copies by glob and by shared function body for nine
per-module sync tests; that discovery now lives in the generator, which fails on
an undeclared copy instead of guessing at one.

:func:`assert_byte_identical` remains for a copy that is not in a skill folder
(``test_logic_in_code_sync.py``). It has a FLOOR: a list shorter than the known
count fails, so an empty list cannot pass green.
"""

from __future__ import annotations

from pathlib import Path

OPERATOR_ROOT = Path(__file__).resolve().parents[1]


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
