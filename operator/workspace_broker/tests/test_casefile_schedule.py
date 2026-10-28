"""The Case File Manager's standing schedule (casefile_schedule.py and the
scheduled half of casefile_job_submit, casefile_scheduled.py), against the
real broker and ledger.

Every branch the weekday cron can take: a weekday re-check, the month's first
business day full read (holidays and the firm's zone included), a weekend or
holiday that queues nothing, a second fire the same day that queues nothing,
a caseload deferred behind a running job and then missed at the cutoff (paged
once), an unauthored caseload or cron row, the Smokeball token gate (under
seven days, and an unknown age: refused, paged once per episode), the budget
refusal paged once, the apply's token gate, and a reply to an older scheduled
list binding to that list (or refused as replaced once it is old). Each test
fails if the line it defends is removed. All names are synthetic.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import bound_replies, casefile_schedule, casework_ledger
from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.casefile_casework import raise_lines
from workspace_broker.casefile_store import CasefileLedger
from workspace_broker.casefile_verbs import CasefileVerbs
from workspace_broker.recipient_policy import sender_key
from workspace_broker.reply_scheduled import casefile_subject, scheduled_recipient_ok
from workspace_broker.server import Broker

GATEWAY_PID = 4242
AGENT_UID = 10000
LIBRARY = "1dad2f6b-7c5b-4cee-a06d-aab9e1e91a23"
MATTER = "11111111-1111-4111-8111-111111111111"
TASK = "e1e1e1e1-e1e1-4e1e-8e1e-e1e1e1e1e1e1"
ADMIN = "admin@firm.example"
DAY = 86400
FRESH = 3 * DAY  # a token three days old: 27 of 30 days left

YAML = """
scope:
  admins:
    - Admin@Firm.example
personas:
  - slug: operator
    skills:
      - name: case-file-manager
        initiation: {{manual: true, scheduled: {scheduled}, webhook: false}}
        enabled: true
        settings:
          folder_name: 'Case File Manager'
          file_to_matter_id: '{library}'
          scheduled_recipients: 'admin@firm.example'
{caseloads}
    cron:
{cron}
self_initiation:
  document_library:
    operator_matter:
      number: 'OPS-LIBRARY'
