"""audit_append verb: PID-gated, append-only, broker-stamped (OP-P1-4)."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.server import Broker


def _broker(tmp_path: Path) -> Broker:
    broker = Broker.__new__(Broker)
    broker.customer_slug = "smd"
    broker.gateway_pid = 42
    broker.ledger = LedgerWriter(str(tmp_path / "audit.db"))
    return broker


def _row(**overrides) -> dict:
    row = {"action_type": "TOOL_CALL_COMPLETED", "actor": "agent", "actor_role": "agent"}
    row.update(overrides)
    return row


def test_append_from_gateway_pid_writes_a_row(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    resp = broker.handle({"action": "audit_append", "row": _row()}, peer_pid=42)
    assert resp["ok"] is True
    assert isinstance(resp["id"], str) and len(resp["id"]) == 26  # ULID
    assert broker.ledger.count() == 1


def test_append_rejected_from_non_gateway_pid(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    with pytest.raises(PermissionError):
        broker.handle({"action": "audit_append", "row": _row()}, peer_pid=999)
    assert broker.ledger.count() == 0


def test_no_update_or_delete_verb_exists(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    broker.handle({"action": "audit_append", "row": _row()}, peer_pid=42)
    for verb in ("audit_update", "audit_delete", "audit_drop", "delete", "update"):
        with pytest.raises(ValueError, match="unsupported broker action|operation and object"):
            broker.handle({"action": verb, "row": _row()}, peer_pid=42)
    assert broker.ledger.count() == 1  # unchanged — no mutation path


def test_broker_stamps_id_and_ts(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    # Agent cannot supply id/ts — those columns are unknown to the append API.
    with pytest.raises(ValueError, match="unknown column"):
        broker.handle(
            {"action": "audit_append", "row": _row(id="forged", ts="2000-01-01T00:00:00Z")},
            peer_pid=42,
        )


def test_append_requires_action_type(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    with pytest.raises(ValueError, match="action_type"):
        broker.handle({"action": "audit_append", "row": {"actor": "agent"}}, peer_pid=42)


def test_append_requires_row_object(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    with pytest.raises(ValueError, match="row"):
        broker.handle({"action": "audit_append", "row": "not-a-dict"}, peer_pid=42)


def test_append_errors_when_ledger_unconfigured(tmp_path: Path) -> None:
    broker = Broker.__new__(Broker)
    broker.customer_slug = "smd"
    broker.gateway_pid = 42
    broker.ledger = None  # pre-WS5 / direct-write image
    with pytest.raises(ValueError, match="not configured"):
        broker.handle({"action": "audit_append", "row": _row()}, peer_pid=42)


def test_metadata_roundtrips_and_is_agent_readable(tmp_path: Path) -> None:
    db = str(tmp_path / "audit.db")
    broker = Broker.__new__(Broker)
    broker.customer_slug = "smd"
    broker.gateway_pid = 42
    broker.ledger = LedgerWriter(db)
    broker.handle(
        {"action": "audit_append", "row": _row(skill_name="inbox-triage", metadata='{"k":"v"}')},
        peer_pid=42,
    )
    # A separate read-only connection (the agent-uid read-seam shape) sees it.
    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = ro.execute("SELECT action_type, skill_name, metadata FROM audit_log").fetchall()
    ro.close()
    assert rows == [("TOOL_CALL_COMPLETED", "inbox-triage", '{"k":"v"}')]
