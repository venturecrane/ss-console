"""webhook_suppressed_append verb: uid-gated, WEBHOOK_SUPPRESSED-only (ss #1791).

The overlay webhook gate records WEBHOOK_SUPPRESSED for an excluded delivery.
It runs as the agent uid on a NON-gateway PID (same shape as the cron pre_run
children behind suppressed_wake_append), so the generic gateway-PID-gated
audit_append refuses it. This sibling verb is its one door; it locks
action_type to WEBHOOK_SUPPRESSED so it cannot forge any other row.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.server import Broker

AGENT_UID = 1000
GATEWAY_PID = 42


def _broker(tmp_path: Path) -> Broker:
    broker = Broker.__new__(Broker)
    broker.customer_slug = "smd"
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.ledger = LedgerWriter(str(tmp_path / "audit.db"))
    return broker


def _row(**overrides) -> dict:
    row = {
        "action_type": "WEBHOOK_SUPPRESSED",
        "actor": "gate",
        "actor_role": "gate",
        "metadata": '{"reason":"excluded-matter:abc","route":"smokeball"}',
    }
    row.update(overrides)
    return row


def test_suppression_from_agent_uid_nongateway_pid_writes_a_row(tmp_path: Path) -> None:
    """The load-bearing case: the gate (agent uid, foreign PID)."""
    broker = _broker(tmp_path)
    resp = broker.handle(
        {"action": "webhook_suppressed_append", "row": _row()},
        peer_pid=992,  # NOT the gateway PID
        peer_uid=AGENT_UID,
    )
    assert resp["ok"] is True
    assert isinstance(resp["id"], str) and len(resp["id"]) == 26  # ULID
    assert broker.ledger.count() == 1


def test_suppression_rejected_from_foreign_uid(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    with pytest.raises(PermissionError):
        broker.handle(
            {"action": "webhook_suppressed_append", "row": _row()},
            peer_pid=992,
            peer_uid=AGENT_UID + 1,
        )
    assert broker.ledger.count() == 0


def test_suppression_rejected_when_agent_uid_unresolved(tmp_path: Path) -> None:
    """agent_uid=None (pre-heartbeat image / __new__ default) is fail-closed."""
    broker = _broker(tmp_path)
    broker.agent_uid = None
    with pytest.raises(PermissionError):
        broker.handle(
            {"action": "webhook_suppressed_append", "row": _row()},
            peer_pid=992,
            peer_uid=AGENT_UID,
        )
    assert broker.ledger.count() == 0


def test_suppression_rejected_when_peer_uid_missing(tmp_path: Path) -> None:
    """Two-arg handle() callers (legacy wire) cannot reach the verb."""
    broker = _broker(tmp_path)
    with pytest.raises(PermissionError):
        broker.handle({"action": "webhook_suppressed_append", "row": _row()}, peer_pid=GATEWAY_PID)
    assert broker.ledger.count() == 0


def test_action_type_is_locked_to_webhook_suppressed(tmp_path: Path) -> None:
    """The verb cannot forge any other audit row (e.g. a SUPPRESSED_WAKE)."""
    broker = _broker(tmp_path)
    with pytest.raises(ValueError):
        broker.handle(
            {
                "action": "webhook_suppressed_append",
                "row": _row(action_type="SUPPRESSED_WAKE"),
            },
            peer_pid=992,
            peer_uid=AGENT_UID,
        )
    assert broker.ledger.count() == 0


def test_row_must_be_an_object(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    with pytest.raises(ValueError):
        broker.handle(
            {"action": "webhook_suppressed_append", "row": "WEBHOOK_SUPPRESSED"},
            peer_pid=992,
            peer_uid=AGENT_UID,
        )


def test_suppression_rejected_when_ledger_unconfigured(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    broker.ledger = None
    with pytest.raises(ValueError):
        broker.handle(
            {"action": "webhook_suppressed_append", "row": _row()},
            peer_pid=992,
            peer_uid=AGENT_UID,
        )


def test_suppression_row_joins_the_hash_chain(tmp_path: Path) -> None:
    """Suppression rows are ordinary chained ledger rows, not a side store."""
    broker = _broker(tmp_path)
    broker.handle(
        {
            "action": "audit_append",
            "row": {"action_type": "TOOL_CALL_COMPLETED", "actor": "agent", "actor_role": "agent"},
        },
        peer_pid=GATEWAY_PID,
        peer_uid=AGENT_UID,
    )
    broker.handle(
        {"action": "webhook_suppressed_append", "row": _row()},
        peer_pid=992,
        peer_uid=AGENT_UID,
    )
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "audit.db"))
    rows = conn.execute("SELECT action_type, prev_hash, row_hash FROM audit_log ORDER BY rowid").fetchall()
    conn.close()
    assert [r[0] for r in rows] == ["TOOL_CALL_COMPLETED", "WEBHOOK_SUPPRESSED"]
    assert rows[1][1] == rows[0][2]  # chains off the prior row's hash
    assert rows[1][2] is not None
