"""A drafting job's completion reply is sent by the seat, in the broker's words
(hermes-smd-reply/job_reply.py), and anything short of a sent reply alerts SMD.

THE INCIDENT (A&P, 2026-11-05, discovery_set). The job filed its documents. The
completion turn wrote its own reply instead of the broker's delivery wording;
the identifier check refused it twice, the content check held three drafts,
none reached the attorney's thread, and no alert fired. The attorney heard
nothing until SMD sent a reply by hand.

FALSIFIER: on the parent commit there is no ``job_reply`` module, the relay
sends the model's own words for a drafting binding, and a turn that drafted
nothing sends nothing and alerts nobody, so every test here fails.
"""

from __future__ import annotations

import json

import pytest

from shared.workspace_broker import BrokerError
from tests.conftest import load_plugin
from tests.test_reply_binding import (  # noqa: F401 - the autouse cleaner
    _SEAT_YAML,
    ADMIN,
    GRAPH_ID,
    JOB,
    WAKE,
    _bind,
    _clean,
    _draft,
    _drafting_wake,
    _FakeD1,
    _wake,
)

WORDS = (
    'The propounded discovery for matter 900201 is filed in the matter: "RFPD Set One.docx". '
    "Nothing has been sent to anyone outside the firm."
)
OWN = "Your discovery for matter 900201 is done and was served 11/04/2026."


class _Broker:
    def __init__(self) -> None:
        self.verdict: dict = {
            "ok": True,
            "bound": True,
            "sender": ADMIN,
            "graph_message_id": GRAPH_ID,
            "mode": "reply",
            "fixed_wording": [WORDS],
        }
        self.fail_send: Exception | None = None
        self.binds: list[dict] = []
        self.sends: list[dict] = []

    def bind_reply(self, req):
        self.binds.append(req)
        return dict(self.verdict)

    def send_bound_reply(self, req, comment, *, html="", session_id="", matter_ref=None):
        if self.fail_send is not None:
            raise self.fail_send
        self.sends.append({"binding": req, "comment": comment, "session_id": session_id})
        # After one send the broker's claim refuses any other.
        self.verdict = {
            "ok": True,
            "bound": False,
            "code": "already_replied",
            "reason": "drafting job has already had its reply for this outcome",
        }
        return "<sent@firm.example>"


@pytest.fixture
def seat(monkeypatch, tmp_path):
    mod = load_plugin("hermes-smd-reply")
    d1, broker = _FakeD1(), _Broker()
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(_SEAT_YAML.format(adapter="msgraph"))
    monkeypatch.setattr(mod, "_INFRA_READY", True, raising=False)
    monkeypatch.setattr(mod, "_CUSTOMER_SLUG", "acme", raising=False)
    monkeypatch.setattr(mod, "_D1_CLIENT", d1, raising=False)
    monkeypatch.setattr(mod, "_LIMITER", mod.relay.RateLimiter(), raising=False)
    monkeypatch.setattr(mod, "_REPLIED", mod.relay.RepliedOnce(), raising=False)
    monkeypatch.setattr(mod, "_DECIDED", mod.relay.DecidedOnce(), raising=False)
    monkeypatch.setattr(mod, "_YAML_PATH", yaml_path, raising=False)
    monkeypatch.setattr(mod, "_JOB_REPLIES", mod.job_reply.JobReplies(), raising=False)
    monkeypatch.setattr(mod.msgraph_broker, "bind_reply", broker.bind_reply)
    monkeypatch.setattr(mod.msgraph_broker, "send_bound_reply", broker.send_bound_reply)
    return mod, d1, broker


def _rows(d1, action: str) -> list[dict]:
    return [m for a, m in d1.events() if a == action]


def _alerts(d1) -> list[dict]:
    return [
        m
        for m in _rows(d1, "REPLY_FAILED")
        if str(m.get("reason", "")).startswith("job_reply_undelivered")
    ]


def test_the_models_own_words_are_never_sent_the_brokers_are(seat) -> None:
    mod, d1, broker = seat
    _drafting_wake(mod)
    assert _bind(mod, {"job_id": JOB})["bound"] is True
    _draft(mod, [ADMIN], body=OWN)
    assert [s["comment"] for s in broker.sends] == [WORDS]
    assert broker.sends[0]["binding"] == {"kind": "drafting_job", "job_id": JOB}
    sent = _rows(d1, "REPLY_SENT")
    assert (
        len(sent) == 1
        and sent[0]["seat_delivered_job"] == JOB
        and sent[0]["in_reply_to"] == GRAPH_ID
    )
    # The end of the turn finds the reply sent and sends nothing more.
    mod.on_turn_end(session_id=WAKE)
    assert len(broker.sends) == 1 and not _alerts(d1)


