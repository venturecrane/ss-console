"""``python -m jobs.attorneys.agree``: does each lane's own attorney list agree
with the firm's installed roster? The seat check ``attorney-roster-agrees``.

Once a roster is installed it wins: the drafting caption blocks, the demand
signatures and the form letters' signers come from it. Each lane's own list
stays in its firm inputs (the drafting inputs' ``attorneys``, the demand
inputs' ``attorneys``, customer.yaml's ``form_letters.signers``) until SMD
retires it, and a list that says something the roster does not is a choice
SMD must settle. So this counts every disagreement and exits non-zero on any,
which fails the self-test and pages SMD (``selftest_failing``). Never silent.

* no roster installed: exit 0, "nothing to compare" (every reader uses its own list);
* a roster that cannot be read: exit 1;
* a lane whose own inputs are absent or do not load: not compared, and said so
  (that lane's own checks already fail on inputs that do not load).

The words carry counts and positions only, never a person's name: the self-test
output leaves the seat.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

from operator_connector_sdk.library import roster as roster_mod


def _drafting() -> list[dict[str, Any]] | None:
    from ..drafting import firm

    return list(firm.load().attorneys)


def _demand() -> dict[str, dict[str, Any]] | None:
    from ..demand import firm

    return dict(firm.load().data["attorneys"])


def _letters() -> dict[str, dict[str, Any]] | None:
    from smokeball_connector.form_letter_facts import authored_signers

    return authored_signers()


def _read(name: str, reader: Callable[[], Any], skipped: list[str]) -> Any:
    try:
        return reader()
    except Exception as exc:  # noqa: BLE001 - absent or unloadable inputs are not compared; the lane's own checks judge them
        skipped.append(f"{name} ({type(exc).__name__})")
        return None


def check(roster: roster_mod.Roster) -> tuple[int, str]:
    """(exit code, one line) for ``roster`` against the seat's lane lists."""
    if roster.status == roster_mod.ABSENT:
        return 0, "no attorney roster installed: nothing to compare, each lane reads its own list"
    if roster.status != roster_mod.OK:
        return 1, f"the installed attorney roster cannot be read: {roster.reason}"
    skipped: list[str] = []
    found = roster_mod.disagreements(
        roster,
        drafting=_read("drafting inputs", _drafting, skipped),
        demand=_read("demand inputs", _demand, skipped),
        letters=_read("form letter signers", _letters, skipped),
    )
    note = f"; not compared: {', '.join(skipped)}" if skipped else ""
    if found:
        return 1, f"{len(found)} disagreement(s) with the attorney roster, which wins: {'; '.join(found)}{note}"
    return 0, f"every lane list agrees with the attorney roster ({len(roster.attorneys)} attorney(s)){note}"


def main() -> int:
    code, line = check(roster_mod.load())
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
