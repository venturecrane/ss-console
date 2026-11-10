"""A held reply ends in a fixed line to the sender, and redrafts are capped.

WHY (ss-console #3946, gate 18). A turn whose reply the checks held used to end
with the sender hearing nothing. On a seat that authors
``send_policy.reply.held_notice: true`` code sends the sender one fixed line,
once, and a reply held more than ``max_redrafts`` times (default 2) is not
redrafted again. Driven through the real reply plugin's hooks
(``on_post_tool_call`` for each draft, ``on_post_llm_call`` for the turn's end)
with the plugin's own relay, held store and audit rows; only the broker
transmit is a recording stub.

FALSIFIER: on main the plugin had no held line and no cap, so the first test's
``sent`` is empty and the cap test's fourth draft is sent.
"""

from __future__ import annotations

import re

import pytest
import yaml

from shared import answered_messages
from shared.pending_acts import PENDING_ACTS
from tests.conftest import load_plugin
from tests.test_reply import _draft, _record_origin, relay_mod  # noqa: F401 - fixture reuse
from tests.test_reply_text_delivery import gate_calls  # noqa: F401 - fixture reuse

SESSION = "s-held"
SENDER = "greg@whitfield.example"
MID = "msg_held"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    mod = load_plugin("hermes-smd-reply")
    monkeypatch.setattr(mod, "_TURN_SENDS", mod.text_delivery.TurnSends(), raising=False)
    monkeypatch.setattr(mod, "_TEXT_DELIVERED", mod.text_delivery.DeliveredOnce(), raising=False)
    monkeypatch.setattr(mod, "_HOLD_COUNTS", mod.held_notice.HoldCounter(), raising=False)
    monkeypatch.setattr(mod, "_HELD_LINES", mod.held_notice.Once(), raising=False)
    answered_messages._reset_for_tests()
    PENDING_ACTS.clear()
    yield
    answered_messages._reset_for_tests()
    PENDING_ACTS.clear()


def _author(mod, **reply) -> None:
    doc = yaml.safe_load(mod._YAML_PATH.read_text())
    doc["send_policy"] = {"reply": reply}
    mod._YAML_PATH.write_text(yaml.safe_dump(doc))


def _held_draft(mod, call: str) -> None:
    """A draft the relay holds: addressed to someone else (recipient lock)."""
    mod.on_post_tool_call(
        tool_name="mcp_agentmail_create_draft",
        args=_draft(["someone-else@example.org"]),
        session_id=SESSION,
        tool_call_id=call,
    )


def _held_reasons(d1) -> list[str]:
    return [m["reason"] for a, m in d1.events() if a == "REPLY_HELD"]


def test_a_held_turn_ends_in_the_fixed_line_once(relay_mod, gate_calls):  # noqa: F811
    mod, d1, sent = relay_mod
    _author(mod, held_notice=True)
    _record_origin(sender=SENDER, message_id=MID, session=SESSION)
    mod.on_pre_llm_call(session_id=SESSION, user_message="(prompt)")
    _held_draft(mod, "c1")
    assert sent == [] and _held_reasons(d1)[-1] == "recipient_mismatch"
    mod.on_post_llm_call(session_id=SESSION, assistant_response="I drafted it.")
    assert sent == [{"message_id": MID, "text": mod.held_notice.FIXED_LINE, "html": ""}]
    mod.on_post_llm_call(session_id=SESSION, assistant_response="I drafted it.")
    assert len(sent) == 1, "the line goes once per email"


def test_a_seat_that_authors_nothing_still_says_nothing(relay_mod, gate_calls):  # noqa: F811
    mod, _d1, sent = relay_mod
    _record_origin(sender=SENDER, message_id=MID, session=SESSION)
    mod.on_pre_llm_call(session_id=SESSION, user_message="(prompt)")
    _held_draft(mod, "c1")
    mod.on_post_llm_call(session_id=SESSION, assistant_response="I drafted it.")
    assert sent == []


def test_an_answered_email_gets_no_line(relay_mod, gate_calls):  # noqa: F811
    mod, _d1, sent = relay_mod
    _author(mod, held_notice=True)
    _record_origin(sender=SENDER, message_id=MID, session=SESSION)
    mod.on_pre_llm_call(session_id=SESSION, user_message="(prompt)")
    _held_draft(mod, "c1")
    mod.on_post_tool_call(
        tool_name="mcp_agentmail_create_draft",
        args=_draft([SENDER]),
        session_id=SESSION,
        tool_call_id="c2",
    )
    assert len(sent) == 1 and sent[0]["text"] != mod.held_notice.FIXED_LINE
    mod.on_post_llm_call(session_id=SESSION, assistant_response="Replied.")
    assert len(sent) == 1


@pytest.mark.parametrize(("authored", "relayed"), [({"held_notice": True}, False), ({}, True)])
def test_redrafts_past_the_cap_are_refused(relay_mod, gate_calls, authored, relayed):  # noqa: F811
    mod, d1, sent = relay_mod
    if authored:
        _author(mod, **authored)
    _record_origin(sender=SENDER, message_id=MID, session=SESSION)
    mod.on_pre_llm_call(session_id=SESSION, user_message="(prompt)")
    for call in ("c1", "c2", "c3"):
        _held_draft(mod, call)
    # The fourth draft is addressed correctly; past the default cap of 2 it
    # does not relay at all.
    mod.on_post_tool_call(
        tool_name="mcp_agentmail_create_draft",
        args=_draft([SENDER]),
        session_id=SESSION,
        tool_call_id="c4",
    )
    if relayed:
        assert len(sent) == 1, "a seat that authors nothing relays as before"
        return
    assert sent == []
    assert _held_reasons(d1)[-1] == mod.held_notice.REDRAFT_LIMIT_REASON
    mod.on_post_llm_call(session_id=SESSION, assistant_response="I could not.")
    assert [s["text"] for s in sent] == [mod.held_notice.FIXED_LINE]


def test_an_authored_cap_is_honoured(relay_mod, gate_calls):  # noqa: F811
    mod, d1, sent = relay_mod
    _author(mod, held_notice=True, max_redrafts=0)
    _record_origin(sender=SENDER, message_id=MID, session=SESSION)
    mod.on_pre_llm_call(session_id=SESSION, user_message="(prompt)")
    _held_draft(mod, "c1")
    mod.on_post_tool_call(
        tool_name="mcp_agentmail_create_draft",
        args=_draft([SENDER]),
        session_id=SESSION,
        tool_call_id="c2",
    )
    assert sent == [] and _held_reasons(d1)[-1] == mod.held_notice.REDRAFT_LIMIT_REASON


def test_the_fixed_line_promises_nothing() -> None:
    mod = load_plugin("hermes-smd-reply")
    line = mod.held_notice.FIXED_LINE
    assert not re.search(r"\$|\d|—|–", line)
    for word in ("hour", "minute", "day", "week", "soon", "today", "tomorrow"):
        assert word not in line.lower()
    assert mod.held_notice.policy({"send_policy": {"reply": {"max_redrafts": 1}}}).enabled is False
    assert mod.held_notice.policy(None).enabled is False
