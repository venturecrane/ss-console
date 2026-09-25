"""The ``casework_event_append`` verb, end to end through ``Broker.handle``.

What the broker supplies that no caller can: the thread a raise went out on
(joined from its own CONFIRM_SEND_DISPATCHED row, a caller's thread_ref always
dropped), that the raise reached a person, and that a ``completed`` names a
successful update_task call in its own audit log. Decisions about the firm's
record also land in the hash-chained audit log.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import casework_ledger as cl
from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.casework_verbs import casework_ledger_path, update_task_witnessed
from workspace_broker.server import Broker

AGENT_UID = 1000
GATEWAY_PID = 42
SESSION = "cron_1b149663f32f_20260925_140005"
OTHER_SESSION = "cron_7651107620f7_20260925_140512"
REF = "5f0c3a9e2b7d4e1f8a6c0b3d9e2f7a41"
MATTER = "2026-PI-106"
TASK = "5b1f0e2a-7c44-4c1e-9d0a-2f6b8e1c3a90"
THREAD = "thr_casework_0925"


@pytest.fixture(autouse=True)
def _no_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(cl.LEDGER_PATH_ENV, raising=False)


def _broker(tmp_path: Path, *, audit: bool = True) -> Broker:
    broker = Broker.__new__(Broker)
    broker.customer_slug = "pilot-smokeball"
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.escalation_ledger_path = str(tmp_path / "escalation-ledger.jsonl")
    broker._escalation_lock = threading.Lock()
    db = tmp_path / "audit.db"
    broker.ledger = LedgerWriter(str(db)) if audit else None
    broker.audit_db_path = str(db) if audit else None
    return broker


def _insert(broker: Broker, action_type: str, meta: dict) -> None:
    conn = sqlite3.connect(broker.audit_db_path)
    try:
        conn.execute(
            "INSERT INTO audit_log (id, ts, action_type, actor, metadata) VALUES (?, '2026-09-25T14:00:05Z', ?, 'x', ?)",
            (uuid.uuid4().hex, action_type, json.dumps(meta, sort_keys=True, separators=(",", ":"))),
        )
        conn.commit()
    finally:
        conn.close()


def _confirm(broker: Broker, *, session_id: str = SESSION, recipients=("dana@firm.example",)) -> None:
    meta = {"session_id": session_id, "outcome": "sent", "recipients": list(recipients), "dispatch_ref": REF}
    _insert(broker, "CONFIRM_SEND_DISPATCHED", {**meta, "thread_id": THREAD})


def _update_task(broker: Broker, *, call: str = "toolu_01", outcome: str = "ok", session_id: str = SESSION) -> None:
    meta = {"tool": cl.UPDATE_TASK_TOOL, "outcome": outcome, "tool_call_id": call, "session_id": session_id}
    _insert(broker, "TOOL_CALL_COMPLETED", meta)


def _base() -> dict:
    return {
        "skill": "task-list-keeper",
        "matter_id": MATTER,
        "kind": "task",
        "source_id": TASK,
        "item_key": cl.item_key(matter_id=MATTER, kind="task", source_id=TASK),
        "session_id": SESSION,
    }


def _proposal(**over) -> dict:
    payload = {"action": "close", "class": "done", "staff_id": "st-1", "evidence": ["Proof of Service (2026-07-10)"]}
    return {**_base(), "event": "proposed", "n": 1, "dispatch_ref": REF, "payload": payload, **over}


def _append(broker: Broker, event: dict, *, uid: int = AGENT_UID) -> dict:
    return broker.handle({"action": "casework_event_append", "event": event}, peer_pid=9999, peer_uid=uid)


def _rows(broker: Broker) -> list[dict]:
    path = casework_ledger_path(broker)
    assert path is not None
    return cl.read_ledger(path)


def _audit_types(broker: Broker) -> list[str]:
    conn = sqlite3.connect(broker.audit_db_path)
    try:
        return [r[0] for r in conn.execute("SELECT action_type FROM audit_log ORDER BY rowid")]
    finally:
        conn.close()


def test_the_ledger_lives_beside_the_escalation_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    broker = _broker(tmp_path)
    assert casework_ledger_path(broker) == str(tmp_path / "casework-ledger.jsonl")
    monkeypatch.setenv(cl.LEDGER_PATH_ENV, str(tmp_path / "x.jsonl"))
    assert casework_ledger_path(broker) == str(tmp_path / "x.jsonl")
    broker.escalation_ledger_path = None
    monkeypatch.delenv(cl.LEDGER_PATH_ENV)
    with pytest.raises(ValueError, match="not configured"):
        _append(broker, _proposal())


def test_only_the_agent_uid_may_append(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    _confirm(broker)
    with pytest.raises(PermissionError, match="agent uid"):
        _append(broker, _proposal(), uid=AGENT_UID + 1)
    assert _append(broker, _proposal())["ok"]


def test_a_raise_gets_the_brokers_thread_and_never_the_callers(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    _confirm(broker)
    result = _append(broker, _proposal(thread_ref="thr_forged_by_the_caller"))
    (row,) = _rows(broker)
    assert (row["thread_ref"], result["thread_ref"], row["n"], row["id"]) == (THREAD, THREAD, 1, result["id"])


def test_a_raise_that_reached_nobody_is_refused(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    _confirm(broker, recipients=("ss-probe-smoke@smd.services",))
    with pytest.raises(ValueError, match="dispatched no message"):
        _append(broker, _proposal())
    assert _rows(broker) == []


def test_a_raise_the_broker_cannot_tie_to_its_send_is_refused(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    _confirm(broker, session_id=OTHER_SESSION)
    _confirm(broker)  # this session did send, but on a different dispatch
    with pytest.raises(ValueError, match="could not tie this raise"):
        _append(broker, _proposal(dispatch_ref="aaaabbbbccccddddeeeeffff00001111"))
    assert _rows(broker) == []


def test_an_approval_joins_its_line_and_is_audited(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    _confirm(broker)
    _append(broker, _proposal())
    with pytest.raises(ValueError, match="no line 1 was raised"):
        _append(broker, {**_base(), "event": "approved", "n": 1, "thread_ref": "thr_other"})
    decided_by = {"name": "Dana Ruiz", "key": "b" * 64}
    _append(broker, {**_base(), "event": "approved", "n": 1, "thread_ref": THREAD, "decided_by": decided_by})
    assert [r["event"] for r in _rows(broker)] == ["proposed", "approved"]
    assert _audit_types(broker)[-1] == "CASEWORK_APPROVED"
    conn = sqlite3.connect(broker.audit_db_path)
    try:
        (meta,) = conn.execute("SELECT metadata FROM audit_log WHERE action_type='CASEWORK_APPROVED'").fetchone()
    finally:
        conn.close()
    assert json.loads(meta)["decided_by"] == decided_by


def test_an_at_stake_close_never_reaches_the_ledger(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    _confirm(broker)
    payload = {"action": "close", "class": "at_stake", "evidence": ["Lien: Valley Imaging"]}
    with pytest.raises(ValueError, match="at_stake"):
        _append(broker, _proposal(payload=payload))
    with pytest.raises(ValueError, match="at_stake"):
        _append(broker, {**_base(), "event": "closed_by_record", "payload": payload})
    assert _rows(broker) == []


def test_completed_needs_this_sessions_successful_update_task(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    _append(broker, {**_base(), "event": "closed_by_record", "payload": _proposal()["payload"]})
    assert _audit_types(broker)[-1] == "CASEWORK_CLOSED_BY_RECORD"
    _update_task(broker, call="toolu_err", outcome="error")
    _update_task(broker, call="toolu_peer", session_id=OTHER_SESSION)
    for call in ("toolu_err", "toolu_peer", "toolu_never"):
        with pytest.raises(ValueError, match="holds no successful"):
            _append(broker, {**_base(), "event": "completed", "tool_call_id": call})
    _update_task(broker)
    _append(broker, {**_base(), "event": "completed", "tool_call_id": "toolu_01"})
    state = cl.derive_state(_rows(broker))[_base()["item_key"]]
    assert state.completed and cl.needs_mention(state)


def test_the_update_task_witness_matches_exactly(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    _update_task(broker, call="toolu_0123")
    event = {**_base(), "tool_call_id": "toolu_01"}
    assert not update_task_witnessed(broker.audit_db_path, event)  # a prefix is not the id
    assert update_task_witnessed(broker.audit_db_path, {**event, "tool_call_id": "toolu_0123"})
    assert not update_task_witnessed(broker.audit_db_path, {**event, "tool_call_id": '%"'})
    assert update_task_witnessed(None, event)  # audit-disabled image: no witness exists


def test_bookkeeping_rows_write_no_audit_row(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    _confirm(broker)
    before = len(_audit_types(broker))
    _append(broker, _proposal())
    assert len(_audit_types(broker)) == before
