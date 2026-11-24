"""The Operator never states a limit of its own to a person (2026-11-24, A&P).

An attorney asked for every parent statement on a matter, attached. The
Operator answered "I cannot attach files to this reply", and when he asked why
it could not open the emails, "my document reader cannot open that format ...
This is a limit of how I read files in Smokeball". The owner's rule: where the
Operator cannot do what was asked, it says ``reply_could_not.LOOK_INTO`` and
SMD does the work. The relay holds a reply that states a limit (step 5,
``relay_checks.limit_held``), and the sentence that replaces it is stamped
``looking_into``, so the request still reads NOT FULFILLED and pages SMD.

The texts here are paraphrased and invented for this test (ADR 0081: no client
text in the repo).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from shared import reply_could_not
from shared.recipient_classifier import RecipientClass
from tests.test_relay_steps import SENDER, _Cfg, _held, _origin, mod  # noqa: F401 - the fixture

#: What the Operator said to the attorney, paraphrased.
LIMIT_REPLIES = [
    "I cannot attach files to this reply.",
    "My document reader cannot open that format, so the emails are listed below.",
    "This is a limit of how I read files in Smokeball.",
    "Our email tool does not carry attachments.",
    "That file format is not supported.",
    "We are not set up to send files from the matter.",
    "I don't have a way to attach those emails.",
]


@pytest.mark.parametrize("text", LIMIT_REPLIES)
def test_each_stated_limit_is_found(text: str) -> None:
    assert reply_could_not.stated_limit(text)
    assert reply_could_not.classify(text) in reply_could_not.STATED_CODES


def test_the_owners_sentence_states_no_limit_and_still_reads_not_fulfilled() -> None:
    """FALSIFIER: if LOOK_INTO counted as a limit the redraft could never pass;
    if it carried no code, SMD would never hear of the request."""
    assert reply_could_not.stated_limit(reply_could_not.LOOK_INTO) == ""
    assert reply_could_not.classify(reply_could_not.LOOK_INTO) == "looking_into"
    assert reply_could_not.metadata("Thanks Chris.\n\n" + reply_could_not.LOOK_INTO) == {
        "could_not": "looking_into"
    }
    assert "looking_into" in reply_could_not.CODES


def test_ordinary_answers_state_no_limit() -> None:
    for text in (
        "I've attached the five records you asked for.",
        "The hearing is on November 12 at 9:00 AM.",
        "If I can't find it, I'll let you know right away.",
        "I checked the bill and couldn't find any errors.",
        "My calendar shows the deposition on Friday.",
        "The format of the order is set by the court.",
    ):
        assert reply_could_not.stated_limit(text) == "", text


def test_a_reply_stating_a_limit_is_held_with_the_sentence_to_use(mod: Any) -> None:  # noqa: F811
    origin = _origin()
    held = mod.relay_checks.limit_held(origin, SENDER, LIMIT_REPLIES[1], "")
    assert held is True and _held(mod) == ["limit_named"]
    detail = next(c[2]["_detail"] for c in mod.calls if c[0] == "_held")
    assert reply_could_not.LOOK_INTO in detail and "my document reader cannot open" in detail


def test_the_html_part_is_read_too(mod: Any) -> None:  # noqa: F811
    held = mod.relay_checks.limit_held(
        _origin(), SENDER, "", "<p>I cannot attach files to this reply.</p>"
    )
    assert held is True


def test_the_redraft_passes_and_smd_is_never_held(mod: Any) -> None:  # noqa: F811
    origin = _origin()
    redraft = "Thanks Chris.\n\n" + reply_could_not.LOOK_INTO
    assert mod.relay_checks.limit_held(origin, SENDER, redraft, "") is False
    assert mod.relay_checks.limit_held(origin, "team@smd.services", LIMIT_REPLIES[0], "") is False
    assert _held(mod) == []


def test_past_the_redraft_cap_the_model_is_told_to_stop(mod: Any) -> None:  # noqa: F811
    origin = _origin()
    for _ in range(mod.relay_checks.SAY_MAX_REDRAFTS + 1):
        mod.relay_checks.limit_held(origin, SENDER, LIMIT_REPLIES[0], "")
    details = [c[2]["_detail"] for c in mod.calls if c[0] == "_held"]
    assert details[-1] == mod.notice.LIMIT_STOP
    assert mod.notice.LIMIT_STOP not in details[:-1]


def test_body_and_facts_holds_the_limit_before_any_send(mod: Any, monkeypatch) -> None:  # noqa: F811
    """The check is wired into step 5, the path every drafted and every
    end-of-turn text reply takes. FALSIFIER: remove the call and this passes the
    body on."""
    monkeypatch.setattr(mod, "_file_facts", lambda s, o: None)
    monkeypatch.setattr(mod.relay, "draft_body", lambda a: ("scan", LIMIT_REPLIES[0], ""))
    monkeypatch.setattr(mod, "classify_recipients_typed", lambda *a: RecipientClass.INTERNAL)
    monkeypatch.setattr(mod.relay, "gate_body", lambda t, **k: SimpleNamespace(allowed=True))
    monkeypatch.setattr(mod, "_session_tainted", lambda s: False)
    got = mod.relay_checks.body_and_facts(_Cfg(), None, "s1", {}, _origin(), None, SENDER)
    assert got is None and _held(mod) == ["limit_named"]


def test_the_hold_notice_carries_the_relay_detail(mod: Any) -> None:  # noqa: F811
    text = mod.notice.render(
        mod.notice.HoldNotice("limit_named", SENDER, "m", 2, "limit_named: say the sentence")
    )
    assert "Draft it again" in text and "limit_named: say the sentence" in text
    stop = mod.notice.render(
        mod.notice.HoldNotice("limit_named", SENDER, "m", 3, mod.notice.LIMIT_STOP)
    )
    assert "Do not draft this reply again." in stop
