"""Which pages of the NEW documents an update reads. $0.

What the delivery covered is read off the delivered exhibits, never the ledger
alone. A coverage record from an older pipeline can be stale or missing; the
delivered exhibit PDFs are what the firm holds, and they are the page truth.

* A new document byte-identical to a delivered exhibit (or to another new
  document) is not an exhibit: it is skipped by sha256 before this module.
* A TEXT page (at least MIN_CHARS of text) is EXCLUDED when ONE delivered
  page covers COVERED_FRAC of its 8-word shingles (digits stripped: a
  re-filed copy differs in its fax stamp and print date, not in its words)
  and states every word and clinical value it states (``Delivered``): a
  re-filed copy of a record the delivery read. A later report on the same
  template is read.
* A TEXT page that is a REFILED COPY of a page of an EARLIER new document
  (``Earlier.copy_of``: COVERED_FRAC of its shingles, and every date, word and
  clinical value it states, fax and print lines aside) is EXCLUDED: the second
  copy costs a compose read and yields a duplicate entry. Template visits
  differ in a date or a word and are both read; pages within one document are
  never compared.
* A TEXT page that a run of 1 to 3 consecutive pages of ONE delivered
  exhibit, or of ONE earlier new document, holds whole (every date, clinical
  value, number and word token of it, with repeats; export stamps aside and
  no token missing: ``update/contained``) is EXCLUDED the same way: a second export of one
  clinic's records breaks its pages elsewhere (2026-11-17).
* Every comparison here reads a page with its fax, print and export stamps
  aside (``strip_stamps``, ``without_furniture``): a stamp line is known only
  by an anchored pattern, never a clinical date line.
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
from dataclasses import dataclass, field, replace
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ...stages.chunking import context_block
from . import contained as CT, noteheading as NH
from .billing_page import BILLING, is_billing
from .pageid import DELIVERED, SKIPPED
from .records_dates import SERVICE_LINE
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
#: differ in. Its dates and words never tell two visits apart. "Generated on"
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


def without_furniture(text: str) -> str:
    """`text` with every fax, print and page-count line removed
    (``_FURNITURE``) and its stamps (``strip_stamps``)."""
    return _FURNITURE.sub("", strip_stamps(text))


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
    #: (document label, page) of this page: a delivered exhibit ("exhibit:N")
    #: or a new document's id. ("", 0) when nobody named it.
    home: tuple[str, int] = ("", 0)


def _page(doc: int, text: str, home: tuple[str, int] = ("", 0)) -> _Page:
    body = without_furniture(text)
    return _Page(
        doc,
        frozenset(shingles(body)),
        frozenset(_dates(body)),
        frozenset(re.findall(r"[a-z]{4,}", body.lower())),
        frozenset(clinical_values(body)),
        home,
    )


#: How a page was found covered: by its 8-word shingles on one page, held by
#: a run of pages (``update/contained``), or as the twin reading of one page
#: in another new document that adds nothing (``update/twins``).
BY_TEXT, BY_RUN, BY_TWIN, BY_PICTURE = "text", "contained", "twin", "picture"


@dataclass(frozen=True)
class Cover:
    """The page that covers an excluded page: recorded as evidence, never a condition."""

    src: str  # DELIVERED_SRC | EARLIER_SRC
    doc: str  # "exhibit:N" or a new document's id
    page: int
    kind: str  # BY_TEXT | BY_RUN
    last: int = 0  # the run's last page (BY_RUN), when it is not `page`

    def as_dict(self) -> dict[str, Any]:
        n = self.doc[len(DELIVERED) :] if self.doc.startswith(DELIVERED) else ""
        where: dict[str, Any] = {"exhibit": int(n)} if n.isdigit() else {"doc": self.doc}
        return {**where, "page": self.page, **({"last": self.last} if self.last else {}), "exact": False}


def service_dates(text: str) -> set[str]:
    """The dates `text` states on a line that dates care (``records_dates.SERVICE_LINE``)."""
    return {d for ln in (text or "").splitlines() if SERVICE_LINE.search(ln) for d in _dates(ln)}


class _Homes:
    """Where each page of a pool came from, and each document's dates."""

    def __init__(self) -> None:
        self.homes: list[tuple[str, int]] = []
        self.doc_dates: dict[str, set[str]] = {}

    def note(self, home: tuple[str, int], text: str) -> None:
        self.homes.append(home)
        self.doc_dates.setdefault(home[0], set()).update(_dates(text or ""))

    def cover(self, src: str, old: _Page | None, at: tuple[int, int] | None) -> Cover | None:
        if old is not None and old.home[0]:
            return Cover(src, old.home[0], old.home[1], BY_TEXT)
        if at is not None and 0 <= at[0] < at[1] <= len(self.homes) and self.homes[at[0]][0]:
            (d, p), last = self.homes[at[0]], self.homes[at[1] - 1][1]
            return Cover(src, d, p, BY_RUN, last if last != p else 0)
        return None

    def lacks_service_date(self, text: str, c: Cover) -> bool:
        """The page dates care on a day its cover's document never states."""
        return bool(service_dates(text) - self.doc_dates.get(c.doc, set()))


