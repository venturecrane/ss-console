"""The verified reply binding: each guard has a test that fails without it.

A turn no inbound opened (a job's completion handoff, a one-shot cron turn) may
answer one earlier email. The broker decides everything the agent could abuse:
the email must be a received message in this mailbox's Inbox (never a draft,
junk, sent, or this mailbox's own), its replyTo may not point anywhere else, its
sender must be one the seat may reply to (re-read at send), the reply goes to
that sender by Graph's own derivation, and it happens once, durably, with the
claim spent only by a POST.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import bound_replies
from workspace_broker.audit_ledger import LedgerWriter
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
INBOX = "INBOXFOLDERID"
MATTER = "b041dd06-30a4-4c1f-912b-27724bd77a64"


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = datetime.now(timezone.utc)
RECEIVED = _iso(NOW - timedelta(hours=3))

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
    """A mailbox holding one message and a Sent Items folder."""

    def __init__(self, **message_over) -> None:
        self.message = {
            "id": GRAPH_ID,
            "from": {"emailAddress": {"address": ADMIN}},
            "replyTo": [],
            "conversationId": "CONV-1",
            "receivedDateTime": RECEIVED,
            "isDraft": False,
            "internetMessageId": IMID,
            "parentFolderId": INBOX,
            **message_over,
        }
        self.sent: list[dict] = []
        self.calls: list[tuple[str, str]] = []
        self.present = True
        self.post_status: int | None = None
        self.fail_reply_source_get = False
        self.post_lands_anyway = False
        self.inbox_status: int | None = None

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.calls.append((request.method, url))
        if url.endswith("/token"):
            return _Response(json.dumps({"access_token": "tok", "expires_in": 3600}))
        decoded = urllib.parse.unquote(url)
        if request.method == "GET" and "/mailFolders/inbox?" in url:
            if self.inbox_status is not None:
                raise urllib.error.HTTPError(url, self.inbox_status, "x", {}, None)  # type: ignore[arg-type]
            return _Response(json.dumps({"id": INBOX}))
        if request.method == "GET" and "/mailFolders/sentitems/messages" in url:
            if "conversationId eq" in decoded:
                since = decoded.split("sentDateTime ge ", 1)[1].split(" ", 1)[0]
                hits = [m for m in self.sent if m["sentDateTime"] >= since]
                return _Response(json.dumps({"value": hits}))
            return _Response(json.dumps({"value": []}))
        if request.method == "GET" and "internetMessageId eq" in decoded:
            return _Response(json.dumps({"value": [self.message] if self.present else []}))
        if request.method == "GET" and f"/messages/{GRAPH_ID}" in url:
            if self.fail_reply_source_get and "parentFolderId" not in decoded:
                raise urllib.error.HTTPError(url, 503, "busy", {}, None)  # type: ignore[arg-type]
            if not self.present:
                raise urllib.error.HTTPError(url, 404, "nf", {}, None)  # type: ignore[arg-type]
            return _Response(json.dumps(self.message))
        if request.method == "POST":
            if self.post_status is not None:
                if self.post_lands_anyway:
                    self.sent.append(
                        {"id": "S", "conversationId": "CONV-1", "sentDateTime": _iso(datetime.now(timezone.utc))}
                    )
                raise urllib.error.HTTPError(url, self.post_status, "x", {}, None)  # type: ignore[arg-type]
            self.sent.append({"id": "S", "conversationId": "CONV-1", "sentDateTime": _iso(NOW)})
            return _Response("")
        raise AssertionError(f"unexpected Graph call {request.method} {url}")

    def replies(self) -> list[str]:
        return [u for m, u in self.calls if m == "POST" and u.endswith("/reply")]


def _broker(tmp_path: Path, mailbox: FakeMailbox, yaml_text: str = YAML, *, inbound_row: bool = True) -> Broker:
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
    broker.ledger = LedgerWriter(broker.audit_db_path)
    if inbound_row and not _rows(broker, "INBOUND_RECEIVED"):
        broker.ledger.append(
            {
                "action_type": "INBOUND_RECEIVED",
                "actor": "operator",
                "metadata": json.dumps({"vendor_message_id": GRAPH_ID}),
            }
        )
    broker.msgraph = MsGraphOps(cred, customer, read_credential_path=read, opener=mailbox, sleep=lambda _s: None)
    return broker


def _rows(broker: Broker, action_type: str) -> list[dict]:
    conn = sqlite3.connect(broker.audit_db_path)
    try:
        conn.execute("SELECT 1 FROM audit_log LIMIT 1")
        rows = conn.execute("SELECT metadata FROM audit_log WHERE action_type=?", (action_type,)).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [json.loads(r[0] or "{}") for r in rows]


def _call(broker: Broker, action: str, binding: dict, **extra) -> dict:
    return broker.handle({"action": action, "binding": binding, **extra}, peer_pid=GATEWAY_PID, peer_uid=AGENT_UID)


def _send(broker: Broker, binding: dict) -> dict:
    return _call(broker, "msgraph_reply_bound", binding, payload={"comment": "Received."}, session_id="sess-1")


MSG = {"kind": "message", "internet_message_id": IMID}
BY_GRAPH_ID = {"kind": "message", "graph_message_id": GRAPH_ID}


def _job(tmp_path: Path, path: list[str], requester: str = ADMIN) -> str:
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
    for step in path:
        ledger.record(job, step, {})
    return job


def _ack_in_thread(box: FakeMailbox) -> None:
    box.sent.append({"id": "ACK", "conversationId": "CONV-1", "sentDateTime": _iso(NOW - timedelta(hours=2))})


# -- the happy paths (Law 12: the refusals below mean nothing if nothing passes)


def test_a_verified_message_binds_names_its_sender_and_is_audited(tmp_path: Path) -> None:
    broker = _broker(tmp_path, FakeMailbox())
    out = _call(broker, "msgraph_reply_bind", MSG)
    assert out["bound"] is True and out["sender"] == ADMIN and out["graph_message_id"] == GRAPH_ID
    assert [r["outcome"] for r in _rows(broker, "REPLY_BINDING")] == ["bound"]


def test_a_bound_reply_goes_to_the_source_message_once(tmp_path: Path) -> None:
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    out = _send(broker, MSG)
    assert out["ok"] is True and out["recipients"] == [ADMIN]
    assert len(box.replies()) == 1 and GRAPH_ID in urllib.parse.unquote(box.replies()[0])
    dispatched = _rows(broker, "CONFIRM_SEND_DISPATCHED")
    assert dispatched[-1]["verb"] == "msgraph_reply_bound"
    assert dispatched[-1]["reply_binding"] == f"message:{IMID}"


def test_the_graph_id_resolves_the_same_email(tmp_path: Path) -> None:
    out = _call(_broker(tmp_path, FakeMailbox()), "msgraph_reply_bind", BY_GRAPH_ID)
    assert out["bound"] is True and out["internet_message_id"] == IMID


# -- what counts as an email a reply may bind to


def test_a_reply_to_elsewhere_is_refused(tmp_path: Path) -> None:
    """Graph's /reply goes to replyTo. FALSIFIER: drop the replyTo check."""
    box = FakeMailbox(replyTo=[{"emailAddress": {"address": STRANGER}}])
    out = _call(_broker(tmp_path, box), "msgraph_reply_bind", MSG)
    assert out["bound"] is False and "replyTo" in out["reason"]


