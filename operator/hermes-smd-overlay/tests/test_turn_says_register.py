"""The relay's fixed-sentence register: several sentences in a turn, every
part compared (``plugins/hermes-smd-reply/relay_checks``, step 5a):

* every sentence a turn notes is kept, in call order and de-duplicated, and the
  reply must carry all of them (an email asking for several documents gets one
  sentence per job, and keeping only the last told the firm about one);
* every part the draft carries (text and HTML) must hold them.

Names, numbers and addresses are invented.
"""

from __future__ import annotations

from typing import Any

from shared import turn_says
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


def test_a_none_alone_holds_nothing_and_tier_two_waits_for_a_real_sentence(mod: Any) -> None:  # noqa: F811
    """Review 2026-11-19: on a "none"-only turn tier 2 ran over the whole body.
    It applies only beside a real fixed sentence (tier 1 is the every-reply
    check). FALSIFIER: apply tier 2 on a none-only turn."""
    origin = _email()
    _note(mod, "records_job_submit", {"say": "none"})
    assert _check(mod, origin, text="Got it.") is True
    assert _check(mod, origin, text="Our team will look at the schedule.") is True


def test_tier_two_never_applies_to_a_reply_to_smds_own_address() -> None:
    """An internal guide seat's help turn may name SMD to SMD."""
    say = "Let me research that and get back to you in this thread."
    body = f"{say}\n\nSMD's onboarding notes cover this."
    assert turn_says.send_problem((say,), body, "", "paralegal@firm.example")
    assert turn_says.send_problem((say,), body, "", "team@smd.services") == ""


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


def test_the_no_hand_off_phrases_catch_a_hand_off_and_spare_legal_usage() -> None:
    """Review 2026-11-19: "is with us" plus "we hit a problem on our end and
    passed this to SMD's team" passed the block rule. FALSIFIER: drop the
    phrases and it passes again; add "handed"/"forwarded"/"a person"/"passed
    to"/"on our side" and the ordinary sentences below are refused."""

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


def test_tier_one_spares_court_rule_citations_and_class_hearings() -> None:
    """Review 2026-11-19: "[rule" matched "[Rule 26(f)]", and "hold class"
    matched class certification. FALSIFIER: the bare "[rule" or "hold class"."""
    for legal in (
        "The deadline runs under [Rule 3.1110].",
        "We met and conferred under [Rule 26(f)].",
        "The court will hold class certification hearings in May.",
    ):
        assert turn_says.tier1_phrase(legal, "") == "", legal
    # The seat's own tag is not refused this release: rule read-backs carry it.
    assert turn_says.tier1_phrase("Recorded as [rule b45394a0].", "") == ""


def test_a_tier_one_hold_names_the_phrase_and_stops_with_its_own_text() -> None:
    """Review 2026-11-19: the hold reused the fixed-wording text, wrong on a
    turn with no fixed sentence."""
    counts = iter([1, 2, 3])
    hold = turn_says.mechanics_hold(
        "p@firm.example", "It is in the drafting queue.", "", lambda: next(counts), "STOP"
    )
    assert hold[0] == "mechanics" and 'without "drafting queue"' in hold[1].lower()
    assert "fixed sentence" not in hold[1]
    turn_says.mechanics_hold(
        "p@firm.example", "It is in the drafting queue.", "", lambda: next(counts), "STOP"
    )
    assert turn_says.mechanics_hold(
        "p@firm.example", "The drafting queue.", "", lambda: next(counts), "STOP"
    ) == (
        "mechanics_cap",
        "STOP",
    )


def test_a_none_points_document_is_refused_however_it_is_named_and_only_when_claimed() -> None:
    """Review 2026-11-19: the exact substring let "the brief for 4410" through.
    FALSIFIER: match "mediation brief for matter 4410" only."""
    entry = turn_says.forbid_entry("4410", ["mediation brief", "brief"])
    for claimed in (
        "Received. The brief for 4410 is with us.",
        "We are preparing the 4410 brief now.",
    ):
        assert turn_says.block_problem((entry,), claimed), claimed
    for other in (
        "The trial in 4410 is set for March 3.",
        "The brief for 4410 is due to the mediator on the 9th.",
        "The brief for matter 5520 is with us.",
    ):
        assert turn_says.block_problem((entry,), other) == "", other


def test_a_second_mechanics_hold_keeps_its_own_redraft_guidance() -> None:
    """Review 2026-11-19: hold 2 got the generic second-hold text, which
    invites the same wording; past the cap it said "fixed sentence"."""
    from tests.conftest import load_plugin

    notice = load_plugin("hermes-smd-reply").notice
    detail = turn_says.MECHANICS_REDRAFT.format(phrase="this turn")
    second = notice.render(notice.HoldNotice("mechanics_named", "p@firm.example", "m", 2, detail))
    assert "without the phrase below" in second and 'without "this turn"' in second
    stopped = notice.render(
        notice.HoldNotice("mechanics_named", "p@firm.example", "m", 3, notice.MECHANICS_STOP)
    )
    assert "Do not draft this reply again." in stopped and "fixed sentence" not in stopped


def test_a_truthful_negative_about_a_none_points_document_passes() -> None:
    """Review 2026-11-19: "started" inside "not started" held a true sentence.
    FALSIFIER: drop the negation test and the first sentence is held."""
    entry = turn_says.forbid_entry("4410", ["mediation brief", "brief"])
    assert (
        turn_says.block_problem((entry,), "I have not started the mediation brief for matter 4410.")
        == ""
    )
    assert turn_says.block_problem((entry,), "I've started the mediation brief for matter 4410.")
