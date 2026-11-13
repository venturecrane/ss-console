"""Staff send-as through a Google Workspace mailbox (ADR 0089 section 9).

The question every test answers: could a message leave the Captain's Gmail
mailbox without him approving that exact draft by an emailed reply? Each test
counts Gmail ``users.messages.send`` calls, the one call that puts his name on
a message. The mailbox is modelled at the Google client boundary
(``gmail_send_as.google_service``); everything above it, the acts, the store,
the audited AgentMail dispatch and the participant fence, is the real code.
"""

from __future__ import annotations

import base64
import email
import email.policy
import json
import re
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import gmail_send_as, send_as_acts
from workspace_broker.agentmail_ops import AgentMailOps
from workspace_broker.operations import WorkspaceOperations
from workspace_broker.send_as_acts import SEND_AS_TTL_SECONDS
from workspace_broker.server import Broker

GATEWAY_PID = 42
AGENT_UID = 1000
SEAT = "smd-office-test"
SEAT_INBOX = "smd-office-test@agentmail.to"
ALIAS = "scott@smd.services"
PRIMARY = "smdurgan@smdurgan.com"
ADMIN = "ops-admin@smd.services"
OUTSIDE = "client@client.example"
THREAD = "18c0ffee"
INBOUND_ID = "in-1@client.example"
REPLY_ID = "reply-1@mail.gmail.com"

SEAT_YAML = f"""
connectors:
  Email:
    adapter: agentmail
    enabled: true
google_auth:
  mode: dwd
  subject: {PRIMARY}
  scopes:
    - https://www.googleapis.com/auth/gmail.modify
  managed_mailboxes:
    - address: {PRIMARY}
      send_as:
        - {ALIAS}
        - {PRIMARY}
personas:
  - name: smd-operations
    entitlements:
      exposure:
        external_send_as_staff: confirm
scope:
  inbound_allow_from:
    - {ALIAS}
    - {PRIMARY}
  admins:
    - {ADMIN}
  staff_send_as:
    - address: {ALIAS}
      name: Scott Durgan
      via: gmail
      mailbox: {PRIMARY}
    - address: {PRIMARY}
      name: Scott Durgan
      via: gmail
      mailbox: {PRIMARY}
"""


# ---------------------------------------------------------------------------
# The mailbox, at the Google client boundary
# ---------------------------------------------------------------------------


class _Call:
    def __init__(self, fn) -> None:
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeGmail:
    """One Gmail mailbox: messages with labels and headers, a thread, and the
    three calls the transport makes. ``sends`` is what every test counts."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sends: list[dict[str, Any]] = []
        self.store: dict[str, dict[str, Any]] = {}
        self.search_error: Exception | None = None
        self.send_error: Exception | None = None
        self.subjects: list[str] = []
        self.add(
            "g-in-1",
            THREAD,
            {"From": f"Client <{OUTSIDE}>", "To": ALIAS, "Delivered-To": PRIMARY, "Message-ID": f"<{INBOUND_ID}>"},
            labels=("INBOX",),
        )

    def add(self, gid: str, thread: str, headers: dict[str, str], labels: tuple[str, ...] = ()) -> None:
        self.store[gid] = {
            "id": gid,
            "threadId": thread,
            "labelIds": list(labels),
            "payload": {"headers": [{"name": k, "value": v} for k, v in headers.items()]},
        }

    def owner_replies(self, rfc: str = REPLY_ID, *, extra: dict[str, str] | None = None, gid: str = "g-reply") -> None:
        """The Captain's own reply to the approval email, in his SENT mail."""
        self.add(gid, "t-approval", {"From": PRIMARY, "Message-ID": f"<{rfc}>", **(extra or {})}, labels=("SENT",))

    # The service object: service.users().messages()/threads()...
    def users(self):
        return self

    def messages(self):
        return _Messages(self)

    def threads(self):
        return _Threads(self)


