"""Tests for operator/safety-substrate/trust_ceiling_log.py (issue #864).

Covers the audit row contract for every (Decision, DecisionReason) combo
plus the validation surface (closed enums, missing required fields,
canonical-key collision detection).

Tests use the same sqlite-in-memory pattern as test_audit_log.py and
test_sticky_stop.py: the audit_log schema is mirrored in-process so the
test does not shell out to wrangler.

Run from the repo root:

    cd operator && python -m pytest safety-substrate/tests/test_trust_ceiling_log.py -v
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
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
from trust_ceiling_log import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    ActionClassName,
    CeilingLevel,
    Decision,
    DecisionReason,
    log_decision,
)


# ---------------------------------------------------------------------------
# Fixtures + helpers
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
        (id_, action_type, actor, actor_role, skill_name, matter_ref, trust_ceiling, metadata) = row
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


# ---------------------------------------------------------------------------
# Smoke: one happy-path row exercises every canonical field
# ---------------------------------------------------------------------------


def test_log_decision_writes_one_row_with_all_canonical_fields():
    writer, conn = _writer()
    audit_id = _run(
        log_decision(
            writer,
            customer="acme",
            skill="inbox-triage",
            action_class=ActionClassName.EXTERNAL_SEND,
            ceiling_level=CeilingLevel.DRAFT_FOR_REVIEW,
            decision=Decision.DRAFT,
            reason=DecisionReason.EXTERNAL_SEND_DRAFT_ROUTE,
            skill_version="2.1.0",
            trace_id="trace-01HXYZ",
            matter_ref="matter-abc-123",
        )
    )
    rows = _rows(conn)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == audit_id
    assert row["action_type"] == "DRAFT_CREATED"
    assert row["actor"] == "agent"
    assert row["actor_role"] == "agent"
    assert row["skill_name"] == "inbox-triage"
    assert row["matter_ref"] == "matter-abc-123"
    assert row["trust_ceiling"] == "draft_for_review"
    md = row["metadata"]
    assert md["trust_ceiling_decision"] is True
    assert md["customer"] == "acme"
    assert md["skill"] == "inbox-triage"
    assert md["action_class"] == "external_send"
    assert md["ceiling_level"] == "draft_for_review"
    assert md["decision"] == "draft_for_review"
    assert md["reason"] == "external_send_draft_route"
    assert md["skill_version"] == "2.1.0"
    assert md["trace_id"] == "trace-01HXYZ"


# ---------------------------------------------------------------------------
# Decision -> action_type mapping
# ---------------------------------------------------------------------------


def test_allow_decision_maps_to_draft_created_action_type():
    writer, conn = _writer()
    _run(
        log_decision(
            writer,
            customer="acme",
            skill="status-report",
            action_class=ActionClassName.READ,
            ceiling_level=CeilingLevel.AUTONOMOUS,
            decision=Decision.ALLOW,
            reason=DecisionReason.READ_ALLOWED,
        )
    )
    rows = _rows(conn)
    assert len(rows) == 1
    assert rows[0]["action_type"] == "DRAFT_CREATED"
    assert rows[0]["metadata"]["decision"] == "allow"
    assert rows[0]["metadata"]["reason"] == "read_allowed"


def test_draft_decision_maps_to_draft_created_action_type():
    writer, conn = _writer()
    _run(
        log_decision(
            writer,
            customer="acme",
            skill="inbox-triage",
            action_class=ActionClassName.EXTERNAL_SEND,
            ceiling_level=CeilingLevel.DRAFT_FOR_REVIEW,
            decision=Decision.DRAFT,
            reason=DecisionReason.EXTERNAL_SEND_DRAFT_ROUTE,
        )
    )
    rows = _rows(conn)
    assert len(rows) == 1
    assert rows[0]["action_type"] == "DRAFT_CREATED"
    assert rows[0]["metadata"]["decision"] == "draft_for_review"


def test_refuse_decision_maps_to_invariant_violation_action_type():
    writer, conn = _writer()
    _run(
        log_decision(
            writer,
            customer="acme",
            skill="settlement-negotiation",
            action_class=ActionClassName.COMMITMENT,
            ceiling_level=CeilingLevel.AUTONOMOUS,
            decision=Decision.REFUSE,
            reason=DecisionReason.COMMITMENT_NO_APPROVAL,
        )
    )
    rows = _rows(conn)
    assert len(rows) == 1
    assert rows[0]["action_type"] == "INVARIANT_VIOLATION"
    assert rows[0]["metadata"]["decision"] == "refuse"
    assert rows[0]["metadata"]["reason"] == "commitment_no_approval"


# ---------------------------------------------------------------------------
# Every (Decision, DecisionReason) combo produces a well-shaped row
# ---------------------------------------------------------------------------


_ALLOW_REASONS = [
    DecisionReason.READ_ALLOWED,
    DecisionReason.INTERNAL_WRITE_AUTONOMOUS,
    DecisionReason.EXTERNAL_SEND_AUTONOMOUS_WITH_APPROVAL,
    DecisionReason.COMMITMENT_WITH_APPROVAL,
    DecisionReason.DESTRUCTIVE_WITH_APPROVAL,
]

_DRAFT_REASONS = [
    DecisionReason.INTERNAL_WRITE_DRAFT_ROUTE,
    DecisionReason.EXTERNAL_SEND_DRAFT_ROUTE,
    DecisionReason.COMMITMENT_REQUIRES_AUTONOMOUS,
]

_REFUSE_REASONS = [
    DecisionReason.CEILING_DISABLED,
    DecisionReason.EXTERNAL_SEND_NO_APPROVAL,
    DecisionReason.COMMITMENT_NO_APPROVAL,
    DecisionReason.DESTRUCTIVE_NO_APPROVAL,
    DecisionReason.DESTRUCTIVE_DRAFT_CEILING,
    DecisionReason.UNKNOWN_ACTION_CLASS,
]


@pytest.mark.parametrize("reason", _ALLOW_REASONS)
def test_each_allow_reason_emits_correctly(reason):
    writer, conn = _writer()
    _run(
        log_decision(
            writer,
            customer="acme",
            skill="some-skill",
            action_class=ActionClassName.READ,
            ceiling_level=CeilingLevel.AUTONOMOUS,
            decision=Decision.ALLOW,
            reason=reason,
        )
    )
    rows = _rows(conn)
    assert len(rows) == 1
    assert rows[0]["metadata"]["decision"] == "allow"
    assert rows[0]["metadata"]["reason"] == reason.value


@pytest.mark.parametrize("reason", _DRAFT_REASONS)
def test_each_draft_reason_emits_correctly(reason):
    writer, conn = _writer()
    _run(
        log_decision(
            writer,
            customer="acme",
            skill="some-skill",
            action_class=ActionClassName.EXTERNAL_SEND,
            ceiling_level=CeilingLevel.DRAFT_FOR_REVIEW,
            decision=Decision.DRAFT,
            reason=reason,
        )
    )
    rows = _rows(conn)
    assert len(rows) == 1
    assert rows[0]["metadata"]["decision"] == "draft_for_review"
    assert rows[0]["metadata"]["reason"] == reason.value
    assert rows[0]["action_type"] == "DRAFT_CREATED"


@pytest.mark.parametrize("reason", _REFUSE_REASONS)
def test_each_refuse_reason_emits_correctly(reason):
    writer, conn = _writer()
    _run(
        log_decision(
            writer,
            customer="acme",
            skill="some-skill",
            action_class=ActionClassName.DESTRUCTIVE,
            ceiling_level=CeilingLevel.DRAFT_FOR_REVIEW,
            decision=Decision.REFUSE,
            reason=reason,
        )
    )
    rows = _rows(conn)
    assert len(rows) == 1
    assert rows[0]["metadata"]["decision"] == "refuse"
    assert rows[0]["metadata"]["reason"] == reason.value
    assert rows[0]["action_type"] == "INVARIANT_VIOLATION"


# ---------------------------------------------------------------------------
# Closed-enum validation
# ---------------------------------------------------------------------------


def test_free_text_reason_rejected():
    writer, _ = _writer()
    with pytest.raises(ValueError, match="DecisionReason"):
        _run(
            log_decision(
                writer,
                customer="acme",
                skill="some-skill",
                action_class=ActionClassName.READ,
                ceiling_level=CeilingLevel.AUTONOMOUS,
                decision=Decision.ALLOW,
                reason="just because",  # type: ignore[arg-type]
            )
        )


def test_free_text_decision_rejected():
    writer, _ = _writer()
    with pytest.raises(ValueError, match="Decision"):
        _run(
            log_decision(
                writer,
                customer="acme",
                skill="some-skill",
                action_class=ActionClassName.READ,
                ceiling_level=CeilingLevel.AUTONOMOUS,
                decision="maybe",  # type: ignore[arg-type]
                reason=DecisionReason.READ_ALLOWED,
            )
        )


def test_invalid_ceiling_level_string_rejected():
    writer, _ = _writer()
    with pytest.raises(ValueError):
        _run(
            log_decision(
                writer,
                customer="acme",
                skill="some-skill",
                action_class=ActionClassName.READ,
                ceiling_level="elevated",
                decision=Decision.ALLOW,
                reason=DecisionReason.READ_ALLOWED,
            )
        )


def test_invalid_action_class_string_rejected():
    writer, _ = _writer()
    with pytest.raises(ValueError):
        _run(
            log_decision(
                writer,
                customer="acme",
                skill="some-skill",
                action_class="touch",
                ceiling_level=CeilingLevel.AUTONOMOUS,
                decision=Decision.ALLOW,
                reason=DecisionReason.READ_ALLOWED,
            )
        )


def test_missing_customer_rejected():
    writer, _ = _writer()
    with pytest.raises(ValueError, match="customer"):
        _run(
            log_decision(
                writer,
                customer="",
                skill="some-skill",
                action_class=ActionClassName.READ,
                ceiling_level=CeilingLevel.AUTONOMOUS,
                decision=Decision.ALLOW,
                reason=DecisionReason.READ_ALLOWED,
            )
        )


def test_missing_skill_rejected():
    writer, _ = _writer()
    with pytest.raises(ValueError, match="skill"):
        _run(
            log_decision(
                writer,
                customer="acme",
                skill="",
                action_class=ActionClassName.READ,
                ceiling_level=CeilingLevel.AUTONOMOUS,
                decision=Decision.ALLOW,
                reason=DecisionReason.READ_ALLOWED,
            )
        )


# ---------------------------------------------------------------------------
# String-value pass-through (dispatch path passes strings, not enums)
# ---------------------------------------------------------------------------


def test_accepts_string_ceiling_level_and_action_class():
    writer, conn = _writer()
    _run(
        log_decision(
            writer,
            customer="acme",
            skill="inbox-triage",
            action_class="external_send",
            ceiling_level="draft_for_review",
            decision=Decision.DRAFT,
            reason=DecisionReason.EXTERNAL_SEND_DRAFT_ROUTE,
        )
    )
    rows = _rows(conn)
    assert rows[0]["metadata"]["action_class"] == "external_send"
    assert rows[0]["metadata"]["ceiling_level"] == "draft_for_review"


def test_refused_ceiling_synonym_accepted():
    # `refused` (adapter convention) and `disabled` (PRD §11.1 wording)
    # are equivalent. The wrapper accepts both without translating.
    writer, conn = _writer()
    _run(
        log_decision(
            writer,
            customer="acme",
            skill="some-skill",
            action_class=ActionClassName.EXTERNAL_SEND,
            ceiling_level="refused",
            decision=Decision.REFUSE,
            reason=DecisionReason.CEILING_DISABLED,
        )
    )
    rows = _rows(conn)
    assert rows[0]["metadata"]["ceiling_level"] == "refused"
    assert rows[0]["trust_ceiling"] == "refused"


# ---------------------------------------------------------------------------
# extra_metadata behavior
# ---------------------------------------------------------------------------


def test_extra_metadata_merges_after_canonical_keys():
    writer, conn = _writer()
    _run(
        log_decision(
            writer,
            customer="acme",
            skill="inbox-triage",
            action_class=ActionClassName.EXTERNAL_SEND,
            ceiling_level=CeilingLevel.DRAFT_FOR_REVIEW,
            decision=Decision.DRAFT,
            reason=DecisionReason.EXTERNAL_SEND_DRAFT_ROUTE,
            extra_metadata={"tool_name": "gmail.send", "recipient_domain": "acme.com"},
        )
    )
    md = _rows(conn)[0]["metadata"]
    assert md["tool_name"] == "gmail.send"
    assert md["recipient_domain"] == "acme.com"
    # Canonical keys still present
    assert md["trust_ceiling_decision"] is True
    assert md["decision"] == "draft_for_review"


def test_extra_metadata_cannot_override_canonical_keys():
    writer, _ = _writer()
    with pytest.raises(ValueError, match="canonical keys"):
        _run(
            log_decision(
                writer,
                customer="acme",
                skill="inbox-triage",
                action_class=ActionClassName.READ,
                ceiling_level=CeilingLevel.AUTONOMOUS,
                decision=Decision.ALLOW,
                reason=DecisionReason.READ_ALLOWED,
                extra_metadata={"decision": "spoofed"},
            )
        )


# ---------------------------------------------------------------------------
# Optional-field handling
# ---------------------------------------------------------------------------


def test_optional_fields_default_to_none_in_metadata():
    writer, conn = _writer()
    _run(
        log_decision(
            writer,
            customer="acme",
            skill="inbox-triage",
            action_class=ActionClassName.READ,
            ceiling_level=CeilingLevel.AUTONOMOUS,
            decision=Decision.ALLOW,
            reason=DecisionReason.READ_ALLOWED,
        )
    )
    md = _rows(conn)[0]["metadata"]
    assert md["skill_version"] is None
    assert md["trace_id"] is None
    assert _rows(conn)[0]["matter_ref"] is None


# ---------------------------------------------------------------------------
# Audit-write failure propagation
# ---------------------------------------------------------------------------


class _FailingExecutor:
    """Executor that raises on every write; used to verify the wrapper
    propagates AuditWriteError to the caller per the spec invariant."""

    async def execute(self, sql, params):
        raise RuntimeError("simulated D1 outage")


def test_audit_write_failure_propagates_as_auditwriteerror():
    writer = AuditLogWriter(_FailingExecutor())
    with pytest.raises(AuditWriteError):
        _run(
            log_decision(
                writer,
                customer="acme",
                skill="inbox-triage",
                action_class=ActionClassName.READ,
                ceiling_level=CeilingLevel.AUTONOMOUS,
                decision=Decision.ALLOW,
                reason=DecisionReason.READ_ALLOWED,
            )
        )


# ---------------------------------------------------------------------------
# Dashboard-aggregation contract
# ---------------------------------------------------------------------------


def test_metadata_has_stable_keys_for_aggregation():
    """The dashboard implementer (separate issue) keys on a stable JSON
    shape. If this set ever changes, the spec and dashboard contract
    must change in lockstep."""
    writer, conn = _writer()
    _run(
        log_decision(
            writer,
            customer="acme",
            skill="inbox-triage",
            action_class=ActionClassName.EXTERNAL_SEND,
            ceiling_level=CeilingLevel.DRAFT_FOR_REVIEW,
            decision=Decision.DRAFT,
            reason=DecisionReason.EXTERNAL_SEND_DRAFT_ROUTE,
            skill_version="2.1.0",
            trace_id="trace-01HXYZ",
        )
    )
    md = _rows(conn)[0]["metadata"]
    assert set(md.keys()) == {
        "trust_ceiling_decision",
        "customer",
        "skill",
        "action_class",
        "ceiling_level",
        "decision",
        "reason",
        "skill_version",
        "trace_id",
    }


def test_filter_predicate_matches_trust_ceiling_rows_only():
    """Mixed audit rows: only the trust-ceiling-decision rows should match
    the dashboard's filter predicate (metadata.trust_ceiling_decision = true)."""
    writer, conn = _writer()
    # Emit one trust-ceiling row.
    _run(
        log_decision(
            writer,
            customer="acme",
            skill="inbox-triage",
            action_class=ActionClassName.READ,
            ceiling_level=CeilingLevel.AUTONOMOUS,
            decision=Decision.ALLOW,
            reason=DecisionReason.READ_ALLOWED,
        )
    )
    # And one unrelated audit row (mirrors a non-trust-ceiling event).
    from adapter.audit_log import AuditEvent

    _run(
        writer.write(
            AuditEvent(
                action_type="DRAFT_APPROVED",
                actor="captain-scott",
                skill_name="inbox-triage",
            )
        )
    )
    rows = _rows(conn)
    assert len(rows) == 2
    matched = [r for r in rows if r["metadata"] and r["metadata"].get("trust_ceiling_decision") is True]
    assert len(matched) == 1
    assert matched[0]["metadata"]["reason"] == "read_allowed"
