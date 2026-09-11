"""Sync gate for the vendored case-alert routing module (WS-RENDER).

CANONICAL SOURCE is ``operator/skills/deadline-miss-escalator/routing.py``
(the skill that owns ``references/case-alert-routing.md``). The
client-verification-tracker carries a byte-identical copy so its stdlib-only
``pre_run.py`` can resolve recipients without a package install — the exact
``escalation_ledger.py`` vendoring precedent, held by the same kind of gate.
Edit the canonical, restamp the copy — never edit a copy.
"""

from __future__ import annotations

from pathlib import Path

_OPERATOR_ROOT = Path(__file__).resolve().parents[1]
_CANONICAL = _OPERATOR_ROOT / "skills" / "deadline-miss-escalator" / "routing.py"

VENDORED_SKILLS = ("client-verification-tracker",)


def test_canonical_exists() -> None:
    assert _CANONICAL.is_file(), f"canonical routing.py missing at {_CANONICAL}"


def test_vendored_copies_are_byte_identical() -> None:
    canonical = _CANONICAL.read_bytes()
    for skill in VENDORED_SKILLS:
        copy = _OPERATOR_ROOT / "skills" / skill / "routing.py"
        assert copy.is_file(), f"{skill} is missing its vendored routing.py"
        assert copy.read_bytes() == canonical, (
            f"{copy} has drifted from the canonical routing.py. Edit "
            f"{_CANONICAL} and copy it over the vendored file byte-for-byte."
        )
