"""The daemon starts only the lanes the seat authors (ADR 0100)."""

from __future__ import annotations

from medchron import casefile_lane, drafting_lane, lanes, litigation_lane, negotiation_lane, note_lane


def _record_starts(monkeypatch) -> list[str]:
    started: list[str] = []
    for name, module in (
        ("drafting", drafting_lane),
        ("litigation", litigation_lane),
        ("negotiation", negotiation_lane),
        ("note", note_lane),
        ("casefile", casefile_lane),
    ):
        monkeypatch.setattr(module, "start_lane", lambda *_a, _n=name, **_k: started.append(_n))
    return started


def test_only_the_authored_lanes_start(monkeypatch) -> None:
    started = _record_starts(monkeypatch)
    monkeypatch.setenv(lanes.AUTHORED_ENV, "medchron note litigation")
    lanes.start_other_lanes(object(), stop=lambda: True, poll_seconds=1.0)
    assert started == ["litigation", "note"]


def test_a_seat_that_authors_no_lane_starts_none(monkeypatch) -> None:
    started = _record_starts(monkeypatch)
    monkeypatch.setenv(lanes.AUTHORED_ENV, "")
    lanes.start_other_lanes(object(), stop=lambda: True, poll_seconds=1.0)
    assert started == []


def test_an_entrypoint_from_before_the_list_starts_every_lane(monkeypatch) -> None:
    """Unset (an older image's entrypoint) keeps the old behaviour: a client lane never drops on an upgrade."""
    started = _record_starts(monkeypatch)
    monkeypatch.delenv(lanes.AUTHORED_ENV, raising=False)
    lanes.start_other_lanes(object(), stop=lambda: True, poll_seconds=1.0)
    assert started == ["drafting", "litigation", "negotiation", "note", "casefile"]