def test_a_reply_to_naming_the_sender_is_fine(tmp_path: Path) -> None:
    box = FakeMailbox(replyTo=[{"emailAddress": {"address": ADMIN}}])
    assert _call(_broker(tmp_path, box), "msgraph_reply_bind", MSG)["bound"] is True


@pytest.mark.parametrize("folder", ["JUNKFOLDER", "DELETEDITEMS", "SENTITEMS", "OUTBOX"])
def test_a_message_outside_the_inbox_is_refused(tmp_path: Path, folder: str) -> None:
    box = FakeMailbox(parentFolderId=folder)
    assert _call(_broker(tmp_path, box), "msgraph_reply_bind", MSG)["bound"] is False
    assert _call(_broker(tmp_path, box), "msgraph_reply_bind", BY_GRAPH_ID)["bound"] is False


def test_a_message_this_mailbox_sent_is_refused(tmp_path: Path) -> None:
    box = FakeMailbox(**{"from": {"emailAddress": {"address": MAILBOX}}})
    out = _call(_broker(tmp_path, box), "msgraph_reply_bind", MSG)
    assert out["bound"] is False and "this mailbox itself" in out["reason"]


def test_a_draft_is_never_a_message_to_answer(tmp_path: Path) -> None:
    box = FakeMailbox(isDraft=True)
    assert _call(_broker(tmp_path, box), "msgraph_reply_bind", MSG)["bound"] is False
    assert _call(_broker(tmp_path, box), "msgraph_reply_bind", BY_GRAPH_ID)["bound"] is False


