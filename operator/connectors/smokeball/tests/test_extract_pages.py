"""The connector keeps every page of a firm's PDF (wave 5, K1).

The defect: ``extract_text_ex`` decided "scanned" for the WHOLE file on an
average, so one typed cover sheet sent the scans behind it down the text-layer
road, and that road then DROPPED every page it found no text on. The method
came back ``pypdf`` and nothing said five pages were never read.

What these tests hold:

* a typed PDF whose every page has text reads byte for byte as before;
* a blank page is kept as ``[p.N: blank]`` and a scan behind a typed cover as
  ``[p.N: scanned page, not read]``, with method ``mixed`` and the pages named;
* a whole scan keeps today's road (the cache, then vision under its cap);
* NOTHING here starts a paid call or writes a cache entry for a mixed file:
  ``test_no_fixture_starts_a_new_vision_call`` counts every request at the
  Messages API seam across every fixture;
* each caller that branches on the method has a rule for ``mixed``: the record
  check reports it and refuses, ``read_document`` names the unread pages, the
  vendor-invoice reader flags it as scanned.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from test_vision_extraction import _forbid_http, _matter_client, _read_document_client

from smokeball_connector import extract, extract_cache, letter_pages, server, vision
from smokeball_connector.extract import (
    METHOD_MIXED,
    METHOD_NONE_SCANNED,
    METHOD_PYPDF,
    METHOD_VISION,
    METHOD_VISION_CACHED,
    extract_text,
    extract_text_ex,
)

# ---- fixtures ---------------------------------------------------------------

#: A page: ("text", body) | ("scan",) | ("stamp", body): an image under a short
#: text layer (a Bates number on a scan) | ("blank",): nothing at all, no content
#: stream | ("drawn",): a content stream that paints, with no text and no image.
Page = tuple[str, ...]

PROSE = (
    "The patient was seen in the clinic today for a follow up visit and reports that the neck pain is improving "
    "with therapy; the plan is to continue the current care."
)


def _esc(s: str) -> str:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _pdf(pages: list[Page]) -> bytes:
    """A PDF built by hand: text pages carry a real text layer, a scan carries a
    one-pixel image XObject drawn over the page, a blank page has no /Contents.
    EVERY page declares the image in its resources, as a file whose pages share
    one resource dictionary does; only a scan draws it, and only drawing counts."""
    objs: list[bytes] = [b"", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    image = b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8 /Length 1 >>\nstream\n\x80\nendstream"
    objs.append(image)
    kids: list[str] = []
    for spec in pages:
        kind = spec[0]
        ops: list[str] = []
        if kind in ("scan", "stamp"):
            ops.append("q 612 0 0 792 0 0 cm /Im1 Do Q")
        if kind in ("text", "stamp"):
            ops.append(f"BT /F1 10 Tf 40 760 Td ({_esc(spec[1])}) Tj ET")
        if kind == "drawn":
            ops.append("10 10 200 300 re f")
        contents = b""
        if ops:
            stream = "\n".join(ops).encode()
            objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
            contents = b" /Contents " + str(len(objs)).encode() + b" 0 R"
        objs.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
            + contents
            + b" /Resources << /Font << /F1 3 0 R >> /XObject << /Im1 4 0 R >> >> >>"
        )
        kids.append(f"{len(objs)} 0 R")
    objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = b"<< /Type /Pages /Kids [" + " ".join(kids).encode() + b"] /Count " + str(len(pages)).encode() + b" >>"
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out)


TYPED = _pdf([("text", PROSE), ("text", PROSE.replace("today", "again"))])
TYPED_WITH_BLANKS = _pdf([("text", PROSE), ("blank",), ("text", PROSE), ("blank",)])
COVER_AND_SCANS = _pdf([("text", "COVER SHEET " + PROSE), ("scan",), ("scan",), ("scan",)])
STAMPED_SCANS = _pdf([("text", "COVER SHEET " + PROSE), ("stamp", "SMITH 000123"), ("scan",)])
DRAWN_PAGE = _pdf([("text", PROSE), ("drawn",)])
WHOLE_SCAN = _pdf([("scan",), ("scan",)])
SHORT_TYPED = _pdf([("text", PROSE), ("text", "Page 2")])

FIXTURES: dict[str, bytes] = {
    "typed": TYPED,
    "typed_with_blanks": TYPED_WITH_BLANKS,
    "cover_and_scans": COVER_AND_SCANS,
    "stamped_scans": STAMPED_SCANS,
    "drawn_page": DRAWN_PAGE,
    "whole_scan": WHOLE_SCAN,
    "short_typed": SHORT_TYPED,
}


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv(extract_cache.CACHE_DIR_ENV, str(tmp_path / "vision-cache"))
    monkeypatch.delenv(extract_cache.CACHE_MAX_BYTES_ENV, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")
    for name in ("SMOKEBALL_VISION_DISABLED", "SMOKEBALL_VISION_MODEL", "SMOKEBALL_VISION_PAGE_CAP"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SMOKEBALL_VISION_WORKERS", "1")


def _sse(text: str) -> bytes:
    events = [
        {"type": "message_start", "message": {"id": "msg_1"}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
        {"type": "message_stop"},
    ]
    return b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)


def _counting_vision(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """The Messages API at the connector's HTTP seam: answers every page, and
    records every request, so a test counts exactly what would have been paid."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=_sse("IMPRESSION: transcribed."))

    monkeypatch.setattr(vision, "_http_client", lambda _t: httpx.Client(transport=httpx.MockTransport(handler)))
    return calls


