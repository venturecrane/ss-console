"""The two-hour reminder to the Captain: a firm person still waiting.

WHY (2026-11-25, Ashton & Price). The Operator told an attorney "Let me look
into that. I'll reply in this thread." The request card went to team@ among the
routine cards, the hourly triage listed it to team@, and nobody acted for 19
hours. The card that leaves a client waiting now goes to the Captain as well
(``src/lib/operator/request-card-email.ts``); this is the second time he hears
of it: once, when the item is still open two hours after the reply.

WHICH ITEMS. A firm person is waiting on SMD when an item is a ``could_not``
line (a reply told them the Operator could not do it) or an owed item
(``job_hand_step``: the broker recorded work SMD owes them, ``smd_owed``). Its
onset is the reply's time, or the owed row's. It is due when it is still listed
two hours after that, on a seat that is not a footnoted test seat, and has not
been reminded before. Only items younger than ``HORIZON`` are reminded, so the
first run after this shipped does not mail the whole backlog.

ONCE PER ITEM. The run's state file carries ``reminded``: the state key (a
hash, the file is a public Actions artifact) and the time of each reminder
sent, kept for ``HORIZON``. A send that fails is not recorded and is tried again
next run. The Resend idempotency key is per item too, so two racing runs within
a day send one email.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from bin.lib.work_health import Item, hhmm
from bin.lib.work_health_mail import state_key
from bin.lib.work_health_owed import KIND as OWED_KIND

#: Item kinds that mean a firm person is waiting on SMD.
WAITING_KINDS = frozenset({"could_not", OWED_KIND})
#: How long after the reply the Captain is reminded.
AFTER = timedelta(hours=2)
#: The oldest item reminded, and how long a reminder is remembered.
HORIZON = timedelta(hours=72)
#: The state file's key for the reminders sent.
REMINDED = "reminded"
#: Items older than this are never reminded: the reminder shipped at this time
#: (PR #4247), and its first runs mailed the 72-hour backlog of items SMD had
#: already answered by hand (2026-11-26). Only replies after it are reminded.
START = datetime(2026, 11, 26, 20, 9, 42, tzinfo=timezone.utc)
#: At most this many reminders per run, so a burst never trips the sender's
#: rate limit; the rest go on the next run.
PER_RUN = 3
#: Where the reminder goes when CAPTAIN_ALERT_EMAIL is not set.
DEFAULT_CAPTAIN = "scott@smd.services"


def carried(previous: Optional[dict], now: datetime) -> dict[str, str]:
    """The reminders the previous run recorded that are still inside the horizon."""
    raw = (previous or {}).get(REMINDED) if isinstance(previous, dict) else None
    out: dict[str, str] = {}
    for key, at in (raw or {}).items() if isinstance(raw, dict) else ():
        try:
            sent = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
        except ValueError:
            continue
        if sent.tzinfo is None:
            sent = sent.replace(tzinfo=timezone.utc)
        if now - sent <= HORIZON:
            out[str(key)] = sent.astimezone(timezone.utc).isoformat()
    return out


def due(items: list[Item], reminded: dict[str, str], now: datetime, test_seats: frozenset[str]) -> list[Item]:
    """The waiting items to remind the Captain of this run."""
    return [
        it
        for it in items
        if it.kind in WAITING_KINDS
        and it.seat not in test_seats
        and it.onset >= START
        and AFTER <= now - it.onset <= HORIZON
        and state_key(it.key) not in reminded
    ]


def subject(it: Item, now: datetime) -> str:
    hours = int((now - it.onset).total_seconds() // 3600)
    return f"Still waiting {hours}h: {it.seat}: a firm person was told at {hhmm(it.onset)} we would get back to them"


def text(it: Item, now: datetime) -> str:
    return (
        f"WHAT HAPPENED: A firm person on {it.seat} was answered at {hhmm(it.onset)} without what they asked, "
        "and nothing has closed it since.\n\n"
        "WHAT YOU NEED TO DO: Give this email to an agent to finish it and answer them in that thread.\n\n"
        "You get this email once for this request.\n\n"
        "---\n"
        "DETAILS, for the agent who picks this up\n"
        f"Seat: {it.seat}. Checked {hhmm(now)}.\n"
        f"{it.what}\n"
        f"To do: {it.todo}\n"
    )


def remind(
    items: list[Item],
    reminded: dict[str, str],
    now: datetime,
    test_seats: frozenset[str],
    send: Callable[[str, str, str], Optional[str]],
) -> tuple[dict[str, str], Optional[str]]:
    """Send each due reminder through ``send(subject, text, run_key)`` (None on
    success, else the error). Returns the reminders to carry, with each sent one
    added, and the first send error."""
    out = dict(reminded)
    first_error = None
    for it in due(items, reminded, now, test_seats)[:PER_RUN]:
        key = state_key(it.key)
        error = send(subject(it, now), text(it, now), f"work-health-waiting:{key}")
        if error and "409" not in error:
            # A 409 is the sender refusing a repeat of the same idempotency key:
            # that reminder already went, so it is recorded as sent below.
            first_error = first_error or error
            if "429" in error:
                break
            continue
        out[key] = now.astimezone(timezone.utc).isoformat()
    return out, first_error


def run(
    items: list[Item],
    previous: Optional[dict],
    now: datetime,
    test_seats: frozenset[str],
    dry_run: bool,
    send: Callable[..., Optional[str]],
) -> tuple[dict[str, str], Optional[str]]:
    """The run's reminders: carry the previous run's, and (unless ``dry_run``)
    send each due one to the Captain through ``send(subject, text, run_key,
    to=...)``, the work-health sender. Returns the reminders to save and the
    first send error. The error is logged and never fails the run: a reminder
    not sent is tried again next run, and failing the run hid the hourly report."""
    reminded = carried(previous, now)
    if dry_run:
        print(f"waiting reminders due: {len(due(items, reminded, now, test_seats))} (dry run)")
        return reminded, None
    to = os.environ.get("CAPTAIN_ALERT_EMAIL") or DEFAULT_CAPTAIN
    reminded, error = remind(items, reminded, now, test_seats, lambda s, t, k: send(s, t, k, to=to))
    if error:
        print(f"WAITING REMINDER SEND FAILED: {error}", file=sys.stderr)
    return reminded, error


__all__ = [
    "AFTER",
    "DEFAULT_CAPTAIN",
    "HORIZON",
    "PER_RUN",
    "START",
    "REMINDED",
    "WAITING_KINDS",
    "carried",
    "due",
    "remind",
    "run",
]
