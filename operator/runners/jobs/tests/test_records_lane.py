"""The daemon's records lane: the stamped job, spool-then-row, exactly one
review row across a crash, ownership, the decision only on a delivered reply
the firm no longer reviews, the proof run's untouched review dir and spool,
same-matter serialization, and the review watch."""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from jobs import proof_mode
from jobs.records_lane import RecordsBroker, RecordsLane
from records_contract import review_row as rr
from records_testkit import JOB, MATTER

JOB2 = "01JRECXRDS0000000000000001"
RUNNER = r"""
import hashlib, json, os, sys, pathlib
jd = pathlib.Path(sys.argv[1])
job = json.loads((jd / "job.json").read_text())
assert job["kind"] == "records" and job["month_cents_used"] == 321 and job["slug"] == "example", job
assert job["per_job_cap_usd"] == 5.0 and job["monthly_budget_usd"] == 100.0, job
env = {k: os.environ.get(k, "") for k in ("JOBS_RECORDS_INPUTS_DIR", "JOBS_RECORDS_STATE_DIR", "SMOKEBALL_PAGE_INDEX_DIR")}
(jd / "env.json").write_text(json.dumps(env))
verdict = os.environ.get("FAKE_VERDICT", "delivered")
# held_some: one record held at the cap, the found one cut (records/search.py).
held_some = verdict == "held_some"
# dry_run_held: a capped proof run (records/run.py): dry_run, would be held.
would_hold = verdict == "dry_run_held"
verdict = "held" if held_some else ("dry_run" if would_hold else verdict)
data = jd / "data"
(data / "cuts").mkdir(parents=True, exist_ok=True)
blob = b"%PDF-1.4 fake cut " + b"x" * 5000
sha = hashlib.sha256(blob).hexdigest()
(data / "cuts" / "1-1.pdf").write_bytes(blob)
cuts = [{"n": 1, "k": 1, "file_id": "00000001-aaaa-bbbb-cccc-dddddddddddd", "pages": "1-4", "page_count": 4,
         "sha256": sha, "size": len(blob), "name": "Clinic records pp. 1-4.pdf", "path": "cuts/1-1.pdf"}]
(data / "cuts.json").write_text(json.dumps(cuts))
held = verdict == "held"
(data / "facts.json").write_text(json.dumps({
    "text": "Attached:\n- a", "html": "<p>Attached:</p>", "items": [], "hold_reason": "per_job_cap_usd: x" if held else None,
    "reply_text": ("Attached:\n- a\n\nSMD is finishing this request." if held else "Here is what we found.\n\nAttached:\n- a"),
    "reply_html": "<p>x</p>"}))
v = {"verdict": verdict, "stage": "verdict", "cents": 42, "items": [{"n": 1, "outcome": "attached", "files": 1}],
     "cuts": [{k: cuts[0][k] for k in ("n", "k", "file_id", "pages", "page_count", "sha256", "size")}]}
if would_hold:
    v.update(would_be="held", reason="per_job_cap_usd: 00000002 2000 pages")
elif held_some:
    v.update(reason="per_job_cap_usd: 00000002 2000 pages", items=[*v["items"], {"n": 2, "outcome": "held", "files": 0}])
elif held:
    v.update(reason="per_job_cap_usd: the run's spend reached the job's cost cap during locate; the run stopped", cuts=[])
elif verdict == "delivered":
    v["proof"] = {"readback": "confirmed", "files": [{"name": "1-1.pdf", "file_id": cuts[0]["file_id"], "size": len(blob), "sha256": sha}],
                  "review": "code_only", "review_cents": 0, "review_model": ""}
(jd / "verdict.json").write_text(json.dumps(v))
print(json.dumps(v))
"""


class FakeBroker:
    def __init__(self, events: list[str]) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.records: list[dict[str, Any]] = []
        self.alerts: list[str] = []
        self.proven: list[str] = []
        self.spool: dict[str, bytes] = {}
        self.events = events
        #: The next records_job_record calls raise this (a down socket, or the broker's refusal).
        self.record_errors: list[str] = []

    def _request(self, req: dict[str, Any]) -> dict[str, Any]:
        from jobs.broker_client import BrokerError

        a = req["action"]
        if a == "records_job_status":
            if "job_id" not in req:
                return {"ok": True, "month_cents": 321}
            return {"ok": True, "job": self.rows.get(req["job_id"])}
        if a == "records_job_record":
            self.records.append(req)
            if self.record_errors:
                raise BrokerError(self.record_errors.pop(0))
            return {"ok": True}
        if a == "attachment_put":
            p = req["payload"]
            handle = p.get("handle") or hashlib.md5(str(len(self.spool)).encode()).hexdigest()
            self.spool[handle] = self.spool.get(handle, b"") + base64.b64decode(p["chunk_b64"])
            self.events.append("put")
            out = {"ok": True, "handle": handle}
            if p["final"]:
                out["sha256"] = hashlib.sha256(self.spool[handle]).hexdigest()
            return out
        if a == "delivery_proof_alert":
            self.alerts.append(req["code"])
            return {"ok": True}
        if a == "delivery_proof_record":
            self.proven.append(req["job_id"])
            return {"ok": True}
        raise AssertionError(f"the records lane does not speak {a}")