def _cache_files(tmp_path) -> list[str]:
    root = tmp_path / "vision-cache"
    return sorted(p.name for p in root.glob("*.json")) if root.is_dir() else []


# ---- the page-by-page read ---------------------------------------------------


def test_a_typed_pdf_reads_byte_for_byte_as_before() -> None:
    result = extract_text_ex(TYPED, file_extension="pdf", allow_vision=True)
    assert result.method == METHOD_PYPDF
    assert result.text == "\n\n".join(letter_pages.page_texts(TYPED))
    assert "[p." not in result.text
    assert result.unread_pages == ()


def test_a_short_typed_page_is_text_not_a_scan() -> None:
    """A page under the floor with no image (a page number, a heading) is what
    the page says; it keeps the old, unmarked text."""
    result = extract_text_ex(SHORT_TYPED, file_extension="pdf")
    assert result.method == METHOD_PYPDF
    assert result.text.endswith("Page 2")
    assert "[p." not in result.text


def test_a_blank_page_is_kept_and_marked() -> None:
    result = extract_text_ex(TYPED_WITH_BLANKS, file_extension="pdf")
    assert result.method == METHOD_PYPDF
    assert result.pages == 4
    assert result.text.startswith("[p.1]\n")
    assert "\n\n[p.2: blank]\n\n[p.3]\n" in result.text
    assert result.text.endswith("[p.4: blank]")
    assert result.unread_pages == ()


def test_scans_behind_a_typed_cover_are_kept_and_marked_not_read(tmp_path, monkeypatch) -> None:
    calls = _counting_vision(monkeypatch)
    result = extract_text_ex(COVER_AND_SCANS, file_extension="pdf", allow_vision=True)
    assert result.method == METHOD_MIXED
    assert result.unread_pages == (2, 3, 4)
    assert result.reason is None
    assert result.text.startswith("[p.1]\nCOVER SHEET")
    for n in (2, 3, 4):
        assert f"[p.{n}: scanned page, not read]" in result.text
    assert calls == [], "a mixed file never calls vision in this release"
    assert _cache_files(tmp_path) == [], "and writes no cache entry"


def test_a_stamped_scan_is_a_scan_and_keeps_its_stamp() -> None:
    result = extract_text_ex(STAMPED_SCANS, file_extension="pdf")
    assert result.method == METHOD_MIXED
    assert result.unread_pages == (2, 3)
    assert "[p.2: scanned page, not read]\nSMITH 000123" in result.text


def test_a_drawn_page_with_no_text_is_never_called_blank() -> None:
    """Something is painted on it and no text layer says what: not read."""
    result = extract_text_ex(DRAWN_PAGE, file_extension="pdf")
    assert result.method == METHOD_MIXED
    assert result.unread_pages == (2,)


def test_a_cached_reading_of_a_mixed_file_is_served_for_nothing(monkeypatch) -> None:
    """A bundle read page by page (``read_attachment_pages``) is cached under its
    bytes. That reading covers the scans, so it is served, as a transcription."""
    reading = "[p.1]\nCOVER SHEET\n\n[p.2]\nA\n\n[p.3]\nB\n\n[p.4]\nC"
    extract_cache.cache_put(COVER_AND_SCANS, reading, pages=4)
    calls = _counting_vision(monkeypatch)
    result = extract_text_ex(COVER_AND_SCANS, file_extension="pdf")
    assert (result.method, result.text, result.pages) == (METHOD_VISION_CACHED, reading, 4)
    assert calls == []


def test_the_old_entry_point_keeps_every_page_too_and_never_reads_the_cache() -> None:
    extract_cache.cache_put(COVER_AND_SCANS, "[p.1]\ncached", pages=4)
    text = extract_text(COVER_AND_SCANS, file_extension="pdf")
    assert "[p.2: scanned page, not read]" in text


def test_a_whole_scan_keeps_todays_road(tmp_path, monkeypatch) -> None:
    calls = _counting_vision(monkeypatch)
    mechanical = extract_text_ex(WHOLE_SCAN, file_extension="pdf")
    assert (mechanical.method, mechanical.reason, mechanical.text) == (METHOD_NONE_SCANNED, "not_attempted", "")
    assert calls == []
    read = extract_text_ex(WHOLE_SCAN, file_extension="pdf", allow_vision=True)
    assert read.method == METHOD_VISION
    assert len(calls) == 2, "one call per page, exactly as before"
    assert len(_cache_files(tmp_path)) == 1


