"""Tests for the two combined-post tools.

``test_letter_pages`` covers the page arithmetic. These cover the decisions the
TOOLS make on top of it, and each is written so that removing the line it
defends makes it fail. Four carry the weight:

* ``test_a_scanned_page_anywhere_sends_the_whole_bundle_to_vision`` — the road
  decision has to survive the trip through the tool, not just the helper.
* ``test_nothing_is_transcribed_when_every_page_has_text`` — the money
  falsifier. If this passes with vision stubbed to explode, the free road is
  genuinely free.
* ``test_a_second_letter_cannot_refile_a_page_across_matters`` — the
  cross-client disclosure a resolution token cannot see.
* ``test_a_refused_write_leaves_the_pages_claimable`` — the inverse. A ledger
  that leaks claims on failure turns one failed upload into a bundle that can
  never be filed, and the firm is told nothing.
"""

from __future__ import annotations

import hashlib
import io
from typing import Any

import pytest
from smokeball_connector import letter_pages as lp
from smokeball_connector import letter_tools as lt
from smokeball_connector import resolution_token


def _pdf(page_texts: list[str]) -> bytes:
    """A PDF of ``len(page_texts)`` pages. An empty string means a page with NO
    text layer at all — a photograph of paper, which is the case under test.

    Built by hand rather than with a renderer so the fixture needs no dependency
    beyond pypdf, and so "this page carries no text layer" is a property of the
    bytes rather than of a drawing library's behaviour. Same shape as the
    builder in ``test_vision_extraction``.
    """

    def esc(s: str) -> str:
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    objs: list[bytes] = [b"", b""]  # 1 = catalog, 2 = pages
    kids: list[str] = []
    for text in page_texts:
        content = "BT /F1 10 Tf 40 760 Td 12 TL\n"
        if text:
            content += f"({esc(text)}) Tj T*\n"
        content += "ET"
        stream = content.encode()
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        content_num = len(objs)
        objs.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents "
            + str(content_num).encode()
            + b" 0 R /Resources << /Font << /F1 __FONT__ 0 R >> >> >>"
        )
        kids.append(f"{len(objs)} 0 R")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    font_num = len(objs)
    objs = [o.replace(b"__FONT__", str(font_num).encode()) for o in objs]
    objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = (
        b"<< /Type /Pages /Kids [" + " ".join(kids).encode() + b"] /Count " + str(len(page_texts)).encode() + b" >>"
    )

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n").encode()
    return bytes(out)


DIGITAL = "Dear Counsel: " + ("this page carries a real text layer. " * 12)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """No cache on disk, no live tenant, no resolution store between tests."""
    monkeypatch.setenv("SMOKEBALL_EXTRACT_CACHE_DIR", str(tmp_path / "cache"))
    resolution_token._reset_for_tests()
    lp.FILED_PAGES._filed.clear()


class _Client:
    """The one collaborator the write tool has: it uploads."""

    def __init__(self) -> None:
        self.uploads: list[tuple[str, str, bytes]] = []
        self.fail = False

    def add_file(self, matter_id: str, file_name: str, blob: bytes, folder_id: Any = None) -> dict:
        if self.fail:
            raise RuntimeError("Smokeball said no")
        self.uploads.append((matter_id, file_name, blob))
        return {"fileId": f"file-{len(self.uploads)}"}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> _Client:
    c = _Client()
    monkeypatch.setattr(lt, "_client", lambda: c)
    return c


def _spooled(monkeypatch: pytest.MonkeyPatch, blob: bytes) -> str:
    """Point ``fetch_bytes`` at these bytes and hand back a reference."""
    monkeypatch.setattr(lt, "fetch_bytes", lambda _client, _url: blob)
    return "spool:" + "0" * 32


def _token(matter_id: str) -> str:
    return resolution_token.mint(matter_id, "2024-0117", ("client_name", "date_of_loss"))


# ---- the road decision, through the tool ---------------------------------