class _Threads:
    def __init__(self, box: FakeGmail) -> None:
        self.box = box

    def get(self, userId, id, format, metadataHeaders):
        assert userId == "me"
        msgs = [m for m in self.box.store.values() if m["threadId"] == id]
        return _Call(lambda: {"id": id, "messages": msgs})


class _Messages:
    def __init__(self, box: FakeGmail) -> None:
        self.box = box

    def send(self, userId, body):
        assert userId == "me"

        def run():
            if self.box.send_error is not None:
                raise self.box.send_error
            with self.box.lock:
                n = len(self.box.sends) + 1
                self.box.sends.append(body)
                parsed = email.message_from_bytes(base64.urlsafe_b64decode(body["raw"]), policy=email.policy.default)
                gid = f"g-sent-{n}"
                headers = {k: str(v) for k, v in parsed.items()}
                self.box.add(gid, body.get("threadId") or f"t-new-{n}", headers, labels=("SENT",))
            return {"id": gid, "threadId": body.get("threadId") or f"t-new-{n}", "labelIds": ["SENT"]}

        return _Call(run)

    def list(self, userId, q, maxResults):
        assert userId == "me"

        def run():
            if self.box.search_error is not None:
                raise self.box.search_error
            match = re.fullmatch(r"rfc822msgid:(\S+) in:sent", q)
            assert match, q
            wanted = match.group(1)
            hits = [
                {"id": m["id"], "threadId": m["threadId"]}
                for m in self.box.store.values()
                if "SENT" in m["labelIds"]
                and any(
                    h["name"].lower() == "message-id" and h["value"].strip("<>") == wanted
                    for h in m["payload"]["headers"]
                )
            ]
            return {"messages": hits} if hits else {"resultSizeEstimate": 0}

        return _Call(run)

    def get(self, userId, id, format, metadataHeaders):
        assert userId == "me"
        return _Call(lambda: self.box.store[id])


class FakeAgentMail:
    """AgentMail's HTTP API, for the seat's own inbox (the real AgentMailOps
    runs against it). ``sends`` are approval emails and notices."""

    def __init__(self) -> None:
        self.sends: list[dict] = []

    def __call__(self, request, timeout=None):
        body = json.loads(request.data.decode()) if request.data else None
        payload: dict[str, Any] = {"inboxes": [{"inbox_id": SEAT_INBOX}]}
        if "/messages/send" in request.full_url:
            self.sends.append(body or {})
            payload = {"message_id": f"<am-{len(self.sends)}@agentmail.to>", "thread_id": "am-thread"}
        return _Response(json.dumps(payload))


class _Response:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> bytes:
        return self._text.encode()

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False


class RecordingLedger:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def append(self, row: dict) -> str:
        self.rows.append(row)
        return f"row-{len(self.rows)}"


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def box(monkeypatch: pytest.MonkeyPatch) -> FakeGmail:
    mailbox = FakeGmail()
    seen: list[str] = []

    def factory(api, version, credential_path, customer_path, subject=""):
        assert (api, version) == ("gmail", "v1")
        seen.append(subject)
        return mailbox

    monkeypatch.setattr(gmail_send_as, "google_service", factory)
    mailbox.subjects = seen
    return mailbox


def _broker(tmp_path: Path, yaml_text: str = SEAT_YAML) -> tuple[Broker, FakeAgentMail]:
    customer = tmp_path / "customer.yaml"
    customer.write_text(yaml_text)
    am_credential = tmp_path / "agentmail.json"
    am_credential.write_text("am_test_key")
    google_credential = tmp_path / "google.json"
    google_credential.write_text("{}")
    http = FakeAgentMail()
    broker = Broker.__new__(Broker)
    broker.customer_slug = SEAT
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.ledger = RecordingLedger()
    broker.msgraph = None
    broker.agentmail = AgentMailOps(am_credential, customer, SEAT, opener=http)
    broker.customer_path = customer
    broker.credential_path = google_credential
    broker.operations = WorkspaceOperations(google_credential, customer)
    broker.audit_db_path = str(tmp_path / "audit.db")
    broker.send_as_now = Clock()
    return broker, http


