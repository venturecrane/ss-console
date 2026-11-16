"""Text extraction for matter documents fetched via ``read_document``.

Bounded, dependency-light, and deliberately dumb: the goal is to hand the
agent the document's TEXT as data — never to render, execute, or interpret
anything inside it. Supported types are the three that cover a law office's
matter folder (PDF, DOCX, plain text), plus Excel workbooks (``xlsx_io``);
everything else returns an explicit
unsupported error so the skill can fail closed instead of guessing.

SCANNED PAPER (ss#2464). A law firm's record is half photocopies: on the
census matter, 24 of 104 PDFs carry no text layer at all, and every one of
them is clinical — MRI reports, office notes, the X-ray reads. pypdf returns
``""`` for those, which used to leave ``read_document`` reporting
``total_chars: 0`` with no error: a skill could not tell an empty document
from an unreadable one. Two things changed here, and the second is the whole
discipline:

1. ``extract_text_ex`` returns an :class:`ExtractResult` that NAMES the path
   the text came from (or the reason there is none). A marker is never text —
   ``ExtractResult.text`` is empty for every non-success method, and ``reason``
   is drawn from a closed set, never from an exception string or model prose.
2. A scanned PDF can be TRANSCRIBED by a vision read (``vision.py``), but only
   when the caller asks for it (``allow_vision=True``, which today only
   ``read_document`` passes). Transcription is an explicit act, never ambient:
   the drafting record check consumes an existing cached transcription and
   never initiates one, so a scanned matter's draft path still opens only
   after a human deliberately read each scan.

A PDF THAT IS NOT A WHOLE SCAN is read page by page (wave 5, K1) and no page is
dropped: a typed document whose every page has text reads exactly as it always
did; otherwise each page is marked, a blank one ``[p.N: blank]`` and a scan
behind a typed cover ``[p.N: scanned page, not read]`` (method ``mixed``).
Those scans are never transcribed here and never cached: reading them is paid
work, and switching it on is a separate decision.

``extract_text`` (the original) keeps its exact signature and behaviour and
NEVER reaches the network. It has callers outside this connector — the voice
corpus builders (``operator/bin/voice-fetch-corpus.py``,
``voice-survey-corpus.py``) and the pilot L2 driver — and a corpus builder that
silently started billing model calls per document would be a nasty surprise.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .xlsx_io import OLE_MAGIC, OPERATOR_CREATOR, is_pptx, is_xlsx, xlsx_creator, xlsx_text

if TYPE_CHECKING:
    from .letter_pages import PdfPage

_PDF_MAGIC = b"%PDF"
_ZIP_MAGIC = b"PK\x03\x04"

# ---- The closed vocabularies -------------------------------------------------
# A caller branches on these, never on text truthiness: "empty string" is the
# ambiguity this module exists to remove.

METHOD_PYPDF = "pypdf"
METHOD_DOCX = "docx"
METHOD_PLAIN = "plain"
METHOD_VISION = "vision"
METHOD_VISION_CACHED = "vision_cached"
METHOD_NONE_SCANNED = "none_scanned"
#: A PDF whose typed pages were read from their text layer and which also holds
#: scanned pages nobody has transcribed. Every page is in the text, each scanned
#: one as ``[p.N: scanned page, not read]``, and ``ExtractResult.unread_pages``
#: names them. It is never the firm's whole record: a caller treats the unread
#: pages the way it treats an unread scan (wave 5, K1).
METHOD_MIXED = "mixed"
METHOD_XLSX = "xlsx"
#: A workbook the Operator itself built (``add_workbook``). Readable like any
#: other, but never counted as the firm's record by the drafting check: the
#: Operator's own figures must not be able to validate a later draft.
METHOD_XLSX_OPERATOR = "xlsx_operator"

METHODS = frozenset(
    {
        METHOD_PYPDF,
        METHOD_DOCX,
        METHOD_PLAIN,
        METHOD_VISION,
        METHOD_VISION_CACHED,
        METHOD_NONE_SCANNED,
        METHOD_MIXED,
        METHOD_XLSX,
        METHOD_XLSX_OPERATOR,
    }
)

#: The roads whose text is the document's own, mechanically read, and so may
#: stand as the firm's record in a drafting check.
MECHANICAL_METHODS = (METHOD_PYPDF, METHOD_DOCX, METHOD_PLAIN, METHOD_XLSX)

#: Why a scanned PDF produced no text. Closed set — never an exception message
#: (which can carry a URL or a credential fragment) and never model output.
REASON_NOT_ATTEMPTED = "not_attempted"  # caller passed allow_vision=False
REASON_NO_CREDENTIAL = "no_credential"
REASON_DISABLED = "disabled"
REASON_OVER_PAGE_CAP = "over_page_cap"
REASON_OVER_BYTE_CAP = "over_byte_cap"
REASON_API_ERROR = "api_error"
REASON_TRUNCATED = "truncated"
REASON_INCOMPLETE = "incomplete_transcription"
# The model declined the page (stop_reason "refusal", e.g. a safety classifier
# on a toxicology report). Distinct from truncation so a refusal is never
# misread as a page that ran out of room.
REASON_REFUSED = "model_refused"
# Another document was being transcribed and did not finish within the wait
# (``vision.lock_timeout``). Nothing was sent; the same read can simply be asked
# again, and a cached neighbour costs nothing.
REASON_BUSY = "busy"

REASONS = frozenset(
    {
        REASON_BUSY,
        REASON_NOT_ATTEMPTED,
        REASON_NO_CREDENTIAL,
        REASON_DISABLED,
        REASON_OVER_PAGE_CAP,
        REASON_OVER_BYTE_CAP,
        REASON_API_ERROR,
        REASON_TRUNCATED,
        REASON_INCOMPLETE,
        REASON_REFUSED,
    }
)

#: A PDF page carrying fewer than this many characters of extracted text is
#: paper, not a document: scanner artefacts, a stamped exhibit number, a fax
#: header. THE connector's one "no text" number: :func:`page_has_text` applies
#: it to one page, :func:`file_looks_scanned` averages it over a file, and
#: ``letter_pages.page_needs_vision`` starts from :func:`page_has_text`.
SCANNED_CHARS_PER_PAGE = 15


def page_has_text(text: str) -> bool:
    """True when one page's text layer carries at least the floor of text."""
    return len(text.strip()) >= SCANNED_CHARS_PER_PAGE


