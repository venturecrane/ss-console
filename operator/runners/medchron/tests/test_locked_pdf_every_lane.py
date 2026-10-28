"""A password-locked PDF a firm puts in a matter, through every lane that reads one.

2026-10-28: a chronology job crashed in billing on a locked bill (fixed in
#3596). The same reader call, pymupdf touching a page of a file that needs a
password, sat in other lanes. pymupdf opens a locked file without complaint
and counts its pages; it raises "document closed or encrypted" only when a
page is read. Each lane now asks ``unopened.locked`` first and names the file
with ``unopened.REASON`` the way it already names a file it cannot read.

Every test here failed on main before this change (recorded in the PR).
"""

from __future__ import annotations

import json
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

import pytest

from medchron import unopened
from medchron.demand import preflight
from medchron.drafting import caption_read
from medchron.litigation import extract as lit
from medchron.negotiation import text as neg
from medchron.stages import vision as V
from medchron_testkit import make_locked_pdf, make_pdf


def _eml_with(attachment: bytes) -> bytes:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"] = (
        "adjuster@carrier.example",
        "a@firm.example",
        "Offer and records",
        "Wed, 7 Oct 2026 10:00:00 -0700",
    )
    m.set_content("We offer $12,500 to settle. The records are attached.")
    m.add_attachment(attachment, maintype="application", subtype="pdf", filename="Records.pdf")
    return m.as_bytes()


# ---- the shared check ---------------------------------------------------------------------
def test_the_shared_check_tells_a_locked_file_from_one_locked_only_against_editing() -> None:
    import pymupdf

    with pymupdf.open(stream=make_locked_pdf(2), filetype="pdf") as doc:
        assert unopened.locked(doc)
        assert len(doc) == 2  # it counts its pages, which is why a page count is no guard
    owner_only = pymupdf.open()
    owner_only.new_page().insert_text((72, 72), "a typed page")
    data = owner_only.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner-only", user_pw="")
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        assert not unopened.locked(doc)
    assert unopened.is_password(unopened.Locked())


# ---- litigation status job ----------------------------------------------------------------
def test_litigation_a_locked_file_is_a_password_failure_record(tmp_path: Path) -> None:
    p = tmp_path / "Motion.pdf"
    p.write_bytes(make_locked_pdf(["NOTICE OF MOTION"]))
    rec = lit.extract_file(p, ".pdf", tmp_path / "txt" / "Motion.txt", None)
    assert rec == {"ok": False, "problem": f"pdf_password: {unopened.REASON}"}


def test_litigation_a_locked_attachment_leaves_its_email_readable(tmp_path: Path) -> None:
    p = tmp_path / "offer.eml"
    p.write_bytes(_eml_with(make_locked_pdf(["MEDICAL RECORDS"])))
    rec = lit.extract_file(p, ".eml", tmp_path / "txt" / "offer.txt", None)
    assert rec["ok"], rec
    text = Path(rec["text_path"]).read_text()
    assert "We offer $12,500 to settle." in text
    assert "[ATTACHMENT] Records.pdf\n[attachment could not be read: pdf_password]" in text


# ---- negotiation watch --------------------------------------------------------------------
def test_negotiation_a_locked_file_is_an_extract_failure(tmp_path: Path) -> None:
    p = tmp_path / "offer.pdf"
    p.write_bytes(make_locked_pdf(["OFFER LETTER"]))
    with pytest.raises(neg.ExtractFailure) as got:
        neg.text_of(p, ".pdf", None)
    assert got.value.problem == f"pdf_password: {unopened.REASON}"


def test_negotiation_a_locked_attachment_leaves_the_offer_email_readable(tmp_path: Path) -> None:
    p = tmp_path / "offer.eml"
    p.write_bytes(_eml_with(make_locked_pdf(["MEDICAL RECORDS"])))
    text, _ = neg.text_of(p, ".eml", None)
    assert "We offer $12,500 to settle." in text
    assert "[attachment could not be read: pdf_password]" in text


# ---- demand letter ------------------------------------------------------------------------
def test_demand_a_locked_file_is_unreadable_for_the_password_reason(tmp_path: Path) -> None:
    p = tmp_path / "bill.pdf"
    p.write_bytes(make_locked_pdf(["STATEMENT OF CHARGES"]))
    row = {"id": "d1", "name": "bill", "ext": ".pdf", "path": str(p)}
    (tmp_path / "text").mkdir()
    rec = preflight.extract_one(row, tmp_path / "text")
    assert rec["unextractable"] == unopened.REASON
    assert rec["transcribe"] == [] and "text_path" not in rec
    # The same file unlocked reads normally (the check is not over-broad).
    p.write_bytes(make_pdf(["STATEMENT OF CHARGES for services rendered on 01/02/2026 at the clinic"]))
    assert "unextractable" not in preflight.extract_one(row, tmp_path / "text")


# ---- drafting caption read ----------------------------------------------------------------
def test_drafting_a_locked_court_paper_leaves_the_caption_unread_for_the_password_reason(tmp_path: Path) -> None:
    p = tmp_path / "Complaint.pdf"
    p.write_bytes(make_locked_pdf(["SUPERIOR COURT OF THE STATE"]))
    doorway = SimpleNamespace(call=lambda *a, **k: pytest.fail("no model call for a file that cannot be opened"))
    with pytest.raises(caption_read.Unread) as got:
        caption_read.read(doorway, "m", {"name": "Complaint", "source_path": str(p)}, lambda _m: None)
    assert str(got.value) == unopened.REASON


# ---- chronology vision pass ---------------------------------------------------------------
def test_vision_a_locked_file_in_the_queue_is_a_results_row_not_a_crash(tmp_path: Path) -> None:
    """Extract records a locked file as the password error before it can be
    queued, so this guards the render the stage did outside its open guard."""
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(make_locked_pdf(2))
    queue = [{"id": "X", "name": "scan.pdf"}]
    log = tmp_path / "ocr_results.jsonl"
    sr = SimpleNamespace(slug_dir=tmp_path, log=lambda _m: None)
    files, pending = V._gather(sr, queue, {"X": str(pdf)}, log)  # type: ignore[arg-type]
    assert files == {} and pending == []
    assert [json.loads(x) for x in log.read_text().splitlines()] == [
        {"id": "X", "name": "scan.pdf", "error": unopened.REASON}
    ]
