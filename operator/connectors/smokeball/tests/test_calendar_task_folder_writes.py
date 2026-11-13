"""Unit coverage for the deadline-engine / document-organization write cut:
calendar events, tasks, and folders. No live Smokeball calls — a recording fake
client is injected so we lock the exact path + JSON body each tool sends, and that
optional (None) args are dropped rather than sent as null."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from smokeball_connector import server
from smokeball_connector.client import SmokeballClient


class _Recorder:
    """Captures the path/method/body the tool layer sends to the REST client."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.responses: dict[str, dict] = {}  # per-path canned GET responses

    def request(self, method: str, path: str, *, json=None):
        self.calls.append({"method": method, "path": path, "json": json})
        return {"ok": True}

    def get(self, path: str, **params):
        # Mirror the real client: drop None-valued params.
        cleaned = {k: v for k, v in params.items() if v is not None}
        self.calls.append({"method": "GET", "path": path, "params": cleaned})
        return self.responses.get(path, {"ok": True})

    # create_folder lives on the client (the chronology runner's delivery step
    # calls it too, ss#2613); bind the real method so its wire body is still
    # what these tests lock.
    create_folder = SmokeballClient.create_folder


@pytest.fixture()
def rec(monkeypatch) -> _Recorder:
    r = _Recorder()
    monkeypatch.setattr(server, "_get_client", lambda: r)
    return r


# ---- tasks ----------------------------------------------------------------
def test_create_task_minimal_drops_none(rec: _Recorder) -> None:
    server.create_task(staff_id="s-1", subject="File responsive pleading")
    assert rec.calls == [
        {
            "method": "POST",
            "path": "/tasks",
            "json": {"staffId": "s-1", "subject": "[Operator] File responsive pleading"},
        }
    ]


def test_create_task_maps_due_date_to_due_date_only(rec: _Recorder) -> None:
    server.create_task(
        staff_id="s-1",
        subject="Serve discovery responses",
        matter_id="m-9",
        due_date="2026-07-15",
        assignee_ids=["s-2", "s-3"],
    )
    assert rec.calls[0]["json"] == {
        "staffId": "s-1",
        "subject": "[Operator] Serve discovery responses",
        "matterId": "m-9",
        "dueDateOnly": "2026-07-15",
        "assigneeIds": ["s-2", "s-3"],
    }


def test_update_task_completion_read_merges_and_stamps_completer(rec: _Recorder) -> None:
    """PUT /tasks is a full replace (proven live 2026-08-31,
    vfy_01M1CWACT2NSB1WFSZXD3KQK5F): the tool must read the task, re-send its
    current fields, carry StaffId, and stamp CompletedByStaffId on completion.
    The read echoes dueDateOnly as a datetime; the PUT re-sends date-only."""
    rec.responses["/tasks/t-7"] = {
        "id": "t-7",
        "subject": "[Operator] Review chronology package",
        "note": "delivered folder pointer",
        "dueDateOnly": "2026-09-01T00:00:00",
        "isCompleted": False,
        "matter": {"id": "m-9"},
        "assignees": [{"id": "s-2"}],
    }
    server.update_task(task_id="t-7", is_completed=True, staff_id="s-1")
    assert rec.calls[0] == {"method": "GET", "path": "/tasks/t-7", "params": {}}
    assert rec.calls[1] == {
        "method": "PUT",
        "path": "/tasks/t-7",
        "json": {
            "staffId": "s-1",
            "subject": "[Operator] Review chronology package",
            "note": "delivered folder pointer",
            "dueDateOnly": "2026-09-01",
            "matterId": "m-9",
            "isCompleted": True,
            "completedByStaffId": "s-1",
            "assigneeIds": ["s-2"],
        },
    }


def test_update_task_keeps_the_tasks_categories(rec: _Recorder) -> None:
    """A full-replace PUT clears what it omits, so an agent's update to a task
    filed under categories (here a reassignment) re-sends them; before this,
    every Operator task update cleared them on the firm's tenant."""
    rec.responses["/tasks/t-7"] = {"id": "t-7", "matter": {"id": "m-9"}, "categories": ["Liens"]}
    server.update_task(task_id="t-7", assignee_ids=["s-4"], staff_id="s-1")
    put = next(c for c in rec.calls if c["method"] == "PUT")
    assert put["json"]["categories"] == ["Liens"]


