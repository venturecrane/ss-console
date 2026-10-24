"""SMD can account for a file the composer read and wrote no line for.

WHY (2026-10-22). The Curtis chronology held at the coverage gate on one
emailed image the composer left without a disposition, while its seven
siblings were each marked an email signature image. A person read it in the
ops workspace; nothing could record what they decided.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import test_upload_daemon as ud
from medchron import config as config_mod, coverage_decide, job as job_mod
from medchron.resume import MARKER_PREFIX
from medchron.stages import coverage as coverage_stage
from medchron.stages.base import StageRun
from medchron_testkit import FakeSeat, seed_raw_manifest

JOB = "01M5J9RGY9G9FFXYBZ3CH59XBY"
IMG = "msgatt-f1dac8706a11"


def _scene(data_root: Path) -> Path:
    sd = data_root / "example-matter"
    rows = [
        {"id": "f1", "name": "clinic note", "ext": ".pdf", "ok": True},
        {"id": IMG, "name": "image001.jpg (2)", "ext": ".jpg", "ok": True},
    ]
    seed_raw_manifest(data_root, rows)
    (sd / "units").mkdir(parents=True, exist_ok=True)
    (sd / "units" / "alpha.json").write_text(json.dumps([{**r, "chars": 245} for r in rows]))
    rd = sd / "runs" / "alpha"
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "entries_scoped_final.md").write_text("Office visit for neck pain (Exhibit 1 - p. 1)\n")
    (rd / "exhibit_map.json").write_text(json.dumps({"clinic note.pdf": 1}))
    return sd


def _gate(job_dir: Path, firm: Path, data_root: Path) -> tuple[int, list[str]]:
    job = job_mod.load(job_dir)
    log: list[str] = []
    sr = StageRun(
        job=job,
        cfg=config_mod.load(str(firm)),
        unit=job.units[0],
        slug_dir=data_root / "example-matter",
        decided={},
        log=log.append,
        seat_factory=lambda: FakeSeat(),
    )
    return coverage_stage.run(sr), log


def _seat(
    tmp_path: Path, job_dir: Path, *, reason: str = "coverage gate: a pulled file is neither cited nor explained"
):
    d, broker = ud._daemon(tmp_path)
    broker.rows[JOB] = {"state": "held", "reason": reason}
    jd = d.job_dir(JOB)
    jd.mkdir(parents=True)
    shutil.copy(job_dir / "job.yaml", jd / "job.yaml")
    (jd / "daemon.json").write_text(json.dumps({"claimed": True, "state": "held"}))
    return d


def test_the_recorded_decision_explains_the_file_and_the_gate_passes(
    tmp_path: Path, job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sd = _scene(data_root)
    rc, log = _gate(job_dir, firm_config_path, data_root)
    assert rc == coverage_stage.REWIND  # since 2026-10-23: one rebuild of the document first
    rc, log = _gate(job_dir, firm_config_path, data_root)
    assert rc == 1 and any("UNEXPLAINED 1" in line for line in log)  # the control: it holds after the rebuild
    d = _seat(tmp_path, job_dir)
    p = coverage_decide.plan(
        d, JOB, file_id=IMG, reason="email signature image, no clinical content", by="smd", source="ops-ap"
    )
    coverage_decide.apply(d, p)
    rc, log = _gate(job_dir, firm_config_path, data_root)
    assert rc == 0, log
    assert any("SMD: email signature image" in line for line in log)
    body = json.loads((sd / coverage_decide.FILE).read_text())
    assert body["decisions"][0]["by"] == "smd" and body["decisions"][0]["source"] == "ops-ap"
    assert json.loads((d.queue / f"{MARKER_PREFIX}{JOB}.json").read_text())["job_id"] == JOB


@pytest.mark.parametrize(
    ("kw", "file_id", "needle"),
    [
        ({"reason": "identity: x"}, IMG, "not a coverage hold"),
        ({}, "f1", "not a file the coverage gate held"),
    ],
)
def test_the_command_refuses_what_it_cannot_record(
    tmp_path: Path, job_dir: Path, firm_config_path: Path, data_root: Path, kw: dict, file_id: str, needle: str
) -> None:
    _scene(data_root)
    _gate(job_dir, firm_config_path, data_root)  # writes coverage_unexplained.json
    d = _seat(tmp_path, job_dir, **kw)
    with pytest.raises(coverage_decide.Refused, match=needle):
        coverage_decide.plan(d, JOB, file_id=file_id, reason="r", by="smd", source="s")
    assert not (d.queue / f"{MARKER_PREFIX}{JOB}.json").exists()
