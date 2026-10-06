"""The verified reply binding: each guard has a test that fails without it.

A turn no inbound opened (a job's completion handoff, a one-shot cron turn) may
answer one earlier email. The broker decides everything the agent could abuse:
the email must be a received message in this mailbox (never a draft), its
sender must be one the seat may reply to (re-read at send), the reply goes to
that sender by Graph's own derivation, and it happens once, durably.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker.demand_ledger import DemandLedger
from workspace_broker.msgraph_ops import MsGraphOps
from workspace_broker.reply_binding import BindingRefused
from workspace_broker.server import Broker

GATEWAY_PID = 42
AGENT_UID = 1000
MAILBOX = "operator@firm.example"
ADMIN = "admin@firm.example"
STRANGER = "someone@elsewhere.example"
IMID = "<CA1x2y3z@mail.firm.example>"
GRAPH_ID = "AAMkSOURCEMESSAGE0001="
MATTER = "b041dd06-30a4-4c1f-912b-27724bd77a64"
RECEIVED = "2026-10-06T16:54:00Z"

YAML = f"""
connectors:
  Email:
    adapter: msgraph
    enabled: true
    msgraph_auth:
      mailbox: {MAILBOX}
scope:
  inbound_allow_from:
    - '@firm.example'
  admins:
    - {ADMIN}
"""
NO_ROSTER_YAML = f"""
connectors:
  Email:
    adapter: msgraph
    enabled: true
    msgraph_auth:
      mailbox: {MAILBOX}
