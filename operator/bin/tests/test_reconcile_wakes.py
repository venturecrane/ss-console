"""Tests for the cron-slot watchdog (reconcile-wakes.py + lib/cron_slots.py).

The finding this control exists for is the slot that passed with NEITHER an
EMITTED_WAKE nor a SUPPRESSED_WAKE row -- the dead-daemon / materializer-drift
/ crashed-pre_run shape no other reconciler can see. Its two failure modes are
pinned with equal weight: a grid that misses real slots (DST arithmetic, the
consumption rule) and a grid that pages on designed behavior (boot windows,
suppressed wakes, `always`-policy rows, in-flight slots).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

_BIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BIN / "lib"))

import cron_slots  # noqa: E402 -- path injected above; the import must follow the sys.path shim

_spec = importlib.util.spec_from_file_location("reconcile_wakes", _BIN / "reconcile-wakes.py")
wakes = importlib.util.module_from_spec(_spec)
sys.modules["reconcile_wakes"] = wakes
_spec.loader.exec_module(wakes)

UTC = timezone.utc
LA = ZoneInfo("America/Los_Angeles")


def _row(skill: str, schedule: str, policy: str = "pre_run_decides") -> cron_slots.CronRow:
    return cron_slots.CronRow(skill=skill, schedule=schedule, wake_policy=policy)


def _wake(kind: str, skill: str, ts: datetime, row_id: str = "") -> dict:
    return {
        "id": row_id or f"{kind}-{skill}-{ts.isoformat()}",
        "ts": ts.isoformat(),
        "action_type": kind,
        "skill_name": skill,
        "metadata": "{}",
    }


# ---------------------------------------------------------------------------
# the expander
# ---------------------------------------------------------------------------


def test_daily_slot_expands_seat_local_across_the_fall_back_transition():
    """DST 2026: America/Los_Angeles falls back on Nov 1. `0 7 * * *` is 14:00Z
    in PDT and 15:00Z in PST; the expander must produce both without any offset
    bookkeeping."""
    rows = [_row("deadline-miss-escalator", "0 7 * * *")]
    since = datetime(2026, 10, 31, 0, 0, tzinfo=UTC)
    until = datetime(2026, 11, 3, 0, 0, tzinfo=UTC)
    slots = cron_slots.expand_slots(rows, LA, since, until)
    fires = [s.fires_at.isoformat() for s in slots]
    assert fires == [
        "2026-10-31T14:00:00+00:00",  # PDT (UTC-7)
        "2026-11-01T15:00:00+00:00",  # PST (UTC-8) -- the transition day
        "2026-11-02T15:00:00+00:00",
    ]
    assert all(s.local.endswith(("-07:00", "-08:00")) for s in slots)


def test_weekday_ranges_and_weekly_rows_expand_like_the_authored_grid():
    rows = [
        _row("daily-needs-you-digest", "23 6 * * 1-5"),
        _row("medical-records-chaser", "9 8 * * 2"),
    ]
    # Mon Aug 24 2026 .. Sun Aug 30 2026, seat-local Pacific (PDT, UTC-7).
    since = datetime(2026, 8, 24, 0, 0, tzinfo=LA).astimezone(UTC)
    until = datetime(2026, 8, 31, 0, 0, tzinfo=LA).astimezone(UTC)
    slots = cron_slots.expand_slots(rows, LA, since, until)
    digest = [s for s in slots if s.skill == "daily-needs-you-digest"]
    chaser = [s for s in slots if s.skill == "medical-records-chaser"]
    assert len(digest) == 5  # weekdays only
    assert len(chaser) == 1  # Tuesday only
    assert chaser[0].local.startswith("2026-08-25T08:09")


def test_always_policy_rows_do_not_expand():
    rows = [_row("connector-auth-check", "47 5 * * *", policy="always")]
    since = datetime(2026, 8, 24, 0, 0, tzinfo=UTC)
    assert cron_slots.expand_slots(rows, LA, since, since + timedelta(days=3)) == []


def test_an_unrecognized_schedule_is_refused_never_guessed():
    with pytest.raises(cron_slots.CronParseError):
        cron_slots.parse_schedule("every 30m")
    with pytest.raises(cron_slots.CronParseError):
        cron_slots.parse_schedule("0 7 * *")  # four fields
    with pytest.raises(cron_slots.CronParseError):
        cron_slots.parse_schedule("61 7 * * *")  # out of range


def test_step_and_list_fields_parse():
    spec = cron_slots.parse_schedule("*/15 9-17 * * 1,3,5")
    assert spec.minutes == frozenset({0, 15, 30, 45})
    assert spec.hours == frozenset(range(9, 18))
    assert spec.dow == frozenset({1, 3, 5})


# ---------------------------------------------------------------------------
# matching + consumption
# ---------------------------------------------------------------------------


def _slot(skill: str, at: datetime) -> cron_slots.Slot:
    return cron_slots.Slot(skill=skill, fires_at=at, local=at.isoformat())


def test_a_missing_slot_is_the_finding_shape():
    at = datetime(2026, 8, 28, 14, 0, tzinfo=UTC)
    verdicts = cron_slots.match_slots([_slot("deadline-miss-escalator", at)], [])
    assert [v.is_missing for v in verdicts] == [True]


def test_a_suppressed_wake_covers_its_slot():
    at = datetime(2026, 8, 28, 14, 0, tzinfo=UTC)
    rows = [_wake("SUPPRESSED_WAKE", "deadline-miss-escalator", at + timedelta(minutes=2))]
    verdicts = cron_slots.match_slots([_slot("deadline-miss-escalator", at)], rows)
    assert verdicts[0].covered_by == "SUPPRESSED_WAKE"
    assert not verdicts[0].is_missing


def test_one_wake_row_cannot_cover_two_slots():
    """Consumption: the second slot must answer for itself, or one healthy fire
    launders the dead one beside it."""
    first = datetime(2026, 8, 28, 14, 0, tzinfo=UTC)
    second = first + timedelta(minutes=20)  # both inside one tolerance window
    rows = [_wake("EMITTED_WAKE", "deadline-miss-escalator", first + timedelta(minutes=1))]
    verdicts = cron_slots.match_slots(
        [_slot("deadline-miss-escalator", first), _slot("deadline-miss-escalator", second)], rows
    )
    assert [v.is_missing for v in verdicts] == [False, True]


def test_a_wake_for_another_skill_does_not_cover_the_slot():
    at = datetime(2026, 8, 28, 14, 0, tzinfo=UTC)
    rows = [_wake("EMITTED_WAKE", "daily-needs-you-digest", at + timedelta(minutes=1))]
    verdicts = cron_slots.match_slots([_slot("deadline-miss-escalator", at)], rows)
    assert verdicts[0].is_missing


def test_boot_window_suppresses_only_slots_inside_it():
    heartbeat = datetime(2026, 8, 28, 15, 0, tzinfo=UTC)
    window = cron_slots.boot_window(heartbeat.isoformat(), 3600)  # boot at 14:00
    inside = cron_slots.SlotVerdict(slot=_slot("x", datetime(2026, 8, 28, 13, 30, tzinfo=UTC)))
    outside = cron_slots.SlotVerdict(slot=_slot("x", datetime(2026, 8, 28, 11, 0, tzinfo=UTC)))
    cron_slots.apply_boot_suppression([inside, outside], window)
    assert inside.suppressed_reason == "boot/reprovision window"
    assert not inside.is_missing
    assert outside.suppressed_reason is None and outside.is_missing


def test_boot_window_with_missing_inputs_is_no_window():
    assert cron_slots.boot_window(None, 3600) is None
    assert cron_slots.boot_window("2026-08-28T15:00:00Z", None) is None


# ---------------------------------------------------------------------------
# seat-level grading (reconcile_seat against a synthetic customers dir)
# ---------------------------------------------------------------------------


def _write_seat(tmp_path: Path, slug: str, body: str) -> None:
    seat = tmp_path / slug
    seat.mkdir(parents=True, exist_ok=True)
    (seat / "customer.yaml").write_text(body, encoding="utf-8")


_CRON_SEAT = """
business_hours:
  timezone: America/Los_Angeles