@dataclass
class Delivered:
    """The delivered exhibits' pages, each kept whole.

    A new page is ALREADY READ only when ONE delivered page covers
    COVERED_FRAC of its shingles AND states every word and clinical value it
    states. Coverage pooled across all delivered pages is not enough: a
    practice's later follow-up shares its template with the earlier ones the
    delivery read, so its exam, diagnoses, plan, causation opinion and
    signature page were dropped before compose (2026-10-14: a four-page report
    and a signature lost). Dates are left out: a re-printed copy carries a new
    print date and is still the record the delivery read.

    Or when a run of pages of ONE delivered exhibit holds it across a
    pagination shift, dates included (``update/contained``)."""

    pages: list[_Page] = field(default_factory=list)
    pool: CT.Pool = field(default_factory=CT.Pool)
    at: _Homes = field(default_factory=_Homes)

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
                out.pages.append(_page(-1, t, (label, i)))
                out.pool.add(CT.facts(-1 - k, t))
                out.at.note((label, i), t)
        return out

    def __or__(self, other: Delivered) -> Delivered:
        out = Delivered(self.pages + other.pages)
        top = min((f.doc for f in self.pool.items), default=0)
        for f in self.pool.items:
            out.pool.add(f)
        for f in other.pool.items:
            out.pool.add(replace(f, doc=f.doc + top))
        for side in (self.at, other.at):
            out.at.homes += side.homes
            for d, ds in side.doc_dates.items():
                out.at.doc_dates.setdefault(d, set()).update(ds)
        return out

    def by_shingles(self, text: str) -> _Page | None:
        """The ONE delivered page that covers `text`, or None."""
        me = _page(-1, text)
        if not me.shingles:
            return None
        need = COVERED_FRAC * len(me.shingles)
        return next(
            (
                e
                for e in self.pages
                if len(me.shingles & e.shingles) >= need and me.words <= e.words and me.values <= e.values
            ),
            None,
        )

    def contained(self, text: str, near: int | None = None) -> tuple[int, int] | None:
        """(start, end) of the run of delivered pages holding the page (``update/contained``), or None."""
        return self.pool.find(CT.facts(0, text), near=near)

    def covers(self, text: str) -> bool:
        return self.by_shingles(text) is not None or self.contained(text) is not None


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
    is never seen again.

    Or when a run of pages of ONE earlier document holds it across a
    pagination shift (``update/contained``): two overlapping exports of one
    clinic's records."""

    pages: list[_Page] = field(default_factory=list)
    doc: int = 0
    pool: CT.Pool = field(default_factory=CT.Pool)
    at: _Homes = field(default_factory=_Homes)
    name: str = ""  # the current document's id, when the caller names it

    def next_document(self, name: str | None = None) -> None:
        self.doc += 1
        self.name = name or f"new:{self.doc}"

    def add(self, text: str, page: int = 0) -> None:
        home = (self.name or f"new:{self.doc}", page)
        self.pages.append(_page(self.doc, text, home))
        self.pool.add(CT.facts(self.doc, text))
        self.at.note(home, text)

    def by_shingles(self, text: str) -> _Page | None:
        """The ONE page of an earlier document `text` is a refiled copy of, or None."""
        me = _page(self.doc, text)
        if not me.shingles:
            return None
        for e in self.pages:
            if e.doc == self.doc or len(me.shingles & e.shingles) < COVERED_FRAC * len(me.shingles):
                continue
            if me.dates <= e.dates and me.words <= e.words and me.values <= e.values:
                return e
        return None

    def contained(self, text: str, near: int | None = None) -> tuple[int, int] | None:
        return self.pool.find(CT.facts(self.doc, text), skip=self.doc, near=near)

    def copy_of(self, text: str) -> bool:
        return self.by_shingles(text) is not None or self.contained(text) is not None


#: {page: ("delivered" | "earlier", window start, window end, held by
#: ``update/contained`` alone)}: where each page it held sits in its pool,
#: for the undated-neighbour pass and the measuring command.
_Held = dict[int, tuple[str, int, int, bool]]
DELIVERED_SRC, EARLIER_SRC = "delivered", "earlier"


#: Why a page a text rule matched is read after all (``_decide``).
SERVICE_DATE_UNCOVERED = "read: it dates care on a day the covering document never states"


