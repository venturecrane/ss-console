"""The participant fence: a firm person gets Operator mail only if they were on
the request it answers, or the firm authored them for the job sending it.

The incident (a law-firm seat, 2026-10-07): a held chronology's wake emailed an attorney who
was not on the requester's email. Each test pins one rule and fails without it;
the happy paths come first, because a refusal proves nothing if nothing passes.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import participant_fence as pf
from workspace_broker.agentmail_ops import AgentMailOps
from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.demand_ledger import DemandLedger
from workspace_broker.msgraph_ops import MsGraphOps, MsGraphRefused
from workspace_broker.pending_rule_store import PendingRuleStore
from workspace_broker.recipient_policy import sender_key
from workspace_broker.server import Broker

GATEWAY_PID = 42
AGENT_UID = 1000
MAILBOX = "operator@firm.example"
ALICE = "alice@firm.example"  # admin, rule routing, staff send-as
CARL = "carl@firm.example"  # admin
DANA = "dana@firm.example"  # paralegal, cc'd on the request
BRUNO = "bruno@firm.example"  # the attorney who was not on the request
PAULA = "paula@firm.example"  # on the central alert list
FAX = "fax@firm.example"  # the office scanner
SCOTT = "scott@smd.services"
CLIENT = "client@outside.example"
INBOX = "INBOXFOLDERID"

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
    - {SCOTT}
  admins: [{CARL}, {ALICE}, {SCOTT}]
  rule_requests_to: [{ALICE}, {SCOTT}]
  outbound_roster:
    - {{address: team@smd.services, class: firm_staff}}
    - {{address: {CLIENT}, class: client}}
  staff_send_as:
    - {{address: {ALICE}, name: Alice}}
  device_senders:
    - {{address: {FAX}, replies_to: {ALICE}}}
escalation:
  red_flag_recipients: [{PAULA}, {SCOTT}]
  failure_recipients: [{SCOTT}]
  case_alert_routing: {{mode: central}}
personas:
  - slug: operator
    skills:
      - name: statute-watch
        enabled: true
        settings: {{recipient: {ALICE}}}
      - name: retired-watch
        enabled: false
        settings: {{recipient: {BRUNO}}}
"""


def _graph_message(gid: str, sender: str, conversation: str, *, to=(MAILBOX,), cc=(), **over) -> dict:
    return {
        "id": gid,
        "from": {"emailAddress": {"address": sender}},
        "toRecipients": [{"emailAddress": {"address": a}} for a in to],
        "ccRecipients": [{"emailAddress": {"address": a}} for a in cc],
        "conversationId": conversation,
        "isDraft": False,
        "internetMessageId": f"<{gid}@mail.firm.example>",
        "parentFolderId": INBOX,
        "receivedDateTime": "2026-10-07T20:00:00Z",
        "replyTo": [],
        **over,
    }


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
    """A Graph mailbox holding a request thread and a few other emails."""

    def __init__(self) -> None:
        self.messages = {
            "REQ": _graph_message("REQ", ALICE, "C1", cc=(DANA,)),
            "REQ2": _graph_message("REQ2", DANA, "C1"),
            "OTHER": _graph_message("OTHER", BRUNO, "C2"),
            # A participant's email in ANOTHER conversation.
            "DANA_ELSE": _graph_message("DANA_ELSE", DANA, "C9"),
            "SMDREQ": _graph_message("SMDREQ", SCOTT, "C3"),
            "FAXSCAN": _graph_message("FAXSCAN", FAX, "C4"),
            "DRAFT": _graph_message("DRAFT", ALICE, "C5", isDraft=True),
            "OWN": _graph_message("OWN", MAILBOX, "C6"),
        }
        self.calls: list[tuple[str, str]] = []
        self.posts: list[str] = []
        self.fail_reads = False

    def __call__(self, request, timeout=None):
        url = request.full_url
        decoded = urllib.parse.unquote(url)
        self.calls.append((request.method, decoded))
        if url.endswith("/token"):
            return _Response(json.dumps({"access_token": "tok", "expires_in": 3600}))
        if request.method == "POST":
            self.posts.append(decoded)
            return _Response("")
        if self.fail_reads:
            raise urllib.error.HTTPError(url, 503, "busy", {}, None)  # type: ignore[arg-type]
        if "/mailFolders/inbox?" in url:
            return _Response(json.dumps({"id": INBOX}))
        if "/mailFolders/sentitems/messages" in url:
            return _Response(json.dumps({"value": []}))
        if "internetMessageId eq" in decoded:
            imid = decoded.split("internetMessageId eq '", 1)[1].split("'", 1)[0]
            hits = [m for m in self.messages.values() if m["internetMessageId"] == imid]
            return _Response(json.dumps({"value": hits}))
        gid = decoded.split("/messages/", 1)[1].split("?", 1)[0] if "/messages/" in decoded else ""
        if gid in self.messages:
            return _Response(json.dumps(self.messages[gid]))
        raise urllib.error.HTTPError(url, 404, "not found", {}, None)  # type: ignore[arg-type]

    def message_reads(self) -> list[str]:
        return [u for m, u in self.calls if m == "GET" and "/messages/" in u and "sentitems" not in u]


