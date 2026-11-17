"""An other-person finding on a scan's OCR layer withholds only after the page
itself is read (2026-11-17).

WHY. Twice in one day an update withheld a client's own record by the
identity rule because the hidden OCR text layer of a scanned page misread one
digit of the client's date of birth; vision had not transcribed that page, so
the text-layer finding stood. Now the hit page goes to vision first and the
rule judges its transcription. Every name and date here is invented; the job's
client is Alpha Example, born 01/01/1970.
"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf

from jobs.medchron import config as config_mod, job as job_mod
from jobs.medchron.gates import foreign_identity as FI
from jobs.stages import identity_pages as IP, update_select as US
from jobs.stages.base import StageRun
from jobs.stages.transcript import merge_pages
from jobs_testkit import FakeSeat

RID = "kr1"
FIRST = "Follow-up examination. Patient: Alpha Example. Neck pain improving."
OCR_MISREAD = "Patient: Alpha Example DOB: 01/01/1976 Assessment: cervical strain."
PAGE_RIGHT = "Patient: Alpha Example\nDOB: 01/01/1970\nAssessment: cervical strain, improving."
PAGE_OTHER = "Patient: Alpha Example\nDOB: 03/17/1948\nAssessment: lumbar strain."


def _sr(job_dir: Path, firm: Path, data_root: Path, log: list[str]) -> StageRun:
    job = job_mod.load(job_dir)
    return StageRun(
        job=job,
        cfg=config_mod.load(str(firm)),
        unit=job.units[0],
        slug_dir=data_root / "example-matter",
        decided={},
        log=log.append,
        seat_factory=lambda: FakeSeat(),
    )


def _pdf(path: Path, page2: str, *, scanned: bool) -> None:
    """Page 1 typed; page 2 either a scan (an image under an invisible OCR
    layer, as an OCR engine writes it) or a typed page."""
    doc = pymupdf.open()
    p1 = doc.new_page()
    p1.insert_text((72, 72), FIRST)
    p2 = doc.new_page()
    if scanned:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
        pix.clear_with(200)
        p2.insert_image(pymupdf.Rect(0, 0, 595, 842), pixmap=pix)
        p2.insert_text((72, 72), page2, render_mode=3)
    else:
        p2.insert_text((72, 72), page2)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))


def _scene(sr: StageRun, page2: str, *, scanned: bool) -> tuple[list[dict], Path]:
    sd = sr.slug_dir
    pdf = sd / "raw" / f"{RID}.pdf"
    _pdf(pdf, page2, scanned=scanned)
    (sd / "text").mkdir(parents=True, exist_ok=True)
    (sd / "text" / f"{RID}.txt").write_text(f"[p.1]\n{FIRST}\n[p.2]\n{page2}\n", encoding="utf-8")
    row = {"id": RID, "name": "Dr. Example Bill and Report", "ext": ".pdf", "path": str(pdf)}
    ext = {**row, "pages": 2, "text_path": str(sd / "text" / f"{RID}.txt")}
    (sd / "extracted.jsonl").write_text(json.dumps(ext) + "\n", encoding="utf-8")
    (sd / "scan_queue.json").write_text("[]", encoding="utf-8")
    ud = sd / "runs" / "alpha" / "update"
    ud.mkdir(parents=True, exist_ok=True)
    return [row], ud


def _vision_reads(sr: StageRun, sent: list[dict], page_text: str) -> None:
    """What the vision pass does with the queued pages: their transcription
    replaces them in the document's text (``transcript.merge_pages``)."""
    tp = sr.slug_dir / "text" / f"{RID}.txt"
    pages = [int(x["page"]) for x in sent]
    tp.write_text(merge_pages(tp.read_text(encoding="utf-8"), {p: page_text for p in pages}, pages), encoding="utf-8")


def test_an_ocr_misread_of_the_clients_birth_date_is_cleared_by_reading_the_page(
    job_dir, firm_config_path, data_root
) -> None:
    log: list[str] = []
    sr = _sr(job_dir, firm_config_path, data_root, log)
    rows, ud = _scene(sr, OCR_MISREAD, scanned=True)
    sent = IP.queue(sr, rows, ud)
    assert sent == [{"id": RID, "page": 2}]  # the hit page only, never the whole file
    q = json.loads((sr.slug_dir / "scan_queue.json").read_text())
    assert [(r["id"], r["vision_pages"]) for r in q] == [(RID, [2])]
    _vision_reads(sr, sent, PAGE_RIGHT)
    skipped: dict[str, list[str]] = {}
    kept = US._leave_out_foreign(sr, ud, rows, skipped)
    assert [r["id"] for r in kept] == [RID] and not skipped
    assert not FI.withheld(sr.slug_dir)
    assert json.loads((ud / IP.CLEARED).read_text()) == [RID]
    assert any("cleared by the page read" in m for m in log)


def test_a_page_read_that_confirms_another_person_still_withholds(job_dir, firm_config_path, data_root) -> None:
    sr = _sr(job_dir, firm_config_path, data_root, [])
    rows, ud = _scene(sr, OCR_MISREAD, scanned=True)
    sent = IP.queue(sr, rows, ud)
    _vision_reads(sr, sent, PAGE_OTHER)
    skipped: dict[str, list[str]] = {}
    assert US._leave_out_foreign(sr, ud, rows, skipped) == []
    assert skipped[FI.RULE_REASONS[FI.RULE_ANOTHER_PERSON]] == [RID]
    assert json.loads((ud / IP.CLEARED).read_text()) == []


def test_a_typed_page_naming_another_person_withholds_with_no_page_read(job_dir, firm_config_path, data_root) -> None:
    sr = _sr(job_dir, firm_config_path, data_root, [])
    rows, ud = _scene(sr, PAGE_OTHER.replace("\n", " "), scanned=False)
    assert IP.queue(sr, rows, ud) == []  # a born-digital page's text is the page: nothing is sent
    assert json.loads((sr.slug_dir / "scan_queue.json").read_text()) == []
    skipped: dict[str, list[str]] = {}
    assert US._leave_out_foreign(sr, ud, rows, skipped) == []
    assert skipped[FI.RULE_REASONS[FI.RULE_ANOTHER_PERSON]] == [RID]
