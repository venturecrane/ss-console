"""suppressed_wake_append verb: uid-gated, SUPPRESSED_WAKE-only (ADR 0021 Stream B).

Cron pre_run scripts run as subprocess CHILDREN of the gateway (hermes
cron/scheduler.py ``subprocess.run``), so they share the agent uid but never
the gateway PID. This verb is the one heartbeat door for them; everything else
about the ledger (hash chain, broker-stamped id/ts, append-only) is unchanged.
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
        "action_type": "SUPPRESSED_WAKE",
        "actor": "pre_run",
        "actor_role": "agent",
        "skill_name": "discovery-response-tracker",
    }
    row.update(overrides)
    return row


def test_heartbeat_from_agent_uid_nongateway_pid_writes_a_row(tmp_path: Path) -> None:
    """The load-bearing case: a cron pre_run child (agent uid, foreign PID)."""
    broker = _broker(tmp_path)
    resp = broker.handle(
        {"action": "suppressed_wake_append", "row": _row()},
        peer_pid=9999,  # NOT the gateway PID
        peer_uid=AGENT_UID,
    )
    assert resp["ok"] is True
    assert isinstance(resp["id"], str) and len(resp["id"]) == 26  # ULID
    assert broker.ledger.count() == 1


def test_heartbeat_rejected_from_foreign_uid(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    with pytest.raises(PermissionError):
        broker.handle(
            {"action": "suppressed_wake_append", "row": _row()},
            peer_pid=9999,
            peer_uid=AGENT_UID + 1,
        )
    assert broker.ledger.count() == 0


def test_heartbeat_rejected_when_agent_uid_unresolved(tmp_path: Path) -> None:
    """agent_uid=None (pre-heartbeat image / __new__ default) is fail-closed."""
    broker = _broker(tmp_path)
    broker.agent_uid = None
    with pytest.raises(PermissionError):
        broker.handle(
            {"action": "suppressed_wake_append", "row": _row()},
            peer_pid=9999,
            peer_uid=AGENT_UID,
        )
    assert broker.ledger.count() == 0


def test_heartbeat_rejected_when_peer_uid_missing(tmp_path: Path) -> None:
    """Two-arg handle() callers (legacy wire) cannot reach the heartbeat verb."""
    broker = _broker(tmp_path)
    with pytest.raises(PermissionError):
        broker.handle({"action": "suppressed_wake_append", "row": _row()}, peer_pid=GATEWAY_PID)
    assert broker.ledger.count() == 0


def test_action_type_is_locked_to_suppressed_wake(tmp_path: Path) -> None:
    """The verb cannot forge any other audit row."""
    broker = _broker(tmp_path)
    with pytest.raises(ValueError):
        broker.handle(
            {
                "action": "suppressed_wake_append",
                "row": _row(action_type="REPLY_SENT"),
            },
            peer_pid=9999,
            peer_uid=AGENT_UID,
        )
    assert broker.ledger.count() == 0


def test_row_must_be_an_object(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    with pytest.raises(ValueError):
        broker.handle(
            {"action": "suppressed_wake_append", "row": "SUPPRESSED_WAKE"},
            peer_pid=9999,
            peer_uid=AGENT_UID,
        )


def test_heartbeat_rejected_when_ledger_unconfigured(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    broker.ledger = None
    with pytest.raises(ValueError):
        broker.handle(
            {"action": "suppressed_wake_append", "row": _row()},
            peer_pid=9999,
            peer_uid=AGENT_UID,
        )


def test_heartbeat_row_joins_the_hash_chain(tmp_path: Path) -> None:
    """Heartbeat rows are ordinary chained ledger rows, not a side store."""
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
        {"action": "suppressed_wake_append", "row": _row()},
        peer_pid=9999,
        peer_uid=AGENT_UID,
    )
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "audit.db"))
    rows = conn.execute("SELECT action_type, prev_hash, row_hash FROM audit_log ORDER BY rowid").fetchall()
    conn.close()
    assert [r[0] for r in rows] == ["TOOL_CALL_COMPLETED", "SUPPRESSED_WAKE"]
    # The heartbeat row chains off the prior row's hash.
    assert rows[1][1] == rows[0][2]
    assert rows[1][2] is not None


# ---------------------------------------------------------------------------
# Agent-uid resolution (live-caught on pilot-smokeball 2026-07-06: at broker
# start the gateway PID still belongs to the ROOT entrypoint — the exec-drop
# to the agent user happens later, so boot-time derivation read uid 0)
# ---------------------------------------------------------------------------


def test_agent_uid_resolves_from_env(tmp_path: Path, monkeypatch) -> None:
    broker = _broker(tmp_path)
    broker.agent_uid = None
    monkeypatch.setenv("SMD_AGENT_UID", str(AGENT_UID))
    resp = broker.handle(
        {"action": "suppressed_wake_append", "row": _row()},
        peer_pid=9999,
        peer_uid=AGENT_UID,
    )
    assert resp["ok"] is True
    assert broker.agent_uid == AGENT_UID  # cached after first resolution


def test_agent_uid_env_zero_is_refused(tmp_path: Path, monkeypatch) -> None:
    """uid 0 is never a valid agent uid — a root caller must not be minted in."""
    broker = _broker(tmp_path)
    broker.agent_uid = None
    broker.gateway_pid = 999999999  # /proc stat fallback must also fail
    monkeypatch.setenv("SMD_AGENT_UID", "0")
    with pytest.raises(PermissionError):
        broker.handle(
            {"action": "suppressed_wake_append", "row": _row()},
            peer_pid=9999,
            peer_uid=0,
        )


def test_agent_uid_proc_stat_of_root_is_refused(tmp_path: Path, monkeypatch) -> None:
    """A pre-exec-drop stat reading the root entrypoint must not be cached."""
    broker = _broker(tmp_path)
    broker.agent_uid = None
    monkeypatch.delenv("SMD_AGENT_UID", raising=False)

    class _RootStat:
        st_uid = 0

    monkeypatch.setattr("workspace_broker.server.os.stat", lambda path: _RootStat())
    with pytest.raises(PermissionError):
        broker.handle(
            {"action": "suppressed_wake_append", "row": _row()},
            peer_pid=9999,
            peer_uid=0,
        )
    assert broker.agent_uid is None  # not cached — a later stat can still succeed


def test_agent_uid_lazy_proc_stat_resolves_nonroot(tmp_path: Path, monkeypatch) -> None:
    broker = _broker(tmp_path)
    broker.agent_uid = None
    monkeypatch.delenv("SMD_AGENT_UID", raising=False)

    class _HermesStat:
        st_uid = AGENT_UID

    monkeypatch.setattr("workspace_broker.server.os.stat", lambda path: _HermesStat())
    resp = broker.handle(
        {"action": "suppressed_wake_append", "row": _row()},
        peer_pid=9999,
        peer_uid=AGENT_UID,
    )
    assert resp["ok"] is True
    assert broker.agent_uid == AGENT_UID
