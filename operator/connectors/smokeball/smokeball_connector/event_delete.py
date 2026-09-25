"""Delete a set of calendar events, as an act an administrator confirms.

TWO TOOLS, AND WHY. The act's payload is the list the administrator reads and
answers, so every value in it has to be a vendor fact, not the model's words:

* ``prepare_event_deletion`` (READ) reads each matter's live calendar and
  returns the manifest: one entry per event, carrying the event id, the matter
  id, the matter number, the subject and the start time, exactly as the vendor
  holds them. It deletes nothing.
* ``delete_events`` (DESTRUCTIVE) takes that manifest. On a seat that authors
  ``destructive: confirm`` the trust gate WITHHOLDS the call, the broker renders
  the manifest as one ``[act ...]`` line, and only an administrator's emailed
  yes to that line replays the stored manifest into this tool.

At confirm time this module re-reads every matter's live calendar and deletes
an entry ONLY when the event is still on that matter and its subject, start
time and matter number still equal what the administrator was shown. Anything
else is skipped and reported with its reason. That re-check is also what makes
a manifest the model composed by hand harmless: an entry whose subject or date
is not the vendor's own never matches, so nothing the administrator did not
read can be deleted.

Vendor shape (read from the published OpenAPI at docs.smokeball.com on
2026-09-25, vfy_01M3CZXRJZTVJMXXG1Q0YEJZBF; NOT yet exercised against a live
tenant): ``DELETE /events/{eventId}`` answers **202** with a hypermedia Link
when accepted and **404** when the event does not exist. Deletion is
asynchronous and soft: ``GET /events/{id}`` reads back ``isDeleted: true``, and
an unflagged ``GET /events`` keeps listing the tombstone. The only proof of an
event's matter link is ``GET /events?MatterId=<id>``: the unfiltered list shows
``matter`` null for every event.

THE CAP. One act covers at most :data:`MAX_EVENTS_PER_ACT` events. Fifty lines
is what an administrator can actually check one by one in a single email, and
it keeps the act line inside the broker's readback ceiling. A calendar larger
than that is cleared in several acts, each confirmed on its own: the manifest
says how many remain.

Recurring series and occurrences are never deleted here: what a DELETE does to
a series is not documented, and they are read-only on the API for create and
update. They are listed as skipped.

Both tools register through ``attachment_tools.register`` (the one registrar
the size-ratcheted ``server.py`` already calls), so ``server.py`` does not grow.
"""

from __future__ import annotations

import re
import time
from typing import Any

from .client import _MATTER_NUMBERISH, SmokeballApiError

#: The most events one act may delete. See the module docstring.
MAX_EVENTS_PER_ACT = 50

#: The most matters one manifest may span.
MAX_MATTERS_PER_ACT = 20

#: Page size for the calendar reads, and the most pages one matter may take.
#: 500 is the vendor's page cap; 40 pages is 20,000 live events on ONE matter.
#: Running out of pages refuses rather than assumes.
_PAGE = 500
_MAX_PAGES = 40

#: Read-back attempts and the pause before each retry (seconds). The vendor
#: applies deletes asynchronously, so the first read may still show the event.
_ATTEMPTS = 4
_BACKOFF = (0.5, 1.5, 3.0)

#: Event and matter ids as the vendor issues them. Anything else is refused
#: before a request is built from it.
_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")

_ENTRY_KEYS = ("event_id", "matter_id", "matter_number", "subject", "start_time")


class EventDeletionRefused(ValueError):
    """A request this module will not act on. Nothing was deleted."""


# ---- shared reads ---------------------------------------------------------
def _require_id(value: Any, name: str) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not _ID.match(text):
        raise EventDeletionRefused(f"{name} {value!r} is not a Smokeball id; nothing was deleted.")
    return text


def _require_matter_id(value: Any) -> str:
    text = _require_id(value, "matter_id")
    if _MATTER_NUMBERISH.fullmatch(text):
        raise EventDeletionRefused(
            f"'{text}' is not a matter id (Smokeball matter ids are UUIDs). It looks like a "
            "matter number; resolve the id first (list_matters / get_matter return both). "
            "Nothing was deleted."
        )
    return text


