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
    assert (action, actor, role, matter, digest) == (
        "SMOKEBALL_WRITE",
        "seat-probe:scott@laptop",
        "captain",
        MATTER,
        "f" * 64,
    )
    meta = json.loads(metadata)
    assert meta["phase"] == "intent" and meta["method"] == "POST" and meta["peer_uid"] == AGENT_UID


def test_the_operators_own_write_is_recorded_as_the_agent(tmp_path: Path, monkeypatch) -> None:
    from workspace_broker import audit_verbs

    monkeypatch.setattr(audit_verbs, "peer_parent_pid", lambda pid: GATEWAY_PID)
    broker = _broker(tmp_path)
    broker.handle(
        {"action": "smokeball_write_append", "row": _row(actor="operator")}, peer_pid=9999, peer_uid=AGENT_UID
    )
    assert _stored(tmp_path)[2] == "agent"


def test_an_operator_claim_from_a_process_the_gateway_did_not_start_is_refused(tmp_path: Path, monkeypatch) -> None:
    """Every agent-uid process can reach this verb. Only the broker's own read of
    the peer's parent can make a row say the Operator did it. Falsifier: trust
    the caller's actor and this row lands as the Operator's."""
    from workspace_broker import audit_verbs

    monkeypatch.setattr(audit_verbs, "peer_parent_pid", lambda pid: 31337)
    broker = _broker(tmp_path)
    with pytest.raises(ValueError, match="only accepted from a child of the gateway"):
        broker.handle(
            {"action": "smokeball_write_append", "row": _row(actor="operator")}, peer_pid=9999, peer_uid=AGENT_UID
        )
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


# ---- would_refuse: the matter-resolution gate in observe mode (2026-11-04) ----
#
# The connector records a gated write the gate would have refused, and lets it
# through, while the gate runs in observe mode. The row is the one
# write_record.record_resolution_observation sends: no method or path (no
# request is made at the door), a tool name and a reason code from a closed set.


def _observation(**overrides) -> dict:
    row = {
        "action_type": "SMOKEBALL_WRITE",
        "phase": "would_refuse",
        "write_id": "w" * 32,
        "actor": "operator",
        "tool": "render_firm_form_letter",
        "reason": "bound_elsewhere",
        "matter_id": MATTER,
    }
    row.update(overrides)
    return row


@pytest.fixture
def _from_the_gateway(monkeypatch) -> None:
    from workspace_broker import audit_verbs

    monkeypatch.setattr(audit_verbs, "peer_parent_pid", lambda pid: GATEWAY_PID)


def _observe(broker: Broker, row: dict) -> dict:
    return broker.handle({"action": "smokeball_write_append", "row": row}, peer_pid=9999, peer_uid=AGENT_UID)


@pytest.mark.usefixtures("_from_the_gateway")
def test_an_observed_refusal_is_recorded_as_the_agent_with_its_tool_and_reason(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    assert _observe(broker, _observation())["ok"] is True
    action, actor, role, matter, digest, metadata = _stored(tmp_path)
    assert (action, actor, role, matter, digest) == ("SMOKEBALL_WRITE", "operator", "agent", MATTER, None)
    meta = json.loads(metadata)
    assert meta["phase"] == "would_refuse"
    assert (meta["tool"], meta["reason"]) == ("render_firm_form_letter", "bound_elsewhere")
    assert "method" not in meta and "path" not in meta


@pytest.mark.usefixtures("_from_the_gateway")
def test_an_observation_with_no_matter_is_still_recorded(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    assert _observe(broker, _observation(reason="no_matter", matter_id=None))["ok"] is True
    assert _stored(tmp_path)[3] is None


def test_an_observation_claiming_the_operator_from_outside_the_gateway_is_refused(tmp_path: Path, monkeypatch) -> None:
    from workspace_broker import audit_verbs

    monkeypatch.setattr(audit_verbs, "peer_parent_pid", lambda pid: 31337)
    broker = _broker(tmp_path)
    with pytest.raises(ValueError, match="only accepted from a child of the gateway"):
        _observe(broker, _observation())
    assert broker.ledger.count() == 0


@pytest.mark.usefixtures("_from_the_gateway")
@pytest.mark.parametrize(
    "bad",
    [
        {"reason": "because"},
        {"reason": None},
        {"tool": ""},
        {"tool": "Render Letter; DROP"},
        {"write_id": None},
        {"actor": ""},
    ],
)
def test_a_malformed_observation_is_refused(tmp_path: Path, bad: dict) -> None:
    broker = _broker(tmp_path)
    with pytest.raises(ValueError):
        _observe(broker, _observation(**bad))
    assert broker.ledger.count() == 0


@pytest.mark.usefixtures("_from_the_gateway")
def test_an_observation_never_stores_text_the_caller_adds(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    _observe(broker, _observation(matter_resolution="resolution:" + "a" * 32, body="the letter's words"))
    metadata = _stored(tmp_path)[5]
    assert "resolution:" not in metadata and "words" not in metadata
