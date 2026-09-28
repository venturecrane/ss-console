"""A matter with no responsible staff: the review goes to the firm's authored
fallback contact, who can close a done task and sees every open one.

The pilot's review on 2026-09-28 (two matters, neither with a responsible
staff member set):

* a service task with the proof of service on file went out as "Looks done:
  a proof of service dated 2026-09-28 is on the matter. You can close it in
  Smokeball." It handed the work back instead of offering to do it, and the
  date was the upload day, not the document's;
* a preservation-letter task 38 days overdue and still needed was DROPPED from
  the review entirely (open + no owner returned nothing).

Now the fallback recipient's own Smokeball staff record (read by email at pull
time) stands in for the owner: their "yes" closes a done task. With no staff
record behind the fallback address, a done task stays named, and an open task
is still listed as a keep either way, so nothing overdue silently vanishes.
Names here are invented.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import io
import json
import sys
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(filename: str, name: str):
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


pre_run = _load("pre_run.py", "tlk_pre_run_no_owner")
pull = _load("pull.py", "tlk_pull_no_owner")
lines = _load("lines.py", "tlk_lines_no_owner")

TODAY = date(2026, 9, 28)
NOW = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)

M_DONE = "0d0d0d0d-0000-4000-8000-000000000001"
M_OPEN = "0d0d0d0d-0000-4000-8000-000000000002"
T_DONE = "0e0e0e0e-0000-4000-8000-000000000001"
T_OPEN = "0e0e0e0e-0000-4000-8000-000000000002"

ATTY = {"staff_id": "s-atty", "email": "atty@firm.test", "enabled": True, "former": False}
FALLBACK = {"staff_id": "s-fallback", "email": "Office@Firm.test", "enabled": True, "former": False}

DONE_TASK = {
    "id": T_DONE,
    "subject": "Serve Doe responses to Acme RFP Set One",
    "dueDate": "2026-09-04",
    "matter": {"id": M_DONE},
    "matterNumber": "2026-PI-900",
}
OPEN_TASK = {
    "id": T_OPEN,
    "subject": "Send preservation letter to Acme Plaza for surveillance video - Roe",
    "dueDate": "2026-08-21",
    "matter": {"id": M_OPEN},
    "matterNumber": "2026-PI-901",
}
#: Uploaded Sep 28; the proof of service inside it was executed earlier.
POS = {
    "name": "2026-09-15 Proof of Service - Responses to Acme RFP Set One - Doe.pdf",
    "date": "2026-09-28T18:00:00Z",
    "read": True,
    "namesFound": ["doe"],
}


def _matter(surname: str, files: list, responsible=None) -> dict:
    return {
        "status": "Open",
        "responsible": responsible,
        "assisting": [],
        "files": files,
        "events": [],
        "clientSurnames": [surname],
        "clientsComplete": True,
    }


def _raw(*, fallback_staff=(FALLBACK,), responsible=None) -> dict:
    raw = {
        "tasks": [DONE_TASK, OPEN_TASK],
        "matters": {
            M_DONE: _matter("doe", [POS], responsible),
            M_OPEN: _matter("roe", [], responsible),
        },
        "openTaskCount": 2,
        "matterCount": 2,
    }
    if fallback_staff is not None:
        raw["fallbackStaff"] = list(fallback_staff)
    return raw


def _yaml() -> dict:
    return {
        "scope": {"inbound_allow_from": ["@firm.test"]},
        "escalation": {
            "case_alert_routing": {"mode": "matter_staff", "fallback_recipients": ["office@firm.test"]},
        },
        "case_manager": {"task_cleanup": {"level": "prepares", "keep_quiet_days": 30, "max_lines": 30}},
    }


class _Writer:
    async def write_suppressed_wake(self, **kw):
        pass


def _review(raw, tmp_path, monkeypatch) -> list[dict]:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    with redirect_stdout(io.StringIO()):
        code = asyncio.new_event_loop().run_until_complete(
            pre_run.run_once(
                pull_fn=lambda: raw,
                customer_yaml=_yaml(),
                writer_factory=_Writer,
                today=TODAY,
                now=NOW,
                casework_events=[],
                escalation_events=[],
            )
        )
    assert code == 0
    envelope = json.loads((tmp_path / ".smd" / "pre_run" / "task-list-keeper.casework.json").read_text())
    return envelope["messages"]


def _by_task(messages) -> dict:
    return {i["task_id"]: i for m in messages for i in m["items"]}


def test_no_owner_done_task_is_a_proposed_close_the_fallback_person_can_approve(tmp_path, monkeypatch):
    messages = _review(_raw(), tmp_path, monkeypatch)
    assert [m["recipients"] for m in messages] == [["office@firm.test"]]
    done = _by_task(messages)[T_DONE]
    assert done["event"] == "proposed"
    assert done["payload"]["action"] == "close" and done["payload"]["staff_id"] == "s-fallback"
    assert done["line"] == (
        '"Serve Doe responses to Acme RFP Set One", due 2026-09-04. Looks done: a proof of service is on '
        "the matter (added Sep 28). Suggest: close it."
    )


def test_no_owner_open_task_is_listed_as_a_keep(tmp_path, monkeypatch):
    open_item = _by_task(_review(_raw(), tmp_path, monkeypatch))[T_OPEN]
    assert open_item["event"] == "proposed" and open_item["payload"]["action"] == "keep"
    # The same words the owner-present open line uses.
    assert open_item["line"].endswith("Still open. Suggest: leave it open, and I won't list it again for 30 days.")


def test_a_fallback_address_with_no_staff_record_names_the_done_task_and_still_lists_the_open_one(
    tmp_path, monkeypatch
):
    for staff in ((), ({**FALLBACK, "enabled": False},), None):
        by_task = _by_task(_review(_raw(fallback_staff=staff), tmp_path, monkeypatch))
        done, open_item = by_task[T_DONE], by_task[T_OPEN]
        assert done["event"] == "named" and done["payload"]["action"] == "keep"
        assert done["payload"]["staff_id"] is None
        assert done["line"].endswith("(added Sep 28). You can close it in Smokeball.")
        assert open_item["event"] == "proposed" and open_item["payload"]["action"] == "keep"


def test_the_fallback_message_says_why_it_came_to_this_person(tmp_path, monkeypatch):
    (message,) = _review(_raw(), tmp_path, monkeypatch)
    assert message["routing_leg"] == "fallback"
    assert message["lead"].startswith(lines.LEAD_FALLBACK)
    assert message["subject"] == "[Tasks] 2 tasks to review"


def test_owner_present_behaviour_is_unchanged(tmp_path, monkeypatch):
    (message,) = _review(_raw(responsible=ATTY), tmp_path, monkeypatch)
    assert message["recipients"] == ["atty@firm.test"] and message["routing_leg"] == "matter_staff_responsible"
    by_task = {i["task_id"]: i for i in message["items"]}
    assert by_task[T_DONE]["payload"]["action"] == "close" and by_task[T_DONE]["payload"]["staff_id"] == "s-atty"
    assert by_task[T_OPEN]["payload"]["action"] == "keep" and by_task[T_OPEN]["payload"]["staff_id"] == "s-atty"
    assert message["lead"].startswith(lines.LEAD_REVIEW)
    assert message["subject"] == "[Tasks] 2 tasks to review on your matters"


def test_the_pull_reads_the_authored_fallback_and_parses_its_staff():
    assert pull.fallback_emails(_yaml()) == ["office@firm.test"]
    assert pull.fallback_emails({}) == []
    snapshot, problem = pull.parse_pull(_raw())
    assert problem is None
    assert snapshot.fallback_staff["office@firm.test"].staff_id == "s-fallback"
    assert pull.parse_pull(_raw(fallback_staff=None))[0].fallback_staff == {}
    ast.parse(pull._PULL_SNIPPET)  # the snippet with the fallback read still parses


def test_the_evidence_day_is_when_the_document_was_added():
    assert lines.evidence_text(["document:proof_of_service:2026-09-28"]) == "a proof of service added Sep 28"
    assert lines.month_day(date(2026, 10, 6)) == "Oct 6"
