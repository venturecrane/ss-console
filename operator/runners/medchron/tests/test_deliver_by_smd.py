"""`medchron.deliver_by_smd`: a chronology SMD filed by hand becomes the job's
delivery, and the daemon's own DELIVER wake answers the requester in her thread.

WHY (2026-10-14). Two chronology updates were filed by hand while their seat jobs sat
`failed`. The requester's notice must come from the Operator, in her request
thread, the way a seat delivery's does; only a job's completion wake can bind
that reply. Each test below names the failure it stands against.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import test_upload_daemon as ud
from medchron import daemon as daemon_mod
from medchron import deliver_by_smd as dbs

JOB = "01M4AAAAAAAAAAAAAAAAAAAAAA"
REQUESTER = "requester@firm.example"
REF = "<REQ1@mail.firm.example>"
#: The overlay's wake pin (hermes-smd-overlay shared/turn_sources.py WAKE_JOB):
#: the turn binds to the job this names, or to nothing.
WAKE_JOB = re.compile(r"\b(demand|drafting|chronology|litigation|negotiation) job ([0-9A-HJKMNP-TV-Z]{26})\b")
RECORD = {
    "documents": 9,
    "pages": 818,
    "cents": 3012,
    "folder_id": "folder-7",
    "files": [{"file_id": "file-new", "name": "Chronology.docx", "bytes": 737000, "sha256": "a" * 64}],
    "replaced": {"file_id": "file-old"},
    "covered": {"covered": ["d1", "d2"], "uncovered": ["d3"], "pulled": 3},
}


class _Broker(ud.FakeBroker):
    def __init__(self) -> None:
        super().__init__()
        self.replies: list[dict] = []

    def _request(self, req):
        if req["action"] == "delivery_reply_status":
            self.proof_requests.append(req)
            return {"ok": True, "replies": list(self.replies)}
        return super()._request(req)


def _seat(tmp_path: Path, *, row_state: str = "failed", state: dict | None = None, envelope: dict | None = None):
    d, _ = ud._daemon(tmp_path)
    broker = _Broker()
    d.broker = broker
    broker.rows[JOB] = {"state": row_state}
    jd = d.job_dir(JOB)
    jd.mkdir(parents=True)
    env = (
        envelope
        if envelope is not None
        else {
            **ud._envelope(JOB),
            "requested_by": REQUESTER,
            "request_ref": REF,
        }
    )
    (jd / "envelope.json").write_text(json.dumps(env))
    st = (
        state
        if state is not None
        else {
            "claimed": True,
            "state": "failed",
            "finished_at": 1.0,
            "wake": {"pending": False, "attempts": 0, "outcome": "delivered", "task": "Outcome: failed."},
        }
    )
    (jd / "daemon.json").write_text(json.dumps(st))
    return d, broker


def _deliver(d, *, update: bool = True) -> dict:
    p = dbs.plan(d, JOB, json.loads(json.dumps(RECORD)), update=update)
    dbs.apply(d, p)
    return p


def test_a_failed_job_becomes_delivered_with_the_record_the_next_update_reads(tmp_path):
    d, broker = _seat(tmp_path)
    _deliver(d)
    assert [(s, sorted(f)) for _, s, f in broker.records] == [
        ("running", []),
        ("delivered", ["cents", "covered", "delivery", "documents", "folder_id", "pages"]),
    ]
    fields = broker.records[-1][2]
    assert fields["folder_id"] == "folder-7" and fields["covered"] == RECORD["covered"]
    assert fields["delivery"]["files"][0]["file_id"] == "file-new"
    assert fields["delivery"]["replaced"] == {"file_id": "file-old"}
    assert "reason" not in fields  # the stopped job's reason is cleared, never relayed
    proven = [r for r in broker.proof_requests if r["action"] == "delivery_proof_record"]
    assert proven and proven[0]["files"] == [{"file_id": "file-new", "size": 737000, "sha256": "a" * 64}]


def test_the_wake_is_the_daemons_own_and_reads_as_an_update(tmp_path):
    d, _ = _seat(tmp_path)
    _deliver(d)
    st = d._daemon_state(JOB)
    assert st["state"] == "delivered" and st["delivered_by"] == "smd"
    task = st["wake"]["task"]
    assert st["wake"]["pending"] is True
    first = task.split("\n")[0]
    assert first == f"Run the medical-chronology-maintainer skill's DELIVER mode for chronology job {JOB}."
    assert WAKE_JOB.search(task).groups() == ("chronology", JOB)
    assert "Outcome: delivered." in task and dbs.UPDATE_LINE in task
    assert "Delivered folder id: folder-7." in task
    assert f"Requester: {REQUESTER}." in task and f"Request ref: {REF}." in task
    assert "$" not in task and "Held at" not in task
    assert JOB in d._wakes_pending()


def test_the_deliver_skill_reads_this_line_as_an_update():
    """The reply says "updated" only because DELIVER keys on this exact line
    (tests/medchron-skill-update-into-delivered.test.ts pins the skill side)."""
    skill = Path(__file__).resolve().parents[3] / "skills" / "medical-chronology-maintainer" / "SKILL.md"
    deliver = " ".join(skill.read_text(encoding="utf-8").split("## DELIVER", 1)[1].split())
    assert f"`{dbs.UPDATE_LINE}`" in deliver
    assert "the chronology was updated in its own folder" in deliver


def test_the_update_line_is_the_one_the_daemon_writes_for_an_update_envelope(tmp_path):
    """FALSIFIER for drift: if the daemon's wording moves, DELIVER would see two
    different signals for one kind of delivery."""
    d, _ = _seat(
        tmp_path,
        envelope={
            **ud._envelope(JOB),
            "requested_by": REQUESTER,
            "request_ref": REF,
            "update": {"into": "delivered_chronology", "chronology_file_id": "x"},
        },
    )
    task = d._compose_wake(JOB, "delivered", {"folder_id": "f"})
    assert dbs.UPDATE_LINE in task.split("\n")
    p = dbs.plan(d, JOB, json.loads(json.dumps(RECORD)), update=True)
    assert p["state"]["wake"]["task"].count(dbs.UPDATE_LINE) == 1


def test_the_daemon_never_runs_the_job_after_it(tmp_path):
    """FALSIFIER: a non-terminal state here and the next tick would claim the
    job and pay for a run of a chronology already filed."""
    d, _ = _seat(tmp_path)
    _deliver(d)
    assert d.tick() is None
    assert JOB not in d._in_progress()
    assert not (d.job_dir(JOB) / "ran").exists()


def test_a_second_run_refuses(tmp_path):
    d, broker = _seat(tmp_path)
    _deliver(d)
    with pytest.raises(dbs.Refused, match="only a failed or held job"):
        dbs.plan(d, JOB, json.loads(json.dumps(RECORD)), update=True)
    assert len(broker.records) == 2


@pytest.mark.parametrize(
    ("kw", "match"),
    [
        ({"row_state": "delivered"}, "only a failed or held job"),
        ({"row_state": "submitted"}, "only a failed or held job"),
        ({"envelope": {**ud._envelope(JOB)}}, "no requester"),
        (
            {"row_state": "held", "state": {"state": "held", "wake": {"pending": True, "task": "Outcome: held."}}},
            "reply still to go",
        ),
        ({"row_state": "running", "state": {"state": "running", "claimed": True}}, "only a failed or held job"),
    ],
)
def test_what_it_refuses(tmp_path, kw, match):
    d, broker = _seat(tmp_path, **kw)
    with pytest.raises(dbs.Refused, match=match):
        dbs.plan(d, JOB, json.loads(json.dumps(RECORD)), update=True)
    assert broker.records == []


def test_a_queued_job_is_refused(tmp_path):
    d, broker = _seat(tmp_path)
    (d.queue / f"{JOB}.json").write_text("{}")
    with pytest.raises(dbs.Refused, match="still queued"):
        dbs.plan(d, JOB, json.loads(json.dumps(RECORD)), update=True)


def test_a_delivered_reply_already_claimed_is_refused(tmp_path):
    d, broker = _seat(tmp_path)
    broker.replies = [{"key": f"medchron_job:{JOB}:1:delivered", "outcome": "sent"}]
    with pytest.raises(dbs.Refused, match="second send"):
        dbs.plan(d, JOB, json.loads(json.dumps(RECORD)), update=True)


def test_a_dispatched_hold_reply_does_not_block_the_delivery(tmp_path):
    d, broker = _seat(
        tmp_path,
        row_state="held",
        state={"state": "held", "claimed": True, "wake": {"pending": False, "outcome": "delivered"}},
    )
    broker.replies = [{"key": f"medchron_job:{JOB}:1:held", "outcome": "sent"}]
    _deliver(d)
    assert broker.records[-1][1] == "delivered"


def test_a_half_finished_write_can_be_completed(tmp_path):
    """The ledger moved to running and the delivered record did not land: the
    daemon's state still says the job ended, so a re-run finishes it."""
    d, broker = _seat(tmp_path, row_state="running")
    _deliver(d)
    assert broker.records[-1][1] == "delivered"