def test_nothing_is_transcribed_when_every_page_has_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """The money falsifier: an all-digital bundle must not reach vision.

    Vision is stubbed to raise, so if the road decision regressed to "always
    transcribe" this test fails loudly rather than quietly spending.
    """

    def _explode(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("an all-text bundle must never be transcribed")

    monkeypatch.setattr(lt.vision, "transcribe_pdf", _explode)
    monkeypatch.setattr(lt.vision, "gate", _explode)

    out = lt._read_pages(_pdf([DIGITAL, DIGITAL, DIGITAL]), "post.pdf")
    assert out["readable"] is True
    assert out["method"] == "pypdf"
    assert out["pageCount"] == 3
    assert out["scannedPages"] == 0
    assert out["text"].startswith("[p.1]")
    assert "[p.3]" in out["text"]


def test_a_scanned_page_anywhere_sends_the_whole_bundle_to_vision(monkeypatch: pytest.MonkeyPatch) -> None:
    """One digital cover sheet must not route three scans onto the free road.

    This is the shipped defect ``letter_pages`` exists to close, asserted at the
    tool boundary: what reaches vision is the WHOLE bundle, not the scanned
    pages, so there is one sha, one cache entry and one spend.
    """
    seen: dict[str, Any] = {}

    def _fake(blob: bytes, *, pages: int) -> Any:
        seen["bytes"] = len(blob)
        seen["pages"] = pages
        return lt.vision.VisionOutcome(
            text="\n\n".join(f"[p.{n}]\ntranscribed page {n}" for n in range(1, pages + 1)),
            pages_read=pages,
            stop_reason="end_turn",
        )

    monkeypatch.setattr(lt.vision, "gate", lambda _b, *, pages: None)
    monkeypatch.setattr(lt.vision, "transcribe_pdf", _fake)

    blob = _pdf([DIGITAL, "", "", ""])
    out = lt._read_pages(blob, "post.pdf")
    assert out["readable"] is True
    assert out["method"] == "vision"
    assert out["scannedPages"] == 3
    assert seen["pages"] == 4
    assert seen["bytes"] == len(blob), "vision must receive the whole bundle, not the scanned pages"


def test_a_vision_refusal_reads_nothing_and_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lt.vision, "gate", lambda _b, *, pages: "over_page_cap")
    out = lt._read_pages(_pdf(["", ""]), "post.pdf")
    assert out["readable"] is False
    assert out["reason"] == "over_page_cap"
    assert out["text"] == ""
    assert out["pageCount"] == 2, "the page count is a fact even when the read refused"


def test_transcribed_text_whose_markers_do_not_run_1_to_n_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transcription we cannot number is a document we cannot cut.

    Nothing downstream re-checks the numbering, so accepting this text means a
    page range chosen against the wrong pages.
    """
    monkeypatch.setattr(lt.vision, "gate", lambda _b, *, pages: None)
    monkeypatch.setattr(
        lt.vision,
        "transcribe_pdf",
        lambda _b, *, pages: lt.vision.VisionOutcome(text="[p.1]\nfirst\n\n[p.3]\nthird", pages_read=2),
    )
    out = lt._read_pages(_pdf(["", ""]), "post.pdf")
    assert out["readable"] is False
    assert out["reason"] == "marker_mismatch"
    assert out["method"] is None
    assert out["text"] == ""


def test_a_bundle_too_long_to_read_whole_is_refused_not_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Truncation loses the tail letters INVISIBLY: the partition that comes
    back omits them and the firm is told the filing is complete."""
    monkeypatch.setattr(lt, "MAX_TEXT_CHARS", 50)
    out = lt._read_pages(_pdf([DIGITAL, DIGITAL]), "post.pdf")
    assert out["readable"] is False
    assert out["reason"] == "too_long"
    assert out["text"] == ""
    assert out["pageCount"] == 2


def test_the_unreadable_vocabulary_matches_the_other_attachment_reader() -> None:
    """Two tools read emailed attachments; a skill must not have to know two
    words for the same refusal."""
    from smokeball_connector import vendor_invoice as vi

    assert lt.REASON_UNSUPPORTED == vi.REASON_UNSUPPORTED
    assert lt.REASON_EMPTY == vi.REASON_EMPTY


