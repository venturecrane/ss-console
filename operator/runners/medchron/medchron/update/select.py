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
* A TEXT page that is a REFILED COPY of a page of an EARLIER new document
  (``Earlier.copy_of``: COVERED_FRAC of its shingles, and every date, word and
  clinical value it states, fax and print lines aside) is EXCLUDED: the second
  copy costs a compose read and yields a duplicate entry. Template visits
  differ in a date or a word and are both read; pages within one document are
  never compared.
* Before any of that, a page whose PICTURE is a delivered page or a page of an
  earlier new document (``update/pageid``, computed before the vision pass so
  a re-filed scan is never transcribed) is EXCLUDED as already read or as a
  repeat: a scanned page has no words for the rules above to compare.
* A page with no text after the vision pass is excluded as blank and counted.
* A BILLING page (``update/billing_page``: a claim form, or an itemized
  statement or ledger with no clinical narrative) is excluded: the base lane
  never itemizes a bill. Its text stays in the transcript the handwritten-date
  check reads, so its dates of service are still evidence.
* Every other page is read (no date window: a new record may hold visits
  before or after the incident).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..stages.chunking import context_block
from .billing_page import BILLING, is_billing
from .pageid import SKIPPED
from .values import clinical_values

MIN_CHARS = 80
COVERED_FRAC = 0.8
SHINGLE = 8
PAGE_MARK = re.compile(r"^\[p\.(\d+)\][^\n]*$", re.M)
#: A page the vision pass failed on, refused, or skipped by its picture
#: (``pageid.SKIPPED``): unread unless ``update/pageid`` accounts for it.
FAILED_MARKS = ("[page transcription failed]", "[page transcription refused]", SKIPPED)

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


def shingles_by_exhibit(delivered_pdfs: dict[int, Path]) -> dict[int, set[str]]:
    """{delivered exhibit: its pages' shingles}: where the delivery read a
    page (a re-filed copy of delivered pages is cited to the exhibit that
    holds them, ``delivered_home``)."""
    return {n: set().union(*(shingles(t) for t in pdf_pages(p))) for n, p in delivered_pdfs.items()}


#: A fax, print or page-count line: the furniture two copies of one record
#: differ in. Its dates and words never tell two visits apart. "Generated on"
#: is an EMR print stamp: a record re-printed for a second request carries
#: the new print date on every page (2026-10-14).
_FURNITURE = re.compile(r"(?im)^.*\b(?:fax|printed|print date|generated on|page \d+ of \d+|sent|received)\b.*$")
_DATE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})(?![\d/])")


def _dates(text: str) -> set[str]:
    out = set()
    for m, d, y in _DATE.findall(text):
        out.add(f"{int(m)}/{int(d)}/{(2000 + int(y)) if len(y) == 2 else int(y)}")
    return out


@dataclass(frozen=True)
class _Page:
    doc: int
    shingles: frozenset[str]
    dates: frozenset[str]
    words: frozenset[str]
    values: frozenset[str]


def _page(doc: int, text: str) -> _Page:
    body = _FURNITURE.sub("", text)
    return _Page(
        doc,
        frozenset(shingles(body)),
        frozenset(_dates(body)),
        frozenset(re.findall(r"[a-z]{4,}", body.lower())),
        frozenset(clinical_values(body)),
    )


@dataclass
class Earlier:
    """The pages of the new documents read so far, in exhibit order.

    A page is a REFILED COPY of an earlier one only when that one page, from
    an EARLIER DOCUMENT, covers COVERED_FRAC of its shingles and states every
    date, every word and every clinical value it states (fax and print lines
    aside). Two visits written on one template differ in a date or a word, so
    both are read; pages of the same document are never compared, so a run of
    template notes inside one record is read whole. When in doubt a page is
    read: collect drops a true repeat after compose, while a page skipped here
    is never seen again."""

    pages: list[_Page] = field(default_factory=list)
    doc: int = 0

    def next_document(self) -> None:
        self.doc += 1

    def add(self, text: str) -> None:
        self.pages.append(_page(self.doc, text))

    def copy_of(self, text: str) -> bool:
        me = _page(self.doc, text)
        if not me.shingles:
            return False
        for e in self.pages:
            if e.doc == self.doc or len(me.shingles & e.shingles) < COVERED_FRAC * len(me.shingles):
                continue
            if me.dates <= e.dates and me.words <= e.words and me.values <= e.values:
                return True
        return False


