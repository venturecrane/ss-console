"""Pilot seed event times are the firm's local clock, never UTC.

Smokeball's ``startTime``/``endTime`` are LOCAL wall times in the event's
``timeZone`` (vendor create-event: "date and time will correlate with the time
zone provided"). The pilot seed once posted ``2026-10-06T16:30:00Z`` meaning
9:30 a.m. Pacific; Smokeball stored ``16:30`` and showed the firm 4:30 p.m.
These tests hold every seed event time to the local form: no ``Z``, no offset.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re

SEED_DIR = pathlib.Path(__file__).resolve().parents[1] / "customers/pilot-smokeball/seed"

# "startTime": "...", "endTime": "..." (or start_time=/end_time=) with a Z or
# a numeric offset after the clock.
_SHIFTED = re.compile(
    r"""["']?(?:startTime|endTime|start_time|end_time)["']?\s*[:=]\s*["']"""
    r"""\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})["']"""
)


def _date_prep_events() -> dict:
    spec = importlib.util.spec_from_file_location("seed_date_prep", SEED_DIR / "seed_date_prep.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DATE_PREP_EVENTS


def test_no_seed_event_time_carries_a_z_or_offset() -> None:
    offenders = []
    for path in sorted(SEED_DIR.rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _SHIFTED.search(line):
                offenders.append(path.name + ":" + str(number) + ": " + line.strip())
    assert offenders == [], "seed event times must be local, no Z:\n" + "\n".join(offenders)


def test_the_scan_can_fail() -> None:
    assert _SHIFTED.search('"startTime": "2026-10-06T16:30:00Z",')
    assert _SHIFTED.search("start_time='2026-10-06T16:30:00-07:00'")
    assert not _SHIFTED.search('"startTime": "2026-10-06T09:30:00",')


def test_date_prep_events_are_the_local_times_the_brief_must_say() -> None:
    events = _date_prep_events()
    assert {key: (e["startTime"], e["endTime"]) for key, e in events.items()} == {
        "hearing-minors-compromise-ramirez": ("2026-10-06T09:30:00", "2026-10-06T10:30:00"),
        "trial-readiness-conference-alvarez": ("2026-10-07T09:00:00", "2026-10-07T10:00:00"),
        "final-status-conference-alvarez-draper": ("2026-10-08T08:30:00", "2026-10-08T09:30:00"),
        "mandatory-settlement-conference-whitfield": ("2026-10-09T10:00:00", "2026-10-09T12:00:00"),
    }
    for event in events.values():
        assert event["timeZone"] == "America/Los_Angeles"
        assert "Z" not in event["startTime"] and "Z" not in event["endTime"]


def test_create_event_cannot_regain_a_utc_all_day_span() -> None:
    server = (
        pathlib.Path(__file__).resolve().parents[1] / "connectors/smokeball/smokeball_connector/server.py"
    ).read_text(encoding="utf-8")
    assert "T00:00:00Z" not in server, "all-day spans post the local midnight, no Z"
