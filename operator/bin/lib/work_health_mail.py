"""The work-health email: which items are new, which are still open, and the text.

DEDUPE IS BY KEY, AGAINST THE PREVIOUS RUN'S STATE. The workflow carries
``{key: first_seen}`` from one run to the next (a GitHub Actions artifact). An
item whose key was in the previous run's state is "still open" and gets one
line with its age; anything else is NEW and gets the full text. Without a
previous state (the first run, or an expired artifact) an item is new only if
its onset is after the previous run started, so a lost state file degrades to a
slightly louder email, never to a silent one.

WHEN TO SEND (``decide``):
  * any LISTED item (see ``is_listed``: everything on a client seat, and a
    failed read anywhere) -> send
  * nothing listed, and this is the day's first run at/after the all-clear
    hour -> send the all-clear (test-seat counts ride along on it), so silence
    is provable
  * otherwise -> send nothing
A seat that could not be read is an item, so a failed read can never produce
an all-clear.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from bin.lib.work_health import Item, hhmm, span

SUBJECT_PREFIX = "[SMD Ops] Work health"


def state_key(key: str) -> str:
    """The state file's key for an item: a hash, because the file is a GitHub
    Actions artifact on a PUBLIC repository. Nothing in it may name work."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


@dataclass
class Plan:
    send: bool
    subject: str
    text: str
    new: list[Item]
    still_open: list[Item]
    state: dict[str, str]


def _first_seen(item: Item, previous: Optional[dict[str, str]], now: datetime) -> datetime:
    if previous and state_key(item.key) in previous:
        try:
            seen = datetime.fromisoformat(previous[state_key(item.key)])
            return min(seen, item.onset)
        except ValueError:
            pass
    return min(item.onset, now)


def split_new(
    items: list[Item], previous: Optional[dict[str, str]], since: Optional[datetime], now: datetime
) -> tuple[list[Item], list[Item], dict[str, str]]:
    new, still = [], []
    state: dict[str, str] = {}
    for it in sorted(items, key=lambda i: (i.rank, i.seat, i.onset)):
        if previous is not None:
            is_new = state_key(it.key) not in previous
        else:
            is_new = since is None or it.onset >= since
        (new if is_new else still).append(it)
        state[state_key(it.key)] = _first_seen(it, previous, now).isoformat()
    return new, still, state


def all_clear_due(now: datetime, since: Optional[datetime], hour_utc: int) -> bool:
    """True on the first run at or after today's all-clear hour."""
    mark = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    if now < mark:
        return False
    return since is None or since < mark


def _seat_order(seats: list[str]) -> list[str]:
    # The paying client first, then the rest alphabetically (Captain: "A&P first").
    return sorted(seats, key=lambda s: (s != "ashton-price", s))


#: Below this rank an item on a TEST seat is only counted, never listed. At or
#: above it (``monitor_gap``, ``unreadable``) it is listed wherever it is: a
#: broken read on a test seat usually means a broken pipeline every seat shares.
LISTED_ON_TEST_SEATS_BELOW_RANK = 1


def is_listed(it: Item, test_seats: frozenset[str]) -> bool:
    """Whether an item gets its own line, as opposed to a count in the footnote.

    WHY TEST SEATS ARE A FOOTNOTE (Captain, 2026-10-16). The email had 33 lines
    and he could not tell which were his. Problems on SMD's own proving,
    preprod and internal seats are SMD's working state, not a client waiting;
    they are counted on one line so they are never silent, and the rest of the
    email is about clients."""
    return it.seat not in test_seats or it.rank < LISTED_ON_TEST_SEATS_BELOW_RANK


def _things(n: int) -> str:
    return "1 thing needs" if n == 1 else f"{n} things need"