def file_looks_scanned(page_texts: list[str]) -> bool:
    """True when the file's text layer yields ~nothing per page on AVERAGE: the
    same floor as :func:`page_has_text`, over the whole file. This decides the
    whole-scan road (the cache, then vision under its page cap) and is exactly
    the rule that road has always used; a file it passes over is then read page
    by page (``_typed_pdf_result``), so a typed cover no longer hides the scans
    behind it."""
    if not page_texts:
        return False
    joined = "\n\n".join(t.strip() for t in page_texts if t.strip())
    return len(joined.strip()) < SCANNED_CHARS_PER_PAGE * len(page_texts)


class UnsupportedDocumentError(RuntimeError):
    """The file type has no text-extraction path. The message names the type so
    the agent can surface 'needs manual review' instead of guessing content."""


@dataclass(frozen=True)
class ExtractResult:
    """What came out, and by which road.

    ``text`` is empty for every non-success method. ``reason`` is set only for
    ``none_scanned`` and is one of :data:`REASONS`. ``pages`` is the PDF page
    count when known (it is what an attorney needs to judge an over-cap refusal)
    and None for every other type. ``unread_pages`` (1-based) is set only for
    ``mixed``: the scanned pages whose text is a marker, not a reading."""

    text: str
    method: str
    reason: str | None = None
    pages: int | None = None
    unread_pages: tuple[int, ...] = ()

    def report(self) -> dict[str, Any]:
        """The fields ``read_document`` returns beside the text, in its order:
        the road, the PDF page count, why there is no text, whether a person
        must read it (a scan this tool could not read, or a ``mixed`` file),
        and which pages a ``mixed`` file did not read."""
        out: dict[str, Any] = {"extraction": self.method}
        if self.pages is not None:
            out["pageCount"] = self.pages
        if self.reason is not None:
            out["extractionReason"] = self.reason
        if self.method in (METHOD_NONE_SCANNED, METHOD_MIXED):
            out["needsHumanRead"] = True
        if self.unread_pages:
            out["unreadPages"] = list(self.unread_pages)
        return out

    def unread_note(self) -> str:
        """The scanned pages not read, said the way a refusal names them."""
        return "scanned pages not read: " + ", ".join(str(n) for n in self.unread_pages)


def extract_text(blob: bytes, *, file_name: str = "", file_extension: str = "") -> str:
    """Extract plain text from a document blob. Detection is magic-bytes first
    (the extension metadata is client-supplied and can lie), extension second.

    MECHANICAL PATHS ONLY. This function never makes a network call and never
    consults the transcription cache — a scanned PDF returns ``""`` here exactly
    as it always has. Callers that want the vision fallback ask for it
    explicitly through :func:`extract_text_ex`."""
    return _extract(
        blob,
        file_name=file_name,
        file_extension=file_extension,
        allow_vision=False,
        allow_cache=False,
    ).text