def _decide(t: str, seen: set[str], earlier: Earlier | None) -> tuple[str, str]:
    if t in FAILED_MARKS:
        return UNREAD, "the page could not be transcribed"
    if len(t) < MIN_CHARS:
        return EXCLUDED, BLANK
    if is_billing(t):
        return EXCLUDED, BILLING
    sh = shingles(t)
    if sh and len(sh & seen) >= COVERED_FRAC * len(sh):
        return EXCLUDED, ALREADY
    if earlier is not None and earlier.copy_of(t):
        return EXCLUDED, OVERLAP
    return READ, "new document"


def classify_new(
    pages: dict[int, str],
    npages: int,
    seen: set[str],
    earlier: Earlier | None = None,
    image: dict[int, str] | None = None,
) -> dict[int, tuple[str, str]]:
    """Per page of one new document: (text | excluded | unread, reason).
    `unread` is a page the transcription could not read: it is never skipped
    silently, the stage holds on it. `earlier`, when given, holds the pages of
    the new documents before this one; this document's pages join it.
    `image`: {page: ALREADY | OVERLAP} for the pages ``update/pageid`` found
    by their picture; those were never transcribed and are excluded first."""
    if earlier is not None:
        earlier.next_document()
    out: dict[int, tuple[str, str]] = {}
    for i in range(1, npages + 1):
        t = (pages.get(i) or "").strip()
        if image and i in image:
            out[i] = (EXCLUDED, image[i])
            continue
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
    (a new exhibit is its source document as it is, so they coincide).

    A read page whose neighbour is NOT read (the delivery read it, or it
    repeats an earlier copy) carries that neighbour's foot or head as a
    delimited context block outside its own segment: a serology's "No" at the
    foot of an unread page and its result at the head of a read one was
    composed POSITIVE (``stages/chunking.context_block``)."""
    want = set(read)

    def ctx(p: int) -> str:  # a billing page is never context: it is not composed at all
        t = pages.get(p) or ""
        return "" if p in want or is_billing(t) else t

    out = []
    for pg in read:
        pre = context_block(ctx(pg - 1), pg - 1, before=True)
        nxt = context_block(ctx(pg + 1), pg + 1, before=False)
        out.append(pre + f"[p.{pg}]\n{pages.get(pg) or ''}" + ("\n" + nxt.rstrip("\n") if nxt else ""))
    return "\n".join(out)


def delivered_home(
    row: dict[str, Any], pages: dict[int, str], by_ex: dict[int, set[str]], image_ex: dict[int, int]
) -> int | None:
    """The delivered exhibit a new document RE-FILES WHOLE, or None.

    A document none of whose pages is read, every one already read in a
    delivered exhibit (or blank), is not a new exhibit: it is a re-filed copy
    of delivered pages, cited where the delivery read them. A re-printed
    hospital record whose every page was delivered once became a new exhibit
    and an Exhibit List line (2026-10-14). The exhibit is the one most of its
    pages are in: by picture (`image_ex`, {page: delivered exhibit}) or, for a
    page with no picture match, the delivered exhibit sharing the most of its
    shingles. A document with a page read, unread, billed, or excluded for
    any other reason is never re-filed whole: it stays an exhibit, and what
    it adds is read."""
    excluded = row["excluded"]
    if row["read"] or row["unread"] or not excluded.get(ALREADY) or set(excluded) - {ALREADY, BLANK}:
        return None
    votes: Counter[int] = Counter()
    for p in range(1, int(row["pages"]) + 1):
        n = image_ex.get(p)
        sh = shingles(pages.get(p))
        if n is None and sh and by_ex:
            best = max(by_ex, key=lambda e: len(sh & by_ex[e]))
            n = best if sh & by_ex[best] else None
        if n is not None:
            votes[n] += 1
    return votes.most_common(1)[0][0] if votes else None