def _items(resp: Any) -> list[dict[str, Any]]:
    if isinstance(resp, dict):
        resp = resp.get("value")
    if not isinstance(resp, list):
        return []
    return [i for i in resp if isinstance(i, dict)]


def live_events_on_matter(client: Any, matter_id: str) -> list[dict[str, Any]]:
    """Every live event ``GET /events?MatterId=`` lists for the matter, paged in
    full. Raises when the calendar is too long to walk, because a partial walk
    cannot prove what is or is not on it."""
    rows: list[dict[str, Any]] = []
    for page in range(_MAX_PAGES):
        batch = _items(
            client.get(
                "/events",
                MatterId=matter_id,
                ExcludeDeletedEvents=True,
                Limit=_PAGE,
                Offset=page * _PAGE,
            )
        )
        rows.extend(batch)
        if len(batch) < _PAGE:
            return rows
    raise EventDeletionRefused(
        f"matter {matter_id} lists more than {_PAGE * _MAX_PAGES} live events, so its "
        "calendar could not be read in full. Nothing was deleted."
    )


def matter_number(client: Any, matter_id: str) -> str | None:
    """The matter's number as the vendor holds it, or None when it has none."""
    matter = client.get(f"/matters/{matter_id}")
    number = matter.get("number") if isinstance(matter, dict) else None
    return number.strip() if isinstance(number, str) and number.strip() else None


def _entry(row: dict[str, Any], matter_id: str, number: str | None) -> dict[str, Any]:
    subject = row.get("subject")
    start = row.get("startTime")
    return {
        "event_id": str(row.get("id")),
        "matter_id": matter_id,
        "matter_number": number,
        "subject": subject if isinstance(subject, str) else "",
        "start_time": start if isinstance(start, str) else "",
    }


def _is_normal(row: dict[str, Any]) -> bool:
    return row.get("type") in (None, "Normal")


# ---- prepare (READ) -------------------------------------------------------
def prepare(client: Any, matter_ids: Any, event_ids: Any = None) -> dict[str, Any]:
    """The manifest ``delete_events`` takes, read from the vendor. Deletes nothing.

    ``event_ids`` narrows the manifest to those events; without it every live
    Normal event on the matters is listed. Recurring events and requested ids
    that are not on the matters are reported under ``skipped``. Past the cap the
    manifest stops and ``remaining`` says how many more a later act must cover.
    """
    if not isinstance(matter_ids, list) or not matter_ids:
        raise EventDeletionRefused("matter_ids must be a non-empty list of matter ids; nothing was read.")
    if len(matter_ids) > MAX_MATTERS_PER_ACT:
        raise EventDeletionRefused(f"at most {MAX_MATTERS_PER_ACT} matters per act; nothing was read.")
    matters = list(dict.fromkeys(_require_matter_id(m) for m in matter_ids))
    wanted: set[str] | None = None
    if event_ids is not None:
        if not isinstance(event_ids, list) or not event_ids:
            raise EventDeletionRefused("event_ids, when given, must be a non-empty list; nothing was read.")
        wanted = {_require_id(e, "event_id") for e in event_ids}

    events: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    found: set[str] = set()
    for matter_id in matters:
        number = matter_number(client, matter_id)
        rows = sorted(
            live_events_on_matter(client, matter_id),
            key=lambda r: (str(r.get("startTime") or ""), str(r.get("id") or "")),
        )
        for row in rows:
            if not isinstance(row.get("id"), str):
                continue
            if wanted is not None and row["id"] not in wanted:
                continue
            found.add(row["id"])
            entry = _entry(row, matter_id, number)
            if not _is_normal(row):
                skipped.append({**entry, "reason": f"recurring ({row.get('type')}); never deleted here"})
                continue
            events.append(entry)
    for missing in sorted((wanted or set()) - found):
        skipped.append(
            {
                "event_id": missing,
                "reason": "not among the live events on these matters (another matter, unlinked, or already deleted)",
            }
        )
    remaining = max(0, len(events) - MAX_EVENTS_PER_ACT)
    return {
        "events": events[:MAX_EVENTS_PER_ACT],
        "remaining": remaining,
        "skipped": skipped,
        "next_step": (
            "Pass `events` to delete_events exactly as returned. Nothing has been deleted."
            + (
                f" {remaining} more events remain beyond the {MAX_EVENTS_PER_ACT}-event cap; "
                "prepare again after this act completes."
                if remaining
                else ""
            )
        ),
    }


