"""A resumed job is counted once against the month, in every lane.

Each lane stamps ``month_cents_used`` into the job before its child runs, and
the child adds its OWN spend (its usage ledgers) to it before every paid call
(``limits.Limits``). A failed job already recorded its spend at the broker, so
a resume whose month read included that row counted the job twice: a wrong
budget refusal (never an overspend). Litigation, case file, negotiation and
records sent no job id to exclude (the 2026-11-10 lanes review, finding M2);
demand, drafting and the chronology always did.

Each case runs a lane's REAL class against its REAL broker verbs and ledger
(``workspace_broker``): job A spends and fails, a second job B records its own
spend, A is resumed, and its child writes down the month figure it was handed.
The arithmetic is proved both ways: month + own equals the true total (no
double count) and is never below it (no under count), and the monthly limit
still binds at the true total.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

import pytest
from jobs import budget, resume, spend
from jobs.limits import BUDGET_SETTING, LimitHold, Limits
from jobs_testkit import PRICING

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

# One million Opus input tokens at 500 cents per million: $5.00.
ROW = {"model": "claude-opus-5", "in": 1_000_000, "out": 0, "stage": "read"}
OWN = 500

CHILD = """
import json, pathlib, sys
jd = pathlib.Path(sys.argv[1])
(jd / 'data').mkdir(exist_ok=True)
job = json.loads((jd / 'job.json').read_text())
with (jd / 'month-seen.txt').open('a') as fh:
    fh.write(str(job['month_cents_used']) + '\\n')
ledger = jd / 'data' / 'usage-ledger.jsonl'
if not ledger.exists():
    ledger.write_text(json.dumps(%s) + '\\n')
v = {"verdict": "failed", "stage": "report", "reason": "gate_refused: a test failure", "cents": %d}
(jd / 'verdict.json').write_text(json.dumps(v))
print(json.dumps(v))
""" % (repr(ROW), OWN)


@pytest.fixture(autouse=True)
def _pricing(tmp_path, monkeypatch):
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    monkeypatch.setenv(budget.PRICING_ENV, str(p))
    monkeypatch.setattr(resume, "STOP_FLAG_GRACE_SECONDS", 0.2)


class RootClient:
    """The daemon's broker socket, as root, onto the real verbs."""

    def __init__(self, prefix: str, verbs: Any) -> None:
        self.prefix, self.verbs = prefix, verbs

    def _request(self, req: dict) -> dict:
        a = req["action"]
        if a.startswith(self.prefix):
            return self.verbs.handle(a, {k: v for k, v in req.items() if k != "action"}, 0)
        if a.startswith("delivery_") or a.startswith("job_note"):
            return {"ok": True, "replies": []}
        raise AssertionError(f"unexpected verb {a}")


def _common(tmp_path: Path) -> dict[str, Any]:
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "runner.py"
    p.write_text(CHILD)
    return {
        "run_dir": run_dir,
        "runner_cmd": [sys.executable, str(p)],
        "customer_slug": "example",
        "sticky_db": str(tmp_path / "sticky.db"),
        "cgroup_root": tmp_path / "cg",
        "child_uid": None,
        "clock": lambda: 1_000_000.0,
    }


