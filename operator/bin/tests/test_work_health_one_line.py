"""The hourly triage lists only real, open problems, once each (2026-11-16).

Each test here is one fix the ops triage agent wrote against work-health
between 2026-11-03 and 2026-11-12, as a fixture where the old code listed a
line it should not have (or missed one) beside the nearest fixture where the
line is still right. Every fixture is invented: the seat, the jobs, the people
and the times name nothing real.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bin.lib import work_health as wh
from bin.lib import work_health_boot as boot
from bin.lib import work_health_mail as mail
from bin.lib import work_health_proof as proof_wh
from bin.lib import work_health_sources as src

_BIN = Path(__file__).resolve().parents[1]


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _BIN / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cli = _load("work_health_cli", "work-health.py")
rec = _load("reconcile_outcomes", "reconcile-outcomes.py")

UTC = timezone.utc
NOW = datetime(2026, 11, 16, 18, 0, tzinfo=UTC)
CFG = wh.load_config(seats={})
SEAT = "example-firm"
JOB = "01KZZZZZZZZZZZZZZZZZZZZZZZ"[:16]  # not a ULID: start times come from the rows
PERSON = "requester@example-firm.test"


def _row(action: str, at: datetime, skill: str | None = None, row_id: str = "", **md) -> dict:
    return {
        "id": row_id or f"{action}-{at.isoformat()}-{len(md)}",
        "ts": at.isoformat().replace("+00:00", "Z"),
        "action_type": action,
        "skill_name": skill,
        "metadata": json.dumps(md),
    }


def _job(lane: str, state: str, at: datetime, job_id: str = JOB, **md) -> dict:
    return _row(f"{lane}_JOB_{state}", at, job_id=job_id, **md)


def _proof(at: datetime, code: str, job_id: str = JOB, lane: str = "drafting") -> dict:
    return _row("DELIVERY_PROOF_FAILED", at, job_id=job_id, lane=lane, code=code, detail="x")


def _kinds(items) -> list[str]:
    return sorted(i.kind for i in items)


# ---------------------------------------------------------------------------
# A: a seat restarting during the check is read again, not listed
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


class _FlakyClient:
    """Fails its first ``fails`` reads with a connection error, then reads."""

    def __init__(self, fails: int) -> None:
        self.fails = fails

    def read_page(self, kind, cursor=None, table=None):
        if self.fails > 0:
            self.fails -= 1
            raise urllib.error.URLError("connection refused")
        return {"entries": [_row("REPLY_SENT", NOW - timedelta(hours=1))], "cursor": None}


class _Watch:
    def __init__(self, answers: list, state: boot.MachineState) -> None:
        self.answers = list(answers)
        self.state = state

    def passing(self):
        return self.answers.pop(0) if self.answers else self.state.passing


def _read(monkeypatch, client, watch, clock):
    monkeypatch.setattr(cli.seam_pull, "seam_client_from_env", lambda slug: client)
    return cli._seat_ledger(SEAT, CFG, NOW, sleep=clock.sleep, clock=clock, watch=watch)


def test_a_restarting_seat_is_read_again_once_its_health_check_passes(monkeypatch):
    """FALSIFIER: the old read listed the seat unreadable on the first
    connection error (six triage runs for nothing, 2026-10-28 to 11-07)."""
    clock = _Clock()
    rows, unreadable = _read(monkeypatch, _FlakyClient(1), _Watch([False, False, True], boot.MachineState(True)), clock)
    assert unreadable is None and rows
    assert sum(clock.slept) == 2 * boot.POLL_SECONDS  # it read as soon as the check passed


def test_a_seat_still_down_after_seven_minutes_is_unreadable_and_says_how_long(monkeypatch):
    clock = _Clock()
    down = NOW - timedelta(minutes=12)
    rows, unreadable = _read(monkeypatch, _FlakyClient(99), _Watch([], boot.MachineState(False, down)), clock)
    assert rows is None and unreadable is not None and unreadable.kind == "unreadable"
    assert sum(clock.slept) == boot.BOOT_WAIT.total_seconds()
    assert "has been down 19m" in unreadable.what and "URLError" in unreadable.what


def test_a_read_refused_for_a_reason_a_wait_cannot_fix_is_not_waited_for(monkeypatch):
    class _Malformed:
        def read_page(self, kind, cursor=None, table=None):
            raise ValueError("seam read audit_export: malformed page shape")

    clock = _Clock()
    rows, unreadable = _read(monkeypatch, _Malformed(), _Watch([], boot.MachineState(None)), clock)
    assert rows is None and unreadable is not None and clock.slept == []


def test_the_machine_list_reads_passing_and_down_since():
    stopped_ms = int((NOW - timedelta(minutes=4)).timestamp() * 1000)
    booting = [
        {
            "state": "started",
            "checks": [{"name": "http", "status": "critical"}],
            "events": [{"type": "exit", "status": "stopped", "timestamp": stopped_ms}],
        }
    ]
    assert boot.machine_state(booting) == boot.MachineState(False, NOW - timedelta(minutes=4))
    up = [{"state": "started", "checks": [{"status": "passing"}]}]
    assert boot.machine_state(up).passing is True
    assert boot.machine_state(None).passing is None


# ---------------------------------------------------------------------------
# B, K: a reply that reached the requester closes the "not seen" proof lines
# ---------------------------------------------------------------------------


def _found(at: datetime, job_id: str = JOB) -> dict:
    return _row(
        "CONFIRM_SEND_DISPATCHED",
        at,
        outcome="sent",
        lookup="ok",
        reply_binding=f"drafting_job:{job_id}:1:delivered",
    )


def test_a_lost_wake_closes_when_the_jobs_reply_is_found_in_sent_items():
    """FALSIFIER: only reply_unproven closed on a found reply, so a wake_lost
    stayed 72 hours after the bound reply was in Sent Items five minutes later."""
    lost = _proof(NOW - timedelta(hours=2), "wake_lost")
    assert _kinds(proof_wh.delivery_proof_items(SEAT, [lost], CFG, NOW)) == ["job_failed"]
    assert (
        proof_wh.delivery_proof_items(SEAT, [lost, _found(NOW - timedelta(hours=2) + timedelta(minutes=5))], CFG, NOW)
        == []
    )
    earlier = _found(NOW - timedelta(hours=3))
    assert _kinds(proof_wh.delivery_proof_items(SEAT, [lost, earlier], CFG, NOW)) == ["job_failed"]


def _held_for_review(at: datetime, session: str = "s-1", to: str = PERSON) -> dict:
    return _row(
        "REPLY_HELD",
        at,
        reason="matter_files_review",
        recipient=to,
        message_id="m-1",
        review_id="rv-1",
        wake_kind="drafting",
        wake_job_id=JOB,
        session_id=session,
    )


def _released(at: datetime, session: str = "s-1", to: str = PERSON) -> dict:
    return _row("REPLY_SENT", at, recipient=to, in_reply_to="m-1", released_from_review="rv-1", session_id=session)


def test_a_reply_released_from_review_is_the_reply_of_the_job_its_hold_named():
    """FALSIFIER: the release's REPLY_SENT names no job, so reply_unproven stayed
    listed 72 hours after the requester had the reply (triage, 2026-11-05)."""
    rows = [_held_for_review(NOW - timedelta(hours=3)), _proof(NOW - timedelta(hours=2, minutes=40), "reply_unproven")]
    assert _kinds(proof_wh.delivery_proof_items(SEAT, rows, CFG, NOW)) == ["job_failed"]
    assert proof_wh.delivery_proof_items(SEAT, [*rows, _released(NOW - timedelta(hours=1))], CFG, NOW) == []
    for other in (
        _released(NOW - timedelta(hours=1), session="s-2"),
        _released(NOW - timedelta(hours=1), to="x@y.test"),
    ):
        assert _kinds(proof_wh.delivery_proof_items(SEAT, [*rows, other], CFG, NOW)) == ["job_failed"]


# ---------------------------------------------------------------------------
# C: one job, one line, naming the real cause
# ---------------------------------------------------------------------------


def test_a_proof_notice_written_with_the_jobs_failure_is_one_line():
    """FALSIFIER: the retention cleanup writes FAILED, then the notice a moment
    later; the proof line dropped only when FAILED came after it."""
    failed = _job("DRAFTING", "FAILED", NOW - timedelta(hours=1), reason="review_held: wiped")
    notice = _proof(NOW - timedelta(hours=1) + timedelta(seconds=2), "review_held")
    assert proof_wh.delivery_proof_items(SEAT, [failed, notice], CFG, NOW) == []
    long_after = _proof(NOW - timedelta(minutes=30), "review_held")
    assert _kinds(proof_wh.delivery_proof_items(SEAT, [failed, long_after], CFG, NOW)) == ["job_failed"]


def test_the_heartbeats_copy_of_a_proof_failure_is_not_a_call_failure_line():
    """FALSIFIER: ``proof:<code>`` shortfalls became "A drafting job call
    failed", a second line for the job its proof line already names."""
    proof = {
        "ts": (NOW - timedelta(hours=1)).isoformat(),
        "class": "failed",
        "tool": "drafting_job",
        "code": "proof:reply_unproven",
    }
    items, _ = src.shortfall_items(SEAT, [proof], CFG, NOW, [])
    assert items == []
    real = {**proof, "code": "send_error"}
    assert _kinds(src.shortfall_items(SEAT, [real], CFG, NOW, [])[0]) == ["shortfall"]


