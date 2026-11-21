"""Which pages of the NEW documents an update reads. $0.

What the delivery covered is read off the delivered exhibits, never the ledger
alone. A coverage record from an older pipeline can be stale or missing; the
delivered exhibit PDFs are what the firm holds, and they are the page truth.

* A new document byte-identical to a delivered exhibit (or to another new
  document) is not an exhibit: it is skipped by sha256 before this module.
* A page is set aside as a REPEAT only when it IS the same page, by one of
  two tests (2026-11-21):
  - its PICTURE is pixel-equal to a delivered page or to a page of an
    earlier new document (``update/pageid`` EXACT, computed before the vision
    pass so a re-filed scan is never transcribed); or
  - its TEXT (at least MIN_CHARS) says EXACTLY what one delivered page, or
    one page of an EARLIER new document, says, export and print stamps and
    edge fax lines aside (``same_text``): a re-filed copy. Pages within one
    document are never compared.
  Nothing else is a repeat. A page that shares a template, most of its
  words, a run of pages, or a near picture with a covered page, and differs
  in one date or value, is READ: on three real updates three readers ruled
  21 pages the older similarity rules set aside different from the page they
  matched, and at least 10 of their facts were never delivered. A visit read
  twice is matched after compose by its date and provider, whatever page it
  cites (``repeats.keep_decision`` at collect, ``final_set.repeats``), and
  kept only for what it adds.
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
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ...stages.chunking import context_block
from . import noteheading as NH
from .billing_page import BILLING, is_billing
from .pageid import DELIVERED, SKIPPED
from .records_dates import SERVICE_LINE

MIN_CHARS = 80
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
    from ...matter import pdf as matter_pdf

    return matter_pdf.page_texts(path)


def seen_shingles(delivered_pdfs: list[Path], numbers: list[int] | None = None) -> Delivered:
    """Every delivered exhibit page, one record per page, each exhibit its
    own document: what the delivery read (``Delivered``). `numbers`: each
    PDF's delivered exhibit number, so an exclusion names the page that
    covers it ("exhibit:N", the picture record's spelling)."""
    labels = [f"{DELIVERED}{n}" for n in numbers] if numbers is not None else None
    return Delivered.of_docs([pdf_pages(p) for p in delivered_pdfs], labels)


def shingles_by_exhibit(delivered_pdfs: dict[int, Path]) -> dict[int, set[str]]:
    """{delivered exhibit: its pages' shingles}: where the delivery read a
    page (a re-filed copy of delivered pages is cited to the exhibit that
    holds them, ``delivered_home``)."""
    return {n: set().union(*(shingles(t) for t in pdf_pages(p))) for n, p in delivered_pdfs.items()}


#: A fax, print or page-count line: the furniture two copies of one record
#: differ in (``same_text`` sets one aside only among a page's first or last
#: EDGE inked lines: "received" or "sent" in the body is clinical). "Generated on"
#: is an EMR print stamp: a record re-printed for a second request carries
#: the new print date on every page (2026-10-14).
#: An export or print STAMP opens its line (2026-11-17: two overlapping
#: exports of one clinic's records differed only in stamps such as "Run
#: date", "Report Date/Time", "Requested by", "Exported" and "Generated on",
#: and each stamp's new date broke the match, so 19 visits were composed
#: twice). Anchored at the line's start and naming a print or export act, so
#: a clinical line ("Report: MRI of the lumbar spine ...", "Date of
#: service 03/04/2025") is never one.
_STAMP_HEAD = (
    r"(?:run|report|print(?:ed)?|export(?:ed)?|generated|requested|created|retrieved)[ \t]*"
    r"(?:date[ \t]*/[ \t]*time|date|time|on|by|at)\b"
    r"|export(?:ed)?\b|requested[ \t]+by\b|generated[ \t]+on\b|printed\b"
)
STAMP = re.compile(rf"(?im)^[ \t]*(?:{_STAMP_HEAD})[^\n]*$")
_FURNITURE = re.compile(
    rf"(?im)^.*\b(?:fax|printed|print date|generated on|page \d+ of \d+|sent|received)\b.*$|^[ \t]*(?:{_STAMP_HEAD})[^\n]*$"
)
_DATE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})(?![\d/])")
#: A line that is ONLY a date and a time of day (an EMR's print header or
#: footer), e.g. "11/02/2025 3:14 PM": a stamp only when it is among the
#: first or last EDGE inked lines of the page, never in the body, where a
#: bare date-time line can be when a note was signed or a vital taken.
BARE_STAMP = re.compile(
    r"(?i)^[ \t]*\d{1,2}/\d{1,2}/(?:\d{2}|\d{4}),?[ \t]+\d{1,2}:\d{2}(?::\d{2})?(?:[ \t]*[ap]\.?m\.?)?"
    r"(?:[ \t]*\(?[a-z]{2,4}\)?)?[ \t]*$"
)
#: A page-number line, alone on its line ("Page 3", "Page 3 of 9", "3 of 9").
PAGE_LINE = re.compile(r"(?i)^[ \t]*(?:page[ \t]*\d+(?:[ \t]*(?:of|/)[ \t]*\d+)?|\d+[ \t]+of[ \t]+\d+)[ \t]*$")
EDGE = 2


