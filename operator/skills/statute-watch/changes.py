"""What changed since last month's statute report, and the state that remembers it.

No network. ``pre_run.py`` hands in last month's case set (the state file),
this month's selection, and what the departure read found for every case that
left the list; this module says, case by case, what the record now shows.

THE STATE FILE. ``$HERMES_HOME/.smd/statute-watch/last-run.json``, schema
exactly ``{"version": 1, "run_local_date": "YYYY-MM-DD", "cases":
[{"matter_id", "number", "statute_date"}]}``, directory 0700, file 0600,
replaced atomically. ``pre_run`` writes it only after the dispatch envelope
was written, never in a dry run and never when ``SMD_STATUTE_WATCH_NO_STATE=1``
(a proof copy must not move next month's comparison). ``run_local_date`` is
kept for the record and never printed.

WORDED AS THE RECORD SHOWS IT. A case that left the list is classified only
from the fields read this month (status, Statute of Limitation date, Filed
date, Case number) against the statute date it carried last month. Nothing is
inferred: a case that could not be read says so, and a case whose record
explains nothing it can be classified by is "could not be checked".
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

STATE_VERSION = 1
NO_STATE_ENV = "SMD_STATUTE_WATCH_NO_STATE"

NEW = "new"
FILED = "filed"
CLOSED = "closed"
NOT_OPEN = "not_open"
PASSED = "passed"
CHANGED = "changed"
REMOVED = "removed"
UNCHECKED = "unchecked"

#: Body and workbook order: the case that needs a look first.
KIND_ORDER = (PASSED, CHANGED, REMOVED, UNCHECKED, NEW, FILED, CLOSED, NOT_OPEN)

_OPEN_STATUSES = frozenset({"open", "pending"})


@dataclass(frozen=True)
class Change:
    """One line of the "since last month" section, every value read."""

    kind: str
    matter_id: str
    matter_number: str | None
    client: str | None  # body form: last names
    client_workbook: str | None  # "Last, First"
    statute: date | None  # the date the record shows now
    previous: date | None  # last month's date, printed only for CHANGED


def _iso(value: object) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def state_path() -> Path:
    return Path(os.environ.get("HERMES_HOME") or "/opt/data") / ".smd" / "statute-watch" / "last-run.json"


def load_state() -> list[dict] | None:
    """Last run's cases, or None when there was no last run (or its file
    cannot be read, reported on stderr). Entries without a matter id or a
    statute date are dropped."""
    path = state_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(data, dict)
            or data.get("version") != STATE_VERSION
            or not isinstance(data.get("cases"), list)
        ):
            raise ValueError("unrecognized state")
    except Exception as exc:  # noqa: BLE001 - an unreadable state file reads as no last run, reported
        sys.stderr.write("[pre_run] statute-watch: last-run state unreadable (" + type(exc).__name__ + ")\n")
        return None
    out: list[dict] = []
    for entry in data["cases"]:
        if isinstance(entry, dict) and isinstance(entry.get("matter_id"), str) and _iso(entry.get("statute_date")):
            out.append(entry)
    return out


def write_state(today: date, cases) -> bool:
    """Atomic 0600 replace of the state file with this run's case set."""
    try:
        path = state_path()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions — 0700 is owner-only, the tightest mode a directory can usefully carry; the rule misreads it as permissive.
        os.chmod(path.parent, 0o700)
        record = {
            "version": STATE_VERSION,
            "run_local_date": today.isoformat(),
            "cases": [
                {"matter_id": c.matter_id, "number": c.matter_number or "", "statute_date": c.statute.isoformat()}
                for c in cases
            ],
        }
        tmp = path.parent / ".last-run.json.tmp"
        tmp.unlink(missing_ok=True)
        handle = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(record, fh)
        os.replace(tmp, path)
        return True
    except Exception as exc:  # noqa: BLE001 - a state write failure costs next month's comparison, never this send
        sys.stderr.write("[pre_run] statute-watch: last-run state not written (" + type(exc).__name__ + ")\n")
        return False


def state_disabled() -> bool:
    return os.environ.get(NO_STATE_ENV, "").strip() == "1"


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def departed(previous: list[dict], cases) -> list[dict]:
    """Last month's entries whose matter is not listed this month."""
    listed = {c.matter_id for c in cases}
    seen: set[str] = set()
    out: list[dict] = []
    for entry in previous:
        mid = entry["matter_id"]
        if mid not in listed and mid not in seen:
            seen.add(mid)
            out.append(entry)
    return out


def classify(entry: dict, record: object, today: date) -> tuple[str, date | None]:
    """``(kind, statute date the record shows now)`` for one departed case."""
    if not isinstance(record, dict) or record.get("error"):
        return UNCHECKED, None
    previous = _iso(entry.get("statute_date"))
    raw = record.get("statute")
    statute = _iso(raw)
    if record.get("filed") or record.get("caseNumber"):
        return FILED, statute
    status = record.get("status")
    if isinstance(status, str) and status.strip():
        if status.strip().lower() == "closed":
            return CLOSED, statute
        if status.strip().lower() not in _OPEN_STATUSES:
            return NOT_OPEN, statute
    if raw in (None, ""):
        return REMOVED, None
    if statute is None:
        return UNCHECKED, None
    if statute != previous:
        return CHANGED, statute
    if statute < today:
        return PASSED, statute
    return UNCHECKED, statute


def build(previous: list[dict], cases, rows_by_id: dict, departures: dict, today: date) -> list[Change]:
    """Every change since last month, in ``KIND_ORDER`` then statute date.

    ``rows_by_id`` maps a listed matter id to its ``report.Row`` (for new
    cases' names); ``departures`` maps a departed matter id to the departure
    read (``status``, ``statute``, ``filed``, ``caseNumber``, ``title`` and
    the names derived from it, or ``error``)."""
    before = {e["matter_id"] for e in previous}
    out: list[Change] = []
    for case in cases:
        if case.matter_id in before:
            continue
        row = rows_by_id.get(case.matter_id)
        out.append(
            Change(
                kind=NEW,
                matter_id=case.matter_id,
                matter_number=case.matter_number,
                client=getattr(row, "client", None),
                client_workbook=getattr(row, "client_workbook", None),
                statute=case.statute,
                previous=None,
            )
        )
    for entry in departed(previous, cases):
        record = departures.get(entry["matter_id"])
        kind, statute = classify(entry, record, today)
        names = record if isinstance(record, dict) else {}
        number = entry.get("number")
        out.append(
            Change(
                kind=kind,
                matter_id=entry["matter_id"],
                matter_number=number if isinstance(number, str) and number else None,
                client=names.get("client"),
                client_workbook=names.get("client_workbook"),
                statute=statute,
                previous=_iso(entry.get("statute_date")),
            )
        )
    out.sort(key=lambda c: (KIND_ORDER.index(c.kind), c.statute or date.max, c.matter_number or "", c.matter_id))
    return out


def handoff_entries(changes: list[Change]) -> list[dict]:
    """Each change's number with every date the body or workbook may print for
    it: the date the record shows now and last month's date."""
    out = []
    for change in changes:
        entry = {"matter_id": change.matter_id, "matter_number": change.matter_number}
        if change.statute is not None:
            entry["statute_date"] = change.statute.isoformat()
        if change.previous is not None:
            entry["previous_statute_date"] = change.previous.isoformat()
        out.append(entry)
    return out
