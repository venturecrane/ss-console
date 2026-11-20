"""The drafting lane's fixed sentences that were prose or untrue (review 2026-11-19).

* A request naming no matter number answered with prose the model could relay
  ("nothing was queued"); it is now a fixed sentence asking for the number.
* An email whose draft of this kind was already queued always got the
  fresh-draft acknowledgment; a revision now gets the revise sentence, and a
  draft already filed or on hold gets the sentence true for it, never a promise
  of a reply "when it's filed". The broker returns the queued job's mode and
  state; never its id.

Matter numbers and addresses are invented.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.conftest import load_plugin
from tests.test_relay_steps import mod  # noqa: F401 - the fixture
from tests.test_reply_turn_say import _email, _fresh  # noqa: F401

SESSION = "mail-turn-9"


def _submit(monkeypatch, answer: dict, **args: Any) -> dict:
    drafting = load_plugin("hermes-smd-lanes").drafting

    class _Broker:
        def drafting_submit(self, envelope):
            return answer

    monkeypatch.setattr(drafting, "JobsBrokerClient", lambda: _Broker())
    call = {"matter_id": "m-1", "matter_number": "4417", "document_class": "memo", **args}
    return json.loads(drafting.drafting_job_submit(call, session_id=SESSION))


def test_a_request_with_no_matter_number_asks_for_it_in_a_fixed_sentence(mod, monkeypatch) -> None:  # noqa: F811
    _email()
    out = _submit(monkeypatch, {"accepted": True}, matter_number="")
    assert out == {
        "accepted": False,
        "code": "matter_number_missing",
        "say": "Received. Which matter is this for? Please reply with the matter number.",
    }


@pytest.mark.parametrize(
    "mode, state, says, not_says",
    [
        ("revise", "running", "We'll make those changes to the memo for matter 4417", "with us"),
        ("draft", "running", "The memo for matter 4417 is with us", "changes"),
        ("draft", "delivered", "already filed in the matter", "when the draft is filed"),
        ("draft", "held", "is on hold, as our earlier reply", "when the draft is filed"),
    ],
)
def test_a_duplicate_answers_with_the_sentence_true_for_the_queued_job(
    mod,  # noqa: F811 - the fixture
    monkeypatch,
    mode,
    state,
    says,
    not_says,
) -> None:
    _email()
    answer = {
        "accepted": False,
        "code": "already_queued",
        "job_id": "01JQUEUEDJOBIDNOTSHOWNXXXX",
        "queued_matter_number": "4417",
        "queued_mode": mode,
        "queued_state": state,
    }
    out = _submit(monkeypatch, answer)
    assert says in out["say"] and not_says not in out["say"]
    assert "01JQUEUED" not in json.dumps(out)


def test_a_malformed_call_the_broker_returns_for_correction_says_nothing(
    mod,  # noqa: F811 - the fixture
    monkeypatch,
) -> None:
    """Review 2026-11-19: the first envelope_invalid is the model's own call to
    correct; no say, so nothing reaches the firm and nothing is owed."""
    _email()
    answer = {
        "accepted": False,
        "code": "envelope_invalid",
        "reason": "bad class",
        "correct_call": True,
    }
    out = _submit(monkeypatch, answer)
    assert "say" not in out and "correct it and submit again" in out["reason"]