def _rejected_job_rows() -> list[dict]:
    return [
        _held_for_review(NOW - timedelta(hours=6)),
        _proof(NOW - timedelta(hours=5, minutes=40), "reply_unproven"),
        _row(
            "REPLY_HELD",
            NOW - timedelta(hours=5),
            reason="matter_files_review_rejected",
            review_id="rv-1",
            message_id="m-1",
            session_id="s-1",
        ),
    ]


def test_smd_rejecting_a_jobs_reply_is_one_line_that_says_so():
    """FALSIFIER: one rejected reply was three lines (proof, silent run, call
    failure), and the two that were read pointed away from the cause."""
    rows = _rejected_job_rows()
    proof = proof_wh.delivery_proof_items(SEAT, rows, CFG, NOW)
    assert _kinds(proof) == ["job_failed"] and "SMD rejected its reply in review" in proof[0].what
    assert proof[0].job == JOB
    folded, _ = src.silent_run_items(SEAT, rows, CFG, NOW, frozenset({JOB}))
    assert folded == []
    # Without the job's line, the holds still stand on their own.
    alone, _ = src.silent_run_items(SEAT, rows, CFG, NOW)
    assert _kinds(alone) == ["silent_run"]


# ---------------------------------------------------------------------------
# D: a job queued behind a lane it waits for is waiting, not stuck
# ---------------------------------------------------------------------------


