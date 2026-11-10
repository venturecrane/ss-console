"""A person's own reminders, kept by code (ss-console #3946, the rep desk).

WHAT THIS IS. On a guide seat whose ``rep-desk`` skill authors
``settings.reminder_store``, an enrolled sales representative emails "remind me
Thursday at 2pm to call the Acme CFO". The model calls ``reminder_set`` with two
values copied from the email, ``when`` (the person's words for the date and
time) and ``what`` (the person's words for the reminder). Everything after that
is code, in this module:

* the date and time are RESOLVED from ``when`` against the seat's own clock and
  time zone (:func:`resolve_when`), never computed by the model;
* the result is VALIDATED: a real calendar date, not in the past, not more than
  two years out (:class:`WhenError` says what is wrong in one sentence);
* ``what`` is made sendable (:func:`plain_what`): a dash becomes a comma, an
  exclamation mark a full stop, a dollar figure words, and a bare clock time is
  refused with "put the time in when";
* the reminder is written into the PERSON'S OWN partition of the authored store
  (``record_store.code_write``; the store is ``partition: sender`` and
  ``managed: true``, so the model cannot write it and nobody else can read it);
* the receipt line the reply must use is returned (:func:`receipt_line`).

A MEETING IS A DATED REMINDER (``kind: meeting``). The seat sends no calendar
invitation (plan, "Not built").

WHO THE PERSON IS. The strict per-turn verified sender (the email this turn's
own trusted prompt prefix carried, ``bound_this_turn``), keyed by the session
id Hermes hands the handler, and on the seat's roster at that moment. A
scheduled turn, a chat turn, a wake or an unlisted sender sets nothing. This is
the same rule the partitioned store's own handlers apply
(``plugins/hermes-smd-record-store``, ``_partition_person``).

THE RECORD (``reminder-<yyyy-mm-dd>-<6 hex>.json``): ``due`` (ISO day, for
code), ``at`` (clock time with AM or PM, or null), ``when`` (the human date
line), ``what``, ``kind`` and ``set_on``. Nothing in it names whose it is: the
directory is the owner. The digest (``shared/person_digest.py``) reads it.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from shared import record_store

#: The rep-desk skill and the setting that names its reminder store.
SKILL = "rep-desk"
STORE_SETTING = "reminder_store"
#: Optional settings on the same skill.
TIMEZONE_SETTING = "timezone"

KIND_REMINDER = "reminder"
KIND_MEETING = "meeting"
KINDS = (KIND_REMINDER, KIND_MEETING)

MAX_WHAT_CHARS = 300
MAX_WHEN_CHARS = 120
#: How far ahead a reminder may be set.
MAX_AHEAD = timedelta(days=731)
#: A month and day with no year that passed this long ago or less is a mistake
#: ("November 3" said on November 10), refused; longer ago means next year.
RECENT_PAST = timedelta(days=60)

RECORD_PREFIX = "reminder-"
_RECORD_RE = re.compile(r"^reminder-(\d{4}-\d{2}-\d{2})-[0-9a-f]{6}\.json$")


class WhenError(ValueError):
    """The date or time could not be resolved or is not valid. One sentence."""


class ReminderError(RuntimeError):
    """The reminder could not be kept. One sentence for the model."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def desk_settings(cfg: Any) -> dict[str, Any] | None:
    """The ``settings`` of the enabled ``rep-desk`` skill on any persona, or
    None when no persona enables it. ``cfg`` is a ``CustomerConfig`` or the
    parsed document."""
    personas = cfg.personas if hasattr(cfg, "personas") else (cfg or {}).get("personas")
    for persona in personas if isinstance(personas, list) else []:
        for skill in (persona.get("skills") or []) if isinstance(persona, dict) else []:
            if (
                isinstance(skill, dict)
                and skill.get("name") == SKILL
                and skill.get("enabled") is True
            ):
                settings = skill.get("settings")
                return dict(settings) if isinstance(settings, dict) else {}
    return None


