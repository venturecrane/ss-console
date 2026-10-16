"""The rehearsal fence: an email marked as a rehearsal or test reaches SMD only.

The incident (the Brokery seat, 2026-10-16): a rehearsal meant for
smdurgan@smdurgan.com went to the client, rostered on the seat's roster lane,
with "[Rehearsal 2]" in its subject. Each test pins one rule and fails without
it; the passes come first, because a refusal proves nothing if nothing passes.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import participant_fence as pf
from workspace_broker import rehearsal_fence as rf
from workspace_broker.agentmail_ops import AgentMailOps, AgentMailRefused
from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.server import Broker

GATEWAY_PID = 42
AGENT_UID = 1000
TIM = "tim@brokerage.example"
SMD = "smdurgan@smdurgan.com"
PROBE = "ss-probe-runner@agentmail.to"

# The Brokery seat's shape: the client is rostered, and a roster lane is authored.
YAML = f"""
connectors:
  Email:
    adapter: agentmail
    enabled: true
    agentmail_inbox: seat@agentmail.to
scope:
  inbound_allow_from: [{TIM}, {SMD}, {PROBE}]
  admins: [{SMD}]
personas:
  - slug: brokery-agent
    skills:
      - name: open-house-visitor-capture
        enabled: true
        settings: {{recipients: roster}}
"""

LANE = "skill:open-house-visitor-capture"


class _Response:
    def __init__(self, body: str) -> None:
        self._body = body.encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_a) -> None:
        return None


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


MESSAGES = {
    "from-tim": {"from": f"Tim <{TIM}>", "to": ["seat@agentmail.to"], "thread_id": "t1", "message_id": "from-tim"},
    "from-smd": {"from": f"Scott <{SMD}>", "to": ["seat@agentmail.to"], "thread_id": "t2", "message_id": "from-smd"},
    # The client's own answer in the leaked thread (2026-10-16, 15:39Z).
    "tim-in-thread": {
        "from": f"Tim <{TIM}>",
        "to": ["seat@agentmail.to"],
        "thread_id": "t3",
        "subject": "Re: [Rehearsal 2] Getting your pilot set up",
    },
    # SMD's own rehearsal instruction: answering it vouches for nobody outside SMD.
    "smd-rehearsal": {
        "from": f"Scott <{SMD}>",
        "to": ["seat@agentmail.to"],
        "thread_id": "t4",
        "subject": "[Rehearsal] Program change",
    },
}


def _broker(tmp_path: Path) -> tuple[Broker, FakeAgentMail]:
    customer = tmp_path / "customer.yaml"
    customer.write_text(YAML)
    cred = tmp_path / "agentmail.json"
    cred.write_text(json.dumps({"api_key": "k"}))
    http = FakeAgentMail(MESSAGES)
    broker = Broker.__new__(Broker)
    broker.customer_slug = "brokery"
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.customer_path = customer
    broker.audit_db_path = str(tmp_path / "audit.db")
    broker.ledger = LedgerWriter(broker.audit_db_path)
    broker.agentmail = AgentMailOps(cred, customer, "seat", opener=http)
    return broker, http


def _send(
    broker: Broker, to: list[str], subject: str, *, cc=(), bcc=(), lane: str | None = LANE, anchor: str | None = None
) -> dict:
    payload: dict = {"to": to, "subject": subject, "text": "x"}
    if cc:
        payload["cc"] = list(cc)
    if bcc:
        payload["bcc"] = list(bcc)
    request: dict = {"action": "agentmail_send", "payload": payload}
    if lane:
        request["lane"] = lane
    if anchor:
        request["anchor"] = {"kind": "agentmail_message", "message_id": anchor}
    return broker.handle(request, peer_pid=GATEWAY_PID, peer_uid=AGENT_UID)


def _reply(broker: Broker, target: str, subject: str) -> dict:
    request = {
        "action": "agentmail_reply",
        "payload": {"message_id": target, "subject": subject, "text": "x"},
        "anchor": {"kind": "agentmail_message", "message_id": target},
    }
    return broker.handle(request, peer_pid=GATEWAY_PID, peer_uid=AGENT_UID)


def _last_failed(broker: Broker) -> dict:
    import sqlite3

    conn = sqlite3.connect(broker.audit_db_path)
    try:
        row = conn.execute(
            "SELECT metadata FROM audit_log WHERE action_type='CONFIRM_SEND_FAILED' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return json.loads(row[0])


# -- the passes -------------------------------------------------------------------------


def test_an_unmarked_send_to_the_client_on_his_lane_goes(tmp_path: Path) -> None:
    broker, http = _broker(tmp_path)
    assert _send(broker, [TIM], "Getting your pilot set up")["ok"] is True
    assert len(http.posts) == 1


def test_a_rehearsal_to_smd_or_a_probe_inbox_goes(tmp_path: Path) -> None:
    broker, http = _broker(tmp_path)
    assert _send(broker, [SMD], "[Rehearsal 2] Getting your pilot set up")["ok"] is True
    assert _send(broker, [PROBE], "[Rehearsal] Open house today")["ok"] is True
    assert len(http.posts) == 2


def test_a_marked_reply_to_smd_goes(tmp_path: Path) -> None:
    broker, http = _broker(tmp_path)
    assert _reply(broker, "from-smd", "Re: [Rehearsal] Program change")["ok"] is True
    assert len(http.posts) == 1


# -- the incident -----------------------------------------------------------------------


def test_the_2026_10_16_send_is_refused(tmp_path: Path) -> None:
    """Exactly what reached the client: the rehearsal subject, his address, his lane."""
    broker, http = _broker(tmp_path)
    with pytest.raises(AgentMailRefused, match=pf.REFUSAL_MARKER):
        _send(broker, [TIM], "[Rehearsal 2] Getting your pilot set up")
    assert not http.posts, "a refused transmit must send nothing"
    row = _last_failed(broker)
    assert row["outcome"] == "refused" and row["fence"] == pf.FENCE_REHEARSAL
    assert TIM not in json.dumps(row["refused"]), "the row carries sender keys, never addresses"


@pytest.mark.parametrize("where", ["to", "cc", "bcc"])
def test_a_rehearsal_with_the_client_anywhere_on_it_is_refused(tmp_path: Path, where: str) -> None:
    broker, http = _broker(tmp_path)
    to, cc, bcc = [SMD], [], []
    {"to": to, "cc": cc, "bcc": bcc}[where].append(TIM)
    with pytest.raises(AgentMailRefused, match="rehearsal"):
        _send(broker, to, "[Rehearsal] check-in", cc=cc, bcc=bcc)
    assert not http.posts


def test_no_lane_and_no_approval_can_carry_a_rehearsal_out(tmp_path: Path) -> None:
    broker, http = _broker(tmp_path)
    for lane in (LANE, None):
        with pytest.raises(AgentMailRefused, match="rehearsal"):
            _send(broker, [TIM], "[Rehearsal] welcome", lane=lane)
    assert not http.posts


def test_a_marked_reply_to_the_client_is_refused(tmp_path: Path) -> None:
    broker, http = _broker(tmp_path)
    with pytest.raises(AgentMailRefused, match="rehearsal"):
        _reply(broker, "from-tim", "Re: [Rehearsal] your question")
    assert not http.posts


# -- an inherited marker: the client already answered in the marked thread ----------


def test_a_reply_in_the_thread_the_client_answered_goes(tmp_path: Path) -> None:
    """Refusing this would cut off a live client because of SMD's own mistake."""
    broker, http = _broker(tmp_path)
    assert _reply(broker, "tim-in-thread", "Re: [Rehearsal 2] Getting your pilot set up")["ok"] is True
    assert _send(broker, [TIM], "Re: [Rehearsal 2] Getting your pilot set up", anchor="tim-in-thread")["ok"] is True
    assert len(http.posts) == 2


