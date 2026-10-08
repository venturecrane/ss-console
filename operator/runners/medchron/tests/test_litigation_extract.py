"""Every extractor on a synthetic file, and every failure is a record, never
an empty success."""

from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path

import pytest

from medchron.litigation import extract
from medchron_testkit import make_pdf


def _x(tmp_path: Path, name: str, data: bytes, ocr=None) -> dict:
    p = tmp_path / name
    p.write_bytes(data)
    return extract.extract_file(p, p.suffix, tmp_path / "txt" / f"{p.stem}.txt", ocr)


def _text(rec: dict) -> str:
    return Path(rec["text_path"]).read_text()


def test_pdf_text_is_paged(tmp_path):
    rec = _x(tmp_path, "c.pdf", make_pdf(["COMPLAINT FOR DAMAGES filed 03/02/2026", "page two text here, long enough"]))
    assert rec["ok"] and rec["scanned_pages"] == 0
    t = _text(rec)
    assert "===== page 1 =====" in t and "===== page 2 =====" in t and "COMPLAINT" in t


def test_a_scanned_page_goes_to_vision_and_without_it_fails(tmp_path):
    pdf = make_pdf(["a typed page that has a real text layer", ""])
    seen = []
    rec = _x(tmp_path, "s.pdf", pdf, ocr=lambda png: seen.append(png[:8]) or "PROOF OF SERVICE [X] personal")
    assert rec["ok"] and rec["scanned_pages"] == 1 and seen[0].startswith(b"\x89PNG")
    assert "[X] personal" in _text(rec)
    rec2 = _x(tmp_path, "s2.pdf", pdf)
    assert not rec2["ok"] and rec2["problem"].startswith("scan_without_vision")


def test_eml_body_and_pdf_attachment(tmp_path):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"] = (
        "defense@counsel.example",
        "a@firm.example",
        "Answer",
        "Tue, 30 Jun 2026 10:00:00 -0700",
    )
    m.set_content("Attached is our answer. The case has settled? No.")
    m.add_attachment(
        make_pdf(["ANSWER OF DEFENDANT DELTA EXAMPLE"]), maintype="application", subtype="pdf", filename="Answer.pdf"
    )
    rec = _x(tmp_path, "a.eml", m.as_bytes())
    t = _text(rec)
    assert rec["ok"] and "From: defense@counsel.example" in t and "Attached is our answer" in t
    assert "[ATTACHMENT] Answer.pdf" in t and "ANSWER OF DEFENDANT" in t


def test_eml_html_body(tmp_path):
    m = EmailMessage()
    m["From"], m["Subject"] = "court@court.example", "Notice"
    m.set_content("<html><body><p>CMC set for <b>11/20/2026</b></p><script>x()</script></body></html>", subtype="html")
    t = _text(_x(tmp_path, "h.eml", m.as_bytes()))
    assert "CMC set for" in t and "11/20/2026" in t and "x()" not in t


def test_htm(tmp_path):
    rec = _x(tmp_path, "r.htm", b"<html><body><h1>Register of Actions</h1><p>Answer filed 04/30/2026</p></body></html>")
    assert rec["ok"] and "Answer filed 04/30/2026" in _text(rec)


def test_plain_rtf_strips_control_words(tmp_path):
    rtf = rb"{\rtf1\ansi{\fonttbl{\f0 Times;}}{\colortbl;\red0\green0\blue0;}\f0 Proof of service\par Served on 04/10/2026 \'93personally\'94\par}"
    rec = _x(tmp_path, "p.rtf", rtf)
    t = _text(rec)
    assert rec["ok"] and "Proof of service" in t and "Served on 04/10/2026" in t
    assert "Times" not in t and "\\par" not in t and "“personally”" in t


def test_docx(tmp_path):
    import docx

    d = docx.Document()
    d.add_paragraph("STIPULATION TO EXTEND TIME TO ANSWER")
    p = tmp_path / "s.docx"
    d.save(p)
    rec = extract.extract_file(p, ".docx", tmp_path / "txt" / "s.txt", None)
    assert rec["ok"] and "STIPULATION" in _text(rec)


def test_doc_without_antiword_is_a_failure_record(tmp_path, monkeypatch):
    monkeypatch.setattr(extract.shutil, "which", lambda name: None)
    rec = _x(tmp_path, "old.doc", b"\xd0\xcf\x11\xe0 legacy word bytes")
    assert rec == {"ok": False, "problem": "doc_unreadable: antiword is not installed on this seat"}


@pytest.mark.parametrize(
    "name,data,problem",
    [
        ("empty.pdf", b"", "empty_file"),
        ("blank.txt", b"   \n  ", "empty_text"),
        ("x.wpd", b"wordperfect", "unsupported"),
        ("broken.pdf", b"%PDF-1.4 not really", "pdf_unreadable"),
    ],
)
def test_nothing_falls_through_as_an_empty_success(tmp_path, name, data, problem):
    rec = _x(tmp_path, name, data)
    assert rec["ok"] is False and rec["problem"].startswith(problem)


def test_msg_retries_in_cp1252(tmp_path, monkeypatch):
    """A .msg whose body only decodes in cp1252: the first open raises, the
    second names the encoding (the failure that dropped a settlement email)."""
    import extract_msg

    opened = []

    class FakeMsg:
        def __init__(self, path, **kw):
            opened.append(kw.get("overrideEncoding"))
            if kw.get("overrideEncoding") != "cp1252":
                raise UnicodeDecodeError("utf-8", b"\x93", 0, 1, "invalid start byte")
            self.subject, self.sender, self.to, self.cc, self.date = (
                "Re: settlement",
                "Adjuster <adj@carrier.example>",
                "a@firm.example",
                "",
                "2026-09-01",
            )
            self.body, self.attachments = "“We are settled at the agreed figure.”", []

        def close(self):
            pass

    monkeypatch.setattr(extract_msg, "Message", FakeMsg)
    rec = _x(tmp_path, "m.msg", b"\xd0\xcf\x11\xe0 ole bytes")
    assert opened == [None, "cp1252"]
    t = _text(rec)
    assert rec["ok"] and "We are settled" in t and "From: adj@carrier.example" in t


def test_msg_that_never_opens_is_a_failure(tmp_path, monkeypatch):
    import extract_msg

    def boom(*a, **k):
        raise ValueError("not an OLE file")

    monkeypatch.setattr(extract_msg, "Message", boom)
    rec = _x(tmp_path, "m.msg", b"junk")
    assert not rec["ok"] and rec["problem"].startswith("msg_unreadable")


def test_extract_matter_is_resumable_and_records_failures(tmp_path):
    good = tmp_path / "raw" / "a.pdf"
    good.parent.mkdir()
    good.write_bytes(make_pdf(["ANSWER filed 04/30/2026 by the defendant"]))
    bad = tmp_path / "raw" / "b.txt"
    bad.write_bytes(b"")
    rows = [
        {"id": "a", "name": "Answer", "ok": True, "path": str(good), "ext": ".pdf"},
        {"id": "b", "name": "Blank", "ok": True, "path": str(bad), "ext": ".txt"},
    ]
    out = extract.extract_matter(tmp_path, rows, None)
    assert [r["ok"] for r in out] == [True, False]
    assert [r["file_id"] for r in extract.failures(tmp_path)] == ["b"]
    good.unlink()  # a resume does not re-read an extracted file
    assert [r["ok"] for r in extract.extract_matter(tmp_path, rows, None)] == [True, False]
