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


#: A token that reads as a word, a number, or a date. Calibrated as-is on a real
#: 59-page bundle (see LEGIBLE_WORD_SHARE); widening it raises the score of the
#: noisy pages too and moves the calibration, so change it only with a re-measure.
_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z'\-]*[.,:;]?$|^\d{1,4}([/\-.]\d{1,4}){0,2}[.,]?$|^[A-Za-z]{1,3}[.,]?$")

#: Below this share of word-shaped tokens, a page's text layer is noise and the
#: page is read with vision instead.
#:
#: WHY THIS EXISTS (a client seat, 2026-09-23). A 59-page stack of hand-annotated
#: discovery charts and calendars came back ``readable: True`` from the text
#: road, because every page HAD a text layer: the scanner's OCR of the
#: handwriting, e.g. ``~~~, 9~2' '~`` and ``~ LC1 S~``. A character count cannot
#: tell that from a letter. Measured on that stack: pages dominated by
#: handwriting scored 0.46-0.67, clean typed pages 0.85-0.93, and a band of
#: typed pages with SOME handwriting scored 0.70-0.80 -- including the page whose
#: handwritten note named a supplemental-discovery date, at 0.75. A line at 0.70
#: would have dropped that note, so the line is 0.80. Erring this way costs a
#: couple of cents of transcription on a page; erring the other way loses a
#: deadline without saying so.
LEGIBLE_WORD_SHARE = 0.80

#: A page with fewer tokens than this is judged by the character floor alone: a
#: short page's ratio swings on one token.
MIN_TOKENS_FOR_LEGIBILITY = 8


def word_share(text: str) -> float:
    """The share of whitespace-separated tokens that read as words or numbers."""
    tokens = text.split()
    if not tokens:
        return 1.0
    return sum(1 for t in tokens if _WORD_RE.match(t)) / len(tokens)


def page_needs_vision(text: str) -> bool:
    """True when this page's text layer cannot be trusted to BE the page.

    Either too little text (paper with no layer), or a layer that is mostly not
    words (OCR of handwriting, stamps, or a bad scan).
    """
    if len(text) < PAGE_TEXT_FLOOR:
        return True
    if len(text.split()) < MIN_TOKENS_FOR_LEGIBILITY:
        return False
    return word_share(text) < LEGIBLE_WORD_SHARE


def scanned_indexes(pages: list[str]) -> list[int]:
    """Which pages (0-based) need vision: no text layer, or an illegible one.

    Per page, never averaged, and only THOSE pages are transcribed, so a bundle
    of 59 typed pages with 20 handwritten ones spends on 20.
    """
    return [i for i, text in enumerate(pages) if page_needs_vision(text)]


def extract_pages(blob: bytes, indexes: list[int]) -> bytes:
    """A new PDF of just these pages (0-based), in the order given."""
    from pypdf import PdfReader, PdfWriter

    try:
        reader = PdfReader(io.BytesIO(blob))
        writer = PdfWriter()
        for index in indexes:
            writer.add_page(reader.pages[index])
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()
    except Exception as exc:  # pypdf raises a wide family; every one means "not a PDF we can page"
        raise PageReadError("split_failed", f"the pages could not be extracted: {exc}") from exc


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

    A page starts ONLY at a marker line that stands alone, follows a blank line
    (or opens the text), and carries exactly the NEXT page number. Everything
    else, blank lines included, is body.

    WHY NOT SPLIT ON BLANK LINES, which is what this did until 2026-09-23. A
    transcribed letter has paragraphs, and a paragraph break is a blank line, so
    splitting on "\\n\\n" broke the numbering on the first real vision read and
    refused a document that was fine. The pilot never showed it because every
    page there had a text layer with no blank lines in it.

    The forged-marker defence is the NEXT-number rule: a page's own printing of
    "[p.9]" cannot start a page unless 9 is exactly the page expected next, and
    even then the count check below refuses a document whose numbering no longer
    reaches page_count cleanly. A document we cannot number is a document we
    cannot cut, and cutting on a guess files one client's letter onto another
    client's matter.
    """
    if not text:
        if page_count == 0:
            return []
        raise PageReadError("marker_mismatch", f"no text, and the document has {page_count} pages")
    pages: list[str] = []
    body: list[str] = []
    legible = True
    started = False
    expected = 1
    prev_blank = True
    for line in text.split("\n"):
        stripped = line.strip()
        match = _MARKER_RE.match(stripped)
        if match is not None and prev_blank and int(match.group(1)) == expected:
            if started:
                pages.append("\n".join(body).strip() if legible else "")
            body, legible, started = [], match.group(2) is None, True
            expected += 1
            prev_blank = False
            continue
        if not started:
            raise PageReadError(
                "marker_mismatch", f"text does not open with the page 1 marker (opens {stripped[:40]!r})"
            )
        body.append(line)
        prev_blank = stripped == ""
    if started:
        pages.append("\n".join(body).strip() if legible else "")
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
    "LEGIBLE_WORD_SHARE",
    "MIN_TOKENS_FOR_LEGIBILITY",
    "compose",
    "extract_pages",
    "page_needs_vision",
    "word_share",
    "page_texts",
    "parse_marked",
    "safe_file_name",
    "scanned_indexes",
    "split_range",
]