def test_an_email_not_in_the_mailbox_is_refused(tmp_path: Path) -> None:
    box = FakeMailbox()
    box.present = False
    assert _call(_broker(tmp_path, box), "msgraph_reply_bind", MSG)["bound"] is False
    assert _call(_broker(tmp_path, box), "msgraph_reply_bind", BY_GRAPH_ID)["bound"] is False


def test_a_lookup_answering_for_another_message_is_refused(tmp_path: Path) -> None:
    other = "AAMkSOMETHINGELSE0002="
    box = FakeMailbox(id=other)
    broker = _broker(tmp_path, box)
    broker.ledger.append(
        {"action_type": "INBOUND_RECEIVED", "actor": "operator", "metadata": json.dumps({"vendor_message_id": other})}
    )
    out = _call(broker, "msgraph_reply_bind", BY_GRAPH_ID)
    assert out["bound"] is False and "different message" in out["reason"]


def test_a_graph_id_with_path_characters_is_refused(tmp_path: Path) -> None:
    out = _call(
        _broker(tmp_path, FakeMailbox()),
        "msgraph_reply_bind",
        {"kind": "message", "graph_message_id": "AAMk/../x+yyyyyyyyyy"},
    )
    assert out["bound"] is False


# -- answered already


def test_a_second_bound_reply_is_refused_durably(tmp_path: Path) -> None:
    """FALSIFIER: drop the bound_replies claim and the second send goes out.
    Sent Items is cleared so only the durable claim can stop it."""
    box = FakeMailbox()
    _send(_broker(tmp_path, box), MSG)
    box.sent.clear()
    with pytest.raises(BindingRefused):
        _send(_broker(tmp_path, box), MSG)
    assert len(box.replies()) == 1


def test_both_spellings_of_one_email_share_one_claim(tmp_path: Path) -> None:
    box = FakeMailbox()
    _send(_broker(tmp_path, box), MSG)
    box.sent.clear()
    with pytest.raises(BindingRefused):
        _send(_broker(tmp_path, box), BY_GRAPH_ID)


def test_an_email_already_answered_from_the_mailbox_is_refused(tmp_path: Path) -> None:
    box = FakeMailbox()
    _ack_in_thread(box)
    out = _call(_broker(tmp_path, box), "msgraph_reply_bind", MSG)
    assert out["bound"] is False and "already been answered" in out["reason"]


def test_an_earlier_send_in_the_thread_does_not_count(tmp_path: Path) -> None:
    box = FakeMailbox()
    box.sent.append({"id": "S0", "conversationId": "CONV-1", "sentDateTime": _iso(NOW - timedelta(days=2))})
    assert _call(_broker(tmp_path, box), "msgraph_reply_bind", MSG)["bound"] is True


def test_an_email_the_ledger_says_was_answered_is_refused(tmp_path: Path) -> None:
    broker = _broker(tmp_path, FakeMailbox())
    broker.ledger.append(
        {"action_type": "REPLY_SENT", "actor": "operator", "metadata": json.dumps({"in_reply_to": GRAPH_ID})}
    )
    out = _call(broker, "msgraph_reply_bind", MSG)
    assert out["bound"] is False and "already answered" in out["reason"]


def test_an_email_with_no_arrival_record_is_refused(tmp_path: Path) -> None:
    out = _call(_broker(tmp_path, FakeMailbox(), inbound_row=False), "msgraph_reply_bind", MSG)
    assert out["bound"] is False and "no record of that email arriving" in out["reason"]


def test_an_old_email_is_refused(tmp_path: Path) -> None:
    box = FakeMailbox(receivedDateTime=_iso(NOW - timedelta(days=30)))
    out = _call(_broker(tmp_path, box), "msgraph_reply_bind", MSG)
    assert out["bound"] is False and "older than" in out["reason"]


def test_an_ordinary_reply_after_a_bound_one_is_refused(tmp_path: Path) -> None:
    """A held inbound reply released after the bound reply must not answer twice."""
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    _send(broker, MSG)
    with pytest.raises(Exception):
        broker.handle(
            {"action": "msgraph_reply", "payload": {"message_id": GRAPH_ID, "comment": "again"}},
            peer_pid=GATEWAY_PID,
            peer_uid=AGENT_UID,
        )
    assert len(box.replies()) == 1


