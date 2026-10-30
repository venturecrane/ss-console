"""The 12-section discovery breakdown on the litigation lane's broker edge
(2026-10-29). It is told apart by the request's OWN words, queues only when the
seat's skill settings switch it on (unauthored is off: the firm agrees the price
first), spends from the month's budget like the status list, and never blocks a
status list for the same people. Each test fails if the line it defends goes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker.litigation_intake import requested_report, wants_discovery12
from workspace_broker.litigation_report import EnvelopeError, report_keys
from workspace_broker.tests.test_litigation_verbs import (  # noqa: F401 - the fixture is used by name
    queued,
    seat,
    submit,
    _yaml,
)

ASK = "Yes, please go ahead with the 12-section discovery breakdown on all litigated files."


@pytest.mark.parametrize(
    ("text", "want"),
    [
        (ASK, True),
        ("Send the twelve section discovery report", True),
        ("The discovery breakdown, please", True),
        ("Send me a fresh litigation status list with discovery.", False),
        ("Send the deadlines report", False),
    ],
)
def test_only_these_words_ask_for_the_breakdown(text, want) -> None:
    assert wants_discovery12(text) is want


def test_the_status_and_deadlines_words_are_unchanged() -> None:
    assert requested_report("Send me a fresh litigation status list.") == ("status", ())


def test_the_envelope_carries_the_report_alone() -> None:
    assert report_keys({"report": "discovery12"}) == {"report": "discovery12"}
    with pytest.raises(EnvelopeError):
        report_keys({"report": "discovery12", "workbooks": ["deadlines"]})


def _on(y: str) -> str:
    return y.replace(
        "          folder_name: 'Litigation Status'\n",
        "          folder_name: 'Litigation Status'\n          discovery_breakdown: 'on'\n",
    )


def test_unswitched_it_is_refused_and_nothing_queues(seat) -> None:
    broker, _db, yaml_path, queue, _firm = seat
    yaml_path.write_text(_yaml())
    out = submit(broker, request_text=ASK)
    assert out["accepted"] is False and "discovery breakdown is not switched on" in out["reason"]
    assert queued(queue) == []


def test_switched_on_it_queues_to_the_status_folder(seat) -> None:
    broker, _db, yaml_path, queue, _firm = seat
    yaml_path.write_text(_on(_yaml()))
    out = submit(broker, request_text=ASK)
    assert out["accepted"] is True and out["report"] == "discovery12", out
    env = json.loads(queued(queue)[0].read_text())
    assert env["report"] == "discovery12" and env["folder_name"] == "Litigation Status" and "workbooks" not in env


def test_it_does_not_block_a_status_list_for_the_same_people(seat) -> None:
    broker, _db, yaml_path, _queue, _firm = seat
    yaml_path.write_text(_on(_yaml()))
    assert submit(broker)["accepted"] is True
    second = submit(broker, message_ref="<other@mail.firm.example>", request_text=ASK)
    assert second["accepted"] is True, second