def test_a_non_pdf_is_refused_before_anything_is_parsed() -> None:
    out = lt._read_pages(b"PK\x03\x04 not a pdf", "post.docx")
    assert out["readable"] is False
    assert out["reason"] == "unsupported"
    assert out["sha256"] == hashlib.sha256(b"PK\x03\x04 not a pdf").hexdigest()


# ---- the write ------------------------------------------------------------


def test_a_letter_is_filed_as_its_own_pages(monkeypatch: pytest.MonkeyPatch, client: _Client) -> None:
    blob = _pdf([DIGITAL, DIGITAL, DIGITAL, DIGITAL])
    url = _spooled(monkeypatch, blob)
    sha = hashlib.sha256(blob).hexdigest()

    out = lt.file_attachment_pages_to_matter(
        matter_id="m-1",
        matter_resolution=_token("m-1"),
        download_url=url,
        file_name="2026-09-22 State Farm",
        sha256=sha,
        first_page=2,
        last_page=3,
    )
    assert out["status"] == "filed"
    assert out["pages"] == {"first": 2, "last": 3}
    assert len(client.uploads) == 1
    _matter, name, uploaded = client.uploads[0]
    assert name == "2026-09-22 State Farm.pdf".replace("-", "-")
    from pypdf import PdfReader

    assert len(PdfReader(io.BytesIO(uploaded)).pages) == 2, "only the letter's own pages are uploaded"


def test_filing_without_a_resolution_creates_nothing(monkeypatch: pytest.MonkeyPatch, client: _Client) -> None:
    blob = _pdf([DIGITAL, DIGITAL])
    url = _spooled(monkeypatch, blob)
    out = lt.file_attachment_pages_to_matter(
        matter_id="m-1",
        matter_resolution="",
        download_url=url,
        file_name="letter",
        sha256=hashlib.sha256(blob).hexdigest(),
        first_page=1,
        last_page=1,
    )
    assert out["status"] == "refused"
    assert out["created"] is False
    assert client.uploads == []


def test_a_resolution_for_another_matter_creates_nothing(monkeypatch: pytest.MonkeyPatch, client: _Client) -> None:
    """The harm this closes is a letter filed on the matter next to the right
    one, which reads as success in every log."""
    blob = _pdf([DIGITAL, DIGITAL])
    url = _spooled(monkeypatch, blob)
    out = lt.file_attachment_pages_to_matter(
        matter_id="m-2",
        matter_resolution=_token("m-1"),
        download_url=url,
        file_name="letter",
        sha256=hashlib.sha256(blob).hexdigest(),
        first_page=1,
        last_page=1,
    )
    assert out["status"] == "refused"
    assert client.uploads == []


def test_bytes_that_changed_since_the_read_create_nothing(monkeypatch: pytest.MonkeyPatch, client: _Client) -> None:
    blob = _pdf([DIGITAL, DIGITAL])
    url = _spooled(monkeypatch, blob)
    out = lt.file_attachment_pages_to_matter(
        matter_id="m-1",
        matter_resolution=_token("m-1"),
        download_url=url,
        file_name="letter",
        sha256="0" * 64,
        first_page=1,
        last_page=1,
    )
    assert out["status"] == "refused"
    assert "bytes changed" in out["reason"]
    assert client.uploads == []


def test_a_range_outside_the_document_creates_nothing(monkeypatch: pytest.MonkeyPatch, client: _Client) -> None:
    blob = _pdf([DIGITAL, DIGITAL])
    url = _spooled(monkeypatch, blob)
    out = lt.file_attachment_pages_to_matter(
        matter_id="m-1",
        matter_resolution=_token("m-1"),
        download_url=url,
        file_name="letter",
        sha256=hashlib.sha256(blob).hexdigest(),
        first_page=2,
        last_page=9,
    )
    assert out["status"] == "refused"
    assert client.uploads == []