def extract_text_ex(
    blob: bytes,
    *,
    file_name: str = "",
    file_extension: str = "",
    allow_vision: bool = False,
) -> ExtractResult:
    """Extract text and NAME the path it came from.

    Raises :class:`UnsupportedDocumentError` for a type with no extraction path,
    exactly as :func:`extract_text` does — an unsupported file is a refusal, not
    a result, and both callers already handle it that way.

    ``allow_vision`` gates the transcription of a scanned PDF. It is an explicit
    act: only ``read_document`` passes True. With it False a scanned PDF still
    consults the on-disk transcription cache (a read that costs nothing and
    cannot invent anything) and otherwise returns
    ``none_scanned(not_attempted)``."""
    return _extract(
        blob,
        file_name=file_name,
        file_extension=file_extension,
        allow_vision=allow_vision,
        allow_cache=True,
    )


def _extract(
    blob: bytes,
    *,
    file_name: str,
    file_extension: str,
    allow_vision: bool,
    allow_cache: bool,
) -> ExtractResult:
    """The one dispatch both public entry points share, so the mechanical
    detection can never drift between them."""
    ext = (file_extension or "").lower().lstrip(".")
    if blob.startswith(_PDF_MAGIC) or ext == "pdf":
        return _pdf_result(blob, allow_vision=allow_vision, allow_cache=allow_cache)
    if blob.startswith(_ZIP_MAGIC) and _office_kind(blob) is not None:
        return _office_result(blob, file_name)
    if blob.startswith(OLE_MAGIC):
        raise UnsupportedDocumentError(
            f"{file_name or 'document'!r} is a legacy Office file (.xls/.doc/.msg) or an encrypted one; "
            "needs manual review"
        )
    if blob.startswith(_ZIP_MAGIC) and (ext in ("docx", "dotx", "docm", "dotm", "") or _looks_like_docx(blob)):
        return ExtractResult(_docx_text(blob), METHOD_DOCX)
    if ext in ("txt", "text", "md", "csv", "log", "eml"):
        return ExtractResult(blob.decode("utf-8", "replace"), METHOD_PLAIN)
    media_type = _image_media_type(blob, ext)
    if media_type is not None:
        return _image_result(blob, media_type, file_name, allow_vision=allow_vision, allow_cache=allow_cache)
    # Last resort: treat as text only when it plausibly IS text (no NUL bytes
    # in the first 4KB); otherwise refuse explicitly.
    if b"\x00" not in blob[:4096]:
        return ExtractResult(blob.decode("utf-8", "replace"), METHOD_PLAIN)
    raise UnsupportedDocumentError(
        f"no text-extraction path for {file_name or 'document'!r} "
        f"(extension {file_extension or 'unknown'!r}); needs manual review"
    )


def _image_media_type(blob: bytes, ext: str) -> str | None:
    """The image type a photo of a document is, by its bytes; None when it is
    not an image. HEIC (an iPhone's default) is named so it can be refused by
    name rather than read as text."""
    if blob.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if blob.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if blob.startswith(b"GIF8"):
        return "image/gif"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "image/webp"
    if blob[4:8] == b"ftyp" and blob[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"hevc"):
        return "image/heic"
    if ext in ("heic", "heif"):
        return "image/heic"
    return None


def _image_result(
    blob: bytes, media_type: str, file_name: str, *, allow_vision: bool, allow_cache: bool
) -> ExtractResult:
    """A PHOTOGRAPH of a document (a card, a license, an estimate): the cache,
    then one vision call. Same contract as a scanned PDF: a photo nobody could
    read comes back empty with its reason, never as an exception or a guess."""
    if media_type == "image/heic":
        raise UnsupportedDocumentError(
            f"{file_name or 'document'!r} is a HEIC photo, which cannot be read here; "
            "it needs to be saved as JPEG or scanned to PDF"
        )
    if not allow_cache:
        return ExtractResult("", METHOD_NONE_SCANNED, reason=REASON_NOT_ATTEMPTED, pages=1)

    from .extract_cache import cache_get, cache_put

    cached = cache_get(blob)
    if cached is not None:
        return ExtractResult(cached, METHOD_VISION_CACHED, pages=1)
    if not allow_vision:
        return ExtractResult("", METHOD_NONE_SCANNED, reason=REASON_NOT_ATTEMPTED, pages=1)

    from .vision import transcribe_image

    outcome = transcribe_image(blob, media_type=media_type)
    if outcome.reason is not None:
        return ExtractResult("", METHOD_NONE_SCANNED, reason=outcome.reason, pages=1)
    cache_put(blob, outcome.text, pages=1)
    return ExtractResult(outcome.text, METHOD_VISION, pages=1)