def _broker(tmp_path: Path, box: FakeMailbox, yaml_text: str = YAML) -> Broker:
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
    broker.establishment = SimpleNamespace(pending=PendingRuleStore(broker.audit_db_path))
    broker.msgraph = MsGraphOps(cred, customer, read_credential_path=read, opener=box, sleep=lambda _s: None)
    return broker


def _anchor(gid: str) -> dict:
    return {"kind": "graph_message", "graph_message_id": gid}


def _send(broker: Broker, to, *, cc=(), bcc=(), anchor=None, lane=None) -> dict:
    request: dict = {"action": "msgraph_send", "payload": {"to": list(to), "body_text": "x"}}
    if cc:
        request["payload"]["cc"] = list(cc)
    if bcc:
        request["payload"]["bcc"] = list(bcc)
    if anchor is not None:
        request["anchor"] = anchor
    if lane is not None:
        request["lane"] = lane
    return broker.handle(request, peer_pid=GATEWAY_PID, peer_uid=AGENT_UID)


def _reply(broker: Broker, target: str, *, anchor=None, to=None) -> dict:
    payload = {"message_id": target, "comment": "Received."}
    if to:
        payload["to"] = to
    request: dict = {"action": "msgraph_reply", "payload": payload}
    if anchor is not None:
        request["anchor"] = anchor
    return broker.handle(request, peer_pid=GATEWAY_PID, peer_uid=AGENT_UID)


def _rows(broker: Broker, action_type: str) -> list[dict]:
    import sqlite3

    conn = sqlite3.connect(broker.audit_db_path)
    try:
        rows = conn.execute(
            "SELECT metadata FROM audit_log WHERE action_type=? ORDER BY rowid", (action_type,)
        ).fetchall()
    finally:
        conn.close()
    return [json.loads(r[0] or "{}") for r in rows]


def _refused(broker: Broker, box: FakeMailbox, call, *, fence: str = pf.FENCE_PARTICIPANTS) -> dict:
    with pytest.raises(MsGraphRefused) as caught:
        call()
    assert str(caught.value).startswith(pf.REFUSAL_MARKER)
    assert pf.DECISION in str(caught.value)
    assert not box.posts, "a refused transmit must send nothing"
    row = _rows(broker, "CONFIRM_SEND_FAILED")[-1]
    assert row["outcome"] == "refused" and row["fence"] == fence
    return row


# -- the happy paths ------------------------------------------------------------


def test_a_participant_is_allowed(tmp_path: Path) -> None:
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    out = _send(broker, [ALICE], cc=[DANA], anchor=_anchor("REQ"))
    assert out["ok"] is True and len(box.posts) == 1
    row = _rows(broker, "CONFIRM_SEND_DISPATCHED")[-1]
    assert row["anchor_kind"] == "graph_message"


