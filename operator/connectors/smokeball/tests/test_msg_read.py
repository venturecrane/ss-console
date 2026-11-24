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

from smokeball_connector import extract, extract_cache, server, vendor_invoice, vision
from smokeball_connector.extract import (
    MAX_EMAIL_ATTACHMENTS,
    MECHANICAL_METHODS,
    METHOD_MSG,
    METHOD_MSG_PARTIAL,
    METHOD_MSG_TRANSCRIBED,
    UnsupportedDocumentError,
    extract_text,
    extract_text_ex,
)
from smokeball_connector.msg_read import is_msg, open_msg
from smokeball_connector.xlsx_io import build_workbook, normalize_sheets


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


def _email(attachments: list[tuple[str, bytes]], subject: str = "Invented: a short note") -> bytes:
    return build_msg(
        subject=subject,
        sender=("Pat Example", "pat@example.test"),
        to=[("Sam Sample", "sam@example.test")],
        body="An invented line.",
        sent=datetime(2026, 3, 4, 18, 0, tzinfo=timezone.utc),
        attachments=attachments,
    )


#: Every attachment either read from its own text or one that cannot be the
#: firm's record (an inline signature image, the Operator's own workbook).
CLEAN = _email(
    [
        ("letter.docx", _docx("An invented attached letter.")),
        ("image001.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 400),
        (
            "Operator totals.xlsx",
            build_workbook(
                normalize_sheets(
                    [{"name": "S", "columns": [{"header": "Amount", "format": "currency"}], "rows": [[5]]}]
                )
            ),
        ),
    ]
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
    assert result.method == METHOD_MSG_PARTIAL, "the scan was not read, so the email is partial"
    assert result.unread_attachments == ("scan.pdf",)
    assert result.report()["needsHumanRead"] is True
    assert result.report()["unreadAttachments"] == ["scan.pdf"]
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
    result = extract_text_ex(EMAIL, file_extension="msg")
    assert "[Attachment 2 of 3: scan.pdf]\nAn invented stored reading." in result.text
    assert result.method == METHOD_MSG_TRANSCRIBED, "a transcription inside an email is never verbatim record"
    assert METHOD_MSG_TRANSCRIBED not in MECHANICAL_METHODS


def test_the_mechanical_reader_reads_an_email_too() -> None:
    text = extract_text(EMAIL, file_name="Follow-up", file_extension="msg")
    assert text.startswith("Email: Invented: the follow-up visit\n")
    assert "[Attachment 2 of 3: scan.pdf]\n[not read: a scan with no stored reading]" in text


def test_the_bytes_decide_not_the_extension() -> None:
    assert extract_text_ex(CLEAN, file_name="saved", file_extension="").method == METHOD_MSG
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
    _matter_client(entries, {"email-1": CLEAN}, monkeypatch)
    read = server.read_document("m-1", "email-1")
    assert read["extraction"] == METHOD_MSG
    assert "error" not in read
    assert "An invented attached letter." in read["text"]


def test_the_record_check_counts_a_saved_email_as_record_not_a_refusal(monkeypatch) -> None:
    assert METHOD_MSG in MECHANICAL_METHODS
    entries = [{"id": "email-1", "name": "Follow-up", "fileExtension": ".msg"}]
    _matter_client(entries, {"email-1": CLEAN}, monkeypatch)
    sources, vision_sources, unextractable = server._collect_matter_sources("m-1")
    assert unextractable == []
    assert vision_sources == []
    assert [name for name, _ in sources] == ["Follow-up"]


def test_the_operators_own_workbook_and_an_inline_image_are_named_not_counted() -> None:
    result = extract_text_ex(CLEAN, file_extension="msg")
    assert result.method == METHOD_MSG
    assert "[Attachment 3 of 3: Operator totals.xlsx]\n[not read: the Operator's own workbook]" in result.text
    assert "[Attachment 2 of 3: image001.png]\n[not read: an inline image]" in result.text


def test_the_record_check_refuses_an_email_with_an_unread_scan(monkeypatch) -> None:
    entries = [{"id": "email-1", "name": "Follow-up", "fileExtension": ".msg"}]
    _matter_client(entries, {"email-1": EMAIL}, monkeypatch)
    sources, vision_sources, unextractable = server._collect_matter_sources("m-1")
    assert (sources, vision_sources) == ([], [])
    assert unextractable == ["Follow-up (attachments not read: scan.pdf)"]


def test_the_record_check_reads_a_transcribed_scan_in_an_email_as_a_transcription(monkeypatch) -> None:
    extract_cache.cache_put(_blank_pdf(), "An invented stored reading.", pages=1)
    entries = [{"id": "email-1", "name": "Follow-up", "fileExtension": ".msg"}]
    _matter_client(entries, {"email-1": EMAIL}, monkeypatch)
    sources, vision_sources, unextractable = server._collect_matter_sources("m-1")
    assert (sources, unextractable) == ([], [])
    assert [name for name, _ in vision_sources] == ["Follow-up"]


def test_an_attached_email_is_named_and_never_opened() -> None:
    inner = _email([("deep.txt", b"Invented deep text.")], subject="Invented inner")
    nest = inner
    for _ in range(40):  # far deeper than any recursion would survive unbounded
        nest = _email([("forwarded.msg", nest)], subject="Invented forward")
    result = extract_text_ex(nest, file_extension="msg")
    assert result.method == METHOD_MSG_PARTIAL
    assert result.text.endswith("[Attachment 1 of 1: forwarded.msg]\n[not read: an attached email]")
    assert "Invented deep text." not in result.text
    with pytest.raises(UnsupportedDocumentError):
        extract._extract(inner, file_name="inner", file_extension="msg", allow_vision=False, allow_cache=False, depth=1)


def test_an_email_reads_at_most_its_first_attachments() -> None:
    many = _email([(f"note{n}.txt", f"Invented note {n}.".encode()) for n in range(MAX_EMAIL_ATTACHMENTS + 1)])
    result = extract_text_ex(many, file_extension="msg")
    assert result.method == METHOD_MSG_PARTIAL
    assert result.unread_attachments == (f"note{MAX_EMAIL_ATTACHMENTS}.txt",)
    assert f"Invented note {MAX_EMAIL_ATTACHMENTS - 1}." in result.text
    assert f"[not read: past the first {MAX_EMAIL_ATTACHMENTS} attachments]" in result.text


def test_a_saved_email_is_never_read_as_a_vendor_invoice_whatever_its_name() -> None:
    assert vendor_invoice._refused_type(CLEAN, "invoice.pdf") == vendor_invoice.REASON_EMAIL_MESSAGE