scope: {{}}
"""


class _Response:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> bytes:
        return self._text.encode()

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False


class FakeMailbox:
    """A mailbox holding one received message and a Sent Items folder."""

    def __init__(self, *, sender: str = ADMIN, is_draft: bool = False, sent: list[dict] | None = None) -> None:
        self.sender = sender
        self.is_draft = is_draft
        self.sent = list(sent or [])
        self.calls: list[tuple[str, str]] = []
        self.present = True

    def _message(self) -> dict:
        return {
            "id": GRAPH_ID,
            "from": {"emailAddress": {"address": self.sender}},
            "conversationId": "CONV-1",
            "receivedDateTime": RECEIVED,
            "isDraft": self.is_draft,
            "internetMessageId": IMID,
        }

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.calls.append((request.method, url))
        if url.endswith("/token"):
            return _Response(json.dumps({"access_token": "tok", "expires_in": 3600}))
        decoded = urllib.parse.unquote(url)
        if request.method == "GET" and "/mailFolders/sentitems/messages" in url:
            if "conversationId eq" in decoded:
                return _Response(json.dumps({"value": self.sent}))
            return _Response(json.dumps({"value": []}))
        if request.method == "GET" and "internetMessageId eq" in decoded:
            return _Response(json.dumps({"value": [self._message()] if self.present else []}))
        if request.method == "GET" and f"/messages/{GRAPH_ID}" in url:
            if not self.present:
                raise urllib.error.HTTPError(url, 404, "nf", {}, None)  # type: ignore[arg-type]
            return _Response(json.dumps(self._message()))
        if request.method == "POST":
            # The reply: Graph derives the recipient from the source message.
            self.sent.append({"id": "S", "conversationId": "CONV-1", "sentDateTime": "2026-10-06T20:00:00Z"})
            return _Response("")
        raise AssertionError(f"unexpected Graph call {request.method} {url}")

    def replies(self) -> list[str]:
        return [u for m, u in self.calls if m == "POST" and u.endswith("/reply")]


class RecordingLedger:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def append(self, row: dict) -> str:
        self.rows.append(row)
        return f"row-{len(self.rows)}"


def _broker(tmp_path: Path, mailbox: FakeMailbox, yaml_text: str = YAML) -> Broker:
    customer = tmp_path / "customer.yaml"
    customer.write_text(yaml_text)
    cred = tmp_path / "msgraph.json"
    cred.write_text(json.dumps({"tenant_id": "t", "client_id": "send", "client_secret": "x"}))
    read = tmp_path / "msgraph-read.json"
    read.write_text(json.dumps({"tenant_id": "t", "client_id": "read", "client_secret": "y"}))
    broker = Broker.__new__(Broker)
    broker.customer_slug = "firm"
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.customer_path = customer
    broker.audit_db_path = str(tmp_path / "audit.db")
    broker.ledger = RecordingLedger()
    broker.msgraph = MsGraphOps(cred, customer, read_credential_path=read, opener=mailbox, sleep=lambda _s: None)
    return broker


def _call(broker: Broker, action: str, binding: dict, **extra) -> dict:
    return broker.handle({"action": action, "binding": binding, **extra}, peer_pid=GATEWAY_PID, peer_uid=AGENT_UID)


def _send(broker: Broker, binding: dict) -> dict:
    return _call(broker, "msgraph_reply_bound", binding, payload={"comment": "Received."}, session_id="sess-1")


MSG = {"kind": "message", "internet_message_id": IMID}
BY_GRAPH_ID = {"kind": "message", "graph_message_id": GRAPH_ID}


def _demand_job(tmp_path: Path, state: str = "delivered", requester: str = ADMIN) -> str:
    ledger = DemandLedger(str(tmp_path / "audit.db"), tmp_path / "q")
    job = ledger.submit(
        {
            "matter": {"id": MATTER, "number": "900201"},
            "file_to": None,
            "requested_by": requester,
            "request_ref": IMID,
            "request_text": "Gap audit and draft demand.",
            "deliverables": ["gap_audit", "demand"],
        }
    )
    path = {"submitted": [], "running": ["running"], "held": ["held"], "delivered": ["running", "delivered"]}
    path["failed"] = ["failed"]
    for step in path[state]:
        ledger.record(job, step, {})
    return job


# -- the happy paths (Law 12: the refusals below mean nothing if nothing passes)


def test_a_verified_message_binds_and_names_its_sender(tmp_path: Path) -> None:
    out = _call(_broker(tmp_path, FakeMailbox()), "msgraph_reply_bind", MSG)
    assert out["bound"] is True and out["sender"] == ADMIN


def test_a_bound_reply_goes_to_the_source_message_once(tmp_path: Path) -> None:
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    out = _send(broker, MSG)
    assert out["ok"] is True and out["recipients"] == [ADMIN]
    assert box.replies() == [box.replies()[0]] and GRAPH_ID in urllib.parse.unquote(box.replies()[0])
    assert json.loads(broker.ledger.rows[-1]["metadata"])["verb"] == "msgraph_reply_bound"


def test_the_graph_id_resolves_the_same_email(tmp_path: Path) -> None:
    out = _call(_broker(tmp_path, FakeMailbox()), "msgraph_reply_bind", BY_GRAPH_ID)
    assert out["bound"] is True and out["internet_message_id"] == IMID


# -- each guard


def test_a_second_bound_reply_is_refused_durably(tmp_path: Path) -> None:
    """FALSIFIER: drop the bound_replies claim and the second send goes out.
    The Sent Items check is disabled here so only the durable claim can stop it."""
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    _send(broker, MSG)
    box.sent.clear()  # as if Sent Items had not caught up
    with pytest.raises(BindingRefused):
        _send(_broker(tmp_path, box), MSG)  # a fresh broker: nothing held in memory
    assert len(box.replies()) == 1


def test_both_spellings_of_one_email_share_one_claim(tmp_path: Path) -> None:
    box = FakeMailbox()
    _send(_broker(tmp_path, box), MSG)
    box.sent.clear()
    with pytest.raises(BindingRefused):
        _send(_broker(tmp_path, box), BY_GRAPH_ID)


def test_an_email_already_answered_from_the_mailbox_is_refused(tmp_path: Path) -> None:
    """A reply by any path (the inbound turn, a released hold, a person in
    Outlook) counts. FALSIFIER: drop the Sent Items check."""
    box = FakeMailbox(sent=[{"id": "S0", "conversationId": "CONV-1", "sentDateTime": "2026-10-06T17:00:00Z"}])
    out = _call(_broker(tmp_path, box), "msgraph_reply_bind", MSG)
    assert out["bound"] is False and "already been answered" in out["reason"]


def test_an_earlier_send_in_the_thread_does_not_count(tmp_path: Path) -> None:
    box = FakeMailbox(sent=[{"id": "S0", "conversationId": "CONV-1", "sentDateTime": "2026-10-05T09:00:00Z"}])
    assert _call(_broker(tmp_path, box), "msgraph_reply_bind", MSG)["bound"] is True


def test_a_sender_off_the_roster_is_refused(tmp_path: Path) -> None:
    box = FakeMailbox(sender=STRANGER)
    out = _call(_broker(tmp_path, box), "msgraph_reply_bind", MSG)
    assert out["bound"] is False and "inbound_allow_from" in out["reason"]
    with pytest.raises(BindingRefused):
        _send(_broker(tmp_path, box), MSG)
    assert box.replies() == []


def test_the_roster_is_reread_at_send_time(tmp_path: Path) -> None:
    """Bound while rostered, de-rostered before the send: no reply."""
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    assert _call(broker, "msgraph_reply_bind", MSG)["bound"] is True
    broker.customer_path.write_text(NO_ROSTER_YAML)
    with pytest.raises(BindingRefused):
        _send(broker, MSG)
    assert box.replies() == []


def test_a_draft_is_never_a_message_to_answer(tmp_path: Path) -> None:
    """Anything with Mail.ReadWrite can write a draft with any From."""
    box = FakeMailbox(is_draft=True)
    assert _call(_broker(tmp_path, box), "msgraph_reply_bind", MSG)["bound"] is False
    assert _call(_broker(tmp_path, box), "msgraph_reply_bind", BY_GRAPH_ID)["bound"] is False


def test_an_email_not_in_the_mailbox_is_refused(tmp_path: Path) -> None:
    box = FakeMailbox()
    box.present = False
    assert _call(_broker(tmp_path, box), "msgraph_reply_bind", MSG)["bound"] is False
    assert _call(_broker(tmp_path, box), "msgraph_reply_bind", BY_GRAPH_ID)["bound"] is False


def test_the_caller_cannot_name_a_recipient(tmp_path: Path) -> None:
    """The binding is exact; a 'to' or 'sender' field is refused, and the
    payload's own 'to' never reaches the reply."""
    out = _call(_broker(tmp_path, FakeMailbox()), "msgraph_reply_bind", {**MSG, "to": STRANGER})
    assert out["bound"] is False
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    out = _call(broker, "msgraph_reply_bound", MSG, payload={"comment": "hi", "to": STRANGER})
    assert out["recipients"] == [ADMIN]


