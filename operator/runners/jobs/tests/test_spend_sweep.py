"""The hourly unrecorded-spend sweep (``jobs/spend.py``), through the real lane,
the real broker and its real audit ledger, at $0.

A litigation job ends failed having spent $5.00 (one priced usage row) and its
row records it. The plant is the one way a row can still read $0 after the
lanes record their usage: a later record of the same state carrying 0 (the
litigation ledger SETS the row's cents to the value sent). Each test then reads
the broker's audit ledger for ``spend_unrecorded`` rows, which is what the
hourly work-health triage lists.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest
from jobs import budget, resume, spend
from jobs_testkit import PRICING
from litigation_testkit import make_inputs

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.litigation_ledger import LitigationLedger
from workspace_broker.litigation_verbs import LitigationVerbs
from workspace_broker.server import Broker

ROW = {"model": "claude-opus-5", "in": 1_000_000, "out": 0, "stage": "read"}
CHILD = """
import json, pathlib, sys
jd = pathlib.Path(sys.argv[1])
(jd / 'data').mkdir(exist_ok=True)
(jd / 'data' / 'usage-ledger.jsonl').write_text(json.dumps(%s) + '\\n')
v = {"verdict": "failed", "stage": "report", "reason": "gate_refused: a test failure", "cents": 500}
(jd / 'verdict.json').write_text(json.dumps(v))
print(json.dumps(v))
""" % repr(ROW)
T0 = 2_000_000_000.0


@pytest.fixture(autouse=True)
def _pricing(tmp_path, monkeypatch):
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    monkeypatch.setenv(budget.PRICING_ENV, str(p))
    monkeypatch.setattr(resume, "STOP_FLAG_GRACE_SECONDS", 0.2)


class RootSocket:
    """The daemon's broker socket as root, onto the real broker's verb table."""

    def __init__(self, broker: Broker) -> None:
        self.broker = broker

    def _request(self, req: dict) -> dict:
        return self.broker.handle(req, peer_pid=999, peer_uid=0)


class Seat:
    def __init__(self, tmp_path: Path) -> None:
        from jobs.litigation_lane import LitigationBroker, LitigationLane

        self.now = T0
        self.db = str(tmp_path / "audit.db")
        q = tmp_path / "run" / "litigation-queue"
        q.mkdir(parents=True)
        self.ledger = LitigationLedger(self.db, q)
        y = tmp_path / "customer.yaml"
        y.write_text("{}\n")
        firm = tmp_path / "broker-firm.yaml"
        firm.write_text("monthly_budget_usd: 750\nper_job_cap_usd: 500\n")
        writer = LedgerWriter(self.db)
        broker = Broker.__new__(Broker)
        broker.gateway_pid = 4242
        broker.agent_uid = 10000
        broker.ledger = writer
        broker.litigation = LitigationVerbs(
            self.ledger, customer_yaml=str(y), audit_append=writer.append, firm_config_path=str(firm)
        )
        runner = tmp_path / "runner.py"
        runner.write_text(CHILD)
        self.lane = LitigationLane(
            run_dir=tmp_path / "run",
            broker=LitigationBroker(RootSocket(broker)),
            runner_cmd=[sys.executable, str(runner)],
            customer_slug="example",
            sticky_db=str(tmp_path / "sticky.db"),
            cgroup_root=tmp_path / "cg",
            child_uid=None,
            clock=lambda: self.now,
            queue_dir=q,
            inputs_dir=str(make_inputs(tmp_path / "inputs")),
            state_dir=str(tmp_path / "state"),
        )

    def submit(self, n: int) -> str:
        return self.ledger.submit(
            {
                "trigger": "request",
                "requester": "admin@firm.example",
                "message_ref": f"<list-{n}@firm.example>",
                "request_text": "status list please",
                "scope": {"paralegal_staff_ids": [f"11111111-2222-3333-4444-55555555555{n}"]},
                "file_to_matter_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "file_to_matter_number": "OPS-OPERATOR-LIBRARY",
                "folder_name": "Litigation Status",
            }
        )

    def run_one(self, n: int) -> str:
        jid = self.submit(n)
        assert self.lane.tick() == "failed"
        assert self.ledger.read(jid)["cents"] == 500  # the lane records its usage (0.1)
        return jid

    def plant_zero(self, jid: str) -> None:
        self.ledger.record(jid, "failed", {"cents": 0})
        assert self.ledger.read(jid)["cents"] == 0

    def hour(self) -> None:
        self.now += spend.SWEEP_SECONDS
        self.lane.tick()

    def alerts(self) -> list[dict[str, Any]]:
        conn = sqlite3.connect(self.db)
        try:
            rows = conn.execute("SELECT metadata FROM audit_log WHERE action_type='DELIVERY_PROOF_FAILED'").fetchall()
        finally:
            conn.close()
        return [m for m in (json.loads(r[0]) for r in rows) if m.get("code") == spend.ALERT_CODE]


def test_a_job_whose_row_reads_zero_under_spent_usage_is_flagged_once(tmp_path):
    """MUTATION: drop the sweep from ``Daemon.wipe_expired`` and no row is written;
    drop ``spend_checked`` and a second row lands once the proof alert's own
    re-alert window (23 hours) passes."""
    seat = Seat(tmp_path)
    jid = seat.run_one(1)
    seat.plant_zero(jid)
    seat.hour()
    assert seat.alerts() == [
        {
            "lane": "litigation",
            "job_id": jid,
            "attempt": 1,
            "code": "spend_unrecorded",
            "detail": f"litigation job {jid}: usage 500 cents, recorded 0 cents",
        }
    ]
    for _ in range(30):  # past the proof alert's own 23-hour re-alert window
        seat.hour()
    assert len(seat.alerts()) == 1


def test_a_job_whose_row_records_its_spend_is_not_flagged(tmp_path):
    seat = Seat(tmp_path)
    seat.run_one(1)
    seat.hour()
    seat.hour()
    assert seat.alerts() == []


def test_a_job_that_ended_before_the_sweep_was_installed_is_not_flagged(tmp_path):
    """History is not flagged: only jobs that end after the watermark."""
    seat = Seat(tmp_path)
    (seat.lane.run_dir / spend.WATERMARK).write_text(json.dumps({"since": T0 + 10}), encoding="utf-8")
    jid = seat.run_one(1)
    seat.plant_zero(jid)
    seat.hour()
    assert seat.alerts() == []


def test_the_sweep_runs_at_most_hourly_and_the_watermark_is_written_once(tmp_path):
    seat = Seat(tmp_path)
    jid = seat.run_one(1)  # the first tick swept before the job ran: the watermark is T0
    assert spend.watermark(seat.lane.run_dir, T0 + 999) == T0
    seat.plant_zero(jid)
    seat.now += spend.SWEEP_SECONDS - 1
    seat.lane.tick()
    assert seat.alerts() == []  # not yet an hour since the last sweep
    seat.now += 1
    seat.lane.tick()
    assert len(seat.alerts()) == 1


def test_an_unreadable_watermark_starts_again_from_now(tmp_path):
    (tmp_path / spend.WATERMARK).write_text("not json", encoding="utf-8")
    assert spend.watermark(tmp_path, 42.0) == 42.0
    assert spend.watermark(tmp_path, 99.0) == 42.0


def test_a_broker_older_than_the_bundle_refuses_the_code_and_the_job_is_untouched(tmp_path, monkeypatch):
    """Release skew: the bundle that raises ``spend_unrecorded`` reaching a seat
    whose image's broker predates the code. The fleet's ordering rule keeps that
    pairing from happening (a bundle reaches a seat only once its image descends
    from base(S), operator/bin/lib/fleet/ordering.py); this is the failure mode
    if it ever did. The broker refuses the alert, the lane keeps running and the
    job's row is unchanged, the ending stays unchecked, and the next hourly sweep
    on the newer broker raises the one alert.

    MUTATION: write ``spend_checked`` when the alert is refused and the alert
    never lands after the image arrives; let the refusal raise out of the sweep
    and ``tick`` fails."""
    from workspace_broker import delivery_proof

    seat = Seat(tmp_path)
    jid = seat.run_one(1)
    seat.plant_zero(jid)
    monkeypatch.setattr(delivery_proof, "CODES", delivery_proof.CODES - {spend.ALERT_CODE})
    seat.hour()
    seat.hour()
    assert seat.alerts() == []
    assert seat.ledger.read(jid)["state"] == "failed"
    assert seat.ledger.read(jid)["cents"] == 0
    assert "spend_checked" not in seat.lane._daemon_state(jid)
    monkeypatch.setattr(delivery_proof, "CODES", delivery_proof.CODES | {spend.ALERT_CODE})
    seat.hour()
    assert [a["job_id"] for a in seat.alerts()] == [jid]
