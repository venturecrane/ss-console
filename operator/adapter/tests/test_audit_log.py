"""Tests for operator/adapter/audit_log.py (issue #891).

Exercises the audit log writer against a sqlite-backed executor that
mirrors the audit_log schema from operator/migrations/0001_per_customer_schema.sql.

Coverage:
  - ULID generation: 26 chars, monotonic, Crockford alphabet
  - ISO 8601 UTC timestamp with millisecond precision
  - Synchronous write path: returns only after commit
  - Schema fidelity: every column populated correctly
  - Action type validation: ValueError on unknown types
  - Digests: SHA-256 of payloads; None preserved as NULL
  - Metadata: JSON-serialized; sort_keys for determinism
  - Performance: p99 < 10ms over 200 writes against in-memory sqlite
  - Indexes: schema 0001 + 0002 indexes resolve point queries via EXPLAIN
  - Failure path: executor exception is wrapped in AuditWriteError

Run from repo root:

    cd operator && python -m pytest adapter/tests/test_audit_log.py -v
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Allow running from repo root or from operator/.
import sys

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2]))  # operator/ on sys.path

from adapter.audit_log import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    ACCEPTED_ACTION_TYPES,
    ActorRole,
    AuditEvent,
    AuditLogWriter,
    AuditWriteError,
    SqliteExecutor,
    _iso_utc,
    _sha256,
    _ulid,
)


# ---------------------------------------------------------------------------
# Schema setup — exact copy of audit_log CREATE TABLE + indexes from
# migrations 0001 and 0002. We mirror by hand so the test does not depend
# on shelling out to wrangler.
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
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
CREATE INDEX idx_audit_ts ON audit_log(ts);
CREATE INDEX idx_audit_action_type ON audit_log(action_type, ts);
CREATE INDEX idx_audit_actor ON audit_log(actor, ts);
CREATE INDEX idx_audit_ts_desc ON audit_log(ts DESC);
CREATE INDEX idx_audit_skill ON audit_log(skill_name, ts DESC);
CREATE INDEX idx_audit_action_class ON audit_log(action_type, ts DESC);
"""


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA_SQL)
    return conn


def _writer() -> tuple[AuditLogWriter, sqlite3.Connection]:
    conn = _make_conn()
    return AuditLogWriter(SqliteExecutor(conn)), conn


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# ULID + timestamp helpers
# ---------------------------------------------------------------------------


def test_ulid_is_26_chars():
    u = _ulid()
    assert len(u) == 26
    assert all(c in "0123456789ABCDEFGHJKMNPQRSTVWXYZ" for c in u)


def test_ulid_sorts_by_time():
    early = _ulid(now_ms=1_000_000_000_000)
    later = _ulid(now_ms=2_000_000_000_000)
    assert early < later


def test_ulid_unique_within_same_ms():
    ulids = {_ulid(now_ms=1_700_000_000_000) for _ in range(100)}
    # Same timestamp prefix, 80 bits of randomness — collisions vanishingly rare
    assert len(ulids) == 100


def test_iso_utc_format():
    dt = datetime(2026, 5, 21, 12, 34, 56, 789_000, tzinfo=timezone.utc)
    assert _iso_utc(dt) == "2026-05-21T12:34:56.789Z"


def test_sha256_none_passes_through():
    assert _sha256(None) is None


def test_sha256_known_value():
    # SHA-256("") = e3b0c4...
    assert _sha256(b"").startswith("e3b0c442")


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


def test_write_inserts_row_with_all_fields():
    writer, conn = _writer()
    event = AuditEvent(
        action_type="DRAFT_CREATED",
        actor="agent",
        actor_role=ActorRole.AGENT,
        skill_name="inbox-triage",
        matter_ref="matter-123",
        input_payload=b"raw email body",
        output_payload=b"draft response",
        diff_payload=None,
        trust_ceiling="draft_for_review",
        metadata={"recipient_cohort_id": "anxious-client", "priority": 5},
    )

    ulid = _run(writer.write(event))

    cur = conn.cursor()
    row = cur.execute("SELECT * FROM audit_log WHERE id = ?", (ulid,)).fetchone()
    assert row is not None
    cols = [d[0] for d in cur.description]
    rec = dict(zip(cols, row))

    assert rec["id"] == ulid
    assert rec["action_type"] == "DRAFT_CREATED"
    assert rec["actor"] == "agent"
    assert rec["actor_role"] == "agent"
    assert rec["skill_name"] == "inbox-triage"
    assert rec["matter_ref"] == "matter-123"
    assert rec["input_digest"] == _sha256(b"raw email body")
    assert rec["output_digest"] == _sha256(b"draft response")
    assert rec["diff_digest"] is None
    assert rec["trust_ceiling"] == "draft_for_review"
    parsed_meta = json.loads(rec["metadata"])
    assert parsed_meta == {"priority": 5, "recipient_cohort_id": "anxious-client"}
    # ts is ISO 8601 UTC with millis + Z
    assert rec["ts"].endswith("Z")
    assert "T" in rec["ts"]


def test_write_with_minimal_event():
    writer, conn = _writer()
    event = AuditEvent(action_type="AGENT_STOPPED", actor="captain")
    ulid = _run(writer.write(event))
    row = conn.execute("SELECT actor, skill_name, metadata FROM audit_log WHERE id=?", (ulid,)).fetchone()
    assert row == ("captain", None, None)


