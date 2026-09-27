"""smokeball_write_append: every write to a firm's Smokeball gets a ledger row.

A&P, 2026-09-26: the Operator's Smokeball writes left rows that named the tool
but not the thing changed, and SMD's direct writes left none (52 documents filed
by hand on 09-25). The connector client now calls this verb before and after
every write and refuses the write when the first call fails. These tests hold
the broker half: who may call it, and that the row is rebuilt from bounded
fields rather than stored as sent.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.server import Broker

AGENT_UID = 1000
GATEWAY_PID = 42
MATTER = "0d3fffa2-03aa-4cf5-8e8c-2f77cb3c9d46"


def _broker(tmp_path: Path) -> Broker:
    broker = Broker.__new__(Broker)
    broker.customer_slug = "smd"
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.ledger = LedgerWriter(str(tmp_path / "audit.db"))
    return broker


def _row(**overrides) -> dict:
    row = {
        "action_type": "SMOKEBALL_WRITE",
        "phase": "intent",
        "write_id": "abc123",
        "actor": "seat-probe:scott@laptop",
        "method": "POST",
        "path": f"/matters/{MATTER}/documents/files",
        "matter_id": MATTER,
        "body_sha256": "f" * 64,
    }
    row.update(overrides)
    return row


def _stored(tmp_path: Path) -> tuple:
    conn = sqlite3.connect(str(tmp_path / "audit.db"))
    row = conn.execute(
        "SELECT action_type, actor, actor_role, matter_ref, input_digest, metadata FROM audit_log ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return row


def test_a_seat_probe_script_write_is_recorded_as_smd(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    resp = broker.handle({"action": "smokeball_write_append", "row": _row()}, peer_pid=9999, peer_uid=AGENT_UID)
    assert resp["ok"] is True
    action, actor, role, matter, digest, metadata = _stored(tmp_path)
    assert (action, actor, role, matter, digest) == ("SMOKEBALL_WRITE", "seat-probe:scott@laptop", "captain", MATTER, "f" * 64)
    meta = json.loads(metadata)
    assert meta["phase"] == "intent" and meta["method"] == "POST" and meta["peer_uid"] == AGENT_UID


def test_the_operators_own_write_is_recorded_as_the_agent(tmp_path: Path, monkeypatch) -> None:
    from workspace_broker import audit_verbs

    monkeypatch.setattr(audit_verbs, "peer_parent_pid", lambda pid: GATEWAY_PID)
    broker = _broker(tmp_path)
    broker.handle({"action": "smokeball_write_append", "row": _row(actor="operator")}, peer_pid=9999, peer_uid=AGENT_UID)
    assert _stored(tmp_path)[2] == "agent"


def test_an_operator_claim_from_a_process_the_gateway_did_not_start_is_refused(tmp_path: Path, monkeypatch) -> None:
    """Every agent-uid process can reach this verb. Only the broker's own read of
    the peer's parent can make a row say the Operator did it. Falsifier: trust
    the caller's actor and this row lands as the Operator's."""
    from workspace_broker import audit_verbs

    monkeypatch.setattr(audit_verbs, "peer_parent_pid", lambda pid: 31337)
    broker = _broker(tmp_path)
    with pytest.raises(ValueError, match="only accepted from a child of the gateway"):
        broker.handle({"action": "smokeball_write_append", "row": _row(actor="operator")}, peer_pid=9999, peer_uid=AGENT_UID)
    assert broker.ledger.count() == 0


def test_the_broker_reads_a_real_parent_pid_from_proc() -> None:
    import os

    from workspace_broker.audit_verbs import peer_parent_pid

    if not os.path.exists(f"/proc/{os.getpid()}/stat"):
        pytest.skip("no /proc on this platform")
    assert peer_parent_pid(os.getpid()) == os.getppid()


def test_root_may_record_a_write(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    resp = broker.handle({"action": "smokeball_write_append", "row": _row()}, peer_pid=9999, peer_uid=0)
    assert resp["ok"] is True


def test_a_foreign_uid_cannot_write_a_row(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    with pytest.raises(PermissionError):
        broker.handle({"action": "smokeball_write_append", "row": _row()}, peer_pid=9999, peer_uid=AGENT_UID + 7)
    assert broker.ledger.count() == 0


@pytest.mark.parametrize(
    "bad",
    [
        {"action_type": "TOOL_CALL_COMPLETED"},
        {"phase": "maybe"},
        {"method": "GET"},
        {"actor": ""},
        {"write_id": None},
    ],
)
def test_a_malformed_row_is_refused(tmp_path: Path, bad: dict) -> None:
    broker = _broker(tmp_path)
    with pytest.raises(ValueError):
        broker.handle({"action": "smokeball_write_append", "row": _row(**bad)}, peer_pid=9999, peer_uid=AGENT_UID)
    assert broker.ledger.count() == 0


def test_fields_the_caller_invents_are_not_stored(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    broker.handle(
        {"action": "smokeball_write_append", "row": _row(trust_ceiling="autonomous", skill_name="forged")},
        peer_pid=9999,
        peer_uid=AGENT_UID,
    )
    conn = sqlite3.connect(str(tmp_path / "audit.db"))
    ceiling, skill = conn.execute("SELECT trust_ceiling, skill_name FROM audit_log").fetchone()
    conn.close()
    assert ceiling is None and skill is None