def test_the_binding_verbs_are_gateway_only(tmp_path: Path) -> None:
    broker = _broker(tmp_path, FakeMailbox())
    for action in ("msgraph_reply_bind", "msgraph_reply_bound"):
        with pytest.raises(PermissionError):
            broker.handle({"action": action, "binding": MSG}, peer_pid=9999, peer_uid=AGENT_UID)


# -- demand jobs


def test_a_finished_demand_job_replies_to_its_requester_once(tmp_path: Path) -> None:
    job = _demand_job(tmp_path, "delivered")
    # The request turn already acknowledged in the thread: that must not block.
    box = FakeMailbox(sent=[{"id": "ACK", "conversationId": "CONV-1", "sentDateTime": "2026-10-06T17:00:00Z"}])
    broker = _broker(tmp_path, box)
    assert _send(broker, {"kind": "demand_job", "job_id": job})["recipients"] == [ADMIN]
    assert DemandLedger(broker.audit_db_path, tmp_path / "q").read(job)["reply_sent_at"]
    with pytest.raises(BindingRefused):
        _send(_broker(tmp_path, box), {"kind": "demand_job", "job_id": job})
    assert len(box.replies()) == 1


@pytest.mark.parametrize("state", ["submitted", "running"])
def test_an_unfinished_demand_job_cannot_reply(tmp_path: Path, state: str) -> None:
    job = _demand_job(tmp_path, state)
    out = _call(_broker(tmp_path, FakeMailbox()), "msgraph_reply_bind", {"kind": "demand_job", "job_id": job})
    assert out["bound"] is False and "has not ended" in out["reason"]


@pytest.mark.parametrize("state", ["held", "failed"])
def test_a_held_or_failed_job_still_gets_its_reply(tmp_path: Path, state: str) -> None:
    job = _demand_job(tmp_path, state)
    out = _call(_broker(tmp_path, FakeMailbox()), "msgraph_reply_bind", {"kind": "demand_job", "job_id": job})
    assert out["bound"] is True


def test_a_demand_reply_only_reaches_the_requester(tmp_path: Path) -> None:
    """The email at request_ref was sent by someone else: refused."""
    job = _demand_job(tmp_path, "delivered", requester="other@firm.example")
    out = _call(_broker(tmp_path, FakeMailbox()), "msgraph_reply_bind", {"kind": "demand_job", "job_id": job})
    assert out["bound"] is False and "requested the job" in out["reason"]


def test_an_unknown_job_is_refused(tmp_path: Path) -> None:
    out = _call(
        _broker(tmp_path, FakeMailbox()),
        "msgraph_reply_bind",
        {"kind": "demand_job", "job_id": "01J0000000000000000000000Z"},
    )
    assert out["bound"] is False
