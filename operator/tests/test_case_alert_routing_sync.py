"""Sync gate for the vendored case-alert routing module (WS-RENDER).

CANONICAL SOURCE is ``operator/skills/deadline-miss-escalator/routing.py``
(the skill that owns ``references/case-alert-routing.md``). The
client-verification-tracker carries a byte-identical copy so its stdlib-only
``pre_run.py`` can resolve recipients without a package install, the exact
``escalation_ledger.py`` vendoring precedent, held by the same kind of gate.
Copies are discovered by glob (``skills/*/routing.py``), so a third skill that
vendors the module is under the gate the moment its copy exists.
Edit the canonical, restamp the copy, never edit a copy.
"""

from __future__ import annotations

from tests.vendored_sync import OPERATOR_ROOT, assert_byte_identical, discover_copies

_CANONICAL = OPERATOR_ROOT / "skills" / "deadline-miss-escalator" / "routing.py"

#: Copies besides the canonical as of 2026-09-11: client-verification-tracker.
_FLOOR = 1


def test_canonical_exists() -> None:
    assert _CANONICAL.is_file(), f"canonical routing.py missing at {_CANONICAL}"


def test_vendored_copies_are_byte_identical() -> None:
    assert_byte_identical(
        _CANONICAL,
        discover_copies("routing.py", exclude=_CANONICAL),
        floor=_FLOOR,
        restamp_hint=f"Edit {_CANONICAL.relative_to(OPERATOR_ROOT)} and copy it over the vendored file byte-for-byte.",
    )
