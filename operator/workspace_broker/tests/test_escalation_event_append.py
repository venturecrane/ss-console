"""escalation_event_append verb: uid-gated, validated, server-stamped (WP-A).

Same caller shape as suppressed_wake_append — a cron pre_run or the agent's
execute_code turn (agent uid, non-gateway PID). The load-bearing guarantee: an
``acked`` with no prior ``fired``/``chased`` raise is REJECTED, so the LLM turn
cannot silence a deadline alarm that never rang, and it can only append through
this validated seam (never the file directly).
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import escalation_ledger as el
from workspace_broker.server import Broker

AGENT_UID = 1000
GATEWAY_PID = 42


def _broker(tmp_path: Path) -> Broker:
    broker = Broker.__new__(Broker)
    broker.customer_slug = "smd"
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.ledger = None  # not used by the escalation verb
    broker.escalation_ledger_path = str(tmp_path / "escalation-ledger.jsonl")
    broker._escalation_lock = threading.Lock()
    return broker


def _fired(**overrides) -> dict:
    event = el.make_event(
        skill="deadline-miss-escalator",
        matter_id="2026-PI-101",
        item_key="abc123def456",
        event="fired",
        attempt=1,
        token="ACK-7Q3M2K",
    )
    event.update(overrides)
    return event


def _read(path: str) -> list[dict]:
    return el.read_ledger(path)


def test_fired_from_agent_uid_nongateway_pid_appends(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    resp = broker.handle(
        {"action": "escalation_event_append", "event": _fired()},
        peer_pid=9999,  # NOT the gateway PID
        peer_uid=AGENT_UID,
    )
    assert resp["ok"] is True
    assert isinstance(resp["id"], str) and len(resp["id"]) == 26  # ULID
    events = _read(broker.escalation_ledger_path)
    assert len(events) == 1
    assert events[0]["event"] == "fired"
    assert events[0]["token"] == "ACK-7Q3M2K"
    assert events[0]["ts"] and events[0]["id"]  # server-stamped


def test_server_stamps_ts_even_if_caller_supplies_one(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    broker.handle(
        {"action": "escalation_event_append", "event": _fired(ts="1999-01-01T00:00:00.000Z")},
        peer_pid=9999,
        peer_uid=AGENT_UID,
    )
    events = _read(broker.escalation_ledger_path)
    assert not events[0]["ts"].startswith("1999")  # the caller cannot backdate


def test_acked_with_prior_fired_is_accepted(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    broker.handle(
        {"action": "escalation_event_append", "event": _fired()},
        peer_pid=9999,
        peer_uid=AGENT_UID,
    )
    acked = el.make_event(
        skill="deadline-miss-escalator",
        matter_id="2026-PI-101",
        item_key="abc123def456",
        event="acked",
        attempt=1,
        token="ACK-7Q3M2K",
    )
    resp = broker.handle(
        {"action": "escalation_event_append", "event": acked},
        peer_pid=9999,
        peer_uid=AGENT_UID,
    )
    assert resp["ok"] is True
    assert [e["event"] for e in _read(broker.escalation_ledger_path)] == ["fired", "acked"]


def test_acked_without_prior_fired_is_rejected(tmp_path: Path) -> None:
    """The security line: you cannot ack an alarm that never rang."""
    broker = _broker(tmp_path)
    acked = el.make_event(
        skill="deadline-miss-escalator",
        matter_id="2026-PI-101",
        item_key="never-fired-key",
        event="acked",
        attempt=1,
        token="ACK-ZZZZZZ",
    )
    with pytest.raises(ValueError):
        broker.handle(
            {"action": "escalation_event_append", "event": acked},
            peer_pid=9999,
            peer_uid=AGENT_UID,
        )
    assert _read(broker.escalation_ledger_path) == []


def test_rejected_from_foreign_uid(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    with pytest.raises(PermissionError):
        broker.handle(
            {"action": "escalation_event_append", "event": _fired()},
            peer_pid=9999,
            peer_uid=AGENT_UID + 1,
        )
    assert _read(broker.escalation_ledger_path) == []


def test_rejected_when_agent_uid_unresolved(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    broker.agent_uid = None
    broker.gateway_pid = 999999999  # /proc stat fallback also fails
    with pytest.raises(PermissionError):
        broker.handle(
            {"action": "escalation_event_append", "event": _fired()},
            peer_pid=9999,
            peer_uid=AGENT_UID,
        )


def test_rejected_when_peer_uid_missing(tmp_path: Path) -> None:
    """Two-arg handle() callers (legacy wire) cannot reach the verb."""
    broker = _broker(tmp_path)
    with pytest.raises(PermissionError):
        broker.handle({"action": "escalation_event_append", "event": _fired()}, peer_pid=GATEWAY_PID)


def test_event_must_be_an_object(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    with pytest.raises(ValueError):
        broker.handle(
            {"action": "escalation_event_append", "event": "fired"},
            peer_pid=9999,
            peer_uid=AGENT_UID,
        )


def test_unknown_event_kind_is_rejected(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    with pytest.raises(ValueError):
        broker.handle(
            {"action": "escalation_event_append", "event": _fired(event="exploded")},
            peer_pid=9999,
            peer_uid=AGENT_UID,
        )


def test_rejected_when_ledger_unconfigured(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    broker.escalation_ledger_path = None
    with pytest.raises(ValueError):
        broker.handle(
            {"action": "escalation_event_append", "event": _fired()},
            peer_pid=9999,
            peer_uid=AGENT_UID,
        )


def test_appends_are_serialized_under_concurrency(tmp_path: Path) -> None:
    """The instance lock keeps the tail-read + append consistent when the
    threaded server fans concurrent appends at the same ledger."""
    broker = _broker(tmp_path)

    def append(i: int) -> None:
        broker.handle(
            {
                "action": "escalation_event_append",
                "event": _fired(item_key=f"key-{i}", token=f"ACK-{i:06d}"),
            },
            peer_pid=9999,
            peer_uid=AGENT_UID,
        )

    threads = [threading.Thread(target=append, args=(i,)) for i in range(25)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    events = _read(broker.escalation_ledger_path)
    assert len(events) == 25  # no lost or interleaved-corrupt lines
    # Every line is individually valid JSON (no torn writes).
    with open(broker.escalation_ledger_path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                json.loads(line)