personas:
  - slug: operator
    cron:
      - skill: medical-records-chaser
        schedule: '9 8 * * 2'
        pre_run: pre_run.py
        wake_policy: pre_run_decides
      - skill: connector-auth-check
        schedule: '47 5 * * *'
        wake_policy: always
"""

_EMPTY_SEAT = """
personas:
  - slug: operator
    cron: []
"""


def _outcomes():
    return wakes._load_outcomes_module()


def test_reconcile_seat_grades_a_missing_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(wakes, "customers_dir", lambda: tmp_path)
    _write_seat(tmp_path, "pilot", _CRON_SEAT)
    now = datetime(2026, 8, 26, 0, 0, tzinfo=UTC)  # Wed; Tue slot elapsed
    report = wakes.reconcile_seat(
        "pilot",
        now=now,
        since=now - timedelta(days=3),
        boot_info=None,
        outcomes=_outcomes(),
        rows=[_wake("EMITTED_WAKE", "unrelated-skill", now - timedelta(hours=5))],
    )
    assert len(report.missing) == 1
    assert report.missing[0].slot.skill == "medical-records-chaser"
    assert report.is_finding
    # The always-policy row is n/a, never a finding.
    assert report.na_rows == ["connector-auth-check (wake_policy: always)"]


def test_reconcile_seat_covered_slot_is_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(wakes, "customers_dir", lambda: tmp_path)
    _write_seat(tmp_path, "pilot", _CRON_SEAT)
    now = datetime(2026, 8, 26, 0, 0, tzinfo=UTC)
    slot_utc = datetime(2026, 8, 25, 15, 9, tzinfo=UTC)  # Tue 08:09 PDT
    report = wakes.reconcile_seat(
        "pilot",
        now=now,
        since=now - timedelta(days=3),
        boot_info=None,
        outcomes=_outcomes(),
        rows=[_wake("SUPPRESSED_WAKE", "medical-records-chaser", slot_utc + timedelta(minutes=1))],
    )
    assert report.missing == [] and not report.is_finding


def test_the_silent_wake_split_annotated_here_found_there(tmp_path, monkeypatch):
    """The 08-28 class: the wake row EXISTS (slot covered here) and the
    obligation it opened ended in silence (reconcile-outcomes' finding). This
    control must annotate, not double-file."""
    monkeypatch.setattr(wakes, "customers_dir", lambda: tmp_path)
    _write_seat(tmp_path, "pilot", _CRON_SEAT)
    now = datetime(2026, 8, 26, 0, 0, tzinfo=UTC)
    slot_utc = datetime(2026, 8, 25, 15, 9, tzinfo=UTC)
    wake_row = _wake("EMITTED_WAKE", "medical-records-chaser", slot_utc + timedelta(minutes=1), row_id="w-1")
    report = wakes.reconcile_seat(
        "pilot",
        now=now,
        since=now - timedelta(days=3),
        boot_info=None,
        outcomes=_outcomes(),
        rows=[wake_row],  # no terminal row after it: silent
    )
    assert report.missing == []  # the slot is COVERED here
    assert not report.is_finding  # never double-filed
    assert [v.outcome_disposition for v in report.verdicts] == ["silent"]
    rendered = wakes.render([report])
    assert "obligation silent" in rendered
    assert "reconcile-outcomes owns that finding" in rendered


def test_boot_window_suppression_at_seat_level(tmp_path, monkeypatch):
    monkeypatch.setattr(wakes, "customers_dir", lambda: tmp_path)
    _write_seat(tmp_path, "pilot", _CRON_SEAT)
    now = datetime(2026, 8, 26, 0, 0, tzinfo=UTC)
    slot_utc = datetime(2026, 8, 25, 15, 9, tzinfo=UTC)
    boot_info = {
        # boot at 15:00Z on the 25th: the 15:09 slot falls inside [-45m, +15m].
        "last_heartbeat_ts": "2026-08-25T16:00:00Z",
        "process_uptime_seconds": 3600,
    }
    report = wakes.reconcile_seat(
        "pilot",
        now=now,
        since=now - timedelta(days=3),
        boot_info=boot_info,
        outcomes=_outcomes(),
        rows=[_wake("EMITTED_WAKE", "unrelated-skill", slot_utc)],
    )
    assert report.missing == [] and not report.is_finding
    assert [v.suppressed_reason for v in report.verdicts] == ["boot/reprovision window"]


def test_an_in_flight_slot_is_pending_not_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(wakes, "customers_dir", lambda: tmp_path)
    _write_seat(tmp_path, "pilot", _CRON_SEAT)
    # Evaluate 10 minutes after the Tuesday slot: tolerance has not elapsed.
    now = datetime(2026, 8, 25, 15, 19, tzinfo=UTC)
    report = wakes.reconcile_seat(
        "pilot",
        now=now,
        since=now - timedelta(days=1),
        boot_info=None,
        outcomes=_outcomes(),
        rows=[_wake("EMITTED_WAKE", "unrelated-skill", now)],
    )
    assert report.pending == 1
    assert report.missing == [] and not report.is_finding


def test_an_empty_cron_seat_is_na(tmp_path, monkeypatch):
    monkeypatch.setattr(wakes, "customers_dir", lambda: tmp_path)
    _write_seat(tmp_path, "quiet", _EMPTY_SEAT)
    now = datetime(2026, 8, 26, 0, 0, tzinfo=UTC)
    report = wakes.reconcile_seat(
        "quiet",
        now=now,
        since=now - timedelta(days=3),
        boot_info=None,
        outcomes=_outcomes(),
        rows=[],
    )
    assert report.empty_cron and not report.is_finding
    assert "n/a   quiet: cron []" in wakes.render([report])


def test_render_carries_the_series_marker_and_digest():
    now = datetime(2026, 8, 26, 0, 0, tzinfo=UTC)
    report = wakes.SeatWakeReport(slug="pilot")
    report.verdicts = [cron_slots.SlotVerdict(slot=_slot("medical-records-chaser", now))]
    rendered = wakes.render([report])
    assert "reconcile-series: cron-slot-watchdog" in rendered
    assert "reconcile-findings:" in rendered
    # --json omits the marker (a bare line inside JSON breaks the parse).
    assert "reconcile-series" not in wakes.as_json([report])


def test_digest_is_stable_and_moves_with_the_finding_set():
    now = datetime(2026, 8, 26, 0, 0, tzinfo=UTC)
    one = wakes.SeatWakeReport(slug="pilot")
    one.verdicts = [cron_slots.SlotVerdict(slot=_slot("a", now))]
    also_one = wakes.SeatWakeReport(slug="pilot")
    also_one.verdicts = [cron_slots.SlotVerdict(slot=_slot("a", now))]
    two = wakes.SeatWakeReport(slug="pilot")
    two.verdicts = [
        cron_slots.SlotVerdict(slot=_slot("a", now)),
        cron_slots.SlotVerdict(slot=_slot("b", now)),
    ]
    assert wakes.finding_digest([one]) == wakes.finding_digest([also_one])
    assert wakes.finding_digest([one]) != wakes.finding_digest([two])
    assert wakes.finding_digest([wakes.SeatWakeReport(slug="pilot")]) == ""


# ---------------------------------------------------------------------------
# main(): partitioning + the loud hold
# ---------------------------------------------------------------------------


class _FakeD1:
    def __init__(self, boot_rows=None, explode=False, **_kwargs):
        self._boot = boot_rows if boot_rows is not None else {}
        self._explode = explode

    def fleet_boot_rows(self):
        if self._explode:
            raise RuntimeError("d1 execute failed: auth")
        return self._boot


def test_authored_unprovisioned_seat_skips_and_provisioned_evaluates(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(wakes, "customers_dir", lambda: tmp_path)
    _write_seat(tmp_path, "ghost", _CRON_SEAT)  # authored, NOT in fleet_status
    _write_seat(tmp_path, "quiet", _EMPTY_SEAT)  # authored, empty cron
    monkeypatch.setenv("OPERATOR_RUNTIME_READ_SECRET", "x")
    monkeypatch.setattr(wakes.console_d1, "ConsoleD1", lambda **kwargs: _FakeD1(boot_rows={}))
    code = wakes.main(["--days", "1", "--now", "2026-08-26T00:00:00Z"])
    out = capsys.readouterr().out
    assert "SKIP  ghost: authored but not provisioned" in out
    assert "n/a   quiet" in out
    # quiet was evaluated (zero slots owed), so this is clean, not a hold.
    assert code == wakes.EXIT_CLEAN


def test_d1_unreachable_is_the_loud_hold(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(wakes, "customers_dir", lambda: tmp_path)
    _write_seat(tmp_path, "pilot", _CRON_SEAT)
    monkeypatch.setenv("OPERATOR_RUNTIME_READ_SECRET", "x")
    monkeypatch.setattr(wakes.console_d1, "ConsoleD1", lambda **kwargs: _FakeD1(explode=True))
    code = wakes.main(["--days", "1"])
    assert code == wakes.EXIT_HOLD
    assert "HOLD: fleet_status read failed" in capsys.readouterr().err


def test_missing_credential_is_the_loud_hold(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(wakes, "customers_dir", lambda: tmp_path)
    _write_seat(tmp_path, "pilot", _CRON_SEAT)
    monkeypatch.delenv("OPERATOR_RUNTIME_READ_SECRET", raising=False)
    code = wakes.main(["--days", "1"])
    assert code == wakes.EXIT_HOLD
    assert "OPERATOR_RUNTIME_READ_SECRET unset" in capsys.readouterr().err


def test_offline_rows_need_exactly_one_slug(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(wakes, "customers_dir", lambda: tmp_path)
    _write_seat(tmp_path, "a", _CRON_SEAT)
    _write_seat(tmp_path, "b", _CRON_SEAT)
    extract = tmp_path / "rows.json"
    extract.write_text("[]")
    code = wakes.main(["--rows", str(extract)])
    assert code == wakes.EXIT_HOLD
    assert "exactly one --slug" in capsys.readouterr().err


def test_offline_extract_drives_a_full_offline_grade(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(wakes, "customers_dir", lambda: tmp_path)
    _write_seat(tmp_path, "pilot", _CRON_SEAT)
    extract = tmp_path / "rows.json"
    extract.write_text(json.dumps([]))
    code = wakes.main(["--rows", str(extract), "--slug", "pilot", "--days", "1", "--now", "2026-08-26T00:00:00Z"])
    out = capsys.readouterr().out
    assert code == wakes.EXIT_FINDING
    assert "MISSING" in out


# ---------------------------------------------------------------------------
# the committed grid actually expands (the authored seats stay parseable)
# ---------------------------------------------------------------------------


def test_every_authored_pre_run_decides_schedule_parses():
    """The gate that keeps a customer.yaml edit from silently shrinking the
    watchdog's grid: every authored pre_run_decides schedule must be a shape
    the expander accepts, or this control quietly stops owing that slot."""
    for slug in wakes.seat_slugs():
        config = wakes.load_customer_yaml(slug)
        assert config is not None, f"{slug}: customer.yaml unreadable"
        for row in cron_slots.authored_cron_rows(config):
            if row.wake_policy != "pre_run_decides":
                continue
            cron_slots.parse_schedule(row.schedule)  # raises on refusal