# -- the roster, and the caller naming nobody


def test_a_sender_off_the_roster_is_refused(tmp_path: Path) -> None:
    box = FakeMailbox(**{"from": {"emailAddress": {"address": STRANGER}}})
    out = _call(_broker(tmp_path, box), "msgraph_reply_bind", MSG)
    assert out["bound"] is False and "inbound_allow_from" in out["reason"]
    with pytest.raises(BindingRefused):
        _send(_broker(tmp_path, box), MSG)
    assert box.replies() == []
    assert "send_refused" in [r["outcome"] for r in _rows(_broker(tmp_path, box), "REPLY_BINDING")]


def test_the_roster_is_reread_at_send_time(tmp_path: Path) -> None:
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    assert _call(broker, "msgraph_reply_bind", MSG)["bound"] is True
    broker.customer_path.write_text(NO_ROSTER_YAML)
    with pytest.raises(BindingRefused):
        _send(broker, MSG)
    assert box.replies() == []


def test_the_caller_cannot_name_a_recipient(tmp_path: Path) -> None:
    out = _call(_broker(tmp_path, FakeMailbox()), "msgraph_reply_bind", {**MSG, "to": STRANGER})
    assert out["bound"] is False
    out = _call(_broker(tmp_path, FakeMailbox()), "msgraph_reply_bound", MSG, payload={"comment": "hi", "to": STRANGER})
    assert out["recipients"] == [ADMIN]


def test_the_binding_verbs_are_gateway_only(tmp_path: Path) -> None:
    broker = _broker(tmp_path, FakeMailbox())
    for action in ("msgraph_reply_bind", "msgraph_reply_bound"):
        with pytest.raises(PermissionError):
            broker.handle({"action": action, "binding": MSG}, peer_pid=9999, peer_uid=AGENT_UID)


# -- the two-phase claim


def test_a_failure_before_the_post_leaves_the_reply_unspent(tmp_path: Path) -> None:
    """The reply's own source fetch fails (a GET, before any POST): no claim is
    taken, so a retry can still send. FALSIFIER: claim before the transmit."""
    box = FakeMailbox()
    box.fail_reply_source_get = True
    broker = _broker(tmp_path, box)
    with pytest.raises(Exception):
        _send(broker, MSG)
    assert not bound_replies.claimed(broker.audit_db_path, f"message:{IMID}")
    assert box.replies() == []
    box.fail_reply_source_get = False
    assert _send(_broker(tmp_path, box), MSG)["recipients"] == [ADMIN]


def test_a_transport_failure_at_the_post_keeps_the_claim_as_unknown(tmp_path: Path) -> None:
    box = FakeMailbox()
    box.post_status = 503
    broker = _broker(tmp_path, box)
    with pytest.raises(Exception):
        _send(broker, MSG)
    conn = sqlite3.connect(broker.audit_db_path)
    outcome = conn.execute("SELECT outcome FROM bound_replies").fetchone()[0]
    conn.close()
    assert outcome == "unknown"
    box.post_status = None
    box.sent.clear()
    with pytest.raises(BindingRefused):
        _send(_broker(tmp_path, box), MSG)


def test_a_graph_4xx_at_the_post_releases_the_claim(tmp_path: Path) -> None:
    """Graph refused the request (4xx) and nothing reached Sent Items: the one
    reply is not silently spent. FALSIFIER: settle every failure 'unknown'."""
    box = FakeMailbox()
    box.post_status = 403
    broker = _broker(tmp_path, box)
    with pytest.raises(Exception):
        _send(broker, MSG)
    assert not bound_replies.claimed(broker.audit_db_path, f"message:{IMID}")
    assert "send_released" in [r["outcome"] for r in _rows(broker, "REPLY_BINDING")]
    box.post_status = None
    assert _send(_broker(tmp_path, box), MSG)["recipients"] == [ADMIN]


def test_a_4xx_whose_reply_reached_sent_items_stays_spent(tmp_path: Path) -> None:
    box = FakeMailbox()
    box.post_status = 400
    box.post_lands_anyway = True
    broker = _broker(tmp_path, box)
    with pytest.raises(Exception):
        _send(broker, MSG)
    assert bound_replies.claimed(broker.audit_db_path, f"message:{IMID}")
    assert "send_unknown" in [r["outcome"] for r in _rows(broker, "REPLY_BINDING")]