def test_a_negotiation_job_queued_behind_a_running_demand_job_is_not_stuck():
    """FALSIFIER: the 15 minute queue limit listed it stuck while the runner
    was correctly holding it behind the demand child (triage, 2026-11-04)."""
    queued = _job("NEGOTIATION", "SUBMITTED", NOW - timedelta(minutes=40), job_id="NEG1")
    demand = [
        _job("DEMAND", "SUBMITTED", NOW - timedelta(minutes=50), job_id="DEM1"),
        _job("DEMAND", "RUNNING", NOW - timedelta(minutes=49), job_id="DEM1"),
    ]
    assert wh.job_items(SEAT, [queued, *demand], CFG, NOW) == []
    assert _kinds(wh.job_items(SEAT, [queued], CFG, NOW)) == ["job_stuck"]
    # The blocker past its own limit is caught, and the queued job is stuck too.
    hung = [
        _job("DEMAND", "SUBMITTED", NOW - timedelta(hours=8), job_id="DEM1"),
        _job("DEMAND", "RUNNING", NOW - timedelta(hours=7), job_id="DEM1"),
    ]
    assert _kinds(wh.job_items(SEAT, [queued, *hung], CFG, NOW)) == ["job_stuck", "job_stuck"]
    # Once the blocker ends, the queue limit counts from its end.
    ended = [*demand, _job("DEMAND", "DELIVERED", NOW - timedelta(minutes=5), job_id="DEM1")]
    assert wh.job_items(SEAT, [queued, *ended], CFG, NOW) == []
    ended_long_ago = [*demand[:2], _job("DEMAND", "DELIVERED", NOW - timedelta(minutes=20), job_id="DEM1")]
    assert _kinds(wh.job_items(SEAT, [queued, *ended_long_ago], CFG, NOW)) == ["job_stuck"]


