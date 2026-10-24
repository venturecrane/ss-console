"""Job outcomes for the Operator help review, counts-shaped (ss-console#3423, increment 3).

WHY A VERB. The help review (the initiation plugin's ``operator_help_signals``
with ``scope: "review"``) asks one question of the job ledgers: in the last
week, which requests ended HELD or FAILED, on which lane, and for what class of
hold. Those ledgers live in the audit file this broker owns; the agent process
cannot open it, which is why increment 2 of the help pointer read no job state
at all (``help_state.py``, "JOB STATE IS NOT READ"). This verb is the narrow
read that closes that gap.

WHAT IT RETURNS, AND WHAT IT NEVER DOES. One row per held or failed job updated
inside the window: its lane, its state, its hold class (the firm-safe token from
``hold_class.classify``, or ``lane_unproven`` for a job waiting on its lane's
proof, the same reason prefix ``request_cards._HOLD_SQL`` reads), and its
``request_ref`` (the request email's internet message id, so the review can
count distinct requesters by joining the agent's own request index). Never a
matter, a reason's text, an amount or a document. The plugin turns the rows
into counts before anything reaches a model.

READ-ONLY, a fresh ``mode=ro`` connection per call, one fixed statement per
lane (no table name is ever composed from input). A lane whose table does not
exist is a lane this seat does not run, not an error. A ledger that cannot be
opened RAISES, so the caller reports "could not look" instead of "nothing held".
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from . import hold_class
from .broker_context import BrokerContext

VERB = "help_job_signals"
DEFAULT_WINDOW_DAYS = 7
MAX_WINDOW_DAYS = 30
#: A hold waiting on its lane's proof (``lane_proofs``), not a hold class.
LANE_UNPROVEN = "lane_unproven"

#: One fixed statement per lane, written out whole.
_SQL: dict[str, str] = {
    "demand": (
        "SELECT state, reason, request_ref FROM demand_jobs WHERE substr(updated_at,1,19) >= ?"
        " AND (state IN ('held','failed') OR reason LIKE 'lane_unproven:%')"
    ),
    "drafting": (
        "SELECT state, reason, request_ref FROM drafting_jobs WHERE substr(updated_at,1,19) >= ?"
        " AND (state IN ('held','failed') OR reason LIKE 'lane_unproven:%')"
    ),
    "litigation": (
        "SELECT state, reason, request_ref FROM litigation_jobs WHERE substr(updated_at,1,19) >= ?"
        " AND (state IN ('held','failed') OR reason LIKE 'lane_unproven:%')"
    ),
    "medchron": (
        "SELECT state, reason, request_ref FROM medchron_jobs WHERE substr(updated_at,1,19) >= ?"
        " AND (state IN ('held','failed') OR reason LIKE 'lane_unproven:%')"
    ),
    "negotiation": (
        "SELECT state, reason, request_ref FROM negotiation_jobs WHERE substr(updated_at,1,19) >= ?"
        " AND (state IN ('held','failed') OR reason LIKE 'lane_unproven:%')"
    ),
}
LANES: tuple[str, ...] = tuple(_SQL)


def _window(request: dict[str, Any]) -> int:
    raw = request.get("days", DEFAULT_WINDOW_DAYS)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return DEFAULT_WINDOW_DAYS
    return max(1, min(MAX_WINDOW_DAYS, raw))


def _class_of(lane: str, state: object, reason: object) -> str | None:
    text = str(reason or "")
    if text.startswith(LANE_UNPROVEN + ":"):
        return LANE_UNPROVEN
    if state == "held":
        return hold_class.classify(lane, text)[0]
    return None


def read_rows(audit_db_path: str | None, *, days: int, now: datetime | None = None) -> dict[str, Any]:
    """The rows and the lanes read. Raises when the ledger cannot be read."""
    if not audit_db_path:
        raise ValueError("help_job_signals: no audit ledger is configured on this seat")
    moment = now or datetime.now(timezone.utc)
    horizon = (moment - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    conn = sqlite3.connect(f"file:{audit_db_path}?mode=ro", uri=True, timeout=5)
    try:
        conn.execute("PRAGMA busy_timeout=2000")
        rows: list[dict[str, Any]] = []
        lanes_read: list[str] = []
        for lane, sql in _SQL.items():
            try:
                found = conn.execute(sql, (horizon,)).fetchall()
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc):
                    continue
                raise
            lanes_read.append(lane)
            for state, reason, ref in found:
                rows.append(
                    {
                        "lane": lane,
                        "state": state if state in ("held", "failed") else "held",
                        "hold_class": _class_of(lane, state, reason),
                        "request_ref": ref if isinstance(ref, str) and ref else None,
                    }
                )
        return {"window_days": days, "lanes_read": lanes_read, "rows": rows}
    finally:
        conn.close()


def help_job_signals(
    broker: BrokerContext, _action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    return {"ok": True, **read_rows(broker.audit_db_path, days=_window(request))}


__all__ = ["DEFAULT_WINDOW_DAYS", "LANES", "LANE_UNPROVEN", "MAX_WINDOW_DAYS", "VERB", "help_job_signals", "read_rows"]