def test_a_turn_that_drafted_nothing_still_gets_the_reply_out(seat) -> None:
    """The 11-05 shape: every draft the model made was refused before the relay."""
    mod, d1, broker = seat
    _drafting_wake(mod)
    mod.on_turn_end(session_id=WAKE)
    assert broker.binds == [{"kind": "drafting_job", "job_id": JOB}]
    assert [s["comment"] for s in broker.sends] == [WORDS]
    assert not _alerts(d1)


def test_both_end_of_turn_hooks_send_it_once(seat) -> None:
    mod, _d1, broker = seat
    _drafting_wake(mod)
    mod.on_post_llm_call(session_id=WAKE, assistant_response="Done.")
    mod.on_turn_end(session_id=WAKE)
    assert len(broker.sends) == 1


def test_a_failed_send_alerts_smd_with_the_job(seat) -> None:
    mod, d1, broker = seat
    broker.fail_send = BrokerError("fixed_wording: nothing was sent", code="FixedWordingRefused")
    _drafting_wake(mod)
    mod.on_turn_end(session_id=WAKE)
    assert broker.sends == []
    (alert,) = _alerts(d1)
    assert alert["wake_kind"] == "drafting_job" and alert["wake_job_id"] == JOB
    assert alert["message_id"] == GRAPH_ID


@pytest.mark.parametrize(
    "verdict",
    [
        {"ok": True, "bound": False, "reason": "the sender is no longer on the reply roster"},
        {"ok": True, "bound": True, "sender": ADMIN, "graph_message_id": GRAPH_ID},
        {
            "ok": True,
            "bound": True,
            "sender": ADMIN,
            "graph_message_id": GRAPH_ID,
            "fixed_wording": ["a", "b"],
        },
    ],
    ids=["bind_refused", "no_words", "two_words"],
)
def test_no_reply_the_seat_can_send_alerts_smd(seat, verdict) -> None:
    mod, d1, broker = seat
    broker.verdict = verdict
    _drafting_wake(mod)
    mod.on_turn_end(session_id=WAKE)
    assert broker.sends == []
    (alert,) = _alerts(d1)
    assert alert["wake_job_id"] == JOB


def test_a_broker_that_cannot_be_reached_alerts_smd(seat, monkeypatch) -> None:
    mod, d1, broker = seat

    def down(_req):
        raise mod.msgraph_broker.MsGraphBrokerUnavailable("broker unreachable")

    monkeypatch.setattr(mod.msgraph_broker, "bind_reply", down)
    _drafting_wake(mod)
    mod.on_turn_end(session_id=WAKE)
    assert broker.sends == [] and len(_alerts(d1)) == 1


@pytest.mark.parametrize("code", ["already_replied", "job_failed", "job_held"])
def test_a_reply_already_sent_or_a_failed_job_is_not_a_new_alert(seat, code) -> None:
    """Already sent is the success; a failed or held job is SMD's through its
    own ``job_stopped`` alert, and the firm is told nothing (2026-11-06).
    FALSIFIER for ``job_held``: on the parent commit it writes a REPLY_FAILED row."""
    mod, d1, broker = seat
    broker.verdict = {"ok": True, "bound": False, "code": code, "reason": "refused"}
    _drafting_wake(mod)
    mod.on_turn_end(session_id=WAKE)
    assert broker.sends == [] and not _alerts(d1)


def test_a_demand_wake_is_untouched(seat) -> None:
    mod, d1, broker = seat
    _wake(mod)
    mod.on_turn_end(session_id=WAKE)
    assert broker.binds == [] and broker.sends == [] and not _alerts(d1)


def test_the_heartbeat_names_the_undelivered_job_reply() -> None:
    from shared import heartbeat

    assert heartbeat._reply_code("REPLY_FAILED", "job_reply_undelivered: the send failed") == (
        "reply_failed:job_reply_undelivered"
    )


def test_the_alert_row_carries_no_body(seat) -> None:
    mod, d1, broker = seat
    broker.fail_send = BrokerError("refused", code="FixedWordingRefused")
    _drafting_wake(mod)
    mod.on_turn_end(session_id=WAKE)
    assert WORDS not in json.dumps(_alerts(d1))
