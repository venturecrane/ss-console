"""Sync gate for the vendored done_since.py copies (case-manager spec, Job 3).

CANONICAL SOURCE is ``operator/skills/task-list-keeper/done_since.py``. The
deadline-miss-escalator and date-prep-brief carry byte-identical copies, so the
task review, the daily digest and the date-prep brief tell a person about the
same finished work in the same words, and no two of them can disagree about
what is still untold. Copies are discovered by glob (``skills/*/done_since.py``).
Edit the canonical, restamp the copies.
"""

from __future__ import annotations

from vendored_sync import OPERATOR_ROOT, assert_byte_identical, discover_copies

_CANONICAL = OPERATOR_ROOT / "skills" / "task-list-keeper" / "done_since.py"

#: Copies besides the canonical as of 2026-09-25: deadline-miss-escalator, date-prep-brief.
_FLOOR = 2


def test_canonical_exists() -> None:
    assert _CANONICAL.is_file(), f"canonical done_since.py missing at {_CANONICAL}"


def test_vendored_copies_are_byte_identical() -> None:
    assert_byte_identical(
        _CANONICAL,
        discover_copies("done_since.py", exclude=_CANONICAL),
        floor=_FLOOR,
        restamp_hint=f"Edit {_CANONICAL.relative_to(OPERATOR_ROOT)} and copy it over the vendored file byte-for-byte.",
    )