@pytest.fixture
def seat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    review, decisions = tmp_path / "review", tmp_path / "decisions"
    monkeypatch.setenv(rr.REVIEW_DIR_ENV, str(review))
    monkeypatch.setenv(rr.DECISIONS_DIR_ENV, str(decisions))
    cy = tmp_path / "customer.yaml"
    cy.write_text(yaml.safe_dump({"matter_file_delivery": {"recipients": ["@example-firm.test"], "review_first": 0}}))
    monkeypatch.setenv("SMD_CUSTOMER_YAML", str(cy))
    monkeypatch.delenv("FAKE_VERDICT", raising=False)
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "records-firm.yaml").write_text("per_job_cap_usd: 5.0\nmonthly_budget_usd: 100.0\nusd_per_page: 0.01\n")
    return {"review": review, "decisions": decisions, "customer": cy, "inputs": inputs}


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


def _lane(
    tmp_path: Path, seat: dict[str, Path], events: list[str] | None = None
) -> tuple[RecordsLane, FakeBroker, list[tuple]]:
    events = [] if events is None else events
    run_dir = tmp_path / "run"
    q = run_dir / "records-queue"
    q.mkdir(parents=True)
    script = tmp_path / "runner.py"
    script.write_text(RUNNER)
    client = FakeBroker(events)
    chowns: list[tuple] = []

    def chown(path: str, uid: int, gid: int) -> None:
        events.append("chown:" + Path(path).name)
        chowns.append((path, uid, gid))

    lane = RecordsLane(
        run_dir=run_dir,
        broker=RecordsBroker(client),
        runner_cmd=[sys.executable, str(script)],
        customer_slug="example",
        sticky_db=str(tmp_path / "sticky.db"),
        cgroup_root=tmp_path / "cg",
        child_uid=None,
        clock=Clock(),
        queue_dir=q,
        inputs_dir=str(seat["inputs"]),
        state_dir=str(tmp_path / "state"),
        index_dir=str(tmp_path / "page-index"),
        chown=chown,
        gateway_ids=lambda: (1000, 1001),
    )
    return lane, client, chowns


def _submit(lane: RecordsLane, client: FakeBroker, job_id: str = JOB, **extra: Any) -> None:
    client.rows[job_id] = {"state": "queued"}
    env = {
        "kind": "records",
        "job_id": job_id,
        "trigger": "request",
        "requester": "pat.lane@example-firm.test",
        "message_ref": "AAMkAGraphMessageId0001",
        "request_text": "the clinic visit",
        "matter_id": MATTER,
        "ack": "turn",
        **extra,
    }
    (lane.queue / f"{job_id}.json").write_text(json.dumps(env))


def _rows(seat: dict[str, Path]) -> list[dict[str, Any]]:
    return rr.read_rows(seat["review"])


def test_a_delivered_job_spools_then_writes_one_row_owned_by_the_gateway(tmp_path: Path, seat: dict[str, Path]) -> None:
    events: list[str] = []
    lane, client, chowns = _lane(tmp_path, seat, events)
    _submit(lane, client)
    assert lane.tick() == "review"
    (row,) = _rows(seat)
    assert row["session_id"] == f"records:{JOB}" and row["to"] == "pat.lane@example-firm.test"
    assert row["message_id"] == "AAMkAGraphMessageId0001" and row["reason"] == ""
    (batch,) = row["batches"]
    assert batch[0]["handle"] in client.spool and batch[0]["pages"] == "1-4"
    # Spool before row; the row (and the dir it created) chowned to the gateway.
    assert events.index("put") < events.index(f"chown:{row['id']}.json")
    assert {(Path(p).name, u, g) for p, u, g in chowns} >= {(f"{row['id']}.json", 1000, 1001), ("review", 1000, 1001)}
    assert oct((seat["review"] / f"{row['id']}.json").stat().st_mode & 0o777) == "0o600"
    # review_first 0 is spent: ROOT approves this exact row.
    assert (seat["decisions"] / f"{row['id']}.{row['digest']}.approve").is_file()
    rec = client.records[-1]
    assert rec["verdict"] == "delivered" and rec["delivery"] == {
        "review_id": row["id"],
        "digest": row["digest"],
        "decision": "approve",
    }
    env = json.loads((lane.job_dir(JOB) / "env.json").read_text())
    assert env == {
        "JOBS_RECORDS_INPUTS_DIR": str(seat["inputs"]),
        "JOBS_RECORDS_STATE_DIR": str(tmp_path / "state"),
        "SMOKEBALL_PAGE_INDEX_DIR": str(tmp_path / "page-index"),
    }