def test_a_mailbox_that_cannot_be_read_at_bind_time_is_audited(tmp_path: Path) -> None:
    box = FakeMailbox()
    box.inbox_status = 503
    broker = _broker(tmp_path, box)
    with pytest.raises(Exception):
        _call(broker, "msgraph_reply_bind", MSG)
    assert "bind_error" in [r["outcome"] for r in _rows(broker, "REPLY_BINDING")]


# -- demand jobs


def test_a_finished_demand_job_replies_to_its_requester_once(tmp_path: Path) -> None:
    job = _job(tmp_path, ["running", "delivered"])
    box = FakeMailbox()
    _ack_in_thread(box)  # the request turn's acknowledgment must not block it
    broker = _broker(tmp_path, box)
    assert _send(broker, {"kind": "demand_job", "job_id": job})["recipients"] == [ADMIN]
    assert DemandLedger(broker.audit_db_path, tmp_path / "q").read(job)["reply_sent_at"]
    with pytest.raises(BindingRefused):
        _send(_broker(tmp_path, box), {"kind": "demand_job", "job_id": job})
    assert len(box.replies()) == 1


def test_a_failed_job_never_replies_to_the_client(tmp_path: Path) -> None:
    """A failed job is resumable and SMD's to resolve; the requester is told
    nothing. FALSIFIER: put failed back in REPLYABLE_DEMAND_STATES."""
    job = _job(tmp_path, ["failed"])
    box = FakeMailbox()
    out = _call(_broker(tmp_path, box), "msgraph_reply_bind", {"kind": "demand_job", "job_id": job})
    assert out["bound"] is False and "Send nothing to anyone" in out["reason"]
    with pytest.raises(BindingRefused):
        _send(_broker(tmp_path, box), {"kind": "demand_job", "job_id": job})
    assert box.replies() == []


def test_failed_then_resumed_then_delivered_replies_once(tmp_path: Path) -> None:
    job = _job(tmp_path, ["failed"])
    box = FakeMailbox()
    ledger = DemandLedger(str(tmp_path / "audit.db"), tmp_path / "q")
    ledger.record(job, "running", {})
    ledger.record(job, "delivered", {})
    _send(_broker(tmp_path, box), {"kind": "demand_job", "job_id": job})
    with pytest.raises(BindingRefused):
        _send(_broker(tmp_path, box), {"kind": "demand_job", "job_id": job})
    assert len(box.replies()) == 1


def test_the_ledgers_reply_mark_is_honored(tmp_path: Path) -> None:
    """The job record already shows this outcome replied to (a reply sent by an
    earlier broker, say): nothing is sent and the binding claim is given back.
    FALSIFIER: ignore mark_replied's return value."""
    job = _job(tmp_path, ["running", "delivered"])
    DemandLedger(str(tmp_path / "audit.db"), tmp_path / "q").mark_replied(job, "1:delivered")
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    with pytest.raises(Exception):
        _send(broker, {"kind": "demand_job", "job_id": job})
    assert box.replies() == []
    assert not bound_replies.claimed(broker.audit_db_path, f"demand_job:{job}:1:delivered")


def test_a_ledger_write_that_raises_gives_the_claim_back(tmp_path: Path, monkeypatch) -> None:
    """mark_replied raises (a locked or broken DB): nothing is sent, and the
    binding claim is released so a retry can still reply. FALSIFIER: drop the
    try/release and the claim stays spent with nothing sent."""
    job = _job(tmp_path, ["running", "delivered"])
    box = FakeMailbox()

    def boom(self, job_id, key="reply"):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(DemandLedger, "mark_replied", boom)
    broker = _broker(tmp_path, box)
    with pytest.raises(sqlite3.OperationalError):
        _send(broker, {"kind": "demand_job", "job_id": job})
    assert box.replies() == []
    assert not bound_replies.claimed(broker.audit_db_path, f"demand_job:{job}:1:delivered")
    monkeypatch.undo()
    assert _send(_broker(tmp_path, box), {"kind": "demand_job", "job_id": job})["recipients"] == [ADMIN]


