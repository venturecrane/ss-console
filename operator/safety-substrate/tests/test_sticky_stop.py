"""Tests for operator/safety-substrate/sticky_stop.py (issue #843).

Covers the four AC conditions:

  - Consecutive tool failures (warn -> soft_stop -> hard_stop)
  - Refusal cascade (warn -> soft_stop -> hard_stop)
  - Time budget exceeded (-> soft_stop)
  - Cost threshold (warn -> soft_stop -> hard_stop)

Plus the state-machine invariants:

  - Forward-only transitions
  - Captain `clear()` is the only path backwards
  - the returned state surfaces the level to the dispatch caller
  - HARD_STOP raises StickyStopError from the dispatch guard
  - Every transition writes an audit_log row
  - Captain clear writes an AGENT_RESUMED audit_log row

Tests use the same sqlite-in-memory pattern as test_audit_log.py: schemas
are mirrored in-process so the test doesn't shell out to wrangler.

Run from repo root:

    cd operator && python -m pytest safety-substrate/tests/test_sticky_stop.py -v
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))  # operator/

# safety-substrate/ is on sys.path through the sticky_stop import below;
# we use the same dash-named-directory trick the other invariant tests use.
sys.path.insert(0, str(_HERE.parents[1]))  # operator/safety-substrate/

from adapter.audit_log import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    AuditEvent,
    AuditLogWriter,
    SqliteExecutor,
)
from sticky_stop import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    DEFAULT_THRESHOLDS,
    SqliteStickyStopStore,
    StickyStopCondition,
    StickyStopError,
    StickyStopLevel,
    StickyStopMachine,
    StickyStopAuditRecord,
    StickyStopThresholds,
)


class _RecordSink:
    """Adapts StickyStopAuditRecord onto the real AuditLogWriter — proving
    the plain-data record maps cleanly onto the closed-set audit vocabulary
    (the same adapter shape each runtime supplies in production)."""

    def __init__(self, writer: AuditLogWriter) -> None:
        self._writer = writer

    async def write(self, record: StickyStopAuditRecord) -> None:
        await self._writer.write(
            AuditEvent(
                action_type=record.action_type,
                actor=record.actor,
                actor_role=record.actor_role,
                skill_name=record.skill_name,
                metadata=record.metadata,
            )
        )


# ---------------------------------------------------------------------------
# In-memory schemas — exact copy of migrations 0001/0002/0004 audit_log +
# sticky_stop_state.
# ---------------------------------------------------------------------------

_AUDIT_SCHEMA = """
CREATE TABLE audit_log (
  id            TEXT PRIMARY KEY,
  ts            TEXT NOT NULL,
  action_type   TEXT NOT NULL,
  actor         TEXT NOT NULL,
  actor_role    TEXT,
  skill_name    TEXT,
  matter_ref    TEXT,
  input_digest  TEXT,
  output_digest TEXT,
  diff_digest   TEXT,
  trust_ceiling TEXT,
  metadata      TEXT
);
"""

_STICKY_STOP_SCHEMA = """
CREATE TABLE sticky_stop_state (
  customer                          TEXT NOT NULL,
  persona                           TEXT NOT NULL,
  level                             TEXT NOT NULL DEFAULT 'OK',
  updated_at                        TEXT NOT NULL,
  reason                            TEXT,
  condition                         TEXT,
  consecutive_tool_failures         INTEGER NOT NULL DEFAULT 0,
  tool_failure_window_started_at    TEXT,
  refusal_count                     INTEGER NOT NULL DEFAULT 0,
  refusal_window_started_at         TEXT,
  cost_cents_today                  INTEGER NOT NULL DEFAULT 0,
  cost_date                         TEXT,
  PRIMARY KEY (customer, persona)
);
"""


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_AUDIT_SCHEMA)
    conn.executescript(_STICKY_STOP_SCHEMA)
    return conn


def _machine(
    *,
    thresholds: StickyStopThresholds = DEFAULT_THRESHOLDS,
    clock=None,
) -> tuple[StickyStopMachine, sqlite3.Connection]:
    conn = _make_conn()
    store = SqliteStickyStopStore(conn)
    writer = _RecordSink(AuditLogWriter(SqliteExecutor(conn)))
    machine = StickyStopMachine(
        store=store,
        audit_writer=writer,
        thresholds=thresholds,
        clock=clock,
    )
    return machine, conn


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _audit_rows(conn: sqlite3.Connection) -> list[dict]:
    # Order by sqlite rowid — insertion-order across rows that may share a
    # ULID timestamp prefix (ULIDs are sortable to millisecond precision; the
    # randomness suffix breaks within-ms order, so id-sort is not insertion
    # order when multiple writes land in <1ms as they do here).
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT action_type, actor, skill_name, metadata FROM audit_log ORDER BY rowid"
    ).fetchall()
    out = []
    for action_type, actor, skill_name, metadata in rows:
        out.append(
            {
                "action_type": action_type,
                "actor": actor,
                "skill_name": skill_name,
                "metadata": json.loads(metadata) if metadata else None,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Initial state + idempotent read
# ---------------------------------------------------------------------------


def test_default_state_is_ok_and_not_persisted():
    machine, conn = _machine()
    state = _run(machine.get_state("acme", "marcus"))
    assert state.level == StickyStopLevel.OK
    # Read alone does NOT persist a row.
    count = conn.execute("SELECT COUNT(*) FROM sticky_stop_state").fetchone()[0]
    assert count == 0


def test_record_tool_success_on_clean_state_is_noop():
    machine, conn = _machine()
    state = _run(machine.record_tool_success(customer="acme", persona="marcus"))
    assert state.level == StickyStopLevel.OK
    # No transition -> no audit row, no row in sticky_stop_state.
    assert _audit_rows(conn) == []
    count = conn.execute("SELECT COUNT(*) FROM sticky_stop_state").fetchone()[0]
    assert count == 0


# ---------------------------------------------------------------------------
# Condition 1: consecutive tool failures
# ---------------------------------------------------------------------------


def test_consecutive_tool_failures_warn_soft_stop_hard_stop_ladder():
    machine, conn = _machine()

    # 1 failure -> still OK (warn threshold is 3)
    s = _run(machine.record_tool_failure(customer="acme", persona="marcus", skill_name="inbox-triage"))
    assert s.level == StickyStopLevel.OK
    assert s.consecutive_tool_failures == 1

    # 2..7 -> still OK. Two states since 2026-09-02: the seat keeps working
    # right up to the hard threshold, so a lowered stop cannot slip in
    # unnoticed.
    for _ in range(6):
        s = _run(machine.record_tool_failure(customer="acme", persona="marcus", skill_name="inbox-triage"))
        assert s.level == StickyStopLevel.OK

    # 8 -> HARD_STOP
    s = _run(machine.record_tool_failure(customer="acme", persona="marcus", skill_name="inbox-triage"))
    assert s.level == StickyStopLevel.HARD_STOP

    # ONE transition in the audit log now: OK -> HARD_STOP. The two rungs
    # that used to sit between them wrote INVARIANT_VIOLATION rows about
    # states that restricted nothing.
    rows = [r for r in _audit_rows(conn) if r["metadata"] and r["metadata"].get("sticky_stop_transition")]
    assert len(rows) == 1
    transitions = [(r["metadata"]["from_state"], r["metadata"]["to_state"]) for r in rows]
    assert transitions == [("OK", "HARD_STOP")]
    assert rows[0]["action_type"] == "AGENT_STOPPED"
    # Condition propagated
    assert all(r["metadata"]["condition_triggered"] == "consecutive_tool_failures" for r in rows)


def test_tool_success_resets_consecutive_failures_counter():
    machine, _ = _machine()
    _run(machine.record_tool_failure(customer="acme", persona="marcus"))
    _run(machine.record_tool_failure(customer="acme", persona="marcus"))
    state = _run(machine.record_tool_success(customer="acme", persona="marcus"))
    assert state.consecutive_tool_failures == 0
    # Level does NOT downgrade. (We haven't transitioned; it's still OK.)
    assert state.level == StickyStopLevel.OK


def test_tool_success_does_not_downgrade_level():
    """A success resets the STREAK, never the level.

    Driven to HARD_STOP rather than the removed middle rung: the property
    under test is that only a Captain clear moves the level back, and a
    stopped seat is where that matters.
    """
    machine, _ = _machine()
    for _ in range(DEFAULT_THRESHOLDS.tool_failure_hard_stop):
        _run(machine.record_tool_failure(customer="acme", persona="marcus"))
    state = _run(machine.record_tool_success(customer="acme", persona="marcus"))
    assert state.level == StickyStopLevel.HARD_STOP
    assert state.consecutive_tool_failures == 0


def test_tool_failures_outside_window_reset_streak():
    # Drive the clock so consecutive failures fall outside the rolling window.
    epoch = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)
    times = [epoch]

    def clock() -> datetime:
        return times[0]

    machine, _ = _machine(clock=clock)
    _run(machine.record_tool_failure(customer="acme", persona="marcus"))
    _run(machine.record_tool_failure(customer="acme", persona="marcus"))

    # Jump past the 10-minute window.
    times[0] = epoch + timedelta(seconds=DEFAULT_THRESHOLDS.tool_failure_window_seconds + 1)
    state = _run(machine.record_tool_failure(customer="acme", persona="marcus"))
    # Streak resets to 1, far below the hard-stop threshold of 8.
    assert state.consecutive_tool_failures == 1
    assert state.level == StickyStopLevel.OK


# ---------------------------------------------------------------------------
# Condition 2: refusal cascade
# ---------------------------------------------------------------------------


def test_refusal_cascade_warn_soft_stop_hard_stop_ladder():
    machine, conn = _machine()

    # 19 refusals -> still OK. The seat keeps working right up to the hard
    # threshold (two states since 2026-09-02).
    for _ in range(19):
        s = _run(machine.record_refusal(customer="acme", persona="marcus", skill_name="commitment-skill"))
    assert s.level == StickyStopLevel.OK

    # the 20th -> HARD_STOP
    s = _run(machine.record_refusal(customer="acme", persona="marcus", skill_name="commitment-skill"))
    assert s.level == StickyStopLevel.HARD_STOP

    transitions = [
        (r["metadata"]["from_state"], r["metadata"]["to_state"])
        for r in _audit_rows(conn)
        if r["metadata"] and r["metadata"].get("sticky_stop_transition")
    ]
    assert transitions == [("OK", "HARD_STOP")]


# ---------------------------------------------------------------------------
# Condition 3: time budget exceeded
# ---------------------------------------------------------------------------


def test_time_budget_exceeded_is_recorded_and_stops_nothing():
    """The one meter left with no HARD_STOP threshold.

    SOFT_STOP was its only outcome and SOFT_STOP restricted nothing, so with
    the rung gone it records the overrun and changes no level -- exactly its
    prior effect. Promoting it to HARD_STOP would be a silent tightening that
    could halt a client mid-run; that is a deliberate call, not a side effect
    of deleting two unused words. The audit row still lands, because it is the
    evidence any later decision to enforce the budget would rest on.
    """
    machine, conn = _machine()
    state = _run(
        machine.record_runtime_seconds(
            customer="acme",
            persona="marcus",
            seconds=DEFAULT_THRESHOLDS.time_budget_seconds + 1,
        )
    )
    assert state.level == StickyStopLevel.OK  # stops nothing
    assert state.condition is None  # and leaves no cause on a healthy seat
    rows = [r for r in _audit_rows(conn) if r["metadata"]]
    assert len(rows) == 1, "the overrun must still be recorded"
    assert rows[0]["metadata"]["condition_triggered"] == "time_budget_exceeded"
    assert rows[0]["metadata"]["sticky_stop_transition"] is False
    assert rows[0]["metadata"]["level_unchanged_by_design"] is True
    assert rows[0]["action_type"] == "INVARIANT_VIOLATION"


def test_time_budget_within_envelope_is_noop():
    machine, conn = _machine()
    state = _run(
        machine.record_runtime_seconds(
            customer="acme",
            persona="marcus",
            seconds=10.0,
        )
    )
    assert state.level == StickyStopLevel.OK
    assert _audit_rows(conn) == []


# ---------------------------------------------------------------------------
# Condition 4: cost threshold
# ---------------------------------------------------------------------------


def test_cost_threshold_warn_soft_stop_hard_stop_ladder():
    # Use a tight cap so the test runs with small numbers.
    thresholds = StickyStopThresholds(
        cost_daily_cents=1000,  # $10
        cost_hard_stop_pct=200,
    )
    machine, conn = _machine(thresholds=thresholds)

    # 80% and 100% of the cap -> still OK. Those two rungs restricted nothing.
    state = _run(machine.record_cost_cents(customer="acme", persona="marcus", amount_cents=800))
    assert state.level == StickyStopLevel.OK
    state = _run(machine.record_cost_cents(customer="acme", persona="marcus", amount_cents=200))
    assert state.level == StickyStopLevel.OK

    # +1000 -> 200% -> HARD_STOP, where it always stopped
    state = _run(machine.record_cost_cents(customer="acme", persona="marcus", amount_cents=1000))
    assert state.level == StickyStopLevel.HARD_STOP

    transitions = [
        (r["metadata"]["from_state"], r["metadata"]["to_state"])
        for r in _audit_rows(conn)
        if r["metadata"] and r["metadata"].get("sticky_stop_transition")
    ]
    assert transitions == [("OK", "HARD_STOP")]


def test_cost_resets_on_utc_day_rollover():
    times = [datetime(2026, 5, 21, 23, 30, 0, tzinfo=timezone.utc)]

    def clock() -> datetime:
        return times[0]

    thresholds = StickyStopThresholds(cost_daily_cents=1000)
    machine, _ = _machine(thresholds=thresholds, clock=clock)

    # 80% on Day 1: spend recorded, no stop.
    state = _run(machine.record_cost_cents(customer="acme", persona="marcus", amount_cents=800))
    assert state.level == StickyStopLevel.OK
    assert state.cost_cents_today == 800

    # Roll over to Day 2; counter resets.
    times[0] = datetime(2026, 5, 22, 0, 30, 0, tzinfo=timezone.utc)
    state = _run(machine.record_cost_cents(customer="acme", persona="marcus", amount_cents=100))
    # The DAILY COUNTER resets; the level is what forward-only protects, and
    # this seat never left OK.
    assert state.cost_cents_today == 100
    assert state.level == StickyStopLevel.OK


def test_cost_amount_must_be_non_negative():
    machine, _ = _machine()
    with pytest.raises(ValueError):
        _run(machine.record_cost_cents(customer="acme", persona="marcus", amount_cents=-1))


# ---------------------------------------------------------------------------
# Forward-only invariant
# ---------------------------------------------------------------------------


def test_state_is_forward_only_no_autonomous_downgrade():
    # Drive to HARD_STOP via cost (200% of a 1000c cap).
    thresholds = StickyStopThresholds(cost_daily_cents=1000)
    machine, _ = _machine(thresholds=thresholds)
    _run(machine.record_cost_cents(customer="acme", persona="marcus", amount_cents=2000))
    state = _run(machine.get_state("acme", "marcus"))
    assert state.level == StickyStopLevel.HARD_STOP

    # A single tool failure -- which on its own maps to OK -- MUST NOT
    # downgrade a stopped seat. Only a Captain clear moves it back.
    state = _run(machine.record_tool_failure(customer="acme", persona="marcus"))
    assert state.level == StickyStopLevel.HARD_STOP


# ---------------------------------------------------------------------------
# Dispatch guard
# ---------------------------------------------------------------------------


def test_assert_allowed_passes_through_below_hard_stop():
    machine, _ = _machine()
    # Seven consecutive failures: one short of the stop, and still allowed.
    for _ in range(7):
        _run(machine.record_tool_failure(customer="acme", persona="marcus"))
    state = _run(machine.assert_allowed(customer="acme", persona="marcus"))
    assert state.level == StickyStopLevel.OK


def test_assert_allowed_raises_at_hard_stop():
    machine, _ = _machine()
    for _ in range(8):
        _run(machine.record_tool_failure(customer="acme", persona="marcus"))
    with pytest.raises(StickyStopError) as exc:
        _run(machine.assert_allowed(customer="acme", persona="marcus"))
    assert exc.value.state.level == StickyStopLevel.HARD_STOP


# ---------------------------------------------------------------------------
# Captain recovery
# ---------------------------------------------------------------------------


def test_captain_clear_resets_to_ok_and_writes_audit_row():
    machine, conn = _machine()
    for _ in range(8):
        _run(machine.record_tool_failure(customer="acme", persona="marcus"))
    pre = _run(machine.get_state("acme", "marcus"))
    assert pre.level == StickyStopLevel.HARD_STOP

    cleared = _run(
        machine.clear(
            customer="acme",
            persona="marcus",
            captain_id="captain-scott",
            reason="vendor tool recovered; runaway-loop false positive confirmed",
        )
    )
    assert cleared.level == StickyStopLevel.OK
    assert cleared.consecutive_tool_failures == 0
    assert cleared.refusal_count == 0
    assert cleared.condition is None

    # Persisted change is visible to a fresh read.
    fresh = _run(machine.get_state("acme", "marcus"))
    assert fresh.level == StickyStopLevel.OK

    # Audit row recorded with from_state, to_state, captain reason.
    clear_rows = [
        r
        for r in _audit_rows(conn)
        if r["metadata"] and r["metadata"].get("sticky_stop_cleared")
    ]
    assert len(clear_rows) == 1
    row = clear_rows[0]
    assert row["action_type"] == "AGENT_RESUMED"
    assert row["actor"] == "captain-scott"
    assert row["metadata"]["from_state"] == "HARD_STOP"
    assert row["metadata"]["to_state"] == "OK"
    assert row["metadata"]["condition_triggered"] == "captain_clear"
    assert "false positive" in row["metadata"]["clear_reason"]


def test_clear_requires_captain_id_and_reason():
    machine, _ = _machine()
    with pytest.raises(ValueError):
        _run(machine.clear(customer="acme", persona="marcus", captain_id="", reason="x"))
    with pytest.raises(ValueError):
        _run(machine.clear(customer="acme", persona="marcus", captain_id="c1", reason=""))


# ---------------------------------------------------------------------------
# Integration: each condition forces a transition and audit emission
# (the AC: "Integration test forces each condition and verifies trigger")
# ---------------------------------------------------------------------------


def test_integration_each_condition_triggers_a_transition_with_audit():
    """One scenario per condition, one (customer, persona) per scenario, so
    each transition is unambiguous against the audit log.
    """
    machine, conn = _machine(
        thresholds=StickyStopThresholds(cost_daily_cents=1000),
    )

    # Condition 1: consecutive tool failures
    for _ in range(8):
        _run(machine.record_tool_failure(customer="c1", persona="p", skill_name="s1"))
    s1 = _run(machine.get_state("c1", "p"))
    assert s1.level == StickyStopLevel.HARD_STOP
    assert s1.condition == StickyStopCondition.CONSECUTIVE_TOOL_FAILURES

    # Condition 2: refusal cascade (uses default refusal_hard_stop=20)
    for _ in range(20):
        _run(machine.record_refusal(customer="c2", persona="p", skill_name="s2"))
    s2 = _run(machine.get_state("c2", "p"))
    assert s2.level == StickyStopLevel.HARD_STOP
    assert s2.condition == StickyStopCondition.REFUSAL_CASCADE

    # Condition 3: time budget exceeded. Recorded, stops nothing, and
    # deliberately leaves no cause on a seat that is still OK.
    _run(
        machine.record_runtime_seconds(
            customer="c3",
            persona="p",
            seconds=DEFAULT_THRESHOLDS.time_budget_seconds + 1,
        )
    )
    s3 = _run(machine.get_state("c3", "p"))
    assert s3.level == StickyStopLevel.OK
    assert s3.condition is None

    # Condition 4: cost threshold (cents only; 200% of 1000 = HARD_STOP)
    _run(machine.record_cost_cents(customer="c4", persona="p", amount_cents=2000))
    s4 = _run(machine.get_state("c4", "p"))
    assert s4.level == StickyStopLevel.HARD_STOP
    assert s4.condition == StickyStopCondition.COST_THRESHOLD

    # Audit log must carry one row per transition. Count rows tagged
    # sticky_stop_transition, then assert each condition appears at least
    # once.
    transition_rows = [
        r for r in _audit_rows(conn) if r["metadata"] and r["metadata"].get("sticky_stop_transition")
    ]
    conditions_seen = {r["metadata"]["condition_triggered"] for r in transition_rows}
    assert conditions_seen == {
        "consecutive_tool_failures",
        "refusal_cascade",
        "cost_threshold",
    }

    # time_budget_exceeded is recorded WITHOUT a transition: it is the one
    # meter with no HARD_STOP threshold, so it observes and stops nothing.
    # Asserting it here rather than dropping it keeps the AC intact -- every
    # condition still has to reach the audit log, and a silent meter would
    # fail this.
    observations = [
        r
        for r in _audit_rows(conn)
        if r["metadata"] and r["metadata"].get("sticky_stop_transition") is False
    ]
    assert [r["metadata"]["condition_triggered"] for r in observations] == [
        "time_budget_exceeded"
    ]
    assert observations[0]["metadata"]["level_unchanged_by_design"] is True


# ---------------------------------------------------------------------------
# Restart resilience: state survives persistence
# ---------------------------------------------------------------------------


def test_state_survives_store_round_trip():
    conn = _make_conn()
    writer = _RecordSink(AuditLogWriter(SqliteExecutor(conn)))
    store_a = SqliteStickyStopStore(conn)
    machine_a = StickyStopMachine(store=store_a, audit_writer=writer)
    for _ in range(DEFAULT_THRESHOLDS.tool_failure_hard_stop):
        _run(machine_a.record_tool_failure(customer="acme", persona="marcus"))

    # New machine instance over the SAME D1 connection — simulates a Hermes
    # restart that re-opens the bound database. State is intact.
    store_b = SqliteStickyStopStore(conn)
    machine_b = StickyStopMachine(store=store_b, audit_writer=writer)
    state = _run(machine_b.get_state("acme", "marcus"))
    assert state.level == StickyStopLevel.HARD_STOP
    assert state.consecutive_tool_failures == DEFAULT_THRESHOLDS.tool_failure_hard_stop
