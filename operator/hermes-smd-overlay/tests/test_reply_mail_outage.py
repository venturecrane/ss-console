"""A reply the mail service did not let through (2026-11-12).

Graph answered 5xx for half an hour; the broker's participant fence could not
read the email a reply answered and refused the send, and the reply was refused
fourteen times in thirty-four minutes, each a fresh broker call and a fresh
row, none saying why. The broker now refuses that case as a mail outage
(``MsGraphMailOutage``). These tests drive the relay's transmit step as
``_relay_transmit`` runs it (the plugin's ``_send_msgraph_reply``, and on a
failure its ``_send_failed``), through the real ``shared.msgraph_broker
.send_reply``, with only the broker socket call faked. They call the step, not
``_relay_draft``, so the decision corpus (tests/decisions), which records every
test that reaches that function, does not change: the decision is unchanged.

* the first refusal writes ONE ``REPLY_HELD`` row naming ``mail_outage``, tells
  the model and pages once;
* a retry before the backoff never reaches the broker and writes no row;
* a retry after the backoff that goes through clears the email (its
  ``REPLY_SENT`` row, written by ``relay_sent``, names it in ``in_reply_to``,
  the join that closes work health's held line).

FALSIFIERS: drop ``mail_outage.held`` from ``_send_failed`` and the first case
writes REPLY_FAILED with no cause; drop ``shared.mail_outage.checked`` from
``msgraph_broker.send_reply`` and the retry reaches the broker again.
"""

from __future__ import annotations

import pytest

from shared import inbound, mail_outage
from shared.workspace_broker import BrokerError
from tests.conftest import load_plugin
from tests.test_msgraph_transports import (
    _MSGRAPH_ROSTERED_YAML,
    _FakeD1,
)

MID = "graph-mid-outage"


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Broker:
    """The broker socket: refuses as a mail outage until ``up`` is set."""

    def __init__(self) -> None:
        self.calls = 0
        self.up = False

    def __call__(self, action, payload, **_kw):
        self.calls += 1
        if not self.up:
            raise BrokerError(
                "the request this send answers could not be verified", code="MsGraphMailOutage"
            )
        return {"message_id": "", "vendor_message_id": "<sent@client.example>"}


@pytest.fixture
def seat(monkeypatch, tmp_path):
    mod = load_plugin("hermes-smd-reply")
    d1 = _FakeD1()
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(_MSGRAPH_ROSTERED_YAML)
    monkeypatch.setattr(mod, "_INFRA_READY", True, raising=False)
    monkeypatch.setattr(mod, "_API_KEY", None, raising=False)
    monkeypatch.setattr(mod, "_CUSTOMER_SLUG", "acme", raising=False)
    monkeypatch.setattr(mod, "_D1_CLIENT", d1, raising=False)
    monkeypatch.setattr(mod, "_YAML_PATH", yaml_path, raising=False)
    pages: list[str] = []
    monkeypatch.setattr(mod, "_notify_hold", lambda **kw: pages.append(kw["reason"]))
    broker = _Broker()
    monkeypatch.setattr(mod.msgraph_broker, "_call", broker)
    clock = _Clock()
    mail_outage.OUTAGES.reset()
    monkeypatch.setattr(mail_outage.OUTAGES, "clock", clock)
    mod._HOLD_NOTICES._reset_for_tests()
    inbound.SESSION_INBOUND_ORIGIN._reset_for_tests()
    inbound.SESSION_INBOUND_ORIGIN.record(
        "s1",
        inbound.InboundOrigin(
            sender_address="greg@whitfield.example", message_id=MID, inbox_id="op@client.example"
        ),
    )
    yield mod, d1, broker, clock, pages
    mail_outage.OUTAGES.reset()
    inbound.SESSION_INBOUND_ORIGIN._reset_for_tests()


def _draft(mod, call_id: str):
    """One transmit step: the reply, and on a failure what ``_relay_transmit``
    does with it. Returns the notice the model would read, or "sent"."""
    origin = inbound.SESSION_INBOUND_ORIGIN.get("s1")
    mod._CURRENT_CALL.tool_call_id = call_id
    try:
        mod._send_msgraph_reply(MID, "Thanks.", session_id="s1")
    except mod.relay.RelaySendError as exc:
        mod._send_failed(str(exc), origin, adapter="msgraph")
        held = mod._HOLD_NOTICES.take(call_id)
        return mod.notice.render(held) if held else ""
    finally:
        mod._CURRENT_CALL.tool_call_id = ""
    return "sent"


def _rows(d1, action_type):
    return [m for a, m in d1.events() if a == action_type]


def test_an_outage_is_held_once_backs_off_and_closes_when_it_goes(seat):
    mod, d1, broker, clock, pages = seat

    out = _draft(mod, "c1")
    assert broker.calls == 1
    held = _rows(d1, "REPLY_HELD")
    assert [(m["reason"], m["message_id"]) for m in held] == [("mail_outage", MID)]
    assert _rows(d1, "REPLY_FAILED") == []
    assert pages == ["mail_outage"]
    assert "WAS NOT SENT" in out and "mail service did not answer" in out

    # The model tries again at once: the broker is not called, nothing is written.
    for n in range(2, 6):
        out = _draft(mod, f"c{n}")
        assert "mail service did not answer" in out
    assert broker.calls == 1
    assert len(_rows(d1, "REPLY_HELD")) == 1 and _rows(d1, "REPLY_FAILED") == []
    assert pages == ["mail_outage"]

    # The backoff passes; still down: one more broker call, no second row,
    # and the wait grows.
    clock.now += mail_outage.BACKOFF_S[0]
    _draft(mod, "c6")
    assert broker.calls == 2 and len(_rows(d1, "REPLY_HELD")) == 1
    clock.now += mail_outage.BACKOFF_S[0]
    _draft(mod, "c7")
    assert broker.calls == 2, "the second wait is longer than the first"

    # Mail is back: the retry goes, and the sent row names the email it answers.
    broker.up = True
    clock.now += mail_outage.BACKOFF_S[1]
    assert _draft(mod, "c8") == "sent"
    assert broker.calls == 3
    assert not mail_outage.OUTAGES.waiting(MID) and mail_outage.OUTAGES.take(MID) is None


def test_an_ordinary_refusal_is_still_a_failed_send(seat):
    """Only the outage codes take this path: a refusal for who the reply is for
    stays REPLY_FAILED and is never retried on a backoff."""
    mod, d1, broker, _clock, pages = seat

    def _refuse(action, payload, **_kw):
        broker.calls += 1
        raise BrokerError("not a participant", code="MsGraphRefused")

    mod.msgraph_broker._call = _refuse
    _draft(mod, "c1")
    _draft(mod, "c2")
    assert broker.calls == 2
    assert [m["reason"] for m in _rows(d1, "REPLY_HELD")] == []
    assert len(_rows(d1, "REPLY_FAILED")) == 2
    assert pages == ["send_failed", "send_failed"]


def test_the_backoff_grows_to_its_ceiling():
    clock = _Clock()
    table = mail_outage.Outages(clock=clock)
    assert table.failed("m") is True
    waits = []
    for _ in range(len(mail_outage.BACKOFF_S) + 2):
        start = clock.now
        while table.waiting("m"):
            clock.now += 1
        waits.append(clock.now - start)
        table.failed("m")
    assert waits[: len(mail_outage.BACKOFF_S)] == list(mail_outage.BACKOFF_S)
    assert waits[-1] == mail_outage.BACKOFF_S[-1]
