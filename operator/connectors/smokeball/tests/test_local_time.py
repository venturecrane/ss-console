"""Court times read in the firm's local time (checklist item 1).

Smokeball returns ``startTime`` as a naive UTC clock plus the event's
``timeZone``; the 2026-09-29 defect printed a 9:30 a.m. hearing as 4:30 PM. The
connector now adds ``localDate``/``localTime`` to every event a read returns and
``localDueDate`` to every task, so no skill converts a time on its own."""

from __future__ import annotations

import pytest

from smokeball_connector import local_time, server


class _Recorder:
    def __init__(self, responses: dict) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def get(self, path: str, **params):
        self.calls.append((path, params))
        return self.responses.get(path, {"ok": True})


LA = "America/Los_Angeles"


def test_morning_hearing_reads_local() -> None:
    assert local_time.local_when("2026-10-06T16:30:00", LA) == ("2026-10-06", "9:30 a.m.")
    assert local_time.local_when("2026-10-06T16:30:00Z", LA) == ("2026-10-06", "9:30 a.m.")


def test_evening_utc_lands_on_the_previous_local_day() -> None:
    assert local_time.local_when("2026-10-07T03:00:00Z", LA) == ("2026-10-06", "8:00 p.m.")


def test_clock_noon_and_midnight() -> None:
    assert local_time.clock(12, 0) == "12:00 p.m."
    assert local_time.clock(0, 5) == "12:05 a.m."


@pytest.mark.parametrize(
    "event",
    [
        {"startTime": "2026-10-06T00:00:00Z", "timeZone": LA, "allDay": True},
        {"startTime": "2026-10-06T16:30:00"},
        {"startTime": "2026-10-06T16:30:00", "timeZone": "Mars/Olympus"},
        {"startTime": "2026-10-06", "timeZone": LA},
        {"timeZone": LA},
    ],
)
def test_no_local_keys_when_the_time_cannot_be_known(event: dict) -> None:
    out = local_time.enrich_event(dict(event))
    assert "localDate" not in out
    assert "localTime" not in out


def test_list_events_enriches_items_and_leaves_start_time(monkeypatch) -> None:
    resp = {
        "value": [
            {"id": "e1", "startTime": "2026-10-06T16:30:00", "timeZone": LA},
            {"id": "e2", "startTime": "2026-10-09T00:00:00Z", "timeZone": LA, "allDay": True},
        ]
    }
    rec = _Recorder({"/events": resp})
    monkeypatch.setattr(server, "_get_client", lambda: rec)
    monkeypatch.setattr(server, "_attach_matter_refs_to_list", lambda client, r, **kw: None)
    out = server.list_events()
    first, second = out["value"]
    assert first["localDate"] == "2026-10-06"
    assert first["localTime"] == "9:30 a.m."
    assert first["startTime"] == "2026-10-06T16:30:00"
    assert "localDate" not in second and "localTime" not in second


def test_task_due_date_only_wins(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_TIMEZONE", LA)
    task = local_time.enrich_task({"dueDateOnly": "2026-10-08", "dueDate": "2026-10-09T07:00:00Z"})
    assert task["localDueDate"] == "2026-10-08"
    assert "localDueDateSource" not in task


def test_task_due_date_converts_through_the_firm_zone(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_TIMEZONE", LA)
    task = local_time.enrich_task({"dueDate": "2026-10-09T03:00:00Z"})
    assert task["localDueDate"] == "2026-10-08"
    assert task["localDueDateSource"] == "firm-timezone"


def test_task_due_date_without_a_zone_says_it_is_utc(monkeypatch) -> None:
    monkeypatch.delenv("HERMES_TIMEZONE", raising=False)
    task = local_time.enrich_task({"dueDate": "2026-10-09T03:00:00Z"})
    assert task["localDueDate"] == "2026-10-09"
    assert task["localDueDateSource"] == "dueDate-utc"


def test_list_and_get_task_are_enriched(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_TIMEZONE", LA)
    rec = _Recorder(
        {
            "/tasks": {"value": [{"id": "t1", "dueDate": "2026-10-09T03:00:00Z"}]},
            "/tasks/t2": {"id": "t2", "dueDateOnly": "2026-10-10"},
        }
    )
    monkeypatch.setattr(server, "_get_client", lambda: rec)
    monkeypatch.setattr(server, "_attach_matter_refs_to_list", lambda client, r, **kw: None)
    monkeypatch.setattr(server, "_attach_matter_ref", lambda client, t, **kw: None)
    listed = server.list_tasks()
    assert listed["value"][0]["localDueDate"] == "2026-10-08"
    assert server.get_task("t2")["localDueDate"] == "2026-10-10"


def test_next_day_still_importable_from_server() -> None:
    assert server._next_day("2026-12-31") == "2027-01-01"