def _propose(broker: Broker, **over: Any) -> dict:
    payload = {
        "from": ALIAS,
        "to": [OUTSIDE],
        "cc": [],
        "subject": "Re: Next steps",
        "body_text": "Thanks, Tuesday works.",
        "thread_id": THREAD,
    }
    request: dict[str, Any] = {
        "action": "send_as_propose",
        "session_id": "sess-1",
        "instructed_by": ALIAS,
        "payload": payload,
        "gate_pass": {"fabrication": True, "matter": True, "identifier": True, "matters": []},
        "tainted": False,
        "sources": [],
    }
    for key, value in over.items():
        if key in payload:
            payload[key] = value
        else:
            request[key] = value
    return broker.handle(request, peer_pid=GATEWAY_PID, peer_uid=AGENT_UID)


def _decide(broker: Broker, tag: str, decision: str = "send", *, by: str = PRIMARY, rfc: str = REPLY_ID) -> dict:
    return broker.handle(
        {
            "action": "send_as_decide",
            "tag_or_act_id": tag,
            "decision": decision,
            "decided_by": by,
            "internet_message_id": f"<{rfc}>",
            "instruction": "make it shorter" if decision == "change" else None,
        },
        peer_pid=GATEWAY_PID,
        peer_uid=AGENT_UID,
    )


def _sent_mime(box: FakeGmail, index: int = -1) -> email.message.EmailMessage:
    raw = box.sends[index]["raw"]
    return email.message_from_bytes(base64.urlsafe_b64decode(raw), policy=email.policy.default)


def _action_types(broker: Broker) -> list[str]:
    return [row["action_type"] for row in broker.ledger.rows]


# ---------------------------------------------------------------------------
# The approved path, once
# ---------------------------------------------------------------------------


def test_an_approved_draft_sends_once_from_the_alias_the_thread_was_received_at(tmp_path: Path, box) -> None:
    broker, am = _broker(tmp_path)
    proposed = _propose(broker)
    assert proposed["ok"] is True, proposed
    assert len(box.sends) == 0
    # The approval email went from the seat's AgentMail inbox to him, with no buttons.
    assert len(am.sends) == 1 and am.sends[0]["to"] == [ALIAS]
    assert "Replies will come to you." in am.sends[0]["text"]
    assert "html" not in am.sends[0] or "/approve?t=" not in am.sends[0].get("html", "")
    box.owner_replies()
    out = _decide(broker, proposed["tag"])
    assert out["status"] == "DISPATCHED", out
    assert len(box.sends) == 1
    sent = _sent_mime(box)
    assert sent["From"].addresses[0].addr_spec == ALIAS
    assert sent["In-Reply-To"] == f"<{INBOUND_ID}>"
    assert sent["X-SMD-Audit-Row"]
    assert sent["Reply-To"] is None and sent["Bcc"] is None
    assert box.sends[0]["threadId"] == THREAD
    assert set(box.subjects) == {PRIMARY}
    assert "SEND_AS_SENT" in _action_types(broker)
    sent_row = json.loads(next(r for r in broker.ledger.rows if r["action_type"] == "SEND_AS_SENT")["metadata"])
    assert sent_row["gmail_message_id"] == "g-sent-1" and sent_row["audit_row_token"] == str(sent["X-SMD-Audit-Row"])


def test_a_reply_from_his_other_address_is_the_same_approver(tmp_path: Path, box) -> None:
    broker, _ = _broker(tmp_path)
    tag = _propose(broker)["tag"]
    box.owner_replies()
    assert _decide(broker, tag, by=ALIAS)["status"] == "DISPATCHED"
    assert len(box.sends) == 1


# ---------------------------------------------------------------------------
# Falsifiers: each one is a way a send could happen without his approval
# ---------------------------------------------------------------------------


