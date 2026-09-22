"""A combined post bundle, read per page and cut per letter.

WHAT THIS IS FOR. A firm scans the day's post as ONE PDF holding SEVERAL
letters, each belonging to a different matter. To file them it has to be read
with page numbers that can be trusted, cut on page boundaries, and named
without letting the letters' own text choose the name. This module is those
three jobs. It decides nothing about WHICH matter a letter belongs to; that is
``matter_resolution``'s arithmetic and the model's reading, fenced by a
resolution token.

WHY IT DOES ITS OWN PAGE EXTRACTION RATHER THAN CALLING ``extract_text_ex``.
Two reasons, both of which silently lose letters:

1. ``extract._looks_scanned`` decides vision-versus-pypdf for the WHOLE
   document on an average: ``len(text) < SCANNED_CHARS_PER_PAGE * pages``. Its
   docstring says the average exists "so a scan bundle with one text-layer
   cover sheet still reads as scanned", and for a bundle that is not true. A
   60-page bundle needs under 900 characters to read as scanned, and one
   ordinary digital page carries two thousand. So one cover sheet routes 59
   scanned pages onto the pypdf road.
2. The pypdf road then DROPS every page it found no text on:
   ``"\\n\\n".join(p.strip() for p in pages if p.strip())``. Block K is not page
   K, empty pages vanish rather than being reported, and ``method`` comes back
   ``pypdf`` with nothing to say anything is wrong.

Together those turn "five letters were scans" into "five letters were never
here". So the road is decided PER PAGE here, and every page 1..N gets an entry
whether or not it carried text.

THE MARKERS ARE THE CONTRACT. Composed the same way on both roads, in the
grammar ``shared`` vision already uses: a legible page is the line ``[p.N]``
then its text, an unreadable one is the bare line ``[p.N: no legible content]``,
and pages are joined by a blank line. A letter is then a range of page numbers,
which is a thing the connector can check, rather than a span of prose, which is
not.

A PAGE'S OWN PRINTING CAN SAY ``[p.7]``. Fax headers, exhibit stamps and
pleading footers all print things of that shape, and a transcription reproduces
them faithfully. So the parser reads a marker ONLY as the first line of a block
after a blank line, and then asserts the numbers run exactly 1..N. Text whose
page numbering cannot be vouched for never drives a page range.
"""

from __future__ import annotations

import io
import re
import threading
from dataclasses import dataclass

from .extract import SCANNED_CHARS_PER_PAGE, UnsupportedDocumentError

#: A page that produced fewer than this many characters is paper rather than a
#: document, judged PER PAGE. Shares the connector's existing number so the two
#: roads agree about what "has text" means.
PAGE_TEXT_FLOOR = SCANNED_CHARS_PER_PAGE

NO_CONTENT = "[no legible content]"

#: A marker, and only as a whole line. Never used to scan a body.
_MARKER_RE = re.compile(r"^\[p\.(\d+)(: no legible content)?\]$")

#: What a composed filename may contain once the vendor's own habits are
#: accounted for. No period survives into the stem: this vendor truncates a
#: displayed name at the first one, so "09.22.2026 State Farm" reaches the firm
#: as "09" and every letter that day collides into an indistinguishable pile.
_NAME_ALLOWED_RE = re.compile(r"[^A-Za-z0-9 _-]+")
_NAME_MAX_STEM = 120


