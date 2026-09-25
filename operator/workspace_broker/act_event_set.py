"""The calendar-event deletion act: a payload the CALL carries, not the config.

WHY THIS ACT IS SHAPED DIFFERENTLY FROM ``create_matter``. The matter act's
payload is authored in the seat's customer.yaml and the model can only ask for
it. A deletion cannot work that way: which events to remove is the request
itself ("clear the calendar on closed file X"), and no config could author it.
So the payload is the list the Smokeball connector's ``prepare_event_deletion``
read from the vendor, carried on the ``delete_events`` call the trust gate
withheld. What keeps "yes" meaning what the administrator read:

* this module renders the ``[act ...]`` line from the stored list, never from
  caller prose, and the row stores that exact list;
* on the confirming turn the overlay replays the STORED list over whatever the
  model re-sends, so nothing the model composes after the yes reaches the tool;
* the connector re-reads every event against the live calendar and deletes it
  only if its matter, matter number, subject and date still equal the entry the
  administrator read. An entry the model composed by hand never matches, so it
  can be SHOWN but never DELETED.

The seat must author ``destructive: confirm`` on some persona for any of this
to be proposed (the weaker of two checks, as for the matter act; the overlay's
gate clamps against the running persona).

Kept out of ``establishment_constants`` / ``establishment_validation`` on
purpose: those two are re-exported through ``establishment`` and pinned name by
name and value by value in ``tests/fixtures/establishment_surface.json``. This
is a new act, not a change to the existing surface.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .establishment_validation import EstablishmentValidationError, _hash_text

#: The deletion act's tool, as the overlay names it at runtime.
DELETE_EVENTS_TOOL = "mcp_smokeball_delete_events"

#: Acts whose payload rides on the call, with the exposure class a seat must
#: author at ``confirm`` before one can be proposed.
CALL_PAYLOAD_ACTS: dict[str, str] = {DELETE_EVENTS_TOOL: "destructive"}

#: Mirrors the connector's ``MAX_EVENTS_PER_ACT``. Fifty lines is what an
#: administrator can check one by one in one email; a larger calendar is
#: cleared in several acts, each confirmed on its own.
MAX_EVENTS_PER_ACT = 50

#: The act line for fifty events runs to a few kilobytes; the rule ceiling
#: (2000 bytes, a sentence) is the wrong bound for it. Still bounded.
MAX_EVENT_ACT_TEXT_BYTES = 9000

_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_ENTRY_KEYS = ("event_id", "matter_id", "matter_number", "subject", "start_time")
_MAX_SUBJECT = 200
_MAX_NUMBER = 64
_SHOWN_SUBJECT = 80


def _str(value: Any, field: str, limit: int, *, required: bool) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise EstablishmentValidationError(f"{field} must be a string")
    if len(value) > limit:
        raise EstablishmentValidationError(f"{field} is longer than {limit} characters")
    return value


def require_event_set(value: Any) -> dict[str, Any]:
    """The ``{"events": [...]}`` payload, shape-checked and normalized.

    Exactly the five keys per entry, ids in the vendor's alphabet, a date on
    every entry, no duplicate event, one to fifty entries. Refused by name, never
    repaired: a repaired entry is one the administrator was not shown."""
    if not isinstance(value, dict) or set(value) != {"events"}:
        raise EstablishmentValidationError(
            f"{DELETE_EVENTS_TOOL} takes exactly one field, 'events', the list prepare_event_deletion returned"
        )
    events = value["events"]
    if not isinstance(events, list) or not events:
        raise EstablishmentValidationError("events must be a non-empty list")
    if len(events) > MAX_EVENTS_PER_ACT:
        raise EstablishmentValidationError(
            f"{len(events)} events is over the {MAX_EVENTS_PER_ACT}-event cap for one act; propose the rest separately"
        )
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, raw in enumerate(events):
        where = f"events[{i}]"
        if not isinstance(raw, dict):
            raise EstablishmentValidationError(f"{where} must be an object")
        unknown = sorted(set(raw) - set(_ENTRY_KEYS))
        if unknown:
            raise EstablishmentValidationError(
                f"{where} carries fields {unknown}; an entry is exactly {list(_ENTRY_KEYS)}"
            )
        event_id = _str(raw.get("event_id"), f"{where}.event_id", 64, required=True) or ""
        matter_id = _str(raw.get("matter_id"), f"{where}.matter_id", 64, required=True) or ""
        if not _ID.match(event_id) or not _ID.match(matter_id):
            raise EstablishmentValidationError(f"{where} carries an id that is not a Smokeball id")
        start = _str(raw.get("start_time"), f"{where}.start_time", 40, required=True) or ""
        if not _DATE.match(start):
            raise EstablishmentValidationError(f"{where}.start_time must begin with a YYYY-MM-DD date")
        if event_id in seen:
            raise EstablishmentValidationError(f"event {event_id} is listed twice")
        seen.add(event_id)
        out.append(
            {
                "event_id": event_id,
                "matter_id": matter_id,
                "matter_number": _str(raw.get("matter_number"), f"{where}.matter_number", _MAX_NUMBER, required=False),
                "subject": _str(raw.get("subject"), f"{where}.subject", _MAX_SUBJECT, required=True),
                "start_time": start,
            }
        )
    return {"events": out}


#: The opening of anything the seat's confirmation matcher reads as a tag
#: (hermes-smd-overlay shared/rule_confirm.py: ``[rule|act|ops XXXXXXXX]`` and
#: ``[draft XXXXXXXX]``), case-insensitive, whitespace-tolerant.
_TAG_OPEN = re.compile(r"\[(?=\s*(?:rule|act|ops|draft)\b)", re.IGNORECASE)


def _shown(text: str | None, limit: int) -> str:
    """A vendor string as the act line shows it: one line, bounded, with any
    bracket that OPENS a tag-shaped word turned into a parenthesis (it could
    otherwise render a second ``[act ...]`` tag and bind a yes to the wrong
    row) and double quotes turned into single ones.

    Only tag-shaped brackets. Read live on pilot-smokeball 2026-09-25: the
    first version turned EVERY bracket into a parenthesis, so a subject
    ``[SMD-PROBE] ...`` read ``(SMD-PROBE) ...`` in the act line, and the model
    "corrected" it back before replying, which is exactly the edit the seat's
    readback gate refuses. An ordinary bracket is left as the firm wrote it.
    The stored payload keeps the raw value, because the connector compares that
    against the vendor."""
    text = re.sub(r"\s+", " ", text or "").strip()
    text = _TAG_OPEN.sub("(", text).replace('"', "'")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def event_set_readback(payload: dict[str, Any]) -> str:
    """The act as one line an administrator answers, grouped by matter, every
    event by date and subject, in the order the payload holds them."""
    events = payload["events"]
    groups: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        groups.setdefault(e["matter_id"], []).append(e)
    parts: list[str] = []
    for rows in groups.values():
        number = rows[0].get("matter_number")
        label = f"matter {_shown(number, _MAX_NUMBER)}" if number else f"the matter with id {rows[0]['matter_id']}"
        listed = "; ".join(
            f'{e["start_time"][:10]} "{_shown(e["subject"], _SHOWN_SUBJECT) or "(no subject)"}"' for e in rows
        )
        parts.append(f"{len(rows)} on {label} ({listed})")
    noun = "event" if len(events) == 1 else "events"
    text = (
        f"Delete {len(events)} Smokeball calendar {noun}: "
        + "; and ".join(parts)
        + '. Reply "yes, delete them" to proceed.'
    )
    if len(text.encode("utf-8")) > MAX_EVENT_ACT_TEXT_BYTES:
        raise EstablishmentValidationError(
            f"the act line would be over {MAX_EVENT_ACT_TEXT_BYTES} bytes; propose fewer events per act"
        )
    return text


def require_exposure(data: dict[str, Any], tool: str) -> None:
    """Refuse unless SOME persona authors ``<class>: confirm`` for this act."""
    klass = CALL_PAYLOAD_ACTS[tool]
    for persona in data.get("personas") or []:
        exposure = ((persona or {}).get("entitlements") or {}).get("exposure") if isinstance(persona, dict) else None
        if isinstance(exposure, dict) and exposure.get(klass) == "confirm":
            return
    raise EstablishmentValidationError(
        f"this seat authors no persona with exposure.{klass} set to 'confirm', so {tool} "
        "cannot be proposed to anybody. Authoring it is a config change the firm agrees to, "
        "not something this turn can do"
    )


def payload_digest(payload: dict[str, Any]) -> str:
    return _hash_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def outcome_counts(outcome: dict[str, Any]) -> dict[str, Any]:
    """The bounded ledger summary of what the connector reported, parsed from
    the ``ref`` the seat's post-call hook forwards (``deleted=N pending=N
    skipped=N failed=N``). Numbers only; nothing the vendor wrote is kept."""
    ref = outcome.get("ref") if isinstance(outcome, dict) else None
    counts: dict[str, Any] = {}
    if isinstance(ref, str):
        for key, num in re.findall(r"\b(deleted|pending|skipped|failed)=(\d{1,4})\b", ref):
            counts[key] = int(num)
    return counts


__all__ = [
    "CALL_PAYLOAD_ACTS",
    "DELETE_EVENTS_TOOL",
    "MAX_EVENTS_PER_ACT",
    "MAX_EVENT_ACT_TEXT_BYTES",
    "event_set_readback",
    "outcome_counts",
    "payload_digest",
    "require_event_set",
    "require_exposure",
]