def test_review_by_smd_writes_no_decision(tmp_path: Path, seat: dict[str, Path]) -> None:
    seat["customer"].write_text(
        yaml.safe_dump({"matter_file_delivery": {"recipients": ["@x.test"], "review_first": 10}})
    )
    lane, client, _ = _lane(tmp_path, seat)
    _submit(lane, client)
    assert lane.tick() == "review"
    assert not seat["decisions"].exists() or not list(seat["decisions"].iterdir())
    assert "decision" not in client.records[-1]["delivery"]


def test_a_held_job_writes_a_words_only_row_and_pages(
    tmp_path: Path, seat: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    lane, client, _ = _lane(tmp_path, seat)
    lane.child_env = {"FAKE_VERDICT": "held"}
    _submit(lane, client)
    assert lane.tick() == "review"
    (row,) = _rows(seat)
    assert row["batches"] == [] and row["reason"].startswith("per_job_cap_usd:")
    assert row["text"].endswith("SMD is finishing this request.")
    assert client.spool == {} and not seat["decisions"].exists()
    assert client.records[-1]["reason"].startswith("per_job_cap_usd:") and client.alerts == ["limit_held"]


def test_a_job_with_one_record_held_carries_the_found_records_files_and_is_never_approved(
    tmp_path: Path, seat: dict[str, Path]
) -> None:
    lane, client, _ = _lane(tmp_path, seat)
    lane.child_env = {"FAKE_VERDICT": "held_some"}
    _submit(lane, client)
    assert lane.tick() == "review"
    (row,) = _rows(seat)
    assert row["reason"].startswith("per_job_cap_usd:") and len(row["batches"]) == 1
    assert row["batches"][0][0]["handle"] in client.spool
    # review_first is spent on this seat, yet a held row waits for SMD: no decision.
    assert not seat["decisions"].exists() or not list(seat["decisions"].iterdir())
    assert "decision" not in client.records[-1]["delivery"] and client.alerts == ["limit_held"]


def test_a_crash_after_the_spool_and_before_the_row_writes_exactly_one_row(
    tmp_path: Path, seat: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    lane, client, _ = _lane(tmp_path, seat)
    _submit(lane, client)
    real = RecordsLane._row
    calls = {"n": 0}

    def crash_once(self, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("the Machine stopped")
        return real(self, *a, **k)

    monkeypatch.setattr(RecordsLane, "_row", crash_once)
    assert lane.tick() == "deferred"
    spooled = dict(client.spool)
    assert len(spooled) == 1 and _rows(seat) == []
    lane._write_state(JOB, retry_after=None)
    assert lane.tick() == "review"
    assert client.spool == spooled  # not spooled again
    assert len(_rows(seat)) == 1 and lane.jobs_run == 1  # the child did not run again


def test_a_proof_run_leaves_the_review_dir_and_the_spool_untouched(
    tmp_path: Path, seat: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    seat["review"].mkdir()
    (seat["review"] / "aaaaaaaaaaaaaaaaaaaaaaaa.json").write_text('{"id": "aaaaaaaaaaaaaaaaaaaaaaaa"}')
    before = {p.name: p.read_bytes() for p in seat["review"].iterdir()}
    lane, client, chowns = _lane(tmp_path, seat)
    lane.child_env = {proof_mode.PROOF_ENV: "1", "FAKE_VERDICT": "dry_run"}
    _submit(lane, client)
    assert lane.tick() == "dry_run"
    assert {p.name: p.read_bytes() for p in seat["review"].iterdir()} == before
    assert client.spool == {} and client.records == [
        {"action": "records_job_record", "job_id": JOB, "state": "running"}
    ]
    assert not seat["decisions"].exists() and chowns == []
    (row,) = rr.read_rows(lane.job_dir(JOB) / "proof-review")
    assert row["batches"][0][0]["handle"] == ""


def test_a_capped_proof_run_writes_its_row_as_held_with_the_reason(
    tmp_path: Path, seat: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    lane, client, _ = _lane(tmp_path, seat)
    lane.child_env = {proof_mode.PROOF_ENV: "1", "FAKE_VERDICT": "dry_run_held"}
    _submit(lane, client)
    assert lane.tick() == "dry_run"
    (row,) = rr.read_rows(lane.job_dir(JOB) / "proof-review")
    assert row["reason"].startswith("per_job_cap_usd") and row["batches"]  # the found record's cut rides it


def test_a_second_job_on_the_same_matter_waits_for_the_first(tmp_path: Path, seat: dict[str, Path]) -> None:
    lane, client, _ = _lane(tmp_path, seat)
    _submit(lane, client)
    _submit(lane, client, JOB2)
    first = lane.claim_next()
    lane._write_state(first, started=True, state="running")  # started, never reported (a crash)
    second = lane.claim_next()
    assert second == JOB2 and lane.run_job(JOB2) == "waiting"
    lane._write_state(first, state="review")
    assert lane.run_job(JOB2) == "review"


def _delivered(tmp_path: Path, seat: dict[str, Path]) -> tuple[RecordsLane, FakeBroker, dict[str, Any]]:
    seat["customer"].write_text(yaml.safe_dump({"matter_file_delivery": {"recipients": ["@x.test"], "review": "smd"}}))
    lane, client, _ = _lane(tmp_path, seat)
    _submit(lane, client)
    assert lane.tick() == "review"
    (row,) = _rows(seat)
    return lane, client, row


def _set_status(seat: dict[str, Path], row: dict[str, Any], status: str) -> None:
    rr.write_row({**row, "status": status}, seat["review"])


def test_a_sent_row_delivers_the_job_with_its_proof(tmp_path: Path, seat: dict[str, Path]) -> None:
    lane, client, row = _delivered(tmp_path, seat)
    _set_status(seat, row, rr.STATUS_SENT)
    lane.check_review_watches()
    # The digest rides every review record: the broker's close finds ROOT's decision by it.
    assert client.records[-1]["delivery"] == {"review_id": row["id"], "digest": row["digest"], "status": "sent"}
    assert all(r["delivery"].get("digest") == row["digest"] for r in client.records if "delivery" in r)
    assert lane._daemon_state(JOB)["state"] == "delivered" and client.proven == [JOB]


@pytest.mark.parametrize("status", [rr.STATUS_REJECTED, rr.STATUS_EXPIRED, rr.STATUS_FAILED])
def test_a_row_that_ends_unsent_fails_the_job_and_pages(tmp_path: Path, seat: dict[str, Path], status: str) -> None:
    lane, client, row = _delivered(tmp_path, seat)
    _set_status(seat, row, status)
    lane.check_review_watches()
    assert client.records[-1]["delivery"]["status"] == status
    assert lane._daemon_state(JOB)["state"] == "failed" and "reply_unproven" in client.alerts


def test_a_row_still_in_review_after_four_hours_pages_once(tmp_path: Path, seat: dict[str, Path]) -> None:
    lane, client, _row = _delivered(tmp_path, seat)
    lane.check_review_watches()
    assert client.alerts == []
    lane.clock.now += 4 * 3600 + 1
    lane.check_review_watches()
    lane.check_review_watches()
    assert client.alerts == ["review_held"] and lane._daemon_state(JOB)["state"] == "review"


def test_a_job_in_review_is_never_run_again(tmp_path: Path, seat: dict[str, Path]) -> None:
    lane, _client, _row = _delivered(tmp_path, seat)
    assert lane.tick() is None and lane.jobs_run == 1


# ---- review findings (PR #3717) -------------------------------------------------


def test_a_report_reentered_after_the_sweeper_sent_the_row_never_rewrites_it(
    tmp_path: Path, seat: dict[str, Path]
) -> None:
    """The broker was down when the report recorded the row (deferred). Before
    the re-entry, the gateway's sweeper sent the approved row. Rewriting it as
    ``review`` would send the records again: ROOT's approve still names its id
    and digest."""
    lane, client, _ = _lane(tmp_path, seat)
    _submit(lane, client)
    # "running" goes through; the report's record finds the socket down.
    client.record_errors = ["", "broker socket: connection refused"]
    real = client._request

    def request(req: dict[str, Any]) -> dict[str, Any]:
        if req["action"] == "records_job_record" and client.record_errors and client.record_errors[0] == "":
            client.record_errors.pop(0)
            client.records.append(req)
            return {"ok": True}
        return real(req)

    client._request = request  # type: ignore[method-assign]
    assert lane.tick() == "deferred"
    (row,) = _rows(seat)
    decisions = sorted(p.name for p in seat["decisions"].iterdir())
    _set_status(seat, row, rr.STATUS_SENT)  # the sweeper sent it
    sent = (seat["review"] / f"{row['id']}.json").read_bytes()
    lane._write_state(JOB, retry_after=None)
    assert lane.tick() == "review"
    assert (seat["review"] / f"{row['id']}.json").read_bytes() == sent
    assert sorted(p.name for p in seat["decisions"].iterdir()) == decisions
    assert lane.jobs_run == 1


def test_a_resumed_failed_job_runs_its_child_again(tmp_path: Path, seat: dict[str, Path]) -> None:
    lane, client, _ = _lane(tmp_path, seat)
    lane.child_env = {"FAKE_VERDICT": "failed"}
    _submit(lane, client)
    assert lane.tick() == "failed" and lane.jobs_run == 1
    (lane.queue / f".resume-{JOB}.json").write_text(json.dumps({"job_id": JOB, "reason": "fixed the inputs"}))
    lane.child_env = {}
    assert lane.tick() == "review"
    assert lane.jobs_run == 2 and len(_rows(seat)) == 1


@pytest.mark.parametrize("pid", ["casefile-child.pid", "child.pid", "litigation-child.pid", "demand-child.pid"])
def test_a_records_job_waits_while_a_sharing_child_runs(tmp_path: Path, seat: dict[str, Path], pid: str) -> None:
    import os

    lane, client, _ = _lane(tmp_path, seat)
    _submit(lane, client)
    (lane.run_dir / pid).write_text(str(os.getpid()))
    assert lane.tick() == "waiting" and lane.jobs_run == 0


@pytest.mark.parametrize("which", ["litigation", "negotiation"])
def test_a_demand_cgroup_lane_waits_while_a_records_child_runs(tmp_path: Path, which: str) -> None:
    import os

    from jobs.litigation_lane import LitigationLane
    from jobs.negotiation_lane import NegotiationLane

    cls = {"litigation": LitigationLane, "negotiation": NegotiationLane}[which]
    lane = cls(
        run_dir=tmp_path,
        broker=None,
        runner_cmd=[],
        customer_slug="example",
        sticky_db=str(tmp_path / "sticky.db"),
        cgroup_root=tmp_path / "cg",
    )
    assert lane._busy() is False
    (tmp_path / "records-child.pid").write_text(str(os.getpid()))
    assert lane._busy() is True


def test_a_casefile_job_waits_while_a_records_child_runs(tmp_path: Path) -> None:
    import os

    from jobs import casefile_lane

    lane = casefile_lane.CasefileLane(
        run_dir=tmp_path,
        broker=None,
        runner_cmd=[],
        customer_slug="example",
        sticky_db=str(tmp_path / "sticky.db"),
        cgroup_root=tmp_path / "cg",
    )
    assert lane._busy() is False
    (tmp_path / "records-child.pid").write_text(str(os.getpid()))
    assert lane._busy() is True


def test_a_record_the_broker_refuses_ends_the_watch_once(tmp_path: Path, seat: dict[str, Path]) -> None:
    """SMD closed the job while the sweeper still sent its approved row: the
    lane's ``sent`` record is refused, and retrying cannot change that."""
    lane, client, row = _delivered(tmp_path, seat)
    _set_status(seat, row, rr.STATUS_SENT)
    client.record_errors = ["broker refused: invalid_transition: the job is closed"]
    before = len(client.records)
    lane.check_review_watches()
    lane.check_review_watches()
    assert len(client.records) == before + 1  # asked once, never again
    st = lane._daemon_state(JOB)
    assert st["state"] == "failed" and st["review_watch"]["refused"] is True
    assert client.alerts == ["reply_unproven"]


def test_a_record_lost_to_a_down_socket_is_retried(tmp_path: Path, seat: dict[str, Path]) -> None:
    lane, client, row = _delivered(tmp_path, seat)
    _set_status(seat, row, rr.STATUS_SENT)
    client.record_errors = ["broker socket: connection refused"]
    lane.check_review_watches()
    assert lane._daemon_state(JOB)["state"] == "review"
    lane.check_review_watches()
    assert lane._daemon_state(JOB)["state"] == "delivered"
