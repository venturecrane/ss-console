"""Which pages of the NEW documents an update reads. $0.

What the delivery covered is read off the delivered exhibits, never the ledger
alone. A coverage record from an older pipeline can be stale or missing; the
delivered exhibit PDFs are what the firm holds, and they are the page truth.

* A new document byte-identical to a delivered exhibit (or to another new
  document) is not an exhibit: it is skipped by sha256 before this module.
* A TEXT page (at least MIN_CHARS of text) is EXCLUDED when at least
  COVERED_FRAC of its 8-word shingles (digits stripped: a re-filed copy
  differs in its fax stamp and print date, not in its words) are already in a
  delivered exhibit: a re-filed copy of a record the delivery read.
* A TEXT page at least COVERED_FRAC covered by the delivered exhibits AND the
  pages of the new documents read before it (in exhibit order) is EXCLUDED as
  a repeat of an earlier new page, unless it states a CLINICAL value (a lab,
  vital or dose: ``values.clinical_values``) no earlier page stated: two
  re-filed copies of one record differ in their fax headers, not in their
  words, and the second costs a compose read and yields a duplicate entry.
  No image hash: the vision pass runs before this, so a hash saves nothing.
* A page with no text after the vision pass is excluded as blank and counted.
* Every other page is read (no date window: a new record may hold visits
  before or after the incident).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .values import clinical_values

MIN_CHARS = 80
COVERED_FRAC = 0.8
SHINGLE = 8
PAGE_MARK = re.compile(r"^\[p\.(\d+)\][^\n]*$", re.M)
FAILED_MARKS = ("[page transcription failed]", "[page transcription refused]")

READ, EXCLUDED, UNREAD = "text", "excluded", "unread"
ALREADY = "already read in a delivered exhibit"
OVERLAP = "repeats an earlier page of the new documents"
BLANK = "no text on the page"


def shingles(text: str | None) -> set[str]:
    words = re.sub(r"[^a-z]+", " ", re.sub(r"\d", "", (text or "").lower())).split()
    return {" ".join(words[i : i + SHINGLE]) for i in range(max(0, len(words) - SHINGLE + 1))}


def pages_of_text(text: str) -> dict[int, str]:
    """{page: text} from a text/<id>.txt body ([p.N] markers, native or
    machine-transcribed)."""
    marks = list(PAGE_MARK.finditer(text or ""))
    out: dict[int, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out[int(m.group(1))] = text[m.end() : end].strip("\n")
    return out


def pdf_pages(path: Path) -> list[str]:
    import pymupdf

    with pymupdf.open(str(path)) as doc:
        return [pg.get_text() for pg in doc]


def seen_shingles(delivered_pdfs: list[Path]) -> set[str]:
    """Every delivered exhibit page's shingles: what the delivery read."""
    seen: set[str] = set()
    for p in delivered_pdfs:
        for t in pdf_pages(p):
            seen |= shingles(t)
    return seen


@dataclass
class Earlier:
    """What the new documents' pages read so far state: their shingles and
    clinical values, built cumulatively in exhibit order."""

    shingles: set[str] = field(default_factory=set)
    values: set[str] = field(default_factory=set)

    def add(self, text: str) -> None:
        self.shingles |= shingles(text)
        self.values |= clinical_values(text)


def _decide(t: str, seen: set[str], earlier: Earlier | None) -> tuple[str, str]:
    if t in FAILED_MARKS:
        return UNREAD, "the page could not be transcribed"
    if len(t) < MIN_CHARS:
        return EXCLUDED, BLANK
    sh = shingles(t)
    if sh and len(sh & seen) >= COVERED_FRAC * len(sh):
        return EXCLUDED, ALREADY
    if earlier is not None and sh and len(sh & (seen | earlier.shingles)) >= COVERED_FRAC * len(sh):
        if not clinical_values(t) - earlier.values:
            return EXCLUDED, OVERLAP
    return READ, "new document"


def classify_new(
    pages: dict[int, str], npages: int, seen: set[str], earlier: Earlier | None = None
) -> dict[int, tuple[str, str]]:
    """Per page of one new document: (text | excluded | unread, reason).
    `unread` is a page the transcription could not read: it is never skipped
    silently, the stage holds on it. `earlier`, when given, is what the new
    documents before this one (and this one's earlier pages) state; every
    page with text is added to it."""
    out: dict[int, tuple[str, str]] = {}
    for i in range(1, npages + 1):
        t = (pages.get(i) or "").strip()
        out[i] = _decide(t, seen, earlier)
        if earlier is not None and out[i][0] != UNREAD and len(t) >= MIN_CHARS:
            earlier.add(t)
    return out


def row(number: int, title: str, decisions: dict[int, tuple[str, str]], pages: dict[int, str]) -> dict[str, Any]:
    """The selection record for one new exhibit."""
    read = sorted(p for p, (d, _) in decisions.items() if d == READ)
    return {
        "exhibit": number,
        "title": title,
        "pages": len(decisions),
        "read": read,
        "unread": sorted(p for p, (d, _) in decisions.items() if d == UNREAD),
        "excluded": dict(Counter(r for d, r in decisions.values() if d == EXCLUDED)),
        "chars": sum(len(pages.get(p) or "") for p in read),
    }


def selected_text(pages: dict[int, str], read: list[int]) -> str:
    """The pages compose reads, page-marked with the exhibit's own numbers
    (a new exhibit is its source document as it is, so they coincide)."""
    return "\n".join(f"[p.{pg}]\n{pages.get(pg) or ''}" for pg in read)