def test_an_outside_client_on_cc_is_left_to_the_existing_ceiling(tmp_path: Path) -> None:
    """The fence never judges an outsider: the authored surface still does."""
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    assert _send(broker, [ALICE], cc=[CLIENT], anchor=_anchor("REQ"))["ok"] is True
    # And an outsider the seat never authored is still refused, by the old fence.
    with pytest.raises(MsGraphRefused, match="authored counterparty surface"):
        _send(broker, ["stranger@elsewhere.example"], anchor=_anchor("REQ"))


def test_smd_alone_needs_no_anchor_and_reads_nothing(tmp_path: Path) -> None:
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    assert _send(broker, [SCOTT, "team@smd.services"], anchor=_anchor("REQ"))["ok"] is True
    assert _send(broker, [SCOTT])["ok"] is True
    assert box.message_reads() == []


# -- the incident ------------------------------------------------------------------


def test_a_firm_attorney_who_was_not_on_the_request_is_refused(tmp_path: Path) -> None:
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    row = _refused(broker, box, lambda: _send(broker, [ALICE, BRUNO], anchor=_anchor("REQ")))
    assert row["refused"] == [sender_key(BRUNO)]
    assert BRUNO not in json.dumps(row["refused"])


def test_a_firm_person_on_bcc_is_refused(tmp_path: Path) -> None:
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    _refused(broker, box, lambda: _send(broker, [ALICE], bcc=[BRUNO], anchor=_anchor("REQ")))


def test_a_firm_send_that_answers_no_request_is_refused(tmp_path: Path) -> None:
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    _refused(broker, box, lambda: _send(broker, [ALICE]))


# -- lanes ----------------------------------------------------------------------------


def test_each_lane_reaches_exactly_its_authored_key(tmp_path: Path) -> None:
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    assert _send(broker, [ALICE, SCOTT], lane="rule_dispatch")["ok"] is True
    assert _send(broker, [PAULA, SCOTT], lane="escalation")["ok"] is True
    assert _send(broker, [ALICE], lane="skill:statute-watch")["ok"] is True
    assert len(box.posts) == 3
    box.posts.clear()
    # A lane widens nothing beyond its own key.
    _refused(broker, box, lambda: _send(broker, [BRUNO], lane="rule_dispatch"))
    _refused(broker, box, lambda: _send(broker, [ALICE], lane="escalation"))
    # A disabled skill authors nobody, and an unknown skill is no lane.
    _refused(broker, box, lambda: _send(broker, [BRUNO], lane="skill:retired-watch"))
    _refused(broker, box, lambda: _send(broker, [ALICE], lane="skill:no-such-skill"))


def test_the_broker_own_send_as_lane_cannot_be_named_by_a_caller(tmp_path: Path) -> None:
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    _refused(broker, box, lambda: _send(broker, [ALICE], lane="send_as"))
    _refused(broker, box, lambda: _send(broker, [ALICE], lane="anything"))


def test_a_lane_and_an_anchor_together_cover_their_union(tmp_path: Path) -> None:
    """rule_dispatch: the admins by lane, the requester (cc) by the anchor."""
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    assert _send(broker, [ALICE, SCOTT], cc=[DANA], anchor=_anchor("REQ2"), lane="rule_dispatch")["ok"] is True


# -- SMD-originated, devices ------------------------------------------------------------


def test_smd_originated_work_reaches_the_admins_and_only_them(tmp_path: Path) -> None:
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    assert _send(broker, [CARL], anchor=_anchor("SMDREQ"))["ok"] is True
    box.posts.clear()
    _refused(broker, box, lambda: _send(broker, [BRUNO], anchor=_anchor("SMDREQ")))


def test_a_fax_scan_reaches_its_authored_person(tmp_path: Path) -> None:
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    assert _send(broker, [ALICE], anchor=_anchor("FAXSCAN"))["ok"] is True
    # (The redirected REPLY to a scan is covered in test_msgraph_send, which
    # fakes the createReply path it rides.)
    box.posts.clear()
    _refused(broker, box, lambda: _send(broker, [CARL], anchor=_anchor("FAXSCAN")))


# -- replies ------------------------------------------------------------------------------


