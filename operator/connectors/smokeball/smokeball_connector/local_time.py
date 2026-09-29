"""Court times in the firm's local time, computed once, in the connector.

Smokeball stores an event's ``startTime`` in UTC with no offset and names the
event's zone in ``timeZone`` (probed 2026-09-28: a hearing posted as 16:30Z with
``America/Los_Angeles`` reads back ``2026-10-06T16:30:00``, the 9:30 a.m.
hearing). A model that prints ``startTime`` writes 4:30 PM for a 9:30 a.m.
hearing: the 2026-09-29 defect (vfy_01M3PWQQPSVGDFY33XQ52RSYFB). So every event
and task a read tool returns carries the local day and clock beside the raw
fields, and the tool docstrings tell the model to write those.

``local_when`` is a port of ``skills/date-prep-brief/file_status.py`` ``_when``
(the one skill that already converted). Pure: no server import, no I/O beyond
reading ``HERMES_TIMEZONE`` for task due dates.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

_FIRM_ZONE_ENV = "HERMES_TIMEZONE"


def _first(item: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def clock(hour: int, minute: int) -> str:
    """``9:30 a.m.``: the way a paralegal writes a time."""
    suffix = "a.m." if hour < 12 else "p.m."
    return str(hour % 12 or 12) + ":" + format(minute, "02d") + " " + suffix


def _to_zone(raw: str, zone: str) -> datetime | None:
    """A stored UTC stamp (naive or ``Z``) as a wall time in ``zone``, or None."""
    try:
        from zoneinfo import ZoneInfo

        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(ZoneInfo(zone))
    except Exception:  # noqa: BLE001 - an unreadable stamp or unknown zone is no time, never a guess
        return None


def local_when(raw_start: str | None, zone: str | None, all_day: bool = False) -> tuple[str | None, str | None]:
    """The local ``(day, time)`` of a stored start in its own zone.

    An all-day event, a start with no clock, or a zone this host cannot resolve
    has no time: ``(None, None)``. A UTC clock is never shown as if it were local."""
    if raw_start is None or zone is None or all_day or len(raw_start) < 16 or raw_start[10] != "T":
        return None, None
    local = _to_zone(raw_start, zone)
    if local is None:
        return None, None
    return local.date().isoformat(), clock(local.hour, local.minute)


def enrich_event(event: Any) -> Any:
    """Add ``localDate`` and ``localTime`` to one event when both resolve.

    Neither key is set otherwise (absent, never null), and ``startTime`` is left
    exactly as the vendor sent it."""
    if not isinstance(event, dict):
        return event
    day, time = local_when(
        _first(event, ("startTime", "StartTime")),
        _first(event, ("timeZone", "TimeZone")),
        event.get("allDay") is True,
    )
    if day is not None and time is not None:
        event["localDate"] = day
        event["localTime"] = time
    return event


def _items(resp: Any) -> list:
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict) and isinstance(resp.get("value"), list):
        return resp["value"]
    return []


def enrich_events(resp: Any) -> Any:
    """Enrich every event in a ``value`` envelope or a bare list; returns ``resp``."""
    for event in _items(resp):
        enrich_event(event)
    return resp


def enrich_task(task: Any) -> Any:
    """Add ``localDueDate`` (and ``localDueDateSource``) to one task.

    ``dueDateOnly`` is the vendor's own local day and wins. Otherwise ``dueDate``
    (a UTC stamp) converts through the seat's ``HERMES_TIMEZONE`` when it is set,
    so a 07:00Z due time lands on the firm's day (source ``firm-timezone``); with
    no zone the UTC day is used and the source says so (``dueDate-utc``)."""
    if not isinstance(task, dict):
        return task
    only = _first(task, ("dueDateOnly", "DueDateOnly"))
    if only is not None:
        task["localDueDate"] = only[:10]
        return task
    due = _first(task, ("dueDate", "DueDate"))
    if due is None or len(due) < 10:
        return task
    zone = os.environ.get(_FIRM_ZONE_ENV, "").strip()
    local = _to_zone(due, zone) if zone and len(due) >= 16 and due[10] == "T" else None
    if local is not None:
        task["localDueDate"] = local.date().isoformat()
        task["localDueDateSource"] = "firm-timezone"
    else:
        task["localDueDate"] = due[:10]
        task["localDueDateSource"] = "dueDate-utc"
    return task


def enrich_tasks(resp: Any) -> Any:
    """Enrich every task in a ``value`` envelope or a bare list; returns ``resp``."""
    for task in _items(resp):
        enrich_task(task)
    return resp


def _next_day(date_str: str) -> str:
    """YYYY-MM-DD -> the following day's YYYY-MM-DD (all-day span normalization)."""
    y, m, d = (int(p) for p in date_str.split("-"))
    return (date(y, m, d) + timedelta(days=1)).isoformat()