def test_a_released_demand_reply_clears_the_ledger_mark(tmp_path: Path) -> None:
    job = _job(tmp_path, ["running", "delivered"])
    box = FakeMailbox()
    box.post_status = 403
    with pytest.raises(Exception):
        _send(_broker(tmp_path, box), {"kind": "demand_job", "job_id": job})
    assert DemandLedger(str(tmp_path / "audit.db"), tmp_path / "q").read(job)["reply_key"] is None
    box.post_status = None
    assert _send(_broker(tmp_path, box), {"kind": "demand_job", "job_id": job})["recipients"] == [ADMIN]


@pytest.mark.parametrize("path", [[], ["running"]])
def test_an_unfinished_demand_job_cannot_reply(tmp_path: Path, path: list[str]) -> None:
    job = _job(tmp_path, path)
    out = _call(_broker(tmp_path, FakeMailbox()), "msgraph_reply_bind", {"kind": "demand_job", "job_id": job})
    assert out["bound"] is False and "has not ended" in out["reason"]


def test_a_demand_reply_only_reaches_the_requester(tmp_path: Path) -> None:
    job = _job(tmp_path, ["running", "delivered"], requester="other@firm.example")
    out = _call(_broker(tmp_path, FakeMailbox()), "msgraph_reply_bind", {"kind": "demand_job", "job_id": job})
    assert out["bound"] is False and "requested the job" in out["reason"]


def test_an_unknown_job_is_refused(tmp_path: Path) -> None:
    out = _call(
        _broker(tmp_path, FakeMailbox()),
        "msgraph_reply_bind",
        {"kind": "demand_job", "job_id": "01J0000000000000000000000Z"},
    )
    assert out["bound"] is False


# -- drafting jobs (the demand job's rules exactly, on the drafting ledger)


def _drafting_job(tmp_path: Path, path: list[str], requester: str = ADMIN) -> str:
    from workspace_broker.drafting_ledger import DraftingLedger

    ledger = DraftingLedger(str(tmp_path / "audit.db"), tmp_path / "dq")
    job = ledger.submit(
        {
            "matter": {"id": MATTER, "number": "900201"},
            "file_to": None,
            "requested_by": requester,
            "request_ref": IMID,
            "request_text": "Draft the mediation brief.",
            "document_class": "mediation_brief",
        }
    )
    for step in path:
        ledger.record(job, step, {})
    return job


@pytest.mark.parametrize("ending", ["delivered", "held"])
def test_a_finished_drafting_job_replies_to_its_requester_once(tmp_path: Path, ending: str) -> None:
    from workspace_broker.drafting_ledger import DraftingLedger

    job = _drafting_job(tmp_path, ["running", ending])
    box = FakeMailbox()
    _ack_in_thread(box)
    broker = _broker(tmp_path, box)
    assert _send(broker, {"kind": "drafting_job", "job_id": job})["recipients"] == [ADMIN]
    assert DraftingLedger(broker.audit_db_path, tmp_path / "dq").read(job)["reply_key"] == f"1:{ending}"
    with pytest.raises(BindingRefused):
        _send(_broker(tmp_path, box), {"kind": "drafting_job", "job_id": job})
    assert len(box.replies()) == 1


def test_a_failed_drafting_job_never_replies_to_the_client(tmp_path: Path) -> None:
    """FALSIFIER: let failed bind for drafting_job and the firm hears about our own fault."""
    job = _drafting_job(tmp_path, ["failed"])
    box = FakeMailbox()
    out = _call(_broker(tmp_path, box), "msgraph_reply_bind", {"kind": "drafting_job", "job_id": job})
    assert out["bound"] is False and "Send nothing to anyone" in out["reason"]
    with pytest.raises(BindingRefused):
        _send(_broker(tmp_path, box), {"kind": "drafting_job", "job_id": job})
    assert box.replies() == []


@pytest.mark.parametrize("path", [[], ["running"]])
def test_an_unfinished_drafting_job_cannot_reply(tmp_path: Path, path: list[str]) -> None:
    job = _drafting_job(tmp_path, path)
    out = _call(_broker(tmp_path, FakeMailbox()), "msgraph_reply_bind", {"kind": "drafting_job", "job_id": job})
    assert out["bound"] is False and "has not ended" in out["reason"]