def strip_stamps(text: str) -> str:
    """`text` without its export and print stamp lines (``STAMP``), its
    page-number lines, and a bare date-time line among its first or last
    EDGE inked lines (``BARE_STAMP``). Nothing else is removed: a clinical
    date line is never a stamp."""
    return "\n".join(ln for ln, stamp in stamp_lines(text) if not stamp)


def stamp_lines(text: str) -> list[tuple[str, bool]]:
    """Every line of `text` with whether ``strip_stamps`` sets it aside."""
    lines = (text or "").split("\n")
    inked = [i for i, ln in enumerate(lines) if ln.strip()]
    edge = set(inked[:EDGE] + inked[-EDGE:])
    return [
        (ln, bool(STAMP.match(ln) or PAGE_LINE.match(ln) or (i in edge and BARE_STAMP.match(ln))))
        for i, ln in enumerate(lines)
    ]


def _dates(text: str) -> set[str]:
    out = set()
    for m, d, y in _DATE.findall(text):
        out.add(f"{int(m)}/{int(d)}/{(2000 + int(y)) if len(y) == 2 else int(y)}")
    return out


#: How a page was found covered: its picture (``update/pageid``, pixel-equal)
#: or its text, the same text as one page (``same_text``).
BY_TEXT, BY_PICTURE = "text", "picture"


@dataclass(frozen=True)
class Cover:
    """The page that covers an excluded page: recorded as evidence, never a condition."""

    src: str  # DELIVERED_SRC | EARLIER_SRC
    doc: str  # "exhibit:N" or a new document's id
    page: int
    kind: str  # BY_TEXT | BY_PICTURE

    def as_dict(self) -> dict[str, Any]:
        n = self.doc[len(DELIVERED) :] if self.doc.startswith(DELIVERED) else ""
        where: dict[str, Any] = {"exhibit": int(n)} if n.isdigit() else {"doc": self.doc}
        return {**where, "page": self.page, "exact": False}


def service_dates(text: str) -> set[str]:
    """The dates `text` states on a line that dates care (``records_dates.SERVICE_LINE``)."""
    return {d for ln in (text or "").splitlines() if SERVICE_LINE.search(ln) for d in _dates(ln)}


