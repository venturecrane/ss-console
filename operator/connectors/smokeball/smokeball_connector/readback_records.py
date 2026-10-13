"""Read-back for the record writes: tasks, calendar events, event reminders and
webhook subscriptions. Each is read by its id after the write and the fields
the write SENT are compared with what the vendor now holds; see readback.py for
the result contract and why it exists.

Two vendor facts shape the comparison, both proven live:

* Event times are LOCAL in the event's ``timeZone``, never UTC (vendor: "date
  and time will correlate with the time zone provided"). The sent and stored
  stamps are compared as written, to the minute; nothing is converted. A stored
  time that differs by the zone offset is exactly the defect this catches.
* The ``/events`` LIST hides the matter link (``matterId`` null on every row,
  2026-09). The single event read carries ``matter``, so the link is read
  there, never from a listing.

Text fields are compared whitespace-normalized. A field the vendor returns as
RTF (memos do; tasks and events have not been seen to) cannot be compared as
text and is reported as unchecked rather than as a mismatch.
"""

from __future__ import annotations

import re
from typing import Any

from .local_time import enrich_task
from .readback import MISMATCH, NOT_VISIBLE, confirmed, poll, shortfall

_WS = re.compile(r"\s+")
_TEXT = ("subject", "note", "description", "location")


def _text(value: Any) -> str:
    return _WS.sub(" ", value).strip() if isinstance(value, str) else ""


def _ids(values: Any) -> set[str]:
    out: set[str] = set()
    for v in values or []:
        if isinstance(v, dict) and v.get("id"):
            out.add(str(v["id"]))
        elif isinstance(v, str) and v:
            out.add(v)
    return out


def _link(record: dict[str, Any], key: str) -> str:
    linked = record.get(key)
    if isinstance(linked, dict):
        return str(linked.get("id") or "")
    return str(record.get(f"{key}Id") or "")


def _stamp(value: Any, all_day: bool) -> str:
    """A local stamp to the minute (the day for an all-day event)."""
    s = str(value or "")
    return s[:10] if all_day else s[:16]


def _diff_text(want: dict[str, Any], got: dict[str, Any], diffs: list[str], unchecked: list[str]) -> None:
    for key in _TEXT:
        if key not in want:
            continue
        stored = got.get(key)
        if isinstance(stored, str) and stored.lstrip().startswith("{\\rtf"):
            unchecked.append(key)
        elif _text(stored) != _text(want[key]):
            diffs.append(f"{key} reads {_text(stored)[:80]!r}, sent {_text(want[key])[:80]!r}")


def event_diffs(want: dict[str, Any], got: Any) -> tuple[list[str], list[str]]:
    """``(differences, unchecked fields)`` between a sent event body and the
    single-event read."""
    if not isinstance(got, dict) or not got:
        return ["the event read back empty"], []
    diffs: list[str] = []
    unchecked: list[str] = []
    _diff_text(want, got, diffs, unchecked)
    all_day = bool(want.get("allDay"))
    for key in ("startTime", "endTime"):
        if key in want and _stamp(got.get(key), all_day) != _stamp(want[key], all_day):
            diffs.append(f"{key} reads {got.get(key)!r} (local), sent {want[key]!r}")
    if "allDay" in want and bool(got.get("allDay")) != all_day:
        diffs.append(f"allDay reads {got.get('allDay')!r}, sent {want['allDay']!r}")
    if want.get("timeZone") and str(got.get("timeZone") or "") != str(want["timeZone"]):
        diffs.append(f"timeZone reads {got.get('timeZone')!r}, sent {want['timeZone']!r}")
    if "matterId" in want and _link(got, "matter") != str(want["matterId"] or ""):
        diffs.append(f"the matter link reads {_link(got, 'matter') or 'none'!r}, sent {want['matterId']!r}")
    missing = _ids(want.get("attendees")) - _ids(got.get("attendees"))
    if missing:
        diffs.append(f"attendees missing {sorted(missing)}")
    return diffs, unchecked


def task_diffs(want: dict[str, Any], got: Any) -> tuple[list[str], list[str]]:
    """``(differences, unchecked fields)`` between a sent task body and the
    task read. The due date is compared as the vendor's local day."""
    if not isinstance(got, dict) or not got:
        return ["the task read back empty"], []
    diffs: list[str] = []
    unchecked: list[str] = []
    _diff_text(want, got, diffs, unchecked)
    if want.get("dueDateOnly"):
        day = str(enrich_task(dict(got)).get("localDueDate") or "")
        if day != str(want["dueDateOnly"])[:10]:
            diffs.append(f"the due date reads {day or 'none'!r}, sent {want['dueDateOnly']!r}")
    if "matterId" in want and _link(got, "matter") != str(want["matterId"] or ""):
        diffs.append(f"the matter link reads {_link(got, 'matter') or 'none'!r}, sent {want['matterId']!r}")
    if "isCompleted" in want and bool(got.get("isCompleted")) != bool(want["isCompleted"]):
        diffs.append(f"isCompleted reads {got.get('isCompleted')!r}, sent {want['isCompleted']!r}")
    missing = _ids(want.get("assigneeIds")) - _ids(got.get("assignees") or got.get("assigneeIds"))
    if missing:
        diffs.append(f"assignees missing {sorted(missing)}")
    return diffs, unchecked


