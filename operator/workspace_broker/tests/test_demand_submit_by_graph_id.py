"""Root-only demand submit by an email's Graph id (demand_verbs._resolve_request_graph_id).

A request answered before the demand lane deployed is known on the seat only by
its Graph id. Root may queue it; the broker resolves the email itself, so the
job's completion reply threads to it. Each test fails without its guard.
"""

from __future__ import annotations

import json
import sys
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.demand_ledger import DemandLedger
from workspace_broker.demand_verbs import DemandVerbs
from workspace_broker.msgraph_ops import MsGraphOps
from workspace_broker.server import Broker

GATEWAY_PID = 4242
AGENT_UID = 10000
MAILBOX = "operator@firm.example"
ADMIN = "admin@firm.example"
GRAPH_ID = "AAMkREQUESTMESSAGE01="
IMID = "<req1@mail.firm.example>"
INBOX = "INBOXFOLDERID"
MATTER = "b041dd06-30a4-4c1f-912b-27724bd77a64"

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
personas:
  - slug: operator
    skills:
      - name: demand-letter-drafter
        enabled: true
        settings:
          demand_allowance_per_cycle: 25
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
    def __init__(self, **over) -> None:
        self.message = {
            "id": GRAPH_ID,
            "from": {"emailAddress": {"address": ADMIN}},
            "replyTo": [],
            "conversationId": "CONV-1",
            "receivedDateTime": "2026-10-06T21:09:00Z",
            "isDraft": False,
            "internetMessageId": IMID,
            "parentFolderId": INBOX,
            **over,
        }

    def __call__(self, request, timeout=None):
        url = request.full_url
        if url.endswith("/token"):
            return _Response(json.dumps({"access_token": "tok", "expires_in": 3600}))
        if request.method == "GET" and "/mailFolders/inbox?" in url:
            return _Response(json.dumps({"id": INBOX}))
        if request.method == "GET" and f"/messages/{GRAPH_ID}" in urllib.parse.unquote(url):
            return _Response(json.dumps(self.message))
        raise AssertionError(f"unexpected Graph call {request.method} {url}")


def _broker(tmp_path: Path, box: FakeMailbox) -> Broker:
    customer = tmp_path / "customer.yaml"
    customer.write_text(YAML)
    cred = tmp_path / "msgraph.json"
    cred.write_text(json.dumps({"tenant_id": "t", "client_id": "send", "client_secret": "x"}))
    read = tmp_path / "msgraph-read.json"
    read.write_text(json.dumps({"tenant_id": "t", "client_id": "read", "client_secret": "y"}))
    db = str(tmp_path / "audit.db")
    writer = LedgerWriter(db)
    broker = Broker.__new__(Broker)
    broker.customer_slug = "firm"
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.customer_path = customer
    broker.audit_db_path = db
    broker.ledger = writer
    broker.msgraph = MsGraphOps(cred, customer, read_credential_path=read, opener=box, sleep=lambda _s: None)
    broker.demand = DemandVerbs(
        DemandLedger(db, tmp_path / "q"), customer_yaml=str(customer), audit_append=writer.append
    )
    return broker


def _envelope(**over):
    env = {
        "matter": {"id": MATTER, "number": "900201"},
        "file_to": None,
        "requested_by": ADMIN,
        "request_graph_id": GRAPH_ID,
        "request_text": "Gap audit and draft demand, please.",
        "deliverables": ["gap_audit", "demand"],
    }
    env.update(over)
    return env


def _submit(broker, *, pid=9999, uid=0, **over):
    return broker.handle({"action": "demand_job_submit", "envelope": _envelope(**over)}, peer_pid=pid, peer_uid=uid)


def test_root_resolves_the_email_and_queues_it(tmp_path: Path) -> None:
    broker = _broker(tmp_path, FakeMailbox())
    out = _submit(broker)
    assert out["accepted"] is True
    row = DemandLedger(broker.audit_db_path, tmp_path / "q").read(out["job_id"])
    assert row["request_ref"] == IMID and row["requester"] == ADMIN


def test_the_gateway_cannot_use_request_graph_id(tmp_path: Path) -> None:
    """FALSIFIER: drop the uid 0 check and the agent can queue any Inbox email."""
    broker = _broker(tmp_path, FakeMailbox())
    with pytest.raises(PermissionError):
        _submit(broker, pid=GATEWAY_PID, uid=AGENT_UID)


def test_a_requester_who_did_not_send_it_is_refused(tmp_path: Path) -> None:
    broker = _broker(tmp_path, FakeMailbox())
    out = _submit(broker, requested_by="other@firm.example")
    assert out["accepted"] is False and "sender of that email" in out["reason"]


def test_a_message_outside_the_inbox_is_refused(tmp_path: Path) -> None:
    broker = _broker(tmp_path, FakeMailbox(parentFolderId="SENTITEMS"))
    out = _submit(broker)
    assert out["accepted"] is False and "Inbox" in out["reason"]


def test_a_draft_or_self_sent_message_is_refused(tmp_path: Path) -> None:
    assert _submit(_broker(tmp_path, FakeMailbox(isDraft=True)))["accepted"] is False
    self_sent = FakeMailbox(**{"from": {"emailAddress": {"address": MAILBOX}}})
    assert _submit(_broker(tmp_path, self_sent), requested_by=MAILBOX)["accepted"] is False


def test_both_ids_at_once_is_refused(tmp_path: Path) -> None:
    out = _submit(_broker(tmp_path, FakeMailbox()), request_ref=IMID)
    assert out["accepted"] is False
