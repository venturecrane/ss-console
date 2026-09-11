"""Sync gate for the vendored broker heartbeat writer (``broker_writer.py``).

CANONICAL SOURCE is ``operator/skills/deadline-miss-escalator/broker_writer.py``
(the skill that split the writer out of its ``pre_run.py`` first). Every other
skill that loads the writer as a sibling carries a byte-identical copy,
discovered by glob. Edit the canonical, restamp the copies, never edit a copy.

Why this file exists: the escalator's and the client-verification-tracker's
copies were 133 lines each and differed by one docstring word, pinned by
nothing (code review 2026-09-10, Architecture 5). That is the drift the other
sync gates exist to stop, arrived at by the one module that had no gate.

Run::

    cd operator && python3 -m pytest tests/test_broker_writer_sync.py -q
"""

from __future__ import annotations

from vendored_sync import OPERATOR_ROOT, assert_byte_identical, discover_copies

_CANONICAL = OPERATOR_ROOT / "skills" / "deadline-miss-escalator" / "broker_writer.py"

#: Copies besides the canonical as of 2026-09-11: client-verification-tracker.
_FLOOR = 1


def test_canonical_exists() -> None:
    assert _CANONICAL.is_file(), f"canonical broker_writer.py missing at {_CANONICAL}"


def test_vendored_copies_are_byte_identical() -> None:
    assert_byte_identical(
        _CANONICAL,
        discover_copies("broker_writer.py", exclude=_CANONICAL),
        floor=_FLOOR,
        restamp_hint=f"Edit {_CANONICAL.relative_to(OPERATOR_ROOT)} and copy it over the vendored file byte-for-byte.",
    )
