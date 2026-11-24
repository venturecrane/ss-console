"""An Outlook email saved in a matter reads in a conversation (2026-11-24).

Until this change ``read_document`` refused every .msg as "a legacy Office
file ... needs manual review", while the job lanes read the same files with
their own copy of the reader. On one A&P matter that was 261 saved emails the
Operator could not read when asked, and every one of them also refused the
drafting record check for every draft on that matter. The connector now owns
the one reader (``msg_read``) and the job lanes call it.

The email here is a real Outlook container (``msg_fixture``), read by the
library the seat runs, with invented content. FALLBACK: restore the OLE refusal
in ``extract._extract`` and every test that reads through ``extract`` fails
(all but the legacy-.doc refusal and the ``open_msg`` fields).
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

import httpx
import pytest
from msg_fixture import build_msg, build_ole
from test_vision_extraction import _matter_client

from smokeball_connector import extract_cache, server, vision
from smokeball_connector.extract import (
    MECHANICAL_METHODS,
    METHOD_MSG,
    UnsupportedDocumentError,
    extract_text,
    extract_text_ex,
)
from smokeball_connector.msg_read import is_msg, open_msg


def _docx(text: str) -> bytes:
    from docx import Document

    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _blank_pdf() -> bytes:
    """A page with no text layer: a scan, as far as any reader can tell."""
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


EMAIL = build_msg(
    subject="Invented: the follow-up visit",
    sender=("Pat Example", "pat@example.test"),
    to=[("Sam Sample", "sam@example.test")],
    cc=[("Lee Copy", "lee@example.test")],
    body="An invented body line.\r\nA second invented line.",
    sent=datetime(2026, 3, 4, 18, 0, tzinfo=timezone.utc),
    attachments=[
        ("letter.docx", _docx("An invented attached letter.")),
        ("scan.pdf", _blank_pdf()),
        ("notes.txt", b"Invented plain notes."),
    ],
)


@pytest.fixture(autouse=True)
def _no_model_and_a_private_store(monkeypatch: pytest.MonkeyPatch, tmp_path) -> list[httpx.Request]:
    """The scan store in the test's own directory, and a model seam that
    records every request: reading an email must never make one."""
    monkeypatch.setenv(extract_cache.CACHE_DIR_ENV, str(tmp_path / "store"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    monkeypatch.setattr(vision, "_http_client", lambda _t: httpx.Client(transport=httpx.MockTransport(handler)))
    return calls


def test_an_email_reads_with_its_head_body_and_attachments(_no_model_and_a_private_store) -> None:
    result = extract_text_ex(EMAIL, file_name="Follow-up", file_extension=".msg", allow_vision=True)
    assert result.method == METHOD_MSG
    head, body, *attachments = result.text.split("\n\n")
    lines = dict(line.split(": ", 1) for line in head.splitlines())
    assert lines["Email"] == "Invented: the follow-up visit"
    assert lines["From"] == "Pat Example <pat@example.test>"
    assert lines["To"] == "Sam Sample <sam@example.test>"
    assert lines["Cc"] == "Lee Copy <lee@example.test>"
    assert lines["Date"].startswith("2026-03-0")
    assert lines["Attachments"] == "letter.docx; scan.pdf; notes.txt"
    assert body == "An invented body line.\r\nA second invented line."
    assert attachments == [
        "[Attachment 1 of 3: letter.docx]\nAn invented attached letter.",
        "[Attachment 2 of 3: scan.pdf]\n[not read: a scan with no stored reading]",
        "[Attachment 3 of 3: notes.txt]\nInvented plain notes.",
    ]
    assert _no_model_and_a_private_store == [], "a scan inside an email is never transcribed by reading the email"


def test_a_scan_inside_an_email_is_served_from_the_store_when_it_was_read(monkeypatch) -> None:
    extract_cache.cache_put(_blank_pdf(), "An invented stored reading.", pages=1)
    text = extract_text_ex(EMAIL, file_extension="msg").text
    assert "[Attachment 2 of 3: scan.pdf]\nAn invented stored reading." in text


def test_the_mechanical_reader_reads_an_email_too() -> None:
    text = extract_text(EMAIL, file_name="Follow-up", file_extension="msg")
    assert text.startswith("Email: Invented: the follow-up visit\n")
    assert "[Attachment 2 of 3: scan.pdf]\n[not read: a scan with no stored reading]" in text


def test_the_bytes_decide_not_the_extension() -> None:
    assert extract_text_ex(EMAIL, file_name="saved", file_extension="").method == METHOD_MSG
    assert is_msg(EMAIL)


def test_a_legacy_word_file_is_still_refused_by_name() -> None:
    doc = build_ole({"WordDocument": b"\0" * 600, "1Table": b"\0" * 64})
    assert not is_msg(doc)
    with pytest.raises(UnsupportedDocumentError, match=r"legacy Office file"):
        extract_text_ex(doc, file_name="old.doc", file_extension="doc")


def test_an_email_the_library_cannot_open_is_refused_by_name(monkeypatch) -> None:
    import extract_msg

    def boom(*_a, **_k):
        raise ValueError("a library error text that must not be passed on")

    monkeypatch.setattr(extract_msg, "Message", boom)
    with pytest.raises(UnsupportedDocumentError) as err:
        extract_text_ex(EMAIL, file_name="Broken email", file_extension="msg")
    assert "Outlook email that could not be read" in str(err.value)
    assert "library error text" not in str(err.value)


def test_open_msg_reads_the_fields_the_job_lanes_use() -> None:
    m = open_msg(EMAIL)
    assert (m.subject, m.sender, m.to, m.cc) == (
        "Invented: the follow-up visit",
        "Pat Example <pat@example.test>",
        "Sam Sample <sam@example.test>",
        "Lee Copy <lee@example.test>",
    )
    assert [(a.name, a.error) for a in m.attachments] == [
        ("letter.docx", None),
        ("scan.pdf", None),
        ("notes.txt", None),
    ]
    assert m.attachments[2].data == b"Invented plain notes."


def test_read_document_returns_the_email(monkeypatch) -> None:
    entries = [{"id": "email-1", "name": "Follow-up", "fileExtension": ".msg"}]
    _matter_client(entries, {"email-1": EMAIL}, monkeypatch)
    read = server.read_document("m-1", "email-1")
    assert read["extraction"] == METHOD_MSG
    assert "error" not in read
    assert "An invented attached letter." in read["text"]


def test_the_record_check_counts_a_saved_email_as_record_not_a_refusal(monkeypatch) -> None:
    assert METHOD_MSG in MECHANICAL_METHODS
    entries = [{"id": "email-1", "name": "Follow-up", "fileExtension": ".msg"}]
    _matter_client(entries, {"email-1": EMAIL}, monkeypatch)
    sources, vision_sources, unextractable = server._collect_matter_sources("m-1")
    assert unextractable == []
    assert vision_sources == []
    assert [name for name, _ in sources] == ["Follow-up"]
