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
from bin.lib import work_health_auth as auth
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


def test_a_request_smd_closed_drops_off():
    rows = [_inbound(NOW - timedelta(minutes=31))]
    assert wh.request_items(SEAT, rows, CFG, NOW, {(SEAT, wh.card_id(VID))}) == []


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
    rows = [
        _job("DRAFTING", "SUBMITTED", NOW - timedelta(hours=1)),
        _job("DRAFTING", "RUNNING", NOW - timedelta(minutes=59)),
    ]
    assert wh.job_items(SEAT, rows, CFG, NOW) == []


def test_a_job_that_never_started_is_stuck_in_the_queue():
    rows = [_job("DEMAND", "SUBMITTED", NOW - timedelta(minutes=20))]
    items = wh.job_items(SEAT, rows, CFG, NOW)
    assert _kinds(items) == ["job_stuck"] and "queued" in items[0].what


def test_a_held_job_shows_only_its_reason_token_never_the_sentence():
    rows = [
        _job("DRAFTING", "SUBMITTED", NOW - timedelta(hours=2), matter_number="201208"),
        _job(
            "DRAFTING",
            "FAILED",
            NOW - timedelta(hours=1),
            reason="review_held: the Smith deposition.docx cites 2030.050",
        ),
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


def test_a_medchron_update_held_for_smd_finishing_is_visible_as_held_and_unproven():
    """The update's review and finisher loop holds with ``update_held:``
    (smd_finishing): the HELD job row is a job_held item, and its
    DELIVERY_PROOF_FAILED row (code update_held) is open work, never silence."""
    reason = "update_held: the independent review of the update left 1 must-fix finding(s); SMD finishes it"
    rows = [
        _job("MEDCHRON", "SUBMITTED", NOW - timedelta(hours=3), job_id="J5"),
        _job("MEDCHRON", "HELD", NOW - timedelta(hours=1), job_id="J5", reason=reason),
    ]
    assert _kinds(wh.job_items(SEAT, rows, CFG, NOW)) == ["job_held"]
    fail = _row(
        "DELIVERY_PROOF_FAILED", NOW - timedelta(hours=1), job_id="J5", lane="medchron", code="update_held", detail="x"
    )
    items = src.delivery_proof_items(SEAT, [fail], CFG, NOW)
    assert _kinds(items) == ["job_failed"] and "update into the delivered chronology" in items[0].what


def test_a_delivered_job_is_not_an_item():
    rows = [
        _job("DEMAND", "SUBMITTED", NOW - timedelta(hours=2)),
        _job("DEMAND", "DELIVERED", NOW - timedelta(hours=1)),
    ]
    assert wh.job_items(SEAT, rows, CFG, NOW) == []


def test_a_job_smd_closed_with_a_reason_drops_off(tmp_path):
    rows = [
        _job("DRAFTING", "SUBMITTED", NOW - timedelta(hours=2), job_id="J9"),
        _job("DRAFTING", "FAILED", NOW - timedelta(hours=1), job_id="J9", reason="review_held"),
    ]
    assert _kinds(wh.job_items(SEAT, rows, CFG, NOW)) == ["job_failed"]
    assert wh.job_items(SEAT, rows, CFG, NOW, {(SEAT, "J9")}) == []
    bad = tmp_path / "closed.yaml"
    bad.write_text("closed:\n  - seat: x\n    job: J9\n")
    with pytest.raises(ValueError):
        wh.load_closed(bad)
    assert (SEAT, "01M4S9MQE7XZ3E67Q5TDCKJGC6") in wh.load_closed()


def test_config_lanes_must_match_the_broker_lanes(tmp_path):
    bad = tmp_path / "wh.yaml"
    raw = wh.CONTRACT_PATH.read_text().replace("  medchron:\n", "  medchronx:\n")
    bad.write_text(raw)
    with pytest.raises(ValueError):
        wh.load_config(bad)


# ---------------------------------------------------------------------------
# Smokeball sign-in
# ---------------------------------------------------------------------------

AP_DEPLOYED = {("statute-watch", "7 12 1 * *"), ("negotiation-watch", "7 8,12,16 * * 1-5")}


def _probe(at: datetime, outcome: str, detail: str = "") -> dict:
    basis = {
        "ok": "connector_auth_ok:smokeball",
        "refused": "connector_auth_refused:smokeball:http_400",
        "unchecked": "connector_auth_unchecked:smokeball:ModuleNotFoundError",
    }[outcome]
    return _row("SUPPRESSED_WAKE", at, skill=auth.AUTH_PROBE_SKILL, decision_basis=basis, error_detail=detail)


def _auth(rows, scheduled=True):
    return auth.smokeball_auth_items(SEAT, rows, CFG, NOW, probe_scheduled=scheduled)


def test_only_an_explicit_refusal_pages_dead():
    rows = [_probe(NOW - timedelta(minutes=70), "ok"), _probe(NOW - timedelta(minutes=10), "refused")]
    items = _auth(rows)
    assert _kinds(items) == ["auth_failed"]
    assert items[0].page == f"ACTION: {SEAT} Smokeball sign-in is dead"


def test_a_check_that_could_not_run_is_never_called_dead():
    """The 2026-10-13 review: a probe's own failure (venv, import, network) is
    not a dead token. It is an item with the error, and it never pages."""
    items = _auth([_probe(NOW - timedelta(minutes=10), "unchecked", "No module named smokeball_connector")])
    assert _kinds(items) == ["auth_unchecked"]
    assert items[0].page is None
    assert "could not run" in items[0].what and "No module named" in items[0].what
    assert "dead" not in items[0].what.lower().replace("not a dead token", "")


def test_the_first_builds_failed_basis_reads_as_could_not_run():
    row = _row(
        "SUPPRESSED_WAKE",
        NOW - timedelta(minutes=10),
        skill=auth.AUTH_PROBE_SKILL,
        decision_basis="connector_auth_failed:smokeball:SmokeballAuthError",
    )
    assert all(i.page is None for i in _auth([row]))


def test_a_later_success_clears_the_refusal():
    rows = [_probe(NOW - timedelta(minutes=70), "refused"), _probe(NOW - timedelta(minutes=10), "ok")]
    assert _auth(rows) == []


def test_a_job_failing_on_auth_is_an_item_but_not_a_page():
    """The 2026-10-12 A&P shape: a scheduled job failed on SmokeballAuthError.
    Second-hand, so it cannot tell a refusal from a transport fault."""
    rows = [
        _job("NEGOTIATION", "SUBMITTED", NOW - timedelta(hours=2), trigger="scheduled"),
        _job("NEGOTIATION", "FAILED", NOW - timedelta(hours=2), reason="inventory_unreadable: SmokeballAuthError"),
    ]
    items = _auth(rows, scheduled=False)
    assert _kinds(items) == ["auth_unchecked"] and items[0].page is None


def test_a_silent_scheduled_check_is_an_action_item():
    items = _auth([_probe(NOW - timedelta(hours=4), "ok")])
    assert _kinds(items) == ["auth_unchecked"] and items[0].what.startswith("ACTION")


def test_a_smokeball_seat_whose_check_is_not_scheduled_is_an_action_item():
    def why(authored, deployed):
        return cli.probe_not_scheduled_why(True, authored, deployed)

    assert why(None, AP_DEPLOYED).startswith("no connector-auth-check")
    assert "not been rebuilt" in why("13 * * * *", AP_DEPLOYED)
    assert why("13 * * * *", AP_DEPLOYED | {("connector-auth-check", "13 * * * *")}) is None
    assert cli.probe_not_scheduled_why(False, None, None) is None
    items = auth.probe_not_scheduled_items(SEAT, NOW, why="x")
    assert _kinds(items) == ["auth_unchecked"] and items[0].what.startswith("ACTION")


def _console(deployed=AP_DEPLOYED):
    console = cli.Console(cli._NoD1())
    console.personas[SEAT] = json.dumps([{"cron": [{"skill": k, "schedule": v} for k, v in sorted(deployed)]}])
    return console


def test_a_seat_that_has_never_reported_is_not_live_yet():
    console = cli.Console(cli._NoD1())
    console.status["new-seat"] = {"customer_slug": "new-seat", "last_heartbeat_ts": None}
    items, gaps, _resolved = cli.evaluate_seat("new-seat", CFG, console, NOW, None)
    assert items == [] and "not live yet" in gaps[0]


# ---------------------------------------------------------------------------
# scheduled runs: graded against what the seat was PROVISIONED with
# ---------------------------------------------------------------------------

PILOT_GIT = {
    "business_hours": {"timezone": "America/Los_Angeles"},
    "personas": [
        {
            "cron": [
                {
                    "skill": "connector-auth-check",
                    "schedule": "13 * * * *",
                    "pre_run": "pre_run.py",
                    "wake_policy": "pre_run_decides",
                },
                {
                    "skill": "daily-thing",
                    "schedule": "47 5 * * *",
                    "pre_run": "pre_run.py",
                    "wake_policy": "pre_run_decides",
                },
            ]
        }
    ],
}


def _wake(skill: str, at: datetime) -> dict:
    return _row("SUPPRESSED_WAKE", at, skill=skill, decision_basis="x")


def test_a_daily_cron_that_ran_on_time_is_not_missed():
    """The falsifier for the 2026-10-13 false lines: a daily 05:47 Pacific row
    (12:47Z) that ran is not "never ran"; the authored-but-unadopted hourly row
    is ONE drift line, not 24 missed slots."""
    deployed = {("connector-auth-check", "47 5 * * *"), ("daily-thing", "47 5 * * *")}
    rows = [_wake("daily-thing", datetime(2026, 10, 13, 12, 48, tzinfo=UTC))]
    assert src.missed_slot_items("pilot", rows, CFG, NOW, None, PILOT_GIT, deployed, adoption_known=False) == []
    # Any other routine's drift is one line naming both schedules.
    deployed = {("connector-auth-check", "13 * * * *"), ("daily-thing", "7 9 * * *")}
    items = src.missed_slot_items("pilot", rows, CFG, NOW, None, PILOT_GIT, deployed, adoption_known=False)
    drift = [i for i in items if i.kind == "schedule_drift"]
    assert len(drift) == 1 and "47 5 * * *" in drift[0].what and "7 9 * * *" in drift[0].what


def test_a_daily_cron_that_did_not_run_is_one_line():
    deployed = {("connector-auth-check", "13 * * * *"), ("daily-thing", "47 5 * * *")}
    rows = [_wake("connector-auth-check", NOW - timedelta(minutes=m)) for m in range(17, 1440, 60)]
    items = src.missed_slot_items("pilot", rows, CFG, NOW, None, PILOT_GIT, deployed, adoption_known=False)
    assert _kinds(items) == ["missed_slot"] and "daily-thing" in items[0].what


def test_many_missed_slots_of_one_routine_are_one_line():
    deployed = {("connector-auth-check", "13 * * * *"), ("daily-thing", "47 5 * * *")}
    rows = [_wake("daily-thing", datetime(2026, 10, 13, 12, 48, tzinfo=UTC))]
    items = src.missed_slot_items("pilot", rows, CFG, NOW, None, PILOT_GIT, deployed, adoption_known=False)
    assert _kinds(items) == ["missed_slot"]
    assert "scheduled runs missed" in items[0].what


def test_an_unreadable_provisioned_schedule_is_an_item_not_a_guess():
    items = src.missed_slot_items("pilot", [], CFG, NOW, None, PILOT_GIT, None, adoption_known=False)
    assert _kinds(items) == ["unreadable"]


# ---------------------------------------------------------------------------
# console records, and open work only
# ---------------------------------------------------------------------------


def test_shortfalls_are_one_line_per_tool_and_only_while_unanswered():
    rows = [
        {"ts": "2026-10-13T10:00:00Z", "class": "failed", "tool": "mcp_smokeball_list_matters", "code": "http_400"},
        {"ts": "2026-10-13T11:00:00Z", "class": "failed", "tool": "mcp_smokeball_list_matters", "code": "http_500"},
        {"ts": "2026-10-13T11:00:00Z", "class": "failed", "tool": "drafting_job_status", "code": "drafting_job_failed"},
        {"ts": "2026-10-10T11:00:00Z", "class": "limit", "tool": "x", "code": "old"},
    ]
    items, resolved = src.shortfall_items(SEAT, rows, CFG, NOW, [])
    assert len(items) == 1 and "2 times" in items[0].what and resolved == 0
    later = [_row("REPLY_SENT", datetime(2026, 10, 13, 12, 0, tzinfo=UTC), in_reply_to="x")]
    items, resolved = src.shortfall_items(SEAT, rows, CFG, NOW, later)
    assert items == [] and resolved == 2


def test_a_held_reply_whose_message_went_out_is_history():
    held = _row("REPLY_HELD", NOW - timedelta(hours=3), message_id=VID, reason="duplicate_reply")
    held["id"] = "HELD1"
    sent = _row("REPLY_SENT", NOW - timedelta(hours=4), in_reply_to=VID)
    assert src._held_then_sent([held, sent], "HELD1")
    assert not src._held_then_sent([held], "HELD1")


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
    items, _gaps, _resolved = cli.evaluate_seat(SEAT, CFG, console, NOW, None)
    assert "unreadable" in _kinds(items)


def test_a_ledger_that_reads_back_empty_is_unreadable_not_quiet():
    with pytest.raises(RuntimeError):
        cli.read_ledger(_EmptyClient(), NOW - timedelta(hours=1))


def test_an_unreadable_seat_can_never_produce_an_all_clear():
    unread = wh.item(SEAT, "unreadable", "ledger", NOW, "x", "y")
    plan = mail.decide(
        items=[unread],
        seats=[SEAT],
        gaps={},
        previous=None,
        since=NOW - timedelta(hours=2),
        now=NOW,
        all_clear_hour_utc=15,
    )
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
    plan = mail.decide(
        items=[it],
        seats=[SEAT],
        gaps={},
        previous=previous,
        since=NOW - timedelta(hours=1),
        now=NOW,
        all_clear_hour_utc=15,
    )
    assert plan.new == [] and plan.still_open == [it]
    assert "(open 5h)" in plan.text and "To do" not in plan.text


def test_an_item_not_seen_last_run_is_new_full_text():
    it = wh.item(SEAT, "job_held", "j", NOW - timedelta(minutes=20), "held", "decide")
    plan = mail.decide(
        items=[it], seats=[SEAT], gaps={}, previous={}, since=NOW - timedelta(hours=1), now=NOW, all_clear_hour_utc=15
    )
    assert plan.new == [it] and "NEW  held" in plan.text and "To do: decide" in plan.text


def test_without_state_onset_decides_new():
    old = wh.item(SEAT, "job_held", "old", NOW - timedelta(hours=3), "old", "x")
    fresh = wh.item(SEAT, "job_held", "fresh", NOW - timedelta(minutes=10), "fresh", "x")
    plan = mail.decide(
        items=[old, fresh],
        seats=[SEAT],
        gaps={},
        previous=None,
        since=NOW - timedelta(hours=1),
        now=NOW,
        all_clear_hour_utc=15,
    )
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
    plan = mail.decide(
        items=[],
        seats=[SEAT],
        gaps={},
        previous=None,
        since=NOW - timedelta(minutes=30),
        now=NOW,
        all_clear_hour_utc=15,
    )
    assert not plan.send


# ---------------------------------------------------------------------------
# the hourly Smokeball check (connector-auth-check/pre_run.py)
# ---------------------------------------------------------------------------

_PRE_RUN = Path(__file__).resolve().parents[2] / "skills" / "connector-auth-check" / "pre_run.py"


def _pre_run():
    spec = importlib.util.spec_from_file_location("auth_pre_run", _PRE_RUN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_python(tmp_path, line: str) -> str:
    script = tmp_path / "python"
    script.write_text(f"#!/bin/sh\necho '{line}'\n")
    script.chmod(0o755)
    return str(script)


def test_pre_run_tells_a_refusal_from_a_check_that_could_not_run(tmp_path, monkeypatch):
    module = _pre_run()
    monkeypatch.setenv("SMD_CONNECTOR_VENV_PYTHON", _fake_python(tmp_path, '{"ok": true}'))
    assert module.check()[0] == "ok"
    monkeypatch.setenv(
        "SMD_CONNECTOR_VENV_PYTHON",
        _fake_python(
            tmp_path, '{"ok": false, "refused": true, "error": "http_400", "detail": "rejected with HTTP 400"}'
        ),
    )
    assert module.check()[:2] == ("refused", "http_400")
    monkeypatch.setenv(
        "SMD_CONNECTOR_VENV_PYTHON",
        _fake_python(
            tmp_path, '{"ok": false, "refused": false, "error": "ModuleNotFoundError", "detail": "import failed"}'
        ),
    )
    assert module.check()[:2] == ("unchecked", "ModuleNotFoundError")
    monkeypatch.setenv("SMD_CONNECTOR_VENV_PYTHON", str(tmp_path / "absent"))
    assert module.check()[:2] == ("unchecked", "connector_python_missing")


def test_the_snippet_reads_a_4xx_refusal_as_refused(tmp_path):
    """Runs the real snippet against a stub connector whose token mint raises
    exactly what client._mint_token raises on invalid_grant."""
    pkg = tmp_path / "smokeball_connector"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "client.py").write_text(
        "class SmokeballAuthError(RuntimeError):\n    pass\n"
        "class _C:\n    def auth_status(self):\n"
        "        raise SmokeballAuthError('token mint (authorization_code) rejected with HTTP 400 at https://x/oauth2/token')\n"
        "def build_client_from_env():\n    return _C()\n"
    )
    import subprocess

    out = subprocess.run(  # noqa: S603 - sys.executable and a module-constant snippet from the pre_run under test; no shell
        [sys.executable, "-c", _pre_run()._CHECK_SNIPPET], capture_output=True, text=True, cwd=tmp_path, check=True
    ).stdout
    verdict = json.loads(out.strip().splitlines()[-1])
    assert verdict["refused"] is True and verdict["error"] == "http_400"


def test_pre_run_never_wakes_and_records_the_basis(tmp_path, monkeypatch, capsys):
    module = _pre_run()
    monkeypatch.setenv(
        "SMD_CONNECTOR_VENV_PYTHON",
        _fake_python(
            tmp_path, '{"ok": false, "refused": true, "error": "http_400", "detail": "rejected with HTTP 400"}'
        ),
    )
    seen = []
    monkeypatch.setattr(module, "_broker", lambda req: seen.append(req) or {"ok": True})
    assert module.main() == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["wakeAgent"] is False
    row_meta = json.loads(seen[0]["row"]["metadata"])
    assert seen[0]["action"] == "suppressed_wake_append"
    assert row_meta["decision_basis"] == "connector_auth_refused:smokeball:http_400"
    # And the email reads that very row as a dead sign-in that pages.
    row = {
        "ts": NOW.isoformat(),
        "action_type": "SUPPRESSED_WAKE",
        "skill_name": "connector-auth-check",
        "metadata": seen[0]["row"]["metadata"],
    }
    assert [i.page for i in auth.smokeball_auth_items(SEAT, [row], CFG, NOW, probe_scheduled=True)] == [
        f"ACTION: {SEAT} Smokeball sign-in is dead"
    ]


def test_every_smokeball_seat_authors_the_hourly_check():
    import yaml

    customers = Path(__file__).resolve().parents[2] / "customers"
    for path in sorted(customers.glob("[!_]*/customer.yaml")):
        has_smokeball, schedule = cli.seat_shape(yaml.safe_load(path.read_text()))
        # Every Smokeball seat authors the hourly check: it is the only detector.
        assert schedule or not has_smokeball, f"{path.parent.name} has Smokeball but no hourly sign-in check"


def test_the_state_file_names_no_work():
    """It is an Actions artifact on a public repository."""
    it = wh.item(SEAT, "job_held", "drafting:J1", NOW, "The drafting job on matter 201208 held", "x")
    plan = mail.decide(items=[it], seats=[SEAT], gaps={}, previous=None, since=None, now=NOW, all_clear_hour_utc=15)
    blob = json.dumps(plan.state)
    assert "201208" not in blob and "drafting" not in blob and SEAT not in blob


def _offline(monkeypatch, tmp_path, rows):
    path = tmp_path / "rows.json"
    path.write_text(json.dumps(rows))
    monkeypatch.setattr(cli, "graph_check", lambda slug: (None, None, None))
    monkeypatch.setattr(cli, "_NoD1", _ApD1)
    return path


class _ApD1:
    def execute(self, sql: str) -> list[dict]:
        if "customer_configs" in sql:
            deployed = sorted(AP_DEPLOYED | {("connector-auth-check", "13 * * * *")})
            return [
                {
                    "customer_slug": SEAT,
                    "personas_json": json.dumps([{"cron": [{"skill": k, "schedule": v} for k, v in deployed]}]),
                }
            ]
        return []


def test_the_run_log_carries_counts_never_the_email_text(tmp_path, capsys, monkeypatch):
    """The CI log of this public repository must never carry a matter number."""
    rows = _offline(
        monkeypatch,
        tmp_path,
        [
            _job("DRAFTING", "SUBMITTED", NOW - timedelta(hours=2), matter_number="201208"),
            _job("DRAFTING", "FAILED", NOW - timedelta(hours=1), reason="review_held: x"),
        ],
    )
    assert cli.main(["--rows", f"{SEAT}={rows}", "--now", NOW.isoformat(), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "job_failed" in out and "201208" not in out


def test_a_dead_sign_in_pages_every_run_on_its_own(monkeypatch, tmp_path):
    """A refused check produces its own ACTION email (not only a digest line),
    on this run and the next."""
    rows = _offline(monkeypatch, tmp_path, [_probe(NOW - timedelta(minutes=10), "refused")])
    sent = []
    monkeypatch.setattr(cli, "send", lambda subject, text, key: sent.append((subject, key)) or None)
    for now in (NOW, NOW + timedelta(minutes=40)):
        assert cli.main(["--rows", f"{SEAT}={rows}", "--now", now.isoformat()]) == 0
    pages = [s for s in sent if s[0].startswith("ACTION:")]
    assert [p[0] for p in pages] == [f"ACTION: {SEAT} Smokeball sign-in is dead"] * 2
    assert pages[0][1] != pages[1][1]  # a fresh idempotency key each hour, so the repeat is not deduped away


def test_a_check_that_could_not_run_never_pages(monkeypatch, tmp_path):
    rows = _offline(monkeypatch, tmp_path, [_probe(NOW - timedelta(minutes=10), "unchecked", "boom")])
    sent = []
    monkeypatch.setattr(cli, "send", lambda subject, text, key: sent.append(subject) or None)
    assert cli.main(["--rows", f"{SEAT}={rows}", "--now", NOW.isoformat()]) == 0
    assert not [s for s in sent if s.startswith("ACTION:")]


def test_a_mailbox_sign_in_failure_pages():
    items = src.graph_probe_item(SEAT, "ops@example.test", "msgraph token mint rejected with HTTP 401", NOW)
    assert items[0].page == f"ACTION: {SEAT} mailbox sign-in is dead"


def test_a_hosted_agent_seat_gets_machine_checks_only():
    """brokery and scott are hosted agents: no request cards, job lanes or
    Smokeball. Rows that would be Operator findings on an Operator seat must
    produce nothing there, and the email says why."""
    import yaml

    customers = Path(__file__).resolve().parents[2] / "customers"
    hosted = [
        p.parent.name
        for p in customers.glob("[!_]*/customer.yaml")
        if cli.seat_product(yaml.safe_load(p.read_text())) == "hosted-agent"
    ]
    assert "scott" in hosted
    console = cli.Console(cli._NoD1())
    rows = [_inbound(NOW - timedelta(hours=1)), _job("DRAFTING", "SUBMITTED", NOW - timedelta(hours=5))]
    for seat in hosted:
        console.status[seat] = {"last_heartbeat_ts": (NOW - timedelta(minutes=1)).isoformat()}
        items, gaps, _ = cli.evaluate_seat(seat, CFG, console, NOW, rows)
        assert items == [] and "machine checks only" in gaps[0]
    # The same rows on an Operator seat ARE findings (the falsifier).
    console.status[SEAT] = {"last_heartbeat_ts": (NOW - timedelta(minutes=1)).isoformat()}
    items, _gaps, _ = cli.evaluate_seat(SEAT, CFG, _console(), NOW, rows)
    assert {"no_reply", "job_stuck"} <= set(_kinds(items))


def test_a_job_held_for_an_unproven_lane_is_waiting_not_stuck():
    rows = [
        _job("LITIGATION", "SUBMITTED", NOW - timedelta(hours=5), reason="lane_unproven: litigation"),
    ]
    items = wh.job_items(SEAT, rows, CFG, NOW)
    assert _kinds(items) == ["job_held"]
    assert "proof run" in items[0].what and "lane-proof.sh" in items[0].todo


def test_the_no_reply_threshold_is_per_seat():
    assert CFG.no_reply_for("smd-staging") == 5 and CFG.no_reply_for(SEAT) == 30
    rows = [_inbound(NOW - timedelta(minutes=6))]
    assert _kinds(wh.request_items("smd-staging", rows, CFG, NOW)) == ["no_reply"]
    assert wh.request_items(SEAT, rows, CFG, NOW) == []


def test_a_drafting_job_held_by_the_lane_gate_is_waiting_not_stuck_in_queue():
    """For drafting the hold writes no audit row; the seat's lane_unproven card is
    the signal. Without the card the same row IS stuck (the falsifier)."""
    rows = [_job("DRAFTING", "SUBMITTED", NOW - timedelta(hours=3))]
    assert _kinds(wh.job_items(SEAT, rows, CFG, NOW)) == ["job_stuck"]
    card = {
        "job_lane": "drafting",
        "lane_key": "document-drafter.discovery_set",
        "event_at": (NOW - timedelta(hours=3) + timedelta(seconds=40)).isoformat(),
    }
    items = wh.job_items(SEAT, rows, CFG, NOW, None, [card])
    assert _kinds(items) == ["job_held"] and "document-drafter.discovery_set" in items[0].what
    other_lane = {**card, "job_lane": "demand"}
    assert _kinds(wh.job_items(SEAT, rows, CFG, NOW, None, [other_lane])) == ["job_stuck"]


def test_a_failed_delivery_proof_is_open_until_proven():
    fail = _row(
        "DELIVERY_PROOF_FAILED",
        NOW - timedelta(hours=1),
        job_id="J7",
        lane="drafting",
        code="readback_mismatch",
        detail="x",
    )
    items = src.delivery_proof_items(SEAT, [fail], CFG, NOW)
    assert _kinds(items) == ["job_failed"] and "the files on the matter differ" in items[0].what
    proven = _row("DELIVERY_PROVEN", NOW - timedelta(minutes=30), job_id="J7", lane="drafting")
    assert src.delivery_proof_items(SEAT, [fail, proven], CFG, NOW) == []


def test_a_missed_hourly_run_is_an_item_and_breaks_the_all_clear():
    """A dropped schedule slot must never read as silence."""
    assert wh.missed_run_items(NOW - timedelta(minutes=60), CFG, NOW) == []
    gap = wh.missed_run_items(NOW - timedelta(hours=3), CFG, NOW)
    assert _kinds(gap) == ["monitor_gap"] and "Previous run missing" in gap[0].what
    plan = mail.decide(
        items=gap, seats=[SEAT], gaps={}, previous=None, since=NOW - timedelta(hours=3), now=NOW, all_clear_hour_utc=15
    )
    assert plan.send and "all clear" not in plan.subject and "\nwork-health\n" in plan.text


def test_slots_before_the_machine_adopted_the_schedule_are_not_owed():
    """The 2026-10-13 21:21Z A&P false line: published 20:35Z, booted 21:12Z,
    and 23 hourly slots before the boot were reported missed."""
    deployed = {("connector-auth-check", "13 * * * *"), ("daily-thing", "47 5 * * *")}
    rows = [_wake("daily-thing", datetime(2026, 10, 13, 12, 48, tzinfo=UTC))]
    booted = NOW - timedelta(minutes=40)  # 15:50Z boot; the 16:13Z slot is not yet gradable at 16:30Z
    boot = {"last_heartbeat_ts": NOW.isoformat(), "process_uptime_seconds": 40 * 60}
    assert src.schedule_adopted_at((booted - timedelta(minutes=37)).isoformat(), boot) == booted
    assert src.schedule_adopted_at((booted + timedelta(minutes=5)).isoformat(), boot) is None
    items = src.missed_slot_items("pilot", rows, CFG, NOW, boot, PILOT_GIT, deployed, booted)
    assert items == []
    # Not yet booted into the published schedule: one line, no grading.
    items = src.missed_slot_items("pilot", rows, CFG, NOW, boot, PILOT_GIT, deployed, None)
    assert _kinds(items) == ["schedule_drift"] and "not been rebuilt" in items[0].what


def test_a_job_a_person_ended_as_superseded_is_not_open_work():
    rows = [
        _job("MEDCHRON", "SUBMITTED", NOW - timedelta(minutes=10)),
        _job("MEDCHRON", "FAILED", NOW - timedelta(minutes=9), reason="superseded: relaunched"),
    ]
    assert wh.job_items(SEAT, rows, CFG, NOW) == []


# ---------------------------------------------------------------------------
# the 2026-10-16 redesign: one line per problem, plain words, clients first
# ---------------------------------------------------------------------------


def _refused_stream(start: datetime, minutes: int) -> list[dict]:
    """An out-of-turn send refused by the outbound scan every 30 seconds: the
    A&P lapse-letter loop of 2026-10-15/16."""
    return [
        _row(
            "FABRICATION_FILTER_TRIGGERED",
            start + timedelta(seconds=30 * i),
            tool="smd_send_message",
            marker_hits="['specific-dollar-amount']",
        )
        for i in range(minutes * 2)
    ]


def test_a_repeating_refused_send_is_one_line_not_one_per_half_hour():
    """FALSIFIER: before the redesign the reconciler's half-hour slices each
    became an item; three hours of refusals was six lines."""
    rows = _refused_stream(NOW - timedelta(hours=3), 170)
    items, _ = src.silent_run_items(SEAT, rows, CFG, NOW)
    assert _kinds(items) == ["silent_run"]
    assert "blocked by the safety check" in items[0].what
    assert "times since" in items[0].what
    later, _ = src.silent_run_items(SEAT, rows + _refused_stream(NOW, 30), CFG, NOW + timedelta(hours=1))
    assert [i.key for i in later] == [items[0].key]


def test_a_refused_letter_whose_request_was_closed_is_not_open_work():
    """FALSIFIER: on 2026-10-16 the A&P lapse letter was refused 5,246 times,
    then SMD closed its request by hand (RULE_LAPSED 13 s after the last
    refusal), yet the email still said "Whoever it was for has not been told".
    A closing row long after the last refusal, or refusals that keep coming
    after it, stay open."""
    start = NOW - timedelta(hours=3)
    rows = _refused_stream(start, 170)
    last = start + timedelta(seconds=30 * (len(rows) - 1))
    told = _row("RULE_LAPSED", last + timedelta(seconds=13), proposal_id="7fbf43bb")
    items, resolved = src.silent_run_items(SEAT, rows + [told], CFG, NOW)
    assert items == [] and resolved == 1
    stale = _row("RULE_LAPSED", last + timedelta(minutes=10), proposal_id="7fbf43bb")
    assert _kinds(src.silent_run_items(SEAT, rows + [stale], CFG, NOW)[0]) == ["silent_run"]
    more = _refused_stream(last + timedelta(seconds=30), 5)
    assert _kinds(src.silent_run_items(SEAT, rows + [told] + more, CFG, NOW)[0]) == ["silent_run"]


def test_a_delivered_job_with_a_lane_alert_is_not_called_undelivered():
    """Six of the 33 lines on 2026-10-16: negotiation runs that delivered, whose
    lane carried a held offer as an alert in the same second."""
    at = NOW - timedelta(hours=2)
    proven = _row("DELIVERY_PROVEN", at, job_id="N1", lane="negotiation")
    alert = _row(
        "DELIVERY_PROOF_FAILED",
        at,
        job_id="N1",
        lane="negotiation",
        code="review_held",
        detail="job: delivered, and its lane raised review_held",
    )
    assert src.delivery_proof_items(SEAT, [proven, alert], CFG, NOW) == []
    held = _row("DELIVERY_PROOF_FAILED", at, job_id="N2", lane="negotiation", code="review_held", detail="held")
    assert _kinds(src.delivery_proof_items(SEAT, [held], CFG, NOW)) == ["job_failed"]


def test_test_seats_are_a_footnote_but_a_failed_read_is_always_listed():
    client = wh.item(SEAT, "no_reply", "a", NOW - timedelta(hours=1), "A request has had NO reply", "answer it")
    pilot = wh.item("pilot-smokeball", "connector_alert", "b", NOW - timedelta(days=60), "Open alert x", "work it")
    broken = wh.item("smd-staging", "unreadable", "c", NOW, "Could not read this seat's work ledger", "fix it")
    plan = mail.decide(
        items=[client, pilot, broken],
        seats=[SEAT, "pilot-smokeball", "smd-staging"],
        gaps={},
        previous=None,
        since=NOW - timedelta(hours=1),
        now=NOW,
        all_clear_hour_utc=15,
        test_seats=frozenset({"pilot-smokeball", "smd-staging"}),
    )
    assert plan.subject == "[SMD Ops] Work health: 2 need attention (2 new)"
    assert plan.text.startswith("2 things need attention, 2 new since the last email.")
    assert "Open alert x" not in plan.text
    assert "Test seats, not listed: pilot-smokeball 1." in plan.text
    assert "Could not read this seat's work ledger" in plan.text
    assert plan.text.index(SEAT) < plan.text.index("smd-staging")


def test_only_test_seat_items_send_nothing_until_the_daily_all_clear():
    pilot = wh.item("pilot-smokeball", "connector_alert", "b", NOW - timedelta(days=60), "Open alert x", "work it")
    kwargs = dict(
        items=[pilot],
        seats=[SEAT, "pilot-smokeball"],
        gaps={},
        previous=None,
        now=NOW,
        all_clear_hour_utc=15,
        test_seats=frozenset({"pilot-smokeball"}),
    )
    assert not mail.decide(since=NOW - timedelta(minutes=30), **kwargs).send
    daily = mail.decide(since=NOW - timedelta(hours=2), **kwargs)
    assert daily.subject.endswith("all clear")
    assert "Test seats, not listed: pilot-smokeball 1." in daily.text


def test_times_read_in_phoenix():
    assert wh.hhmm(datetime(2026, 10, 16, 14, 51, tzinfo=UTC)) == "Oct 16 7:51am MST"
    assert wh.hhmm(datetime(2026, 1, 5, 23, 5, tzinfo=UTC)) == "Jan 5 4:05pm MST"


def test_a_seat_is_hidden_only_when_its_own_yaml_says_it_is_not_a_client():
    assert cli.footnoted_seats(["ashton-price", "brokery", "pilot-smokeball", "smd-staging", "no-such-seat"]) == {
        "pilot-smokeball",
        "smd-staging",
    }