def same_text(text: str | None) -> str:
    """What a page SAYS, for the one text test of a repeat: its text without
    its export and print stamps (``strip_stamps``) and without a fax, print
    or page-count line (``_FURNITURE``) among its first or last EDGE inked
    lines, its whitespace collapsed. Two pages are the same page by their
    words only when these are EXACTLY equal. A furniture word in the BODY
    ("received", "sent") is on a clinical line and stays: two pages that
    differ only there say different things (2026-11-21)."""
    lines = strip_stamps(text or "").split("\n")
    inked = [i for i, ln in enumerate(lines) if ln.strip()]
    edge = set(inked[:EDGE] + inked[-EDGE:])
    kept = [ln for i, ln in enumerate(lines) if not (i in edge and _FURNITURE.fullmatch(ln))]
    return " ".join(" ".join(kept).split())


@dataclass
class Delivered:
    """The delivered exhibits' pages, each by what it says (``same_text``).

    A new page is ALREADY READ by its words only when it says EXACTLY what
    one delivered page says (stamps and edge furniture aside). A page that
    shares a template, most of its words, or a run of pages with a delivered
    page, and differs in one date or value, is read: three readers ruled 21
    of 387 pages the old shingle, run, twin and picture-confirmed rules set
    aside DIFFERENT from the page they were matched to, and at least 10 of
    their facts were missing from the delivered documents (2026-11-21)."""

    texts: dict[str, tuple[str, int]] = field(default_factory=dict)

    @classmethod
    def of(cls, texts: Iterable[str]) -> Delivered:
        return cls.of_docs([list(texts)])

    @classmethod
    def of_docs(cls, docs: list[list[str]], labels: list[str] | None = None) -> Delivered:
        """One document per list (a delivered exhibit), its pages in order;
        `labels` names each ("exhibit:N"), else its position."""
        out = cls()
        for k, texts in enumerate(docs):
            label = labels[k] if labels is not None and k < len(labels) else f"{DELIVERED}{k + 1}"
            for i, t in enumerate(texts, 1):
                key = same_text(t)
                if key:
                    out.texts.setdefault(key, (label, i))
        return out

    def __or__(self, other: Delivered) -> Delivered:
        return Delivered({**other.texts, **self.texts})

    def same_page(self, text: str) -> tuple[str, int] | None:
        """(exhibit, page) of the delivered page that says exactly what `text` says, or None."""
        key = same_text(text)
        return self.texts.get(key) if key else None


@dataclass
class Earlier:
    """The pages of the new documents read so far, in exhibit order.

    A page is a REFILED COPY of an earlier one only when it says EXACTLY what
    one page of an EARLIER DOCUMENT says (``same_text``). Pages of the same
    document are never compared, so a run of template notes inside one record
    is read whole. When in doubt a page is read: collect drops a true repeat
    after compose, while a page set aside here is never seen again."""

    #: {what a page says: (its document's number, its home)}: the first page
    #: to say it, which is from the earliest document that does.
    texts: dict[str, tuple[int, tuple[str, int]]] = field(default_factory=dict)
    doc: int = 0
    name: str = ""  # the current document's id, when the caller names it

    def next_document(self, name: str | None = None) -> None:
        self.doc += 1
        self.name = name or f"new:{self.doc}"

    def add(self, text: str, page: int = 0) -> None:
        key = same_text(text)
        if key:
            self.texts.setdefault(key, (self.doc, (self.name or f"new:{self.doc}", page)))

    def same_page(self, text: str) -> tuple[str, int] | None:
        """(document, page) of the page of an EARLIER document that says exactly what `text` says, or None."""
        key = same_text(text)
        got = self.texts.get(key) if key else None
        return got[1] if got is not None and got[0] != self.doc else None


DELIVERED_SRC, EARLIER_SRC = "delivered", "earlier"


def _decide(
    t: str,
    seen: Delivered,
    earlier: Earlier | None,
    page: int,
    tally: Counter[str] | None,
    covers: dict[int, Cover] | None = None,
) -> tuple[str, str]:
    if t in FAILED_MARKS:
        return UNREAD, "the page could not be transcribed"
    if len(t) < MIN_CHARS:
        return EXCLUDED, BLANK
    if is_billing(t):
        return EXCLUDED, BILLING
    for src, pool, why in ((DELIVERED_SRC, seen, ALREADY), (EARLIER_SRC, earlier, OVERLAP)):
        home = pool.same_page(t) if pool is not None else None
        if home is None:
            continue
        if tally is not None:
            tally[f"the same text as a {src} page"] += 1
        if covers is not None and home[0]:
            covers[page] = Cover(src, home[0], home[1], BY_TEXT)
        return EXCLUDED, why
    return READ, "new document"