"""
CRON = """      - skill: case-file-manager
        schedule: '17 6-14 * * 1-5'
        pre_run: pre_run.py
        wake_policy: pre_run_decides"""
CASELOADS = "          scheduled_caseloads: 'Pat Example; Sam Sample'"
FIRM = "monthly_budget_usd: 200\nper_job_cap_usd: 50\ntimezone: America/Los_Angeles\nholidays: [2026-12-01]\n"


def write_yaml(path: Path, *, scheduled: str = "true", caseloads: str = CASELOADS, cron: str = CRON) -> None:
    path.write_text(YAML.format(scheduled=scheduled, library=LIBRARY, caseloads=caseloads, cron=cron or "      []"))


@pytest.fixture
def seat(tmp_path, monkeypatch):
    db = str(tmp_path / "audit.db")
    writer = LedgerWriter(db)
    yaml_path = tmp_path / "customer.yaml"
    write_yaml(yaml_path)
    firm = tmp_path / "casefile-broker-firm.yaml"
    firm.write_text(FIRM)
    monkeypatch.setenv("SMD_CASEFILE_FIRM_CONFIG", str(firm))
    monkeypatch.setenv(casework_ledger.LEDGER_PATH_ENV, str(tmp_path / "casework.jsonl"))
    queue = tmp_path / "casefile-queue"
    broker = Broker.__new__(Broker)
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.ledger = writer
    broker.audit_db_path = db
    broker.customer_path = yaml_path
    broker.escalation_ledger_path = None
    broker._escalation_lock = threading.Lock()
    broker.casefile = CasefileVerbs(
        CasefileLedger(db, queue), customer_yaml=str(yaml_path), audit_append=writer.append, broker=broker
    )
    return {"broker": broker, "db": db, "yaml": yaml_path, "firm": firm, "queue": queue,
            "casework": tmp_path / "casework.jsonl"}  # fmt: skip


def at(seat, iso: str) -> None:
    seat["broker"].casefile._now = lambda: datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)


def fire(seat, age=FRESH, *, pid: int = 999, uid: int = AGENT_UID) -> dict:
    """One cron fire, as the pre_run makes it (agent uid, off the gateway)."""
    env = {"trigger": "scheduled", "smokeball_token_age_seconds": age}
    return seat["broker"].handle({"action": "casefile_job_submit", "envelope": env}, peer_pid=pid, peer_uid=uid)


def statuses(got: dict) -> list[str]:
    return [r["status"] for r in got.get("results") or []]


def pages(db: str) -> list[dict]:
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT metadata FROM audit_log WHERE action_type='DELIVERY_PROOF_FAILED' ORDER BY rowid")
        return [json.loads(r[0]) for r in rows]
    finally:
        conn.close()


def queued_env(seat, job_id: str) -> dict:
    return json.loads((seat["queue"] / f"{job_id}.json").read_text())


def record(seat, job: str, state: str, **fields) -> None:
    seat["broker"].handle(
        {"action": "casefile_job_record", "job_id": job, "state": state, "fields": fields}, peer_pid=1, peer_uid=0
    )


# ---- the calendar ------------------------------------------------------------------------
def test_a_weekday_fire_queues_one_recheck_per_authored_caseload(seat) -> None:
    at(seat, "2026-11-03T16:00:00")  # Tuesday 08:00 Pacific
    got = fire(seat)
    assert got["accepted"] is True and got["mode"] == "recheck" and got["date"] == "2026-11-03"
    assert statuses(got) == ["queued", "queued"]
    env = queued_env(seat, got["results"][0]["job_id"])
    assert env["trigger"] == "scheduled" and env["mode"] == "recheck"
    assert env["scope"] == {"assisting": {"names": ["Pat Example"]}}
    assert env["requester"] == ADMIN
    assert env["message_ref"] == casefile_schedule.scheduled_ref(datetime(2026, 11, 3).date(), "Pat Example")


def test_the_first_business_day_of_the_month_is_a_full_read(seat) -> None:
    at(seat, "2026-11-02T16:00:00")  # Monday 2 November: the 1st is a Sunday
    got = fire(seat)
    assert got["mode"] == "full" and statuses(got) == ["queued", "queued"]
    assert queued_env(seat, got["results"][0]["job_id"])["mode"] == "full"


def test_a_firm_holiday_moves_the_full_read_to_the_next_business_day(seat) -> None:
    at(seat, "2026-12-01T17:00:00")  # Tuesday 1 December, on the firm's holiday list
    got = fire(seat)
    assert got["accepted"] is False and got["status"] == "not_business_day"
    at(seat, "2026-12-02T17:00:00")
    assert fire(seat)["mode"] == "full"
    at(seat, "2026-12-03T17:00:00")
    assert fire(seat)["mode"] == "recheck"


def test_a_weekend_fire_queues_nothing(seat) -> None:
    at(seat, "2026-10-31T16:00:00")  # Saturday
    got = fire(seat)
    assert got["accepted"] is False and got["status"] == "not_business_day"
    assert not seat["queue"].exists() or not list(seat["queue"].glob("*.json"))


def test_the_day_is_the_firms_not_utcs(seat) -> None:
    # 02:30 UTC Monday is 18:30 Sunday in the firm's zone: nothing.
    at(seat, "2026-11-02T02:30:00")
    assert fire(seat)["status"] == "not_business_day"
    # 01:30 UTC Saturday is 17:30 Friday there: a business day (past the cutoff).
    at(seat, "2026-11-07T01:30:00")
    got = fire(seat)
    assert got["date"] == "2026-11-06" and got["mode"] == "recheck"


def test_a_second_fire_the_same_day_queues_nothing_even_after_a_failure(seat) -> None:
    at(seat, "2026-11-03T16:00:00")
    first = fire(seat)
    job = first["results"][0]["job_id"]
    record(seat, job, "running")
    record(seat, job, "failed", reason="unexpected: x")
    at(seat, "2026-11-03T17:00:00")
    again = fire(seat)
    assert statuses(again) == ["already_queued_today", "already_queued_today"]
    assert again["results"][0]["job_id"] == job


# ---- deferral and the cutoff -------------------------------------------------------------
def test_a_caseload_behind_a_running_job_is_deferred_then_missed_and_paged_once(seat) -> None:
    at(seat, "2026-11-03T16:00:00")
    monday_jobs = fire(seat)["results"]
    record(seat, monday_jobs[0]["job_id"], "running")  # still running into Wednesday
    record(seat, monday_jobs[1]["job_id"], "running")
    record(seat, monday_jobs[1]["job_id"], "delivered", files=[])
    at(seat, "2026-11-04T16:00:00")  # Wednesday 08:00
    got = fire(seat)
    assert statuses(got) == ["deferred", "queued"]
    assert not pages(seat["db"])
    at(seat, "2026-11-04T22:30:00")  # 14:30, past the 14:00 cutoff
    late = fire(seat)
    assert statuses(late) == ["missed", "already_queued_today"]
    assert late["results"][0]["paged"] is True
    assert [p["detail"].split(":")[0] for p in pages(seat["db"])] == ["casefile_schedule_missed"]
    fire(seat)  # another fire after the cutoff: no second page
    assert len(pages(seat["db"])) == 1


def test_a_deferred_caseload_queues_on_the_next_fire_once_the_job_ends(seat) -> None:
    at(seat, "2026-11-03T16:00:00")
    jobs = fire(seat)["results"]
    record(seat, jobs[0]["job_id"], "running")
    at(seat, "2026-11-04T16:00:00")
    assert statuses(fire(seat))[0] == "deferred"
    record(seat, jobs[0]["job_id"], "delivered", files=[])
    at(seat, "2026-11-04T17:00:00")
    assert statuses(fire(seat))[0] == "queued"


# ---- what must be authored ---------------------------------------------------------------
def test_an_unauthored_caseload_queues_nothing(seat) -> None:
    write_yaml(seat["yaml"], caseloads="")
    at(seat, "2026-11-03T16:00:00")
    got = fire(seat)
    assert got["accepted"] is False and "no caseload is authored" in got["reason"]


def test_no_cron_row_means_the_agent_uid_cannot_start_a_schedule(seat) -> None:
    write_yaml(seat["yaml"], cron="")
    at(seat, "2026-11-03T16:00:00")
    assert "schedule is not switched on" in fire(seat)["reason"]
    write_yaml(seat["yaml"], scheduled="false")
    assert "not switched on on a schedule" in fire(seat)["reason"]


def test_a_turn_cannot_submit_a_scheduled_run(seat) -> None:
    at(seat, "2026-11-03T16:00:00")
    with pytest.raises(PermissionError):
        fire(seat, pid=GATEWAY_PID)


def test_the_envelope_is_exactly_the_scheduled_form(seat) -> None:
    at(seat, "2026-11-03T16:00:00")
    env = {"trigger": "scheduled", "smokeball_token_age_seconds": FRESH, "mode": "full"}
    got = seat["broker"].handle({"action": "casefile_job_submit", "envelope": env}, peer_pid=999, peer_uid=AGENT_UID)
    assert got["accepted"] is False


# ---- the Smokeball connection gate -------------------------------------------------------
def test_under_seven_days_left_refuses_and_pages_once_per_episode(seat) -> None:
    at(seat, "2026-11-03T16:00:00")
    got = fire(seat, age=24 * DAY)  # 6 of 30 days left
    assert got["accepted"] is False and got["status"] == "token_gate"
    assert "needs renewing" in got["reason"]
    assert not seat["queue"].exists() or not list(seat["queue"].glob("*.json"))
    at(seat, "2026-11-04T16:00:00")
    assert fire(seat, age=25 * DAY)["status"] == "token_gate"  # the same token, a day older
    assert [p["detail"].split(":")[0] for p in pages(seat["db"])] == ["casefile_schedule_token"]
    assert fire(seat, age=FRESH)["accepted"] is True  # re-consented: the episode ends
    at(seat, "2026-11-30T16:00:00")
    fire(seat, age=24 * DAY)  # a new token's own episode pages again
    assert len(pages(seat["db"])) == 2


def test_an_unknown_token_age_refuses_and_pages(seat) -> None:
    at(seat, "2026-11-03T16:00:00")
    got = fire(seat, age=None)
    assert got["status"] == "token_gate" and "not known" in got["reason"]
    assert len(pages(seat["db"])) == 1


def test_a_seat_with_a_proven_longer_lifetime_says_so_in_its_settings(seat) -> None:
    text = (
        seat["yaml"]
        .read_text()
        .replace(
            "scheduled_recipients: 'admin@firm.example'",
            "scheduled_recipients: 'admin@firm.example'\n          smokeball_token_lifetime_days: '180'",
        )
    )
    seat["yaml"].write_text(text)
    at(seat, "2026-11-03T16:00:00")
    assert fire(seat, age=60 * DAY)["accepted"] is True


def test_a_spent_budget_refuses_and_pages_once_a_day(seat) -> None:
    seat["firm"].write_text(FIRM.replace("monthly_budget_usd: 200", "monthly_budget_usd: 0.01"))
    at(seat, "2026-11-03T16:00:00")
    first = fire(seat)
    job = first["results"][0]["job_id"]
    record(seat, job, "running")
    record(seat, job, "delivered", cents=500, files=[])
    conn = sqlite3.connect(seat["db"])  # the spend lands in the test's month, not the wall clock's
    conn.execute("UPDATE casefile_spend SET at='2026-11-03T17:00:00Z'")
    conn.commit()
    conn.close()
    at(seat, "2026-11-04T16:00:00")
    got = fire(seat)
    assert got["accepted"] is False and "budget" in got["reason"] and got["paged"] is True
    at(seat, "2026-11-04T17:00:00")
    fire(seat)
    assert [p["detail"].split(":")[0] for p in pages(seat["db"])] == ["casefile_schedule_refused"]


# ---- delivery: one new email per list, and replies bind to their own list ------------------------
SHA = "c" * 64


def _delivered_scheduled(seat, iso: str, thread: str) -> str:
    at(seat, iso)
    job = fire(seat)["results"][0]["job_id"]
    record(seat, job, "running")
    lines = [{"n": 1, "kind": "task", "matter_id": MATTER, "source_id": TASK + iso[8:10], "sha256": SHA}]
    record(seat, job, "delivered", cents=10, lines=lines, lines_proposed=1, files=[])
    key = f"casefile_job:{job}:1:delivered"
    bound_replies.claim(seat["db"], key, "", "", "sess")
    bound_replies.settle(seat["db"], key, "sent")
    assert raise_lines(seat["broker"], job, thread, "sess", key) == 1
    return job


def _approve(seat, thread: str) -> None:
    rows = casework_ledger.read_ledger(str(seat["casework"]))
    raise_row = next(r for r in rows if r["event"] == "proposed" and r["thread_ref"] == thread)
    ev = {k: raise_row[k] for k in ("skill", "kind", "matter_id", "source_id", "item_key")}
    ev.update(event="approved", n=1, thread_ref=thread, decided_by={"name": "A", "key": sender_key(ADMIN)})
    casework_ledger.append_line(str(seat["casework"]), casework_ledger.stamp_event(ev))


def _apply(seat, thread: str, age=FRESH) -> dict:
    req = {"action": "casefile_apply_submit", "thread_ref": thread, "approver": ADMIN,
           "message_ref": "<yes@mail.example>", "numbers": [1], "smokeball_token_age_seconds": age}  # fmt: skip
    return seat["broker"].handle(req, peer_pid=GATEWAY_PID, peer_uid=AGENT_UID)


def test_a_reply_to_an_older_list_applies_that_lists_lines_not_the_newest(seat) -> None:
    monday = _delivered_scheduled(seat, "2026-11-02T16:00:00", "thread-monday")
    wednesday = _delivered_scheduled(seat, "2026-11-04T16:00:00", "thread-wednesday")
    _approve(seat, "thread-monday")
    got = _apply(seat, "thread-monday")
    assert got["accepted"] is True, got
    env = queued_env(seat, got["job_id"])
    assert env["apply"]["source_job_id"] == monday != wednesday


def test_a_scheduled_list_older_than_ten_days_is_refused_as_replaced(seat) -> None:
    _delivered_scheduled(seat, "2026-11-02T16:00:00", "thread-old")
    _approve(seat, "thread-old")
    conn = sqlite3.connect(seat["db"])
    conn.execute("UPDATE casefile_jobs SET created_at='2026-11-02T16:00:00Z'")
    conn.commit()
    conn.close()
    at(seat, "2026-11-13T16:00:00")
    got = _apply(seat, "thread-old")
    assert got["accepted"] is False and got["stale"] is True
    assert "this list was replaced" in got["reason"]


def test_an_apply_is_held_when_the_smokeball_connection_is_about_to_lapse(seat) -> None:
    _delivered_scheduled(seat, "2026-11-02T16:00:00", "thread-a")
    _approve(seat, "thread-a")
    got = _apply(seat, "thread-a", age=None)
    assert got["accepted"] is False and got["reason"].startswith("token_gate") and got["paged"] is True


def test_a_scheduled_job_goes_to_the_authored_recipient_under_a_dated_subject(seat) -> None:
    ref = casefile_schedule.scheduled_ref(datetime(2026, 11, 2).date(), "Pat Example")
    assert casefile_subject(ref, "full") == "Case file monthly full review, 2026-11-02"
    assert casefile_subject(ref, "recheck") == "Case file daily re-check, 2026-11-02"
    assert casefile_subject("<x@y.example>", "full") == ""
    assert scheduled_recipient_ok(seat["broker"], "casefile_job", ADMIN) is True
    assert scheduled_recipient_ok(seat["broker"], "casefile_job", "someone@else.example") is False


def test_a_scheduled_review_raises_its_lines_too(seat) -> None:
    """A recheck job's proposed lines are recorded and raised like a full's."""
    at(seat, "2026-11-03T16:00:00")
    job = fire(seat)["results"][0]["job_id"]
    record(seat, job, "running")
    lines = [{"n": 1, "kind": "task", "matter_id": MATTER, "source_id": TASK, "sha256": SHA}]
    record(seat, job, "delivered", cents=10, lines=lines, lines_proposed=1, files=[])
    assert len(seat["broker"].casefile.ledger.lines(job)) == 1