def _litigation(tmp_path: Path, monkeypatch: Any) -> tuple[Any, Any, Callable[[int], str]]:
    from litigation_testkit import make_inputs
    from jobs.litigation_lane import LitigationBroker, LitigationLane
    from workspace_broker.litigation_ledger import LitigationLedger
    from workspace_broker.litigation_verbs import LitigationVerbs

    q = tmp_path / "run" / "litigation-queue"
    ledger = LitigationLedger(str(tmp_path / "audit.db"), q)
    y = tmp_path / "customer.yaml"
    y.write_text("{}\n")
    firm = tmp_path / "broker-firm.yaml"
    firm.write_text("monthly_budget_usd: 750\nper_job_cap_usd: 500\n")
    verbs = LitigationVerbs(ledger, customer_yaml=str(y), audit_append=lambda row: "id", firm_config_path=str(firm))
    lane = LitigationLane(
        **_common(tmp_path),
        broker=LitigationBroker(RootClient("litigation_", verbs)),
        queue_dir=q,
        inputs_dir=str(make_inputs(tmp_path / "inputs")),
        state_dir=str(tmp_path / "state"),
    )

    def submit(n: int) -> str:
        return ledger.submit(
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

    return lane, verbs, submit


def _casefile(tmp_path: Path, monkeypatch: Any) -> tuple[Any, Any, Callable[[int], str]]:
    from casefile_testkit import envelope, make_inputs
    from jobs.casefile_lane import CasefileBroker, CasefileLane
    from workspace_broker.casefile_store import CasefileLedger
    from workspace_broker.casefile_verbs import CasefileVerbs

    q = tmp_path / "run" / "casefile-queue"
    ledger = CasefileLedger(str(tmp_path / "audit.db"), q)
    verbs = CasefileVerbs(ledger, customer_yaml=str(tmp_path / "customer.yaml"), audit_append=lambda row: "id")
    lane = CasefileLane(
        **_common(tmp_path),
        broker=CasefileBroker(RootClient("casefile_", verbs)),
        queue_dir=q,
        inputs_dir=str(make_inputs(tmp_path / "inputs")),
        state_dir=str(tmp_path / "state"),
    )

    def submit(n: int) -> str:
        env = {k: v for k, v in envelope().items() if k not in ("kind", "job_id")}
        env.update(message_ref=f"<req-{n}@firm.example>", scope={"assisting": {"names": [f"Pat Example{n}"]}})
        return ledger.submit(env)

    return lane, verbs, submit


def _negotiation(tmp_path: Path, monkeypatch: Any) -> tuple[Any, Any, Callable[[int], str]]:
    from jobs.negotiation_lane import NegotiationBroker, NegotiationLane
    from workspace_broker.negotiation_ledger import NegotiationLedger
    from workspace_broker.negotiation_verbs import NegotiationVerbs

    q = tmp_path / "run" / "negotiation-queue"
    ledger = NegotiationLedger(str(tmp_path / "audit.db"), q)
    verbs = NegotiationVerbs(ledger, customer_yaml=str(tmp_path / "customer.yaml"), audit_append=lambda row: "id")
    lane = NegotiationLane(
        **_common(tmp_path),
        broker=NegotiationBroker(RootClient("negotiation_", verbs)),
        queue_dir=q,
        state_dir=str(tmp_path / "state"),
    )

    def submit(n: int) -> str:
        return ledger.submit(
            {
                "trigger": "scheduled",
                "requester": "admin@firm.example",
                "message_ref": f"scheduled:2026-10-1{n}T08",
                "request_text": "negotiation watch",
                "matter_statuses": ["Open"],
                "negotiation_design": "",
                "firm_words": ["example"],
                "seed_saved_before": "",
                "per_job_cap_usd": 8,
                "monthly_budget_usd": 25,
            }
        )

    return lane, verbs, submit


def _records(tmp_path: Path, monkeypatch: Any) -> tuple[Any, Any, Callable[[int], str]]:
    from jobs.records_lane import RecordsBroker, RecordsLane
    from workspace_broker.records_store import RecordsLedger
    from workspace_broker.records_verbs import RecordsVerbs

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "records-firm.yaml").write_text("per_job_cap_usd: 5.0\nmonthly_budget_usd: 100.0\nusd_per_page: 0.01\n")
    q = tmp_path / "run" / "records-queue"
    ledger = RecordsLedger(str(tmp_path / "audit.db"), q)
    verbs = RecordsVerbs(ledger, customer_yaml=str(tmp_path / "customer.yaml"), audit_append=lambda row: "id")
    lane = RecordsLane(
        **_common(tmp_path),
        broker=RecordsBroker(RootClient("records_", verbs)),
        queue_dir=q,
        inputs_dir=str(inputs),
        state_dir=str(tmp_path / "state"),
        index_dir=str(tmp_path / "page-index"),
        chown=lambda *a: None,
        gateway_ids=lambda: (1000, 1001),
    )

    def submit(n: int) -> str:
        return ledger.submit(
            {
                "trigger": "request",
                "requester": "pat.lane@example-firm.test",
                "message_ref": f"AAMkAGraphMessageId000{n}",
                "request_text": "the clinic visit",
                "matter_id": "11111111-2222-3333-4444-555555555555",
                "ack": "turn",
            }
        )

    return lane, verbs, submit


LANES = {"litigation": _litigation, "casefile": _casefile, "negotiation": _negotiation, "records": _records}


def _fail_with_spend(lane: Any, verbs: Any, submit: Callable[[int], str], n: int) -> str:
    jid = submit(n)
    assert lane.tick() == "failed", lane._daemon_state(jid)
    return jid


@pytest.mark.parametrize("lane_name", sorted(LANES))
def test_a_resumed_job_is_metered_against_the_month_once(tmp_path, monkeypatch, lane_name):
    """MUTATION: drop the job id from the lane's month read and the resumed
    child is handed both jobs' spend, so month + own reads 1500 for a true 1000."""
    lane, verbs, submit = LANES[lane_name](tmp_path, monkeypatch)
    a = _fail_with_spend(lane, verbs, submit, 1)
    b = _fail_with_spend(lane, verbs, submit, 2)
    # Both jobs' spend is at the broker: A's and the other job B's.
    true_total = verbs.month_cents("")[1]
    assert true_total == 2 * OWN
    assert verbs.handle(f"{lane_name}_job_resume", {"job_id": a, "reason": "fixed the gate"}, 0)["ok"]
    assert lane.tick() == "failed"
    seen = [int(x) for x in (lane.job_dir(a) / "month-seen.txt").read_text().split()]
    own = spend.priced_cents(lane.job_dir(a), lane.LANE)
    assert own == OWN
    # Run 1 saw nothing spent yet; the resume sees every job's spend but its own.
    assert seen[0] == 0
    assert seen[1] == true_total - OWN == verbs.month_cents(a)[1]
    assert seen[1] + own == true_total, "a resumed job's own spend was counted twice"
    # The limit still binds at the true total: no path under-counts.
    at_limit = Limits(
        cap_usd=100.0,
        monthly_budget_usd=true_total / 100.0,
        usd_per_scanned_page=0.0,
        usd_per_audit_claim=0.0,
        month_cents_used=seen[1],
    )
    with pytest.raises(LimitHold) as held:
        at_limit.check_each_call(own / 100.0, "read")
    assert held.value.setting == BUDGET_SETTING
    under = Limits(
        cap_usd=100.0,
        monthly_budget_usd=(true_total + 1) / 100.0,
        usd_per_scanned_page=0.0,
        usd_per_audit_claim=0.0,
        month_cents_used=seen[1],
    )
    under.check_each_call(own / 100.0, "read")
    assert b != a