def test_a_reply_to_its_own_anchor_reads_nothing(tmp_path: Path) -> None:
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    assert _reply(broker, "REQ", anchor=_anchor("REQ"))["ok"] is True
    # The reply verb reads its source once (sender vetting, and since 2026-10-08
    # the To/Cc lines it copies the request's authored participants from); the
    # fence adds no read of its own.
    assert len(box.message_reads()) == 1


def test_a_reply_in_the_anchor_conversation_to_a_participant_is_allowed(tmp_path: Path) -> None:
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    assert _reply(broker, "REQ2", anchor=_anchor("REQ"))["ok"] is True


def test_a_reply_outside_the_anchor_conversation_is_refused(tmp_path: Path) -> None:
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    # Dana was on the request, but this email of hers is another conversation.
    _refused(broker, box, lambda: _reply(broker, "DANA_ELSE", anchor=_anchor("REQ")))
    _refused(broker, box, lambda: _reply(broker, "OTHER", anchor=_anchor("REQ")))


def test_a_reply_with_no_anchor_is_refused(tmp_path: Path) -> None:
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    _refused(broker, box, lambda: _reply(broker, "REQ"))


# -- failure to look ----------------------------------------------------------------------


@pytest.mark.parametrize("gid", ["MISSING", "DRAFT", "OWN"])
def test_an_anchor_that_cannot_be_verified_is_unverifiable(tmp_path: Path, gid: str) -> None:
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    _refused(broker, box, lambda: _send(broker, [ALICE], anchor=_anchor(gid)), fence=pf.FENCE_UNVERIFIABLE)


def test_a_lookup_failure_is_unverifiable_never_a_transport_error(tmp_path: Path) -> None:
    box = FakeMailbox()
    box.fail_reads = True
    broker = _broker(tmp_path, box)
    _refused(broker, box, lambda: _send(broker, [ALICE], anchor=_anchor("REQ")), fence=pf.FENCE_UNVERIFIABLE)
    assert all(r["outcome"] != "transport_error" for r in _rows(broker, "CONFIRM_SEND_FAILED"))


def test_an_unreadable_customer_yaml_refuses(tmp_path: Path) -> None:
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    broker.customer_path = tmp_path / "missing.yaml"
    _refused(broker, box, lambda: _send(broker, [SCOTT]), fence=pf.FENCE_UNVERIFIABLE)


def test_a_malformed_anchor_is_refused_not_ignored(tmp_path: Path) -> None:
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    _refused(broker, box, lambda: _send(broker, [SCOTT], anchor={"kind": "graph_message", "id": "REQ"}))
    _refused(broker, box, lambda: _send(broker, [SCOTT], anchor={"kind": "nobody", "graph_message_id": "REQ"}))


def test_a_held_send_is_rechecked_at_replay(tmp_path: Path) -> None:
    """The anchor captured at hold is re-read at replay: the mailbox, not the
    capture, decides who was on the request."""
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    assert _send(broker, [DANA], anchor=_anchor("REQ"))["ok"] is True
    box.messages["REQ"]["ccRecipients"] = []
    box.posts.clear()
    _refused(broker, box, lambda: _send(broker, [DANA], anchor=_anchor("REQ")))


# -- job and rule anchors -------------------------------------------------------------------


def _demand_job(tmp_path: Path, requester: str, request_ref: str) -> str:
    ledger = DemandLedger(str(tmp_path / "audit.db"), tmp_path / "q")
    return ledger.submit(
        {
            "matter": {"id": "b041dd06-30a4-4c1f-912b-27724bd77a64", "number": "900201"},
            "file_to": None,
            "requested_by": requester,
            "request_ref": request_ref,
            "request_text": "Gap audit.",
            "deliverables": ["gap_audit"],
        }
    )


def test_a_job_anchor_is_its_request_email_from_its_requester(tmp_path: Path) -> None:
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    job = _demand_job(tmp_path, ALICE, "<REQ@mail.firm.example>")
    anchor = {"kind": "demand_job", "job_id": job}
    assert _send(broker, [ALICE, DANA], anchor=anchor)["ok"] is True
    box.posts.clear()
    _refused(broker, box, lambda: _send(broker, [BRUNO], anchor=anchor))