def test_write_rejects_unknown_action_type():
    writer, _ = _writer()
    with pytest.raises(ValueError, match="not in ACCEPTED_ACTION_TYPES"):
        _run(writer.write(AuditEvent(action_type="MADE_UP_TYPE", actor="agent")))


def test_write_wraps_executor_failure_as_audit_write_error():
    class BoomExecutor:
        async def execute(self, sql, params):
            raise RuntimeError("D1 unreachable")

    writer = AuditLogWriter(BoomExecutor())
    with pytest.raises(AuditWriteError, match="caller MUST abort"):
        _run(writer.write(AuditEvent(action_type="DRAFT_CREATED", actor="agent")))


def test_metadata_is_deterministic_json():
    writer, conn = _writer()
    # Two writes with the same metadata dict (key order shuffled) produce
    # the same serialized JSON.
    md_a = {"b": 1, "a": 2}
    md_b = {"a": 2, "b": 1}
    u1 = _run(writer.write(AuditEvent(action_type="DRAFT_CREATED", actor="agent", metadata=md_a)))
    u2 = _run(writer.write(AuditEvent(action_type="DRAFT_CREATED", actor="agent", metadata=md_b)))
    rows = conn.execute(
        "SELECT metadata FROM audit_log WHERE id IN (?, ?) ORDER BY id", (u1, u2)
    ).fetchall()
    assert rows[0][0] == rows[1][0]
    assert rows[0][0] == '{"a":2,"b":1}'


def test_actor_role_accepts_plain_string_for_forward_compat():
    writer, conn = _writer()
    ulid = _run(
        writer.write(AuditEvent(action_type="RBAC_EVENT", actor="agent", actor_role="future_role"))  # type: ignore[arg-type]
    )
    row = conn.execute("SELECT actor_role FROM audit_log WHERE id=?", (ulid,)).fetchone()
    assert row[0] == "future_role"


# ---------------------------------------------------------------------------
# Accepted action types match the spec
# ---------------------------------------------------------------------------


def test_accepted_action_types_includes_safety_substrate_events():
    # A few load-bearing ones; the full set is documented in d1-schema.md §1
    must_have = {
        "DRAFT_CREATED",
        "INVARIANT_VIOLATION",
        "TRUST_PROMOTED",
        "ESCALATION_FIRED",
        "DECOMMISSION_FINAL",
        "COMPLIANCE_PACKET_EXPORTED",
    }
    assert must_have.issubset(ACCEPTED_ACTION_TYPES)


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------


def test_indexes_resolve_point_queries():
    conn = _make_conn()
    # Insert one row so EXPLAIN has data to plan against
    conn.execute(
        "INSERT INTO audit_log (id, ts, action_type, actor, skill_name) "
        "VALUES ('01HZZZ', '2026-05-21T12:00:00.000Z', 'DRAFT_CREATED', 'agent', 'inbox-triage')"
    )
    conn.commit()

    # Each query plan must mention one of our indexes — confirms the index
    # exists and the planner is willing to use it.
    for sql, expected_index_substring in (
        ("SELECT * FROM audit_log WHERE skill_name=? ORDER BY ts DESC", "idx_audit_skill"),
        ("SELECT * FROM audit_log WHERE action_type=? ORDER BY ts DESC", "idx_audit"),
        ("SELECT * FROM audit_log ORDER BY ts DESC LIMIT 50", "idx_audit"),
    ):
        plan = conn.execute(f"EXPLAIN QUERY PLAN {sql}", ("x",) if "?" in sql else ()).fetchall()
        plan_text = " ".join(str(r) for r in plan)
        assert expected_index_substring in plan_text, f"plan for {sql!r}: {plan_text}"


# ---------------------------------------------------------------------------
# Performance: AC says p99 < 10ms write
# ---------------------------------------------------------------------------


def test_write_under_10ms_p99():
    writer, _ = _writer()
    durations: list[float] = []

    async def run():
        for i in range(200):
            t0 = time.perf_counter()
            await writer.write(
                AuditEvent(
                    action_type="DRAFT_CREATED",
                    actor="agent",
                    skill_name="inbox-triage",
                    metadata={"iteration": i},
                )
            )
            durations.append((time.perf_counter() - t0) * 1000.0)

    _run(run())

    durations.sort()
    p99 = durations[int(len(durations) * 0.99)]
    p50 = durations[int(len(durations) * 0.5)]
    # 10ms is the AC budget; sqlite in :memory: should land far below that.
    assert p99 < 10.0, f"p99={p99:.3f}ms (p50={p50:.3f}ms) exceeds 10ms budget"


# ---------------------------------------------------------------------------
# Synchronous semantics: row visible to a fresh SELECT immediately after write
# ---------------------------------------------------------------------------


def test_row_visible_immediately_after_write():
    writer, conn = _writer()
    ulid = _run(
        writer.write(
            AuditEvent(
                action_type="MEMORY_RULE_ADDED",
                actor="captain",
                actor_role=ActorRole.CAPTAIN,
                metadata={"rule_id": "01HZ..."},
            )
        )
    )
    count = conn.execute("SELECT COUNT(*) FROM audit_log WHERE id=?", (ulid,)).fetchone()[0]
    assert count == 1
