"""Which attorneys a "set up Dana's memos" request may name, and the name each is
filed under. ONE call site for the roster, on purpose.

WHY ONE FUNCTION. There is no single list of a firm's attorneys yet (design
2026-11-06, "Unified: who an attorney is"). This is the THIRD partial list, beside
the drafting inputs' caption blocks (``drafting/attorneys.py``, by email) and the
demand inputs' signer map (``demand/library_pick.py``, by name). It reads the
firm's AUTHORED drafting attorneys, by email, and maps each email to the name the
case system's staff record gives that person ("first last"), because that is the
name the drafting resolver files a matter's attorney under
(``operator_connector_sdk.library.caller.attorney_key`` of
``facts.json``'s ``responsible_attorney``, itself the staff record's first and
last name). The unified roster replaces this one function later; nothing else
here knows where attorneys come from.

FAIL CLOSED, NEVER A GUESS. An authored email the case system has no staff
member for raises ``RosterUnusable(ROSTER_UNMATCHED)``; a staff listing that
could not be read raises ``RosterUnusable(COULD_NOT_LOOK)``; drafting inputs
that do not load raise ``RosterUnusable(ROSTER_INPUTS_MISSING)``. Each holds the
job for SMD. A name the request gives is matched to exactly one attorney by full
name, or by a first or last name only one of them has; none is
``ATTORNEY_NOT_ROSTERED`` and more than one is ``ATTORNEY_AMBIGUOUS``, both told
to the firm (an attorney not on the roster cannot be set up for: identity, not
style).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from operator_connector_sdk.library.caller import attorney_key

#: Hold codes: SMD's to fix, the firm is told nothing.
ROSTER_INPUTS_MISSING = "roster_inputs_missing"
ROSTER_UNMATCHED = "roster_unmatched"
COULD_NOT_LOOK = "could_not_look"
#: Outcomes the firm is told.
ATTORNEY_NOT_ROSTERED = "attorney_not_rostered"
ATTORNEY_AMBIGUOUS = "attorney_ambiguous"

_STAFF_PAGE = 500


class RosterUnusable(RuntimeError):
    """The roster could not be built; ``code`` is the hold code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Attorney:
    """``email`` as the firm authored it; ``case_system_name`` as its staff
    record reads; ``folded_name`` the key the library ledger files it under."""

    email: str
    case_system_name: str
    folded_name: str


def _staff(client: Any) -> list[dict[str, Any]]:
    try:
        resp = client.get("/staff", Limit=_STAFF_PAGE, Offset=0)
    except Exception as exc:  # any transport fault is "could not look", never "nobody"
        raise RosterUnusable(COULD_NOT_LOOK) from exc
    rows = resp.get("value") if isinstance(resp, dict) else None
    if not isinstance(rows, list):
        raise RosterUnusable(COULD_NOT_LOOK)
    return [r for r in rows if isinstance(r, dict)]


def _name(row: dict[str, Any]) -> str:
    return " ".join(str(row.get(k) or "").strip() for k in ("firstName", "lastName")).strip()


def attorneys(inputs: Any, client: Any) -> list[Attorney]:
    """The firm's attorneys: each authored drafting attorney (``inputs``, a
    loaded ``drafting.firm`` config, or None when it did not load) with the name
    the case system gives the staff member of that email. Raises
    ``RosterUnusable``."""
    if inputs is None:
        raise RosterUnusable(ROSTER_INPUTS_MISSING)
    authored = [str(a.get("email") or "").strip().lower() for a in inputs.attorneys]
    by_email: dict[str, str] = {}
    for row in _staff(client):
        email = str(row.get("email") or "").strip().lower()
        if email and _name(row):
            by_email.setdefault(email, _name(row))
    out = []
    for email in authored:
        name = by_email.get(email)
        if not name:
            raise RosterUnusable(ROSTER_UNMATCHED)
        out.append(Attorney(email, name, attorney_key(name)))
    return out


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[\s.,]+", text.casefold()) if t]


def pick(named: str, roster: list[Attorney]) -> tuple[Attorney | None, str]:
    """The one attorney ``named`` is, or (None, the outcome the firm is told)."""
    want = attorney_key(named)
    full = [a for a in roster if a.folded_name == want]
    if len(full) == 1:
        return full[0], ""
    words = _tokens(named)
    if len(full) > 1 or len(words) != 1:
        return None, ATTORNEY_AMBIGUOUS if len(full) > 1 else ATTORNEY_NOT_ROSTERED
    part = [a for a in roster if words[0] in (_tokens(a.case_system_name)[:1] + _tokens(a.case_system_name)[-1:])]
    if len(part) == 1:
        return part[0], ""
    return None, ATTORNEY_AMBIGUOUS if part else ATTORNEY_NOT_ROSTERED


__all__ = [
    "ATTORNEY_AMBIGUOUS",
    "ATTORNEY_NOT_ROSTERED",
    "COULD_NOT_LOOK",
    "ROSTER_INPUTS_MISSING",
    "ROSTER_UNMATCHED",
    "Attorney",
    "RosterUnusable",
    "attorneys",
    "pick",
]