def test_a_drafting_reply_only_reaches_the_requester(tmp_path: Path) -> None:
    job = _drafting_job(tmp_path, ["running", "delivered"], requester="other@firm.example")
    out = _call(_broker(tmp_path, FakeMailbox()), "msgraph_reply_bind", {"kind": "drafting_job", "job_id": job})
    assert out["bound"] is False and "requested the job" in out["reason"]


def test_a_drafting_job_id_is_not_read_from_the_demand_ledger(tmp_path: Path) -> None:
    """The two lanes' ids never cross. FALSIFIER: read drafting_job from the
    demand ledger and a delivered demand job could answer as a drafting job."""
    demand = _job(tmp_path, ["running", "delivered"])
    out = _call(_broker(tmp_path, FakeMailbox()), "msgraph_reply_bind", {"kind": "drafting_job", "job_id": demand})
    assert out["bound"] is False and "no drafting job" in out["reason"]


# -- chronology jobs (2026-10-07: a held chronology's wake had no binding, and the
# Operator wrote a new email to the requester AND the matter's attorney)


def _chronology_job(tmp_path: Path, path: list[str], requester: str = ADMIN) -> str:
    from workspace_broker.medchron_ledger import MedchronLedger

    ledger = MedchronLedger(str(tmp_path / "audit.db"), tmp_path / "mq")
    job = ledger.submit(
        {
            "matter": {"id": MATTER, "number": "900201", "title": "Doe v. Roe"},
            "units": [{"client_name": "Jane Doe", "surname": "Doe", "dob": "01/01/1980"}],
            "incident": {"date": "2025-01-01", "source": "matter_layout"},
            "requested_by": requester,
            "request_ref": IMID,
        },
        remaining=1000,
    )
    for step in path:
        ledger.record(job, step, {})
    return job


@pytest.mark.parametrize("ending", ["delivered", "held"])
def test_a_finished_chronology_job_replies_to_its_requester_once(tmp_path: Path, ending: str) -> None:
    job = _chronology_job(tmp_path, ["running", ending])
    box = FakeMailbox()
    _ack_in_thread(box)
    assert _send(_broker(tmp_path, box), {"kind": "medchron_job", "job_id": job})["recipients"] == [ADMIN]
    with pytest.raises(BindingRefused):
        _send(_broker(tmp_path, box), {"kind": "medchron_job", "job_id": job})
    assert len(box.replies()) == 1


def test_a_held_then_resumed_then_delivered_chronology_replies_for_each_outcome(tmp_path: Path) -> None:
    from workspace_broker.medchron_ledger import MedchronLedger

    job = _chronology_job(tmp_path, ["running", "held"])
    box = FakeMailbox()
    _ack_in_thread(box)
    _send(_broker(tmp_path, box), {"kind": "medchron_job", "job_id": job})
    ledger = MedchronLedger(str(tmp_path / "audit.db"), tmp_path / "mq")
    ledger.record(job, "running", {})
    ledger.record(job, "delivered", {})
    _send(_broker(tmp_path, box), {"kind": "medchron_job", "job_id": job})
    assert len(box.replies()) == 2


def test_a_failed_chronology_job_never_replies_to_the_client(tmp_path: Path) -> None:
    job = _chronology_job(tmp_path, ["failed"])
    box = FakeMailbox()
    out = _call(_broker(tmp_path, box), "msgraph_reply_bind", {"kind": "medchron_job", "job_id": job})
    assert out["bound"] is False and "Send nothing to anyone" in out["reason"]
    assert box.replies() == []


def test_a_chronology_reply_only_reaches_the_requester(tmp_path: Path) -> None:
    """FALSIFIER: drop the requester check and the bind answers whoever sent the
    request email, even when the job names someone else."""
    job = _chronology_job(tmp_path, ["running", "held"], requester="attorney@firm.example")
    out = _call(_broker(tmp_path, FakeMailbox()), "msgraph_reply_bind", {"kind": "medchron_job", "job_id": job})
    assert out["bound"] is False and "requested the job" in out["reason"]


def test_a_chronology_job_id_is_not_read_from_another_ledger(tmp_path: Path) -> None:
    demand = _job(tmp_path, ["running", "delivered"])
    out = _call(_broker(tmp_path, FakeMailbox()), "msgraph_reply_bind", {"kind": "medchron_job", "job_id": demand})
    assert out["bound"] is False and "no chronology job" in out["reason"]


