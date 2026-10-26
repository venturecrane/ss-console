"""The apply of approved case file lines: each line's facts bound to the hash
the broker holds, the record read again and anything changed since the
proposal refused, the write made and read back, a resumed job never writing a
line twice, and a proof run stopping before any write."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

import casefile_testkit as kit
from medchron.casefile import apply as apply_mod, own_writes
from medchron.casefile.apply import ApplyRun
from medchron.casefile.run import CasefileRun

APPLY_JOB = "01KTJ0BX0000000000000000AP"


@pytest.fixture
def proposed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pricing_path: Path) -> dict:
    for k, v in kit.environ(tmp_path).items():
        monkeypatch.setenv(k, v)
    seat = kit.CfSeat()
    jd = kit.make_job(tmp_path / "job")
    r = CasefileRun(
        jd, inputs_dir=str(kit.make_inputs(tmp_path / "inputs")), pricing=str(pricing_path),
        state_dir=tmp_path / "state", seat_factory=lambda: seat, client=kit.Scripted(), log=lambda m: None,
        today=lambda: dt.date(2026, 10, 26), readback_pause=0,
    )  # fmt: skip
    v = json.loads(r.run().to_json())
    assert v["verdict"] == "delivered"
    monkeypatch.setattr(apply_mod, "READBACK_WAITS", (0,))
    return {"seat": seat, "lines": v["lines"], "state": tmp_path / "state", "tmp": tmp_path}


def _apply(p: dict, lines: list[dict], approver: str = "admin@firm.example") -> dict:
    jd = p["tmp"] / f"apply-{len(list(p['tmp'].glob('apply-*')))}"
    jd.mkdir()
    doc = {
        "kind": "casefile", "job_id": APPLY_JOB, "trigger": "request", "mode": "apply", "requester": approver,
        "message_ref": "<approve@firm.example>", "request_text": "yes", "slug": "example", "month_cents_used": 0,
        "apply": {"source_job_id": kit.JOB, "approver": approver, "thread_ref": "thread-1", "lines": lines},
    }  # fmt: skip
    (jd / "job.json").write_text(json.dumps(doc))
    run = ApplyRun(jd, state_dir=p["state"], seat_factory=lambda: p["seat"], log=lambda m: None, sleep=lambda s: None)
    return {"run": run, "verdict": json.loads(run.run().to_json()), "dir": jd}


def _task_line(p: dict) -> dict:
    return next(ln for ln in p["lines"] if ln["kind"] == "task")


def test_an_approved_closure_is_written_as_the_approver_and_read_back(proposed):
    got = _apply(proposed, [_task_line(proposed)])["verdict"]
    assert got["verdict"] == "delivered"
    assert got["line_outcomes"] == [{"n": _task_line(proposed)["n"], "kind": "task", "outcome": "completed"}]
    method, path, body = proposed["seat"].client.writes[-1]
    assert (method, path) == ("PUT", f"/tasks/{kit.TASK_OPEN}")
    assert body["completedByStaffId"] == kit.STAFF_X and body["staffId"] == kit.STAFF_R
    assert body["subject"] == "Request police report"  # a full replace keeps what it read
    assert got["writes"] == [{"method": "PUT", "path": f"/tasks/{kit.TASK_OPEN}", "ok": True}]
    assert kit.TASK_OPEN in own_writes.ids(proposed["state"])


def test_a_task_changed_since_the_proposal_is_refused_and_never_written(proposed):
    t = next(t for t in proposed["seat"].tasks if t["id"] == kit.TASK_OPEN)
    t["subject"] = "Request police report (second copy)"
    got = _apply(proposed, [_task_line(proposed)])["verdict"]
    assert got["line_outcomes"][0]["outcome"] == "refused_changed"
    assert not [w for w in proposed["seat"].client.writes if w[0] == "PUT"]


def test_a_line_whose_facts_do_not_hash_to_what_was_approved_is_refused(proposed):
    line = {**_task_line(proposed), "sha256": "0" * 64}
    got = _apply(proposed, [line])["verdict"]
    assert got["line_outcomes"][0]["outcome"] == "refused_not_as_proposed"


def test_an_approver_the_staff_list_cannot_place_writes_nothing(proposed):
    got = _apply(proposed, [_task_line(proposed)], approver="nobody@firm.example")["verdict"]
    assert got["line_outcomes"][0]["outcome"] == "refused_no_staff"


def test_a_resumed_apply_reads_back_a_write_that_may_have_landed_and_never_writes_twice(proposed):
    first = _apply(proposed, [_task_line(proposed)])
    run: ApplyRun = first["run"]
    puts = len([w for w in proposed["seat"].client.writes if w[0] == "PUT"])
    again = json.loads(run.run().to_json())
    assert again["line_outcomes"][0]["outcome"] == "completed"
    assert len([w for w in proposed["seat"].client.writes if w[0] == "PUT"]) == puts


def test_a_medicals_tab_changed_since_the_proposal_is_refused(proposed):
    proposed["seat"].layout["Providers[1]/Provider/DisplayName"] = "Someone added a row"
    line = next(ln for ln in proposed["lines"] if ln["kind"] == "medicals")
    assert _apply(proposed, [line])["verdict"]["line_outcomes"][0]["outcome"] == "refused_changed"


def test_an_approved_medicals_row_goes_through_the_connector_write(proposed, monkeypatch):
    from smokeball_connector import medicals_tools

    seen = {}

    def fake_write(client, bill, item_id):
        seen.update(provider=bill.provider, amount=str(bill.amount), item=item_id, start=bill.start)
        return {"status": "written", "index": 1}

    monkeypatch.setattr(medicals_tools, "_write_row", fake_write)
    line = next(ln for ln in proposed["lines"] if ln["kind"] == "medicals")
    got = _apply(proposed, [line])["verdict"]
    assert got["line_outcomes"][0]["outcome"] == "written"
    assert seen == {"provider": "Exampletown Clinic", "amount": "1250.00", "item": kit.MED_ITEM, "start": "2026-01-13"}


def test_a_proof_run_reads_again_and_writes_nothing(proposed, monkeypatch):
    monkeypatch.setenv("MEDCHRON_PROOF_RUN", "1")
    got = _apply(proposed, [_task_line(proposed)])["verdict"]
    assert got["verdict"] == "dry_run" and got["line_outcomes"][0]["outcome"] == "would_complete"
    assert not [w for w in proposed["seat"].client.writes if w[0] == "PUT"]
