"""Tests for the hourly work-health email (work-health.py + lib/work_health*.py).

Every item class has a falsifier: a fixture where the thing went wrong (the item
must appear) beside the nearest fixture where it did not (it must not). The two
properties pinned hardest are the ones the email exists for: a request with no
reply is reported EVEN WHEN a job row exists (the seat's own NO REPLY card goes
quiet there, overlay request_cards.py:428), and a seat that could not be read is
an item, so a failed read can never become an all-clear.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bin.lib import work_health as wh
from bin.lib import work_health_mail as mail
from bin.lib import work_health_sources as src

_BIN = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("work_health_cli", _BIN / "work-health.py")
cli = importlib.util.module_from_spec(_spec)
sys.modules["work_health_cli"] = cli
_spec.loader.exec_module(cli)

UTC = timezone.utc
NOW = datetime(2026, 10, 13, 16, 30, tzinfo=UTC)
CFG = wh.load_config()
SEAT = "ashton-price"
VID = "AAMkADEyOWU0MzEzLWZmMjctNGE1ZS1hZGFhLWNjTEST"


def _row(action: str, at: datetime, skill: str | None = None, **md) -> dict:
    return {
        "id": f"{action}-{at.isoformat()}-{len(md)}",
        "ts": at.isoformat().replace("+00:00", "Z"),
        "action_type": action,
        "skill_name": skill,
        "metadata": json.dumps(md),
    }


def _inbound(at: datetime, vid: str = VID, trust: str = "internal") -> dict:
    return _row("INBOUND_RECEIVED", at, vendor_message_id=vid, trust_class=trust)


def _kinds(items):
    return sorted(i.kind for i in items)


# ---------------------------------------------------------------------------
# requests
# ---------------------------------------------------------------------------


def test_a_request_with_no_reply_after_30_minutes_is_an_item():
    rows = [_inbound(NOW - timedelta(minutes=31))]
    items = wh.request_items(SEAT, rows, CFG, NOW)
    assert _kinds(items) == ["no_reply"]
    assert wh.card_id(VID) in items[0].what


def test_a_request_still_inside_30_minutes_is_not_yet_an_item():
    assert wh.request_items(SEAT, [_inbound(NOW - timedelta(minutes=29))], CFG, NOW) == []


def test_a_replied_request_is_not_an_item():
    rows = [_inbound(NOW - timedelta(hours=2)), _row("REPLY_SENT", NOW - timedelta(hours=2), in_reply_to=VID)]
    assert wh.request_items(SEAT, rows, CFG, NOW) == []


def test_a_job_row_does_not_excuse_a_missing_reply():
    """The seat's NO REPLY card is suppressed once any job exists; that is the
    gap this email closes. A job ran, nobody acknowledged the request: item."""
    rows = [
        _inbound(NOW - timedelta(hours=1)),
        _row("DRAFTING_JOB_SUBMITTED", NOW - timedelta(minutes=59), job_id="J1", matter_number="201208"),
    ]
    assert "no_reply" in _kinds(wh.request_items(SEAT, rows, CFG, NOW))


def test_mail_from_outside_the_firm_is_not_a_request():
    assert wh.request_items(SEAT, [_inbound(NOW - timedelta(hours=1), trust="external")], CFG, NOW) == []


def test_a_held_reply_with_nothing_sent_is_its_own_item():
    rows = [
        _inbound(NOW - timedelta(hours=1)),
        _row("REPLY_HELD", NOW - timedelta(minutes=55), message_id=VID, reason="output_checklist: 2 blocks"),
    ]
    items = wh.request_items(SEAT, rows, CFG, NOW)
    assert _kinds(items) == ["reply_held"]
    assert "output_checklist" in items[0].what and "2 blocks" not in items[0].what


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------


def _job(lane: str, state: str, at: datetime, job_id: str = "01JOBAAAAAAAAAAA", **md) -> dict:
    return _row(f"{lane}_JOB_{state}", at, job_id=job_id, **md)


def test_a_job_running_past_its_lane_limit_is_stuck():
    rows = [
        _job("DRAFTING", "SUBMITTED", NOW - timedelta(hours=3), matter_number="201208"),
        _job("DRAFTING", "RUNNING", NOW - timedelta(hours=3) + timedelta(seconds=5)),
    ]
    items = wh.job_items(SEAT, rows, CFG, NOW)
    assert _kinds(items) == ["job_stuck"]
    assert "201208" in items[0].what and "running" in items[0].what


def test_a_job_running_inside_its_lane_limit_is_not_stuck():
    rows = [_job("DRAFTING", "SUBMITTED", NOW - timedelta(hours=1)), _job("DRAFTING", "RUNNING", NOW - timedelta(minutes=59))]
    assert wh.job_items(SEAT, rows, CFG, NOW) == []


def test_a_job_that_never_started_is_stuck_in_the_queue():
    rows = [_job("DEMAND", "SUBMITTED", NOW - timedelta(minutes=20))]
    items = wh.job_items(SEAT, rows, CFG, NOW)
    assert _kinds(items) == ["job_stuck"] and "queued" in items[0].what


def test_a_held_job_shows_only_its_reason_token_never_the_sentence():
    rows = [
        _job("DRAFTING", "SUBMITTED", NOW - timedelta(hours=2), matter_number="201208"),
        _job("DRAFTING", "FAILED", NOW - timedelta(hours=1), reason="review_held: the Smith deposition.docx cites 2030.050"),
    ]
    items = wh.job_items(SEAT, rows, CFG, NOW)
    assert _kinds(items) == ["job_failed"]
    assert "review_held" in items[0].what
    assert "Smith" not in items[0].what and "2030" not in items[0].what


def test_a_bad_ending_answered_by_a_later_delivery_on_the_matter_drops_off():
    rows = [
        _job("DRAFTING", "SUBMITTED", NOW - timedelta(hours=3), job_id="A", matter_number="201208"),
        _job("DRAFTING", "HELD", NOW - timedelta(hours=2, minutes=50), job_id="A", reason="request_incomplete"),
        _job("DRAFTING", "SUBMITTED", NOW - timedelta(hours=2), job_id="B", matter_number="201208"),
        _job("DRAFTING", "DELIVERED", NOW - timedelta(hours=1), job_id="B"),
    ]
    assert wh.job_items(SEAT, rows, CFG, NOW) == []


def test_a_bad_ending_older_than_the_window_drops_off():
    rows = [_job("MEDCHRON", "SUBMITTED", NOW - timedelta(days=5)), _job("MEDCHRON", "HELD", NOW - timedelta(days=4))]
    assert wh.job_items(SEAT, rows, CFG, NOW) == []


def test_a_delivered_job_is_not_an_item():
    rows = [_job("DEMAND", "SUBMITTED", NOW - timedelta(hours=2)), _job("DEMAND", "DELIVERED", NOW - timedelta(hours=1))]
    assert wh.job_items(SEAT, rows, CFG, NOW) == []


def test_config_lanes_must_match_the_broker_lanes(tmp_path):
    bad = tmp_path / "wh.yaml"
    raw = wh.CONTRACT_PATH.read_text().replace("  medchron:\n", "  medchronx:\n")
    bad.write_text(raw)
    with pytest.raises(ValueError):
        wh.load_config(bad)


# ---------------------------------------------------------------------------
# Smokeball sign-in
# ---------------------------------------------------------------------------


def _probe(at: datetime, ok: bool) -> dict:
    basis = "connector_auth_ok:smokeball" if ok else "connector_auth_failed:smokeball:SmokeballAuthError"
    return _row("SUPPRESSED_WAKE", at, skill=wh.AUTH_PROBE_SKILL, decision_basis=basis)


def test_a_failed_hourly_check_is_an_item():
    rows = [_probe(NOW - timedelta(minutes=70), True), _probe(NOW - timedelta(minutes=10), False)]
    items = wh.smokeball_auth_items(SEAT, rows, CFG, NOW, has_smokeball=True, probe_authored=True)
    assert _kinds(items) == ["auth_failed"]


def test_a_later_success_clears_the_failure():
    rows = [_probe(NOW - timedelta(minutes=70), False), _probe(NOW - timedelta(minutes=10), True)]
    assert wh.smokeball_auth_items(SEAT, rows, CFG, NOW, has_smokeball=True, probe_authored=True) == []


def test_an_idle_seat_with_a_job_failing_on_auth_is_an_item_without_the_probe():
    """The 2026-10-12 A&P shape: no probe authored, a scheduled job failed on
    SmokeballAuthError, nothing succeeded after."""
    rows = [
        _job("NEGOTIATION", "SUBMITTED", NOW - timedelta(hours=2), trigger="scheduled"),
        _job("NEGOTIATION", "FAILED", NOW - timedelta(hours=2), reason="inventory_unreadable: SmokeballAuthError"),
    ]
    items = wh.smokeball_auth_items(SEAT, rows, CFG, NOW, has_smokeball=True, probe_authored=False)
    assert _kinds(items) == ["auth_failed"]


def test_a_silent_probe_is_an_item():
    rows = [_probe(NOW - timedelta(hours=4), True)]
    items = wh.smokeball_auth_items(SEAT, rows, CFG, NOW, has_smokeball=True, probe_authored=True)
    assert _kinds(items) == ["auth_unchecked"]


def test_an_unauthored_probe_is_a_coverage_gap_not_an_hourly_item():
    assert wh.smokeball_auth_items(SEAT, [], CFG, NOW, has_smokeball=True, probe_authored=False) == []
    assert wh.coverage_gaps(has_smokeball=True, probe_authored=False, probe_running=False)
    assert "adopts" in wh.coverage_gaps(has_smokeball=True, probe_authored=True, probe_running=False)[0]
    assert wh.coverage_gaps(has_smokeball=True, probe_authored=True, probe_running=True) == []


def test_authored_but_not_yet_running_is_a_gap_and_its_slots_are_not_missed(monkeypatch):
    """The window between merging the A&P cron row and the seat's rebuild: the
    Machine has no such job, so its slots are not 'missed'."""
    monkeypatch.setattr(cli, "graph_check", lambda slug: (None, None, None))
    console = cli.Console(cli._NoD1())
    items, gaps = cli.evaluate_seat(SEAT, CFG, console, NOW, [])
    assert not any(f"|missed_slot|{wh.AUTH_PROBE_SKILL}|" in i.key for i in items)
    assert any("adopts" in g for g in gaps)
    probe = _probe(NOW - timedelta(minutes=20), True)
    items, gaps = cli.evaluate_seat(SEAT, CFG, console, NOW, [probe])
    assert not any("adopts" in g for g in gaps)
    assert any(f"|missed_slot|{wh.AUTH_PROBE_SKILL}|" in i.key for i in items)


def test_a_seat_that_has_never_reported_is_not_live_yet(monkeypatch):
    console = cli.Console(cli._NoD1())
    console.status["new-seat"] = {"customer_slug": "new-seat", "last_heartbeat_ts": None}
    items, gaps = cli.evaluate_seat("new-seat", CFG, console, NOW, None)
    assert items == [] and "not live yet" in gaps[0]


# ---------------------------------------------------------------------------
# console records
# ---------------------------------------------------------------------------


def test_shortfalls_group_by_tool_and_skip_job_echoes():
    rows = [
        {"ts": "2026-10-13T10:00:00Z", "class": "failed", "tool": "mcp_smokeball_list_matters", "code": "http_400"},
        {"ts": "2026-10-13T11:00:00Z", "class": "failed", "tool": "mcp_smokeball_list_matters", "code": "http_400"},
        {"ts": "2026-10-13T11:00:00Z", "class": "failed", "tool": "drafting_job_status", "code": "drafting_job_failed"},
        {"ts": "2026-10-10T11:00:00Z", "class": "limit", "tool": "x", "code": "old"},
    ]
    items = src.shortfall_items(SEAT, rows, CFG, NOW)
    assert len(items) == 1 and "2 times" in items[0].what


def test_a_stale_heartbeat_is_an_item_and_a_fresh_one_is_not():
    fresh = {"last_heartbeat_ts": (NOW - timedelta(minutes=2)).isoformat()}
    stale = {"last_heartbeat_ts": (NOW - timedelta(hours=1)).isoformat()}
    assert src.heartbeat_items(SEAT, fresh, CFG, NOW) == []
    assert _kinds(src.heartbeat_items(SEAT, stale, CFG, NOW)) == ["heartbeat_stale"]
    assert _kinds(src.heartbeat_items(SEAT, None, CFG, NOW)) == ["heartbeat_stale"]


def test_a_mailbox_poller_that_stopped_succeeding_is_an_item():
    ok = {"connectors_json": json.dumps({"msgraph_mail": {"consecutive_failures": 0, "last_ok_age_seconds": 40}})}
    dead = {"connectors_json": json.dumps({"msgraph_mail": {"consecutive_failures": 0, "last_ok_age_seconds": 7200}})}
    assert src.connector_items(SEAT, ok, NOW) == []
    assert _kinds(src.connector_items(SEAT, dead, NOW)) == ["auth_failed"]


def test_an_idle_smokeball_with_an_old_success_is_not_an_item():
    """Smokeball is called only when work needs it; an old last success is idleness."""
    idle = {"connectors_json": json.dumps({"smokeball": {"consecutive_failures": 0, "last_ok_age_seconds": 90000}})}
    assert src.connector_items(SEAT, idle, NOW) == []


def test_missed_scheduled_slots_come_from_the_watchdog():
    """A weekday with the A&P negotiation-watch rows authored and NO wake rows:
    the 08:07 Pacific slot (15:07Z) passed with neither row."""
    items = src.missed_slot_items(SEAT, [], CFG, NOW, None)
    assert "missed_slot" in _kinds(items)
    assert any("negotiation-watch" in i.what for i in items)


# ---------------------------------------------------------------------------
# a seat that cannot be read
# ---------------------------------------------------------------------------


class _DeadClient:
    def read_page(self, kind, cursor=None, table=None):
        raise OSError("connection refused")


class _EmptyClient:
    def read_page(self, kind, cursor=None, table=None):
        return {"entries": [], "cursor": None}


def test_an_unreadable_ledger_is_an_item(monkeypatch):
    monkeypatch.setattr(cli.seam_pull, "seam_client_from_env", lambda slug: _DeadClient())
    console = cli.Console(cli._NoD1())
    items, _gaps = cli.evaluate_seat(SEAT, CFG, console, NOW, None)
    assert "unreadable" in _kinds(items)


def test_a_ledger_that_reads_back_empty_is_unreadable_not_quiet():
    with pytest.raises(RuntimeError):
        cli.read_ledger(_EmptyClient(), NOW - timedelta(hours=1))


def test_an_unreadable_seat_can_never_produce_an_all_clear():
    unread = wh.item(SEAT, "unreadable", "ledger", NOW, "x", "y")
    plan = mail.decide(items=[unread], seats=[SEAT], gaps={}, previous=None, since=NOW - timedelta(hours=2), now=NOW, all_clear_hour_utc=15)
    assert plan.send and "all clear" not in plan.subject


def test_the_ulid_floor_sorts_at_its_instant():
    a = cli.ulid_floor(NOW)
    assert len(a) == 26 and cli.ulid_floor(NOW - timedelta(seconds=1)) < a < cli.ulid_floor(NOW + timedelta(seconds=1))


# ---------------------------------------------------------------------------
# new vs still open, and the all-clear
# ---------------------------------------------------------------------------


def test_an_item_seen_last_run_is_still_open_one_line():
    it = wh.item(SEAT, "no_reply", "abc", NOW - timedelta(hours=5), "A request ... NO reply", "do")
    previous = {mail.state_key(it.key): (NOW - timedelta(hours=5)).isoformat()}
    plan = mail.decide(items=[it], seats=[SEAT], gaps={}, previous=previous, since=NOW - timedelta(hours=1), now=NOW, all_clear_hour_utc=15)
    assert plan.new == [] and plan.still_open == [it]
    assert "OPEN 5h" in plan.text and "To do" not in plan.text


def test_an_item_not_seen_last_run_is_new_full_text():
    it = wh.item(SEAT, "job_held", "j", NOW - timedelta(minutes=20), "held", "decide")
    plan = mail.decide(items=[it], seats=[SEAT], gaps={}, previous={}, since=NOW - timedelta(hours=1), now=NOW, all_clear_hour_utc=15)
    assert plan.new == [it] and "NEW  held" in plan.text and "To do: decide" in plan.text


def test_without_state_onset_decides_new():
    old = wh.item(SEAT, "job_held", "old", NOW - timedelta(hours=3), "old", "x")
    fresh = wh.item(SEAT, "job_held", "fresh", NOW - timedelta(minutes=10), "fresh", "x")
    plan = mail.decide(items=[old, fresh], seats=[SEAT], gaps={}, previous=None, since=NOW - timedelta(hours=1), now=NOW, all_clear_hour_utc=15)
    assert plan.new == [fresh] and plan.still_open == [old]


@pytest.mark.parametrize(
    ("now", "since", "sends"),
    [
        (NOW, NOW - timedelta(hours=1, minutes=40), True),  # first run after 15:00Z
        (NOW, NOW - timedelta(minutes=30), False),  # an earlier run today already passed 15:00Z
        (NOW.replace(hour=14), NOW.replace(hour=13), False),  # before the hour
    ],
)
def test_the_daily_all_clear(now, since, sends):
    plan = mail.decide(items=[], seats=[SEAT], gaps={}, previous=None, since=since, now=now, all_clear_hour_utc=15)
    assert plan.send is sends
    if sends:
        assert plan.subject.endswith("all clear")


def test_nothing_to_report_sends_nothing_off_the_hour():
    plan = mail.decide(items=[], seats=[SEAT], gaps={}, previous=None, since=NOW - timedelta(minutes=30), now=NOW, all_clear_hour_utc=15)
    assert not plan.send


# ---------------------------------------------------------------------------
# the hourly Smokeball check (connector-auth-check/pre_run.py)
# ---------------------------------------------------------------------------

_PRE_RUN = Path(__file__).resolve().parents[2] / "skills" / "connector-auth-check" / "pre_run.py"


def _pre_run():
    spec = importlib.util.spec_from_file_location("auth_pre_run", _PRE_RUN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_python(tmp_path, line: str) -> str:
    script = tmp_path / "python"
    script.write_text(f"#!/bin/sh\necho '{line}'\n")
    script.chmod(0o755)
    return str(script)


def test_pre_run_ok_and_failure_verdicts(tmp_path, monkeypatch):
    module = _pre_run()
    monkeypatch.setenv("SMD_CONNECTOR_VENV_PYTHON", _fake_python(tmp_path, '{"ok": true}'))
    assert module.check() == (True, "")
    monkeypatch.setenv("SMD_CONNECTOR_VENV_PYTHON", _fake_python(tmp_path, '{"ok": false, "error": "SmokeballAuthError"}'))
    assert module.check() == (False, "SmokeballAuthError")
    monkeypatch.setenv("SMD_CONNECTOR_VENV_PYTHON", str(tmp_path / "absent"))
    assert module.check() == (False, "connector_python_missing")


def test_pre_run_never_wakes_and_records_the_basis(tmp_path, monkeypatch, capsys):
    module = _pre_run()
    monkeypatch.setenv("SMD_CONNECTOR_VENV_PYTHON", _fake_python(tmp_path, '{"ok": false, "error": "SmokeballAuthError"}'))
    seen = []
    monkeypatch.setattr(module, "_broker", lambda req: seen.append(req) or {"ok": True})
    assert module.main() == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["wakeAgent"] is False
    row_meta = json.loads(seen[0]["row"]["metadata"])
    assert seen[0]["action"] == "suppressed_wake_append"
    assert row_meta["decision_basis"] == "connector_auth_failed:smokeball:SmokeballAuthError"
    # And the email reads that very row as a failure.
    row = {"ts": NOW.isoformat(), "action_type": "SUPPRESSED_WAKE", "skill_name": "connector-auth-check", "metadata": seen[0]["row"]["metadata"]}
    assert _kinds(wh.smokeball_auth_items(SEAT, [row], CFG, NOW, has_smokeball=True, probe_authored=True)) == ["auth_failed"]


def test_ap_authors_the_hourly_check():
    import yaml

    config = yaml.safe_load((Path(__file__).resolve().parents[2] / "customers" / SEAT / "customer.yaml").read_text())
    assert cli.seat_shape(config) == (True, True)


def test_the_state_file_names_no_work():
    """It is an Actions artifact on a public repository."""
    it = wh.item(SEAT, "job_held", "drafting:J1", NOW, "The drafting job on matter 201208 held", "x")
    plan = mail.decide(items=[it], seats=[SEAT], gaps={}, previous=None, since=None, now=NOW, all_clear_hour_utc=15)
    blob = json.dumps(plan.state)
    assert "201208" not in blob and "drafting" not in blob and SEAT not in blob


def test_the_run_log_carries_counts_never_the_email_text(tmp_path, capsys, monkeypatch):
    """The CI log of this public repository must never carry a matter number."""
    rows = tmp_path / "rows.json"
    rows.write_text(json.dumps([
        _job("DRAFTING", "SUBMITTED", NOW - timedelta(hours=2), matter_number="201208"),
        _job("DRAFTING", "FAILED", NOW - timedelta(hours=1), reason="review_held: x"),
    ]))
    monkeypatch.setattr(cli, "graph_check", lambda slug: (None, None, None))
    assert cli.main(["--rows", f"{SEAT}={rows}", "--now", NOW.isoformat(), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "job_failed" in out and "201208" not in out