def test_no_approval_no_send(tmp_path: Path, box) -> None:
    broker, _ = _broker(tmp_path)
    assert _propose(broker)["ok"] is True
    box.owner_replies()
    assert len(box.sends) == 0


def test_a_mutated_stored_payload_is_refused(tmp_path: Path, box) -> None:
    broker, _ = _broker(tmp_path)
    proposed = _propose(broker)
    db = sqlite3.connect(broker.audit_db_path)
    payload = json.loads(db.execute("SELECT payload_json FROM send_as_acts").fetchone()[0])
    payload["to"] = ["attacker@evil.example"]
    db.execute("UPDATE send_as_acts SET payload_json = ?", (json.dumps(payload),))
    db.commit()
    db.close()
    box.owner_replies()
    out = _decide(broker, proposed["tag"])
    assert out["status"] == "REFUSED" and "no longer matches" in out["reason"]
    assert len(box.sends) == 0


def test_the_digest_covers_thread_and_from(tmp_path: Path, box) -> None:
    broker, _ = _broker(tmp_path)
    _propose(broker)
    db = sqlite3.connect(broker.audit_db_path)
    row = db.execute("SELECT payload_json, digest FROM send_as_acts").fetchone()
    db.close()
    msg = json.loads(row[0])
    assert msg["thread_id"] == THREAD and msg["in_reply_to"] == f"<{INBOUND_ID}>" and msg["from"] == ALIAS
    for field, value in (("thread_id", "otherthread"), ("in_reply_to", "<x@y>"), ("from_name", "Someone Else")):
        assert send_as_acts.digest_of({**msg, field: value}) != row[1]


def test_an_old_tag_after_a_revision_is_superseded(tmp_path: Path, box) -> None:
    broker, _ = _broker(tmp_path)
    old = _propose(broker)["tag"]
    box.owner_replies()
    assert _decide(broker, old, "change")["status"] == "REVISED"
    new = _propose(broker, body_text="Tuesday works. Thanks.")["tag"]
    assert new != old
    box.owner_replies(rfc="reply-2@mail.gmail.com", gid="g-reply-2")
    assert _decide(broker, old, rfc="reply-2@mail.gmail.com")["status"] == "SUPERSEDED"
    assert len(box.sends) == 0


def test_an_expired_draft_sends_nothing(tmp_path: Path, box) -> None:
    broker, _ = _broker(tmp_path)
    tag = _propose(broker)["tag"]
    box.owner_replies()
    broker.send_as_now.t += SEND_AS_TTL_SECONDS + 1
    assert _decide(broker, tag)["status"] == "EXPIRED"
    assert len(box.sends) == 0


def test_a_wrong_approver_cannot_send(tmp_path: Path, box) -> None:
    broker, _ = _broker(tmp_path)
    tag = _propose(broker)["tag"]
    box.owner_replies()
    assert _decide(broker, tag, by="someone@smd.services")["status"] == "REFUSED"
    assert len(box.sends) == 0


def test_an_administrator_cannot_send_but_may_cancel(tmp_path: Path, box) -> None:
    broker, _ = _broker(tmp_path)
    tag = _propose(broker)["tag"]
    box.owner_replies()
    assert _decide(broker, tag, by=ADMIN)["status"] == "REFUSED"
    assert len(box.sends) == 0
    assert _decide(broker, tag, "cancel", by=ADMIN)["status"] == "CANCELLED"
    assert _decide(broker, tag)["status"] == "REFUSED"
    assert len(box.sends) == 0


def test_an_answer_absent_from_his_sent_mail_is_refused(tmp_path: Path, box) -> None:
    broker, _ = _broker(tmp_path)
    tag = _propose(broker)["tag"]
    out = _decide(broker, tag)
    assert out["status"] == "REFUSED" and "not in the approver's Sent mail" in out["reason"]
    assert len(box.sends) == 0


