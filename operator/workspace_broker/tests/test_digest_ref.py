"""The digest's numbered map: the broker, not the caller, says which thread.

A plain reply ("got it on 1") quiets the item numbered 1 IN THE THREAD IT
ANSWERS. ``digest_ref.stamp_thread_ref`` makes that scope the broker's fact: it
joins the raise's ``dispatch_ref`` (within the raise's own session) to the
``CONFIRM_SEND_DISPATCHED`` row the broker wrote itself, and copies the vendor
thread id from it. Anything it cannot join loses its number and keeps its raise.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import escalation_ledger as el
from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.digest_ref import stamp_thread_ref
from workspace_broker.server import Broker

AGENT_UID = 1000
GATEWAY_PID = 42
SESSION = "cron_1b149663f32f_20260925_140005"
OTHER_SESSION = "cron_7651107620f7_20260925_140512"
REF = "5f0c3a9e2b7d4e1f8a6c0b3d9e2f7a41"
OTHER_REF = "aaaabbbbccccddddeeeeffff00001111"


def _broker(tmp_path: Path) -> Broker:
    broker = Broker.__new__(Broker)
    broker.customer_slug = "pilot-smokeball"
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.ledger = None
    broker.escalation_ledger_path = str(tmp_path / "escalation-ledger.jsonl")
    broker._escalation_lock = threading.Lock()
    db = tmp_path / "audit.db"
    LedgerWriter(str(db))
    broker.audit_db_path = str(db)
    return broker


def _confirm(broker: Broker, *, session_id: str = SESSION, dispatch_ref: str = REF, **thread: str) -> None:
    """A confirm row in the exact serialization ``append_send_row`` writes."""
    meta = {
        "customer": broker.customer_slug,
        "verb": "agentmail_send",
        "session_id": session_id,
        "outcome": "sent",
        "recipients": ["scott@smd.services"],
        "message_id": "msg_digest",
        "dispatch_ref": dispatch_ref,
        **thread,
    }
    conn = sqlite3.connect(broker.audit_db_path)
    try:
        conn.execute(
            "INSERT INTO audit_log (ts, action_type, actor, actor_role, skill_name, metadata)"
            " VALUES ('2026-09-25T14:00:05Z', 'CONFIRM_SEND_DISPATCHED', 'operator', 'agent',"
            " 'deadline-miss-escalator', ?)",
            (json.dumps(meta, sort_keys=True, separators=(",", ":")),),
        )
        conn.commit()
    finally:
        conn.close()


def _raise(kind: str = "fired", *, n=1, dispatch_ref=REF, item_key: str = "item-one", **extra) -> dict:
    event = el.make_event(
        skill="deadline-miss-escalator",
        matter_id="2026-PI-101",
        item_key=item_key,
        event=kind,
        attempt=1,
        token="ACK-7Q3M2K",
    )
    event["session_id"] = SESSION
    if n is not None:
        event["n"] = n
    if dispatch_ref is not None:
        event["dispatch_ref"] = dispatch_ref
    event.update(extra)
    return event


def _append(broker: Broker, event: dict) -> dict:
    return broker.handle({"action": "escalation_event_append", "event": event}, peer_pid=9999, peer_uid=AGENT_UID)


def _rows(broker: Broker) -> list[dict]:
    return el.read_ledger(broker.escalation_ledger_path)


def test_an_agentmail_raise_is_stamped_with_the_digest_thread(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    _confirm(broker, thread_id="thr_digest_0925")
    _append(broker, _raise())
    row = _rows(broker)[0]
    assert (row["n"], row["dispatch_ref"], row["thread_ref"]) == (1, REF, "thr_digest_0925")


def test_a_graph_raise_is_stamped_with_the_conversation(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    _confirm(broker, conversation_id="AAQkDIGEST=")
    _append(broker, _raise())
    assert _rows(broker)[0]["thread_ref"] == "AAQkDIGEST="


def test_a_group_number_is_shared_by_several_raises(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    _confirm(broker, thread_id="thr_g")
    _append(broker, _raise(n=5, item_key="also-open-a"))
    _append(broker, _raise(n=5, item_key="also-open-b"))
    assert [(r["item_key"], r["n"], r["thread_ref"]) for r in _rows(broker)] == [
        ("also-open-a", 5, "thr_g"),
        ("also-open-b", 5, "thr_g"),
    ]


def test_another_sessions_dispatch_is_not_this_raises_thread(tmp_path: Path) -> None:
    """The nonce is unguessable, but the session is still part of the key: a
    confirm row from another turn never lends its thread."""
    broker = _broker(tmp_path)
    _confirm(broker, session_id=OTHER_SESSION, thread_id="thr_other_turn")
    # A person-reaching dispatch in THIS session, so the witness admits the raise.
    _confirm(broker, dispatch_ref=OTHER_REF, thread_id="thr_this_turn")
    _append(broker, _raise())
    row = _rows(broker)[0]
    assert row["event"] == "fired"
    assert not {"n", "dispatch_ref", "thread_ref"} & set(row)


def test_an_unmatched_raise_loses_its_number_and_keeps_its_raise(tmp_path: Path) -> None:
    """The alarm reached a person (the witness says so); losing the raise would
    re-fire a delivered alarm. Only the number is dropped."""
    broker = _broker(tmp_path)
    _confirm(broker, dispatch_ref=OTHER_REF, thread_id="thr_x")
    resp = _append(broker, _raise())
    assert resp["ok"] is True
    row = _rows(broker)[0]
    assert row["event"] == "fired"
    assert not {"n", "dispatch_ref", "thread_ref"} & set(row)


def test_a_matched_dispatch_with_no_thread_id_strips_the_number(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    _confirm(broker)  # a vendor that returned no thread id
    _append(broker, _raise())
    row = _rows(broker)[0]
    assert not {"n", "dispatch_ref", "thread_ref"} & set(row)


def test_a_number_with_no_dispatch_ref_is_stripped(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    _confirm(broker, thread_id="thr_digest")
    _append(broker, _raise(dispatch_ref=None))
    assert "n" not in _rows(broker)[0]


def test_a_caller_supplied_thread_ref_is_always_discarded(tmp_path: Path) -> None:
    """A caller that could name the thread could make a reply in one thread
    quiet an item raised in another."""
    broker = _broker(tmp_path)
    _confirm(broker, thread_id="thr_real")
    _append(broker, _raise(thread_ref="thr_forged"))
    _append(broker, _raise(n=None, dispatch_ref=None, item_key="item-two", thread_ref="thr_forged"))
    rows = _rows(broker)
    assert rows[0]["thread_ref"] == "thr_real"
    assert "thread_ref" not in rows[1]


@pytest.mark.parametrize("field", ["n", "dispatch_ref"])
def test_digest_fields_on_an_ack_are_refused(tmp_path: Path, field: str) -> None:
    broker = _broker(tmp_path)
    _confirm(broker, thread_id="thr_digest")
    _append(broker, _raise())
    ack = el.make_event(
        skill="deadline-miss-escalator",
        matter_id="2026-PI-101",
        item_key="item-one",
        event="acked",
        attempt=1,
        token="ACK-7Q3M2K",
    )
    ack[field] = 1 if field == "n" else REF
    with pytest.raises(ValueError, match="carries no digest number"):
        _append(broker, ack)
    assert len(_rows(broker)) == 1


def test_an_ack_with_only_a_thread_ref_has_it_dropped_not_refused(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    _confirm(broker, thread_id="thr_digest")
    _append(broker, _raise())
    ack = el.make_event(
        skill="deadline-miss-escalator",
        matter_id="2026-PI-101",
        item_key="item-one",
        event="acked",
        attempt=1,
        token="ACK-7Q3M2K",
    )
    ack["thread_ref"] = "thr_digest"
    _append(broker, ack)
    assert "thread_ref" not in _rows(broker)[1]


@pytest.mark.parametrize("bad", [REF.upper(), REF[:-1], REF + "0", "g" * 32, 123])
def test_a_malformed_dispatch_ref_is_refused(tmp_path: Path, bad) -> None:
    broker = _broker(tmp_path)
    _confirm(broker, thread_id="thr_digest")
    with pytest.raises(ValueError, match="dispatch_ref must be"):
        _append(broker, _raise(dispatch_ref=bad))
    assert _rows(broker) == []


@pytest.mark.parametrize("bad", [0, -1, 1000, True, "1", 1.0])
def test_an_out_of_range_number_is_refused(tmp_path: Path, bad) -> None:
    broker = _broker(tmp_path)
    _confirm(broker, thread_id="thr_digest")
    with pytest.raises(ValueError, match="n must be"):
        _append(broker, _raise(n=bad))
    assert _rows(broker) == []


def test_an_unreadable_audit_db_strips_rather_than_blocks() -> None:
    event = _raise()
    event["thread_ref"] = "thr_forged"
    stamp_thread_ref("/nonexistent/audit.db", event)
    assert not {"n", "dispatch_ref", "thread_ref"} & set(event)
    assert event["event"] == "fired"
