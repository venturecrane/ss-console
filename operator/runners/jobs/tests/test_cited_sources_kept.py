"""A delivered chronology's cited sources outlive its job dir (2026-11-06).

Retention wipes a delivered job's dir 72 hours after delivery, and an update
read the cited sources it must not file again only from that dir: an update
requested later re-filed files the firm already held. The delivered job now
records ``{file id: sha256}`` of its cited sources on its ledger row, the
broker stamps the record on the update's envelope, the daemon writes it into
job.yaml, and ``prior_reading.cited_sources`` reads it with the dir gone.
Invented ids and hashes throughout."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import yaml

from jobs.medchron import covered, job as job_mod
from jobs.stages import prior_reading, update_select, vision_seed

BASE = "01M5BASE00000000000000000A"
SHA = {rid: ch * 64 for rid, ch in (("e1", "e"), ("q1", "f"), ("r1", "e"))}


def _jsonl(p: Path, rows: list[dict]) -> None:
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _delivered(tmp_path: Path) -> Path:
    """A delivered job dir whose chronology cites clinic.pdf (e1), not quiet.pdf (q1)."""
    base = tmp_path / "run" / "jobs" / BASE / "data" / "firm"
    (base / "units").mkdir(parents=True)
    (base / "runs" / "alpha").mkdir(parents=True)
    (base / "out" / "alpha").mkdir(parents=True)
    _jsonl(base / "raw_manifest.jsonl", [{"id": r, "ok": True, "sha256": SHA[r]} for r in ("e1", "q1")])
    (base / "units" / "alpha.json").write_text(
        json.dumps([{"id": "e1", "name": "clinic", "ext": ".pdf"}, {"id": "q1", "name": "quiet", "ext": ".pdf"}])
    )
    (base / "runs" / "alpha" / "entries_final.md").write_text(
        "01/02/2026\nExample Clinic\n\nStrain. (Exhibit 1 - p. 1)\n"
    )
    (base / "out" / "alpha" / "page_map.json").write_text(
        json.dumps([{"exhibit": 1, "files": [{"file": "clinic.pdf"}]}])
    )
    return base


def _update_job(tmp_path: Path, env_extra: dict) -> job_mod.Job:
    """job.yaml as the daemon writes it for an update (``vision_seed.staged_fields``)."""
    env = {"matter": {"id": "m-1"}, "update": {"into": "delivered_chronology"}, **env_extra}
    doc = {
        "slug": "firm",
        "matter": {"id": "m-1", "number": "2026-PI-1"},
        "units": [
            {
                "unit": "alpha",
                "client_name": "Alpha Example",
                "name_token": "Example",
                "surname": "Example",
                "dob": "01/02/1980",
            }
        ],
        "incident": {"date": "2026-01-15", "source": "administrator_request"},
        "data_root": str(tmp_path / "data"),
        "install_root": str(tmp_path / "run"),
        "selection": {"include_file_ids": ["r1", "q1"]},
        "update": {"into": "delivered_chronology"},
        **vision_seed.staged_fields(env, tmp_path / "run", "01M5UPD000000000000000000A"),
    }
    jd = tmp_path / "upd-job"
    jd.mkdir(exist_ok=True)
    (jd / "job.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    return job_mod.load(jd)


def _sr(tmp_path: Path, job: job_mod.Job) -> SimpleNamespace:
    sd = tmp_path / "upd" / "firm"
    sd.mkdir(parents=True, exist_ok=True)
    rows = []
    for rid in ("r1", "q1"):
        src = tmp_path / f"{rid}.pdf"
        src.write_bytes(b"x")
        rows.append({"id": rid, "ok": True, "sha256": SHA[rid], "ext": ".pdf", "path": str(src), "name": rid})
    _jsonl(sd / "raw_manifest.jsonl", rows)
    return SimpleNamespace(job=job, slug_dir=sd, unit=SimpleNamespace(unit="alpha"), log=lambda *_: None)


def test_a_cited_source_is_recognised_after_the_delivered_dir_is_wiped(tmp_path):
    base = _delivered(tmp_path)
    # The delivered job: the driver stamps its outcome, the lane puts the
    # record on the ledger row (``delivery_fields``), the broker hands every
    # delivered row's record to the update's envelope.
    cited = prior_reading.cited_by_id(base, "alpha")
    assert cited == {"e1": SHA["e1"]}
    outcome = {"unit": "alpha", "outcome": "delivered", "cited_sources": cited, "covered": None}
    fields = covered.delivery_fields({"outcome": "delivered"}, [outcome])
    assert fields["delivery"]["cited_sources"] == {"alpha": {"e1": SHA["e1"]}}

    shutil.rmtree(tmp_path / "run" / "jobs" / BASE)  # retention, 72 hours after delivery
    job = _update_job(tmp_path, {"delivered_cited": fields["delivery"]["cited_sources"]})
    assert job.delivered_cited == {"alpha": {"e1": SHA["e1"]}}
    sr = _sr(tmp_path, job)
    assert prior_reading.cited_sources(sr) == {SHA["e1"]}  # type: ignore[arg-type]
    out, skipped = update_select._candidates(sr, {})  # type: ignore[arg-type]
    # r1 is the cited clinic file re-pulled under a new id: never filed again.
    assert skipped["byte-identical to a delivered exhibit"] == ["r1"]
    assert {r["id"] for r in out} == {"q1"}


def test_without_a_record_and_without_the_dir_nothing_is_assumed(tmp_path):
    """Falsifier: no record, dir gone: the file is read, as before."""
    _delivered(tmp_path)
    shutil.rmtree(tmp_path / "run" / "jobs" / BASE)
    sr = _sr(tmp_path, _update_job(tmp_path, {"delivered_cited": {"alpha": {"e1": "not a hash"}}}))
    assert prior_reading.cited_sources(sr) == set()  # type: ignore[arg-type]
    out, _skipped = update_select._candidates(sr, {})  # type: ignore[arg-type]
    assert {r["id"] for r in out} == {"q1", "r1"}
