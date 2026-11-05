"""`facts`: the header of a review record, read from the matter's own record.

The reader used to copy these from its pack into the record, and the copy was
what the File Status printed and what ``rank`` ranked on (ADR 0104). Here code
reads them from the inventory, and whatever the reader typed is replaced by
:func:`settle`, the difference recorded:

* ``clients``, ``matter_type``, ``status``, ``opened``, ``responsible``,
  ``assisting``, ``number`` and ``matter_id``: the matter's record.
* ``date_of_loss``: the case details' accident date
  (``form_letter_facts.date_of_loss``, the form letters' own reader).
* ``statute_date``: the case details' statute date; else what the firm's statute
  reminder tasks imply. A reminder's due date is NOT the statute date: "1 week
  before statute runs" is due a week before it, so its offset is added back. The
  shortest reminder decides (a month back from 3/31 lands on 2/28, and forward
  again on 3/28); reminders further apart than month ends explain, or a reminder
  that disagrees with the case details, are put to the firm as a question and the
  earlier reading never loses silently.

A date code does not have is the reader's only when a document it read prints
it (``validate``): the reader cites, never computes. A stored record from before
this rule keeps its uncited date until a re-read: re-ranking must not drop a
deadline it holds, and a re-read must cite it or clear it.

The statute's window is ``rank``'s alone, so a reader's finding that restates the
date or a window is dropped once code has the date. Nothing here calls a model.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from typing import Any

from smokeball_connector.form_letter_facts import date_of_loss as layout_date_of_loss

from ..litigation.deadlines.pull import SOL
from ..proof import checks

#: Header fields code always owns.
CODE_OWNED = ("number", "matter_id", "clients", "matter_type", "status", "opened", "responsible", "assisting")
#: (date field, where it came from, the document the reader cites when code has none).
DATED = (
    ("date_of_loss", "date_of_loss_source", "date_of_loss_source_doc_id"),
    ("statute_date", "statute_source", "statute_source_doc_id"),
)
CITE_KEYS = tuple(c for _, _, c in DATED)
CASE_DETAILS = "case_details"
REMINDER = "statute_reminder"
DOCUMENT = "document"
#: How each source reads, in the pack and on the print-out.
SOURCE_WORDS = {
    CASE_DETAILS: "the case details",
    REMINDER: "a statute reminder task",
    DOCUMENT: "a document in the file",
}
#: A question code wrote opens with this, so the next settle can replace it.
ASKS = "Statute date check: "
#: Month-end arithmetic moves a reminder's reading by at most this; further apart is a disagreement.
TOLERANCE_DAYS = 3
NO_NAME = "(no name entered)"
_OFFSET = re.compile(r"(\d+)\s*(day|week|month|year)s?", re.I)
_STATUTE = re.compile(r"\bstatute\b", re.I)
_WINDOW = re.compile(r"\b(?:inside|within)\s+\d+\s+days?\b", re.I)
_DASHES = ("—", "–")


def _flat(v: Any) -> str:
    """A firm's own spelling, with no dash a record may not hold (``validate``
    and ``book`` refuse one, and the reader could never fix a name it did not type)."""
    out = " ".join(str(v or "").split())
    for d in _DASHES:
        out = out.replace(d, "-")
    return out


def _date(v: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(v or "")[:10])
    except ValueError:
        return None


def _md(d: dt.date) -> str:
    return f"{d.month}/{d.day}/{str(d.year)[2:]}"


def _layout(inv: dict[str, Any]) -> dict[str, Any]:
    """Every layout value on the matter, merged; the first non-empty wins (the
    shape ``form_letter_facts.matter_layout_values`` reads)."""
    merged: dict[str, Any] = {}
    for item in (inv.get("layouts") or {}).values():
        values = item.get("values") if isinstance(item, dict) else None
        for key, value in (values or {}).items():
            if merged.get(key) in (None, "") and value not in (None, ""):
                merged[key] = value
    return merged


def _forward(day: dt.date, n: int, unit: str) -> dt.date:
    if unit == "day":
        return day + dt.timedelta(days=n)
    if unit == "week":
        return day + dt.timedelta(weeks=n)
    months = n if unit == "month" else n * 12
    year, month = divmod(day.year * 12 + day.month - 1 + months, 12)
    return dt.date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def reminder_readings(inv: dict[str, Any], firm: Any) -> list[dt.date]:
    """What each dated statute reminder says the statute date is, shortest
    reminder first. An undated copy says nothing."""
    out: list[tuple[int, dt.date]] = []
    for t in [*(inv.get("tasks_open") or []), *(inv.get("tasks_done") or [])]:
        subject = str(t.get("subject") or "").strip()
        due = _date(t.get("dueDate") or t.get("dueDateOnly"))
        if due is None or not any(p.fullmatch(subject) for p in firm.statute_rx):
            continue
        m = _OFFSET.search(subject)
        if m is None:
            out.append((0, due))  # names no offset: the reminder is for the day itself
            continue
        reading = _forward(due, int(m.group(1)), m.group(2).lower())
        out.append(((reading - due).days, reading))
    return [d for _, d in sorted(out, key=lambda x: x[0])]


def _statute(inv: dict[str, Any], firm: Any) -> tuple[str, str, list[str]]:
    """(date, source, questions for the firm)."""
    authored = _date(_layout(inv).get(SOL))
    readings = reminder_readings(inv, firm)
    far = [d for d in readings if abs((d - (authored or readings[0])).days) > TOLERANCE_DAYS]
    if authored is not None:
        if not far:
            return authored.isoformat(), CASE_DETAILS, []
        said = ", ".join(_md(d) for d in sorted(set(far)))
        return (
            authored.isoformat(),
            CASE_DETAILS,
            [
                f"{ASKS}the case details say {_md(authored)}, and a statute reminder task points to {said}. Which one stands?"
            ],
        )
    if not readings:
        return "", "", []
    if not far:
        return readings[0].isoformat(), REMINDER, []
    said = ", ".join(_md(d) for d in sorted(set(readings)))
    return (
        min(readings).isoformat(),
        REMINDER,
        [f"{ASKS}the statute reminder tasks point to different dates ({said}). Which one stands?"],
    )


def _date_of_loss(inv: dict[str, Any]) -> tuple[str, str]:
    value = layout_date_of_loss(_layout(inv)).value
    if not value:
        return "", ""
    return dt.datetime.strptime(value, "%m/%d/%Y").date().isoformat(), CASE_DETAILS


def header(inv: dict[str, Any], firm: Any) -> dict[str, Any]:
    """Everything the record's header holds, from the inventory: the fields of
    ``CODE_OWNED`` and ``DATED`` (with their sources), and ``questions``, the
    conflicts only a person can settle. A date code cannot read is empty."""
    m, d = inv.get("matter") or {}, inv.get("detail") or {}
    kind = d.get("matterType")
    kind = kind.get("name") if isinstance(kind, dict) else kind
    clients = [_flat(c.get("name")) for c in inv.get("clients") or [] if isinstance(c, dict)]
    loss, loss_source = _date_of_loss(inv)
    statute, statute_source, questions = _statute(inv, firm)
    opened = _date(d.get("openedDate"))
    return {
        "number": str(m.get("number") or ""),
        "matter_id": str(m.get("id") or ""),
        "clients": [c for c in clients if c and c != NO_NAME],
        "matter_type": _flat(kind),
        "status": _flat(m.get("status")),
        "opened": opened.isoformat() if opened else "",
        "responsible": _flat(m.get("responsible")),
        "assisting": _flat(m.get("assisting")),
        "date_of_loss": loss,
        "date_of_loss_source": loss_source,
        "statute_date": statute,
        "statute_source": statute_source,
        "questions": questions,
    }


def _same(typed: Any, code: Any) -> bool:
    if isinstance(typed, list) and isinstance(code, list):
        return [_flat(x).casefold() for x in typed] == [_flat(x).casefold() for x in code]
    return _flat(typed).casefold() == _flat(code).casefold()


def restates_statute(finding: Any) -> bool:
    """A reader's time-sensitive line that states the statute's date or a window
    on it. One that only names a statute (a reminder with no due date) does not."""
    text = str(finding.get("finding") if isinstance(finding, dict) else finding)
    return bool(_STATUTE.search(text) and (checks.dates(text) or _WINDOW.search(text)))


def settle(rec: dict[str, Any], inv: dict[str, Any], firm: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(the record with its header code's, every field the reader typed that
    differed). Never mutates ``rec``. Settling a settled record changes nothing."""
    h = header(inv, firm)
    out = dict(rec)
    differences: list[dict[str, Any]] = []
    for field in CODE_OWNED:
        typed = rec.get(field)
        if typed not in (None, "", []) and not _same(typed, h[field]):
            differences.append({"field": field, "typed": typed, "code": h[field]})
        out[field] = h[field]
    for field, source, cite in DATED:
        typed = rec.get(field)
        if h[field]:
            if typed not in (None, "") and not _same(typed, h[field]):
                differences.append({"field": field, "typed": typed, "code": h[field]})
            out[field], out[source] = h[field], h[source]
        else:
            out[source] = DOCUMENT if typed and rec.get(cite) else ""
    questions = rec.get("questions")
    if isinstance(questions, list) or h["questions"]:
        kept = [q for q in questions or [] if not str(q).startswith(ASKS)]
        out["questions"] = [*kept, *h["questions"]]
    findings = rec.get("time_sensitive")
    if h["statute_date"] and isinstance(findings, list):
        out["time_sensitive"] = [f for f in findings if not restates_statute(f)]
    return out, differences


__all__ = [
    "ASKS",
    "CASE_DETAILS",
    "CITE_KEYS",
    "CODE_OWNED",
    "DATED",
    "DOCUMENT",
    "REMINDER",
    "SOURCE_WORDS",
    "header",
    "reminder_readings",
    "restates_statute",
    "settle",
]