def test_a_chronology_queued_behind_a_running_chronology_is_not_stuck():
    """FALSIFIER: one request queued two chronologies; the second waited behind
    the first in its own lane (one job at a time) and the 30 minute queue limit
    listed it stuck while the first was well inside its run limit (2026-11-17)."""
    first = [
        _job("MEDCHRON", "SUBMITTED", NOW - timedelta(minutes=80), job_id="MED1"),
        _job("MEDCHRON", "RUNNING", NOW - timedelta(minutes=79), job_id="MED1"),
    ]
    second = _job("MEDCHRON", "SUBMITTED", NOW - timedelta(minutes=80), job_id="MED2")
    assert wh.job_items(SEAT, [*first, second], CFG, NOW) == []
    # The first past its own run limit is caught, and the second is stuck too.
    hung = [
        _job("MEDCHRON", "SUBMITTED", NOW - timedelta(hours=14), job_id="MED1"),
        _job("MEDCHRON", "RUNNING", NOW - timedelta(hours=13), job_id="MED1"),
    ]
    old_second = _job("MEDCHRON", "SUBMITTED", NOW - timedelta(hours=14), job_id="MED2")
    assert _kinds(wh.job_items(SEAT, [*hung, old_second], CFG, NOW)) == ["job_stuck", "job_stuck"]
    # Once the first ends (here held for review), the queue limit counts from its end.
    held_now = [*first, _job("MEDCHRON", "HELD", NOW - timedelta(minutes=5), job_id="MED1")]
    assert [i for i in wh.job_items(SEAT, [*held_now, second], CFG, NOW) if i.job == "MED2"] == []
    held_long_ago = [*first, _job("MEDCHRON", "HELD", NOW - timedelta(minutes=45), job_id="MED1")]
    late = [i for i in wh.job_items(SEAT, [*held_long_ago, second], CFG, NOW) if i.job == "MED2"]
    assert _kinds(late) == ["job_stuck"]
    # A sibling that ran and ended before this job was queued does not excuse it.
    before = [
        _job("MEDCHRON", "SUBMITTED", NOW - timedelta(hours=3), job_id="MED0"),
        _job("MEDCHRON", "RUNNING", NOW - timedelta(hours=3), job_id="MED0"),
        _job("MEDCHRON", "DELIVERED", NOW - timedelta(hours=2), job_id="MED0"),
    ]
    assert _kinds(wh.job_items(SEAT, [*before, second], CFG, NOW)) == ["job_stuck"]


def test_a_lane_that_does_not_wait_for_another_is_still_held_to_its_queue_limit():
    queued = _job("DRAFTING", "SUBMITTED", NOW - timedelta(minutes=40), job_id="DRF1")
    demand = [_job("DEMAND", "RUNNING", NOW - timedelta(minutes=30), job_id="DEM1")]
    assert _kinds(wh.job_items(SEAT, [queued, *demand], CFG, NOW)) == ["job_stuck"]


def test_records_jobs_are_read_and_a_review_wait_is_not_stuck():
    """FALSIFIER: _JOB_TYPE did not read RECORDS_JOB_* rows at all."""
    running = [
        _job("RECORDS", "SUBMITTED", NOW - timedelta(hours=7), job_id="REC1"),
        _job("RECORDS", "RUNNING", NOW - timedelta(hours=7), job_id="REC1"),
    ]
    items = wh.job_items(SEAT, running, CFG, NOW)
    assert _kinds(items) == ["job_stuck"] and "matter records" in items[0].what
    in_review = [*running, _job("RECORDS", "REVIEW", NOW - timedelta(hours=6), job_id="REC1")]
    assert wh.job_items(SEAT, in_review, CFG, NOW) == []


# ---------------------------------------------------------------------------
# I: an unreadable seat lists the read, not its ungraded refusals
# ---------------------------------------------------------------------------


class _Malformed:
    def read_page(self, kind, cursor=None, table=None):
        raise ValueError("malformed page shape")


def test_an_unreadable_seat_does_not_list_its_refusals(monkeypatch):
    """FALSIFIER: shortfall_items(..., None) listed refusals that later work had
    answered, and they came back as NEW (triage, 2026-11-05)."""
    monkeypatch.setattr(cli.seam_pull, "seam_client_from_env", lambda slug: _Malformed())
    monkeypatch.setattr(cli, "graph_check", lambda slug: (None, None, None))
    console = cli.Console(cli._NoD1())
    console.shortfalls[SEAT] = [
        {"ts": (NOW - timedelta(hours=2)).isoformat(), "class": "failed", "tool": "mcp_smokeball_get_file", "code": "x"}
    ]
    items, _gaps, _resolved = cli.evaluate_seat(SEAT, CFG, console, NOW, None)
    assert "unreadable" in _kinds(items) and "shortfall" not in _kinds(items)


