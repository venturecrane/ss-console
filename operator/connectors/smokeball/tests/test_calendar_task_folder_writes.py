"""Unit coverage for the deadline-engine / document-organization write cut:
calendar events, tasks, and folders. No live Smokeball calls — a recording fake
client is injected so we lock the exact path + JSON body each tool sends, and that
optional (None) args are dropped rather than sent as null."""

from __future__ import annotations

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


def test_update_task_without_staff_id_refuses_before_the_wire(rec: _Recorder) -> None:
    """The task read never echoes staffId (proven live 2026-08-31), so a
    completion with no staff_id cannot form a valid PUT; refuse loudly instead
    of letting the tenant 400 (or worse, letting a replace-PUT clear fields)."""
    rec.responses["/tasks/t-7"] = {"id": "t-7", "isCompleted": False}
    with pytest.raises(ValueError, match="staff_id"):
        server.update_task(task_id="t-7", is_completed=True)
    assert [c for c in rec.calls if c["method"] == "PUT"] == []


# ---- events ---------------------------------------------------------------
def test_create_event_forces_normal_type(rec: _Recorder) -> None:
    # attendees + time_zone became required with the live-API contract fix
    # (L2 round 2, 2026-07-06) — see test_create_event_contract.py.
    server.create_event(
        subject="Discovery responses due",
        start_time="2026-07-15T09:00:00",
        end_time="2026-07-15T09:30:00",
        attendees=["s-1"],
        time_zone="America/Los_Angeles",
        matter_id="m-9",
    )
    assert rec.calls[0] == {
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


def test_update_event_partial(rec: _Recorder) -> None:
    server.update_event(event_id="e-2", start_time="2026-08-01T09:00:00")
    assert rec.calls[0] == {
        "method": "PUT",
        "path": "/events/e-2",
        "json": {"startTime": "2026-08-01T09:00:00"},
    }


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
