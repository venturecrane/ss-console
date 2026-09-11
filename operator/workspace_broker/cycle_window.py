"""The allowance window: a billing cycle, or the calendar month when no cycle
is authored (ss#2618).

WHY THIS FILE IS SEPARATE AND DEPENDENCY-FREE. Three surfaces compute this
window and they must agree exactly:

  1. this module, on the seat (the broker, which is the only computer -- the
     daemon and the runner relay what it returns);
  2. ``src/lib/admin/medchron-jobs-read.ts``, in the admin console;
  3. the laptop pipeline in the private engagements repo, which is the surface
     every delivered chronology has actually run on.

Before 2026-09-11 all three keyed on ``created_at[:7]``, and #3 derived that
prefix from ``time.strftime`` -- LOCAL time -- so the laptop and the seat already
disagreed about when a month began. That is the class of bug this file exists to
end: one algorithm, stdlib only, copied rather than re-implemented, with the
shared fixture (``cycle_window_fixture.json``) hash-pinned on every side.

THE RULE (Stripe's own): a cycle starts on the anchor day, clamped to the last
day of a short month, and RETURNS to the anchor day afterwards. A 31st anchor
runs Jan 31 -> Feb 28 -> Mar 31, never Feb 28 -> Mar 28. Chaining from the
previous end is the common way to get this wrong, so each boundary is computed
from the anchor independently.

Everything is UTC, because ``created_at`` is (``audit_ledger._iso_utc``). A cycle
therefore turns at UTC midnight, which is 5pm the previous day in Phoenix. That
is deliberate and matches the instant the firm is actually billed on.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

# `created_at` shape: 2026-09-11T12:34:56.789Z (audit_ledger._iso_utc). Bounds
# are emitted in this SAME full form, never as bare dates, so no comparison ever
# depends on a shorter string sorting before a longer one.
_DAY_START = "T00:00:00.000Z"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

ANCHOR_KEY = "chronology_package_cycle_anchor_day"
EFFECTIVE_FROM_KEY = "chronology_package_cycle_effective_from"

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class AnchorInvalid(ValueError):
    """An authored anchor that cannot be read.

    Deliberately NOT the same answer as "absent". Absent means "no cycle is
    authored, meter by calendar month" -- a known state. Invalid means a human
    authored a control the seat cannot honour, and silently metering on a
    different window than the firm believes is the harm. So this refuses and
    names the key, matching the unauthored-allowance refusal.
    """


@dataclass(frozen=True)
class Window:
    """Half-open ``[start, end)``. ``label`` is PROSE for a human and is the only
    part safe to put in a sentence; never parse it."""

    start: str
    end: str
    label: str
    anchored: bool


def _clamp(year: int, month: int, day: int) -> date:
    """The anchor day in this month, or its last day when the month is short."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _at_day_start(d: date) -> str:
    return d.isoformat() + _DAY_START


def _label(start: date, end: date, anchored: bool) -> str:
    if not anchored:
        return f"{start.year:04d}-{start.month:02d}"
    last = end - timedelta(days=1)  # `end` is exclusive; say the last day included
    return f"the cycle ending {_MONTHS[last.month - 1]} {last.day}"


def resolve_anchor(settings: dict | None) -> int | None:
    """The authored anchor day, or None when no cycle is authored.

    Raises ``AnchorInvalid`` when the key is present but unreadable. The reader
    is where the three surfaces actually diverge -- `"15"` (quoted in YAML)
    validates against the open scalar map, projects to D1 as a string, and then
    reads as 15 on one surface and nothing on another -- so the strictness lives
    here and is fixture-pinned rather than reimplemented per caller.
    """
    if not isinstance(settings, dict) or ANCHOR_KEY not in settings:
        return None
    value = settings[ANCHOR_KEY]
    # bool is an int in Python; `true` is not a day of the month.
    if isinstance(value, bool) or not isinstance(value, int):
        raise AnchorInvalid(f"{ANCHOR_KEY}: must be a whole number from 1 to 31")
    if not 1 <= value <= 31:
        raise AnchorInvalid(f"{ANCHOR_KEY}: must be a whole number from 1 to 31")
    return value


def resolve_effective_from(settings: dict | None) -> str | None:
    """The date before which no cycle window may reach.

    Without it, authoring an anchor re-partitions every row already in the
    ledger (the debit rule keys on ``created_at``), which silently hands the
    firm a second allowance or bills it twice for the same pages depending on
    the day chosen. With it the first cycle is simply short.
    """
    if not isinstance(settings, dict) or EFFECTIVE_FROM_KEY not in settings:
        return None
    value = settings[EFFECTIVE_FROM_KEY]
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise AnchorInvalid(f"{EFFECTIVE_FROM_KEY}: must be a date, YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise AnchorInvalid(f"{EFFECTIVE_FROM_KEY}: must be a real date, YYYY-MM-DD") from exc
    return value


def cycle_window(now: str, anchor_day: int | None = None, effective_from: str | None = None) -> Window:
    """The window ``now`` falls in. ``now`` is an ISO-8601 UTC timestamp."""
    today = date.fromisoformat(now[:10])

    if anchor_day is None:
        start = date(today.year, today.month, 1)
        ny, nm = _next_month(today.year, today.month)
        end = date(ny, nm, 1)
        anchored = False
    else:
        this = _clamp(today.year, today.month, anchor_day)
        if today >= this:
            sy, sm = today.year, today.month
        else:
            sy, sm = _prev_month(today.year, today.month)
        start = _clamp(sy, sm, anchor_day)
        ey, em = _next_month(sy, sm)
        end = _clamp(ey, em, anchor_day)
        anchored = True

    label = _label(start, end, anchored)
    if effective_from is not None:
        floor = date.fromisoformat(effective_from)
        if floor > start:
            # The first cycle after authoring is short: nothing created before
            # the firm's cycle began is re-partitioned into it.
            start = floor
    return Window(start=_at_day_start(start), end=_at_day_start(end), label=label, anchored=anchored)
