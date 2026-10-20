"""Record-before-send (send_intent.py, 2026-10-20).

The defect: the transmit verbs called the vendor FIRST and wrote the audit row
AFTER, so a ledger failure between the two left a sent email with no row and
told the caller the send had failed, which is the answer that buys a duplicate.

The falsifiers here fail on the old code:

* ``test_a_failed_intent_append_means_the_vendor_is_never_called_*``: the old
  code wrote nothing before the send, so a ledger that refuses its first write
  still let the vendor call through.
* ``test_a_failed_confirm_append_after_a_send_returns_success_with_a_flag_*``:
  the old code let the ledger's exception escape after the vendor accepted the
  message, so the caller saw a failure.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_agentmail_send as am
import test_msgraph_send as mg
import test_participant_fence as pfx
from workspace_broker.agentmail_ops import AgentMailRefused, AgentMailTransportError, collect_recipients
from workspace_broker.msgraph_ops import MsGraphRefused
from workspace_broker.send_intent import CONFIRM_FLAG, INTENT_ACTION_TYPE, INTENT_KEY, NOT_SENT
from workspace_broker.transmit_verbs import dispatch_transmit

BODY = "SECRET-BODY-TEXT-zq81"


class FailingLedger:
    """Records rows like the verb tests' ledger, and raises the way the real
    sqlite writer does under contention, for the action types named."""

    def __init__(self, fail_on: tuple[str, ...]) -> None:
        self.rows: list[dict] = []
        self._fail_on = fail_on

    def append(self, row: dict) -> str:
        if row["action_type"] in self._fail_on:
            raise sqlite3.OperationalError("database is locked")
        self.rows.append(row)
        return f"row-{len(self.rows)}"


def _meta(row: dict) -> dict:
    return json.loads(row["metadata"])


def _agentmail_posts(http: am.FakeHTTP) -> list[str]:
    return [url for method, url, _ in http.calls if method == "POST" and "/messages/send" in url]


def _graph_posts(http: mg.FakeGraph) -> list[str]:
    return [url for method, url, _ in http.calls if method == "POST" and "sendMail" in url]


def _agentmail_send(broker, **extra) -> dict:
    return broker.handle(
        {"action": "agentmail_send", "payload": {"to": ["scott@smd.services"], "text": BODY}, **extra},
        peer_pid=am.GATEWAY_PID,
        peer_uid=am.AGENT_UID,
    )


def _graph_send(broker, **extra) -> dict:
    return broker.handle(
        {"action": "msgraph_send", "payload": {"to": ["scott@smd.services"], "body_text": BODY}, **extra},
        peer_pid=mg.GATEWAY_PID,
        peer_uid=mg.AGENT_UID,
    )


# --- the falsifiers ---------------------------------------------------------


def test_a_failed_intent_append_means_the_vendor_is_never_called_agentmail(tmp_path: Path) -> None:
    http = am.FakeHTTP()
    broker = am._broker(tmp_path, http)
    broker.ledger = FailingLedger((INTENT_ACTION_TYPE,))
    with pytest.raises(AgentMailRefused) as refused:
        _agentmail_send(broker)
    assert str(refused.value) == NOT_SENT
    assert _agentmail_posts(http) == []
    assert broker.ledger.rows == []


def test_a_failed_intent_append_means_the_vendor_is_never_called_msgraph(tmp_path: Path) -> None:
    http = mg.FakeGraph()
    broker = mg._broker(tmp_path, http)
    broker.ledger = FailingLedger((INTENT_ACTION_TYPE,))
    with pytest.raises(MsGraphRefused) as refused:
        _graph_send(broker)
    assert str(refused.value).startswith("not sent")
    assert _graph_posts(http) == []
    assert broker.ledger.rows == []


def test_a_failed_confirm_append_after_a_send_returns_success_with_a_flag_agentmail(tmp_path: Path) -> None:
    http = am.FakeHTTP()
    broker = am._broker(tmp_path, http)
    broker.ledger = FailingLedger(("CONFIRM_SEND_DISPATCHED",))
    response = _agentmail_send(broker)
    assert response["ok"] is True
    assert response[CONFIRM_FLAG] is False
    assert response["message_id"] == "msg_generated"
    assert len(_agentmail_posts(http)) == 1
    # What is left is the orphan intent the reconciler pages on.
    assert [row["action_type"] for row in broker.ledger.rows] == [INTENT_ACTION_TYPE]


def test_a_failed_confirm_append_after_a_send_returns_success_with_a_flag_msgraph(tmp_path: Path) -> None:
    http = mg.FakeGraph()
    broker = mg._broker(tmp_path, http)
    broker.ledger = FailingLedger(("CONFIRM_SEND_DISPATCHED",))
    response = _graph_send(broker)
    assert response["ok"] is True
    assert response[CONFIRM_FLAG] is False
    assert len(_graph_posts(http)) == 1
    assert [row["action_type"] for row in broker.ledger.rows] == [INTENT_ACTION_TYPE]


# --- the shape of the pair --------------------------------------------------


def test_the_intent_row_carries_the_joins_and_never_the_body(tmp_path: Path) -> None:
    broker = am._broker(tmp_path, am.FakeHTTP())
    response = _agentmail_send(
        broker,
        session_id="sess-1",
        matter_ref="matter-9",
        audit_extra={"skill_name": "deadline-miss-escalator", "routing_leg": "central"},
    )
    assert CONFIRM_FLAG not in response
    intent, confirm = broker.ledger.rows
    assert intent["action_type"] == INTENT_ACTION_TYPE
    assert intent["matter_ref"] == "matter-9"
    assert intent["skill_name"] == "deadline-miss-escalator"
    meta = _meta(intent)
    assert meta["verb"] == "agentmail_send"
    assert meta["session_id"] == "sess-1"
    assert meta["recipients"] == ["scott@smd.services"]
    assert meta["routing_leg"] == "central"
    assert len(meta["input_digest"]) == 64
    assert BODY not in json.dumps(broker.ledger.rows)
    # The intent attempted the same content the confirm says went out.
    assert _meta(confirm)["input_digest"] == meta["input_digest"]
    assert _meta(confirm)[INTENT_KEY] == "row-1"
    assert confirm["skill_name"] == "deadline-miss-escalator"


def test_a_failed_send_settles_its_intent_too(tmp_path: Path) -> None:
    broker = am._broker(tmp_path, am.FakeHTTP())

    def boom(_payload):
        raise AgentMailTransportError("503 from the vendor")

    with pytest.raises(AgentMailTransportError):
        _dispatch(broker, send=boom)
    intent, failed = broker.ledger.rows
    assert intent["action_type"] == INTENT_ACTION_TYPE
    assert failed["action_type"] == "CONFIRM_SEND_FAILED"
    assert _meta(failed)["outcome"] == "transport_error"
    assert _meta(failed)[INTENT_KEY] == "row-1"


def test_a_failure_row_that_cannot_land_does_not_mask_the_vendor_error(tmp_path: Path) -> None:
    """The vendor's own exception is what the caller acts on; a sqlite error in
    its place would hide whether the send may have gone."""
    broker = am._broker(tmp_path, am.FakeHTTP())
    broker.ledger = FailingLedger(("CONFIRM_SEND_FAILED",))

    def boom(_payload):
        raise AgentMailTransportError("503 from the vendor")

    with pytest.raises(AgentMailTransportError):
        _dispatch(broker, send=boom)


def test_an_unexpected_send_error_still_settles_the_intent_by_type_only(tmp_path: Path) -> None:
    broker = am._broker(tmp_path, am.FakeHTTP())

    def boom(_payload):
        raise KeyError("text the ledger must not hold")

    with pytest.raises(KeyError):
        _dispatch(broker, send=boom)
    failed = broker.ledger.rows[-1]
    assert failed["action_type"] == "CONFIRM_SEND_FAILED"
    assert _meta(failed)["outcome"] == "error"
    assert _meta(failed)["reason"] == "KeyError"
    assert "must not hold" not in json.dumps(broker.ledger.rows)


def test_no_ledger_means_no_send(tmp_path: Path) -> None:
    """``append_send_row`` returns "" with no ledger; that is not a recorded
    intent, so the send is refused rather than made unrecorded."""
    broker = am._broker(tmp_path, am.FakeHTTP())
    broker.ledger = None
    called: list[dict] = []
    with pytest.raises(AgentMailRefused) as refused:
        _dispatch(broker, send=lambda payload: called.append(payload) or {})
    assert str(refused.value) == NOT_SENT
    assert called == []


def test_a_fence_refusal_writes_no_intent(tmp_path: Path) -> None:
    """The participant fence refuses before the vendor is near, so there is no
    intent to settle: its refusal row stands alone, exactly as before. On the
    real sqlite writer, because that is where an orphan would be read from."""
    box = pfx.FakeMailbox()
    broker = pfx._broker(tmp_path, box)
    pfx._refused(broker, box, lambda: pfx._send(broker, [pfx.ALICE]))
    assert pfx._rows(broker, INTENT_ACTION_TYPE) == []


def test_on_the_real_ledger_the_confirm_names_the_intent_row_id(tmp_path: Path) -> None:
    """The join is the hash-chained writer's own ULID, not a test double's."""
    box = pfx.FakeMailbox()
    broker = pfx._broker(tmp_path, box)
    assert pfx._send(broker, [pfx.SCOTT])["ok"] is True
    conn = sqlite3.connect(broker.audit_db_path)
    try:
        rows = conn.execute("SELECT id, action_type, metadata FROM audit_log ORDER BY rowid").fetchall()
    finally:
        conn.close()
    assert [r[1] for r in rows] == [INTENT_ACTION_TYPE, "CONFIRM_SEND_DISPATCHED"]
    assert json.loads(rows[1][2])[INTENT_KEY] == rows[0][0]


def test_a_vendor_refusal_settles_its_intent(tmp_path: Path) -> None:
    """Past the fence, the vendor's own policy check can still refuse: the
    intent was written, and the refusal row names it."""
    broker = am._broker(tmp_path, am.FakeHTTP())
    with pytest.raises(AgentMailRefused):
        broker.handle(
            {"action": "agentmail_send", "payload": {"to": ["nobody@example.com"], "text": BODY}},
            peer_pid=am.GATEWAY_PID,
            peer_uid=am.AGENT_UID,
        )
    intent, failed = broker.ledger.rows
    assert intent["action_type"] == INTENT_ACTION_TYPE
    assert _meta(failed)["outcome"] == "refused"
    assert _meta(failed)[INTENT_KEY] == "row-1"


def _dispatch(broker, *, send) -> dict:
    return dispatch_transmit(
        broker,
        "agentmail_send",
        {"payload": {"to": ["scott@smd.services"], "text": BODY}},
        send=send,
        reply=send,
        refused=AgentMailRefused,
        transport=AgentMailTransportError,
        attempted_for_send=collect_recipients,
        identity_key="inbox_id",
        channel="agentmail",
    )
