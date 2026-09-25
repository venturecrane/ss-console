"""Body construction for Smokeball's full-replace event PUT.

Proven live 2026-09-24 on the A&P tenant (vfy_01M3AYM2WQ7BT99SJF7M6JXBKZ):
``PUT /events/{id}`` is a FULL REPLACE, like ``PUT /tasks/{id}``. A PUT that
carried new dates, allDay, timeZone and description but no attendees 400'd
("Event Attendees must have at least one attendee"). A PUT that carried
subject, dates, description, attendees, type and timeZone but no ``matterId``
succeeded and set the event's ``matter`` to null, so the deadline fell off the
matter's calendar (``/events?MatterId=...``) while still reading back with the
right date. ``merge_event_update`` builds the merged body from the event's
current state plus the requested changes and ``put_event_update`` sends it;
``server.update_event`` is the MCP tool over them.
"""

from __future__ import annotations

from typing import Any, Callable


def _attendee_ids(current: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for a in current.get("attendees") or []:
        if isinstance(a, dict) and a.get("id"):
            ids.append(a["id"])
        elif isinstance(a, str):
            ids.append(a)
    return ids


def merge_event_update(
    current: Any,
    *,
    subject: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    description: str | None = None,
    location: str | None = None,
    all_day: bool | None = None,
    attendees: list[str] | None = None,
    time_zone: str | None = None,
    matter_id: str | None = None,
) -> tuple[dict[str, Any], str | None]:
    """Return ``(put_body, matter_id)`` for an event update.

    Every field the caller does not change is re-sent from the current event,
    the matter link above all. Raises ``ValueError`` before the wire when the
    event is recurring (read-only on the API) or when no attendee can be
    established (the API refuses the PUT).
    """
    if not isinstance(current, dict):
        current = {}
    kind = current.get("type")
    if kind not in (None, "Normal"):
        raise ValueError(
            f"update_event: event type is {kind!r}; recurring events are read-only "
            "on the Smokeball API. Only Normal events can be updated."
        )
    cur_matter = current.get("matter")
    cur_matter_id = cur_matter.get("id") if isinstance(cur_matter, dict) else None
    link = matter_id if matter_id is not None else cur_matter_id
    people = attendees if attendees is not None else _attendee_ids(current)
    if not people:
        raise ValueError(
            "update_event: Smokeball requires at least one attendee on every event "
            "PUT and the event has none to carry forward; pass attendees=[<staff_id>]."
        )
    fields = {
        "subject": subject if subject is not None else current.get("subject"),
        "startTime": start_time if start_time is not None else current.get("startTime"),
        "endTime": end_time if end_time is not None else current.get("endTime"),
        "description": description if description is not None else current.get("description"),
        "location": location if location is not None else current.get("location"),
        "allDay": all_day if all_day is not None else current.get("allDay"),
        "timeZone": time_zone if time_zone is not None else current.get("timeZone"),
        "matterId": link,
        "attendees": people,
        "type": "Normal",
    }
    return {k: v for k, v in fields.items() if v is not None}, link


def put_event_update(
    client: Any,
    event_id: str,
    verify: Callable[[Any, str, str | None, str | None], Any],
    **changes: Any,
) -> Any:
    """Read the event, merge the changes, run the matter-reference guard on any
    new text, and send the full-replace PUT. ``verify`` is the server's
    ``_verify_matter_reference``, passed in so this module does not import the
    MCP server."""
    body, link = merge_event_update(client.get(f"/events/{event_id}"), **changes)
    subject, description = changes.get("subject"), changes.get("description")
    if subject is not None or description is not None:
        verify(client, link or "", subject, description)
    return client.request("PUT", f"/events/{event_id}", json=body)
