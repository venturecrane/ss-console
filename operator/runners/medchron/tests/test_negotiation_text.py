"""The negotiation watch's text reader (negotiation/text.py): each kind it reads,
a scan sent to vision, and a failure that is never an empty document."""

from __future__ import annotations

import io
from email.message import EmailMessage

import pytest

from medchron.negotiation import text as T
from medchron_testkit import make_pdf


def test_a_pdf_with_a_text_layer_is_read_page_by_page(tmp_path):
    p = tmp_path / "offer.pdf"
    p.write_bytes(
        make_pdf(["Example Mutual offers $15,000 to settle all claims.", "Signed by the adjuster for Example Mutual."])
    )
    text, scanned = T.text_of(p, ".pdf", None)
    assert "offers $15,000" in text and "===== page 2 =====" in text and scanned == 0


def test_a_scanned_page_goes_to_vision(tmp_path):
    p = tmp_path / "scan.pdf"
    p.write_bytes(make_pdf([""]))
    seen = []
    text, scanned = T.text_of(p, ".pdf", lambda png: seen.append(png) or "OCR: offer of $9,000")
    assert scanned == 1 and seen and "offer of $9,000" in text
    with pytest.raises(T.ExtractFailure):
        T.text_of(p, ".pdf", None)


def test_an_eml_carries_headers_and_body(tmp_path):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"] = "adj@carrier.example", "firm@firm.example", "Offer", "Wed, 7 Oct 2026"
    m.set_content("We offer $12,500.")
    p = tmp_path / "offer.eml"
    p.write_bytes(m.as_bytes())
    text, _ = T.text_of(p, ".eml", None)
    assert "From: adj@carrier.example" in text and "We offer $12,500." in text


def test_a_docx_is_read(tmp_path):
    import docx

    d = docx.Document()
    d.add_paragraph("Our demand is $50,000.")
    buf = io.BytesIO()
    d.save(buf)
    p = tmp_path / "demand.docx"
    p.write_bytes(buf.getvalue())
    assert "Our demand is $50,000." in T.text_of(p, ".docx", None)[0]


def test_an_empty_or_unsupported_file_is_a_failure_never_empty_text(tmp_path):
    empty = tmp_path / "x.pdf"
    empty.write_bytes(b"")
    with pytest.raises(T.ExtractFailure, match="empty_file"):
        T.text_of(empty, ".pdf", None)
    other = tmp_path / "x.xlsx"
    other.write_bytes(b"data")
    with pytest.raises(T.ExtractFailure, match="unsupported"):
        T.text_of(other, ".xlsx", None)
