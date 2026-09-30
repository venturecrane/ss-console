"""Court times in the firm's local time, read once, in the connector.

An event's ``startTime`` and ``endTime`` are the firm's LOCAL wall clock in the
event's ``timeZone``, never UTC. The vendor's create-event reference says so:
"Start date and time of the event. Supported date format is ISO
YYYY-MM-DDThh:mm:ss. Note: date and time will correlate with the time zone
provided." A client seat bears it out: 231 firm-entered timed events cluster at
hours 08-10 and 13-15, business hours in ``America/Los_Angeles``; were those
UTC, morning hearings would read 15-17.

So the clock is taken as written and ``timeZone`` is informational only: no
zone arithmetic ever runs here. Shifting a stored ``09:00:00`` from UTC is how a
correctly entered 9:00 a.m. trial once rendered as 2:00 a.m. in a prep note.
Every event and task a read tool returns carries the local day and clock beside
the raw fields, and the tool docstrings tell the model to write those.

Pure: no server import, no I/O.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any


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


def local_when(raw_start: str | None, zone: str | None, all_day: bool = False) -> tuple[str | None, str | None]:
    """The local ``(day, time)`` of a stored start, read as written.

    ``raw_start`` is already the firm's wall clock in ``zone`` (the vendor
    sentence above), so the day is ``raw_start[:10]`` and the time is its
    ``hh:mm``, with no shift. ``zone`` is accepted for the caller's record and
    never used for arithmetic, so an unfamiliar zone name does not hide a time.
    An all-day event, a missing or short start, or a start with no clock has no
    time: ``(None, None)``."""
    del zone  # informational: the stored clock is already local to it
    if raw_start is None or all_day or len(raw_start) < 16 or raw_start[10] != "T" or raw_start[13] != ":":
        return None, None
    hour, minute = raw_start[11:13], raw_start[14:16]
    if not (hour.isdigit() and minute.isdigit()) or int(hour) > 23 or int(minute) > 59:
        return None, None
    try:
        day = date.fromisoformat(raw_start[:10]).isoformat()
    except ValueError:
        return None, None
    return day, clock(int(hour), int(minute))


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

    ``dueDateOnly`` is the vendor's own local day and wins. Otherwise the day of
    ``dueDate`` is taken as written (source ``dueDate``): Smokeball dates are the
    firm's local dates, so no zone shift is applied."""
    if not isinstance(task, dict):
        return task
    only = _first(task, ("dueDateOnly", "DueDateOnly"))
    if only is not None:
        task["localDueDate"] = only[:10]
        return task
    due = _first(task, ("dueDate", "DueDate"))
    if due is None or len(due) < 10:
        return task
    task["localDueDate"] = due[:10]
    task["localDueDateSource"] = "dueDate"
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