def reminder_store(cfg: Any) -> str | None:
    """The authored reminder store's name when it is usable: named by
    ``settings.reminder_store`` and authored ``partition: sender`` and
    ``managed: true``. None otherwise (no tool, no digest)."""
    settings = desk_settings(cfg)
    name = (settings or {}).get(STORE_SETTING)
    if not isinstance(name, str) or not name.strip():
        return None
    try:
        policy = record_store.authored_policies(cfg if hasattr(cfg, "raw") else None).get(name)
    except record_store.RecordStoreError:
        return None
    if policy is None or not policy.partitioned or not policy.managed:
        return None
    return name


def seat_zone(cfg: Any) -> str:
    """The seat's zone: ``rep-desk`` ``settings.timezone``, else
    ``HERMES_TIMEZONE`` (what the scheduler runs in), else
    ``business_hours.timezone``, else UTC."""
    settings = desk_settings(cfg) or {}
    zone = settings.get(TIMEZONE_SETTING)
    if isinstance(zone, str) and zone.strip():
        return zone.strip()
    env = os.environ.get("HERMES_TIMEZONE", "").strip()
    if env:
        return env
    raw = cfg.raw if hasattr(cfg, "raw") else cfg
    hours = raw.get("business_hours") if isinstance(raw, dict) else None
    zone = hours.get("timezone") if isinstance(hours, dict) else None
    return zone.strip() if isinstance(zone, str) and zone.strip() else "UTC"


def local_now(zone: str, now: datetime | None = None) -> datetime:
    """``now`` (default: the clock) in ``zone``; an unknown zone reads as UTC."""
    moment = now if now is not None else datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo

        return moment.astimezone(ZoneInfo(zone))
    except Exception:  # noqa: BLE001 - an unknown zone reads as UTC, never a crash
        return moment.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Resolving the person's words to a date and time
# ---------------------------------------------------------------------------

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_WEEKDAYS = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "weds": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}
_NUMBER_WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}

_MONTH_NAMES = "|".join(sorted(_MONTHS, key=len, reverse=True))
_WEEKDAY_NAMES = "|".join(sorted(_WEEKDAYS, key=len, reverse=True))
_TIME_MERIDIEM = re.compile(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?m\.?|p\.?m\.?)(?![a-z])")
_TIME_24 = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_NOON = re.compile(r"\b(noon|midday)\b")
_MIDNIGHT = re.compile(r"\bmidnight\b")
_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_NUMERIC = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?\b")
_MONTH_DAY = re.compile(
    rf"\b({_MONTH_NAMES})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b"
)
_DAY_MONTH = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_NAMES})\.?(?:,?\s+(\d{{4}}))?\b"
)
_WEEKDAY = re.compile(rf"\b(?:(next|this|on)\s+)?({_WEEKDAY_NAMES})\b")
_IN_N = re.compile(r"\bin\s+(\d{1,3}|[a-z]+)\s+(day|days|week|weeks)\b")


@dataclass(frozen=True)
class When:
    """A resolved, validated moment: the local day and, when given, the time."""

    day: date
    at: time | None

    def iso_day(self) -> str:
        return self.day.isoformat()

    def clock(self) -> str | None:
        """``2:30 PM``: always with AM or PM, so no reply check reads it as a
        bare clock time."""
        return clock_words(self.at) if self.at is not None else None

    def words(self) -> str:
        """``Thursday, November 12, 2026`` or ``... at 2:30 PM``."""
        text = day_words(self.day)
        return f"{text} at {self.clock()}" if self.at is not None else text


def day_words(day: date) -> str:
    return f"{day.strftime('%A')}, {day.strftime('%B')} {day.day}, {day.year}"


def clock_words(at: time) -> str:
    hour = at.hour % 12 or 12
    return f"{hour}:{at.minute:02d} {'AM' if at.hour < 12 else 'PM'}"


