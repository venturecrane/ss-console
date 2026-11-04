"""SMD reads the first ledger-path briefs before they file (``review_first.py``).

Synthetic matter and scripted model (``test_drafting_evidence_lane``). Each
test is built to fail on the property it names: a held brief never uploads;
a release files exactly the held bytes, once, and changes no byte of the
attorney notes; a release of changed bytes refuses; the count comes from the
lane's release record; ``review_first: 0`` files as before.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from drafting_testkit import firm_data
from jobs import drafting_lane
from jobs.drafting import evidence_config, release as release_mod, review_first
from jobs.demand import deliver
from jobs.drafting.run import DraftingRun
from test_drafting_evidence_lane import CHECKER, HELD, LedgerClient, _run

from jobs_testkit import PRICING


@pytest.fixture(autouse=True)
def _gate(monkeypatch):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


@pytest.fixture
def pricing(tmp_path: Path) -> Path:
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    return p


def _again(tmp_path: Path, pricing: Path, seat, releases: int | None = None):
    """The same job run again (a resume), with the lane's stamps."""
    jd = tmp_path / "job"
    doc = json.loads((jd / "job.json").read_text())
    if releases is not None:
        doc["ledger_releases"] = releases
    (jd / "job.json").write_text(json.dumps(doc))
    r = DraftingRun(
        jd,
        inputs_dir=str(tmp_path / "inputs"),
        pricing=str(pricing),
        seat_factory=lambda: seat,
        client=LedgerClient(),
        log=lambda _m: None,
        readback_pause=0.0,
    )
    return r, r.run()


def _shas(r: DraftingRun) -> dict[str, str]:
    m = json.loads((deliver.out_dir(r.data) / "upload_manifest.json").read_text())
    return {x["role"]: hashlib.sha256(Path(x["local_path"]).read_bytes()).hexdigest() for x in m}


def test_review_first_defaults_to_one_on_the_ledger_path_and_zero_elsewhere():
    assert evidence_config.settings(firm_data(evidence=HELD), "mediation_brief").review_first == 1
    assert (
        evidence_config.settings(firm_data(evidence={**HELD, "review_first": 3}), "mediation_brief").review_first == 3
    )
    assert evidence_config.settings(firm_data(evidence={"mode": "digest"}), "mediation_brief").review_first == 0


def test_a_held_brief_runs_through_its_files_and_never_uploads(tmp_path, pricing):
    r, v, log, seat = _run(tmp_path, pricing, LedgerClient(), evidence=HELD)
    assert v.outcome == "failed" and v.reason.startswith("held_for_smd: review_first:"), (v.reason, log[-4:])
    assert seat.created == [] and seat.sent == []
    assert r._is_done("manifest") and r._is_done("review")  # everything a filing needs was built
    held = json.loads((r.data / review_first.HELD).read_text())
    assert {f["role"] for f in held["files"]} == {"draft", "attorney_notes"}
    # resumed with no release, it holds again and still files nothing
    _r, v2 = _again(tmp_path, pricing, seat)
    assert v2.reason.startswith("held_for_smd:") and seat.created == []


def test_a_release_files_exactly_the_held_bytes_once_and_writes_no_notes(tmp_path, pricing):
    r, v, _log, seat = _run(tmp_path, pricing, LedgerClient(), evidence=HELD)
    assert v.reason.startswith("held_for_smd:")
    before = _shas(r)
    (r.job.job_dir / review_first.RELEASE_MARKER).write_text(json.dumps({"by": "SMD", "why": "read it"}))
    r2, v2 = _again(tmp_path, pricing, seat)
    assert v2.outcome == "delivered" and v2.smd_released is True, v2.reason
    assert _shas(r2) == before, "the files filed are the bytes SMD read; the notes gained nothing"
    uploaded = [hashlib.sha256(b).hexdigest() for k, b in seat.blobs.items() if k.startswith("up-")]
    assert sorted(uploaded) == sorted(before.values()), "each held file uploaded once, byte for byte"
    assert v2.proof and v2.proof.get("readback") == "confirmed"
    assert "release" not in (r2.data / "notes-final.md").read_text().lower()