def test_the_no_text_rule_lives_in_one_place() -> None:
    """``page_needs_vision`` starts from the same rule the whole-file average
    uses, so the two roads agree about what a page with no text is."""
    floor = extract.SCANNED_CHARS_PER_PAGE
    assert extract.page_has_text("x" * floor) and not extract.page_has_text("x" * (floor - 1))
    assert letter_pages.page_needs_vision("x" * (floor - 1)) is True
    assert letter_pages.page_needs_vision("x" * floor) is False
    assert extract.file_looks_scanned(["x" * (floor * 2 - 1), ""]) is True
    assert extract.file_looks_scanned(["x" * (floor * 2), ""]) is False
    assert extract.file_looks_scanned([]) is False


def test_no_fixture_starts_a_new_vision_call(monkeypatch) -> None:
    """The release's spend bound: across every fixture, read the way
    ``read_document`` reads (``allow_vision=True``), the only calls are the
    whole scan's two pages, which today's code already makes."""
    calls = _counting_vision(monkeypatch)
    methods: dict[str, str] = {}
    for key, blob in FIXTURES.items():
        before = len(calls)
        methods[key] = extract_text_ex(blob, file_extension="pdf", allow_vision=True).method
        if key != "whole_scan":
            assert len(calls) == before, f"{key} started a vision call"
    assert len(calls) == 2
    assert methods["whole_scan"] == METHOD_VISION
    assert {methods[k] for k in ("cover_and_scans", "stamped_scans", "drawn_page")} == {METHOD_MIXED}


# ---- each caller's rule for ``mixed`` ---------------------------------------


def test_read_document_names_the_unread_pages_and_asks_for_a_person(monkeypatch) -> None:
    _read_document_client(COVER_AND_SCANS, monkeypatch)
    no_http = _forbid_http(monkeypatch)
    read = server.read_document("m-1", "file-9")
    assert read["extraction"] == METHOD_MIXED
    assert read["unreadPages"] == [2, 3, 4]
    assert read["needsHumanRead"] is True
    assert read["pageCount"] == 4
    assert "[p.3: scanned page, not read]" in read["text"]
    assert no_http == [], "read_document starts no vision call for a mixed file"


def test_read_document_tells_the_agent_what_a_not_read_marker_means() -> None:
    doc = server.read_document.__doc__ or ""
    assert "``mixed``" in doc
    assert "page exists and was not transcribed" in doc


def test_the_record_check_reports_a_mixed_file_and_refuses(monkeypatch) -> None:
    entries = [
        {"id": "mixed", "name": "Records Bundle.pdf", "fileExtension": ".pdf"},
        {"id": "typed", "name": "Police Report.pdf", "fileExtension": ".pdf"},
    ]
    _matter_client(entries, {"mixed": COVER_AND_SCANS, "typed": TYPED}, monkeypatch)
    no_http = _forbid_http(monkeypatch)
    sources, vision_sources, unextractable = server._collect_matter_sources("m-1")
    assert [name for name, _ in sources] == ["Police Report.pdf"]
    assert vision_sources == []
    assert unextractable == ["Records Bundle.pdf (scanned pages not read: 2, 3, 4)"]
    assert no_http == []


def test_the_record_check_reads_a_typed_file_with_blank_pages_as_record(monkeypatch) -> None:
    entries = [{"id": "typed", "name": "Notes.pdf", "fileExtension": ".pdf"}]
    _matter_client(entries, {"typed": TYPED_WITH_BLANKS}, monkeypatch)
    _forbid_http(monkeypatch)
    sources, _vision, unextractable = server._collect_matter_sources("m-1")
    assert unextractable == []
    assert [name for name, _ in sources] == ["Notes.pdf"]


def test_a_mixed_vendor_invoice_is_flagged_scanned_never_keyed_from_its_cover(monkeypatch) -> None:
    from smokeball_connector import vendor_invoice as vi

    url = "https://download.agentmail.to/attachments/att_1?token=tok123"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if str(request.url) == url:
            return httpx.Response(200, content=STAMPED_SCANS)
        return httpx.Response(404)

    from smokeball_connector.client import SmokeballClient

    client = SmokeballClient(region="us", environment="staging", client_id="c", client_secret="s", api_key="k")
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    out: dict[str, Any] = vi.read_attachment(client, url, "invoice.pdf")
    assert (out["readable"], out["method"], out["reason"], out["text"]) == (False, METHOD_MIXED, "scanned", "")
    assert vi._source_text(STAMPED_SCANS, "invoice.pdf", None) is None