def test_an_answer_carrying_the_broker_audit_header_is_refused(tmp_path: Path, box) -> None:
    broker, _ = _broker(tmp_path)
    tag = _propose(broker)["tag"]
    box.owner_replies(extra={"X-SMD-Audit-Row": "01HXXXXXXXXXXXXXXXXXXXXXXX"})
    out = _decide(broker, tag)
    assert out["status"] == "REFUSED" and "sent by the Operator" in out["reason"]
    assert len(box.sends) == 0


def test_an_answer_that_is_a_gmail_id_the_broker_recorded_is_refused(tmp_path: Path, box) -> None:
    broker, _ = _broker(tmp_path)
    first = _propose(broker)["tag"]
    box.owner_replies()
    assert _decide(broker, first)["status"] == "DISPATCHED"
    second = _propose(broker, body_text="A second note.")["tag"]
    # Present the broker's own sent message (its header stripped) as the answer.
    sent = box.store["g-sent-1"]
    sent["payload"]["headers"] = [h for h in sent["payload"]["headers"] if h["name"] != "X-SMD-Audit-Row"]
    own = next(h["value"] for h in sent["payload"]["headers"] if h["name"].lower() == "message-id").strip("<>")
    out = _decide(broker, second, rfc=own)
    assert out["status"] == "REFUSED" and "sent by the Operator" in out["reason"]
    assert len(box.sends) == 1


def test_a_failed_sent_search_refuses(tmp_path: Path, box) -> None:
    broker, _ = _broker(tmp_path)
    tag = _propose(broker)["tag"]
    box.owner_replies()
    box.search_error = RuntimeError("backend unavailable")
    out = _decide(broker, tag)
    assert out["status"] == "REFUSED" and "could not be confirmed" in out["reason"]
    assert len(box.sends) == 0


def test_an_answer_with_no_message_id_refuses(tmp_path: Path, box) -> None:
    broker, _ = _broker(tmp_path)
    tag = _propose(broker)["tag"]
    box.owner_replies()
    out = _decide(broker, tag, rfc="")
    assert out["status"] == "REFUSED"
    assert len(box.sends) == 0


def test_a_replayed_answer_sends_nothing_more(tmp_path: Path, box) -> None:
    broker, _ = _broker(tmp_path)
    tag = _propose(broker)["tag"]
    box.owner_replies()
    assert _decide(broker, tag)["status"] == "DISPATCHED"
    assert _decide(broker, tag)["status"] == "REFUSED"
    assert len(box.sends) == 1


def test_racing_decides_send_exactly_once(tmp_path: Path, box) -> None:
    broker, _ = _broker(tmp_path)
    tag = _propose(broker)["tag"]
    box.owner_replies()
    results: list[dict] = []
    barrier = threading.Barrier(6)

    def run() -> None:
        barrier.wait()
        results.append(_decide(broker, tag))

    threads = [threading.Thread(target=run) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r["status"] for r in results).count("DISPATCHED") == 1
    assert len(box.sends) == 1


def test_a_refused_gmail_send_is_reported_not_sent_and_never_retried(tmp_path: Path, box) -> None:
    class HttpError(Exception):
        class resp:
            status = 403

    broker, am = _broker(tmp_path)
    tag = _propose(broker)["tag"]
    box.owner_replies()
    box.send_error = HttpError("forbidden")
    out = _decide(broker, tag)
    assert out["status"] == "FAILED"
    assert am.sends[-1]["subject"].startswith("Not sent:")
    box.send_error = None
    assert _decide(broker, tag)["status"] == "REFUSED"
    assert len(box.sends) == 0


# ---------------------------------------------------------------------------
# From is chosen in code; links are off; the generic surface cannot send
# ---------------------------------------------------------------------------


