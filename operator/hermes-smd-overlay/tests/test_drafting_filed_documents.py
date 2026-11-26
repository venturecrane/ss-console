"""An email about drafting documents ALREADY FILED on the matter (2026-11-24).

The attorney's "the interrogatories need follow-up questions", in the thread of
sets SMD had filed by hand, reached only a different draft still running ("we
added what you wrote to the instructions"), and nothing revised the sets he was
reading. These pin the overlay's half: the hold's one sentence, the carry to the
running draft after a submit, and the submit after an addition whose thread
holds filed documents. Names and ids are invented.
"""

from __future__ import annotations

import json

import pytest

from shared import request_index
from tests.test_drafting_add_to_request import (
    ADMIN,
    ASKED,
    JOB,
    LATER,
    SESSION,
    THREAD,
    WORDS,
    _clear,
    _email,
    _indexed,
)

LOOK = "Let me look into that. I'll reply in this thread."
MATTER_ID = "b041dd06-30a4-4c1f-912b-27724bd77a64"
RUNNING = "01J0000000000000000000RUNN"


class _Broker:
    def __init__(self, submit: dict, added: dict | None = None) -> None:
        self.submit_answer = submit
        self.added = {
            "ok": True,
            "result": "applied",
            "matter_number": "900201",
            "document_class": "discovery_set",
        }
        self.added.update(added or {})
        self.adds: list[dict] = []
        self.submitted: list[dict] = []

    def drafting_submit(self, envelope):
        self.submitted.append(envelope)
        return self.submit_answer

    def drafting_add_to_request(self, **fields):
        self.adds.append(fields)
        return self.added


@pytest.fixture
def lanes(monkeypatch, tmp_path):
    from tests.conftest import load_plugin

    monkeypatch.setenv("SMD_REQUEST_INDEX_DB_PATH", str(tmp_path / "requests.db"))
    request_index.record_inbound(
        vendor_message_id="AAMkASKED=",
        internet_message_id=ASKED,
        sender=ADMIN,
        subject="discovery",
        thread_ref=THREAD,
        body="Please draft the special interrogatories.",
    )
    mod = load_plugin("hermes-smd-lanes").drafting
    monkeypatch.setattr(
        mod, "_request_attachments", lambda _origin: ([], "")
    )  # a comment attaches nothing
    _clear()
    _indexed()
    _email()
    yield mod, monkeypatch
    _clear()


def _use(mod, monkeypatch, broker) -> _Broker:
    monkeypatch.setattr(mod, "JobsBrokerClient", lambda **_: broker)
    return broker


def _submit(mod) -> dict:
    args = {"matter_id": MATTER_ID, "matter_number": "900201", "document_class": "discovery_set"}
    return json.loads(mod.drafting_job_submit(args, session_id=SESSION))


@pytest.mark.parametrize(
    "code",
    ["prior_filed_by_smd", "prior_several_drafts", "revision_underway", "revision_partly_filed"],
)
def test_a_hold_for_smds_hand_says_the_operator_owns_the_follow_up(lanes, code) -> None:
    """FALSIFIER: drop ``hand_say`` from ``refusal_say`` and the reply is the
    fresh-draft acknowledgment, a promise of a new draft nobody is making."""
    mod, mp = lanes
    _use(
        mod,
        mp,
        _Broker({"ok": True, "accepted": False, "code": code, "reason": "x", "smd_told": True}),
    )
    assert _submit(mod)["say"] == LOOK


def test_a_hold_not_recorded_as_owed_says_nothing(lanes) -> None:
    mod, mp = lanes
    _use(
        mod,
        mp,
        _Broker({"ok": True, "accepted": False, "code": "prior_filed_by_smd", "reason": "x"}),
    )
    out = _submit(mod)
    assert out["say"] == "none" and "instruction" in out


def test_the_email_also_reaches_the_draft_still_running(lanes) -> None:
    """FALSIFIER: drop ``reach_underway`` and the running draft of the same kind
    of set never hears the comment."""
    mod, mp = lanes
    answer = {
        "ok": True,
        "accepted": False,
        "code": "prior_filed_by_smd",
        "reason": "x",
        "smd_told": True,
        "underway_job_id": RUNNING,
    }
    broker = _use(mod, mp, _Broker(answer))
    out = _submit(mod)
    assert out["say"] == LOOK
    assert [(a["job_id"], a["about_filed"], a["words"]) for a in broker.adds] == [
        (RUNNING, True, WORDS)
    ]


def test_a_revision_beside_a_running_draft_carries_to_both(lanes) -> None:
    mod, mp = lanes
    answer = {
        "ok": True,
        "accepted": True,
        "job_id": "01J0000000000000000000REVS",
        "mode": "revise",
        "document_class": "discovery_set",
        "matter_number": "900201",
        "underway_job_id": RUNNING,
    }
    broker = _use(mod, mp, _Broker(answer))
    out = _submit(mod)
    assert out["accepted"] and "changes" in out["say"]
    assert broker.adds and broker.adds[0]["job_id"] == RUNNING


def test_an_addition_whose_thread_holds_filed_documents_submits_it_too(lanes) -> None:
    """12:05: the comment was added to the running redraft only. FALSIFIER: drop
    ``also_submit`` and the filed sets are never revised or held for SMD."""
    mod, mp = lanes
    hold = {
        "ok": True,
        "accepted": False,
        "code": "prior_filed_by_smd",
        "reason": "x",
        "smd_told": True,
    }
    added = {"filed_in_thread": True, "matter_id": MATTER_ID}
    broker = _use(mod, mp, _Broker(hold, added))
    out = json.loads(mod.drafting_job_add_to_request({"job_id": JOB}, session_id=SESSION))
    assert out["result"] == "applied" and out["say"] == LOOK
    assert len(broker.submitted) == 1 and broker.submitted[0]["matter"]["id"] == MATTER_ID
    assert broker.submitted[0]["request_ref"] == LATER
    assert len(broker.adds) == 1  # the addition is not carried a second time


def test_an_addition_with_no_filed_documents_submits_nothing(lanes) -> None:
    mod, mp = lanes
    broker = _use(mod, mp, _Broker({"ok": True, "accepted": True}))
    out = json.loads(mod.drafting_job_add_to_request({"job_id": JOB}, session_id=SESSION))
    assert (
        out["result"] == "applied"
        and broker.submitted == []
        and "added what you wrote" in out["say"]
    )