def test_update_task_without_staff_id_refuses_before_the_wire(rec: _Recorder) -> None:
    """The task read never echoes staffId (proven live 2026-08-31), so a
    completion with no staff_id cannot form a valid PUT; refuse loudly instead
    of letting the tenant 400 (or worse, letting a replace-PUT clear fields)."""
    rec.responses["/tasks/t-7"] = {"id": "t-7", "isCompleted": False}
    with pytest.raises(ValueError, match="staff_id"):
        server.update_task(task_id="t-7", is_completed=True)
    assert [c for c in rec.calls if c["method"] == "PUT"] == []


_UPDATE_TASK_GOLDEN = Path(__file__).resolve().parent / "goldens" / "update_task_puts.json"
_UPDATE_TASK_CASES = {
    # a stamped subject change and a completion, on a task filed under two categories
    "with_categories": (
        {"subject": "Call the adjuster", "note": "left a message", "dueDateOnly": "2026-09-01T00:00:00",
         "isCompleted": False, "matter": {"id": "m-9"}, "assignees": [{"id": "s-2"}], "categories": ["Liens", "Medical"]},
        {"subject": "Call the adjuster back", "is_completed": True, "staff_id": "s-1"},
    ),
    # a reassignment, on a task with no categories
    "without_categories": (
        {"subject": "[Operator] Order records", "isCompleted": False, "matter": {"id": "m-9"},
         "assignees": [{"id": "s-2"}]},
        {"assignee_ids": ["s-4"], "staff_id": "s-1"},
    ),
}  # fmt: skip


def test_update_task_put_bodies_are_pinned_on_the_wire(rec: _Recorder) -> None:
    """The agent tool's task PUT, byte for byte (the JSON text httpx sends, key
    order included), per case in ``tests/goldens/update_task_puts.json``.
    ``SMOKEBALL_WRITE_GOLDENS=1`` rewrites it."""
    got = {}
    for case, (task, args) in sorted(_UPDATE_TASK_CASES.items()):
        rec.calls.clear()
        rec.responses["/tasks/t-7"] = {"id": "t-7", **task}
        server.update_task(task_id="t-7", **args)
        puts = [c for c in rec.calls if c["method"] == "PUT"]
        got[case] = [
            {"method": c["method"], "path": c["path"], "wire": json.dumps(c["json"], ensure_ascii=False, separators=(",", ":"))}
            for c in puts
        ]  # fmt: skip
    if os.environ.get("SMOKEBALL_WRITE_GOLDENS"):
        _UPDATE_TASK_GOLDEN.parent.mkdir(exist_ok=True)
        _UPDATE_TASK_GOLDEN.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    assert got == json.loads(_UPDATE_TASK_GOLDEN.read_text(encoding="utf-8"))


def test_full_replace_body_keeps_categories_and_names_the_completer() -> None:
    """The job lanes' task PUT body: the merged fields, the task's categories
    re-sent (the full replace clears what it omits), the text unstamped, and
    ``completed_by`` only on a body that completes the task."""
    from smokeball_connector.task_update import full_replace_body

    cur = {"subject": "Draft responses", "matter": {"id": "m-9"}, "assignees": [{"id": "s-2"}], "categories": ["D"]}
    body = full_replace_body(cur, note="done", is_completed=True, staff_id="s-2", completed_by="s-3")
    assert body == {
        "staffId": "s-2",
        "subject": "Draft responses",
        "note": "done",
        "matterId": "m-9",
        "isCompleted": True,
        "completedByStaffId": "s-3",
        "assigneeIds": ["s-2"],
        "categories": ["D"],
    }
    moved = full_replace_body({**cur, "categories": []}, assignee_ids=["s-4"], staff_id="s-1", completed_by="s-3")
    assert moved == {"staffId": "s-1", "subject": "Draft responses", "matterId": "m-9", "assigneeIds": ["s-4"]}
    with pytest.raises(ValueError, match="staff_id"):
        full_replace_body(cur, staff_id=None)


