"""``logic_in_code.rules_by_hand`` (stopgap, 2026-11-20): a drafting rule, or a
reply about one, gets one fixed sentence; nothing about rules is created,
listed, confirmed, read back or written to; SMD is owed the rule.

FALSIFIER: on the parent commit ``establish_propose`` reaches the broker's
propose verb, a tagged "yes" stashes a confirmation and injects its note, and
these tests fail. With the switch absent every existing establishment test
passes unchanged.
"""

from __future__ import annotations

import json

import pytest

from shared import rules_by_hand, smd_owed, turn_says
from shared.inbound import SESSION_INBOUND_ORIGIN, SESSION_TAINT, InboundOrigin
from tests.conftest import load_plugin

SENDER = "pat@example-firm.test"
SESSION = "sess-rbh"
MSG = "<m-rbh-1@example-firm.test>"
IDS = ("a1b2c3d4", "b2c3d4e5", "c3d4e5f6", "d4e5f6a7", "e5f6a7b8", "f6a7b8c9", "0a1b2c3d")


class _Config:
    connectors: dict = {}
    admins = [SENDER]

    def sender_is_admin(self, sender):
        return str(sender).strip().lower() == SENDER

    def sender_on_roster(self, sender):
        return True


class _CustomerConfig:
    @classmethod
    def from_volume(cls, path=None):
        return _Config()


def _row(pid):
    return {
        "proposal_id": pid,
        "kind": "rule",
        "state": "open",
        "scope": "person",
        "subject": {"person": SENDER},
        "text": f"Lead every memo with the answer ({pid}).",
        "readback": f"[rule {pid}] Lead every memo with the answer.",
        "instructed_by": SENDER,
        "for_admin": False,
    }


@pytest.fixture
def seat(monkeypatch, tmp_path):
    def make(mode):
        cfg = tmp_path / "customer.yaml"
        cfg.write_text(f"logic_in_code:\n  rules_by_hand: {mode}\n" if mode else "customer_id: x\n")
        monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(cfg))
        mod = load_plugin("hermes-smd-establishment")
        state: dict = {"broker": [], "owed": [], "owed_ok": True}

        def broker(payload):
            state["broker"].append(payload)
            if payload.get("action") == "establish_pending":
                return {"ok": True, "pending": [_row(p) for p in IDS]}
            return {"ok": True, "proposal_id": "9f9f9f9f", "readback": "[rule 9f9f9f9f] x"}

        def owed(payload):
            state["owed"].append(payload)
            return {"ok": True, "smd_told": state["owed_ok"]}

        monkeypatch.setattr(mod, "_broker_request", broker)
        monkeypatch.setattr(smd_owed, "_request", owed)
        monkeypatch.setattr(mod, "CustomerConfig", _CustomerConfig)
        # No reply plugin on this seat, whatever an earlier test loaded: nothing
        # is owed or registered, so the switch alone decides what is said.
        monkeypatch.setattr(turn_says, "_NOTE", None)
        monkeypatch.setenv("SMD_ADMIN_POSSESSION_DB_PATH", str(tmp_path / "possession.db"))
        mod._ADMIN_STASH.clear()
        mod._CONFIRMED_STASH.clear()
        mod._READBACK_OWED.clear()
        SESSION_INBOUND_ORIGIN._origins.clear()
        SESSION_TAINT._tainted.clear()
        SESSION_INBOUND_ORIGIN.record(
            "", InboundOrigin(sender_address=SENDER, message_id=MSG, inbox_id="op@x")
        )
        SESSION_INBOUND_ORIGIN.note_turn_prompt(SESSION, MSG)
        SESSION_INBOUND_ORIGIN.bind(SESSION, MSG)
        return mod, state

    yield make
    SESSION_TAINT._tainted.clear()


def _propose(mod):
    return json.loads(
        mod._propose(
            {"scope": "person", "text": "Lead every memo with the answer."}, session_id=SESSION
        )
    )


def test_switch_off_proposes_as_before(seat):
    mod, state = seat(None)
    out = _propose(mod)
    assert "say" not in out
    assert [p["action"] for p in state["broker"]] == ["establish_propose"]
    assert state["owed"] == []


def test_observe_is_off(seat):
    mod, state = seat("observe")
    assert not rules_by_hand.on()
    assert "say" not in _propose(mod)


def test_propose_says_one_sentence_records_owed_and_creates_nothing(seat):
    mod, state = seat("enforce")
    assert mod.on_pre_tool_call(tool_name=mod.TOOL_PROPOSE, session_id=SESSION, args={}) is None
    assert _propose(mod) == {"say": rules_by_hand.SAY}
    assert state["broker"] == []  # no proposal row
    assert len(state["owed"]) == 1
    sent = state["owed"][0]
    assert sent["kind"] == "rule_stated" and sent["request_ref"] == MSG
    assert "record it by hand" in sent["detail"] and SENDER in sent["detail"]


def test_broker_failure_says_none(seat):
    mod, state = seat("enforce")
    state["owed_ok"] = False
    out = _propose(mod)
    assert out["say"] == "none" and "Say nothing about this rule" in out["instruction"]


def test_pending_and_rule_submit_are_blocked(seat):
    mod, _ = seat("enforce")
    for tool, args in (
        (mod.TOOL_PENDING, {}),
        (mod.TOOL_SUBMIT, {"scope": "person", "proposal_id": IDS[0]}),
    ):
        verdict = mod.on_pre_tool_call(tool_name=tool, session_id=SESSION, args=args)
        assert verdict and verdict["action"] == "block"


def test_tagged_yes_over_seven_open_rules_confirms_nothing(seat, monkeypatch):
    mod, state = seat("enforce")
    commits: list = []
    monkeypatch.setattr(mod, "_submit", lambda *a, **k: commits.append(a) or "{}")
    monkeypatch.setattr(mod, "_decline_rule", lambda *a, **k: commits.append(a) or True)
    message = f"[rule {IDS[0]}] yes and [rule {IDS[1]}] yes"
    context = mod.on_pre_llm_call(session_id=SESSION, sender_id=SENDER, user_message=message)[
        "context"
    ]
    assert commits == []
    assert SESSION not in mod._CONFIRMED_STASH
    for pid in IDS:
        assert pid not in context
    assert "[rule" not in context and "Reply yes" not in context
    assert rules_by_hand.SAY in context
    assert len(state["owed"]) == 1 and state["owed"][0]["detail"].startswith("reply about a rule")


def test_bare_yes_with_open_rules_shows_nothing_about_them(seat):
    mod, state = seat("enforce")
    context = mod.on_pre_llm_call(session_id=SESSION, sender_id=SENDER, user_message="yes")[
        "context"
    ]
    assert SESSION not in mod._CONFIRMED_STASH
    assert not any(pid in context for pid in IDS)
    assert len(state["owed"]) == 1


def test_no_rule_letter_is_dispatched(seat, monkeypatch):
    mod, _ = seat("enforce")
    sent: list = []
    monkeypatch.setattr(mod.rule_dispatch, "notify_outcome", lambda **k: sent.append(k))
    assert mod._dispatch_outcome_letter(kind="lapsed", row=_row(IDS[0])) is True
    assert sent == []


def test_switch_off_rule_letter_still_dispatched(seat, monkeypatch):
    mod, _ = seat(None)
    sent: list = []

    class _N:
        sent = True
        outcome_unknown = False

    monkeypatch.setattr(mod.rule_dispatch, "notify_outcome", lambda **k: sent.append(k) or _N())
    assert mod._dispatch_outcome_letter(kind="lapsed", row=_row(IDS[0])) is True
    assert len(sent) == 1