def _decide(
    t: str,
    seen: Delivered,
    earlier: Earlier | None,
    held: _Held,
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
        if pool is None:
            continue
        old, at = pool.by_shingles(t), pool.contained(t)
        if old is None and at is None:
            continue
        # The page that covers it is RECORDED (``covers``); a page that dates
        # care on a day the covering document never states is read (a later
        # visit on the same template, 2026-11-17).
        cov = pool.at.cover(src, old, at)
        if cov is not None and pool.at.lacks_service_date(t, cov):
            _count(tally, SERVICE_DATE_UNCOVERED)
            continue
        if at is not None:
            held[page] = (src, at[0], at[1], old is None)  # where it sits, for an undated neighbour
        if old is None:
            _count(tally, f"held by a run of {src} pages")
        if covers is not None and cov is not None:
            covers[page] = cov
        return EXCLUDED, why
    return READ, "new document"


def _count(tally: Counter[str] | None, key: str) -> None:
    if tally is not None:
        tally[key] += 1


def _undated_neighbours(
    out: dict[int, tuple[str, str]],
    pages: dict[int, str],
    seen: Delivered,
    earlier: Earlier | None,
    held: _Held,
    tally: Counter[str] | None,
    covers: dict[int, Cover] | None = None,
    force: set[int] | None = None,
) -> None:
    """A read page that states no date, beside a page ``update/contained``
    held, is held when the same document's pages near that window hold it.
    A page in `force` (the repeat check read it) is never held here."""
    changed = True
    while changed:
        changed = False
        for p, (d, _) in sorted(out.items()):
            t = (pages.get(p) or "").strip()
            if d != READ or p in held or p in (force or ()) or CT.facts(0, t).dates:
                continue
            for q in (p - 1, p + 1):
                if q not in held:
                    continue
                src, at = held[q][:2]
                got = seen.contained(t, near=at) if src == DELIVERED_SRC else None
                if src == EARLIER_SRC and earlier is not None:
                    got = earlier.contained(t, near=at)
                pool: Delivered | Earlier | None = seen if src == DELIVERED_SRC else earlier
                cov = pool.at.cover(src, None, got) if pool is not None and got is not None else None
                if got is not None:
                    if covers is not None and cov is not None:  # recorded, never a condition
                        covers[p] = cov
                    held[p] = (src, got[0], got[1], True)
                    out[p] = (EXCLUDED, ALREADY if src == DELIVERED_SRC else OVERLAP)
                    _count(tally, f"held beside a held page ({src})")
                    changed = True
                    break


def classify_new(
    pages: dict[int, str],
    npages: int,
    seen: Delivered,
    earlier: Earlier | None = None,
    image: dict[int, str] | None = None,
    tally: Counter[str] | None = None,
    held_out: _Held | None = None,
    covers_out: dict[int, Cover] | None = None,
    doc_id: str | None = None,
    force: set[int] | None = None,
) -> dict[int, tuple[str, str]]:
    """Per page of one new document: (text | excluded | unread, reason).
    `force`: pages the repeat check found are NOT copies of the page a
    repeat rule matched (``update/repeat_check``): each is read as if no
    repeat rule had matched it, and no neighbour rule sets it aside.
    `unread` is a page the transcription could not read: it is never skipped
    silently, the stage holds on it. `earlier`, when given, holds the pages of
    the new documents before this one; this document's pages join it.
    `image`: {page: ALREADY | OVERLAP} for the pages ``update/pageid`` found
    by their picture; those were never transcribed and are excluded first.
    `tally`, when given, counts the pages ``update/contained`` held;
    `held_out`, when given, receives where each of them sits (``_Held``);
    `covers_out`, when given, receives the page that covers each page a text
    rule excluded (``Cover``); `doc_id` names this document in that record."""
    if earlier is not None:
        earlier.next_document(doc_id)
    out: dict[int, tuple[str, str]] = {}
    held: _Held = {}
    force = force or set()
    for i in range(1, npages + 1):
        t = (pages.get(i) or "").strip()
        if image and i in image and i not in force:
            out[i] = (EXCLUDED, image[i])
            continue
        out[i] = _decide(t, seen, earlier, held, i, tally, covers_out)
        if i in force and out[i] in ((EXCLUDED, ALREADY), (EXCLUDED, OVERLAP)):
            out[i] = (READ, "new document")
            held.pop(i, None)
            if covers_out is not None:
                covers_out.pop(i, None)
        if earlier is not None and out[i][0] != UNREAD and len(t) >= MIN_CHARS:
            earlier.add(t, i)
    if held:
        _undated_neighbours(out, pages, seen, earlier, held, tally, covers_out, force)
    if held_out is not None:
        held_out.update(held)
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
