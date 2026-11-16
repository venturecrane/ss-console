"""The connector keeps every page of a firm's PDF, and reads only the scans (wave 5, K1).

The defect: ``extract_text_ex`` decided "scanned" for the WHOLE file on an
average, so one typed cover sheet sent the scans behind it down the text-layer
road, and that road then DROPPED every page it found no text on. The method
came back ``pypdf`` and nothing said five pages were never read.

What these tests hold:

* a typed PDF whose every page has text reads byte for byte as before;
* a blank page is kept as ``[p.N: blank]``;
* a scan behind a typed cover is ``[p.N: scanned page, not read]`` wherever
  vision is not allowed, and where it is (``read_document``) ONLY those pages
  are sent, one call each, up to the page bound, each stored under its own
  page name and marked ``[p.N] (machine transcription)``; a second read pays 0;
* a whole scan keeps today's road, calls and cache entry;
* each caller that branches on the method has a rule for ``mixed``: the record
  check reports it and refuses until ``read_document`` has read those pages, then
  reads them from the store; ``read_document`` names what is left and why; the
  vendor-invoice reader flags it as scanned.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from test_vision_extraction import _matter_client, _read_document_client

from smokeball_connector import extract, extract_cache, letter_pages, mixed_read, server, vision
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


def _sse(text: str, stop: str = "end_turn") -> bytes:
    events = [
        {"type": "message_start", "message": {"id": "msg_1"}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": stop}},
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


def _page_entries(tmp_path, blob: bytes) -> list[str]:
    sha = extract_cache.cache_key(blob)
    return [name for name in _cache_files(tmp_path) if name.startswith(f"{sha}.p")]


# ---- the page-by-page read ---------------------------------------------------


def test_a_typed_pdf_reads_byte_for_byte_as_before(monkeypatch) -> None:
    calls = _counting_vision(monkeypatch)
    result = extract_text_ex(TYPED, file_extension="pdf", allow_vision=True)
    assert result.method == METHOD_PYPDF
    assert result.text == "\n\n".join(letter_pages.page_texts(TYPED))
    assert "[p." not in result.text
    assert result.unread_pages == () and result.transcribed_pages == ()
    assert calls == []


def test_a_short_typed_page_is_text_not_a_scan() -> None:
    """A page under the floor with no image (a page number, a heading) is what
    the page says; it keeps the old, unmarked text."""
    result = extract_text_ex(SHORT_TYPED, file_extension="pdf")
    assert result.method == METHOD_PYPDF
    assert result.text.endswith("Page 2")
    assert "[p." not in result.text


def test_a_blank_page_is_kept_and_marked(monkeypatch) -> None:
    calls = _counting_vision(monkeypatch)
    result = extract_text_ex(TYPED_WITH_BLANKS, file_extension="pdf", allow_vision=True)
    assert result.method == METHOD_PYPDF
    assert result.pages == 4
    assert result.text.startswith("[p.1]\n")
    assert "\n\n[p.2: blank]\n\n[p.3]\n" in result.text
    assert result.text.endswith("[p.4: blank]")
    assert result.unread_pages == ()
    assert calls == [], "a blank page is never sent"


def test_scans_behind_a_typed_cover_are_kept_and_marked_where_vision_is_not_allowed(tmp_path, monkeypatch) -> None:
    calls = _counting_vision(monkeypatch)
    result = extract_text_ex(COVER_AND_SCANS, file_extension="pdf")
    assert result.method == METHOD_MIXED
    assert result.unread_pages == (2, 3, 4)
    assert result.reason is None
    assert result.text.startswith("[p.1]\nCOVER SHEET")
    for n in (2, 3, 4):
        assert f"[p.{n}: scanned page, not read]" in result.text
    assert calls == [], "only a caller that allows vision pays"
    assert _cache_files(tmp_path) == []


def test_only_the_unread_pages_are_read_once_each_and_stored(tmp_path, monkeypatch) -> None:
    calls = _counting_vision(monkeypatch)
    result = extract_text_ex(COVER_AND_SCANS, file_extension="pdf", allow_vision=True)
    assert len(calls) == 3, "one call per scanned page; the typed cover is never sent"
    assert result.method == METHOD_VISION
    assert result.transcribed_pages == (2, 3, 4) and result.unread_pages == ()
    assert result.text.startswith("[p.1]\nCOVER SHEET")
    for n in (2, 3, 4):
        assert f"[p.{n}] (machine transcription)\nIMPRESSION: transcribed." in result.text
    assert "not read" not in result.text
    sha = extract_cache.cache_key(COVER_AND_SCANS)
    version = mixed_read.method_version()
    assert _page_entries(tmp_path, COVER_AND_SCANS) == [f"{sha}.p{n}.{version}.json" for n in (2, 3, 4)]
    assert f"{sha}.json" not in _cache_files(tmp_path), "the whole-file entry is never written for a mixed file"

    again = extract_text_ex(COVER_AND_SCANS, file_extension="pdf", allow_vision=True)
    assert len(calls) == 3, "a second read pays 0"
    assert (again.method, again.text) == (METHOD_VISION_CACHED, result.text)


def test_each_sent_page_is_the_scanned_page_itself(monkeypatch) -> None:
    """The pages sent are the file's own scans, cut out one by one: each request
    carries a one-page PDF drawing the image, and never the typed cover."""
    import base64
    import io

    from pypdf import PdfReader

    calls = _counting_vision(monkeypatch)
    extract_text_ex(STAMPED_SCANS, file_extension="pdf", allow_vision=True)
    assert len(calls) == 2
    for request in calls:
        body = json.loads(request.content)
        assert body["model"] == vision.model()
        assert body["messages"][0]["content"][1]["text"] == vision.INSTRUCTION
        sent = base64.b64decode(body["messages"][0]["content"][0]["source"]["data"])
        reader = PdfReader(io.BytesIO(sent))
        assert len(reader.pages) == 1
        assert "COVER SHEET" not in (reader.pages[0].extract_text() or "")


def test_the_page_bound_is_counted_on_pages_sent_and_held_across_reads(monkeypatch) -> None:
    monkeypatch.setenv("SMOKEBALL_VISION_PAGE_CAP", "2")
    calls = _counting_vision(monkeypatch)
    result = extract_text_ex(COVER_AND_SCANS, file_extension="pdf", allow_vision=True)
    assert len(calls) == 2, "the bound is enforced before any call"
    assert result.method == METHOD_MIXED
    assert result.transcribed_pages == (2, 3) and result.unread_pages == (4,)
    assert result.reason == extract.REASON_OVER_PAGE_CAP
    assert "[p.4: scanned page, not read]" in result.text
    report = result.report()
    assert (report["unreadCount"], report["unreadPages"], report["extractionReason"]) == (1, [4], "over_page_cap")
    again = extract_text_ex(COVER_AND_SCANS, file_extension="pdf", allow_vision=True)
    assert len(calls) == 2, "pages already read count against the bound: a second read sends nothing"
    assert again.unread_pages == (4,)


def test_a_failed_read_stores_nothing_and_leaves_the_pages_unread(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SMOKEBALL_VISION_DISABLED", "1")
    calls = _counting_vision(monkeypatch)
    result = extract_text_ex(COVER_AND_SCANS, file_extension="pdf", allow_vision=True)
    assert calls == []
    assert (result.method, result.reason, result.unread_pages) == (METHOD_MIXED, extract.REASON_DISABLED, (2, 3, 4))
    assert _cache_files(tmp_path) == []


def test_a_stamped_scan_is_a_scan_and_keeps_its_stamp_until_read() -> None:
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
    result = extract_text_ex(COVER_AND_SCANS, file_extension="pdf", allow_vision=True)
    assert (result.method, result.text, result.pages) == (METHOD_VISION_CACHED, reading, 4)
    assert calls == []


def test_the_old_entry_point_keeps_every_page_and_never_reads_a_store(monkeypatch) -> None:
    _counting_vision(monkeypatch)
    extract_text_ex(COVER_AND_SCANS, file_extension="pdf", allow_vision=True)
    text = extract_text(COVER_AND_SCANS, file_extension="pdf")
    assert "[p.2: scanned page, not read]" in text
    assert "machine transcription" not in text


def test_a_whole_scan_keeps_todays_road(tmp_path, monkeypatch) -> None:
    calls = _counting_vision(monkeypatch)
    mechanical = extract_text_ex(WHOLE_SCAN, file_extension="pdf")
    assert (mechanical.method, mechanical.reason, mechanical.text) == (METHOD_NONE_SCANNED, "not_attempted", "")
    assert calls == []
    read = extract_text_ex(WHOLE_SCAN, file_extension="pdf", allow_vision=True)
    assert read.method == METHOD_VISION
    assert len(calls) == 2, "one call per page, exactly as before"
    assert _cache_files(tmp_path) == [f"{extract_cache.cache_key(WHOLE_SCAN)}.json"], "the whole-file entry, as before"


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


def test_the_method_version_names_the_model_and_the_prompt(monkeypatch) -> None:
    base = mixed_read.method_version()
    monkeypatch.setenv("SMOKEBALL_VISION_MODEL", "claude-other-model")
    assert mixed_read.method_version() != base
    monkeypatch.delenv("SMOKEBALL_VISION_MODEL")
    monkeypatch.setattr(vision, "INSTRUCTION", vision.INSTRUCTION + " ")
    assert mixed_read.method_version() != base


#: The calls each fixture makes, read the way ``read_document`` reads: the
#: typed ones none, the whole scan its two pages as today, each mixed one a call
#: per scanned page and nothing for its typed pages.
CALLS_PER_FIXTURE = {
    "typed": 0,
    "typed_with_blanks": 0,
    "cover_and_scans": 3,
    "stamped_scans": 2,
    "drawn_page": 1,
    "whole_scan": 2,
    "short_typed": 0,
}


def test_every_fixture_calls_once_per_unread_page_and_never_again(monkeypatch) -> None:
    """The spend bound across every fixture, with a counting stub at the
    Messages API seam: once per scanned page that had no reading, and 0 on the
    second pass."""
    assert set(CALLS_PER_FIXTURE) == set(FIXTURES)
    calls = _counting_vision(monkeypatch)
    for key, blob in FIXTURES.items():
        before = len(calls)
        extract_text_ex(blob, file_extension="pdf", allow_vision=True)
        assert len(calls) - before == CALLS_PER_FIXTURE[key], key
    first = len(calls)
    for blob in FIXTURES.values():
        extract_text_ex(blob, file_extension="pdf", allow_vision=True)
    assert len(calls) == first, "a second read of any fixture pays 0"


# ---- each caller's rule for ``mixed`` ---------------------------------------


def test_read_document_reads_the_scans_and_names_them_as_transcription(monkeypatch) -> None:
    _read_document_client(COVER_AND_SCANS, monkeypatch)
    calls = _counting_vision(monkeypatch)
    read = server.read_document("m-1", "file-9")
    assert len(calls) == 3
    assert read["extraction"] == METHOD_VISION
    assert read["transcribedPages"] == [2, 3, 4]
    assert "needsHumanRead" not in read and "unreadPages" not in read
    assert read["pageCount"] == 4
    assert "[p.3] (machine transcription)" in read["text"]


def test_read_document_names_what_it_left_unread_and_why(monkeypatch) -> None:
    monkeypatch.setenv("SMOKEBALL_VISION_DISABLED", "1")
    _read_document_client(COVER_AND_SCANS, monkeypatch)
    calls = _counting_vision(monkeypatch)
    read = server.read_document("m-1", "file-9")
    assert calls == []
    assert read["extraction"] == METHOD_MIXED
    assert (read["unreadPages"], read["unreadCount"]) == ([2, 3, 4], 3)
    assert read["extractionReason"] == extract.REASON_DISABLED
    assert read["needsHumanRead"] is True
    assert "[p.3: scanned page, not read]" in read["text"]


def test_read_document_tells_the_agent_what_the_markers_mean() -> None:
    doc = " ".join((server.read_document.__doc__ or "").split())
    assert "``mixed``" in doc
    assert "page exists and was not transcribed" in doc
    assert "(machine transcription)" in doc


_MATTER = [
    {"id": "mixed", "name": "Records Bundle.pdf", "fileExtension": ".pdf"},
    {"id": "typed", "name": "Police Report.pdf", "fileExtension": ".pdf"},
]


def test_the_record_check_reports_a_mixed_file_and_refuses_and_never_reads(monkeypatch) -> None:
    _matter_client(_MATTER, {"mixed": COVER_AND_SCANS, "typed": TYPED}, monkeypatch)
    calls = _counting_vision(monkeypatch)
    sources, vision_sources, unextractable = server._collect_matter_sources("m-1")
    assert [name for name, _ in sources] == ["Police Report.pdf"]
    assert vision_sources == []
    assert unextractable == ["Records Bundle.pdf (scanned pages not read: 2, 3, 4)"]
    assert calls == [], "the record check never transcribes"


def test_the_record_check_reads_the_stored_pages_once_read_document_has_read_them(monkeypatch) -> None:
    _read_document_client(COVER_AND_SCANS, monkeypatch)
    calls = _counting_vision(monkeypatch)
    server.read_document("m-1", "file-9")
    paid = len(calls)
    _matter_client(_MATTER, {"mixed": COVER_AND_SCANS, "typed": TYPED}, monkeypatch)
    sources, vision_sources, unextractable = server._collect_matter_sources("m-1")
    assert unextractable == [], "the refusal clears"
    assert [name for name, _ in vision_sources] == ["Records Bundle.pdf"]
    assert "[p.2] (machine transcription)" in vision_sources[0][1]
    assert [name for name, _ in sources] == ["Police Report.pdf"]
    assert len(calls) == paid, "served from the store at $0"

    import os
    from pathlib import Path

    from smokeball_connector import record_check

    checker = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"
    assert checker.is_file(), "the real checker must be present or this proves nothing"
    monkeypatch.setitem(os.environ, record_check.CHECKER_PATH_ENV, str(checker))
    verdict = record_check.run_record_check(
        "# DRAFT\n\nThe records note that the patient reports the neck pain is improving with therapy.\n",
        sources,
        unextractable=unextractable,
        vision_sources=vision_sources,
    )
    assert verdict.passed, verdict.refusals
    assert "Records Bundle.pdf" in verdict.warnings[0], "named as a machine transcription"


def test_the_record_check_reads_a_typed_file_with_blank_pages_as_record(monkeypatch) -> None:
    entries = [{"id": "typed", "name": "Notes.pdf", "fileExtension": ".pdf"}]
    _matter_client(entries, {"typed": TYPED_WITH_BLANKS}, monkeypatch)
    _counting_vision(monkeypatch)
    sources, _vision, unextractable = server._collect_matter_sources("m-1")
    assert unextractable == []
    assert [name for name, _ in sources] == ["Notes.pdf"]


def test_a_mixed_vendor_invoice_is_flagged_scanned_never_keyed_from_its_cover(monkeypatch) -> None:
    from smokeball_connector import vendor_invoice as vi
    from smokeball_connector.client import SmokeballClient

    url = "https://download.agentmail.to/attachments/att_1?token=tok123"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if str(request.url) == url:
            return httpx.Response(200, content=STAMPED_SCANS)
        return httpx.Response(404)

    calls = _counting_vision(monkeypatch)
    client = SmokeballClient(region="us", environment="staging", client_id="c", client_secret="s", api_key="k")
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    out: dict[str, Any] = vi.read_attachment(client, url, "invoice.pdf")
    assert (out["readable"], out["method"], out["reason"], out["text"]) == (False, METHOD_MIXED, "scanned", "")
    assert vi._source_text(STAMPED_SCANS, "invoice.pdf", None) is None
    extract_text_ex(STAMPED_SCANS, file_extension="pdf", allow_vision=True)  # pages read and stored
    again = vi.read_attachment(client, url, "invoice.pdf")
    assert (again["readable"], again["method"], again["reason"]) == (False, METHOD_VISION_CACHED, "scanned")
    assert len(calls) == 2, "the invoice reader itself never transcribes"


# ---- review fixes: what counts as a blank page, what is stored, one payer ----


def _pypdf_file(middle: bytes | None, *, annots: list[dict[str, Any]] | None = None) -> bytes:
    """Typed page, a middle page with exactly ``middle`` as its content stream
    (None: no content stream), typed page. Built with pypdf, as another
    producer's file would be."""
    import io

    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject

    w = PdfWriter()
    font = w._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
    )

    def page(content: bytes | None) -> Any:
        p = w.add_blank_page(612, 792)
        p[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        if content is not None:
            stream = DecodedStreamObject()
            stream.set_data(content)
            p[NameObject("/Contents")] = w._add_object(stream)
        return p

    page(f"BT /F1 12 Tf 72 700 Td ({PROSE[:80]}) Tj ET".encode())
    mid = page(middle)
    if annots:
        mid[NameObject("/Annots")] = ArrayObject(
            [w._add_object(DictionaryObject({NameObject(k): v for k, v in a.items()})) for a in annots]
        )
    page(f"BT /F1 12 Tf 72 700 Td ({PROSE[80:160]}) Tj ET".encode())
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _link_annotation() -> dict[str, Any]:
    from pypdf.generic import ArrayObject, FloatObject, NameObject

    rect = ArrayObject([FloatObject(v) for v in (72, 72, 200, 90)])
    return {"/Type": NameObject("/Annot"), "/Subtype": NameObject("/Link"), "/Rect": rect}


#: Pages that put no ink on paper, each as a producer writes one.
BLANK_PATTERNS: dict[str, tuple[bytes | None, bool]] = {
    "no content stream": (None, False),
    "save and restore only (q Q)": (b"q Q", False),
    "an empty text object (a reportlab blank page)": (b"BT /F1 12 Tf 14.4 TL ET", False),
    "a white background over the whole page": (b"1 1 1 rg 0 0 612 792 re f", False),
    "a white background in grey": (b"q 1 g 0 0 612 792 re f Q", False),
    "a path built and never painted": (b"0 0 m 100 100 l n", False),
    "a text object showing only spaces": (b"BT /F1 12 Tf 72 700 Td (   ) Tj ET", False),
    "a link annotation with no appearance": (b"q Q", True),
}


@pytest.mark.parametrize("label", sorted(BLANK_PATTERNS))
def test_a_page_with_no_ink_is_blank_and_leaves_a_typed_file_typed(label: str, monkeypatch) -> None:
    middle, link = BLANK_PATTERNS[label]
    blob = _pypdf_file(middle, annots=[_link_annotation()] if link else None)
    assert [letter_pages.page_kind(p) for p in letter_pages.read_pages(blob)] == ["text", "blank", "text"], label
    calls = _counting_vision(monkeypatch)
    result = extract_text_ex(blob, file_extension="pdf", allow_vision=True)
    assert (result.method, result.unread_pages) == (METHOD_PYPDF, ()), label
    assert "[p.2: blank]" in result.text
    assert calls == [], "a blank page is never paid for"


#: Pages that DO put ink on paper, though no text layer says what.
INK_PATTERNS: dict[str, bytes] = {
    # DECIDED: a full-page fill in the default colour (black) is a black page, a
    # redaction or a dark scan, never nothing.
    "a full-page fill in the default colour": b"0 0 612 792 re f",
    "a full-page fill in grey": b"0.5 g 0 0 612 792 re f",
    "a white background with a box on it": b"1 1 1 rg 0 0 612 792 re f 0 g 72 72 100 100 re f",
    "a white fill over part of the page": b"1 1 1 rg 72 72 100 100 re f",
    "a white fill under a scaled page": b"q 2 0 0 2 0 0 cm 1 1 1 rg 0 0 612 792 re f Q",
    "a white fill whose colour was restored away": b"q 1 1 1 rg Q 0 0 612 792 re f",
    "a stroked line": b"0 0 m 100 100 l S",
    "a shading": b"/Sh0 sh",
}


@pytest.mark.parametrize("label", sorted(INK_PATTERNS))
def test_a_page_with_ink_and_no_text_is_never_called_blank(label: str) -> None:
    blob = _pypdf_file(INK_PATTERNS[label])
    assert [letter_pages.page_kind(p) for p in letter_pages.read_pages(blob)] == ["text", "unread", "text"], label
    result = extract_text_ex(blob, file_extension="pdf")
    assert (result.method, result.unread_pages) == (METHOD_MIXED, (2,)), label


def _scripted_vision(monkeypatch: pytest.MonkeyPatch, answers: list[tuple[str, str]], delay: float = 0.0):
    """The Messages API answering call k with ``answers[k]`` (text, stop
    reason), and any call past the list with a transcription."""
    import threading
    import time

    calls: list[httpx.Request] = []
    guard = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        with guard:
            calls.append(request)
            k = len(calls) - 1
        if delay:
            time.sleep(delay)
        text, stop = answers[k] if k < len(answers) else ("IMPRESSION: transcribed.", "end_turn")
        return httpx.Response(200, content=_sse(text, stop))

    monkeypatch.setattr(vision, "_http_client", lambda _t: httpx.Client(transport=httpx.MockTransport(handler)))
    return calls


ONE_SCAN = _pdf([("text", "COVER SHEET " + PROSE), ("scan",), ("text", PROSE)])


def test_an_illegible_page_is_stored_as_read_and_paid_for_once(monkeypatch) -> None:
    """The model's own "cannot read this page" is a reading: stored, marked, and
    never bought again. (Before the fix every read of this file paid again.)"""
    calls = _scripted_vision(monkeypatch, [(vision.NO_CONTENT_SENTINEL, "end_turn")] * 5)
    results = [extract_text_ex(ONE_SCAN, file_extension="pdf", allow_vision=True) for _ in range(3)]
    assert len(calls) == 1
    assert [r.method for r in results] == [METHOD_VISION, METHOD_VISION_CACHED, METHOD_VISION_CACHED]
    assert "[p.2] (machine transcription: no legible content)" in results[0].text
    assert results[0].unread_pages == () and results[0].transcribed_pages == (2,)
    record = extract_text_ex(ONE_SCAN, file_extension="pdf")
    assert (record.method, record.unread_pages) == (METHOD_VISION_CACHED, ()), "the record check sees it read"


def test_one_failing_page_keeps_the_others_and_is_retried_only_to_its_cap(monkeypatch) -> None:
    ok = ("IMPRESSION: transcribed.", "end_turn")
    refused = ("", "refusal")
    calls = _scripted_vision(monkeypatch, [ok, refused, ok, refused, refused])
    first = extract_text_ex(COVER_AND_SCANS, file_extension="pdf", allow_vision=True)
    assert len(calls) == 3
    assert (first.method, first.unread_pages, first.reason) == (METHOD_MIXED, (3,), extract.REASON_REFUSED)
    assert first.transcribed_pages == (2, 4), "the pages that read are kept"
    second = extract_text_ex(COVER_AND_SCANS, file_extension="pdf", allow_vision=True)
    assert len(calls) == 4, "a re-read pays only the failed page"
    assert second.unread_pages == (3,)
    third = extract_text_ex(COVER_AND_SCANS, file_extension="pdf", allow_vision=True)
    assert len(calls) == 2 + mixed_read.PAGE_ATTEMPTS, "the page has used its attempts: never paid again"
    assert (third.unread_pages, third.reason) == ((3,), extract.REASON_REFUSED)
    assert "[p.3: scanned page, not read]" in third.text


def test_a_transport_fault_is_not_counted_against_the_page(monkeypatch) -> None:
    """A fault of the seat or the network says nothing about the page: no
    attempt is recorded, and the next read tries again."""
    from smokeball_connector import extract_cache

    monkeypatch.setattr(vision, "BACKOFF_BASE_SECONDS", 0.0)
    failing: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        failing.append(1)
        return httpx.Response(400, json={"error": {"message": "bad request"}})

    monkeypatch.setattr(vision, "_http_client", lambda _t: httpx.Client(transport=httpx.MockTransport(handler)))
    result = extract_text_ex(ONE_SCAN, file_extension="pdf", allow_vision=True)
    assert (result.method, result.reason) == (METHOD_MIXED, extract.REASON_API_ERROR)
    sha, version = extract_cache.cache_key(ONE_SCAN), mixed_read.method_version()
    assert extract_cache.page_failure_get(sha, 2, version) is None
    calls = _counting_vision(monkeypatch)
    assert extract_text_ex(ONE_SCAN, file_extension="pdf", allow_vision=True).method == METHOD_VISION
    assert len(calls) == 1


def test_two_reads_racing_on_one_file_pay_for_each_page_once(monkeypatch) -> None:
    """Both reads choose their pages, then both reach the gate together; the one
    that waits for the lock looks again and sends nothing."""
    import threading

    calls = _scripted_vision(monkeypatch, [], delay=0.2)
    both_planned = threading.Barrier(2, timeout=10)
    real_gate = vision.gate

    def gate(*args: Any, **kwargs: Any) -> Any:
        both_planned.wait()
        return real_gate(*args, **kwargs)

    monkeypatch.setattr(vision, "gate", gate)
    results: list[Any] = []

    def read() -> None:
        results.append(extract_text_ex(COVER_AND_SCANS, file_extension="pdf", allow_vision=True))

    threads = [threading.Thread(target=read) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert len(results) == 2
    assert len(calls) == 3, "each of the three scanned pages is paid for once"
    assert sorted(r.method for r in results) == [METHOD_VISION, METHOD_VISION_CACHED]
    assert results[0].text == results[1].text