@pytest.mark.parametrize(
    ("drop", "match"),
    [("folder_id", "folder_id"), ("files", "files"), ("covered", "covered"), ("pages", "pages")],
)
def test_an_incomplete_record_is_refused(tmp_path, drop, match):
    d, broker = _seat(tmp_path)
    rec = {k: v for k, v in RECORD.items() if k != drop}
    with pytest.raises(dbs.Refused, match=match):
        dbs.plan(d, JOB, rec, update=True)
    assert broker.records == []


def test_dry_run_prints_everything_and_writes_nothing(tmp_path, monkeypatch, capsys):
    d, broker = _seat(tmp_path)
    before = (d.job_dir(JOB) / "daemon.json").read_text()
    rec = tmp_path / "record.json"
    rec.write_text(json.dumps(RECORD))
    monkeypatch.setattr(daemon_mod, "build", lambda: d)
    assert dbs.main([JOB, str(rec), "--update", "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True and out["records"][1][0] == "delivered"
    assert out["proof"]["action"] == "delivery_proof_record"
    assert dbs.UPDATE_LINE in out["state"]["wake"]["task"]
    assert broker.records == []
    assert not [r for r in broker.proof_requests if r["action"] == "delivery_proof_record"]
    assert (d.job_dir(JOB) / "daemon.json").read_text() == before


def test_main_applies_and_a_refusal_exits_nonzero(tmp_path, monkeypatch, capsys):
    d, broker = _seat(tmp_path)
    rec = tmp_path / "record.json"
    rec.write_text(json.dumps(RECORD))
    monkeypatch.setattr(daemon_mod, "build", lambda: d)
    assert dbs.main([JOB, str(rec), "--update"]) == 0
    assert d._daemon_state(JOB)["wake"]["pending"] is True
    assert dbs.main([JOB, str(rec), "--update"]) == 2
    assert "REFUSED" in capsys.readouterr().err
    assert len(broker.records) == 2