# ---------------------------------------------------------------------------
# J, O: what was reported is remembered across a blind run; NEW means new
# ---------------------------------------------------------------------------


def _decide(items, previous, now=NOW):
    return mail.decide(
        items=items,
        seats=[SEAT],
        gaps={},
        previous=previous,
        since=now - timedelta(hours=1),
        now=now,
        all_clear_hour_utc=15,
    )


def test_one_unreadable_run_does_not_make_everything_new_again():
    """FALSIFIER: the state was written from what the run saw, so a blind run
    erased the seat's entries and the next run sent 5 of 8 lines as NEW."""
    held = wh.item(SEAT, "job_held", "drafting:J1", NOW - timedelta(hours=5), "held", "decide")
    first = _decide([held], None)
    blind = wh.item(SEAT, "unreadable", "ledger", NOW + timedelta(hours=1), "could not read", "check")
    second = _decide([blind], first.state, NOW + timedelta(hours=1))
    third = _decide([held], second.state, NOW + timedelta(hours=2))
    assert third.new == [] and third.still_open == [held]


def test_a_problem_fixed_on_a_read_seat_is_new_when_it_returns():
    held = wh.item(SEAT, "job_held", "drafting:J1", NOW - timedelta(hours=5), "held", "decide")
    first = _decide([held], None)
    clean = _decide([], first.state, NOW + timedelta(hours=1))
    back = _decide([held], clean.state, NOW + timedelta(hours=2))
    assert back.new == [held]


def test_an_entry_is_carried_for_72_hours_at_most():
    held = wh.item(SEAT, "job_held", "drafting:J1", NOW - timedelta(hours=5), "held", "decide")
    state = _decide([held], None).state
    blind = wh.item(SEAT, "unreadable", "ledger", NOW, "could not read", "check")
    for hours in (24, 48, 73):
        state = _decide([blind], state, NOW + timedelta(hours=hours)).state
    assert _decide([held], state, NOW + timedelta(hours=74)).new == [held]


def test_a_version_one_state_file_is_still_read():
    held = wh.item(SEAT, "job_held", "drafting:J1", NOW - timedelta(hours=5), "held", "decide")
    legacy = {mail.state_key(held.key): (NOW - timedelta(hours=5)).isoformat()}
    plan = _decide([held], legacy)
    assert plan.new == [] and plan.state["version"] == mail.STATE_VERSION


def _shortfalls(*ages_h: float) -> list[dict]:
    return [
        {"ts": (NOW - timedelta(hours=h)).isoformat(), "class": "failed", "tool": "mcp_smokeball_get_file", "code": "x"}
        for h in ages_h
    ]


def test_a_call_failure_line_is_new_again_only_for_a_newer_hit():
    """FALSIFIER: the key held the earliest hit's time, so a late row a few
    milliseconds older (or the oldest hit ageing out) re-announced the line."""
    first, _ = src.shortfall_items(SEAT, _shortfalls(5, 3), CFG, NOW, [])
    older_row, _ = src.shortfall_items(SEAT, _shortfalls(5.0001, 5, 3), CFG, NOW, [])
    aged, _ = src.shortfall_items(SEAT, _shortfalls(3), CFG, NOW, [])
    assert first[0].key == older_row[0].key == aged[0].key
    state = _decide(first, None).state
    assert _decide(older_row, state).new == []
    newer, _ = src.shortfall_items(SEAT, _shortfalls(5, 3, 0.5), CFG, NOW, [])
    assert _decide(newer, state).new == newer


# ---------------------------------------------------------------------------
# L, M: decided work is not open work, and an old job's start is not today
# ---------------------------------------------------------------------------


def test_a_job_smd_stopped_on_purpose_is_not_open_work():
    rows = [
        _job("DRAFTING", "SUBMITTED", NOW - timedelta(hours=2)),
        _job("DRAFTING", "FAILED", NOW - timedelta(hours=1), reason="stopped_by_smd: rehearsal over"),
    ]
    assert wh.job_items(SEAT, rows, CFG, NOW) == []


