"""The work-health email: which items are new, which are still open, and the text.

DEDUPE IS BY KEY, AGAINST THE PREVIOUS RUN'S STATE. The workflow carries
``{key: first_seen}`` from one run to the next (a GitHub Actions artifact). An
item whose key was in the previous run's state is "still open" and gets one
line with its age; anything else is NEW and gets the full text. Without a
previous state (the first run, or an expired artifact) an item is new only if
its onset is after the previous run started, so a lost state file degrades to a
slightly louder email, never to a silent one.

WHEN TO SEND (``decide``):
  * any item at all -> send (new items lead; still-open items are one line each)
  * no items, and this is the day's first run at/after the all-clear hour ->
    send the one-line all-clear, so silence is provable
  * otherwise -> send nothing
A seat that could not be read is an item, so a failed read can never produce
an all-clear.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from bin.lib.work_health import Item, span

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


def render(
    *,
    new: list[Item],
    still_open: list[Item],
    seats: list[str],
    gaps: dict[str, list[str]],
    now: datetime,
    state: dict[str, str],
) -> tuple[str, str]:
    lines: list[str] = []
    for seat in _seat_order(seats):
        seat_new = [i for i in new if i.seat == seat]
        seat_old = [i for i in still_open if i.seat == seat]
        if not seat_new and not seat_old:
            continue
        lines.append(f"== {seat} ({len(seat_new)} new, {len(seat_old)} still open)")
        for it in seat_new:
            lines.append(f"NEW  {it.what}")
            lines.append(f"     To do: {it.todo}")
        for it in seat_old:
            seen = datetime.fromisoformat(state[state_key(it.key)])
            lines.append(f"OPEN {span(now - seen)}  {it.what}")
        lines.append("")
    gap_lines = [f"  {seat}: {g}" for seat in _seat_order(list(gaps)) for g in gaps[seat]]
    if gap_lines:
        lines.append("Not checked this run:")
        lines.extend(gap_lines)
        lines.append("")
    lines.append(f"Checked {len(seats)} seat(s) at {now.strftime('%Y-%m-%d %H:%MZ')}: {', '.join(_seat_order(seats))}.")
    lines.append("What each line means and how to act on it: docs/handbook/work-health.md.")
    counts = {}
    for it in new + still_open:
        counts[it.seat] = counts.get(it.seat, 0) + 1
    per_seat = ", ".join(f"{s} {counts[s]}" for s in _seat_order(list(counts)))
    subject = f"{SUBJECT_PREFIX}: {len(new)} new, {len(still_open)} still open ({per_seat})"
    return subject, "\n".join(lines) + "\n"


def render_all_clear(seats: list[str], gaps: dict[str, list[str]], now: datetime) -> tuple[str, str]:
    lines = [
        f"All clear at {now.strftime('%Y-%m-%d %H:%MZ')}: no request waiting on a reply, no stuck, held or failed job, "
        "no missed or silent scheduled run, no failing connection, no new error.",
        f"Seats checked: {', '.join(_seat_order(seats))}.",
    ]
    gap_lines = [f"  {seat}: {g}" for seat in _seat_order(list(gaps)) for g in gaps[seat]]
    if gap_lines:
        lines += ["Not checked:", *gap_lines]
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
) -> Plan:
    now = now.astimezone(timezone.utc)
    new, still, state = split_new(items, previous, since, now)
    if items:
        subject, text = render(new=new, still_open=still, seats=seats, gaps=gaps, now=now, state=state)
        return Plan(True, subject, text, new, still, state)
    if all_clear_due(now, since, all_clear_hour_utc):
        subject, text = render_all_clear(seats, gaps, now)
        return Plan(True, subject, text, [], [], state)
    return Plan(False, "", "", [], [], state)