def test_an_inherited_marker_reaches_nobody_who_was_not_on_the_thread(tmp_path: Path) -> None:
    broker, http = _broker(tmp_path)
    with pytest.raises(AgentMailRefused, match="rehearsal"):
        _send(broker, [TIM, "agent@brokerage.example"], "Re: [Rehearsal 2] x", anchor="tim-in-thread", lane=None)
    assert not http.posts


def test_smd_rehearsal_mail_vouches_for_no_client(tmp_path: Path) -> None:
    """An instruction from SMD marked as a rehearsal cannot carry the marker to a client."""
    broker, http = _broker(tmp_path)
    with pytest.raises(AgentMailRefused, match="rehearsal"):
        _send(broker, [TIM], "[Rehearsal] Program change", anchor="smd-rehearsal")
    assert not http.posts


# -- the marker -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "subject",
    [
        "[Rehearsal] Welcome",
        "[Rehearsal 8] Welcome to your Brokery assistant",
        "[rehearsal] lower case",
        "Re: [Rehearsal 2] Getting your pilot set up",
        "Rehearsal 4: Welcome to your Brokery assistant",
        "Re: Fwd: Rehearsal: welcome",
        "[SMD-PROBE 2026-10-16T15:00Z] drafting prove-out",
        "[Dry run] weekly report",
        "[dry-run] weekly report",
    ],
)
def test_marked_subjects(subject: str) -> None:
    assert rf.is_marked(subject)


@pytest.mark.parametrize(
    "subject",
    [
        "Test results from Dr. Lee",  # a real law-firm subject must reach its attorney
        "Testimony scheduled for Monday",
        "Getting your pilot set up",
        "Notes from the dress rehearsal for the deposition",  # not leading, not bracketed
        "[Testimony] exhibit list",
        "[Test] welcome",  # "test" and "probe" are client words, not markers
        "[TEST 3] digest",
        "[Probe] send path",
        "[Test results] Smith matter",
        "",
        None,
    ],
)
def test_unmarked_subjects(subject) -> None:
    assert not rf.is_marked(subject)


def test_smd_owned_is_exactly_smd_domains_and_the_probe_inboxes() -> None:
    assert rf.is_smd_owned("Scott <scott@smd.services>")
    assert rf.is_smd_owned(SMD)
    assert rf.is_smd_owned(PROBE)
    assert not rf.is_smd_owned(TIM)
    assert not rf.is_smd_owned("seat@agentmail.to")
    assert not rf.is_smd_owned("ss-probe-runner@agentmail.to.evil.example")


def test_the_fence_is_in_the_closed_vocabulary() -> None:
    assert pf.FENCE_REHEARSAL in pf.FENCES
