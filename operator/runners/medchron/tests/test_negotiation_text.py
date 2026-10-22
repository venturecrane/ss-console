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


class _FakeMsg:
    """An Outlook container as extract_msg presents it: header values carry
    display names, and a sender saved inside Exchange carries an X500 address."""

    def __init__(self, path, **_kw):
        self.subject = "Offer"
        self.sender = "Pat Paralegal </O=EXCHANGELABS/OU=EXCHANGE ADMINISTRATIVE GROUP (FYDIBOHF23SPDLT)/CN=RECIPIENTS/CN=ab12-pat>"
        self.to = "Jane Adjuster <jadjuster@carrier.example>"
        self.cc = ""
        self.bcc = ""
        self.date = "Wed, 7 Oct 2026 10:00:00 -0700"
        self.body = "Forwarding the carrier's offer of $12,500."
        self.attachments = []

    def close(self):
        pass


def test_an_msg_head_shows_who_sent_it_to_whom_even_from_inside_exchange(tmp_path, monkeypatch):
    # 2026-10-21: the head kept only regex-matched SMTP addresses, so this sender
    # rendered as a blank "From:" and every display name was gone, while the
    # review's first check is exactly who sent the email to whom.
    import sys
    import types

    monkeypatch.setitem(sys.modules, "extract_msg", types.SimpleNamespace(Message=_FakeMsg))
    p = tmp_path / "fwd.msg"
    p.write_bytes(b"not read by the fake")
    text, _ = T.text_of(p, ".msg", None)
    head = dict(line.split(": ", 1) for line in text.split("\n\n", 1)[0].splitlines())
    assert head["From"] == "Pat Paralegal <internal Exchange address>"
    assert head["To"] == "Jane Adjuster <jadjuster@carrier.example>"
    assert "/O=EXCHANGELABS" not in text


def test_the_wall_still_decides_on_addresses_alone(tmp_path, monkeypatch):
    import sys
    import types

    from medchron.demand import pull

    monkeypatch.setitem(sys.modules, "extract_msg", types.SimpleNamespace(Message=_FakeMsg))
    m = pull._open_msg(tmp_path / "x.msg")
    assert m["sender"] == "" and m["recipients"] == ["jadjuster@carrier.example"]