def render(
    *,
    new: list[Item],
    still_open: list[Item],
    seats: list[str],
    gaps: dict[str, list[str]],
    now: datetime,
    state: dict[str, str],
    resolved: Optional[dict[str, int]] = None,
    test_seats: frozenset[str] = frozenset(),
) -> tuple[str, str]:
    """The email: what needs a person first, in plain sentences.

    Layout (Captain, 2026-10-16: "it just puts in a lot of noise"): one count
    line, then each client seat's items (new ones with their To do, earlier
    ones one line each with their age), then everything that needs no action
    on one line apiece. Repeats are collapsed upstream (one item per problem,
    with a count), so a line here is a problem, never an occurrence."""
    listed_new = [i for i in new if is_listed(i, test_seats)]
    listed_old = [i for i in still_open if is_listed(i, test_seats)]
    footnoted: dict[str, int] = {}
    for it in new + still_open:
        if not is_listed(it, test_seats):
            footnoted[it.seat] = footnoted.get(it.seat, 0) + 1
    total = len(listed_new) + len(listed_old)
    lines = [f"{_things(total)} attention, {len(listed_new)} new since the last email.", ""]
    for seat in _seat_order(sorted({i.seat for i in listed_new + listed_old})):
        seat_new = [i for i in listed_new if i.seat == seat]
        seat_old = [i for i in listed_old if i.seat == seat]
        lines.append(seat)
        for it in seat_new:
            lines.append(f"  NEW  {it.what}")
            lines.append(f"       To do: {it.todo}")
        for it in seat_old:
            seen = datetime.fromisoformat(state[state_key(it.key)])
            lines.append(f"  {it.what} (open {span(now - seen)})")
        lines.append("")
    lines += _footer(seats, gaps, now, resolved, footnoted)
    subject = f"{SUBJECT_PREFIX}: {total} need attention ({len(listed_new)} new)"
    return subject, "\n".join(lines) + "\n"


def _footer(
    seats: list[str],
    gaps: dict[str, list[str]],
    now: datetime,
    resolved: Optional[dict[str, int]],
    footnoted: dict[str, int],
) -> list[str]:
    lines: list[str] = []
    if footnoted:
        counts = ", ".join(f"{s} {footnoted[s]}" for s in _seat_order(list(footnoted)))
        lines.append(f"Test seats, not listed: {counts}.")
    if resolved:
        done = ", ".join(f"{s} {resolved[s]}" for s in _seat_order(list(resolved)))
        lines.append(f"Answered by later work, no action: {done}.")
    if gaps:
        lines.append("Not checked: " + "; ".join(f"{s} ({gaps[s][0]})" for s in _seat_order(list(gaps))) + ".")
    lines.append(f"Checked {len(seats)} seat(s) at {hhmm(now)}. What each line means: docs/handbook/work-health.md.")
    return lines


def render_all_clear(
    seats: list[str],
    gaps: dict[str, list[str]],
    now: datetime,
    footnoted: Optional[dict[str, int]] = None,
) -> tuple[str, str]:
    lines = [
        "Nothing needs attention: no request waiting on a reply, no stuck, held or failed job, "
        "no missed or silent scheduled run, no failing connection, no new error.",
        "",
        *_footer(seats, gaps, now, None, footnoted or {}),
    ]
    return f"{SUBJECT_PREFIX}: all clear", "\n".join(lines) + "\n"


def decide(
    *,
    items: list[Item],
    seats: list[str],
    gaps: dict[str, list[str]],
    previous: Optional[dict[str, str]],
    since: Optional[datetime],
    now: datetime,
    all_clear_hour_utc: int,
    resolved: Optional[dict[str, int]] = None,
    test_seats: frozenset[str] = frozenset(),
) -> Plan:
    """Every item is carried in the state, listed or not, so a test-seat item
    that later becomes listable keeps its true age. An hour with only footnoted
    items sends nothing; they ride along on the daily all-clear instead."""
    now = now.astimezone(timezone.utc)
    new, still, state = split_new(items, previous, since, now)
    if any(is_listed(it, test_seats) for it in items):
        subject, text = render(
            new=new,
            still_open=still,
            seats=seats,
            gaps=gaps,
            now=now,
            state=state,
            resolved=resolved,
            test_seats=test_seats,
        )
        return Plan(True, subject, text, new, still, state)
    if all_clear_due(now, since, all_clear_hour_utc):
        footnoted: dict[str, int] = {}
        for it in items:
            footnoted[it.seat] = footnoted.get(it.seat, 0) + 1
        subject, text = render_all_clear(seats, gaps, now, footnoted)
        return Plan(True, subject, text, new, still, state)
    return Plan(False, "", "", new, still, state)
