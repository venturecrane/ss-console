"""emitted_wake_append verb: uid-gated, EMITTED_WAKE-only (ss-console #2253).

The wake half of the cron gate. The four gated pre_run scripts wrote a row when
they suppressed and nothing when they woke, so the one tick that mattered was
the one tick with no row — on 2026-08-10 a fabricated escalation email was
discoverable only by reading the mailbox.

Caller shape is identical to suppressed_wake_append's (a cron pre_run child:
agent uid, non-gateway PID), and this is deliberately a SEPARATE verb rather
than a widened sibling, so each verb still pins exactly one action_type.

The caller swallows this verb's failures — a wake is never gated on its own
audit row — which is exactly why the validation lives here: a best-effort
caller cannot be trusted to validate on the broker's behalf.
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
        "action_type": "EMITTED_WAKE",
        "actor": "agent",
        "actor_role": "agent",
        "skill_name": "deadline-miss-escalator",
        "metadata": '{"decision_basis":"deadline_in_escalation_range"}',
    }
    row.update(overrides)
    return row


def test_wake_row_from_agent_uid_nongateway_pid_is_written(tmp_path: Path) -> None:
    """The load-bearing case: a cron pre_run child (agent uid, foreign PID)."""
    broker = _broker(tmp_path)
    resp = broker.handle(
        {"action": "emitted_wake_append", "row": _row()},
        peer_pid=9999,  # NOT the gateway PID
        peer_uid=AGENT_UID,
    )
    assert resp["ok"] is True
    assert isinstance(resp["id"], str) and len(resp["id"]) == 26  # ULID
    assert broker.ledger.count() == 1


def test_wake_row_rejected_from_foreign_uid(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    with pytest.raises(PermissionError):
        broker.handle(
            {"action": "emitted_wake_append", "row": _row()},
            peer_pid=9999,
            peer_uid=AGENT_UID + 1,
        )
    assert broker.ledger.count() == 0


def test_wake_row_rejected_when_agent_uid_unresolved(tmp_path: Path) -> None:
    """agent_uid=None (pre-heartbeat image / __new__ default) is fail-closed."""
    broker = _broker(tmp_path)
    broker.agent_uid = None
    broker.gateway_pid = 999999999  # /proc stat fallback must also fail
    with pytest.raises(PermissionError):
        broker.handle(
            {"action": "emitted_wake_append", "row": _row()},
            peer_pid=9999,
            peer_uid=AGENT_UID,
        )
    assert broker.ledger.count() == 0


def test_wake_row_rejected_when_peer_uid_missing(tmp_path: Path) -> None:
    """Two-arg handle() callers (legacy wire) cannot reach this verb."""
    broker = _broker(tmp_path)
    with pytest.raises(PermissionError):
        broker.handle({"action": "emitted_wake_append", "row": _row()}, peer_pid=GATEWAY_PID)
    assert broker.ledger.count() == 0


@pytest.mark.parametrize("forged", ["SUPPRESSED_WAKE", "REPLY_SENT", "WEBHOOK_SUPPRESSED", "TRUST_PROMOTED"])
def test_action_type_is_locked_to_emitted_wake(tmp_path: Path, forged: str) -> None:
    """The verb cannot forge any other audit row — including its own sibling's.

    SUPPRESSED_WAKE is in this list on purpose: the two verbs describe opposite
    decisions, and a wake verb that could write a suppress row would let a gate
    that fired be recorded as a gate that stayed quiet.
    """
    broker = _broker(tmp_path)
    with pytest.raises(ValueError):
        broker.handle(
            {"action": "emitted_wake_append", "row": _row(action_type=forged)},
            peer_pid=9999,
            peer_uid=AGENT_UID,
        )
    assert broker.ledger.count() == 0


def test_the_sibling_verb_still_cannot_write_an_emitted_wake(tmp_path: Path) -> None:
    """The pin holds in both directions: one action_type per verb, still."""
    broker = _broker(tmp_path)
    with pytest.raises(ValueError):
        broker.handle(
            {
                "action": "suppressed_wake_append",
                "row": _row(action_type="EMITTED_WAKE"),
            },
            peer_pid=9999,
            peer_uid=AGENT_UID,
        )
    assert broker.ledger.count() == 0


def test_row_must_be_an_object(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    with pytest.raises(ValueError):
        broker.handle(
            {"action": "emitted_wake_append", "row": "EMITTED_WAKE"},
            peer_pid=9999,
            peer_uid=AGENT_UID,
        )


def test_wake_row_rejected_when_ledger_unconfigured(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    broker.ledger = None
    with pytest.raises(ValueError):
        broker.handle(
            {"action": "emitted_wake_append", "row": _row()},
            peer_pid=9999,
            peer_uid=AGENT_UID,
        )


def test_wake_row_joins_the_hash_chain(tmp_path: Path) -> None:
    """Wake rows are ordinary chained ledger rows, not a side store — a row an
    operator could excise without breaking the chain would not be evidence."""
    broker = _broker(tmp_path)
    broker.handle(
        {"action": "suppressed_wake_append", "row": _row(action_type="SUPPRESSED_WAKE")},
        peer_pid=9999,
        peer_uid=AGENT_UID,
    )
    broker.handle(
        {"action": "emitted_wake_append", "row": _row()},
        peer_pid=9999,
        peer_uid=AGENT_UID,
    )
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "audit.db"))
    rows = conn.execute("SELECT action_type, prev_hash, row_hash FROM audit_log ORDER BY rowid").fetchall()
    conn.close()
    assert [r[0] for r in rows] == ["SUPPRESSED_WAKE", "EMITTED_WAKE"]
    assert rows[1][1] == rows[0][2]  # chains off the prior row's hash
    assert rows[1][2] is not None
