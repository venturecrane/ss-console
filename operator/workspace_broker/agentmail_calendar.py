"""One calendar invitation on an AgentMail send, validated and built here.

WHY THIS EXISTS (ss-console#3264, ADR 0092 pending). A hosted agent keeps a
calendar for the people it serves by SENDING them invitations: an iMIP
message (RFC 6047) whose ``text/calendar`` part carries a ``METHOD:REQUEST``
to create or move an event and ``METHOD:CANCEL`` to remove it. Proven live in
Google and Apple calendars on 2026-10-16 (vfy_01M55T51PV1YB4D56PW7KK8WK0,
vfy_01M55THDN2KSHQ8YKAN0QQKPTE, vfy_01M55TK7D5M02RNYGZ85WZAQR5), with a test
script that called AgentMail with a send-capable key directly. No seat may do
that (ss#2258): every transmit goes through this broker and leaves a row.

AgentMail carries no general attachments here (``agentmail_ops``
``_refuse_attachments``), and that stays true. This module is not an
attachment lane. It is one field, ``calendar: {method, ics}``, and the broker
decides everything about the part it becomes:

* the filename (``invite.ics``), the content type
  (``text/calendar; method=<METHOD>; charset=UTF-8``) and the disposition
  are fixed here, never read off the wire;
* the ``.ics`` is parsed and must be ONE event a calendar client will treat
  as an invitation from this seat: its ``METHOD`` matches the declared method,
  its ``ORGANIZER`` is this seat's own inbox, and every ``ATTENDEE`` is a
  ``mailto:`` address the message itself is addressed to (``to`` or ``cc``),
  so the recipient fence and the participant fence that already govern the
  message govern every person the invitation names;
* nothing in it can reach a person the message does not: an ``EMAIL`` alarm
  (which a client may mail to its own attendee list) and an ``ATTACH`` (a URL
  or an inline blob) are refused, as is any component other than an event,
  its time zones and its alarms;
* the sha256 of the exact bytes sent is computed here and stamped on the
  CONFIRM_SEND row as ``attachment_sha256`` (``transmit_verbs``), so the
  ledger proves which invitation went out without holding it.

Every refusal reaches the agent as ``AgentMailRefused``, with a reason written here.
"""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .recipient_policy import normalize_address

#: The payload key. Its value is ``{"method": ..., "ics": ...}`` and nothing else.
CALENDAR_FIELD = "calendar"
METHODS = ("REQUEST", "CANCEL")
#: An invitation is a few hundred bytes; a recurring event with a time zone a
#: few kilobytes. Anything near this is not an invitation.
MAX_ICS_BYTES = 64 * 1024
FILENAME = "invite.ics"

#: Components an invitation may carry. VTIMEZONE's two sub-components are the
#: rule blocks a time zone is written in.
_ALLOWED_COMPONENTS = frozenset({"VCALENDAR", "VEVENT", "VTIMEZONE", "STANDARD", "DAYLIGHT", "VALARM"})
_ALARM_ACTIONS = frozenset({"DISPLAY", "AUDIO"})
_NAME_RE = re.compile(r"^[A-Za-z0-9-]+$")


class CalendarRefused(Exception):
    """The invitation is refused. ``agentmail_ops`` re-raises it as
    ``AgentMailRefused`` (audited as ``refused``); this module does not import
    the ops module that imports it. Every message is a sentence written here."""


@dataclass(frozen=True)
class Invitation:
    """The part to send, and the facts the audit row and the fence need."""

    method: str
    attachment: dict[str, str]
    sha256: str
    uid: str
    sequence: int
    attendees: tuple[str, ...]


@dataclass(frozen=True)
class _Line:
    name: str
    params: dict[str, str]
    value: str


def _split(line: str) -> _Line:
    """``NAME;P=V;P="a:b":value`` -> its parts. Quoted parameter values may hold
    ``:`` and ``;``; the first unquoted ``:`` ends the parameters."""
    quoted = False
    for index, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        elif char == ":" and not quoted:
            head, value = line[:index], line[index + 1 :]
            break
    else:
        raise CalendarRefused(f"calendar refused: a content line has no value: {line[:60]!r}")
    parts: list[str] = []
    current, quoted = "", False
    for char in head:
        if char == '"':
            quoted = not quoted
        if char == ";" and not quoted:
            parts.append(current)
            current = ""
        else:
            current += char
    parts.append(current)
    name = parts[0].upper()
    if not _NAME_RE.match(name):
        raise CalendarRefused(f"calendar refused: {parts[0][:40]!r} is not a property name")
    params: dict[str, str] = {}
    for part in parts[1:]:
        key, sep, val = part.partition("=")
        if not sep or not _NAME_RE.match(key):
            raise CalendarRefused(f"calendar refused: malformed parameter on {name}")
        params[key.upper()] = val.strip('"')
    return _Line(name, params, value)


