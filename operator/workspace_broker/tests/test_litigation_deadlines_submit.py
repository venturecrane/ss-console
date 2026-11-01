"""The deadlines report on the litigation lane's broker edge (2026-10-15).

The report is told from the status list by the request's OWN words, in code
(``litigation_intake.requested_report``); the overlay's tool is unchanged and
the model never chooses. A deadlines job files to its own authored folder,
never blocks or is blocked by a status list for the same people, is not
refused by a spent month (it spends nothing), and its counts reach the
agent's status read. Each test fails if the line it defends is removed."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker.litigation_intake import requested_report
from workspace_broker.tests.test_litigation_verbs import (  # noqa: F401 - the fixture is used by name
    ATTORNEY_A,
    ATTORNEY_B,
    REF,
    call,
    queued,
    seat,
    submit,
    _yaml,
)

ASK = (
    "Chris's paralegal is out, please send me the deadlines report and statute report "
    "for Chris's and Craig's cases in litigation"
)


@pytest.mark.parametrize(
    ("text", "want"),
    [
        (ASK, ("deadlines", ("deadlines", "statute"))),
        ("Please send the deadlines report for Pat's litigation cases.", ("deadlines", ("deadlines",))),
        ("Can I get the statute report for Sam?", ("deadlines", ("statute",))),
        ("Send me the deadline and statute reports", ("deadlines", ("deadlines", "statute"))),
        ("What are the upcoming court dates on Pat's cases?", ("deadlines", ("deadlines",))),
        ("Send me a fresh litigation status list.", ("status", ())),
        ("The litigation case review list please", ("status", ())),
        ("Send the litigation status list, we have deadlines coming up", ("status", ())),
        ("Send the litigation status list and the deadlines report", ("deadlines", ("deadlines",))),
    ],
)
def test_the_request_words_decide_the_report(text, want) -> None:
    assert requested_report(text) == want


def _deadlines_yaml(folder: str | None = "Deadlines") -> str:
    y = _yaml()
    if folder:
        y = y.replace(
            "          folder_name: 'Litigation Status'\n",
            f"          folder_name: 'Litigation Status'\n          deadlines_folder_name: '{folder}'\n",
        )
    return y


def test_a_deadlines_ask_queues_the_deadlines_report_to_its_own_folder(seat) -> None:
    broker, _db, yaml_path, queue, _firm = seat
    yaml_path.write_text(_deadlines_yaml())
    out = submit(broker, request_text=ASK, scope={"attorneys": ["Avery", "Blake"]})
    assert out["accepted"] is True, out
    assert (out["report"], out["workbooks"]) == ("deadlines", ["deadlines", "statute"])
    env = json.loads(queued(queue)[0].read_text())
    assert env["report"] == "deadlines" and env["folder_name"] == "Deadlines"
    # One side named: a people scope (any staff role), resolved by the runner (2026-10-24).
    assert env["scope"] == {"people": {"names": ["Avery Stone", "Blake Stone"], "requester": None}}


def test_the_deadlines_folder_is_authored_never_defaulted(seat) -> None:
    broker, _db, yaml_path, queue, _firm = seat
    yaml_path.write_text(_deadlines_yaml(folder=None))
    out = submit(broker, request_text=ASK)
    assert out["accepted"] is False and "where the status list is filed is not authored" in out["reason"]
    assert queued(queue) == []


def test_a_status_list_ask_is_unchanged(seat) -> None:
    broker, _db, yaml_path, queue, _firm = seat
    yaml_path.write_text(_deadlines_yaml())
    out = submit(broker)
    assert out["accepted"] is True and out["report"] == "status"
    env = json.loads(queued(queue)[0].read_text())
    assert "report" not in env and env["folder_name"] == "Litigation Status"


def test_a_deadlines_report_and_a_status_list_for_the_same_people_both_queue(seat) -> None:
    broker, _db, yaml_path, _queue, _firm = seat
    yaml_path.write_text(_deadlines_yaml())
    assert submit(broker, scope={"attorneys": ["Avery"]})["accepted"] is True
    second = submit(broker, message_ref="<other@mail.firm.example>", request_text=ASK, scope={"attorneys": ["Avery"]})
    assert second["accepted"] is True, second
    third = submit(broker, message_ref="<third@mail.firm.example>", request_text=ASK, scope={"attorneys": ["Avery"]})
    assert third["accepted"] is False and "a deadlines report for the same scope is already underway" in third["reason"]


def test_a_spent_month_does_not_refuse_the_deadlines_report(seat) -> None:
    broker, _db, yaml_path, _queue, firm = seat
    # The ledger stamps rows with the wall clock, so the month is read on it too:
    # a clock pinned to October read a $0 month from November on.
    broker.litigation._now = lambda: datetime.now(timezone.utc)
    yaml_path.write_text(_deadlines_yaml())
    firm.write_text(firm.read_text().replace("monthly_budget_usd: 750", "monthly_budget_usd: 1"))
    job = submit(broker)["job_id"]
    call(broker, "litigation_job_record", pid=1, uid=0, job_id=job, state="running", fields={})
    call(broker, "litigation_job_record", pid=1, uid=0, job_id=job, state="failed", fields={"cents": 500})
    status = submit(broker, message_ref="<xxx1@mail.firm.example>")
    assert status["accepted"] is False and "budget is spent" in status["reason"]
    again = submit(broker, message_ref="<yyy1@mail.firm.example>", request_text=ASK)
    assert again["accepted"] is True, again


def test_the_counts_reach_the_status_read_and_are_integers_only(seat) -> None:
    broker, _db, yaml_path, _queue, _firm = seat
    yaml_path.write_text(_deadlines_yaml())
    job = submit(broker, request_text=ASK)["job_id"]
    call(broker, "litigation_job_record", pid=1, uid=0, job_id=job, state="running", fields={})
    with pytest.raises(ValueError, match="counts carries only"):
        call(broker, "litigation_job_record", pid=1, uid=0, job_id=job, state="running",
             fields={"counts": {"client_name": 1}})  # fmt: skip
    call(broker, "litigation_job_record", pid=1, uid=0, job_id=job, state="running",
         fields={"counts": {"matters_read": 601, "litigation": 176}})  # fmt: skip
    got = call(broker, "litigation_job_status", job_id=job)["job"]
    assert got["report"] == "deadlines" and got["counts"] == {"litigation": 176, "matters_read": 601}
    assert REF  # the request ref is never projected
    assert "request_ref" not in got
