"""Tests for operator/safety-substrate/refusal.py (issue #866).

Refusal-handling runtime semantics on top of `trust_ceiling_log` (PR #953).
Verifies:

  * The canonical trust-ceiling-decision row is written exactly once
    (no duplicate audit rows for one refusal).
  * The customer-facing notification row is tagged so the in-app
    notification surface (#876 / #964) can pick it up.
  * The customer-facing message comes from a closed enum (no internal
    DecisionReason vocabulary leaks).
  * The sticky-stop refusal counter remains independent: this handler
    does not transition sticky-stop states directly.
  * The Captain-side ESCALATION_FIRED row only fires when refusals
    cascade past the configured threshold within the window.
  * The skill is told to abort (RefusalOutcome.aborted == True).
  * Audit-write failures propagate (caller-abort invariant).

Run from the repo root:

    cd operator && python -m pytest safety-substrate/tests/test_refusal.py -v
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
sys.path.insert(0, str(_HERE.parents[1]))  # operator/safety-substrate/

from adapter.audit_log import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    AuditLogWriter,
    AuditWriteError,
    SqliteExecutor,
)
from refusal import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    CustomerMessage,
    InMemoryRefusalCounter,
    RefusalHandler,
    RefusalOutcome,
)
from trust_ceiling_log import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    ActionClassName,
    CeilingLevel,
    DecisionReason,
)


# ---------------------------------------------------------------------------
# Fixtures
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


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_AUDIT_SCHEMA)
    return conn


def _writer() -> tuple[AuditLogWriter, sqlite3.Connection]:
    conn = _make_conn()
    return AuditLogWriter(SqliteExecutor(conn)), conn


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _rows(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.cursor()
    raw = cur.execute(
        "SELECT id, action_type, actor, actor_role, skill_name, matter_ref, "
        "trust_ceiling, metadata FROM audit_log ORDER BY rowid"
    ).fetchall()
    out = []
    for row in raw:
        (
            id_,
            action_type,
            actor,
            actor_role,
            skill_name,
            matter_ref,
            trust_ceiling,
            metadata,
        ) = row
        out.append(
            {
                "id": id_,
                "action_type": action_type,
                "actor": actor,
                "actor_role": actor_role,
                "skill_name": skill_name,
                "matter_ref": matter_ref,
                "trust_ceiling": trust_ceiling,
                "metadata": json.loads(metadata) if metadata else None,
            }
        )
    return out


def _decision_rows(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["metadata"] and r["metadata"].get("trust_ceiling_decision") is True]


def _notification_rows(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["metadata"] and r["metadata"].get("refusal_notification") is True]


def _captain_alert_rows(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["metadata"] and r["metadata"].get("refusal_cascade_alert") is True]


def _handler(
    writer: AuditLogWriter,
    *,
    threshold: int = 5,
    window_seconds: int = 3600,
    clock=None,
) -> RefusalHandler:
    counter = InMemoryRefusalCounter(window_seconds=window_seconds)
    return RefusalHandler(
        audit_writer=writer,
        counter=counter,
        cascade_threshold=threshold,
        cascade_window_seconds=window_seconds,
        clock=clock,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_handle_writes_decision_row_and_notification_row_and_aborts():
    writer, conn = _writer()
    handler = _handler(writer)
    outcome = _run(
        handler.handle(
            customer="acme",
            skill="settlement-negotiation",
            action_class=ActionClassName.COMMITMENT,
            ceiling_level=CeilingLevel.AUTONOMOUS,
            reason=DecisionReason.COMMITMENT_NO_APPROVAL,
            skill_version="2.1.0",
            trace_id="trace-01HXYZ",
            matter_ref="matter-abc-123",
        )
    )
    assert isinstance(outcome, RefusalOutcome)
    assert outcome.aborted is True
    assert outcome.message == CustomerMessage.APPROVAL_REQUIRED_COMMITMENT
    assert outcome.captain_alert_audit_id is None
    assert outcome.recent_refusal_count == 1

    rows = _rows(conn)
    assert len(rows) == 2  # one decision row + one notification row
    decision_rows = _decision_rows(rows)
    notif_rows = _notification_rows(rows)
    captain_rows = _captain_alert_rows(rows)
    assert len(decision_rows) == 1
    assert len(notif_rows) == 1
    assert len(captain_rows) == 0

    # Decision row carries the canonical trust-ceiling-decision shape.
    drow = decision_rows[0]
    assert drow["action_type"] == "INVARIANT_VIOLATION"
    assert drow["metadata"]["decision"] == "refuse"
    assert drow["metadata"]["reason"] == "commitment_no_approval"
    assert drow["id"] == outcome.decision_audit_id

    # Notification row carries notification_eligible plus the customer-
    # facing message text. It links back to the decision row.
    nrow = notif_rows[0]
    assert nrow["action_type"] == "DRAFT_REJECTED"
    assert nrow["metadata"]["notification_eligible"] is True
    assert nrow["metadata"]["refusal_notification"] is True
    assert nrow["metadata"]["customer_message"] == CustomerMessage.APPROVAL_REQUIRED_COMMITMENT.value
    assert nrow["metadata"]["decision_audit_id"] == outcome.decision_audit_id
    assert nrow["metadata"]["trace_id"] == "trace-01HXYZ"
    assert nrow["metadata"]["skill_version"] == "2.1.0"
    assert nrow["id"] == outcome.notification_audit_id


# ---------------------------------------------------------------------------
# No duplicate audit rows
# ---------------------------------------------------------------------------


def test_one_refusal_emits_exactly_one_trust_ceiling_decision_row():
    """The canonical audit row written by log_decision() is NOT
    duplicated by the refusal handler. Exactly one row with
    `metadata.trust_ceiling_decision == true` per refusal."""
    writer, conn = _writer()
    handler = _handler(writer)
    _run(
        handler.handle(
            customer="acme",
            skill="some-skill",
            action_class=ActionClassName.EXTERNAL_SEND,
            ceiling_level=CeilingLevel.AUTONOMOUS,
            reason=DecisionReason.EXTERNAL_SEND_NO_APPROVAL,
        )
    )
    rows = _rows(conn)
    assert len(_decision_rows(rows)) == 1


# ---------------------------------------------------------------------------
# Customer-facing message vocabulary mapping (closed enum, per refuse-side
# DecisionReason)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason,expected_message",
    [
        (DecisionReason.CEILING_DISABLED, CustomerMessage.SKILL_DISABLED),
        (
            DecisionReason.EXTERNAL_SEND_NO_APPROVAL,
            CustomerMessage.APPROVAL_REQUIRED_SEND,
        ),
        (
            DecisionReason.COMMITMENT_NO_APPROVAL,
            CustomerMessage.APPROVAL_REQUIRED_COMMITMENT,
        ),
        (
            DecisionReason.DESTRUCTIVE_NO_APPROVAL,
            CustomerMessage.APPROVAL_REQUIRED_DESTRUCTIVE,
        ),
        (
            DecisionReason.DESTRUCTIVE_DRAFT_CEILING,
            CustomerMessage.DESTRUCTIVE_BLOCKED_AT_DRAFT_CEILING,
        ),
        (DecisionReason.UNKNOWN_ACTION_CLASS, CustomerMessage.UNKNOWN_ACTION),
    ],
)
def test_each_refuse_reason_maps_to_expected_customer_message(reason, expected_message):
    writer, conn = _writer()
    handler = _handler(writer)
    outcome = _run(
        handler.handle(
            customer="acme",
            skill="some-skill",
            action_class=ActionClassName.DESTRUCTIVE,
            ceiling_level=CeilingLevel.DRAFT_FOR_REVIEW,
            reason=reason,
        )
    )
    assert outcome.message == expected_message
    nrow = _notification_rows(_rows(conn))[0]
    assert nrow["metadata"]["customer_message"] == expected_message.value


def test_customer_message_does_not_leak_internal_reason_vocabulary():
    """The customer message is one of CustomerMessage; it must NOT be
    the raw DecisionReason value (substrate-internal vocabulary)."""
    writer, conn = _writer()
    handler = _handler(writer)
    _run(
        handler.handle(
            customer="acme",
            skill="some-skill",
            action_class=ActionClassName.COMMITMENT,
            ceiling_level=CeilingLevel.AUTONOMOUS,
            reason=DecisionReason.COMMITMENT_NO_APPROVAL,
        )
    )
    nrow = _notification_rows(_rows(conn))[0]
    customer_message = nrow["metadata"]["customer_message"]
    # The message must be a CustomerMessage value, not a DecisionReason value.
    customer_message_values = {m.value for m in CustomerMessage}
    decision_reason_values = {r.value for r in DecisionReason}
    assert customer_message in customer_message_values
    assert customer_message not in decision_reason_values


# ---------------------------------------------------------------------------
# Free-text reason rejected
# ---------------------------------------------------------------------------


def test_free_text_reason_rejected():
    writer, _ = _writer()
    handler = _handler(writer)
    with pytest.raises(ValueError, match="DecisionReason"):
        _run(
            handler.handle(
                customer="acme",
                skill="some-skill",
                action_class=ActionClassName.READ,
                ceiling_level=CeilingLevel.AUTONOMOUS,
                reason="i felt like it",  # type: ignore[arg-type]
            )
        )


# ---------------------------------------------------------------------------
# Captain alert: pattern-based, not always
# ---------------------------------------------------------------------------


def test_single_refusal_does_not_emit_captain_alert():
    writer, conn = _writer()
    handler = _handler(writer, threshold=5)
    outcome = _run(
        handler.handle(
            customer="acme",
            skill="some-skill",
            action_class=ActionClassName.EXTERNAL_SEND,
            ceiling_level=CeilingLevel.AUTONOMOUS,
            reason=DecisionReason.EXTERNAL_SEND_NO_APPROVAL,
        )
    )
    assert outcome.captain_alert_audit_id is None
    assert _captain_alert_rows(_rows(conn)) == []


def test_refusal_cascade_fires_captain_alert_at_threshold():
    writer, conn = _writer()
    handler = _handler(writer, threshold=5)
    last_outcome: RefusalOutcome | None = None
    for _ in range(5):
        last_outcome = _run(
            handler.handle(
                customer="acme",
                skill="same-skill",
                action_class=ActionClassName.EXTERNAL_SEND,
                ceiling_level=CeilingLevel.AUTONOMOUS,
                reason=DecisionReason.EXTERNAL_SEND_NO_APPROVAL,
            )
        )
    assert last_outcome is not None
    assert last_outcome.recent_refusal_count == 5
    assert last_outcome.captain_alert_audit_id is not None

    captain_rows = _captain_alert_rows(_rows(conn))
    # The first FOUR refusals were under threshold; only the 5th fires.
    # Each subsequent one also fires while the window count stays >= 5.
    # Total Captain alert rows = 1 (the 5th refusal only).
    assert len(captain_rows) == 1
    crow = captain_rows[0]
    assert crow["action_type"] == "ESCALATION_FIRED"
    assert crow["metadata"]["refusal_cascade_alert"] is True
    assert crow["metadata"]["recent_refusal_count"] == 5
    assert crow["metadata"]["cascade_threshold"] == 5
    assert crow["metadata"]["customer"] == "acme"
    assert crow["metadata"]["skill"] == "same-skill"


def test_refusal_cascade_window_drops_old_events():
    """Events outside the window should not contribute to the count."""
    fake_now = {"t": datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)}

    def clock():
        return fake_now["t"]

    writer, conn = _writer()
    handler = _handler(writer, threshold=3, window_seconds=600, clock=clock)

    # Two refusals early.
    for _ in range(2):
        _run(
            handler.handle(
                customer="acme",
                skill="s",
                action_class=ActionClassName.EXTERNAL_SEND,
                ceiling_level=CeilingLevel.AUTONOMOUS,
                reason=DecisionReason.EXTERNAL_SEND_NO_APPROVAL,
            )
        )
    # Advance past the 600s window.
    fake_now["t"] = fake_now["t"] + timedelta(seconds=900)
    outcome = _run(
        handler.handle(
            customer="acme",
            skill="s",
            action_class=ActionClassName.EXTERNAL_SEND,
            ceiling_level=CeilingLevel.AUTONOMOUS,
            reason=DecisionReason.EXTERNAL_SEND_NO_APPROVAL,
        )
    )
    # The two old events expired. Only this event counts.
    assert outcome.recent_refusal_count == 1
    assert outcome.captain_alert_audit_id is None
    assert _captain_alert_rows(_rows(conn)) == []


def test_cascade_threshold_is_per_skill():
    """Refusals on different skills do not aggregate into one cascade."""
    writer, conn = _writer()
    handler = _handler(writer, threshold=3, window_seconds=3600)
    for _ in range(2):
        _run(
            handler.handle(
                customer="acme",
                skill="skill-A",
                action_class=ActionClassName.EXTERNAL_SEND,
                ceiling_level=CeilingLevel.AUTONOMOUS,
                reason=DecisionReason.EXTERNAL_SEND_NO_APPROVAL,
            )
        )
    for _ in range(2):
        _run(
            handler.handle(
                customer="acme",
                skill="skill-B",
                action_class=ActionClassName.EXTERNAL_SEND,
                ceiling_level=CeilingLevel.AUTONOMOUS,
                reason=DecisionReason.EXTERNAL_SEND_NO_APPROVAL,
            )
        )
    # 2 refusals per skill; threshold is 3; no cascade fires.
    assert _captain_alert_rows(_rows(conn)) == []


def test_cascade_threshold_validation():
    writer, _ = _writer()
    with pytest.raises(ValueError, match="cascade_threshold"):
        RefusalHandler(
            audit_writer=writer,
            cascade_threshold=0,
        )
    with pytest.raises(ValueError, match="cascade_window_seconds"):
        RefusalHandler(
            audit_writer=writer,
            cascade_window_seconds=0,
        )


# ---------------------------------------------------------------------------
# Refusal vs sticky-stop: this module does NOT directly transition states
# ---------------------------------------------------------------------------


def test_refusal_handler_does_not_emit_sticky_stop_rows():
    """sticky-stop state transitions write rows tagged
    `metadata.sticky_stop_transition == true`. The refusal handler must
    NOT write any sticky-stop transition rows on its own; that is the
    sticky-stop state machine's job. The refusal handler may TICK the
    refusal counter (callers wire that in), but the transition decision
    belongs to sticky-stop."""
    writer, conn = _writer()
    handler = _handler(writer)
    # Fire enough refusals to drive a hypothetical sticky-stop transition
    # if the handler were doing so directly.
    for _ in range(10):
        _run(
            handler.handle(
                customer="acme",
                skill="same-skill",
                action_class=ActionClassName.EXTERNAL_SEND,
                ceiling_level=CeilingLevel.AUTONOMOUS,
                reason=DecisionReason.EXTERNAL_SEND_NO_APPROVAL,
            )
        )
    rows = _rows(conn)
    sticky_rows = [r for r in rows if r["metadata"] and r["metadata"].get("sticky_stop_transition") is True]
    assert sticky_rows == []


# ---------------------------------------------------------------------------
# Skill MUST NOT execute the refused action
# ---------------------------------------------------------------------------


def test_outcome_aborted_is_always_true():
    """The dispatch path consults RefusalOutcome.aborted before invoking
    any tool. It is True for every refuse-side reason."""
    writer, _ = _writer()
    handler = _handler(writer)
    for reason in [
        DecisionReason.CEILING_DISABLED,
        DecisionReason.EXTERNAL_SEND_NO_APPROVAL,
        DecisionReason.COMMITMENT_NO_APPROVAL,
        DecisionReason.DESTRUCTIVE_NO_APPROVAL,
        DecisionReason.DESTRUCTIVE_DRAFT_CEILING,
        DecisionReason.UNKNOWN_ACTION_CLASS,
    ]:
        outcome = _run(
            handler.handle(
                customer="acme",
                skill="some-skill",
                action_class=ActionClassName.DESTRUCTIVE,
                ceiling_level=CeilingLevel.DRAFT_FOR_REVIEW,
                reason=reason,
            )
        )
        assert outcome.aborted is True


# ---------------------------------------------------------------------------
# Audit-write failure propagation (caller-abort invariant)
# ---------------------------------------------------------------------------


class _FailingExecutor:
    """Executor that raises on every write."""

    async def execute(self, sql, params):
        raise RuntimeError("simulated D1 outage")


def test_audit_write_failure_propagates():
    writer = AuditLogWriter(_FailingExecutor())
    handler = _handler(writer)
    with pytest.raises(AuditWriteError):
        _run(
            handler.handle(
                customer="acme",
                skill="some-skill",
                action_class=ActionClassName.EXTERNAL_SEND,
                ceiling_level=CeilingLevel.AUTONOMOUS,
                reason=DecisionReason.EXTERNAL_SEND_NO_APPROVAL,
            )
        )


# ---------------------------------------------------------------------------
# String-valued action_class / ceiling_level pass-through (dispatch path
# naturally passes strings, not enums; same pattern as log_decision()).
# ---------------------------------------------------------------------------


def test_string_action_class_and_ceiling_level_accepted():
    writer, conn = _writer()
    handler = _handler(writer)
    outcome = _run(
        handler.handle(
            customer="acme",
            skill="some-skill",
            action_class="external_send",
            ceiling_level="autonomous",
            reason=DecisionReason.EXTERNAL_SEND_NO_APPROVAL,
        )
    )
    assert outcome.aborted is True
    nrow = _notification_rows(_rows(conn))[0]
    assert nrow["metadata"]["action_class"] == "external_send"
    assert nrow["metadata"]["ceiling_level"] == "autonomous"


# ---------------------------------------------------------------------------
# trace_id propagates from decision row to notification row to captain alert
# row (for downstream cross-row correlation)
# ---------------------------------------------------------------------------


def test_trace_id_propagates_to_all_emitted_rows():
    writer, conn = _writer()
    handler = _handler(writer, threshold=1)
    outcome = _run(
        handler.handle(
            customer="acme",
            skill="some-skill",
            action_class=ActionClassName.EXTERNAL_SEND,
            ceiling_level=CeilingLevel.AUTONOMOUS,
            reason=DecisionReason.EXTERNAL_SEND_NO_APPROVAL,
            trace_id="trace-xyz",
        )
    )
    assert outcome.captain_alert_audit_id is not None
    rows = _rows(conn)
    drow = _decision_rows(rows)[0]
    nrow = _notification_rows(rows)[0]
    crow = _captain_alert_rows(rows)[0]
    assert drow["metadata"]["trace_id"] == "trace-xyz"
    assert nrow["metadata"]["trace_id"] == "trace-xyz"
    assert crow["metadata"]["trace_id"] == "trace-xyz"


# ---------------------------------------------------------------------------
# InMemoryRefusalCounter behavior in isolation
# ---------------------------------------------------------------------------


def test_in_memory_counter_drops_events_outside_window():
    counter = InMemoryRefusalCounter(window_seconds=60)
    t0 = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)
    assert counter.record_and_count(customer="c", skill="s", now=t0) == 1
    assert counter.record_and_count(customer="c", skill="s", now=t0 + timedelta(seconds=30)) == 2
    # Advance outside the window: only the most recent event remains.
    assert counter.record_and_count(customer="c", skill="s", now=t0 + timedelta(seconds=120)) == 1


def test_in_memory_counter_partitions_by_customer_and_skill():
    counter = InMemoryRefusalCounter(window_seconds=3600)
    t0 = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)
    counter.record_and_count(customer="acme", skill="s1", now=t0)
    counter.record_and_count(customer="acme", skill="s2", now=t0)
    counter.record_and_count(customer="other", skill="s1", now=t0)
    # Each combination tracked independently.
    assert counter.record_and_count(customer="acme", skill="s1", now=t0) == 2
    assert counter.record_and_count(customer="acme", skill="s2", now=t0) == 2
    assert counter.record_and_count(customer="other", skill="s1", now=t0) == 2
