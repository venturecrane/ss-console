"""Which attorneys a "set up Dana's memos" request may name, and the name each is
filed under. ONE call site for the roster, on purpose.

THE FIRM'S ROSTER FIRST (2026-11-07). When the firm's attorney roster is
installed (``operator_connector_sdk.library.roster``, design 2026-11-06,
"Unified: who an attorney is"), it IS the list: every attorney on it, by the
name the case system gives them, which is the name the drafting resolver files
a matter's attorney under (``caller.attorney_key`` of ``facts.json``'s
``responsible_attorney``). No staff listing is read. A roster that cannot be
read raises ``RosterUnusable(ROSTER_UNREADABLE)``.

WITH NO ROSTER INSTALLED, exactly as before it existed: the firm's AUTHORED
drafting attorneys, by email (``drafting/attorneys.py``), each mapped to the
name the case system's staff record gives that person ("first last").

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

from operator_connector_sdk.library import roster as roster_mod
from operator_connector_sdk.library.caller import attorney_key

#: Hold codes: SMD's to fix, the firm is told nothing.
ROSTER_INPUTS_MISSING = "roster_inputs_missing"
ROSTER_UNMATCHED = "roster_unmatched"
COULD_NOT_LOOK = "could_not_look"
ROSTER_UNREADABLE = "roster_unreadable"
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


def attorneys(inputs: Any, client: Any, roster: roster_mod.Roster | None = None) -> list[Attorney]:
    """The firm's attorneys: the installed roster's (``roster``, read from the
    seat when None), else each authored drafting attorney (``inputs``, a loaded
    ``drafting.firm`` config, or None when it did not load) with the name the
    case system gives the staff member of that email. Raises ``RosterUnusable``."""
    read = roster if roster is not None else roster_mod.load()
    if read.status == roster_mod.UNUSABLE:
        raise RosterUnusable(ROSTER_UNREADABLE)
    if read.status == roster_mod.OK:
        return [Attorney(p.emails[0] if p.emails else "", p.name, attorney_key(p.name)) for p in read.attorneys]
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
    "ROSTER_UNREADABLE",
    "Attorney",
    "RosterUnusable",
    "attorneys",
    "pick",
]
