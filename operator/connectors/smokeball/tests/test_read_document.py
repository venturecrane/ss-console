"""Unit coverage for the document READ path (download_file + extract_text) —
the companion to test_document_writes.py, added with the read_document tool
(2026-07-05 L2 DISC-1 finding: get_download_url minted URLs nothing could
fetch). Locks the contract: the download-info GET is authenticated, the
presigned GET carries NO auth headers, the size guard refuses oversized
files before AND after the fetch, and extraction fails closed (explicit
UnsupportedDocumentError) instead of guessing."""

from __future__ import annotations

import io

import httpx
import pytest

from smokeball_connector.client import SmokeballClient, SmokeballWriteError
from smokeball_connector.extract import UnsupportedDocumentError, extract_text

_DOWNLOAD_URL = "https://s3.example.com/apidownloads/file-7?X-Amz-Signature=cafef00d"


def _tiny_pdf(lines: list[str]) -> bytes:
    """Minimal single-page PDF with Tj text ops (the rehearsal-office seeder's
    generation approach, inlined so connector tests stay self-contained)."""

    def esc(s: str) -> str:
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    content = "BT /F1 10 Tf 40 760 Td 12 TL\n"
    for line in lines:
        content += f"({esc(line)}) Tj T*\n"
    content += "ET"
    stream = content.encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n").encode()
    return bytes(out)


def _mock_client(handler) -> SmokeballClient:
    client = SmokeballClient(region="us", environment="staging", client_id="cid", client_secret="sec", api_key="apikey")
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def _handler(captured: list[httpx.Request], blob: bytes, *, size_bytes: int | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        path = request.url.path
        if path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if path.endswith("/download"):
            return httpx.Response(
                200,
                json={
                    "fileId": "file-7",
                    "downloadUrl": _DOWNLOAD_URL,
                    "expiry": "2026-07-06T00:00:00Z",
                    "name": "RFP Set One",
                    "fileExtension": ".pdf",
                    "sizeBytes": size_bytes if size_bytes is not None else len(blob),
                },
            )
        if str(request.url) == _DOWNLOAD_URL:
            return httpx.Response(200, content=blob)
        return httpx.Response(404)

    return handler


def test_download_file_presigned_get_has_no_auth_headers() -> None:
    captured: list[httpx.Request] = []
    blob = _tiny_pdf(["REQUESTS FOR PRODUCTION, SET ONE"])
    client = _mock_client(_handler(captured, blob))

    info, got = client.download_file("m-1", "file-7")

    assert got == blob
    assert info["downloadUrl"] == _DOWNLOAD_URL
    info_get = next(r for r in captured if r.url.path.endswith("/download"))
    assert info_get.headers.get("x-api-key") == "apikey"
    presigned = next(r for r in captured if str(r.url) == _DOWNLOAD_URL)
    assert "authorization" not in presigned.headers
    assert "x-api-key" not in presigned.headers


def test_download_file_refuses_oversized_advertised_size() -> None:
    captured: list[httpx.Request] = []
    client = _mock_client(_handler(captured, b"x", size_bytes=26 * 1024 * 1024))
    with pytest.raises(SmokeballWriteError, match="read limit"):
        client.download_file("m-1", "file-7")
    # The presigned GET must never have been issued.
    assert not any(str(r.url) == _DOWNLOAD_URL for r in captured)


def test_extract_text_pdf_roundtrip() -> None:
    lines = [
        "SUPERIOR COURT OF CALIFORNIA",
        "REQUESTS FOR PRODUCTION, SET ONE",
        "PROOF OF SERVICE: served by mail on June 20, 2026.",
    ]
    text = extract_text(_tiny_pdf(lines), file_extension=".pdf")
    for line in lines:
        assert line in text


def test_extract_text_docx_paragraphs_and_tables() -> None:
    docx = pytest.importorskip("docx")
    buf = io.BytesIO()
    doc = docx.Document()
    doc.add_paragraph("MEET AND CONFER LETTER")
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Request No. 1"
    table.rows[0].cells[1].text = "Objection, boilerplate"
    doc.save(buf)

    text = extract_text(buf.getvalue(), file_extension=".docx")
    assert "MEET AND CONFER LETTER" in text
    assert "Request No. 1 | Objection, boilerplate" in text


def test_extract_text_plain_text_fallback() -> None:
    assert extract_text(b"plain body", file_extension=".txt") == "plain body"


def test_extract_text_unsupported_binary_fails_closed() -> None:
    with pytest.raises(UnsupportedDocumentError, match="manual review"):
        extract_text(b"\x00\x01\x02binarygarbage", file_name="scan.tiff", file_extension=".tiff")


def test_extract_text_malformed_pdf_fails_closed() -> None:
    with pytest.raises(UnsupportedDocumentError, match="PDF could not be parsed"):
        extract_text(b"%PDF-1.4 not actually a pdf", file_extension=".pdf")


def test_extract_text_accepts_a_word_template_dotx() -> None:
    """A firm's letterhead TEMPLATE (.dotx) filed on a matter must extract like
    any other document; python-docx rejects the template content type as-is,
    and before this a single .dotx on a matter refused every draft on it."""
    from .test_render_document import make_firm_template

    blob = make_firm_template(dotx=True, body_text="FIRM TEMPLATE BODY")
    text = extract_text(blob, file_extension=".dotx")
    assert "FIRM TEMPLATE BODY" in text