def _pdf_result(blob: bytes, *, allow_vision: bool, allow_cache: bool) -> ExtractResult:
    """The PDF road: a whole scan goes to the cache, then vision; any other
    file is read page by page from its text layer, every page kept."""
    pages = _pdf_pages(blob)
    count = len(pages)
    if not file_looks_scanned([p.text for p in pages]):
        return _typed_pdf_result(blob, pages, allow_cache=allow_cache)
    if not allow_cache:
        return ExtractResult("", METHOD_NONE_SCANNED, reason=REASON_NOT_ATTEMPTED, pages=count)

    from .extract_cache import cache_get, cache_put

    cached = cache_get(blob)
    if cached is not None:
        return ExtractResult(cached, METHOD_VISION_CACHED, pages=count)
    if not allow_vision:
        return ExtractResult("", METHOD_NONE_SCANNED, reason=REASON_NOT_ATTEMPTED, pages=count)

    from .vision import transcribe_pdf

    outcome = transcribe_pdf(blob, pages=count)
    if outcome.reason is not None:
        return ExtractResult("", METHOD_NONE_SCANNED, reason=outcome.reason, pages=count)
    cache_put(blob, outcome.text, pages=count)
    return ExtractResult(outcome.text, METHOD_VISION, pages=count)


def _typed_pdf_result(blob: bytes, pages: list[PdfPage], *, allow_cache: bool) -> ExtractResult:
    """A PDF with a text layer, read page by page at no cost, NO page dropped.

    Until wave 5 (K1) this road joined the pages that had text and dropped the
    rest, so the scans behind a typed cover sheet vanished with method ``pypdf``
    and nothing to say so. Now each page is decided on its own
    (``letter_pages.page_kind``):

    * every page has text: the text exactly as before, unmarked, so a typed
      document reads byte for byte as it always did;
    * some pages are blank (no image, nothing drawn): every page is marked
      ``[p.N]``, a blank one ``[p.N: blank]``, method ``pypdf``;
    * some pages are scans with no text: the transcription cache is consulted
      (free); with no entry, each such page is ``[p.N: scanned page, not read]``
      and the method is ``mixed``. Vision is NEVER called here and nothing is
      cached: paying to read those pages is a separate decision."""
    from .letter_pages import PAGE_TEXT, PAGE_UNREAD, compose_kept, page_kind

    count = len(pages)
    kinds = [page_kind(p) for p in pages]
    if all(k == PAGE_TEXT for k in kinds):
        return ExtractResult("\n\n".join(p.text for p in pages), METHOD_PYPDF, pages=count)
    unread = tuple(n for n, kind in enumerate(kinds, start=1) if kind == PAGE_UNREAD)
    if not unread:
        return ExtractResult(compose_kept(pages), METHOD_PYPDF, pages=count)
    if allow_cache:
        from .extract_cache import cache_get

        cached = cache_get(blob)
        if cached is not None:
            return ExtractResult(cached, METHOD_VISION_CACHED, pages=count)
    return ExtractResult(compose_kept(pages), METHOD_MIXED, pages=count, unread_pages=unread)


def _office_kind(blob: bytes) -> str | None:
    """``"xlsx"`` or ``"pptx"`` for the two Office Open XML types that also
    carry ``[Content_Types].xml`` and would otherwise take the DOCX road."""
    if is_xlsx(blob):
        return "xlsx"
    return "pptx" if is_pptx(blob) else None


def _office_result(blob: bytes, file_name: str) -> ExtractResult:
    if _office_kind(blob) == "pptx":
        raise UnsupportedDocumentError(f"{file_name or 'document'!r} is a PowerPoint file; needs manual review")
    try:
        text = xlsx_text(blob)
    except ValueError as exc:
        raise UnsupportedDocumentError(str(exc)) from exc
    return ExtractResult(text, METHOD_XLSX_OPERATOR if xlsx_creator(blob) == OPERATOR_CREATOR else METHOD_XLSX)


def _looks_like_docx(blob: bytes) -> bool:
    # A DOCX is a zip whose early central-directory bytes name its parts.
    return b"[Content_Types].xml" in blob[:4096] or b"word/" in blob[:4096]


def _pdf_pages(blob: bytes) -> list[PdfPage]:
    """Every page of the PDF (``letter_pages.read_pages``), or the refusal
    this module has always raised for a PDF that will not parse."""
    from .letter_pages import PageReadError, read_pages

    try:
        return read_pages(blob)
    except PageReadError as exc:
        raise UnsupportedDocumentError(str(exc)) from exc


def _docx_text(blob: bytes) -> str:
    from docx import Document

    from .docx_base import _dotx_to_docx_bytes, _is_dotx

    # A Word TEMPLATE (.dotx/.dotm) is a .docx with one content-type string
    # changed; python-docx refuses it as-is. A firm's letterhead template filed
    # on a matter must extract like any other document, not poison every
    # record check on that matter.
    if _is_dotx(blob):
        blob = _dotx_to_docx_bytes(blob)
    try:
        doc = Document(io.BytesIO(blob))
    except Exception as exc:
        raise UnsupportedDocumentError(f"DOCX could not be parsed: {exc}") from exc
    parts: list[str] = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n".join(parts)
