"""create_event live-API validation contract (L2 round 2, 2026-07-06).

The /events endpoint rejects, one 400 at a time: missing attendees ("Event
Attendees must have at least one attendee."), all-day events whose start/end
are not exact 24-hour boundaries, and a missing/invalid timeZone. Each 400
also counts toward the connector breaker, so three contract misses take the
whole MCP down mid-turn (how DISC-2 found this). The tool now requires
attendees + time_zone up front (instructive ValueError, no HTTP spent) and
normalizes all-day spans to midnight boundaries. Contract live-verified
against the staging tenant 2026-07-06:

  POST /events {no attendees}                       -> 400 attendees
  POST /events {attendees, 00:00->23:59:59 allDay}  -> 400 24-hour intervals
  POST /events {attendees, 24h span, no timeZone}   -> 400 timeZone
  POST /events {attendees, 24h span, IANA timeZone} -> 202
"""

import pytest

from smokeball_connector import server
from smokeball_connector.server import _next_day, create_event


class _CapturingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method: str, path: str, json: dict | None = None) -> dict:
        self.calls.append((method, path, json or {}))
        return {"id": "evt-1"}


@pytest.fixture()
def capture(monkeypatch: pytest.MonkeyPatch) -> _CapturingClient:
    client = _CapturingClient()
    monkeypatch.setattr(server, "_get_client", lambda: client)
    return client


def test_missing_attendees_fails_fast_without_http(capture: _CapturingClient) -> None:
    with pytest.raises(ValueError, match="at least one attendee"):
        create_event(
            subject="Deadline",
            start_time="2026-07-27T00:00:00Z",
            end_time="2026-07-27T00:00:00Z",
            attendees=[],
            time_zone="America/Los_Angeles",
        )
    assert capture.calls == []


def test_missing_time_zone_fails_fast_without_http(capture: _CapturingClient) -> None:
    with pytest.raises(ValueError, match="IANA time_zone"):
        create_event(
            subject="Deadline",
            start_time="2026-07-27T00:00:00Z",
            end_time="2026-07-27T00:00:00Z",
            attendees=["staff-1"],
            time_zone="",
        )
    assert capture.calls == []


def test_all_day_normalizes_to_midnight_span(capture: _CapturingClient) -> None:
    create_event(
        subject="RFP Set One response due (PROPOSED)",
        start_time="2026-07-27T09:30:00Z",
        end_time="2026-07-27T23:59:59Z",
        attendees=["staff-1"],
        time_zone="America/Los_Angeles",
        matter_id="m-1",
        all_day=True,
    )
    ((_, path, body),) = capture.calls
    assert path == "/events"
    assert body["startTime"] == "2026-07-27T00:00:00"
    assert body["endTime"] == "2026-07-28T00:00:00"
    assert "Z" not in body["startTime"] and "Z" not in body["endTime"]
    assert body["allDay"] is True
    assert body["attendees"] == ["staff-1"]
    assert body["timeZone"] == "America/Los_Angeles"
    assert body["type"] == "Normal"


def test_all_day_multi_day_span_kept(capture: _CapturingClient) -> None:
    create_event(
        subject="Trial",
        start_time="2026-10-13T00:00:00Z",
        end_time="2026-10-17T00:00:00Z",
        attendees=["staff-1"],
        time_zone="America/Los_Angeles",
        all_day=True,
    )
    ((_, _, body),) = capture.calls
    assert body["startTime"] == "2026-10-13T00:00:00"
    assert body["endTime"] == "2026-10-17T00:00:00"


def test_timed_event_passes_through_untouched(capture: _CapturingClient) -> None:
    create_event(
        subject="Hearing",
        start_time="2026-10-06T09:30:00",
        end_time="2026-10-06T10:30:00",
        attendees=["staff-1"],
        time_zone="America/Los_Angeles",
        matter_id="m-1",
    )
    ((_, _, body),) = capture.calls
    assert body["startTime"] == "2026-10-06T09:30:00"
    assert body["endTime"] == "2026-10-06T10:30:00"
    assert "allDay" not in body or not body["allDay"]


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-10-06T09:30:00Z", "2026-10-06T10:30:00"),
        ("2026-10-06T09:30:00", "2026-10-06T10:30:00-07:00"),
        ("2026-10-06T09:30:00+00:00", "2026-10-06T10:30:00"),
    ],
)
def test_a_timed_stamp_with_a_zone_suffix_is_refused(capture: _CapturingClient, start: str, end: str) -> None:
    with pytest.raises(ValueError, match="post the firm's local clock, YYYY-MM-DDThh:mm:ss, no Z"):
        create_event(
            subject="Hearing",
            start_time=start,
            end_time=end,
            attendees=["staff-1"],
            time_zone="America/Los_Angeles",
        )
    assert capture.calls == []


def test_update_event_refuses_a_zone_suffix_before_any_read(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom():
        raise AssertionError("no client call may happen")

    monkeypatch.setattr(server, "_get_client", _boom)
    with pytest.raises(ValueError, match="no Z"):
        server.update_event("evt-1", start_time="2026-10-06T09:30:00Z")


def test_next_day_rolls_month_and_year() -> None:
    assert _next_day("2026-07-31") == "2026-08-01"
    assert _next_day("2026-12-31") == "2027-01-01"