# ---- events ---------------------------------------------------------------
def test_create_event_forces_normal_type(rec: _Recorder) -> None:
    # attendees + time_zone became required with the live-API contract fix
    # (L2 round 2, 2026-07-06) — see test_create_event_contract.py. The matter
    # read that adds the responsible attorney comes first; this matter names
    # s-1 as responsible, so the attendee list is unchanged
    # (test_create_event_responsible_attorney.py covers the union).
    rec.responses["/matters/m-9"] = {"id": "m-9", "personResponsible": {"id": "s-1"}}
    server.create_event(
        subject="Discovery responses due",
        start_time="2026-07-15T09:00:00",
        end_time="2026-07-15T09:30:00",
        attendees=["s-1"],
        time_zone="America/Los_Angeles",
        matter_id="m-9",
    )
    assert rec.calls[0] == {"method": "GET", "path": "/matters/m-9", "params": {}}
    assert rec.calls[1] == {
        "method": "POST",
        "path": "/events",
        "json": {
            "subject": "[Operator] Discovery responses due",
            "startTime": "2026-07-15T09:00:00",
            "endTime": "2026-07-15T09:30:00",
            "matterId": "m-9",
            "attendees": ["s-1"],
            "timeZone": "America/Los_Angeles",
            "type": "Normal",
        },
    }


_LINKED_EVENT = {
    "id": "e-2",
    "type": "Normal",
    "subject": "[Operator] Discovery: responses due",
    "description": "From the firm's discovery chart.",
    "startTime": "2026-09-25T00:00:00",
    "endTime": "2026-09-26T00:00:00",
    "allDay": True,
    "timeZone": "America/Los_Angeles",
    "matter": {"id": "m-9", "href": "https://api.smokeball.com/matters/m-9", "rel": "Matters"},
    "attendees": [{"id": "s-1", "href": "https://api.smokeball.com/staff/s-1", "rel": "Staff"}],
}


def test_update_event_moving_a_date_keeps_the_matter_link(rec: _Recorder) -> None:
    """PUT /events is a full replace (proven live 2026-09-24 on the A&P tenant,
    vfy_01M3AYM2WQ7BT99SJF7M6JXBKZ): a PUT without matterId set the event's
    matter to null, so a moved deadline dropped off the matter's calendar. The
    tool must read the event and re-send its matter link, attendees, and every
    other field the caller did not change."""
    rec.responses["/events/e-2"] = dict(_LINKED_EVENT)
    server.update_event(event_id="e-2", start_time="2026-10-09T00:00:00", end_time="2026-10-10T00:00:00")
    assert rec.calls[0] == {"method": "GET", "path": "/events/e-2", "params": {}}
    put = next(c for c in rec.calls if c["method"] == "PUT")  # the read-back GETs follow it
    assert put["method"] == "PUT" and put["path"] == "/events/e-2"
    assert put["json"] == {
        "subject": "[Operator] Discovery: responses due",
        "startTime": "2026-10-09T00:00:00",
        "endTime": "2026-10-10T00:00:00",
        "description": "From the firm's discovery chart.",
        "allDay": True,
        "timeZone": "America/Los_Angeles",
        "matterId": "m-9",
        "attendees": ["s-1"],
        "type": "Normal",
    }


def test_update_event_keeps_lawtoolbox_additional_data(rec: _Recorder) -> None:
    """A PUT that omits additionalData reads back {} (proven live 2026-11-05,
    vfy_01M6PJ8XAK8PRN52NG5ENPW3D3). On a LawToolBox deadline that key is
    LawToolBox's own id for the event, so an attendee-only edit must re-send it."""
    rec.responses["/events/e-2"] = {**_LINKED_EVENT, "additionalData": {"LawToolBox": "e-2"}, "nonBillable": False}
    server.update_event(event_id="e-2", attendees=["s-1"])
    put = next(c for c in rec.calls if c["method"] == "PUT")
    assert put["json"]["additionalData"] == {"LawToolBox": "e-2"}
    assert put["json"]["nonBillable"] is False


def test_event_readback_flags_a_dropped_additional_data() -> None:
    """The falsifier: a read-back that lost the LawToolBox key is a difference."""
    from smokeball_connector.readback_records import event_diffs

    sent = {"subject": "s", "additionalData": {"LawToolBox": "e-2"}}
    assert any("additionalData" in d for d in event_diffs(sent, {"subject": "s", "additionalData": {}})[0])
    assert not any("additionalData" in d for d in event_diffs(sent, {"subject": "s", **sent})[0])


def test_event_readback_flags_a_billable_flag_the_vendor_flipped() -> None:
    """2026-11-05: Smokeball stored three billable all-day events as
    non-billable on a full PUT that sent ``nonBillable: false``."""
    from smokeball_connector.readback_records import event_diffs

    sent = {"subject": "s", "allDay": True, "nonBillable": False}
    assert any("nonBillable" in d for d in event_diffs(sent, {**sent, "nonBillable": True})[0])
    assert not any("nonBillable" in d for d in event_diffs(sent, dict(sent))[0])
    assert not any("nonBillable" in d for d in event_diffs({"subject": "s"}, {"subject": "s", "nonBillable": True})[0])


