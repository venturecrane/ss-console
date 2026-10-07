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

``with_responsible_attorney`` is the create-side rule: ``server.create_event``
puts the matter's responsible attorney on every event it creates on a matter.
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


def _responsible_staff_id(matter: Any) -> str | None:
    """The matter's responsible attorney as a staff id, or None.

    The raw ``GET /matters/{id}`` carries it twice: ``personResponsible`` as a
    ``{id, href, rel}`` link and the flat ``personResponsibleStaffId``. Read the
    link first and fall back to the flat field."""
    if not isinstance(matter, dict):
        return None
    person = matter.get("personResponsible")
    staff_id = person.get("id") if isinstance(person, dict) else None
    if not staff_id:
        staff_id = matter.get("personResponsibleStaffId")
    return staff_id if isinstance(staff_id, str) and staff_id else None


def with_responsible_attorney(client: Any, matter_id: str | None, attendees: list[str]) -> list[str]:
    """Return ``attendees`` with the matter's responsible attorney appended.

    Every event the Operator creates on a matter lands on that matter's
    responsible attorney's calendar (firm ask, 2026-09-24: two deadline events
    copied their attendees from an existing paralegal-only event, so the
    attorney never saw them). Caller order is kept; the attorney is appended
    when missing and never duplicated. Fail-open: no matter, a failed matter
    read, or a matter with no responsible person leaves ``attendees`` as given,
    because a read failure must not block the firm's deadline write."""
    people = list(attendees)
    if not matter_id:
        return people
    try:
        staff_id = _responsible_staff_id(client.get(f"/matters/{matter_id}"))
    except Exception:  # noqa: BLE001 - enrichment must never break the write
        return people
    if staff_id and staff_id not in people:
        people.append(staff_id)
    return people