class PageReadError(RuntimeError):
    """The bundle could not be read as pages. Carries a closed-set reason."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True)
class PagedText:
    """Per-page text plus the marked composition the model reads."""

    pages: list[str]
    text: str
    page_count: int
    scanned_pages: int


def page_texts(blob: bytes) -> list[str]:
    """Every page's mechanically extracted text, in order, NOTHING dropped.

    An empty string is a real answer about a real page. The whole defect this
    module exists to close is a list whose length stopped matching the
    document's.
    """
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(blob))
        return [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception as exc:  # pypdf raises a wide family; every one means "not a PDF we can page"
        raise PageReadError("not_pdf", f"PDF could not be parsed: {exc}") from exc


def scanned_indexes(pages: list[str]) -> list[int]:
    """Which pages (0-based) carry too little text to be a document.

    Per page, never averaged. A bundle is sent to vision when ANY page is
    scanned, because the alternative is reading the digital pages and silently
    losing the scanned ones.
    """
    return [i for i, text in enumerate(pages) if len(text) < PAGE_TEXT_FLOOR]


def compose(pages: list[str]) -> str:
    """The page list as marked text: ``[p.N]`` plus body, or the bare marker.

    Mirrors the grammar ``vision._compose`` emits so one parser reads both
    roads. The number comes from the page's POSITION here and is never
    something a page said about itself.
    """
    blocks: list[str] = []
    for index, text in enumerate(pages, start=1):
        body = text.strip()
        blocks.append(f"[p.{index}]\n{body}" if body else f"[p.{index}: no legible content]")
    return "\n\n".join(blocks)


def parse_marked(text: str, page_count: int) -> list[str]:
    """Marked text back into a page list, or refuse.

    Refuses unless every block opens with a marker AND the numbers run exactly
    1..page_count in order. A document we cannot number is a document we cannot
    cut, and cutting it on a guess files one client's letter onto another
    client's matter.
    """
    blocks = text.split("\n\n") if text else []
    pages: list[str] = []
    for position, block in enumerate(blocks, start=1):
        lines = block.split("\n", 1)
        match = _MARKER_RE.match(lines[0].strip())
        if match is None or int(match.group(1)) != position:
            raise PageReadError(
                "marker_mismatch",
                f"page markers do not run 1..{page_count} (block {position} opens {lines[0][:40]!r})",
            )
        pages.append("" if match.group(2) else (lines[1].strip() if len(lines) > 1 else ""))
    if len(pages) != page_count:
        raise PageReadError(
            "marker_mismatch",
            f"the text carries {len(pages)} pages and the document has {page_count}",
        )
    return pages


def split_range(blob: bytes, first_page: int, last_page: int) -> bytes:
    """A new PDF holding pages first..last inclusive, 1-based.

    pypdf rewrites rather than slices, so the output's byte length is not a
    slice of the input's. Any byte ceiling is re-checked on the RESULT by the
    caller, never inferred from the source.
    """
    from pypdf import PdfReader, PdfWriter

    try:
        reader = PdfReader(io.BytesIO(blob))
        total = len(reader.pages)
        if not 1 <= first_page <= last_page <= total:
            raise PageReadError(
                "range_out_of_bounds",
                f"pages {first_page}-{last_page} are not inside a {total}-page document",
            )
        writer = PdfWriter()
        for index in range(first_page - 1, last_page):
            writer.add_page(reader.pages[index])
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()
    except PageReadError:
        raise
    except Exception as exc:  # pypdf raises a wide family; every one means "not a PDF we can page"
        raise PageReadError("split_failed", f"the page range could not be cut: {exc}") from exc


def safe_file_name(name: str, first_page: int, last_page: int) -> str:
    """A filename composed from a caller's string, made safe to send.

    Nothing downstream sanitises this: it goes into the metadata POST verbatim.
    On this feature the caller's string is read off a letter, which is text an
    outside party wrote, so the guard belongs here.

    Every period leaves the stem (see ``_NAME_ALLOWED_RE``), the charset is
    closed, the length is capped, and an empty or hostile name degrades to
    something a person can still find rather than to nothing.
    """
    raw = name if isinstance(name, str) else ""
    stem = raw.replace("\\", "/").rsplit("/", 1)[-1]
    stem = "".join(ch for ch in stem if ch.isprintable())
    if stem.lower().endswith(".pdf"):
        stem = stem[:-4]
    stem = _NAME_ALLOWED_RE.sub(" ", stem)
    stem = " ".join(stem.split())[:_NAME_MAX_STEM].strip()
    if not stem:
        stem = f"letter pp{first_page}-{last_page}"
    return f"{stem}.pdf"


class FiledPages:
    """Which pages of which spooled bundle this process has already filed.

    Closes the one harm the resolution token cannot see. A token binds a
    matter, so two resolutions in one turn produce two valid tokens, and
    nothing in them says page 3 already went somewhere. Filing page 3 onto two
    matters is a cross-client disclosure that looks like success twice.

    In-process and unsynchronised across restarts, deliberately, exactly as
    ``resolution_token`` is: a restart forgets, and forgetting refuses nothing
    it should have allowed, because the caller re-reads and re-resolves anyway.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._filed: dict[str, set[int]] = {}

    def claim(self, key: str, first_page: int, last_page: int) -> int | None:
        """Claim a range, or return the first page already filed under this key."""
        wanted = set(range(first_page, last_page + 1))
        with self._lock:
            taken = self._filed.setdefault(key, set())
            clash = sorted(wanted & taken)
            if clash:
                return clash[0]
            taken |= wanted
        return None

    def release(self, key: str, first_page: int, last_page: int) -> None:
        """Give a claimed range back when the write did not happen."""
        with self._lock:
            taken = self._filed.get(key)
            if taken is not None:
                taken -= set(range(first_page, last_page + 1))


FILED_PAGES = FiledPages()


__all__ = [
    "FILED_PAGES",
    "NO_CONTENT",
    "PAGE_TEXT_FLOOR",
    "FiledPages",
    "PageReadError",
    "PagedText",
    "UnsupportedDocumentError",
    "compose",
    "page_texts",
    "parse_marked",
    "safe_file_name",
    "scanned_indexes",
    "split_range",
]
