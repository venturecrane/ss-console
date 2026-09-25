"""Sync gate for the vendored casework_view.py copies (case-manager spec).

CANONICAL SOURCE is ``operator/skills/task-list-keeper/casework_view.py``. The
deadline-miss-escalator carries a byte-identical copy, so the review and the
alarm read the casework ledger with one set of rules: a task the review holds
must never also sit in the deadline digest. Copies are discovered by glob
(``skills/*/casework_view.py``). Edit the canonical, restamp the copy.
"""

from __future__ import annotations

from vendored_sync import OPERATOR_ROOT, assert_byte_identical, discover_copies

_CANONICAL = OPERATOR_ROOT / "skills" / "task-list-keeper" / "casework_view.py"

#: Copies besides the canonical as of 2026-09-25: deadline-miss-escalator.
_FLOOR = 1


def test_canonical_exists() -> None:
    assert _CANONICAL.is_file(), f"canonical casework_view.py missing at {_CANONICAL}"


def test_vendored_copies_are_byte_identical() -> None:
    assert_byte_identical(
        _CANONICAL,
        discover_copies("casework_view.py", exclude=_CANONICAL),
        floor=_FLOOR,
        restamp_hint=f"Edit {_CANONICAL.relative_to(OPERATOR_ROOT)} and copy it over the vendored file byte-for-byte.",
    )
