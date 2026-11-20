"""A hook's sentence joins the relay's register through ``shared.turn_says``.

The establishment plugin's read-back of a person's own rules, its numbered list
and its reply to a confirmation are written by a HOOK, not a tool, so they reach
step 5a (``plugins/hermes-smd-reply/relay_checks``) through
``shared.turn_says.note``. Each is a required paragraph of the reply: other
paragraphs may answer the rest of the email, and the sentence itself may not be
reworded.

Names, numbers and addresses are invented.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from shared import outbound_gate, turn_says
from tests.conftest import load_plugin
from tests.rule_turns import bound_turn, relay_register
from tests.test_relay_steps import _held, mod  # noqa: F401 - the fixture
from tests.test_reply_turn_say import SESSION, _check, _email, _fresh, _note  # noqa: F401

ONE = "Received. The memo for matter 4417 is with us."


def _detail(m: Any) -> str:
    (name, args, kwargs) = [c for c in m.calls if c[0] == "_held"][-1]
    return kwargs.get("_detail", "")


def test_a_plugin_sentence_joins_the_same_register_and_its_slot_is_replaced(mod: Any) -> None:  # noqa: F811
    origin = _email()
    assert turn_says.note(SESSION, origin.message_id, "First version.", slot="establishment")
    assert turn_says.note(SESSION, origin.message_id, "Second version.", slot="establishment")
    assert _check(mod, origin, text="Second version.") is True
    _note(mod, "drafting_job_submit", {"say": ONE})
    assert _check(mod, origin, text=f"Second version.\n\n{ONE}") is True


def test_without_the_reply_plugin_a_plugin_sentence_records_nothing(monkeypatch) -> None:
    monkeypatch.setattr(turn_says, "_NOTE", None)
    assert turn_says.note("s", "m", "Anything.") is False


# ---------------------------------------------------------------------------
# The establishment plugin's sentences, end to end through the relay's check
# ---------------------------------------------------------------------------

ATTORNEY = "paralegal@firm.example"  # the address _email() records as the sender
RULE_A, RULE_B = "1a1a1a1a", "2b2b2b2b"
TEXT = {RULE_A: "In memos, put the answer in the first line.", RULE_B: "In memos, no headings."}


class _Cfg:
    connectors: dict = {}

    @property
    def admins(self):
        return [ATTORNEY]

    def sender_is_admin(self, sender):
        return str(sender or "").lower() == ATTORNEY

    def sender_on_roster(self, sender):
        return True

    def authored_person_name(self, _a):
        return None


@pytest.fixture
def est(monkeypatch, tmp_path):
    em = load_plugin("hermes-smd-establishment")
    state: dict = {"pending": [], "binding": None, "next": iter((RULE_A, RULE_B))}

    def broker(payload):
        action = payload.get("action")
        if action == "establish_pending":
            return {
                "ok": True,
                "pending": [] if payload.get("proposal_id") else list(state["pending"]),
            }
        if action == "establish_readback_for":
            b = state["binding"]
            return {"ok": True, "bound": bool(b), "proposal_ids": b or [], "listed": False}
        if action == "establish_propose":
            return {
                "ok": True,
                "duplicate_of": None,
                "proposal_id": next(state["next"]),
                "scope": payload["scope"],
                "subject": payload["subject"],
                "for_admin": False,
                "readback": payload["text"],
            }
        return {"ok": True, "run_id": "run-1"}

    class _CfgCls:
        @classmethod
        def from_volume(cls, path=None):
            return _Cfg()

    monkeypatch.setattr(em, "_broker_request", broker)
    monkeypatch.setattr(em, "CustomerConfig", _CfgCls)
    monkeypatch.setenv("SMD_ADMIN_POSSESSION_DB_PATH", str(tmp_path / "p.db"))
    yaml = tmp_path / "customer.yaml"
    yaml.write_text("customer_id: x\n", "utf-8")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(yaml))
    for store in (em._ADMIN_STASH, em._CONFIRMED_STASH, em._READBACK_OWED, em._OWN_TURN):
        store.clear()
    outbound_gate._reset_firm_sentences_for_tests()
    if not turn_says.published():  # the reply plugin, when a test loads it, publishes its own
        relay_register(monkeypatch)
    yield em, state


def _row(pid):
    return {
        "proposal_id": pid,
        "kind": "rule",
        "scope": "firm_adjust",
        "subject": {"output_class": "outbound_client", "property": "voice"},
        "text": TEXT[pid],
        "instructed_by": ATTORNEY,
        "for_admin": False,
        "state": "open",
    }


def _turn(em, message):
    """An email turn as the relay sees it, then the establishment hook."""
    origin = _email(message)
    em.on_pre_llm_call(session_id=SESSION, sender_id=ATTORNEY, user_message=message)
    return origin


def test_a_reworded_confirmation_is_refused_with_the_sentence_quoted(mod, est) -> None:  # noqa: F811
    em, state = est
    state["pending"] = [_row(RULE_A)]
    state["binding"] = [RULE_A]
    origin = _turn(em, "Yes.")
    say = em._CONFIRMED_SAY[SESSION]
    assert say == "Got it. That is recorded."
    assert _check(mod, origin, text=say) is True
    assert _check(mod, origin, text="Got it, your rule may not appear in the next draft.") is False
    assert f'own paragraph: "{say}"' in _detail(mod)


def test_a_read_back_is_a_required_block(mod, est) -> None:  # noqa: F811
    em, _state = est
    origin = _turn(em, "From now on, in memos, put the answer in the first line.")
    args = {
        "scope": "firm_adjust",
        "subject": {"output_class": "outbound_client", "property": "voice"},
        "text": TEXT[RULE_A],
        "instructed_by": ATTORNEY,
        "source_ref": "msg-1",
    }
    out = json.loads(em._propose(args, session_id=SESSION))
    assert _check(mod, origin, text=out["readback"]) is True
    # A question in the same email is answered beside the read-back.
    assert _check(mod, origin, text=f"{out['readback']}\n\nThe deadline is in the calendar.")
    # The read-back itself may not be reworded.
    reworded = out["readback"].replace("first line", "opening line")
    assert _check(mod, origin, text=reworded) is False


def test_two_rules_in_one_turn_ask_for_the_answer_once(mod, est) -> None:  # noqa: F811
    em, _state = est
    origin = _turn(em, "From now on, in memos: the answer in the first line, and no headings.")
    base = {
        "scope": "firm_adjust",
        "subject": {"output_class": "outbound_client", "property": "voice"},
        "instructed_by": ATTORNEY,
        "source_ref": "msg-1",
    }
    json.loads(em._propose({**base, "text": TEXT[RULE_A]}, session_id=SESSION))
    out = json.loads(em._propose({**base, "text": TEXT[RULE_B]}, session_id=SESSION))
    expected = f"{TEXT[RULE_A]}\n\n{TEXT[RULE_B]}\n\nReply yes to confirm both."
    assert out["readback"] == expected
    assert out["readback"].count("Reply yes") == 1
    assert em._READBACK_OWED[SESSION] == [expected]
    assert _check(mod, origin, text=expected) is True


def test_a_turn_the_establishment_plugin_says_nothing_about_is_as_before(mod, est) -> None:  # noqa: F811
    em, _state = est
    origin = _turn(em, "Please pull the file for matter 4417.")
    assert _check(mod, origin, text="Anything the model wrote.") is True


def test_ask_line_counts_in_words() -> None:
    rc = load_plugin("hermes-smd-establishment").readback_confirm
    assert [rc.ask_line(n) for n in (1, 2, 3, 12)] == [
        "Reply yes to confirm.",
        "Reply yes to confirm both.",
        "Reply yes to confirm all three.",
        "Reply yes to confirm all 12.",
    ]


# ---------------------------------------------------------------------------
# An own rule committed by code whose install fails is paged (logged at ERROR)
# ---------------------------------------------------------------------------


def test_the_install_check_pages_each_failure_at_error(caplog) -> None:
    """The broker reads each due run (``establish_install_check``, persisted
    beside the bindings, so a restart does not forget it); each failure it
    returns is logged at ERROR, ids only, which is what reaches monitoring."""
    rc = load_plugin("hermes-smd-establishment").readback_confirm
    failed = [{"proposal_id": RULE_A, "run_id": "run-9", "why": "its install ended refused"}]
    with caplog.at_level("ERROR"):
        assert rc.check_installs(lambda _p: {"ok": True, "failed": failed}) == [RULE_A]
    assert [r.levelname for r in caplog.records if "run-9" in r.getMessage()] == ["ERROR"]
    assert rc.check_installs(lambda _p: {"ok": True, "failed": []}) == []


def test_the_code_commit_is_watched_by_the_broker(est) -> None:
    em, state = est
    state["pending"] = [_row(RULE_A)]
    state["binding"] = [RULE_A]
    calls: list[dict] = []
    real = em._broker_request

    def broker(payload):
        calls.append(payload)
        return real(payload)

    em._broker_request = broker
    try:
        bound_turn(SESSION, ATTORNEY, "AAMkW=")
        em.on_pre_llm_call(session_id=SESSION, sender_id=ATTORNEY, user_message="Yes.")
    finally:
        em._broker_request = real
    assert {"action": "establish_install_watch", "proposal_id": RULE_A, "run_id": "run-1"} in calls
