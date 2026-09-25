"""Sync gate for the vendored digest_items.py copies.

CANONICAL SOURCE is ``operator/skills/deadline-miss-escalator/digest_items.py``
(the send gate's label masking, ``display_label``, and the digest banding).
task-list-keeper carries a byte-identical copy so a task label on a review line
is masked exactly the way the deadline digest masks it: one reduction the send
gate is known to pass. Copies are discovered by glob
(``skills/*/digest_items.py``). Edit the canonical, restamp the copy.
"""

from __future__ import annotations

from vendored_sync import OPERATOR_ROOT, assert_byte_identical, discover_copies

_CANONICAL = OPERATOR_ROOT / "skills" / "deadline-miss-escalator" / "digest_items.py"

#: Copies besides the canonical as of 2026-09-25: task-list-keeper.
_FLOOR = 1


def test_canonical_exists() -> None:
    assert _CANONICAL.is_file(), f"canonical digest_items.py missing at {_CANONICAL}"


def test_vendored_copies_are_byte_identical() -> None:
    assert_byte_identical(
        _CANONICAL,
        discover_copies("digest_items.py", exclude=_CANONICAL),
        floor=_FLOOR,
        restamp_hint=f"Edit {_CANONICAL.relative_to(OPERATOR_ROOT)} and copy it over the vendored file byte-for-byte.",
    )
