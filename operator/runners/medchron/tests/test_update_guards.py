"""The guards found live 2026-10-13, each a test that fails on the code
before the fix:

(a) a selection that names its documents opens ONLY the named email
    containers, and no attachment whose text names a different person is
    folded without a hold (before vision at the fold, after it at identity);
(b) the firm's authored exclusions apply to named ids too, so an agent that
    names everything cannot make the runner read photos;
(c) no repair loses a value the audit did not name.

Plus the update envelope's shape (the job, the lane key, the daemon's wake).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from medchron import config as config_mod, decisions, job as job_mod, lane_proof
from medchron.audit import repair as R
from medchron.stages import download as download_stage, identity as identity_stage, msg as msg_stage
from medchron.stages.base import StageRun
from medchron_testkit import FakeSeat, doc_row, make_pdf, seed_seat_files

PROSE = "Patient seen in clinic for neck pain after the collision; plan to continue therapy. " * 8
MEDICAL, PHOTOS = "fold-med", "fold-photo"
FOLDERS = [
    {"id": MEDICAL, "name": "MEDICAL", "parentId": None, "path": "/MEDICAL"},
    {"id": PHOTOS, "name": "PHOTOS", "parentId": None, "path": "/PHOTOS"},
]


def _named_job(job_dir: Path, ids: list[str], **extra) -> None:
    body = yaml.safe_load((job_dir / "job.yaml").read_text())
    body["selection"] = {"include_file_ids": ids}
    body.update(extra)
    (job_dir / "job.yaml").write_text(yaml.safe_dump(body, sort_keys=False))


def _sr(job_dir: Path, firm: Path, data_root: Path, seat: FakeSeat, log: list[str]) -> StageRun:
    job = job_mod.load(job_dir)
    return StageRun(
        job=job,
        cfg=config_mod.load(str(firm)),
        unit=job.units[0],
        slug_dir=data_root / "example-matter",
        decided={},
        log=log.append,
        seat_factory=lambda: seat,
    )


def _seat() -> FakeSeat:
    rec, photo = make_pdf([PROSE]), make_pdf(["holiday photo"])
    docs = [
        doc_row("r1", "clinic note.pdf", MEDICAL, len(rec)),
        doc_row("p1", "scene photo.pdf", PHOTOS, len(photo)),
        doc_row("m1", "RE: records.msg", MEDICAL, 2),
        doc_row("m2", "RE: someone else.msg", MEDICAL, 2),
    ]
    return FakeSeat(docs, list(FOLDERS), {"r1": rec, "p1": photo, "m1": b"m1", "m2": b"m2"})


# ---- (b) named ids obey the firm's folder rules ------------------------------
def test_a_named_photo_is_left_out_by_the_firms_rules_and_said_so(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    seat = _seat()
    _named_job(job_dir, ["r1", "p1"])
    seed_seat_files(data_root, seat)
    log: list[str] = []
    sr = _sr(job_dir, firm_config_path, data_root, seat, log)
    decisions.selection(sr.job, sr.cfg, sr.slug_dir, dry_run=False)
    assert download_stage.run(sr) == 0
    pulled = {r["id"] for r in map(json.loads, (sr.slug_dir / "raw_manifest.jsonl").read_text().splitlines())}
    assert pulled == {"r1"}  # the old code pulled the photo because it was named
    excluded = json.loads((sr.slug_dir / "named_excluded.json").read_text())
    assert [x["id"] for x in excluded] == ["p1"] and "folder rules" in excluded[0]["why"]


def test_naming_only_excluded_documents_holds(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    seat = _seat()
    _named_job(job_dir, ["p1"])
    seed_seat_files(data_root, seat)
    sr = _sr(job_dir, firm_config_path, data_root, seat, [])
    decisions.selection(sr.job, sr.cfg, sr.slug_dir, dry_run=False)
    assert download_stage.run(sr) == 2 and not (sr.slug_dir / "raw_manifest.jsonl").exists()


# ---- (a) only the named containers are opened --------------------------------
class _Att:
    def __init__(self, name: str, data: bytes) -> None:
        self.longFilename, self.shortFilename, self.data = name, name, data


class _Msg:
    registry: dict[str, list[tuple[str, bytes]]] = {}

    def __init__(self, path: str) -> None:
        self.subject = "records"
        self.attachments = [_Att(n, d) for n, d in self.registry[Path(path).name]]

    def close(self) -> None:
        pass


def test_an_update_opens_only_the_named_email_containers(
    job_dir: Path, firm_config_path: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seat = _seat()
    _Msg.registry = {"m1.msg": [("ours.pdf", make_pdf([PROSE] * 30))], "m2.msg": [("theirs.pdf", make_pdf(["x"] * 30))]}
    monkeypatch.setitem(__import__("sys").modules, "extract_msg", SimpleNamespace(Message=_Msg))
    _named_job(job_dir, ["r1", "m1"])
    seed_seat_files(data_root, seat)
    sr = _sr(job_dir, firm_config_path, data_root, seat, [])
    decisions.selection(sr.job, sr.cfg, sr.slug_dir, dry_run=False)
    assert download_stage.run(sr) == 0
    seat.mints.clear()
    assert msg_stage.run_index(sr) == 0
    assert seat.mints == [["m1"]]  # the old code minted and opened m2 as well
    report = json.loads((sr.slug_dir / "msg_attachments.json").read_text())
    text = json.dumps(report)
    assert report["emails_opened"] == 1 and "ours.pdf" in text and "theirs.pdf" not in text


def _fold_scene(data_root: Path, text: str) -> tuple[Path, str]:
    sd = data_root / "example-matter"
    (sd / "msg_pdfs").mkdir(parents=True, exist_ok=True)
    pdf = make_pdf([text])
    import hashlib

    sha = hashlib.sha256(pdf).hexdigest()
    (sd / "msg_pdfs" / f"{sha[:12]}.pdf").write_bytes(pdf)
    report = {
        "comparable": True,
        "attachments": [
            {
                "sha256": sha,
                "local": f"{sha[:12]}.pdf",
                "names": ["a.pdf"],
                "emails": ["e"],
                "already_pulled_as": None,
                "bytes": len(pdf),
            }
        ],
    }
    (sd / "msg_attachments.json").write_text(json.dumps(report))
    return sd, sha[:12]


def test_the_fold_holds_on_an_attachment_naming_another_person(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    job, cfg = job_mod.load(job_dir), config_mod.load(str(firm_config_path))
    sd, sha12 = _fold_scene(data_root, "Patient Name: Gamma Other\nDOB: 05/05/1990\nOffice visit for knee pain.")
    d = decisions.fold(job, cfg, sd, dry_run=False)
    assert d.held and "different person" in d.holds[0] and sha12 in d.holds[0]
    assert not (sd / "msg_fold.json").exists()  # the old code folded it
    (sd / "identity_release.json").write_text(json.dumps({"release": [sha12]}))
    assert not decisions.fold(job, cfg, sd, dry_run=False).held  # a person released it


def test_the_fold_takes_the_clients_own_record(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    job, cfg = job_mod.load(job_dir), config_mod.load(str(firm_config_path))
    sd, sha12 = _fold_scene(
        data_root, "Patient: Alpha Example\nDOB: 01/01/1970\nGuarantor DOB: 03/03/1950\nNeck pain follow up."
    )
    d = decisions.fold(job, cfg, sd, dry_run=False)
    assert not d.held and d.payload["fold"] == [sha12]


def test_identity_holds_on_a_folded_scan_naming_another_person(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sd = data_root / "example-matter"
    (sd / "text").mkdir(parents=True)
    (sd / "text" / "msgatt-abc.txt").write_text("[p.1] (machine transcription)\nDate of Birth: 07/07/2001\nvisit")
    (sd / "raw_manifest.jsonl").write_text(json.dumps({"id": "msgatt-abc", "ok": True, "from_email": "e"}) + "\n")
    log: list[str] = []
    sr = _sr(job_dir, firm_config_path, data_root, _seat(), log)
    assert identity_stage.run(sr) == 2 and "different person" in log[-1]


# ---- (c) no repair loses a value the audit did not name -----------------------
class _Ctx(SimpleNamespace):
    pass


def _ctx(rows: list[dict]) -> _Ctx:
    applied: list[tuple[str, str]] = []
    return _Ctx(
        logrow=lambda **kw: rows.append(kw),
        apply=lambda a, b: applied.append((a, b)),
        paths=None,
        applied=applied,
    )


CLAIM = {"claim": "Lisinopril 10 mg daily and metoprolol 25 mg twice daily were continued.", "key": "k1"}
FINDING = {"key": "k1", "verdict": "PARTIAL", "unsupported_assertions": ["twice daily"], "contradictions": []}


def test_a_rewrite_that_drops_an_unnamed_value_is_rejected() -> None:
    rows: list[dict] = []
    x = _ctx(rows)
    new = "Lisinopril was continued."  # loses 10 and 25, neither named by the audit
    assert R._settle_one(x, FINDING, CLAIM, CLAIM["claim"] + " (Exhibit 1 - p. 2)", new, "") == "rejected"
    assert x.applied == [] and rows[-1]["lost_values"] == ["10", "25"]


def test_a_rewrite_that_drops_only_what_the_audit_named_is_applied() -> None:
    rows: list[dict] = []
    x = _ctx(rows)
    finding = {**FINDING, "unsupported_assertions": ["metoprolol 25 mg twice daily"]}
    new = "Lisinopril 10 mg daily was continued."
    assert R._settle_one(x, finding, CLAIM, CLAIM["claim"] + " (Exhibit 1 - p. 2)", new, "") == "repaired"
    assert x.applied


def test_a_repair_round_drop_that_takes_unnamed_values_is_rejected() -> None:
    rows: list[dict] = []
    x = _ctx(rows)
    assert R._settle_one(x, FINDING, CLAIM, CLAIM["claim"] + " (Exhibit 1 - p. 2)", "DROP", "") == "rejected"
    assert x.applied == [] and rows[-1]["lost_values"] == ["10", "25"]


# ---- the envelope ------------------------------------------------------------------
def test_an_update_job_parses_and_refuses_what_it_cannot_place(job_dir: Path) -> None:
    _named_job(job_dir, ["r1"], update={"into": "delivered_chronology", "chronology_file_id": "doc-9"})
    job = job_mod.load(job_dir)
    assert job.update is not None and job.update.chronology_file_id == "doc-9"
    _named_job(job_dir, ["r1"], update={"into": "a second package"})
    with pytest.raises(job_mod.JobError, match="update.into"):
        job_mod.load(job_dir)
    body = yaml.safe_load((job_dir / "job.yaml").read_text())
    body["update"] = {"into": "delivered_chronology"}
    body.pop("selection")
    (job_dir / "job.yaml").write_text(yaml.safe_dump(body))
    with pytest.raises(job_mod.JobError, match="include_file_ids"):
        job_mod.load(job_dir)


def test_an_update_is_its_own_lane_and_waits_for_its_own_proof() -> None:
    env = {"update": {"into": "delivered_chronology"}}
    assert lane_proof.lane_key("medchron", env) == "medical-chronology-maintainer.update_delivered"
    assert lane_proof.lane_key("medchron", {}) == "medical-chronology-maintainer"
    assert lane_proof.KEY_RE.match(lane_proof.lane_key("medchron", env))


def test_the_wake_says_the_request_was_an_update(tmp_path: Path) -> None:
    from medchron import daemon as daemon_mod

    d = daemon_mod.Daemon.__new__(daemon_mod.Daemon)
    jd = tmp_path / "jobs" / "J1"
    jd.mkdir(parents=True)
    (jd / "envelope.json").write_text(
        json.dumps({"matter": {"number": "2099-EX-0001"}, "update": {"into": "delivered_chronology"}})
    )
    d.job_dir = lambda _j: jd  # type: ignore[method-assign]
    text = d._compose_wake("J1", "delivered", {"folder_id": "fold-chron"})
    assert "Request: update into the delivered chronology." in text and "Delivered folder id: fold-chron." in text