def _take_time(text: str) -> tuple[time | None, str]:
    """The clock time in ``text`` and ``text`` without it."""
    m = _TIME_MERIDIEM.search(text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if not 1 <= hour <= 12 or minute > 59:
            raise WhenError(f"{m.group(0).strip()!r} is not a clock time")
        pm = m.group(3).startswith("p")
        hour = (hour % 12) + (12 if pm else 0)
        return time(hour, minute), text[: m.start()] + " " + text[m.end() :]
    m = _TIME_24.search(text)
    if m:
        return time(int(m.group(1)), int(m.group(2))), text[: m.start()] + " " + text[m.end() :]
    m = _NOON.search(text)
    if m:
        return time(12, 0), text[: m.start()] + " " + text[m.end() :]
    m = _MIDNIGHT.search(text)
    if m:
        raise WhenError("midnight is ambiguous; ask which day and give a time such as 11:59 PM")
    return None, text


def _number(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    return _NUMBER_WORDS.get(token)


def _real_date(year: int, month: int, day: int) -> date:
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise WhenError(f"there is no such date as {month}/{day}/{year}") from exc


def _no_year(month: int, day: int, today: date) -> date:
    """A month and day with no year: this year, or next year when it has
    passed by more than :data:`RECENT_PAST` (a recent past date is a mistake)."""
    this_year = _real_date(today.year, month, day) if not (month == 2 and day == 29) else None
    if this_year is None:  # February 29 with no year: the next leap year
        year = today.year
        while True:
            try:
                candidate = date(year, 2, 29)
            except ValueError:
                year += 1
                continue
            if candidate >= today:
                return candidate
            year += 1
    if this_year >= today or today - this_year <= RECENT_PAST:
        return this_year
    return _real_date(today.year + 1, month, day)


def _take_day(text: str, today: date) -> date | None:
    if re.search(r"\bday after tomorrow\b", text):
        return today + timedelta(days=2)
    if re.search(r"\b(today|tonight|this (morning|afternoon|evening))\b", text):
        return today
    if re.search(r"\btomorrow\b", text):
        return today + timedelta(days=1)
    m = _IN_N.search(text)
    if m:
        n = _number(m.group(1))
        if n is None:
            raise WhenError(f"could not read the number in {m.group(0)!r}")
        unit = 7 if m.group(2).startswith("week") else 1
        return today + timedelta(days=n * unit)
    m = _ISO.search(text)
    if m:
        return _real_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _MONTH_DAY.search(text)
    if m:
        month, day = _MONTHS[m.group(1)], int(m.group(2))
        if m.group(3):
            return _real_date(int(m.group(3)), month, day)
        return _no_year(month, day, today)
    m = _DAY_MONTH.search(text)
    if m:
        day, month = int(m.group(1)), _MONTHS[m.group(2)]
        if m.group(3):
            return _real_date(int(m.group(3)), month, day)
        return _no_year(month, day, today)
    m = _NUMERIC.search(text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        if m.group(3):
            year = int(m.group(3))
            return _real_date(year + 2000 if year < 100 else year, month, day)
        return _no_year(_check_month(month), day, today)
    m = _WEEKDAY.search(text)
    if m:
        wanted = _WEEKDAYS[m.group(2)]
        ahead = (wanted - today.weekday()) % 7 or 7
        return today + timedelta(days=ahead)
    return None


def _check_month(month: int) -> int:
    if not 1 <= month <= 12:
        raise WhenError(f"{month} is not a month")
    return month


def resolve_when(words: str, now_local: datetime) -> When:
    """The date and time ``words`` names, relative to ``now_local`` (an aware
    datetime in the seat's zone), validated. Raises :class:`WhenError`."""
    if not isinstance(words, str) or not words.strip():
        raise WhenError("when is empty; ask the person for a date")
    if len(words) > MAX_WHEN_CHARS:
        raise WhenError(
            "when is longer than a date and time; pass only the person's words for when"
        )
    text = " " + re.sub(r"\s+", " ", words.strip().lower().replace(",", " ")) + " "
    at, rest = _take_time(text)
    today = now_local.date()
    day = _take_day(rest, today)
    if day is None:
        if at is None:
            raise WhenError(
                f"could not tell a date from {words.strip()!r}; ask the person for a date, "
                "such as Thursday or November 12"
            )
        day = today
    resolved = When(day=day, at=at)
    _validate(resolved, now_local)
    return resolved


def _validate(when: When, now_local: datetime) -> None:
    today = now_local.date()
    if when.day < today:
        raise WhenError(
            f"{day_words(when.day)} has already passed; ask the person for a later date"
        )
    if when.day == today and when.at is not None:
        moment = datetime.combine(when.day, when.at, tzinfo=now_local.tzinfo)
        if moment <= now_local:
            raise WhenError(
                f"{when.clock()} today has already passed; ask the person for a later time"
            )
    if when.day - today > MAX_AHEAD:
        raise WhenError("that is more than two years away; ask the person for a nearer date")


# ---------------------------------------------------------------------------
# The person's words for what, made sendable
# ---------------------------------------------------------------------------

_DASHES = re.compile(r"\s*[—–]\s*|\s+-{1,2}\s+")
_DOLLARS = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)(\s?(?:k|m|million|thousand)\b)?", re.I)
_BARE_CLOCK = re.compile(r"\b([01]?\d|2[0-3]):[0-5]\d\b(?!\s*(?:a\.?m\.?|p\.?m\.?))", re.I)
_SCALE = {"k": "thousand", "m": "million", "million": "million", "thousand": "thousand"}


def _dollar_words(m: re.Match[str]) -> str:
    amount = m.group(1)
    scale = (m.group(2) or "").strip().lower()
    if scale:
        return f"{amount} {_SCALE[scale]} dollars"
    return f"{amount} dollars"


def plain_what(words: str) -> str:
    """``words`` as a reminder line any reply check passes.

    Deterministic, and only these changes: whitespace collapsed; a dash between
    words becomes a comma; an exclamation mark becomes a full stop; a dollar
    figure is written as words ("12,000 dollars"), the form every seat check
    passes. A bare clock time (``3:00`` with no AM or PM) is refused: the time
    belongs in ``when``, where code resolves it. Raises :class:`ReminderError`.
    """
    if not isinstance(words, str) or not words.strip():
        raise ReminderError("what is empty; pass the person's words for the reminder")
    text = re.sub(r"\s+", " ", words).strip()
    if len(text) > MAX_WHAT_CHARS:
        raise ReminderError(
            f"what is longer than {MAX_WHAT_CHARS} characters; keep the reminder to one line "
            "and put longer detail in a note"
        )
    if _BARE_CLOCK.search(text):
        raise ReminderError(
            "what carries a clock time; pass the time in when and leave it out of what"
        )
    text = _DASHES.sub(", ", text)
    text = text.replace("!", ".")
    text = _DOLLARS.sub(_dollar_words, text)
    text = re.sub(r"\.{2,}", ".", text).strip(" ,")
    return text[0].upper() + text[1:] if text else text


# ---------------------------------------------------------------------------
# Records and the receipt
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Reminder:
    due: date
    at: time | None
    what: str
    kind: str
    set_on: date
    name: str = ""

    @property
    def when(self) -> When:
        return When(self.due, self.at)

    def to_json(self) -> str:
        return json.dumps(
            {
                "due": self.due.isoformat(),
                "at": self.when.clock(),
                "when": self.when.words(),
                "what": self.what,
                "kind": self.kind,
                "set_on": self.set_on.isoformat(),
            },
            ensure_ascii=False,
            indent=1,
        )

    @classmethod
    def from_json(cls, name: str, text: str) -> Reminder | None:
        """The reminder in a record, or None for anything that is not one."""
        try:
            raw = json.loads(text)
            due = date.fromisoformat(str(raw["due"]))
            at = _parse_clock(raw.get("at"))
            what = str(raw["what"]).strip()
            kind = raw.get("kind") if raw.get("kind") in KINDS else KIND_REMINDER
            set_on = date.fromisoformat(str(raw.get("set_on") or raw["due"]))
        except (ValueError, KeyError, TypeError):
            return None
        if not what:
            return None
        return cls(due=due, at=at, what=what, kind=kind, set_on=set_on, name=name)


def _parse_clock(value: Any) -> time | None:
    if not isinstance(value, str) or not value.strip():
        return None
    m = re.fullmatch(r"(\d{1,2}):(\d{2}) (AM|PM)", value.strip())
    if not m:
        raise ValueError(f"not a clock time: {value!r}")
    hour = int(m.group(1)) % 12 + (12 if m.group(3) == "PM" else 0)
    return time(hour, int(m.group(2)))


def is_reminder_record(name: str) -> bool:
    return bool(_RECORD_RE.match(name or ""))


def record_name(due: date) -> str:
    return f"{RECORD_PREFIX}{due.isoformat()}-{secrets.token_hex(3)}.json"


def receipt_line(reminder: Reminder) -> str:
    """The one line the reply uses, exactly."""
    label = "Meeting" if reminder.kind == KIND_MEETING else "Reminder"
    return f"{label} kept for {reminder.when.words()}: {reminder.what}"


def build(when_words: str, what_words: str, kind: str | None, now_local: datetime) -> Reminder:
    """A validated reminder from the person's words. Raises WhenError or ReminderError."""
    resolved = resolve_when(when_words, now_local)
    what = plain_what(what_words)
    chosen = kind if kind in KINDS else KIND_REMINDER
    return Reminder(
        due=resolved.day, at=resolved.at, what=what, kind=chosen, set_on=now_local.date()
    )


def keep(store: str, person: str, reminder: Reminder, *, config: Any = None) -> Reminder:
    """Write ``reminder`` into ``person``'s own partition of ``store``."""
    name = record_name(reminder.due)
    try:
        record_store.code_write(store, person, name, reminder.to_json(), config=config)
    except record_store.RecordStoreError as exc:
        raise ReminderError(str(exc)) from exc
    return Reminder(
        due=reminder.due,
        at=reminder.at,
        what=reminder.what,
        kind=reminder.kind,
        set_on=reminder.set_on,
        name=name,
    )


def person_reminders(store: str, person: str, *, config: Any = None) -> list[Reminder]:
    """Every reminder in ``person``'s partition, soonest first. Records that are
    not reminders (the digest's own mark, anything malformed) are skipped."""
    out: list[Reminder] = []
    for row in record_store.code_list(store, person, config=config):
        name = str(row.get("name") or "")
        if not is_reminder_record(name):
            continue
        text = record_store.code_read(store, person, name, config=config)
        reminder = Reminder.from_json(name, text)
        if reminder is not None:
            out.append(reminder)
    return sorted(out, key=lambda r: (r.due, r.at or time(0, 0), r.name))


# ---------------------------------------------------------------------------
# The verified person of a turn
# ---------------------------------------------------------------------------


def verified_person(session_id: Any, *, config: Any = None) -> str | None:
    """The turn's strict per-turn verified sender when they are on the seat's
    roster now, else None. Keyed by the handler's session id; a scheduled turn
    (a cron session id) has none. The same rule as the partitioned store's own
    handlers."""
    from shared.cron_attribution import parse_cron_session
    from shared.inbound import SESSION_INBOUND_ORIGIN

    sid = session_id if isinstance(session_id, str) else ""
    if not sid or parse_cron_session(sid):
        return None
    origin = SESSION_INBOUND_ORIGIN.bound_this_turn(sid)
    address = ((origin.sender_address if origin is not None else "") or "").strip().lower()
    if not address or not record_store.listed_on_seat(address, config):
        return None
    return address


__all__ = [
    "KINDS",
    "KIND_MEETING",
    "KIND_REMINDER",
    "Reminder",
    "ReminderError",
    "SKILL",
    "STORE_SETTING",
    "When",
    "WhenError",
    "build",
    "clock_words",
    "day_words",
    "desk_settings",
    "is_reminder_record",
    "keep",
    "local_now",
    "person_reminders",
    "plain_what",
    "receipt_line",
    "reminder_store",
    "resolve_when",
    "seat_zone",
    "verified_person",
]