def _object_id(result: Any, given: str | None) -> str:
    if given:
        return str(given)
    if isinstance(result, dict):
        return str(result.get("id") or result.get("eventId") or result.get("taskId") or "")
    return ""


def _prove(client: Any, path_of: str, oid: str, want: dict[str, Any], differ: Any, result: Any, kind: str) -> Any:
    if not oid:
        return shortfall(result, NOT_VISIBLE, f"the {kind} write returned no id, so it cannot be read back")
    got, done, error = poll(lambda: client.get(path_of.format(oid)), lambda r: not differ(want, r)[0])
    if got is None:
        why = f" (last read error {error})" if error else ""
        return shortfall(result, NOT_VISIBLE, f"the {kind} {oid} could not be read back{why}")
    diffs, unchecked = differ(want, got)
    if not done:
        return shortfall(result, MISMATCH, f"the {kind} {oid} reads back different: " + "; ".join(diffs))
    note = f"the {kind} read back by id; the fields written match"
    return confirmed(result, note + (f" ({', '.join(unchecked)} not comparable as text)" if unchecked else ""))


def prove_event(client: Any, body: dict[str, Any], result: Any, event_id: str | None = None) -> Any:
    """``create_event`` / ``update_event``: the single event read matches the
    body sent (subject, description, local start and end, all-day, zone,
    matter link, attendees)."""
    return _prove(client, "/events/{}", _object_id(result, event_id), body, event_diffs, result, "event")


def prove_task(client: Any, body: dict[str, Any], result: Any, task_id: str | None = None) -> Any:
    """``create_task`` / ``update_task``: the task read matches the body sent."""
    return _prove(client, "/tasks/{}", _object_id(result, task_id), body, task_diffs, result, "task")


def _rows(resp: Any) -> list[dict[str, Any]]:
    if isinstance(resp, list):
        return [r for r in resp if isinstance(r, dict)]
    if isinstance(resp, dict):
        for key in ("value", "items", "data", "results", "reminders"):
            if isinstance(resp.get(key), list):
                return [r for r in resp[key] if isinstance(r, dict)]
    return []


def prove_reminder(client: Any, event_id: str, body: dict[str, Any], result: Any) -> Any:
    """``create_event_reminder``: the event's reminders include one at the sent
    offset and unit (by the returned id when the write returned one)."""
    want_id = str(result.get("id") or "") if isinstance(result, dict) else ""

    def present(resp: Any) -> bool:
        for row in _rows(resp):
            if want_id and str(row.get("id") or "") == want_id:
                return True
            if row.get("offset") == body.get("offset") and row.get("offsetTypeId") == body.get("offsetTypeId"):
                return True
        return False

    resp, done, error = poll(lambda: client.get(f"/events/{event_id}/reminders"), present)
    if done:
        return confirmed(result, "the reminder reads back on the event")
    if resp is None:
        why = f" (last read error {error})" if error else ""
        return shortfall(result, NOT_VISIBLE, f"the event's reminders could not be read back{why}")
    return shortfall(result, NOT_VISIBLE, f"no reminder at offset {body.get('offset')} appeared on event {event_id}")


def prove_webhook(client: Any, body: dict[str, Any], result: Any) -> Any:
    """``create_webhook_subscription``: the subscription lists by its id (or by
    name and callback URL), carrying every event type sent. The signing key is
    never compared: the vendor does not echo it, and it is not printed."""
    want_id = str(result.get("id") or "") if isinstance(result, dict) else ""

    def match(resp: Any) -> dict[str, Any] | None:
        for row in _rows(resp):
            same = (
                str(row.get("id") or "") == want_id
                if want_id
                else (
                    row.get("name") == body["name"] and row.get("eventNotificationUrl") == body["eventNotificationUrl"]
                )
            )
            if same:
                return row
        return None

    resp, done, error = poll(lambda: client.get("/webhooks"), lambda r: match(r) is not None)
    if not done:
        why = f" (last read error {error})" if error else ""
        return shortfall(result, NOT_VISIBLE, f"the subscription {body['name']!r} did not appear in /webhooks{why}")
    row = match(resp) or {}
    missing = set(body["eventTypes"]) - set(row.get("eventTypes") or [])
    if missing:
        return shortfall(result, MISMATCH, f"the subscription reads back without event types {sorted(missing)}")
    return confirmed(result, "the subscription reads back with every event type sent")


__all__ = ["event_diffs", "prove_event", "prove_reminder", "prove_task", "prove_webhook", "task_diffs"]