def test_a_job_whose_request_email_another_person_sent_is_no_anchor(tmp_path: Path) -> None:
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    job = _demand_job(tmp_path, CARL, "<REQ@mail.firm.example>")
    _refused(
        broker,
        box,
        lambda: _send(broker, [ALICE], anchor={"kind": "demand_job", "job_id": job}),
        fence=pf.FENCE_UNVERIFIABLE,
    )


def test_a_rule_anchor_is_the_origin_recorded_at_creation(tmp_path: Path) -> None:
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    pending = broker.establishment.pending
    row = pending.create(
        scope="person",
        subject={"person": DANA},
        text="Short.",
        instructed_by=DANA,
        for_admin=False,
        origin=_anchor("REQ2"),
    )
    assert row["origin"] == _anchor("REQ2")
    anchor = {"kind": "rule", "proposal_id": row["proposal_id"]}
    assert _send(broker, [DANA], anchor=anchor)["ok"] is True
    box.posts.clear()
    # A row recorded without its email (every row proposed before the fence):
    # its letter reaches the requester the broker recorded, and nobody else.
    legacy = pending.create(
        scope="person", subject={"person": DANA}, text="Older.", instructed_by=DANA, for_admin=False
    )
    legacy_anchor = {"kind": "rule", "proposal_id": legacy["proposal_id"]}
    reads = len(box.message_reads())
    assert _send(broker, [DANA], anchor=legacy_anchor)["ok"] is True
    assert len(box.message_reads()) == reads
    box.posts.clear()
    _refused(broker, box, lambda: _send(broker, [ALICE], anchor=legacy_anchor))
    _refused(
        broker,
        box,
        lambda: _send(broker, [DANA], anchor={"kind": "rule", "proposal_id": "0123456789abcdef"}),
        fence=pf.FENCE_UNVERIFIABLE,
    )


def test_a_rule_origin_sent_by_someone_else_is_no_anchor(tmp_path: Path) -> None:
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    row = broker.establishment.pending.create(
        scope="person",
        subject={"person": DANA},
        text="Short.",
        instructed_by=DANA,
        for_admin=False,
        origin=_anchor("REQ"),
    )
    _refused(
        broker,
        box,
        lambda: _send(broker, [DANA], anchor={"kind": "rule", "proposal_id": row["proposal_id"]}),
        fence=pf.FENCE_UNVERIFIABLE,
    )


def test_a_malformed_rule_origin_is_refused_at_creation(tmp_path: Path) -> None:
    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    with pytest.raises(Exception, match="origin"):
        broker.establishment.pending.create(
            scope="person",
            subject={"person": DANA},
            text="x",
            instructed_by=DANA,
            for_admin=False,
            origin={"kind": "rule", "proposal_id": "abcd"},
        )


# -- the bound reply and staff send-as keep working ---------------------------------------


def test_the_staff_send_as_lane_is_the_brokers_own(tmp_path: Path) -> None:
    from workspace_broker.send_as_transport import audited_send

    box = FakeMailbox()
    broker = _broker(tmp_path, box)
    assert audited_send(broker, {"to": [ALICE], "subject": "s", "body_text": "b"})["ok"] is True
    with pytest.raises(MsGraphRefused):
        audited_send(broker, {"to": [BRUNO], "subject": "s", "body_text": "b"})


# -- AgentMail (the pilot) -------------------------------------------------------------------


class FakeAgentMail:
    def __init__(self, messages: dict[str, dict]) -> None:
        self.messages = messages
        self.posts: list[str] = []

    def __call__(self, request, timeout=None):
        url = urllib.parse.unquote(request.full_url)
        if request.method == "GET" and url.endswith("/inboxes"):
            return _Response(json.dumps({"inboxes": [{"inbox_id": "seat@agentmail.to"}]}))
        if request.method == "POST":
            self.posts.append(url)
            return _Response(json.dumps({"message_id": "m-out", "thread_id": "t1"}))
        mid = url.rsplit("/messages/", 1)[1]
        if mid in self.messages:
            return _Response(json.dumps(self.messages[mid]))
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)  # type: ignore[arg-type]