def test_a_release_of_changed_bytes_refuses_and_files_nothing(tmp_path, pricing):
    r, _v, _log, seat = _run(tmp_path, pricing, LedgerClient(), evidence=HELD)
    m = json.loads((deliver.out_dir(r.data) / "upload_manifest.json").read_text())
    Path(next(x for x in m if x["role"] == "draft")["local_path"]).write_bytes(b"not what SMD read")
    (r.job.job_dir / review_first.RELEASE_MARKER).write_text("{}")
    _r, v2 = _again(tmp_path, pricing, seat)
    assert v2.outcome == "failed" and "not the bytes SMD read" in v2.reason and seat.created == []


def test_the_count_comes_from_the_lane_release_record(tmp_path, pricing):
    r, v, _log, seat = _run(tmp_path, pricing, LedgerClient(), evidence=HELD)
    assert v.reason.startswith("held_for_smd:")
    _r, v2 = _again(tmp_path, pricing, seat, releases=1)  # one released already: this one files on its own
    assert v2.outcome == "delivered" and v2.smd_released is False


def test_the_lane_counts_each_filed_release_once_and_stamps_it(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    assert drafting_lane.releases(run_dir) == []
    (run_dir / drafting_lane.RELEASES).write_text('{"job_id": "J1"}\n{"job_id": "J1"}\nnot json\n{"job_id": "J2"}\n')
    assert drafting_lane.releases(run_dir) == ["J1", "J2"]


class _Broker:
    def __init__(self, row):
        self.row, self.sent = row, []

    def _request(self, req):
        self.sent.append(req)
        if req["action"] == "drafting_job_status":
            return {"ok": True, "job": self.row}
        return {"ok": True, "job_id": req["job_id"]}


def test_the_release_cli_asks_the_root_resume_and_the_discard_records_a_reason(capsys):
    held = {"state": "failed", "reason": "held_for_smd: review_first: ..."}
    b = _Broker(held)
    assert release_mod.main(["J1", "--by", "scott", "--why", "read every cite"], client=b) == 0
    assert b.sent[-1] == {
        "action": "drafting_job_resume",
        "job_id": "J1",
        "reason": "read every cite",
        "release_held": True,
        "by": "scott",
    }
    assert release_mod.main(["J1", "--by", "scott", "--why", "wrong matter", "--discard"], client=b) == 0
    assert b.sent[-1]["action"] == "drafting_job_record" and b.sent[-1]["fields"]["reason"].startswith(
        release_mod.DISCARDED
    )
    with pytest.raises(SystemExit):
        release_mod.main(["J1", "--by", "s", "--why", "w"], client=_Broker({"state": "delivered", "reason": None}))


def test_the_daemon_carries_a_held_release_to_exactly_one_attempt(tmp_path):
    from jobs import resume

    class D:
        def __init__(self):
            self.queue, self.state = tmp_path / "q", {}
            self.queue.mkdir()
            (tmp_path / "jobs" / "J1").mkdir(parents=True)
            (tmp_path / "jobs" / "J1" / "envelope.json").write_text("{}")

        def job_dir(self, j):
            return tmp_path / "jobs" / j

        def _daemon_state(self, j):
            return dict(self.state)

        def _write_state(self, j, **kw):
            self.state.update(kw)

        def _chown_child(self, p):
            pass

    d = D()
    (d.queue / ".resume-J1.json").write_text(
        json.dumps({"job_id": "J1", "reason": "read", "release_held": True, "by": "s"})
    )
    assert resume.take_requests(d) == ["J1"]
    resume.start_run(d, "J1", ["run"])
    assert json.loads((d.job_dir("J1") / review_first.RELEASE_MARKER).read_text())["by"] == "s"
    resume.start_run(d, "J1", ["run"])  # the next attempt is not released
    assert not (d.job_dir("J1") / review_first.RELEASE_MARKER).exists()
