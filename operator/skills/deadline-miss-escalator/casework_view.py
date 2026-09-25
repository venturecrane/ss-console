"""What the casework ledger says about one task or court date, for a pre_run.

CANONICAL SOURCE: ``operator/skills/task-list-keeper/casework_view.py``. The
deadline-miss-escalator carries a byte-identical copy, discovered and pinned by
``operator/tests/test_casework_view_sync.py``. Edit here, restamp the copy.

Two routines read the same ledger and must reach the same verdict about an
item, or a task would be both "in Monday's review" and "still in the deadline
digest": the task-list-keeper decides what to propose, and the escalator drops
what the keeper already owns. Both answers come from here, over the vendored
``casework_ledger`` fold (``derive_state``), so they cannot disagree.

Stdlib only; the ledger module is passed in, never imported, because each skill
path-loads its own vendored copy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

#: The pack default for how long a person's "leave it" keeps a task quiet, used
#: when the firm authored no ``case_manager.task_cleanup.keep_quiet_days``.
DEFAULT_KEEP_QUIET_DAYS = 30


def task_state(ledger, states: dict, matter_id: str, task_id: str | None):
    """The casework state for one Smokeball task, or None (never raised)."""
    if not matter_id or not task_id:
        return None
    try:
        return states.get(ledger.item_key(matter_id=matter_id, kind="task", source_id=task_id))
    except ValueError:
        return None


def date_state(ledger, states: dict, matter_id: str, event_id: str | None):
    """The casework state for one court date (Smokeball event), or None."""
    if not matter_id or not event_id:
        return None
    try:
        return states.get(ledger.item_key(matter_id=matter_id, kind="date", source_id=event_id))
    except ValueError:
        return None


def _within(day_iso: str | None, today: date, days: int) -> bool:
    try:
        start = date.fromisoformat(str(day_iso)[:10])
    except ValueError:
        return False
    return today < start + timedelta(days=max(0, days))


def is_quiet(ledger, state, today: date, keep_quiet_days: int) -> bool:
    """True while a person's answer still keeps this task out of every list.

    Three shapes of the same answer: a ``kept`` row, a line the person HELD
    ("leave 2"), and a KEEP line the person approved ("yes" to "I'll leave it").
    Each holds for ``keep_quiet_days`` from when it was said."""
    if state is None:
        return False
    if ledger.is_kept_quiet(state, today, keep_quiet_days):
        return True
    for decision in state.decisions.values():
        answered_keep = decision.verdict == "approved" and decision.payload.get("action") == "keep"
        if (decision.verdict == "held" or answered_keep) and _within(decision.verdict_ts, today, keep_quiet_days):
            return True
    return False


def has_pending_proposal(state) -> bool:
    """A numbered line about this item is out and nobody has answered it."""
    if state is None:
        return False
    return any(d.raise_event == "proposed" and d.verdict is None for d in state.decisions.values())


def awaiting_write(state) -> bool:
    """A write is authorized and has not landed or failed yet."""
    return state is not None and state.authorization is not None


def keeper_owns_task(ledger, state, today: date, keep_quiet_days: int) -> bool:
    """The task-list-keeper holds this task, so the deadline digest drops it:
    a proposal is out, a person told us to leave it, a write is on its way, or
    the Operator already handed it over once."""
    if state is None:
        return False
    return (
        has_pending_proposal(state)
        or awaiting_write(state)
        or bool(state.named)
        or is_quiet(ledger, state, today, keep_quiet_days)
    )


def brief_status(state) -> str:
    """``none`` when the date was never briefed; ``unanswered`` when a brief's
    decisions are still open; ``answered`` once every briefed line has a verdict."""
    if state is None:
        return "none"
    briefed = [d for d in state.decisions.values() if d.raise_event == "briefed"]
    if not briefed:
        return "none"
    return "unanswered" if any(d.verdict is None for d in briefed) else "answered"


def unmentioned_closes(ledger, states: dict) -> list:
    """Tasks the Operator closed on the record's evidence and has not yet told
    anyone about (Job 3), oldest close first, then by key for a stable order."""
    rows = [s for s in states.values() if s.kind == "task" and ledger.needs_mention(s)]
    return sorted(rows, key=lambda s: (s.last_completed_date or date.min, s.item_key))


# ---------------------------------------------------------------------------
# The authored ``case_manager:`` block (customer.yaml). Absent block, or an
# absent sub-block, means that job is OFF (ADR 0035): no defaults are imposed.
# The console validator (src/lib/operator/customer-yaml/sections-case-manager.ts)
# refuses a malformed block before it reaches a seat; this parse still treats
# anything it cannot read as unauthored rather than guessing.
# ---------------------------------------------------------------------------

LEVELS = ("surfaces", "prepares", "handles")
DEFAULT_MAX_LINES = 30

_WEEKDAYS = {
    "0": "Sunday",
    "7": "Sunday",
    "1": "Monday",
    "2": "Tuesday",
    "3": "Wednesday",
    "4": "Thursday",
    "5": "Friday",
    "6": "Saturday",
    "sun": "Sunday",
    "mon": "Monday",
    "tue": "Tuesday",
    "wed": "Wednesday",
    "thu": "Thursday",
    "fri": "Friday",
    "sat": "Saturday",
}


@dataclass(frozen=True)
class CaseManager:
    own_level: str | None = None
    legacy_task_ids: frozenset = frozenset()
    cleanup_level: str | None = None
    keep_quiet_days: int = DEFAULT_KEEP_QUIET_DAYS
    max_lines: int = DEFAULT_MAX_LINES
    quiet_level: str | None = None
    review_day: str | None = None


def _level(block) -> str | None:
    if not isinstance(block, dict):
        return None
    level = block.get("level")
    return level if level in LEVELS else None


def _pos(value, fallback: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return fallback
    return value


def _review_day(node) -> str | None:
    """The weekday the task review runs, read off the authored cron entry for
    ``task-list-keeper`` (its day-of-week field), or None when the schedule
    names no single day. Never assumed: the digest names a day only when the
    firm's own schedule says it."""
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if cur.get("skill") == "task-list-keeper" and isinstance(cur.get("schedule"), str):
                fields = cur["schedule"].split()
                return _WEEKDAYS.get(fields[4].strip().lower()) if len(fields) == 5 else None
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


