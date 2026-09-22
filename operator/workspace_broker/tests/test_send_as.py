"""send_as_propose / send_as_decide / send_as_match_reply (ADR 0089).

The question each test answers: could a message go out under a staff member's
name without that staff member approving that exact text? The mailbox holds
Exchange Send As on the staff member, so the only thing standing between the
agent and impersonation is this module; every path to a From is exercised here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# The msgraph suite's FakeGraph models Sent Items faithfully; reuse it rather
# than fork a second model of the same mailbox.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from workspace_broker import send_as_acts, send_as_links
from workspace_broker.send_as_acts import SEND_AS_TTL_SECONDS, canonical_payload, digest_of, parse_tag
from workspace_broker.server import Broker

from test_msgraph_send import GATEWAY_PID, AGENT_UID, MAILBOX, FakeGraph, RecordingLedger, _ops

STAFF = "paralegal@examplefirm.example"
ADMIN = "chris@examplefirm.example"
OTHER_STAFF = "attorney@examplefirm.example"
OUTSIDE = "adjuster@insurer.example"

SEAT_YAML = f"""
connectors:
  Email:
    adapter: msgraph
    enabled: true
    msgraph_auth:
      mailbox: {MAILBOX}
personas:
  - name: operator
    entitlements:
      exposure:
        external_send: draft_for_review
        external_send_as_staff: confirm
scope:
  inbound_allow_from:
    - '@examplefirm.example'
  admins:
    - {ADMIN}
  domain_blocks:
    - blocked.example
  staff_send_as:
    - address: {STAFF}
      name: Pat Paralegal
    - address: {OTHER_STAFF}
      name: Alex Attorney
