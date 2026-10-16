"""The staff reassignment on the litigation lane's broker edge (2026-10-15).

The kind is told from the status list and the deadlines report by the
request's OWN words, in code (``litigation_reassign``); who the cases move
from and to are read from those words, never from the turn's scope, and a
request whose shape cannot be read is refused rather than guessed. The kind is
switched on per seat by an authored setting. A reassignment's write list rides
the job's audit row. Each test fails if the line it defends is removed."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker.litigation_reassign import ReassignUnreadable, is_reassignment, parse
from workspace_broker.tests.test_litigation_verbs import (  # noqa: F401 - the fixture is used by name
    audit_rows,
    call,
    queued,
    seat,
    submit,
    _yaml,
)

ASK = (
    "I need the Operator to update all cases that were assigned to Pat Example or Wendy to Dana Newhire, "
    "Lee's new paralegal. All of the tasks need to be updated as well to reflect the same. (Litigation cases only)"
)
M1 = "bbbbbbbb-0000-4000-8000-000000000001"
T1 = "cccccccc-0000-4000-8000-000000000001"


def test_the_firms_own_sentence_parses_from_to_attorney_and_scope() -> None:
    assert is_reassignment(ASK)
    assert parse(ASK) == {
        "from_names": ["Pat Example", "Wendy"],
        "to_name": "Dana Newhire",
        "attorney_names": ["Lee"],
        "litigation_only": True,
        "statuses": ["Open", "Pending"],
    }


@pytest.mark.parametrize(
    ("text", "frm", "to", "attorneys", "lit", "statuses"),
    [
        ("Please move Pat's cases to Dana.", ["Pat"], "Dana", [], False, ["Open", "Pending"]),
        ("Reassign the files from Pat Example, Sam and Lee Roe to Dana Roe for Chris's cases, including closed ones",
         ["Pat Example", "Sam", "Lee Roe"], "Dana Roe", ["Chris"], False, ["Open", "Pending", "Closed"]),
        ("Can you switch all litigation matters assigned to Pat over to Dana? Open cases only.",
         ["Pat"], "Dana", [], True, ["Open"]),
    ],
)  # fmt: skip
def test_other_ways_of_asking_parse(text, frm, to, attorneys, lit, statuses) -> None:
    got = parse(text)
    assert (got["from_names"], got["to_name"], got["attorney_names"]) == (frm, to, attorneys)
    assert (got["litigation_only"], got["statuses"]) == (lit, statuses)


@pytest.mark.parametrize(
    "text",
    [
        "Send me a fresh litigation status list.",
        "Can you send me an updated litigation status list?",
        "Please send the deadlines report for Pat's litigation cases.",
        "Who is the paralegal assigned to 100123?",
    ],
)
def test_lists_reports_and_questions_are_not_reassignments(text) -> None:
    assert not is_reassignment(text)


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("Move all of Pat's files to Dana and Lee.", "more than one person for the cases to go to"),
        ("Please move Pat's cases to the new paralegal.", "could not tell from the email"),
        ("Reassign Pat's cases to Pat.", "to the same person"),
    ],
)
def test_a_shape_it_cannot_read_is_refused_never_guessed(text, why) -> None:
    assert is_reassignment(text)
    with pytest.raises(ReassignUnreadable, match=why):
        parse(text)


def _on(y: str, value: str = "enabled") -> str:
    return y.replace(
        "          folder_name: 'Litigation Status'\n",
        f"          folder_name: 'Litigation Status'\n          staff_reassignment: '{value}'\n",
    )


def test_a_reassignment_ask_queues_the_reassignment_from_the_emails_words(seat) -> None:
    broker, db, yaml_path, queue, _firm = seat
    yaml_path.write_text(_on(_yaml()))
    out = submit(broker, request_text=ASK, scope={"paralegals": ["Not On Any Roster"]})  # the turn's scope is ignored
    assert out["accepted"] is True, out
    assert out["report"] == "reassign" and out["reassign"]["to_name"] == "Dana Newhire"
    env = json.loads(queued(queue)[0].read_text())
    assert env["report"] == "reassign" and env["scope"] == {"all": True}
    assert env["reassign"]["from_names"] == ["Pat Example", "Wendy"]
    rows = audit_rows(db)
    assert rows[-1][0] == "LITIGATION_JOB_SUBMITTED" and rows[-1][1]["report"] == "reassign"


def test_the_kind_is_refused_unless_the_seat_switched_it_on(seat) -> None:
    broker, _db, yaml_path, queue, _firm = seat
    for y in (_yaml(), _on(_yaml(), "yes")):
        yaml_path.write_text(y)
        out = submit(broker, request_text=ASK)
        assert out["accepted"] is False and "not switched on for this seat" in out["reason"]
    assert queued(queue) == []


def test_an_unreadable_reassignment_is_refused_in_a_sentence(seat) -> None:
    broker, _db, yaml_path, queue, _firm = seat
    yaml_path.write_text(_on(_yaml()))
    out = submit(broker, request_text="Please move Pat's cases to the new paralegal.")
    assert out["accepted"] is False and "could not tell from the email" in out["reason"]
    assert queued(queue) == []


def test_one_reassignment_at_a_time_and_a_status_list_is_not_blocked(seat) -> None:
    broker, _db, yaml_path, _queue, _firm = seat
    yaml_path.write_text(_on(_yaml()))
    assert submit(broker, request_text=ASK)["accepted"] is True
    again = submit(broker, message_ref="<two@mail.firm.example>", request_text="Move Sam's cases to Dana.")
    assert (
        again["accepted"] is False and "a staff reassignment for the same scope is already underway" in again["reason"]
    )
    assert submit(broker, message_ref="<three@mail.firm.example>")["accepted"] is True


def test_the_write_list_rides_the_audit_row_and_is_checked(seat) -> None:
    broker, db, yaml_path, _queue, _firm = seat
    yaml_path.write_text(_on(_yaml()))
    job = submit(broker, request_text=ASK)["job_id"]
    call(broker, "litigation_job_record", pid=1, uid=0, job_id=job, state="running", fields={})
    with pytest.raises(ValueError, match="each write is"):
        call(broker, "litigation_job_record", pid=1, uid=0, job_id=job, state="delivered",
             fields={"writes": [{"method": "DELETE", "path": f"/matters/{M1}"}]})  # fmt: skip
    writes = [{"method": "PATCH", "path": f"/matters/{M1}", "status": 200, "ok": True},
              {"method": "PUT", "path": f"/tasks/{T1}", "status": None, "ok": False}]  # fmt: skip
    call(broker, "litigation_job_record", pid=1, uid=0, job_id=job, state="delivered",
         fields={"counts": {"cases_moved": 1, "tasks_not_moved": 1}, "writes": writes})  # fmt: skip
    kind, meta = audit_rows(db)[-1]
    assert kind == "LITIGATION_JOB_DELIVERED" and meta["writes"] == writes
    assert meta["counts"] == {"cases_moved": 1, "tasks_not_moved": 1}
    got = call(broker, "litigation_job_status", job_id=job)["job"]
    assert got["report"] == "reassign" and "writes" not in got
