"""A firm's PDF opened and its text layer read: the one place the jobs lanes do it.

Each lane keeps the open it always used (``policy``'s ``library``), because a
corrupt or locked file fails differently under each and that failure is what
the lane records:

* :func:`court_layers`: the litigation lane, the case file manager and the
  negotiation watch. pymupdf, from bytes or a path, under the MuPDF lock; the
  page images vision needs are rendered and the document closed BEFORE the
  first vision call.
* :func:`record_layers`: demand's preflight. pymupdf with each page's image
  flag.
* :func:`pypdf_layers`: the chronology's extract (pypdf, one unreadable page an
  empty page).
* :func:`page_texts` and :func:`page_text`: the whole-PDF readers (an exemplar,
  a foreign-identity check, a billing chart, an evidence page, an update's
  delivered pages), pymupdf with no lock.

MEMORY (2026-10-13: A&P litigation job 01M4V2G6 was OOM-killed in its 1 GiB
cgroup at 2525 of 4808 documents). MuPDF keeps a PROCESS-WIDE store of
decoded images and fonts that outlives ``doc.close()`` (it fills toward its
256 MB default and stays there), and pymupdf appends every MuPDF warning to a
module-level list nothing trims. Both grow with the number of documents read,
not with the largest one. So every MuPDF use of the court lanes runs under
:data:`MUPDF` and ends with :func:`release`, a PDF is opened from its path
(its bytes are never all in Python at once), and no PDF stays open across a
vision call: a document's footprint is gone before the next one starts.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from .. import unopened
from ..text_rules import PASSWORD, ExtractFailure
from .policy import COURT_LONG_SIDE

MUPDF = threading.Lock()


def release() -> None:
    """Drop MuPDF's cached decoded objects and pymupdf's warning list. What an
    open document still references MuPDF keeps; only the leftovers go."""
    import pymupdf

    pymupdf.TOOLS.store_shrink(100)
    pymupdf.TOOLS.reset_mupdf_warnings()


def open_pdf(src: bytes | Path) -> Any:
    import pymupdf

    if isinstance(src, Path):
        return pymupdf.open(str(src), filetype="pdf")
    return pymupdf.open(stream=src, filetype="pdf")


def png(doc: Any, page: int, long_side: int) -> bytes:
    import pymupdf

    pg = doc[page - 1]
    zoom = long_side / (max(pg.rect.width, pg.rect.height) or 1.0)
    return pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")


def render_png(
    pdf: bytes | Path, page: int, long_side: int = COURT_LONG_SIDE, *, opener: Callable[[Any], Any] = open_pdf
) -> bytes:
    with MUPDF:
        try:
            doc = opener(pdf)
        except Exception:
            release()
            raise
        try:
            return png(doc, page, long_side)
        finally:
            doc.close()
            release()


def court_layers(
    src: bytes | Path,
    pick: Callable[[list[str]], list[int]],
    *,
    vision: bool,
    long_side: int = COURT_LONG_SIDE,
    opener: Callable[[Any], Any] = open_pdf,
) -> tuple[list[str], list[int], list[tuple[int, bytes]]]:
    """(each page's text layer, the pages ``pick`` chose for vision, their
    images). Raises ``ExtractFailure`` for a PDF that does not open, one locked
    with a password, and a page that needs vision when there is none
    (``vision`` False). ``opener`` is the lane's own open (a test watches it)."""
    with MUPDF:
        try:
            doc = opener(src)
        except Exception as exc:  # noqa: BLE001 - a PDF that does not open is one failure record
            release()
            raise ExtractFailure(f"pdf_unreadable: {type(exc).__name__}") from None
        try:
            if unopened.locked(doc):
                raise ExtractFailure(f"{PASSWORD}: {unopened.REASON}")
            texts = [pg.get_text() or "" for pg in doc]
            picks = pick(texts)
            if picks and not vision:
                raise ExtractFailure("scan_without_vision: a page has no text layer and no vision reader was given")
            images = [(i, png(doc, i + 1, long_side)) for i in picks]
        finally:
            doc.close()
            release()
    return texts, picks, images


def record_layers(path: Path) -> list[dict[str, Any]]:
    """Demand's preflight: each page's number, text layer, and whether it
    carries an image. A locked file raises ``unopened.Locked``."""
    import pymupdf

    out = []
    with pymupdf.open(str(path)) as doc:
        unopened.refuse_locked(doc)
        for i, page in enumerate(doc, 1):
            text = page.get_text() or ""
            has_image = bool(page.get_images(full=False))
            out.append({"page": i, "text": text, "has_image": has_image})
    return out


def pypdf_layers(path: Path) -> list[tuple[int, str]]:
    """The chronology's text layer: pypdf, page by page; a page whose text
    pypdf cannot extract is an empty page."""
    from pypdf import PdfReader

    pages = []
    for i, pg in enumerate(PdfReader(str(path)).pages, 1):
        try:
            t = pg.extract_text() or ""
        except Exception:  # noqa: BLE001 - one unreadable page is an empty page
            t = ""
        pages.append((i, t))
    return pages


def page_texts(path: Path | str) -> list[str]:
    """Every page's text layer of a whole PDF (pymupdf, no lock). Raises what
    the open raises; each caller keeps its own handling."""
    import pymupdf

    with pymupdf.open(str(path)) as doc:
        return [pg.get_text() for pg in doc]


def page_text(path: Path | str, page: int) -> str | None:
    """One page's text layer (1-based), or None when the PDF has no such page."""
    import pymupdf

    with pymupdf.open(str(path)) as doc:
        if 1 <= page <= len(doc):
            return str(doc[page - 1].get_text())
    return None


__all__ = [
    "MUPDF",
    "court_layers",
    "open_pdf",
    "page_text",
    "page_texts",
    "png",
    "pypdf_layers",
    "record_layers",
    "release",
    "render_png",
]