def classify_new(
    pages: dict[int, str],
    npages: int,
    seen: Delivered,
    earlier: Earlier | None = None,
    image: dict[int, str] | None = None,
    tally: Counter[str] | None = None,
    covers_out: dict[int, Cover] | None = None,
    doc_id: str | None = None,
) -> dict[int, tuple[str, str]]:
    """Per page of one new document: (text | excluded | unread, reason).
    `unread` is a page the transcription could not read: it is never skipped
    silently, the stage holds on it. `earlier`, when given, holds the pages of
    the new documents before this one; this document's pages join it.
    `image`: {page: ALREADY | OVERLAP} for the pages ``update/pageid`` found
    pixel-equal; those were never transcribed and are excluded first.
    `tally`, when given, counts the pages excluded by their text;
    `covers_out`, when given, receives the page that covers each page the
    text test excluded (``Cover``); `doc_id` names this document in that record."""
    if earlier is not None:
        earlier.next_document(doc_id)
    out: dict[int, tuple[str, str]] = {}
    for i in range(1, npages + 1):
        t = (pages.get(i) or "").strip()
        if image and i in image:
            out[i] = (EXCLUDED, image[i])
            continue
        out[i] = _decide(t, seen, earlier, i, tally, covers_out)
        if earlier is not None and out[i][0] != UNREAD and len(t) >= MIN_CHARS:
            earlier.add(t, i)
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
    composed POSITIVE (``stages/chunking.context_block``). A read page that
    continues a note whose heading page is not read also carries that note's
    heading, or says none was found (``update/noteheading``)."""
    want = set(read)

    def ctx(p: int) -> str:  # a billing page is never context: it is not composed at all
        t = pages.get(p) or ""
        return "" if p in want or is_billing(t) else t

    out = []
    for pg in read:
        pre = NH.heading_block(pages, pg, want.__contains__) + context_block(ctx(pg - 1), pg - 1, before=True)
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


def new_home(
    row: dict[str, Any], pages: dict[int, str], by_doc: dict[str, set[str]], image_doc: dict[int, str]
) -> str | None:
    """The new document a new document RE-FILES WHOLE, or None.

    A document none of whose pages is read, every one a repeat of a page of
    a new document read before it (or blank), is a re-filed copy of that
    document: not an exhibit, cited there. The new documents are read
    largest first (``pageid.reading_order``), so the copy is the smaller one
    and the larger keeps every page they share: a 124-page file that was
    pages 281 to 404 of a 404-page file was filed as a second exhibit and a
    visit was itemized twice (2026-10-14). The home is the document most of
    its pages repeat: by picture (`image_doc`, {page: document id}) or, for a
    page with no picture match, the document read before it sharing the most
    of its shingles (`by_doc`). A document with a page read, unread, billed,
    already delivered, or excluded for any other reason stays an exhibit,
    and what it adds is read."""
    excluded = row["excluded"]
    if row["read"] or row["unread"] or not excluded.get(OVERLAP) or set(excluded) - {OVERLAP, BLANK}:
        return None
    votes: Counter[str] = Counter()
    for p in range(1, int(row["pages"]) + 1):
        d = image_doc.get(p)
        sh = shingles(pages.get(p))
        if d is None and sh and by_doc:
            best = max(by_doc, key=lambda o: len(sh & by_doc[o]))
            d = best if sh & by_doc[best] else None
        if d is not None:
            votes[d] += 1
    return votes.most_common(1)[0][0] if votes else None
