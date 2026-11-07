"""The "set up our ..." tool (hermes-smd-lanes/library_setup.py): who asked comes
from the verified email, never the model; the model gives only the kind and an
attorney's name; a dark answer carries nothing to say; a live one carries the
broker's sentence as given. Made-up firm."""

from __future__ import annotations

import json

import pytest

from shared.action_classes import ActionClass, classify_tool
from tests.test_drafting_tools import ADMIN, IMID, _clear, _email

JOB = "01J0000000000000000000000Y"


class _Client:
    def __init__(self, answer: dict) -> None:
        self.answer = answer
        self.envelopes: list[dict] = []

    def library_setup_submit(self, envelope):
        self.envelopes.append(envelope)
        return self.answer


@pytest.fixture
def setup(monkeypatch):
    from tests.conftest import load_plugin

    mod = load_plugin("hermes-smd-lanes").library_setup
    holder: dict = {}

    def make(answer: dict) -> _Client:
        client = _Client(answer)
        holder["client"] = client
        return client

    monkeypatch.setattr(mod, "JobsBrokerClient", lambda: holder["client"])
    _clear()
    yield mod, make
    _clear()


def test_who_asked_comes_from_the_email_and_the_model_gives_only_kind_and_attorney(setup):
    mod, make = setup
    client = make({"ok": True, "accepted": True, "job_id": JOB, "live": False})
    _email("Admin here: set up our memos, please.")
    out = json.loads(
        mod.library_setup_submit(
            {"kind": "memo", "attorney": "  Blair   Sample ", "requested_by": "x@y.z"}
        )
    )
    assert out == {"accepted": True, "job_id": JOB, "live": False}
    env = client.envelopes[0]
    assert set(env) == {"kind", "attorney", "requested_by", "request_ref", "request_text"}
    assert (env["requested_by"], env["request_ref"], env["attorney"]) == (
        ADMIN,
        IMID,
        "Blair Sample",
    )
    assert env["request_text"].startswith("Admin here")


def test_live_the_broker_sentence_is_passed_as_given(setup):
    mod, make = setup
    make(
        {
            "ok": True,
            "accepted": True,
            "job_id": JOB,
            "live": True,
            "say": "the broker's own sentence",
        }
    )
    _email()
    out = json.loads(mod.library_setup_submit({"kind": "mediation_brief"}))
    assert out["live"] is True and out["say"] == "the broker's own sentence"


def test_dark_there_is_nothing_to_say_even_if_the_broker_sent_one(setup):
    mod, make = setup
    make({"ok": True, "accepted": True, "job_id": JOB, "live": False, "say": "never relayed"})
    _email()
    assert "say" not in json.loads(mod.library_setup_submit({"kind": "memo"}))


def test_a_refusal_is_relayed_as_the_broker_gave_it(setup):
    mod, make = setup
    make({"ok": True, "accepted": False, "reason": "the broker's refusal"})
    _email()
    assert json.loads(mod.library_setup_submit({"kind": "memo"})) == {
        "accepted": False,
        "reason": "the broker's refusal",
    }


def test_a_turn_no_email_opened_queues_nothing(setup):
    mod, make = setup
    client = make({"accepted": True})
    out = json.loads(mod.library_setup_submit({"kind": "memo"}))
    assert out["accepted"] is False and client.envelopes == []


def test_the_schema_has_no_requester_field_and_the_tool_is_an_internal_write(setup):
    mod, _ = setup
    schema = mod.TOOLS["library_setup_submit"][1]
    assert set(schema["properties"]) == {"kind", "attorney"} and schema["required"] == ["kind"]
    assert schema["properties"]["kind"]["enum"] == ["mediation_brief", "memo"]
    assert classify_tool("library_setup_submit").action_class is ActionClass.INTERNAL_WRITE
