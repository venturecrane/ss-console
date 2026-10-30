"""A litigation job's scope: the shapes the broker queues, normalized, and the
one key per distinct scope that keeps two jobs for the same people from
running at once. Held apart from ``litigation_ledger`` (its size ceiling);
the ledger re-exports ``scope_key`` and ``SCOPE_ID_KEYS``.

The shapes: ``{"all": true}``; staff id lists under ``attorney_staff_ids``,
``paralegal_staff_ids`` or both (a matter must match each); or, since
2026-10-24, ``{"people": {"names": [...], "requester": <email or null>}}``:
every matter the named people (and, for "my cases", the requester, found by
email) hold any staff role on, resolved by the runner against Smokeball's
staff list.
"""

from __future__ import annotations

import re
from typing import Any

from .litigation_report import EnvelopeError

_UUID = re.compile(r"^[0-9a-fA-F-]{32,40}$")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}$")


#: The id lists a scope may carry, in the order ``scope_key`` writes them.
SCOPE_ID_KEYS = ("attorney_staff_ids", "paralegal_staff_ids")


def _ids(raw: Any) -> list[str] | None:
    if isinstance(raw, list) and 0 < len(raw) <= 50 and all(isinstance(i, str) and _UUID.match(i) for i in raw):
        return sorted({i.strip().lower() for i in raw})
    return None


#: A people scope's caps (``{"people": {"names": [...], "requester": ...}}``).
MAX_PEOPLE = 20
MAX_PERSON_CHARS = 100


def _people(raw: Any) -> dict[str, Any] | None:
    """A people scope, normalized, or None. ``names`` are roster names (the
    broker placed each); ``requester`` is the verified sender's address when
    the request said "my cases", else null. At least one of the two."""
    if not (isinstance(raw, dict) and set(raw) == {"names", "requester"}):
        return None
    names, who = raw["names"], raw["requester"]
    if not isinstance(names, list) or len(names) > MAX_PEOPLE or not all(isinstance(n, str) for n in names):
        return None
    clean = sorted({" ".join(n.split()) for n in names})
    if any(not n or len(n) > MAX_PERSON_CHARS for n in clean):
        return None
    if who is not None and not (isinstance(who, str) and _EMAIL.match(who.strip())):
        return None
    who = who.strip().lower() if isinstance(who, str) else None
    if not clean and who is None:
        return None
    return {"names": clean, "requester": who}


def normalize(raw: Any) -> dict[str, Any]:
    if raw == {"all": True}:
        return {"all": True}
    if isinstance(raw, dict) and set(raw) == {"people"}:
        people = _people(raw["people"])
        if people is not None:
            return {"people": people}
    elif isinstance(raw, dict) and raw and set(raw) <= set(SCOPE_ID_KEYS):
        got = {k: _ids(raw[k]) for k in SCOPE_ID_KEYS if k in raw}
        if all(v is not None for v in got.values()):
            return got
    raise EnvelopeError(
        'scope must be {"all": true}, staff id lists under "attorney_staff_ids", "paralegal_staff_ids" or both, '
        'or {"people": {"names": [...], "requester": <email or null>}}'
    )


def scope_key(scope: dict[str, Any]) -> str:
    """One string per distinct scope, so a job underway for one scope never
    blocks a different one: ``all``; ``staff:<ids>`` (attorneys, the key every
    earlier row carries); ``paralegal:<ids>``; ``staff:<ids>|paralegal:<ids>``;
    ``people:<names>`` and/or ``me:<requester>`` (any staff role, 2026-10-24)."""
    report = scope.get("report")
    if report in (
        "deadlines",
        "reassign",
        "discovery12",
    ):  # never blocks (or is blocked by) a status list for the same people
        return f"{report}:" + scope_key({k: v for k, v in scope.items() if k != "report"})
    if scope.get("all") is True:
        return "all"
    people = scope.get("people")
    if isinstance(people, dict):
        named = ["people:" + ",".join(n.lower() for n in people["names"])] if people.get("names") else []
        return "|".join(named + (["me:" + str(people["requester"])] if people.get("requester") else []))
    parts = []
    if scope.get("attorney_staff_ids"):
        parts.append("staff:" + ",".join(scope["attorney_staff_ids"]))
    if scope.get("paralegal_staff_ids"):
        parts.append("paralegal:" + ",".join(scope["paralegal_staff_ids"]))
    return "|".join(parts)


__all__ = ["MAX_PEOPLE", "MAX_PERSON_CHARS", "SCOPE_ID_KEYS", "normalize", "scope_key"]