"""

NO_CONFIRM_YAML = SEAT_YAML.replace("external_send_as_staff: confirm", "external_send_as_staff: autonomous")


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


class SendAsGraph(FakeGraph):
    """FakeGraph plus the two reads send-as adds: the Sent Items origin check
    and the conversation lookup."""

    def __init__(self, *, own_sent_ids: tuple[str, ...] = (), origin_status: int | None = None, **kw) -> None:
        super().__init__(**kw)
        self.own_sent_ids = own_sent_ids
        self.origin_status = origin_status
        self.sent_messages: list[dict] = []

    def __call__(self, request, timeout=None):
        url = request.full_url
        if request.method == "GET" and ("%24filter" in url or "$filter" in url):
            import urllib.error

            self.calls.append((request.method, url, None))
            if self.origin_status is not None:
                raise urllib.error.HTTPError(url, self.origin_status, "nope", {}, None)  # type: ignore[arg-type]
            import urllib.parse

            query = urllib.parse.unquote(url)
            hits = [{"id": "x", "internetMessageId": mid} for mid in self.own_sent_ids if mid in query]
            from test_msgraph_send import _Response

            return _Response(json.dumps({"value": hits}))
        if request.method == "GET" and "$select=conversationId" in url:
            from test_msgraph_send import _Response

            self.calls.append((request.method, url, None))
            return _Response(json.dumps({"conversationId": "conv-1"}))
        if request.method == "POST" and url.endswith("/sendMail") and request.data:
            self.sent_messages.append(json.loads(request.data.decode())["message"])
        return super().__call__(request, timeout)


def _broker(tmp_path: Path, http: FakeGraph, yaml_text: str = SEAT_YAML) -> Broker:
    broker = Broker.__new__(Broker)
    broker.customer_slug = "test-seat"
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.ledger = RecordingLedger()
    broker.msgraph = _ops(tmp_path, http, yaml_text)
    broker.customer_path = tmp_path / "customer.yaml"
    broker.audit_db_path = str(tmp_path / "audit.db")
    broker.send_as_now = Clock()
    return broker


def _propose(broker: Broker, **over) -> dict:
    request = {
        "action": "send_as_propose",
        "session_id": "sess-1",
        "instructed_by": STAFF,
        "payload": {
            "from": STAFF,
            "to": [OUTSIDE],
            "cc": [],
            "subject": "Records request",
            "body_text": "Please send the records.",
        },
        "gate_pass": {"fabrication": True, "matter": True, "identifier": True, "matters": []},
        "tainted": False,
        "sources": [],
    }
    for key, value in over.items():
        if key in request["payload"]:
            request["payload"][key] = value
        else:
            request[key] = value
    return broker.handle(request, peer_pid=GATEWAY_PID, peer_uid=AGENT_UID)


def _decide(broker: Broker, tag: str, decision: str, by: str = STAFF, **over) -> dict:
    request = {
        "action": "send_as_decide",
        "tag_or_act_id": tag,
        "decision": decision,
        "decided_by": by,
        "internet_message_id": "<answer-1@examplefirm.example>",
        "instruction": over.pop("instruction", None),
    }
    request.update(over)
    return broker.handle(request, peer_pid=GATEWAY_PID, peer_uid=AGENT_UID)


def _action_types(broker: Broker) -> list[str]:
    return [r["action_type"] for r in broker.ledger.rows]


# -- propose ----------------------------------------------------------------


def test_a_proposal_emails_the_approver_the_exact_draft(tmp_path: Path) -> None:
    http = SendAsGraph()
    broker = _broker(tmp_path, http)
    out = _propose(broker)
    assert out["ok"] is True
    assert out["tag"].startswith("[draft ") and out["notified"] is True
    approval = http.sent_messages[-1]
    assert [r["emailAddress"]["address"] for r in approval["toRecipients"]] == [STAFF]
    assert "from" not in approval  # the approval comes from the seat mailbox, not as anyone
    text = approval["body"]["content"]
    assert OUTSIDE in text and "not on your firm's roster" in text
    assert "Please send the records." in text and f"{out['tag']} send" in text
    assert "SEND_AS_PROPOSED" in _action_types(broker)


def test_nothing_goes_to_the_outside_party_at_propose_time(tmp_path: Path) -> None:
    http = SendAsGraph()
    _propose(_broker(tmp_path, http))
    recipients = [r["emailAddress"]["address"] for m in http.sent_messages for r in m["toRecipients"]]
    assert OUTSIDE not in recipients


def test_a_tainted_draft_says_so_in_the_approval(tmp_path: Path) -> None:
    http = SendAsGraph()
    _propose(_broker(tmp_path, http), tainted=True, sources=["email from adjuster@insurer.example"])
    assert (
        "Prepared after reading outside material: email from adjuster@insurer.example"
        in http.sent_messages[-1]["body"]["content"]
    )


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        (
            {"from": "stranger@examplefirm.example", "instructed_by": "stranger@examplefirm.example"},
            "not on scope.staff_send_as",
        ),
        ({"instructed_by": OTHER_STAFF}, "only as the staff member who asked"),
        ({"gate_pass": {"fabrication": True, "matter": False, "identifier": True}}, "did not pass"),
        ({"to": ["someone@blocked.example"]}, "blocked domain"),
        ({"to": []}, "at least one To"),
        ({"body_text": " "}, "needs a body"),
    ],
)
def test_proposals_the_rules_forbid_are_refused_and_audited(tmp_path: Path, over: dict, reason: str) -> None:
    http = SendAsGraph()
    broker = _broker(tmp_path, http)
    out = _propose(broker, **over)
    assert out["ok"] is False and reason in out["reason"]
    assert http.sent_messages == []
    assert _action_types(broker) == ["SEND_AS_REFUSED"]


def test_an_administrator_may_ask_for_a_draft_in_a_staff_members_name(tmp_path: Path) -> None:
    out = _propose(_broker(tmp_path, SendAsGraph()), instructed_by=ADMIN)
    assert out["ok"] is True


def test_a_seat_that_does_not_author_confirm_proposes_nothing(tmp_path: Path) -> None:
    out = _propose(_broker(tmp_path, SendAsGraph(), NO_CONFIRM_YAML))
    assert out["ok"] is False and "external_send_as_staff: confirm" in out["reason"]


def test_bcc_and_reply_to_from_the_caller_never_reach_the_draft(tmp_path: Path) -> None:
    msg = canonical_payload(
        {
            "from": STAFF,
            "to": [OUTSIDE],
            "subject": "s",
            "body_text": "b",
            "bcc": ["spy@evil.example"],
            "reply_to": ["spy@evil.example"],
        },
        MAILBOX,
    )
    assert "bcc" not in msg
    assert msg["reply_to"] == [STAFF, MAILBOX]


def test_the_digest_covers_the_rendered_html(tmp_path: Path) -> None:
    msg = canonical_payload(
        {"from": STAFF, "to": [OUTSIDE], "subject": "s", "body_text": "line one\nline two"}, MAILBOX
    )
    changed = {**msg, "html": msg["html"].replace("two", "2")}
    assert digest_of(msg) != digest_of(changed)


def test_propose_is_gateway_only(tmp_path: Path) -> None:
    broker = _broker(tmp_path, SendAsGraph())
    with pytest.raises(PermissionError):
        broker.handle({"action": "send_as_propose"}, peer_pid=9999, peer_uid=AGENT_UID)
    with pytest.raises(PermissionError):
        broker.handle({"action": "send_as_decide"}, peer_pid=9999, peer_uid=AGENT_UID)


# -- decide -----------------------------------------------------------------


def test_the_approver_sends_it_from_their_own_address(tmp_path: Path) -> None:
    http = SendAsGraph()
    broker = _broker(tmp_path, http)
    tag = _propose(broker)["tag"]
    out = _decide(broker, tag, "send")
    assert out["status"] == "DISPATCHED"
    sent = http.sent_messages[-1]
    assert sent["from"]["emailAddress"]["address"] == STAFF
    assert [r["emailAddress"]["address"] for r in sent["toRecipients"]] == [OUTSIDE]
    assert [r["emailAddress"]["address"] for r in sent["replyTo"]] == [STAFF, MAILBOX]
    assert "bccRecipients" not in sent
    assert "SEND_AS_SENT" in _action_types(broker)


def test_an_approved_draft_sends_once(tmp_path: Path) -> None:
    http = SendAsGraph()
    broker = _broker(tmp_path, http)
    tag = _propose(broker)["tag"]
    assert _decide(broker, tag, "send")["status"] == "DISPATCHED"
    again = _decide(broker, tag, "send", internet_message_id="<answer-2@examplefirm.example>")
    assert again["status"] == "REFUSED"
    assert sum(1 for m in http.sent_messages if "from" in m) == 1


def test_an_administrator_cannot_send_in_a_staff_members_name(tmp_path: Path) -> None:
    http = SendAsGraph()
    broker = _broker(tmp_path, http)
    tag = _propose(broker)["tag"]
    assert _decide(broker, tag, "send", by=ADMIN)["status"] == "REFUSED"
    assert not any("from" in m for m in http.sent_messages)


def test_an_administrator_may_cancel(tmp_path: Path) -> None:
    broker = _broker(tmp_path, SendAsGraph())
    tag = _propose(broker)["tag"]
    assert _decide(broker, tag, "cancel", by=ADMIN)["status"] == "CANCELLED"
    assert _decide(broker, tag, "send")["status"] == "REFUSED"


def test_another_staff_member_cannot_answer(tmp_path: Path) -> None:
    broker = _broker(tmp_path, SendAsGraph())
    tag = _propose(broker)["tag"]
    assert _decide(broker, tag, "send", by=OTHER_STAFF)["status"] == "REFUSED"
    assert _decide(broker, tag, "change", by=OTHER_STAFF, instruction="x")["status"] == "REFUSED"


def test_an_answer_sent_by_the_operators_own_mailbox_is_refused(tmp_path: Path) -> None:
    forged = "<forged@examplefirm.example>"
    http = SendAsGraph(own_sent_ids=(forged,))
    broker = _broker(tmp_path, http)
    tag = _propose(broker)["tag"]
    out = _decide(broker, tag, "send", internet_message_id=forged)
    assert out["status"] == "REFUSED" and "Operator's own mailbox" in out["reason"]
    assert not any("from" in m for m in http.sent_messages)


def test_a_failed_origin_check_refuses_rather_than_trusting(tmp_path: Path) -> None:
    http = SendAsGraph(origin_status=503)
    broker = _broker(tmp_path, http)
    tag = _propose(broker)["tag"]
    assert _decide(broker, tag, "send")["status"] == "REFUSED"
    assert not any("from" in m for m in http.sent_messages)


def test_an_expired_draft_sends_nothing(tmp_path: Path) -> None:
    http = SendAsGraph()
    broker = _broker(tmp_path, http)
    tag = _propose(broker)["tag"]
    broker.send_as_now.t += SEND_AS_TTL_SECONDS + 1
    out = _decide(broker, tag, "send")
    assert out["status"] == "EXPIRED"
    assert not any("from" in m for m in http.sent_messages)


def test_change_revises_and_the_next_proposal_supersedes_it(tmp_path: Path) -> None:
    http = SendAsGraph()
    broker = _broker(tmp_path, http)
    first = _propose(broker)["tag"]
    out = _decide(broker, first, "change", instruction="add the date of loss")
    assert out["status"] == "REVISED" and out["instruction"] == "add the date of loss"
    # The text they already hold comes back, so the redraft turn can carry its
    # identifiers as provenance (it read none of them itself).
    assert out["prior_text"] and out["prior_subject"]
    second = _propose(broker, body_text="Please send the records. Date of loss: 8/24/26.")["tag"]
    stale = _decide(broker, first, "send", internet_message_id="<answer-2@examplefirm.example>")
    assert stale["status"] == "SUPERSEDED" and stale["replaced_by"] == second
    assert (
        _decide(broker, second, "send", internet_message_id="<answer-3@examplefirm.example>")["status"] == "DISPATCHED"
    )


def test_a_requested_change_leaves_the_draft_answerable(tmp_path: Path) -> None:
    # smd-staging, 2026-09-22: the change closed the draft, the redraft was then
    # refused by the identifier gate, and the approver was left with a dead tag
    # and no replacement. A revision request must keep every outcome reachable
    # until the replacement actually exists.
    http = SendAsGraph()
    broker = _broker(tmp_path, http)
    tag = _propose(broker)["tag"]
    assert _decide(broker, tag, "change", instruction="add the date of loss")["status"] == "REVISED"
    out = _decide(broker, tag, "send", internet_message_id="<answer-2@examplefirm.example>")
    assert out["status"] == "DISPATCHED"
    assert http.sent_messages[-1]["from"]["emailAddress"]["address"] == STAFF


def test_a_change_that_never_gets_a_replacement_can_still_be_cancelled(tmp_path: Path) -> None:
    http = SendAsGraph()
    broker = _broker(tmp_path, http)
    tag = _propose(broker)["tag"]
    _decide(broker, tag, "change", instruction="add the date of loss")
    out = _decide(broker, tag, "cancel", internet_message_id="<answer-2@examplefirm.example>")
    assert out["status"] == "CANCELLED"
    assert not any("from" in m for m in http.sent_messages)


def test_a_transport_failure_is_reported_and_never_retried(tmp_path: Path) -> None:
    http = SendAsGraph()
    broker = _broker(tmp_path, http)
    tag = _propose(broker)["tag"]

    def boom(_msg, _from):
        from workspace_broker.msgraph_ops import MsGraphTransportError

        raise MsGraphTransportError("msgraph POST failed: HTTP 503")

    broker.msgraph.send_as_staff = boom  # type: ignore[method-assign]
    out = _decide(broker, tag, "send")
    assert out["status"] == "FAILED"
    assert _decide(broker, tag, "send", internet_message_id="<answer-2@examplefirm.example>")["status"] == "REFUSED"
    notice = http.sent_messages[-1]
    assert notice["subject"].startswith("Not sent:")


def test_a_draft_whose_sender_left_the_roster_does_not_send(tmp_path: Path) -> None:
    http = SendAsGraph()
    broker = _broker(tmp_path, http)
    tag = _propose(broker)["tag"]
    broker.customer_path.write_text(SEAT_YAML.replace(f"    - address: {STAFF}\n      name: Pat Paralegal\n", ""))
    assert _decide(broker, tag, "send")["status"] == "REFUSED"


def test_tags_parse_strictly(tmp_path: Path) -> None:
    assert parse_tag("[draft 1a2b3c4d]") == "1a2b3c4d"
    assert parse_tag("1A2B3C4D") == "1a2b3c4d"
    for bad in ("[act 1a2b3c4d]", "[draft 1a2b]", "", None):
        with pytest.raises(send_as_acts.SendAsRefused):
            parse_tag(bad)


# -- replies ----------------------------------------------------------------


def test_a_reply_on_the_sent_conversation_notifies_the_approver(tmp_path: Path) -> None:
    http = SendAsGraph()
    broker = _broker(tmp_path, http)
    tag = _propose(broker)["tag"]
    _decide(broker, tag, "send")
    out = broker.handle(
        {
            "action": "send_as_match_reply",
            "conversation_id": "conv-1",
            "internet_message_id": "<r@insurer.example>",
            "from": OUTSIDE,
        },
        peer_pid=9999,
        peer_uid=AGENT_UID,
    )
    assert out == {"matched": True, "tag": tag}
    notice = http.sent_messages[-1]
    assert [r["emailAddress"]["address"] for r in notice["toRecipients"]] == [STAFF]
    assert notice["subject"].startswith("Reply received:")


def test_an_unrelated_conversation_matches_nothing(tmp_path: Path) -> None:
    broker = _broker(tmp_path, SendAsGraph())
    out = broker.handle(
        {"action": "send_as_match_reply", "conversation_id": "conv-other", "from": OUTSIDE},
        peer_pid=GATEWAY_PID,
        peer_uid=AGENT_UID,
    )
    assert out == {"matched": False, "tag": None}


def test_every_seat_mailbox_send_leaves_an_identity_joinable_row(tmp_path: Path) -> None:
    """The approval email, and the send AS the staff member, each carry the
    audit header on the message and the same token on a row, which is the exact
    key operator/bin/reconcile-sends.py joins on. Without it the reconciler
    would report the Operator's own approval mail as unaudited egress."""
    http = SendAsGraph()
    broker = _broker(tmp_path, http)
    tag = _propose(broker)["tag"]
    _decide(broker, tag, "send")
    rows = {r["action_type"]: json.loads(r["metadata"]) for r in broker.ledger.rows}
    approval_row = rows["CONFIRM_SEND_DISPATCHED"]
    sent_row = rows["SEND_AS_SENT"]
    assert approval_row["audit_row_token"] and approval_row["recipients"] == [STAFF]
    assert sent_row["audit_row_token"] and sent_row["from"] == STAFF
    wire_tokens = {h["value"] for hs in http.transmitted_headers for h in hs}
    assert {approval_row["audit_row_token"], sent_row["audit_row_token"]} <= wire_tokens