def test_a_from_the_thread_was_not_received_at_is_refused(tmp_path: Path, box) -> None:
    broker, am = _broker(tmp_path)
    out = _propose(broker, **{"from": PRIMARY})
    assert out["ok"] is False and f"received at {ALIAS}" in out["reason"]
    assert len(am.sends) == 0 and len(box.sends) == 0


def test_a_thread_received_at_no_authored_address_proposes_nothing(tmp_path: Path, box) -> None:
    broker, am = _broker(tmp_path)
    box.store["g-in-1"]["payload"]["headers"] = [
        {"name": "From", "value": OUTSIDE},
        {"name": "To", "value": "someone-else@smd.services"},
        {"name": "Message-ID", "value": f"<{INBOUND_ID}>"},
    ]
    out = _propose(broker)
    assert out["ok"] is False and "not received at any address" in out["reason"]
    assert len(am.sends) == 0


def test_a_new_message_carries_the_requested_authored_from(tmp_path: Path, box) -> None:
    broker, _ = _broker(tmp_path)
    tag = _propose(broker, **{"from": PRIMARY, "thread_id": None})["tag"]
    box.owner_replies()
    assert _decide(broker, tag)["status"] == "DISPATCHED"
    sent = _sent_mime(box)
    assert sent["From"].addresses[0].addr_spec == PRIMARY and sent["In-Reply-To"] is None
    assert "threadId" not in box.sends[0]


def test_approval_links_are_refused_on_a_gmail_seat(tmp_path: Path, box, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMD_APPROVE_BASE_URL", "https://seat.example")
    broker, am = _broker(tmp_path)
    proposed = _propose(broker)
    assert "/approve?t=" not in json.dumps(am.sends[-1])
    out = broker.handle(
        {"action": "send_as_decide_link", "token": f"{proposed['act_id']}.send.9999999999.sig"},
        peer_pid=GATEWAY_PID,
        peer_uid=AGENT_UID,
    )
    assert out["status"] == "REFUSED" and "reply only" in out["reason"]
    assert len(box.sends) == 0


def test_workspace_gmail_send_stays_unsupported(tmp_path: Path, box) -> None:
    broker, _ = _broker(tmp_path)
    assert not broker.operations.supports("workspace_gmail_send")
    assert "workspace_gmail_send" not in broker.operations.supported_operations()
    with pytest.raises(ValueError):
        broker.operations.dispatch("workspace_gmail_send", {"to": OUTSIDE, "subject": "x", "body": "y"})
    assert len(box.sends) == 0


def test_a_mixed_transport_roster_refuses_everything(tmp_path: Path, box) -> None:
    mixed = SEAT_YAML.replace(
        f"    - address: {PRIMARY}\n      name: Scott Durgan\n      via: gmail\n",
        f"    - address: {PRIMARY}\n      name: Scott Durgan\n      via: msgraph\n",
    )
    broker, am = _broker(tmp_path, mixed)
    with pytest.raises(Exception):
        _propose(broker)
    assert len(am.sends) == 0 and len(box.sends) == 0


def test_a_reply_naming_the_sent_message_notifies_him(tmp_path: Path, box) -> None:
    broker, am = _broker(tmp_path)
    tag = _propose(broker)["tag"]
    box.owner_replies()
    assert _decide(broker, tag)["status"] == "DISPATCHED"
    own = next(h["value"] for h in box.store["g-sent-1"]["payload"]["headers"] if h["name"].lower() == "message-id")
    before = len(am.sends)
    out = broker.handle(
        {
            "action": "send_as_match_reply",
            "conversation_id": "am-thread-unrelated",
            "internet_message_id": "<theirs@client.example>",
            "from": OUTSIDE,
            "in_reply_to": own,
            "references": f"<{INBOUND_ID}> {own}",
        },
        peer_pid=GATEWAY_PID,
        peer_uid=AGENT_UID,
    )
    assert out["matched"] is True and out["tag"] == tag
    assert len(am.sends) == before + 1 and am.sends[-1]["subject"].startswith("Reply received:")
    assert len(box.sends) == 1
