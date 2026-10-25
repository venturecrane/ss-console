"""A named email attachment is read through its email (``stages/email_parents``),
and coverage names the email, not the runner's attachment id (``covered``)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from medchron import covered
from medchron.stages import email_parents, msg

MATTER = "m-1"


def _job(jobs: Path, name: str, *, state: str = "delivered", matter: str = MATTER) -> Path:
    sd = jobs / name / "data" / "firm"
    sd.mkdir(parents=True)
    (jobs / name / "daemon.json").write_text(json.dumps({"state": state, "finished_at": "2026-10-25T07:30:00Z"}))
    (jobs / name / "job.yaml").write_text(f"slug: firm\nmatter:\n  id: {matter}\n")
    return sd


def _jsonl(p: Path, rows: list[dict]) -> None:
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _sr(tmp_path: Path) -> SimpleNamespace:
    jobs = tmp_path / "run" / "jobs"
    sd = _job(jobs, "UPD", state="claimed")
    job = SimpleNamespace(install_root=str(tmp_path / "run"), matter_id=MATTER, path=sd.parent.parent / "job.yaml")
    return SimpleNamespace(job=job, slug_dir=sd, jobs=jobs)


def test_an_attachment_resolves_to_its_recorded_email_id(tmp_path):
    sr = _sr(tmp_path)
    base = _job(sr.jobs, "BASE")
    _jsonl(base / "raw_manifest.jsonl", [{"id": "msgatt-aaa", "container": "Mail.msg", "container_id": "E1"}])
    found, left = email_parents.resolve(sr, {"msgatt-aaa", "D9"})
    assert found == {"msgatt-aaa": ["E1"]} and left == []


def test_an_older_row_resolves_by_the_emails_file_name_every_match_kept(tmp_path):
    """Before the id was recorded the row carries only the email's file name;
    two emails can share it, and both are opened."""
    sr = _sr(tmp_path)
    base = _job(sr.jobs, "BASE")
    _jsonl(base / "raw_manifest.jsonl", [{"id": "msgatt-aaa", "container": "Re records.msg"}])
    _jsonl(
        base / "msg_manifest.jsonl",
        [
            {"id": "E1", "name": "Re records", "ext": ".msg"},
            {"id": "E2", "name": "Re records", "ext": ".msg"},
            {"id": "E3", "name": "Other", "ext": ".msg"},
        ],
    )
    found, _ = email_parents.resolve(sr, {"msgatt-aaa"})
    assert found == {"msgatt-aaa": ["E1", "E2"]}


def test_an_attachment_no_delivered_job_explains_stays_unresolved(tmp_path):
    """Falsifier: a held job, another matter, or no row at all resolves nothing."""
    sr = _sr(tmp_path)
    for name, kw in (("HELD", {"state": "held"}), ("OTHER", {"matter": "m-2"})):
        sd = _job(sr.jobs, name, **kw)
        _jsonl(sd / "raw_manifest.jsonl", [{"id": "msgatt-aaa", "container_id": "E1"}])
    base = _job(sr.jobs, "BASE")
    _jsonl(base / "raw_manifest.jsonl", [{"id": "msgatt-zzz", "container_id": "E5"}])
    found, left = email_parents.resolve(sr, {"msgatt-aaa"})
    assert found == {} and left == ["msgatt-aaa"]


def test_the_email_stage_opens_the_added_emails(tmp_path):
    d = tmp_path
    (d / "include.json").write_text(json.dumps({"include_file_ids": ["D1", "msgatt-aaa"]}))
    email_parents.write(d, {"msgatt-aaa": ["E1", "E2"]})
    assert msg.named_ids(d) == {"D1", "msgatt-aaa", "E1", "E2"}
    (d / "named_excluded.json").write_text(json.dumps([{"id": "E2"}]))
    assert msg.named_ids(d) == {"D1", "msgatt-aaa", "E1"}


def test_coverage_names_the_email_for_a_recorded_attachment(tmp_path):
    _jsonl(
        tmp_path / "raw_manifest.jsonl",
        [
            {"id": "msgatt-aaa", "container_id": "E1"},
            {"id": "msgatt-bbb"},
            {"id": "D1"},
        ],
    )
    assert covered._as_matter_ids(tmp_path, ["D1", "msgatt-aaa", "msgatt-bbb"]) == ["D1", "E1", "msgatt-bbb"]


BIG = b"%PDF-1.4 " + b"x" * 200_000


def test_the_index_records_each_attachments_email_id(tmp_path):
    ix = msg._Index(tmp_path, {})
    ix.add_attachment("Subject", "scan.pdf", BIG, container="Mail.msg", container_id="E1")
    ix.add_attachment("Subject", "scan.pdf", BIG, container="Mail2.msg", container_id="E2")
    (rec,) = ix.by_hash.values()
    assert rec["container_ids"] == ["E1", "E2"] and rec["containers"] == ["Mail.msg", "Mail2.msg"]