def _sendmails(box: FakeMailbox) -> list[str]:
    return [u for m, u in box.calls if m == "POST" and u.endswith("/sendMail")]


# -- negotiation notices (2026-10-09). The negotiation watch emails the firm once
# per new offer: each notice binds as ONE new email to the authored scheduled
# recipient under the broker's subject; the job itself never binds.

NEG_YAML = YAML + (
    "personas:\n"
    "  - slug: operator\n"
    "    skills:\n"
    "      - name: negotiation-watch\n"
    "        enabled: true\n"
    "        initiation: {manual: false, scheduled: true, webhook: false}\n"
    "        settings:\n"
    f"          scheduled_recipients: '{ADMIN}'\n"
    "          monthly_budget_usd: 25\n"
)


def _negotiation_notice(tmp_path: Path, state: str = "delivered") -> tuple[str, str]:
    from workspace_broker.negotiation_ledger import NegotiationLedger

    ledger = NegotiationLedger(str(tmp_path / "audit.db"), tmp_path / "nq")
    job = ledger.submit(
        {
            "trigger": "scheduled",
            "requester": ADMIN,
            "message_ref": "scheduled:2026-10-12T08",
            "request_text": "scheduled weekday negotiation watch run",
            "matter_statuses": ["Open"],
            "negotiation_design": "",
            "firm_words": [],
            "per_job_cap_usd": 10,
            "monthly_budget_usd": 25,
        }
    )
    ledger.record(job, "running", {})
    notice = {"matter_id": "m-1", "matter_number": "200123", "status": "entered", "text": "New offer on matter 200123."}
    row = ledger.record(job, state, {"notices": [notice]} if state == "delivered" else {})
    return job, (row["notice_ids"] or [""])[0]


def test_a_negotiation_notice_sends_one_new_email_under_the_brokers_subject(tmp_path: Path) -> None:
    from workspace_broker.negotiation_ledger import NegotiationLedger

    _job_id, notice = _negotiation_notice(tmp_path)
    box = FakeMailbox()
    broker = _broker(tmp_path, box, NEG_YAML)
    bound = _call(broker, "msgraph_reply_bind", {"kind": "negotiation_job", "job_id": notice})
    assert bound["bound"] is True and bound["mode"] == "new_message"
    assert bound["sender"] == ADMIN and bound["subject"] == "New offer, matter 200123"
    _call(
        broker,
        "msgraph_reply_bound",
        {"kind": "negotiation_job", "job_id": notice},
        payload={"comment": "New offer on matter 200123.", "to": [STRANGER], "subject": "x"},
        session_id="sess-1",
    )
    assert len(_sendmails(box)) == 1 and box.replies() == []
    assert NegotiationLedger(broker.audit_db_path, tmp_path / "nq").read(notice)["reply_key"] == "1:delivered"
    with pytest.raises(BindingRefused):
        _send(_broker(tmp_path, box, NEG_YAML), {"kind": "negotiation_job", "job_id": notice})
    assert len(_sendmails(box)) == 1


def test_a_negotiation_job_itself_never_binds(tmp_path: Path) -> None:
    job, _notice = _negotiation_notice(tmp_path)
    broker = _broker(tmp_path, FakeMailbox(), NEG_YAML)
    out = _call(broker, "msgraph_reply_bind", {"kind": "negotiation_job", "job_id": job})
    assert out["bound"] is False and "announced by its notices" in out["reason"]


def test_a_failed_negotiation_job_is_never_told(tmp_path: Path) -> None:
    job, _ = _negotiation_notice(tmp_path, state="failed")
    broker = _broker(tmp_path, FakeMailbox(), NEG_YAML)
    out = _call(broker, "msgraph_reply_bind", {"kind": "negotiation_job", "job_id": job})
    assert out["bound"] is False and "failed on SMD's side" in out["reason"]


def test_a_notice_whose_recipient_is_no_longer_authored_sends_nothing(tmp_path: Path) -> None:
    _job_id, notice = _negotiation_notice(tmp_path)
    box = FakeMailbox()
    other = NEG_YAML.replace(f"scheduled_recipients: '{ADMIN}'", "scheduled_recipients: 'other@firm.example'")
    out = _call(_broker(tmp_path, box, other), "msgraph_reply_bind", {"kind": "negotiation_job", "job_id": notice})
    assert out["bound"] is False and "nothing was sent" in out["reason"]
    assert _sendmails(box) == []