# ---- delete (DESTRUCTIVE, confirmed) --------------------------------------
def _require_manifest(events: Any) -> list[dict[str, Any]]:
    if not isinstance(events, list) or not events:
        raise EventDeletionRefused(
            "events must be the non-empty list prepare_event_deletion returned; nothing was deleted."
        )
    if len(events) > MAX_EVENTS_PER_ACT:
        raise EventDeletionRefused(
            f"{len(events)} events is over the {MAX_EVENTS_PER_ACT}-event cap for one act; nothing was deleted."
        )
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in events:
        if not isinstance(raw, dict):
            raise EventDeletionRefused(
                "every entry must be an object from prepare_event_deletion; nothing was deleted."
            )
        entry = {
            "event_id": _require_id(raw.get("event_id"), "event_id"),
            "matter_id": _require_matter_id(raw.get("matter_id")),
            "matter_number": raw.get("matter_number") if isinstance(raw.get("matter_number"), str) else None,
            "subject": raw.get("subject") if isinstance(raw.get("subject"), str) else "",
            "start_time": raw.get("start_time") if isinstance(raw.get("start_time"), str) else "",
        }
        if entry["event_id"] in seen:
            raise EventDeletionRefused(f"event {entry['event_id']} is listed twice; nothing was deleted.")
        seen.add(entry["event_id"])
        out.append(entry)
    return out


def _mismatch(entry: dict[str, Any], row: dict[str, Any] | None, number: str | None) -> str | None:
    """Why this entry may not be deleted now, or None when it still matches."""
    if row is None:
        return "no longer among this matter's live events (moved, unlinked, or already deleted)"
    if not _is_normal(row):
        return f"recurring ({row.get('type')}); never deleted here"
    if (entry["matter_number"] or None) != number:
        return "the matter number shown does not match the matter"
    if entry["subject"] != (row.get("subject") or ""):
        return "its subject changed since it was proposed"
    if entry["start_time"] != (row.get("startTime") or ""):
        return "its date changed since it was proposed"
    return None


def _read_back(client: Any, ids: list[str], sleep: Any) -> dict[str, dict[str, Any]]:
    """Poll each deleted event by id until it reads back deleted or 404.
    Returns ``{event_id: readback}`` for the ones confirmed gone; the rest are
    still pending when the polls run out."""
    gone: dict[str, dict[str, Any]] = {}
    open_ids = list(ids)
    for attempt in range(_ATTEMPTS):
        if not open_ids:
            break
        if attempt:
            sleep(_BACKOFF[min(attempt - 1, len(_BACKOFF) - 1)])
        still: list[str] = []
        for event_id in open_ids:
            try:
                event = client.get(f"/events/{event_id}")
            except SmokeballApiError as exc:
                if exc.status == 404:
                    gone[event_id] = {"status": 404}
                    continue
                still.append(event_id)
                continue
            except Exception:  # noqa: BLE001 - an unreadable event is pending, never a failed delete
                still.append(event_id)
                continue
            if isinstance(event, dict) and event.get("isDeleted") is True:
                gone[event_id] = {"status": 200, "isDeleted": True}
            else:
                still.append(event_id)
        open_ids = still
    return gone


