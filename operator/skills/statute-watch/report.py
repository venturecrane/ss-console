"""Pure selection for statute-watch: which open cases the monthly report lists.

No I/O. ``pre_run.py`` hands in what the connector-venv pull read and gets back
the cases, soonest first, plus the two counts the report must state rather than
drop: how many open matters were read, and how many could not be checked.

THE SELECTION RULE (the firm's own words, the ask this routine answers): an
open or pending case whose Smokeball Statute of Limitation date falls from
today through the next three months, with no Filed date and no Case number.
"Three months" is ``WINDOW_DAYS`` counted from the seat-local day, the same
window the first run of this report used. This module compares an authored
date to today; it never produces a date.

A FAILED READ IS COUNTED, NEVER DROPPED. A matter whose layout could not be
read, or whose statute field holds something that is not a date, is not
"not due": it is a case nobody checked. It lands in ``unreadable`` and the
report says how many. A matter list that could not be read at all raises
``PullFailed``: there is no report to send, and the caller sends nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

#: Today through today + 91 days: "the next three months".
WINDOW_DAYS = 91

#: At most this many cases are listed; the rest are counted in one line. The
#: overlay seeds at most 100 matter records from one handoff, so a listed case
#: is always a case whose number and date were handed over together.
RENDER_CAP = 100

_CAPTION_TOKENS = frozenset({"v", "vs", "versus"})
_NAME_MAX_CHARS = 40


#: A matter list at least this long in which NO matter carries a readable
#: statute date is not a firm with no statutes: it is a read that came back
#: without the field (a renamed layout key, a changed payload). Reported as a
#: failure rather than rendered as "no cases", which would be a silent zero.
SILENT_ZERO_FLOOR = 20


class PullFailed(Exception):
    """The matter list itself could not be read: there is nothing to report."""


class StatuteFieldAbsent(PullFailed):
    """Many matters were read and none carried a statute date at all."""


@dataclass(frozen=True)
class Case:
    """One open, unfiled case with a statute date inside the window."""

    matter_id: str
    matter_number: str | None
    statute: date
    days_left: int
    client_ids: tuple[str, ...]
    staff_id: str | None


@dataclass(frozen=True)
class Selection:
    open_matters: int
    unreadable: int
    cases: tuple[Case, ...]


@dataclass(frozen=True)
class Row:
    """One report line's values, every one read, none composed."""

    matter_number: str | None
    statute: date
    days_left: int
    client: str | None
    attorney: str | None
    attorney_assigned: bool
    court_documents: int | None


def iso_day(value: object) -> date | None:
    """The calendar day an ISO date or datetime string names, or None."""
    if not isinstance(value, str) or len(value) < 10:
        return None
    head = value[:10]
    if head[4] != "-" or head[7] != "-" or not head.replace("-", "").isdigit():
        return None
    try:
        return date.fromisoformat(head)
    except ValueError:
        return None


def _text(value: object) -> str | None:
    if isinstance(value, (str, int)) and str(value).strip():
        return str(value).strip()
    return None


def _in_window(matter: dict, today: date, window_days: int) -> tuple[bool, date | None]:
    """``(readable, statute day in window or None)`` for one pulled matter."""
    if matter.get("layoutError"):
        return False, None
    raw = matter.get("statute")
    if raw in (None, ""):
        return True, None
    day = iso_day(raw)
    if day is None:
        return False, None
    if matter.get("filed") or matter.get("caseNumber"):
        return True, None
    if not (0 <= (day - today).days <= window_days):
        return True, None
    return True, day


def select(raw: object, today: date, window_days: int = WINDOW_DAYS) -> Selection:
    """The in-window unfiled cases, soonest first (ties by matter number).

    Raises ``PullFailed`` when the matter list was not read, and
    ``StatuteFieldAbsent`` when ``SILENT_ZERO_FLOOR`` or more matters were read
    and not one carried a readable statute date."""
    if not isinstance(raw, dict) or raw.get("listError") or not isinstance(raw.get("matters"), list):
        raise PullFailed("matter list unavailable")
    cases: list[Case] = []
    unreadable = 0
    seen: set[str] = set()
    dated = 0
    for matter in raw["matters"]:
        if not isinstance(matter, dict):
            unreadable += 1
            continue
        matter_id = _text(matter.get("id"))
        if matter_id is None or matter_id in seen:
            continue
        seen.add(matter_id)
        if iso_day(matter.get("statute")) is not None:
            dated += 1
        readable, day = _in_window(matter, today, window_days)
        if not readable:
            unreadable += 1
            continue
        if day is None:
            continue
        client_ids = tuple(c for c in (_text(v) for v in matter.get("clientIds") or []) if c)
        cases.append(
            Case(
                matter_id=matter_id,
                matter_number=_text(matter.get("number")),
                statute=day,
                days_left=(day - today).days,
                client_ids=client_ids,
                staff_id=_text(matter.get("staffId")),
            )
        )
    if len(seen) >= SILENT_ZERO_FLOOR and dated == 0:
        raise StatuteFieldAbsent("no matter carried a statute date")
    cases.sort(key=lambda c: (c.statute, c.matter_number or "", c.matter_id))
    return Selection(open_matters=len(seen), unreadable=unreadable, cases=tuple(cases))


def surname(raw: object) -> str | None:
    """A last name as the report prints it: letters only, never a caption.

    The field is the contact's last name, so it should be one name; this
    guards the record that is not. Periods are removed, other single-letter
    initials dropped, and the name is cut at a "v", "vs", "versus" or "in re"
    token, so no rendered name can read as a case caption or a reporter cite to
    the send gate ("Garcia v. Allstate" renders "Garcia"; a middle initial "V."
    is what makes "Maria V. Garcia" a caption, so it cuts there too). A token
    carrying a digit is dropped."""
    if not isinstance(raw, str):
        return None
    kept: list[str] = []
    for token in raw.replace(".", " ").split():
        clean = re.sub(r"[^A-Za-z'\-]", "", token)
        lowered = clean.lower()
        if lowered in _CAPTION_TOKENS:
            break
        if lowered == "re" and kept and kept[-1].lower() == "in":
            kept.pop()
            break
        if len(clean) < 2 or any(ch.isdigit() for ch in token):
            continue
        kept.append(clean)
    name = " ".join(kept)[:_NAME_MAX_CHARS].strip()
    return name or None


def rows(cases: tuple[Case, ...] | list[Case], details: dict) -> list[Row]:
    """Join each listed case to what the detail pull read about it."""
    staff_raw = details.get("staff")
    staff: dict = staff_raw if isinstance(staff_raw, dict) else {}
    matters_raw = details.get("matters")
    per_matter: dict = matters_raw if isinstance(matters_raw, dict) else {}
    out: list[Row] = []
    for case in cases:
        facts = per_matter.get(case.matter_id)
        facts = facts if isinstance(facts, dict) else {}
        docs = facts.get("courtDocuments")
        out.append(
            Row(
                matter_number=case.matter_number,
                statute=case.statute,
                days_left=case.days_left,
                client=surname(facts.get("clientLastName")),
                attorney=surname(staff.get(case.staff_id)) if case.staff_id else None,
                attorney_assigned=case.staff_id is not None,
                court_documents=docs if isinstance(docs, int) and not isinstance(docs, bool) else None,
            )
        )
    return out
