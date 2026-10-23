"""A password-protected file is disclosed as unopened, not held as residue.

WHY (2026-10-22). The Curtis chronology (matter 200530) read 6,496 pages over
nine hours and then held at the orphans decision on one emailed PDF: pulled
intact, then "File has not been decrypted" at extract. No unit can own a file
with no text, so it reached the decision as unexplained residue, and the run
stopped before the audit with the client waiting. The file is locked; that is
a fact about it, and the reader is owed the fact, not a stopped run.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import test_upload_daemon as ud
from medchron import config as config_mod, covered, decisions, job as job_mod, redo_doc, unopened
from medchron.resume import MARKER_PREFIX
from medchron.stages import limitations
from medchron_testkit import seed_raw_manifest

LOCKED = "msgatt-4de4280ad21d"
JOB = "01M5J9RGY9G9FFXYBZ3CH59XBY"


def _scene(data_root: Path, error: str) -> Path:
    sd = data_root / "example-matter"
    (sd / "units").mkdir(parents=True, exist_ok=True)
    (sd / "units" / "alpha.json").write_text(json.dumps([{"id": "f1", "name": "clinic note", "ext": ".pdf"}]))
    seed_raw_manifest(
        data_root,
        [
            {"id": "f1", "name": "clinic note", "ext": ".pdf", "ok": True, "folder": "/Medical"},
            {"id": LOCKED, "name": "Hospital Encounters", "ext": ".pdf", "ok": True, "folder": "/(email attachment)"},
        ],
    )
    rows = [
        {"id": "f1", "name": "clinic note", "ext": ".pdf", "pages": 3, "chars": 900},
        {"id": LOCKED, "name": "Hospital Encounters", "ext": ".pdf", "error": error},
    ]
    (sd / "extracted.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return sd


@pytest.mark.parametrize(
    "error", ["File has not been decrypted", "PDF is encrypted", "password required to open this file"]
)
def test_the_password_class_is_recognised(error: str) -> None:
    assert unopened.is_password(error)


@pytest.mark.parametrize("error", ["no text", "unhandled ext", "EOF marker not found", "", None])
def test_any_other_extract_error_is_not_the_password_class(error: object) -> None:
    assert not unopened.is_password(error)


def test_a_password_protected_file_is_explained_and_the_run_moves(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sd = _scene(data_root, "File has not been decrypted")
    job, cfg = job_mod.load(job_dir), config_mod.load(str(firm_config_path))
    d = decisions.orphans(job, cfg, sd, job.units[0], dry_run=False)
    assert not d.held, d.holds
    entry = {"name": "Hospital Encounters.pdf", "reason": unopened.REASON}
    assert entry in json.loads((sd / "orphans.json").read_text())["orphans"]


def test_control_any_other_unreadable_file_still_holds(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    """The control: the same file with a broken-file error still holds, so the
    explanation is the password class and nothing wider."""
    sd = _scene(data_root, "EOF marker not found")
    job, cfg = job_mod.load(job_dir), config_mod.load(str(firm_config_path))
    d = decisions.orphans(job, cfg, sd, job.units[0], dry_run=False)
    assert d.held and "Hospital Encounters" in d.holds[0]


def test_the_reader_is_told_the_file_was_not_opened(data_root: Path) -> None:
    sd = _scene(data_root, "File has not been decrypted")
    text = "\n".join(limitations.section(sd, "alpha", lambda _m: None))
    assert "could not be read, and nothing from them appears in this chronology" in text
    assert '"Hospital Encounters" (/(email attachment)) - ' + unopened.REASON in text


def test_control_without_a_locked_file_the_section_names_none(data_root: Path) -> None:
    sd = _scene(data_root, "File has not been decrypted")
    (sd / "extracted.jsonl").write_text(json.dumps({"id": "f1", "name": "clinic note", "pages": 3}) + "\n")
    text = "\n".join(limitations.section(sd, "alpha", lambda _m: None))
    assert "Hospital Encounters" not in text


def test_a_locked_file_stays_uncovered_so_an_update_reads_it_once_opened(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sd = _scene(data_root, "File has not been decrypted")
    job, cfg = job_mod.load(job_dir), config_mod.load(str(firm_config_path))
    decisions.orphans(job, cfg, sd, job.units[0], dry_run=False)
    sets = covered.covered_sets(sd, "alpha", cfg)
    assert LOCKED in sets["uncovered"] and LOCKED not in sets["covered"]


# ---- rebuilding the document of a job held after strip -------------------------
def _seat(tmp_path: Path, job_dir: Path, daemon_state: str = "held"):
    d, broker = ud._daemon(tmp_path)
    broker.rows[JOB] = {"state": "held", "reason": "1 pulled file(s) owned by no unit"}
    jd = d.job_dir(JOB)
    jd.mkdir(parents=True)
    shutil.copy(job_dir / "job.yaml", jd / "job.yaml")
    (jd / "daemon.json").write_text(json.dumps({"claimed": True, "state": daemon_state}))
    return d


def test_the_rebuild_restores_the_unstripped_exhibits_and_resumes_from_build_doc(
    tmp_path: Path, job_dir: Path, data_root: Path
) -> None:
    sd = data_root / "example-matter"
    out = sd / "out" / "alpha"
    out.mkdir(parents=True)
    (out / "Exhibit 1 - clinic.pdf").write_bytes(b"stripped")
    (out / "Exhibit 1 - clinic.pdf.orig").write_bytes(b"original")
    (out / "Exhibit 2 - imaging.pdf").write_bytes(b"never stripped")
    (sd / "strip_result.json").write_text("{}")
    d = _seat(tmp_path, job_dir)
    redo_doc.apply(d, redo_doc.plan(d, JOB, by="smd", source="ss-console#3414"))
    assert (out / "Exhibit 1 - clinic.pdf").read_bytes() == b"original"
    assert not (out / "Exhibit 1 - clinic.pdf.orig").exists()  # strip_apply's guard no longer refuses
    assert (out / "Exhibit 2 - imaging.pdf").read_bytes() == b"never stripped"
    assert not (sd / "strip_result.json").exists()
    marker = json.loads((d.queue / f"{MARKER_PREFIX}{JOB}.json").read_text())
    assert marker["redo"] == ["build_doc", "strip_falsify", "strip_dry", "strip_apply"]


def test_the_rebuild_refuses_a_job_the_daemon_is_running(tmp_path: Path, job_dir: Path) -> None:
    d = _seat(tmp_path, job_dir, daemon_state="running")
    with pytest.raises(redo_doc.Refused, match="does not hold"):
        redo_doc.plan(d, JOB, by="smd", source="x")
    assert not (d.queue / f"{MARKER_PREFIX}{JOB}.json").exists()