def test_a_second_letter_cannot_refile_a_page_across_matters(monkeypatch: pytest.MonkeyPatch, client: _Client) -> None:
    """Two valid resolutions, two different matters, one overlapping page.

    The resolution token cannot see this: it binds a MATTER, and nothing in it
    says page 3 already went somewhere. Filing page 3 onto two matters is a
    cross-client disclosure that looks like success twice.
    """
    blob = _pdf([DIGITAL, DIGITAL, DIGITAL, DIGITAL])
    url = _spooled(monkeypatch, blob)
    sha = hashlib.sha256(blob).hexdigest()
    first = lt.file_attachment_pages_to_matter(
        matter_id="m-1",
        matter_resolution=_token("m-1"),
        download_url=url,
        file_name="letter one",
        sha256=sha,
        first_page=1,
        last_page=3,
    )
    assert first["status"] == "filed"

    second = lt.file_attachment_pages_to_matter(
        matter_id="m-2",
        matter_resolution=_token("m-2"),
        download_url=url,
        file_name="letter two",
        sha256=sha,
        first_page=3,
        last_page=4,
    )
    assert second["status"] == "refused"
    assert "already filed" in second["reason"]
    assert len(client.uploads) == 1


def test_a_refused_write_leaves_the_pages_claimable(monkeypatch: pytest.MonkeyPatch, client: _Client) -> None:
    """The inverse of the ledger. If a failed upload kept its claim, a transient
    Smokeball error would make those pages unfilable for the rest of the
    process and nothing would say so."""
    blob = _pdf([DIGITAL, DIGITAL])
    url = _spooled(monkeypatch, blob)
    sha = hashlib.sha256(blob).hexdigest()

    client.fail = True
    failed = lt.file_attachment_pages_to_matter(
        matter_id="m-1",
        matter_resolution=_token("m-1"),
        download_url=url,
        file_name="letter",
        sha256=sha,
        first_page=1,
        last_page=2,
    )
    assert failed["status"] == "refused"
    assert failed["created"] is False

    client.fail = False
    retried = lt.file_attachment_pages_to_matter(
        matter_id="m-1",
        matter_resolution=_token("m-1"),
        download_url=url,
        file_name="letter",
        sha256=sha,
        first_page=1,
        last_page=2,
    )
    assert retried["status"] == "filed", "a retry after a failed upload must not be blocked by the ledger"


def test_one_resolution_does_not_file_two_letters(monkeypatch: pytest.MonkeyPatch, client: _Client) -> None:
    blob = _pdf([DIGITAL, DIGITAL, DIGITAL, DIGITAL])
    url = _spooled(monkeypatch, blob)
    sha = hashlib.sha256(blob).hexdigest()
    token = _token("m-1")

    assert (
        lt.file_attachment_pages_to_matter(
            matter_id="m-1",
            matter_resolution=token,
            download_url=url,
            file_name="letter one",
            sha256=sha,
            first_page=1,
            last_page=2,
        )["status"]
        == "filed"
    )

    reused = lt.file_attachment_pages_to_matter(
        matter_id="m-1",
        matter_resolution=token,
        download_url=url,
        file_name="letter two",
        sha256=sha,
        first_page=3,
        last_page=4,
    )
    assert reused["status"] == "refused"
    assert len(client.uploads) == 1


def test_the_filename_is_sanitised_on_the_way_out(monkeypatch: pytest.MonkeyPatch, client: _Client) -> None:
    """The name is read off a letter an outside party wrote, and the vendor
    truncates a displayed name at its first period."""
    blob = _pdf([DIGITAL])
    url = _spooled(monkeypatch, blob)
    lt.file_attachment_pages_to_matter(
        matter_id="m-1",
        matter_resolution=_token("m-1"),
        download_url=url,
        file_name="../../09.22.2026 State Farm",
        sha256=hashlib.sha256(blob).hexdigest(),
        first_page=1,
        last_page=1,
    )
    name = client.uploads[0][1]
    assert name.count(".") == 1 and name.endswith(".pdf")
    assert "/" not in name
