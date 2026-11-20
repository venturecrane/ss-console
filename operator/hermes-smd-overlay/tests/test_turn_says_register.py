"""The relay's whole-reply register: several sentences in a turn, every part
compared, and a hook's sentence through ``shared.turn_says``.

Three changes to step 5a (``plugins/hermes-smd-reply/relay_checks``):

* every sentence a turn notes is kept, in call order and de-duplicated, and the
  reply must be all of them (an email asking for several documents gets one
  sentence per job, and keeping only the last told the firm about one);
* every part the draft carries (text and HTML) must be that reply;
* a plugin hook (the establishment plugin) registers its sentence into the same
  register through ``shared.turn_says``, so the model cannot reword the reply to
  a confirmation, the numbered list, or a read-back.

Names, numbers and addresses are invented.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from shared import inbound, outbound_gate, turn_says
from tests.conftest import load_plugin
from tests.test_relay_steps import _held, mod  # noqa: F401 - the fixture
from tests.test_reply_turn_say import SESSION, _check, _email, _fresh, _note  # noqa: F401

ONE = "Received. The memo for matter 4417 is with us."
TWO = "Received. The mediation brief for matter 4417 is with us."
THREE = "We could not take the deposition outline for matter 4417: name the deponent."


def _detail(m: Any) -> str:
    (name, args, kwargs) = [c for c in m.calls if c[0] == "_held"][-1]
    return kwargs.get("_detail", "")


def test_three_says_make_one_reply_in_call_order(mod: Any) -> None:  # noqa: F811
    origin = _email()
    for say in (ONE, TWO, THREE):
        _note(mod, "drafting_job_submit", {"say": say})
    assert _check(mod, origin, text=f"{ONE}\n\n{TWO}\n\n{THREE}") is True
    # One line between them is the same reply.
    assert _check(mod, origin, text=f"{ONE}\n{TWO}\n{THREE}") is True


def test_a_reply_carrying_only_the_last_say_is_held_with_the_whole_reply_quoted(mod: Any) -> None:  # noqa: F811
    origin = _email()
    _note(mod, "drafting_job_submit", {"say": ONE})
    _note(mod, "records_job_submit", {"say": TWO})
    assert _check(mod, origin, text=TWO) is False
    assert f'own paragraph: "{ONE}"' in _detail(mod)


def test_the_same_say_twice_is_one_paragraph(mod: Any) -> None:  # noqa: F811
    origin = _email()
    _note(mod, "drafting_job_submit", {"say": ONE})
    _note(mod, "drafting_job_submit", {"say": ONE})
    assert _check(mod, origin, text=ONE) is True


def test_a_none_with_a_real_say_is_the_real_say(mod: Any) -> None:  # noqa: F811
    origin = _email()
    _note(mod, "records_job_submit", {"say": "none"})
    _note(mod, "drafting_job_submit", {"say": ONE})
    assert _check(mod, origin, text=ONE) is True


def test_a_none_alone_holds_nothing_but_refuses_mechanics(mod: Any) -> None:  # noqa: F811
    origin = _email()
    _note(mod, "records_job_submit", {"say": "none"})
    assert _check(mod, origin, text="Got it.") is True
    assert _check(mod, origin, text="We hit a problem on our end and passed this to SMD.") is False


def test_every_part_the_draft_carries_must_be_the_reply(mod: Any) -> None:  # noqa: F811
    """An AgentMail-shaped draft carries text and HTML; other words in the HTML
    would be sent."""
    origin = _email()
    _note(mod, "drafting_job_submit", {"say": ONE})
    assert _check(mod, origin, text=ONE, html=f"<p>{ONE}</p>") is True
    # The block rule holds in the HTML part too: there the sentence is missing.
    assert _check(mod, origin, text=ONE, html="<p>Received. Your memo is in hand.</p>") is False
    assert _check(mod, origin, text=ONE, html=f"<p>{ONE}</p><p>We will write again.</p>") is True
    assert _check(mod, origin, html=f"<p>{ONE}</p>") is True


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
        inbound.SESSION_INBOUND_ORIGIN.record(
            SESSION,
            inbound.InboundOrigin(sender_address=ATTORNEY, message_id="AAMkW=", inbox_id="x"),
        )
        em.on_pre_llm_call(session_id=SESSION, sender_id=ATTORNEY, user_message="Yes.")
    finally:
        em._broker_request = real
    assert {"action": "establish_install_watch", "proposal_id": RULE_A, "run_id": "run-1"} in calls


def test_the_no_hand_off_phrases_catch_a_hand_off_and_spare_legal_usage() -> None:
    """Review 2026-11-19: "is with us" plus "we hit a problem on our end and
    passed this to SMD's team" passed the block rule. FALSIFIER: drop the
    phrases and it passes again; add "handed"/"forwarded"/"a person"/"passed
    to"/"on our side" and the ordinary sentences below are refused."""
    from shared import turn_says

    say = "Received. The memo for matter 4417 is with us."
    for leak in (
        "We hit a problem on our end and passed this to SMD's team.",
        "I've passed your question along.",
        "Our team is looking at it.",
        "There was a problem on our side.",
    ):
        assert turn_says.block_problem((say,), f"{say}\n\n{leak}"), leak
    for legal in (
        "The decision was handed down on March 3.",
        "The court forwarded the order to both sides.",
        "A person with knowledge of the accident will testify.",
        "Title passed to the buyer at closing.",
        "The law is on our side on the lien question.",
    ):
        assert turn_says.block_problem((say,), f"{say}\n\n{legal}") == "", legal
