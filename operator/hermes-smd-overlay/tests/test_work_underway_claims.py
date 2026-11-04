"""A reply's "still being prepared" comes from the job store, not the model.

A&P, 2026-11-04 15:30 MST: answering a "Yes" on a rule, the Operator wrote
"The revised brief for matter 200045 is still being prepared with your
corrections" while no drafting job was running (the last failed at 14:48).
The send gate now refuses that sentence unless a live job backs it.
"""

from __future__ import annotations

import pytest

from shared import work_claims
from tests.conftest import load_plugin

LIVE_CLAIM = "The revised brief for matter 200045 is still being prepared with your corrections."


def _job(state, matter="200045"):
    return {"id": "j1", "state": state, "matter_number": matter}


# --- the pure half -----------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        LIVE_CLAIM,
        "We are currently drafting the brief.",
        "Your memo is in progress.",
        "The chronology is underway and I'll send it when done.",
        "We're still working on the discovery responses.",
        # A "no" in another clause does not launder the claim.
        "No worries, the brief is still being prepared.",
    ],
)
def test_in_progress_claims_are_recognized(text):
    assert work_claims.underway_claims(text)


@pytest.mark.parametrize(
    "text",
    [
        "Your rule is recorded and will apply to future mediation briefs.",
        "Nothing is being prepared for matter 200045 right now.",
        "The brief isn't being drafted yet; reply 'go' and we will start it.",
        "The last drafting run failed, so no new draft is in progress.",
        "Thanks, Chris.",
    ],
)
def test_true_or_unrelated_sentences_are_not_claims(text):
    assert work_claims.underway_claims(text) == []


def test_a_claim_with_no_live_job_is_unsupported():
    claim = work_claims.unsupported_claim(LIVE_CLAIM, lambda: [])
    assert claim and "being prepared" in claim


def test_a_failed_job_does_not_back_the_claim():
    live = work_claims.live_work([lambda: [_job("failed"), _job("delivered")]])
    assert work_claims.unsupported_claim(LIVE_CLAIM, lambda: live)


def test_a_live_job_on_the_named_matter_backs_the_claim():
    for state in ("submitted", "running", "held"):
        live = work_claims.live_work([lambda s=state: [_job(s)]])
        assert work_claims.unsupported_claim(LIVE_CLAIM, lambda live=live: live) is None


def test_a_live_job_on_another_matter_does_not_back_the_claim():
    live = work_claims.live_work([lambda: [_job("running", matter="199001")]])
    assert work_claims.unsupported_claim(LIVE_CLAIM, lambda: live)


def test_a_read_that_cannot_answer_refuses_the_claim():
    def broken():
        raise RuntimeError("broker down")

    assert work_claims.unsupported_claim(LIVE_CLAIM, broken)


def test_no_claim_means_no_read():
    def must_not_run():
        raise AssertionError("read the ledger for a reply that claims nothing")

    assert work_claims.unsupported_claim("Your rule is recorded.", must_not_run) is None


# --- the send gate -----------------------------------------------------------


@pytest.fixture
def plugin(monkeypatch):
    mod = load_plugin("hermes-smd-establishment")
    mod._CONFIRMED_STASH.clear()
    mod._READBACK_OWED.clear()
    return mod


def _send(mod, body):
    return mod.on_pre_tool_call(
        tool_name="mcp_msgraph_mail_reply_message",
        session_id="sess-w",
        args={"message_id": "m-1", "body": f"Hi Chris,\n\n{body}\n\nThanks"},
    )


def test_the_live_ap_sentence_is_blocked_with_no_job_running(plugin, monkeypatch):
    """Fails on the old code: nothing read the job store before a send."""
    monkeypatch.setattr(
        plugin,
        "_LIVE_WORK_READER",
        lambda: work_claims.live_work([lambda: [_job("failed")]]),
    )
    verdict = _send(plugin, LIVE_CLAIM)
    assert verdict is not None and verdict["action"] == "block"
    assert "200045" in verdict["message"]


def test_the_same_sentence_ships_while_the_job_runs(plugin, monkeypatch):
    monkeypatch.setattr(
        plugin,
        "_LIVE_WORK_READER",
        lambda: work_claims.live_work([lambda: [_job("running")]]),
    )
    assert _send(plugin, LIVE_CLAIM) is None


def test_an_ordinary_reply_never_touches_the_job_store(plugin, monkeypatch):
    def must_not_run():
        raise AssertionError("ledger read on a reply with no claim")

    monkeypatch.setattr(plugin, "_LIVE_WORK_READER", must_not_run)
    assert _send(plugin, "Your rule is recorded and applies to future briefs.") is None