PILOT_YAML = """
connectors:
  Email:
    adapter: agentmail
    enabled: true
    agentmail_inbox: seat@agentmail.to
scope:
  inbound_allow_from: [runner@agentmail.to, admin@agentmail.to, x@agentmail.to, y@agentmail.to]
  admins: [scott@smd.services, admin@agentmail.to]
"""


def test_an_agentmail_anchor_vouches_for_its_own_participants(tmp_path: Path) -> None:
    from workspace_broker.agentmail_ops import AgentMailRefused

    customer = tmp_path / "customer.yaml"
    customer.write_text(PILOT_YAML)
    cred = tmp_path / "agentmail.json"
    cred.write_text(json.dumps({"api_key": "k"}))
    http = FakeAgentMail(
        {
            "in1": {
                "from": "Runner <runner@agentmail.to>",
                "to": ["seat@agentmail.to"],
                "cc": ["x@agentmail.to"],
                "thread_id": "t1",
            },
            "out1": {"from": "seat@agentmail.to", "to": ["x@agentmail.to"], "thread_id": "t1"},
        }
    )
    broker = Broker.__new__(Broker)
    broker.customer_slug = "seat"
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.customer_path = customer
    broker.audit_db_path = str(tmp_path / "audit.db")
    broker.ledger = LedgerWriter(broker.audit_db_path)
    broker.agentmail = AgentMailOps(cred, customer, "seat", opener=http)

    def send(to, anchor):
        return broker.handle(
            {"action": "agentmail_send", "payload": {"to": to, "text": "x"}, "anchor": anchor},
            peer_pid=GATEWAY_PID,
            peer_uid=AGENT_UID,
        )

    def am(mid):
        return {"kind": "agentmail_message", "message_id": mid}

    assert send(["x@agentmail.to"], am("in1"))["ok"] is True
    with pytest.raises(AgentMailRefused, match=pf.REFUSAL_MARKER):
        send(["y@agentmail.to"], am("in1"))
    with pytest.raises(AgentMailRefused, match="could not be verified"):
        send(["x@agentmail.to"], am("out1"))  # the seat's own message is no request
    with pytest.raises(AgentMailRefused, match="could not be verified"):
        send(["x@agentmail.to"], _anchor("REQ"))  # a Graph anchor on the AgentMail channel
    assert len(http.posts) == 1


# -- the pure classifier ---------------------------------------------------------------------


def test_the_classifier_reads_the_typed_roster_first(tmp_path: Path) -> None:
    customer = tmp_path / "customer.yaml"
    customer.write_text(YAML)
    facts = pf.seat_facts(customer)
    assert facts.classify("team@smd.services") == "smd"
    assert facts.classify("smdurgan@smdurgan.com") == "smd"
    assert facts.classify(CLIENT) == "outside"
    assert facts.classify("Bruno <BRUNO@firm.example>") == "firm"
    assert facts.classify("someone@elsewhere.example") == "outside"
    assert facts.lanes["escalation"] == frozenset({PAULA, SCOTT})
    assert "skill:retired-watch" not in facts.lanes


def test_a_skills_scheduled_recipients_are_its_lane(tmp_path: Path) -> None:
    """The negotiation watch's offer emails go to the skill's authored
    scheduled_recipients (a comma-separated scalar). FALSIFIER: read only
    settings.recipient and the notice's new message is refused at the fence."""
    p = tmp_path / "customer.yaml"
    p.write_text(
        "personas:\n"
        "  - skills:\n"
        "      - name: negotiation-watch\n"
        "        enabled: true\n"
        "        settings:\n"
        "          scheduled_recipients: 'Office@Firm.example, second@firm.example'\n"
    )
    lanes = pf.seat_facts(p).lanes
    assert lanes["skill:negotiation-watch"] == frozenset({"office@firm.example", "second@firm.example"})