def test_update_event_rename_keeps_the_provenance_mark(rec: _Recorder) -> None:
    """create_event stamps "[Operator]"; a rename through update_event used to
    send the new subject bare, so staff could no longer tell the entry was
    machine-made (seen live on the pilot 2026-10-20, vfy_01M5D8FMQ95CWE0JDS5WN1ZZA5)."""
    rec.responses["/events/e-2"] = dict(_LINKED_EVENT)
    server.update_event(event_id="e-2", subject="Discovery: responses due 11/30")
    put = next(c for c in rec.calls if c["method"] == "PUT")
    assert put["json"]["subject"] == "[Operator] Discovery: responses due 11/30"


def test_update_event_explicit_matter_id_relinks(rec: _Recorder) -> None:
    rec.responses["/events/e-2"] = {**_LINKED_EVENT, "matter": None}
    server.update_event(event_id="e-2", matter_id="m-9")
    assert next(c for c in rec.calls if c["method"] == "PUT")["json"]["matterId"] == "m-9"


def test_update_event_recurring_refuses_before_the_wire(rec: _Recorder) -> None:
    rec.responses["/events/e-2"] = {**_LINKED_EVENT, "type": "Recurring"}
    with pytest.raises(ValueError, match="recurring"):
        server.update_event(event_id="e-2", start_time="2026-10-09T00:00:00")
    assert [c for c in rec.calls if c["method"] == "PUT"] == []


def test_update_event_without_any_attendee_refuses_before_the_wire(rec: _Recorder) -> None:
    rec.responses["/events/e-2"] = {**_LINKED_EVENT, "attendees": []}
    with pytest.raises(ValueError, match="attendee"):
        server.update_event(event_id="e-2", start_time="2026-10-09T00:00:00")
    assert [c for c in rec.calls if c["method"] == "PUT"] == []


def test_create_event_reminder(rec: _Recorder) -> None:
    server.create_event_reminder(event_id="e-2", offset=2, offset_type_id=3, user_ids=["s-1"])
    assert rec.calls[0] == {
        "method": "POST",
        "path": "/events/e-2/reminders",
        "json": {"offset": 2, "offsetTypeId": 3, "userIds": ["s-1"]},
    }


def test_list_events_maps_window_params(rec: _Recorder) -> None:
    server.list_events(matter_id="m-9", from_="2026-07-01", to="2026-07-31")
    assert rec.calls[0] == {
        "method": "GET",
        "path": "/events",
        "params": {
            "MatterId": "m-9",
            "From": "2026-07-01",
            "To": "2026-07-31",
            "ExcludeDeletedEvents": True,
            "Limit": 500,
            "Offset": 0,
        },
    }


def test_list_events_excludes_deleted_by_default(rec: _Recorder) -> None:
    # The VENDOR default is false (spec: "default: false"), which returns
    # soft-deleted tombstones alongside live events — proven live 2026-08-02
    # when 11 deleted PROPOSED events were still listed (#2155). The connector
    # must invert that default so a routine never reads a deleted deadline as
    # live; tombstones are an explicit opt-in.
    server.list_events()
    assert rec.calls[0]["params"]["ExcludeDeletedEvents"] is True


def test_list_events_tombstone_read_is_explicit_opt_in(rec: _Recorder) -> None:
    server.list_events(exclude_deleted=False)
    assert rec.calls[0]["params"]["ExcludeDeletedEvents"] is False


# ---- folders --------------------------------------------------------------
def test_create_folder_minimal(rec: _Recorder) -> None:
    server.create_folder(matter_id="m-9", name="Discovery - Set One")
    assert rec.calls[0] == {
        "method": "POST",
        "path": "/matters/m-9/documents/folders",
        "json": {"name": "Discovery - Set One"},
    }


def test_create_folder_nested(rec: _Recorder) -> None:
    server.create_folder(matter_id="m-9", name="Set One", parent_folder_id="f-root")
    assert rec.calls[0]["json"] == {"name": "Set One", "parentFolderId": "f-root"}


def test_list_folders(rec: _Recorder) -> None:
    server.list_folders(matter_id="m-9")
    assert rec.calls[0] == {
        "method": "GET",
        "path": "/matters/m-9/documents/folders",
        "params": {"Limit": 500, "Offset": 0},
    }
