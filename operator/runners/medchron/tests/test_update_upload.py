"""The update's filing, the safe way: upload new, read back, prove by hash,
and only then delete the delivered chronology document by id. Never anything
else, never before the proof, never over a file that is not ours."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from medchron import config as config_mod, job as job_mod
from medchron.stages import update_upload, upload
from medchron.stages.base import StageRun
from medchron_testkit import FakeSeat, doc_row

FOLDER = "fold-chron"
OLD = "Alpha Example - Medical Chronology 09-01-26.docx"
NEW = "Alpha Example - Medical Chronology 10-13-26.docx"
EX2 = "Exhibit 2 - Example Imaging - 02-10-2026 (Medical Records).pdf"


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_upload.time, "sleep", lambda _s: None)
    monkeypatch.setattr(upload.time, "sleep", lambda _s: None)


def _scene(job_dir: Path, firm: Path, data_root: Path, log: list[str]) -> tuple[StageRun, FakeSeat]:
    sd = data_root / "example-matter"
    out, ud = sd / "out" / "alpha", sd / "runs" / "alpha" / "update"
    out.mkdir(parents=True)
    ud.mkdir(parents=True)
    manifest = []
    for name, data in ((NEW, b"new chronology bytes"), (EX2, b"%PDF new exhibit")):
        (out / name).write_bytes(data)
        manifest.append(
            {
                "name": name,
                "folder": "CHRON",
                "local_path": str(out / name),
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        )
    (out / "upload_manifest.json").write_text(json.dumps(manifest))
    (ud / "update_plan.json").write_text(
        json.dumps(
            {
                "chronology": {"id": "c1", "name": OLD},
                "folder_id": FOLDER,
                "folder_name": "CHRON",
                "exhibits": [{"exhibit": 1, "id": "e1", "name": "Exhibit 1 - x.pdf"}],
                "others": ["Attorney notes.docx"],
            }
        )
    )
    docs = [
        doc_row("c1", OLD, FOLDER, 9),
        doc_row("e1", "Exhibit 1 - x.pdf", FOLDER, 9),
        doc_row("a1", "Attorney notes.docx", FOLDER, 9),
    ]
    seat = FakeSeat(docs, [{"id": FOLDER, "name": "CHRON", "parentId": None, "path": "/CHRON"}], {})
    job = job_mod.load(job_dir)
    sr = StageRun(
        job=job,
        cfg=config_mod.load(str(firm)),
        unit=job.units[0],
        slug_dir=sd,
        decided={},
        log=log.append,
        seat_factory=lambda: seat,
    )
    return sr, seat


def test_the_delivered_document_is_deleted_by_id_only_after_the_new_one_is_proven(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    log: list[str] = []
    sr, seat = _scene(job_dir, firm_config_path, data_root, log)
    assert update_upload.run(sr, pause=0, tries=2) == 0, log
    assert [s["name"] for s in seat.sent] == [NEW, EX2] and seat.created == []
    assert seat.deleted == ["c1"]  # never e1 (still cited) nor a1 (the firm's own)
    rec = json.loads((sr.slug_dir / "runs" / "alpha" / "delivery.json").read_text())
    assert rec["replaced"]["deleted"] and rec["replaced"]["confirmed_gone"] and rec["folder_id"] == FOLDER
    assert all(f["proven"] for f in rec["files"])
    # A resume after the delete deletes nothing again.
    assert update_upload.run(sr, pause=0, tries=2) == 0 and seat.deleted == ["c1"]


def test_bytes_the_matter_does_not_hold_stop_the_filing_before_any_delete(
    job_dir: Path, firm_config_path: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log: list[str] = []
    sr, seat = _scene(job_dir, firm_config_path, data_root, log)
    real = seat.add_file

    def corrupt(matter_id, folder_id, name, data):
        r = real(matter_id, folder_id, name, data)
        seat.blobs[f"up-{len(seat.sent)}"] = b"x" * len(data)  # same size, other bytes
        return r

    seat.add_file = corrupt  # type: ignore[method-assign]
    assert update_upload.run(sr, pause=0, tries=2) == 1
    assert seat.deleted == [] and any(ln.startswith("readback_mismatch:") for ln in log)


def test_a_name_in_the_folder_that_is_not_ours_refuses_and_sends_nothing(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sr, seat = _scene(job_dir, firm_config_path, data_root, [])
    seat.docs.append(doc_row("z1", NEW, FOLDER, 3))  # a same-day chronology the firm filed
    assert update_upload.run(sr, pause=0, tries=2) == 1
    assert seat.sent == [] and seat.deleted == []


def test_a_delivered_document_moved_since_the_plan_holds(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sr, seat = _scene(job_dir, firm_config_path, data_root, [])
    seat.docs[0]["folderId"] = "elsewhere"
    assert update_upload.run(sr, pause=0, tries=2) == 2
    assert seat.sent == [] and seat.deleted == []


def test_a_failed_delete_holds_with_both_documents_on_the_matter(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    log: list[str] = []
    sr, seat = _scene(job_dir, firm_config_path, data_root, log)

    def refuse(matter_id, file_id):
        raise RuntimeError("403")

    seat.delete_file = refuse  # type: ignore[method-assign]
    assert update_upload.run(sr, pause=0, tries=2) == 2
    assert [s["name"] for s in seat.sent] == [NEW, EX2] and "both are on the matter" in log[-1]