def _lines(ics: str) -> list[_Line]:
    """Unfold (RFC 5545 3.1) and split. CRLF only: what is validated is what is
    sent, so a bare LF is refused rather than rewritten."""
    if "\r\n" not in ics or re.search(r"(?<!\r)\n|\r(?!\n)", ics):
        raise CalendarRefused("calendar refused: lines must end in CRLF (RFC 5545 3.1)")
    if not ics.endswith("\r\n"):
        raise CalendarRefused("calendar refused: the calendar must end with a line break")
    unfolded = re.sub(r"\r\n[ \t]", "", ics)
    raw = unfolded.split("\r\n")[:-1]
    if any(not line for line in raw):
        raise CalendarRefused("calendar refused: empty content line")
    return [_split(line) for line in raw]


def _mailto(value: str) -> str:
    text = value.strip()
    if not text.lower().startswith("mailto:"):
        raise CalendarRefused("calendar refused: ORGANIZER and ATTENDEE must be mailto: addresses")
    address = normalize_address(text[len("mailto:") :])
    if "@" not in address:
        raise CalendarRefused("calendar refused: an ORGANIZER or ATTENDEE is not an address")
    return address


def _addressed(payload: dict[str, Any]) -> frozenset[str]:
    found: set[str] = set()
    for key in ("to", "cc"):
        value = payload.get(key)
        items = [value] if isinstance(value, str) else value if isinstance(value, list) else []
        found.update(normalize_address(v) for v in items if isinstance(v, str))
    return frozenset(a for a in found if a)


def _check_begin(component: str, stack: list[str], opened: bool) -> None:
    """Refuse a BEGIN that would put a component where an invitation has none."""
    if component not in _ALLOWED_COMPONENTS:
        raise CalendarRefused(f"calendar refused: an invitation carries no {component} component")
    if (not stack) != (component == "VCALENDAR") or (component == "VCALENDAR" and opened):
        raise CalendarRefused("calendar refused: exactly one VCALENDAR must hold everything")
    if component == "VEVENT" and stack != ["VCALENDAR"]:
        raise CalendarRefused("calendar refused: a VEVENT must sit directly in the VCALENDAR")
    if component == "VALARM" and stack[-1:] != ["VEVENT"]:
        raise CalendarRefused("calendar refused: a VALARM must sit in the VEVENT")


@dataclass
class _Tree:
    """The calendar's properties by where they sit."""

    top: list[_Line]
    events: list[list[_Line]]
    alarms: list[_Line]


def _place(tree: _Tree, stack: list[str], line: _Line) -> None:
    if not stack:
        raise CalendarRefused("calendar refused: content outside the VCALENDAR")
    if stack == ["VCALENDAR"]:
        tree.top.append(line)
    elif stack[-1] == "VEVENT":
        tree.events[-1].append(line)
    elif stack[-1] == "VALARM":
        tree.alarms.append(line)


def _structure(lines: Iterable[_Line]) -> _Tree:
    """The calendar's properties by component. Refuses anything but a well-nested
    tree of the allowed components."""
    stack: list[str] = []
    tree = _Tree([], [], [])
    opened = closed = False
    for line in lines:
        if closed:
            raise CalendarRefused("calendar refused: content after the VCALENDAR")
        component = line.value.strip().upper()
        if line.name == "BEGIN":
            _check_begin(component, stack, opened)
            if component == "VEVENT":
                tree.events.append([])
            opened = True
            stack.append(component)
        elif line.name == "END":
            if not stack or stack[-1] != component:
                raise CalendarRefused("calendar refused: components are not properly nested")
            stack.pop()
            closed = not stack
        else:
            _place(tree, stack, line)
    if not closed:
        raise CalendarRefused("calendar refused: the VCALENDAR is not closed")
    return tree


def _one(props: list[_Line], name: str, where: str) -> _Line:
    found = [p for p in props if p.name == name]
    if len(found) != 1:
        raise CalendarRefused(f"calendar refused: the {where} must carry exactly one {name}")
    return found[0]