def delete_set(client: Any, events: Any, *, sleep: Any = time.sleep) -> dict[str, Any]:
    """Re-verify each entry against the live calendar, delete the ones that
    still match, and read every delete back. Never raises after the first
    DELETE: every outcome is reported per event."""
    manifest = _require_manifest(events)
    by_matter: dict[str, dict[str, dict[str, Any]]] = {}
    numbers: dict[str, str | None] = {}
    for matter_id in dict.fromkeys(e["matter_id"] for e in manifest):
        numbers[matter_id] = matter_number(client, matter_id)
        by_matter[matter_id] = {
            str(r.get("id")): r for r in live_events_on_matter(client, matter_id) if isinstance(r.get("id"), str)
        }

    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    sent: list[dict[str, Any]] = []
    for entry in manifest:
        row = by_matter[entry["matter_id"]].get(entry["event_id"])
        reason = _mismatch(entry, row, numbers[entry["matter_id"]])
        if reason is not None:
            skipped.append({**entry, "reason": reason})
            continue
        try:
            client.request("DELETE", f"/events/{entry['event_id']}")
        except SmokeballApiError as exc:
            if exc.status == 404:
                skipped.append({**entry, "reason": "already gone when the delete was sent"})
            else:
                failed.append({**entry, "reason": f"the vendor refused the delete (HTTP {exc.status})"})
            continue
        sent.append(entry)

    gone = _read_back(client, [e["event_id"] for e in sent], sleep)
    deleted = [{**e, "readback": gone[e["event_id"]]} for e in sent if e["event_id"] in gone]
    pending = [e for e in sent if e["event_id"] not in gone]
    return {
        "deleted": deleted,
        "pending": pending,
        "skipped": skipped,
        "failed": failed,
        "counts": {
            "proposed": len(manifest),
            "deleted": len(deleted),
            "pending": len(pending),
            "skipped": len(skipped),
            "failed": len(failed),
        },
        # The ledger's reference for this act (the trust plugin records ``ref``).
        "ref": (f"deleted={len(deleted)} pending={len(pending)} skipped={len(skipped)} failed={len(failed)}"),
        "note": (
            "pending means the vendor accepted the delete and had not applied it when last read; "
            "it is never a reason to delete again."
        ),
    }


# ---- MCP tools ------------------------------------------------------------
def _client() -> Any:
    from . import server

    return server._get_client()


def prepare_event_deletion(matter_ids: list[str], event_ids: list[str] | None = None) -> Any:
    """Read the calendar events a deletion would remove. Deletes nothing.

    Returns ``events``: one entry per live, non-recurring event on the given
    matters (``event_id``, ``matter_id``, ``matter_number``, ``subject``,
    ``start_time``, as Smokeball holds them), at most 50; ``remaining`` when
    the calendar holds more; and ``skipped`` with a reason for anything left out
    (recurring events, requested ids not on these matters). Pass ``event_ids``
    to narrow it to particular events.

    To delete them, call ``delete_events`` with ``events`` exactly as returned.
    """
    return prepare(_client(), matter_ids, event_ids)


def delete_events(events: list[dict[str, Any]]) -> Any:
    """Delete calendar events, using the ``events`` list prepare_event_deletion
    returned, unchanged. At most 50 per call.

    An administrator confirms this first: the seat withholds the call and hands
    back an ``[act ...]`` line listing every event; only the administrator's
    yes to that line performs it. At that point each event is re-read and
    deleted only if it is still on its matter with the same subject and date;
    anything that changed is skipped with its reason. The result lists what was
    deleted, what is pending (accepted, not yet applied; never delete again),
    what was skipped and what failed."""
    return delete_set(_client(), events)


def register(server: Any) -> None:
    """Register both tools. Called once, from ``attachment_tools.register``."""
    server.tool()(prepare_event_deletion)
    server.tool()(delete_events)


__all__ = [
    "MAX_EVENTS_PER_ACT",
    "MAX_MATTERS_PER_ACT",
    "EventDeletionRefused",
    "delete_events",
    "delete_set",
    "live_events_on_matter",
    "prepare",
    "prepare_event_deletion",
    "register",
]