def test_a_closed_jobs_proof_failure_is_not_listed():
    proof = _proof(NOW - timedelta(hours=1), "review_held")
    assert _kinds(proof_wh.delivery_proof_items(SEAT, [proof], CFG, NOW)) == ["job_failed"]
    assert proof_wh.delivery_proof_items(SEAT, [proof], CFG, NOW, {(SEAT, JOB)}) == []


def test_an_old_job_closed_today_is_not_said_to_have_started_today():
    """FALSIFIER: "started Nov 6" for jobs created in August: the window's
    first row of an old job is its close."""
    closed_today = [_job("DRAFTING", "FAILED", NOW - timedelta(hours=1), reason="review_held: x")]
    (line,) = wh.job_items(SEAT, closed_today, CFG, NOW)
    assert "ended" in line.what and "started" not in line.what
    ulid = "01K4" + "0" * 22  # a ULID minted in 2025
    (line,) = wh.job_items(SEAT, [_job("DRAFTING", "FAILED", NOW - timedelta(hours=1), job_id=ulid)], CFG, NOW)
    assert f"started {wh.hhmm(wh.ulid_time(ulid))}" in line.what and wh.ulid_time(ulid).year == 2025


# ---------------------------------------------------------------------------
# Z: one mail-connection blip, one line
# ---------------------------------------------------------------------------


def _status(failures: int) -> dict:
    return {"connectors_json": json.dumps({"msgraph": {"consecutive_failures": failures, "last_ok_age_seconds": 60}})}


def test_the_failure_count_and_its_open_alert_are_one_line():
    """FALSIFIER: the count line and the connector_down alert were two NEW lines."""
    counted = src.connector_items(SEAT, _status(3), NOW)
    alert = src.alert_items(
        SEAT, [{"condition": "connector_down:msgraph", "opened_at": (NOW - timedelta(minutes=20)).isoformat()}], NOW
    )
    (line,) = src.fold_connector_lines(counted + alert)
    assert line.page is not None and "alert has been open since" in line.what
    other = src.alert_items(SEAT, [{"condition": "connector_down:smokeball", "opened_at": NOW.isoformat()}], NOW)
    assert len(src.fold_connector_lines(counted + other)) == 2


def test_a_vendor_5xx_while_the_seat_connection_is_healthy_is_one_blip_never_a_page():
    error = "msgraph GET failed: HTTP 503"
    (blip,) = src.graph_probe_item(SEAT, "ops@example-firm.test", error, NOW, src.seat_mail_connection_ok(_status(0)))
    assert blip.page is None and "the vendor, not a stale secret" in blip.what
    (failing,) = src.graph_probe_item(
        SEAT, "ops@example-firm.test", error, NOW, src.seat_mail_connection_ok(_status(3))
    )
    assert failing.page is not None
    (refused,) = src.graph_probe_item(
        SEAT, "ops@example-firm.test", "msgraph token mint rejected with HTTP 401", NOW, True
    )
    assert refused.page is not None


# ---------------------------------------------------------------------------
# AB: a review run that owed nobody anything and said [SILENT] is finished
# ---------------------------------------------------------------------------


def _review_run(note: bool, response: str) -> list[dict]:
    wake = _row("EMITTED_WAKE", NOW - timedelta(hours=3), skill="operator-help", decision_basis="review_due", note=note)
    turn = _row("LLM_TURN_COMPLETED", NOW - timedelta(hours=3) + timedelta(minutes=2), skill="operator-help")
    turn["output_digest"] = hashlib.sha256(response.encode("utf-8")).hexdigest()
    return [wake, turn]


def test_a_review_run_that_owed_nothing_and_said_silent_is_not_a_silent_run():
    """FALSIFIER: every weekday review run of operator-help (EMITTED_WAKE
    review_due, note false, answer [SILENT]) was a silent-run line."""
    contract = rec.load_contract()
    (ob,) = rec.analyze(contract, SEAT, _review_run(False, "[SILENT]"), now=NOW, roster={})
    assert ob.closed_by == "nothing_owed" and not ob.is_finding
    for note, response in ((True, "[SILENT]"), (False, "Promoted tip 3.")):
        (ob,) = rec.analyze(contract, SEAT, _review_run(note, response), now=NOW, roster={})
        assert ob.is_finding, (note, response)