def _declared(payload: dict[str, Any]) -> tuple[str, bytes] | None:
    """``(method, ics bytes)`` the payload declares, or ``None`` for no field."""
    raw = payload.get(CALENDAR_FIELD)
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != {"method", "ics"}:
        raise CalendarRefused("calendar refused: calendar carries exactly method and ics")
    method, ics = raw.get("method"), raw.get("ics")
    if method not in METHODS:
        raise CalendarRefused(f"calendar refused: method must be one of {list(METHODS)}")
    if not isinstance(ics, str) or not ics:
        raise CalendarRefused("calendar refused: ics must be the calendar text")
    data = ics.encode("utf-8")
    if len(data) > MAX_ICS_BYTES:
        raise CalendarRefused(f"calendar refused: over {MAX_ICS_BYTES} bytes is not an invitation")
    return str(method), data


def _identity(event: list[_Line]) -> tuple[str, int]:
    """The event's UID and SEQUENCE, with its other required properties checked."""
    uid = _one(event, "UID", "event").value.strip()
    if not uid:
        raise CalendarRefused("calendar refused: the event's UID is empty")
    _one(event, "DTSTAMP", "event")
    _one(event, "DTSTART", "event")
    try:
        sequence = int(_one(event, "SEQUENCE", "event").value.strip())
    except ValueError as exc:
        raise CalendarRefused("calendar refused: SEQUENCE must be a whole number") from exc
    if sequence < 0:
        raise CalendarRefused("calendar refused: SEQUENCE must not be negative")
    return uid, sequence


def _people(event: list[_Line], payload: dict[str, Any], organizer: str) -> tuple[str, ...]:
    """The attendees, once the ORGANIZER is this inbox and every attendee is a
    to/cc recipient of the message."""
    if _mailto(_one(event, "ORGANIZER", "event").value) != normalize_address(organizer):
        raise CalendarRefused("calendar refused: the ORGANIZER must be this seat's own inbox")
    attendees = tuple(_mailto(p.value) for p in event if p.name == "ATTENDEE")
    if not attendees:
        raise CalendarRefused("calendar refused: the event names no ATTENDEE")
    addressed = _addressed(payload)
    missing = sorted({a for a in attendees if a not in addressed})
    if missing:
        raise CalendarRefused(
            "calendar refused: every ATTENDEE must be a to or cc recipient of this message, "
            "so the fences that govern the message govern the invitation: " + ", ".join(missing)
        )
    return attendees


def _nothing_reaches_past(event: list[_Line], alarms: list[_Line]) -> None:
    """No ATTACH, and no alarm that could mail anyone or name anyone."""
    if any(p.name == "ATTACH" for p in event):
        raise CalendarRefused("calendar refused: an invitation carries no ATTACH")
    for prop in alarms:
        if prop.name in ("ATTENDEE", "ATTACH"):
            raise CalendarRefused(f"calendar refused: an alarm carries no {prop.name}")
        if prop.name == "ACTION" and prop.value.strip().upper() not in _ALARM_ACTIONS:
            raise CalendarRefused("calendar refused: an alarm's ACTION must be DISPLAY or AUDIO")


def build(payload: dict[str, Any], *, organizer: str) -> Invitation | None:
    """The invitation ``payload`` asks to carry, or ``None`` when it asks none.

    ``organizer`` is this seat's own inbox address, which the event's ORGANIZER
    must name: a calendar client shows the organizer as who invited you, and a
    message from this inbox claiming another organizer is a forgery."""
    declared = _declared(payload)
    if declared is None:
        return None
    method, data = declared
    tree = _structure(_lines(data.decode("utf-8")))
    if _one(tree.top, "METHOD", "calendar").value.strip().upper() != method:
        raise CalendarRefused("calendar refused: the calendar's METHOD is not the method declared")
    _one(tree.top, "VERSION", "calendar")
    if len(tree.events) != 1:
        raise CalendarRefused("calendar refused: an invitation carries exactly one VEVENT")
    event = tree.events[0]
    uid, sequence = _identity(event)
    attendees = _people(event, payload, organizer)
    _nothing_reaches_past(event, tree.alarms)
    return Invitation(
        method=method,
        attachment={
            "filename": FILENAME,
            "content_type": f"text/calendar; method={method}; charset=UTF-8",
            "content_disposition": "attachment",
            "content": base64.b64encode(data).decode("ascii"),
        },
        sha256=hashlib.sha256(data).hexdigest(),
        uid=uid,
        sequence=sequence,
        attendees=attendees,
    )


__all__ = ["CALENDAR_FIELD", "FILENAME", "MAX_ICS_BYTES", "METHODS", "CalendarRefused", "Invitation", "build"]