def test_no_row_ever_carries_the_draft_body(tmp_path: Path) -> None:
    broker = _broker(tmp_path, SendAsGraph())
    tag = _propose(broker, body_text="Confidential medical detail XYZZY")["tag"]
    _decide(broker, tag, "send")
    assert all("XYZZY" not in json.dumps(r) for r in broker.ledger.rows)


def test_the_generic_send_still_refuses_a_caller_supplied_from(tmp_path: Path) -> None:
    http = SendAsGraph()
    broker = _broker(tmp_path, http)
    broker.msgraph.send({"to": [STAFF], "subject": "s", "body_text": "b", "from": OUTSIDE})
    assert "from" not in http.sent_messages[-1]


# -- approve links (ADR 0089 amendment 5a) ----------------------------------


@pytest.fixture
def links(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A seat with a web face, and a link key this test owns."""
    monkeypatch.setattr(send_as_links, "LINK_KEY_PATH", str(tmp_path / "link.key"))
    monkeypatch.setenv("SMD_APPROVE_BASE_URL", "https://seat.example")
    return None


def _click(broker: Broker, token: str) -> dict:
    return broker.handle(
        {"action": "send_as_decide_link", "token": token},
        peer_pid=GATEWAY_PID + 1,  # not the gateway: the web gate is its own pid
        peer_uid=AGENT_UID,
    )


def _token_from_email(http: SendAsGraph, which: str) -> str:
    body = http.sent_messages[-1]["body"]["content"]
    marker = "https://seat.example/approve?t="
    first = body.index(marker)
    start = first if which == "send" else body.index(marker, first + 1)
    start += len(marker)
    return body[start : body.index('"', start)]


def _save_flags(http: SendAsGraph) -> list:
    out = []
    for call in http.calls:
        if call[0] != "POST" or not str(call[1]).endswith("/sendMail") or not call[2]:
            continue
        body = call[2]
        body = json.loads(body.decode()) if isinstance(body, (bytes, bytearray)) else body
        out.append(body.get("saveToSentItems"))
    return out


def test_the_approval_email_carries_buttons_and_leaves_no_copy(tmp_path: Path, links) -> None:
    http = SendAsGraph()
    broker = _broker(tmp_path, http)
    assert _propose(broker)["ok"] is True
    approval = http.sent_messages[-1]
    assert approval["body"]["contentType"] == "HTML"
    assert ">Send it<" in approval["body"]["content"]
    assert ">Cancel<" in approval["body"]["content"]
    # The buttons are keys, so this mailbox must keep no copy the agent could read.
    assert _save_flags(http)[-1] is False
    assert "no_sent_copy" not in json.dumps(approval)


def test_a_send_button_click_sends_once_and_tells_the_approver(tmp_path: Path, links) -> None:
    http = SendAsGraph()
    broker = _broker(tmp_path, http)
    _propose(broker)
    token = _token_from_email(http, "send")
    out = _click(broker, token)
    assert out["status"] == "DISPATCHED"
    sent = [m for m in http.sent_messages if "from" in m]
    assert sent and sent[-1]["from"]["emailAddress"]["address"] == STAFF
    assert "was sent from your address" in http.sent_messages[-1]["body"]["content"]
    again = _click(broker, token)
    assert again["status"] != "DISPATCHED"
    assert len([m for m in http.sent_messages if "from" in m]) == 1


def test_a_cancel_button_click_sends_nothing(tmp_path: Path, links) -> None:
    http = SendAsGraph()
    broker = _broker(tmp_path, http)
    _propose(broker)
    out = _click(broker, _token_from_email(http, "cancel"))
    assert out["status"] == "CANCELLED"
    assert not any("from" in m for m in http.sent_messages)
    assert "was cancelled" in http.sent_messages[-1]["body"]["content"]


def test_a_tampered_or_foreign_token_decides_nothing(tmp_path: Path, links) -> None:
    http = SendAsGraph()
    broker = _broker(tmp_path, http)
    _propose(broker)
    row_id, decision, expires, sig = _token_from_email(http, "cancel").split(".")
    # The signature covers the verb, so a cancel link rewritten to "send" fails.
    assert _click(broker, f"{row_id}.send.{expires}.{sig}")["status"] == "REFUSED"
    # And a token naming a row this seat does not hold decides nothing.
    assert _click(broker, f"deadbeef.{decision}.{expires}.{sig}")["status"] == "REFUSED"
    assert not any("from" in m for m in http.sent_messages)


def test_an_expired_link_sends_nothing(tmp_path: Path, links) -> None:
    http = SendAsGraph()
    broker = _broker(tmp_path, http)
    _propose(broker)
    token = _token_from_email(http, "send")
    broker.send_as_now.t += SEND_AS_TTL_SECONDS + 1
    assert _click(broker, token)["status"] in ("EXPIRED", "REFUSED")
    assert not any("from" in m for m in http.sent_messages)
