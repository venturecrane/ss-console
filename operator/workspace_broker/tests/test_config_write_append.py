"""config_write_append: the root appliers' CONFIG_WRITE rows reach the ledger.

2026-10-20: the config and skills appliers run as root pollers, not as the
gateway, so the generic audit_append refused every row they wrote and every
live customer.yaml apply and skills release landed unrecorded. These hold the
verb: root may write, nobody else may, and only CONFIG_WRITE.
"""

from __future__ import annotations

import sqlite3
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


def _row(action_type: str = "CONFIG_WRITE") -> dict:
    return {"action_type": action_type, "actor": "operator", "metadata": '{"config_apply":true}'}


def _count(tmp_path: Path) -> int:
    conn = sqlite3.connect(str(tmp_path / "audit.db"))
    try:
        return conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    finally:
        conn.close()


def test_root_records_a_config_write(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    resp = broker.handle({"action": "config_write_append", "row": _row()}, peer_pid=777, peer_uid=0)
    assert resp["ok"] is True
    assert _count(tmp_path) == 1


def test_the_generic_verb_still_refuses_root(tmp_path: Path) -> None:
    """The failure this verb exists for: a root poller on the generic append."""
    broker = _broker(tmp_path)
    with pytest.raises(PermissionError, match="gateway"):
        broker.handle({"action": "audit_append", "row": _row()}, peer_pid=777, peer_uid=0)
    assert _count(tmp_path) == 0


def test_the_agent_and_the_gateway_may_not_write_a_config_row(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    for pid, uid in ((9999, AGENT_UID), (GATEWAY_PID, AGENT_UID)):
        with pytest.raises(PermissionError):
            broker.handle({"action": "config_write_append", "row": _row()}, peer_pid=pid, peer_uid=uid)
    assert _count(tmp_path) == 0


def test_only_config_write_rows(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    with pytest.raises(ValueError, match="CONFIG_WRITE"):
        broker.handle(
            {"action": "config_write_append", "row": _row("CONFIRM_SEND_DISPATCHED")}, peer_pid=777, peer_uid=0
        )
    assert _count(tmp_path) == 0