def load_case_manager(customer_yaml) -> CaseManager | None:
    """The authored block, or None when the seat authored none."""
    data = customer_yaml if isinstance(customer_yaml, dict) else {}
    block = data.get("case_manager")
    if not isinstance(block, dict) or not block:
        return None
    raw_own, raw_cleanup = block.get("own_tasks"), block.get("task_cleanup")
    own: dict = raw_own if isinstance(raw_own, dict) else {}
    cleanup: dict = raw_cleanup if isinstance(raw_cleanup, dict) else {}
    legacy = own.get("legacy_task_ids")
    legacy_ids = frozenset(
        str(t).strip().casefold()
        for t in (legacy if isinstance(legacy, list) else [])
        if isinstance(t, str) and t.strip()
    )
    return CaseManager(
        own_level=_level(own),
        legacy_task_ids=legacy_ids,
        cleanup_level=_level(cleanup),
        keep_quiet_days=_pos(cleanup.get("keep_quiet_days"), DEFAULT_KEEP_QUIET_DAYS),
        max_lines=_pos(cleanup.get("max_lines"), DEFAULT_MAX_LINES),
        quiet_level=_level(block.get("quiet")),
        review_day=_review_day(data),
    )


def is_operator_task(cm: CaseManager, task_id: str | None, subject: str) -> bool:
    """The Operator created this task: its subject carries the provenance stamp
    the connector adds to every Operator write, or the firm's config lists it
    (the July 2026 rehearsal tasks predate the stamp). Smokeball's own creator
    field cannot answer this: it records the consenting human for every write
    (smokeball_connector/server.py ``_stamp``), and a task read never echoes
    the owning staff id (task_update.py)."""
    if subject.lstrip().lower().startswith("[operator]"):
        return True
    return bool(task_id) and str(task_id).strip().casefold() in cm.legacy_task_ids
