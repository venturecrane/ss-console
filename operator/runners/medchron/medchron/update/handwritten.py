"""A date read off a HANDWRITTEN page is checked against the typed record. $0.

The vision pass opens a page whose dates or entries are handwritten with the
line ``[handwritten]`` (``stages/vision.SYSTEM``). A handwritten 7 reads as a
1, a 3 as an 8; the visit is real, its date is a reading. The typed
statements and bills of the same provider state the dates of service, so:

* the reading is a typed date of service of that provider: confirmed;
* between the reading's confirmed neighbours (or the statement's start or
  end) as many statement dates no entry reads as unconfirmed readings: the
  readings take those dates in chronological order, within 7 days when the
  interval is open at either end (``decide``);
* anything else: the date is unsupported, and the entry shows the
  reading as written, marked ``MARK_OPEN`` "(handwritten date)": a fact
  about the record, like ``body.RECORDS_MARK``, never a guess between
  candidates and never words about our process. That the date is unsettled
  is SMD's to know: the stage logs it and ``compose_report.json`` keeps it.

A handwritten LEDGER page lists its visits as rows in the order they were
seen, so the ROW ORDER is evidence too (``by_row_order``): a row sits between
its neighbours' dates. A misread row ("1/4" between "1/14" and "1/28") takes
the one unread statement date between its confirmed neighbour rows, never a
date chosen by sorting the misreadings (2026-10-14 parity run: "1/3", "1/4",
"7/18", "7/25" were 12/3, 1/21, 2/18, 2/25, and the chronology came out of
order).

A statement is the provider's when it names the provider, or when it is a
typed page of the SAME document as the handwritten page (a chiropractor's
"records and bills" file: the itemized statement prints the doctor and the
street address, never the trade name the chronology uses; 2026-10-14 parity
run, where no statement was found and every visit stayed unconfirmed). Dates
read as MM/DD/YY or MM-DD-YY, and a line item may sit in a table cell.

A ledger row may show a month and day only, and compose then writes a
heading with no year ("07/18 (year not stated)"). That heading is an entry of
its own (``body.HEAD``); ``with_years`` gives it the year of the entries around
it so it can be placed, marked ``NO_YEAR``, and the ROW ORDER settles its date
like any row's (row order reads month and day, never the year). A yearless
row the row order cannot settle stays open: shown with that year, marked, and
listed for SMD; never ``decide``'s guess (2026-10-14 parity run, where two
such rows rode inside an unrelated entry with the wrong month).

The audit of a claim cited to a handwritten page is given the same typed
statements as evidence (``statement_block``).
"""

from __future__ import annotations

import re
from datetime import date

from . import body as B

MARK = "[handwritten]"
MARK_OPEN = "(handwritten date)"
#: The marker an earlier version wrote; words about our process, never kept.
LEGACY_OPEN = "(handwritten date, unconfirmed)"
#: A date line whose year the record does not state (``with_years``).
NO_YEAR = "(year not stated in the record)"
WINDOW_DAYS = 7
#: What the audit is told about a claim cited to a handwritten page.
REREAD = (
    "The cited page is handwritten. Read every date, vital sign (blood pressure, pulse, temperature, weight), "
    "dose and other number the claim states directly off the page image, digit by digit; a transcription of "
    "handwriting misreads digits. A value the image does not clearly show as the claim states it is unsupported."
)
STATEMENT = re.compile(
    r"(?i)\b(?:statement|date of service|dates of service|dos|charges?|balance due|ledger|invoice|itemi[sz]ed)\b"
)
_DATE = re.compile(r"(?<![\d/-])(\d{1,2})([/-])(\d{1,2})\2(\d{4}|\d{2})(?![\d/-])")
_GENERIC = {
    "clinic", "center", "centre", "medical", "health", "group", "associates", "hospital", "care", "services",
    "chiropractic", "therapy", "physical", "imaging", "radiology", "orthopedic", "orthopedics", "family",
    "urgent", "practice", "office", "institute", "inc", "llc",
}  # fmt: skip


def is_handwritten(page_text: str | None) -> bool:
    return (page_text or "").lstrip().lower().startswith(MARK)


def _names(provider: str, text: str) -> bool:
    """True when `text` carries the provider's distinctive words (all of them)."""
    ws = [w for w in re.findall(r"[a-z]{3,}", provider.split(",")[0].lower()) if w not in _GENERIC]
    if not ws:
        ws = re.findall(r"[a-z]{4,}", provider.lower())
    low = (text or "").lower()
    return bool(ws) and all(re.search(rf"\b{re.escape(w)}\b", low) for w in ws)


def _dates(text: str) -> set[date]:
    out: set[date] = set()
    for mm, _sep, dd, yy in _DATE.findall(text or ""):
        y = int(yy) + (2000 if len(yy) == 2 else 0)
        try:
            out.add(date(y, int(mm), int(dd)))
        except ValueError:
            continue
    return out


def _typed_statement(t: str | None) -> bool:
    return not is_handwritten(t) and bool(STATEMENT.search(t or ""))


def statements(pages: list[str], provider: str, same_doc: list[str] | tuple[str, ...] = ()) -> list[str]:
    """The typed statement and bill pages of `pages` that name `provider`,
    and those of `same_doc` (the handwritten page's own document), named or
    not. Each page once, in order."""
    got = [t for t in same_doc if _typed_statement(t)]
    got += [t for t in pages if _typed_statement(t) and _names(provider, t)]
    return list(dict.fromkeys(got))


_DOS_LABEL = re.compile(r"(?i)\b(?:dos|dates? of service|service date)\b")
_LINE_ITEM = re.compile(
    r"^[\s|\u2502\u2503]*\d{1,2}([/-])\d{1,2}\1\d{2,4}\b.*(?:\b\d{5}\b|\b[A-Z]\d{4}\b|\$?\d+\.\d{2}\b)"
)
#: A statement's other dates: when it was printed, due, paid or posted.
_NOT_SERVICE = re.compile(r"(?i)\b(?:statement date|due|payment|paid|print|billing date|posted|balance|deposit)\b")


def service_dates(pages: list[str], provider: str, same_doc: list[str] | tuple[str, ...] = ()) -> set[date]:
    """Dates of SERVICE on the provider's typed statements: a date on a line
    labelled DOS / Date of Service / Service Date, or the date leading a
    line-item row that carries a procedure code or a charge. A statement's
    print, due and payment dates never count; anything unclear counts as no
    date, and the visit then shows its reading as unconfirmed."""
    out: set[date] = set()
    for t in statements(pages, provider, same_doc):
        for ln in t.splitlines():
            if _NOT_SERVICE.search(ln):
                continue
            if _DOS_LABEL.search(ln) or _LINE_ITEM.match(ln):
                out |= _dates(ln)
    return out


def decide(reading: date, dos: set[date], readings: tuple[date, ...] = ()) -> tuple[str, date | None]:
    """('confirmed' | 'redated', the date) or ('open', None).

    `readings` are every handwritten reading of the provider (this one
    included). A reading on the statement is confirmed. The others are
    settled per INTERVAL between confirmed neighbouring visits (or the
    statement's start or end): when the interval holds as many unconfirmed
    readings as statement dates no entry reads (at least one), visits being
    chronological, the i-th reading in date order is the i-th unread date.
    Between two confirmed visits the bracket is the constraint and no day
    window applies; at the statement's start or end only one side bounds the
    interval, so each pair must also lie within 7 days.
    Unequal counts, or two entries sharing one unconfirmed reading, leave
    every reading of the interval open: a guess moves one visit onto
    another (2026-10-14 parity run)."""
    if reading in dos:
        return "confirmed", reading
    ok = sorted({r for r in readings if r in dos})
    lo = max((d for d in ok if d < reading), default=date.min)
    hi = min((d for d in ok if d > reading), default=date.max)
    free = sorted(d for d in dos if lo < d < hi and d not in ok)
    every = readings if reading in readings else (*readings, reading)
    mine = sorted(r for r in every if lo < r < hi and r not in dos)
    if not free or len(set(mine)) != len(mine) or len(mine) != len(free):
        return "open", None
    pairs = list(zip(mine, free))
    if any(not lo < d < hi for _, d in pairs) or any(a[1] >= b[1] for a, b in zip(pairs, pairs[1:])):
        return "open", None  # never out of order with its neighbours
    bracketed = lo != date.min and hi != date.max
    if not bracketed and any(abs((d - r).days) > WINDOW_DAYS for r, d in pairs):
        return "open", None  # at the statement's edge only one side bounds it: the 7-day window stands in
    return "redated", dict(pairs)[reading]


_ROW = re.compile(r"^[\s|\u2502\u2503]*(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?(?![\d/-])", re.M)


def rows(page_text: str | None) -> list[tuple[int, int]]:
    """(month, day) of each row of a HANDWRITTEN ledger page, in page order:
    every line that opens with a date. Empty for a typed page."""
    if not is_handwritten(page_text):
        return []
    found = ((int(m.group(1)), int(m.group(2))) for m in _ROW.finditer(page_text or ""))
    return [(mo, dd) for mo, dd in found if 1 <= mo <= 12 and 1 <= dd <= 31]


def _near(md: tuple[int, int], d: date) -> bool:
    """A misreading `md` could be `d`: the same month, the same day, or
    within the window (a 7 read as a 2 keeps the day; a 21 read as a 4 keeps
    the month)."""
    if md[0] == d.month or md[1] == d.day:
        return True
    try:
        r = date(d.year, md[0], md[1])
    except ValueError:
        return False
    return abs((r - d).days) <= WINDOW_DAYS


def _confirmed(page_rows: list[tuple[int, int]], dos: set[date]) -> dict[int, date]:
    """{row: its statement date} for the rows whose reading is exactly one
    statement date, kept only as the LONGEST run in date order: a row that
    matches a statement date out of order is a misreading that happens to
    hit another visit's date, never an anchor."""
    cand = [(i, c[0]) for i, md in enumerate(page_rows) if len(c := [d for d in dos if (d.month, d.day) == md]) == 1]
    best: list[list[tuple[int, date]]] = []
    for k, (i, d) in enumerate(cand):
        prev = [best[j] for j in range(k) if cand[j][1] < d]
        best.append(max(prev, key=len, default=[]) + [(i, d)])
    return dict(max(best, key=len, default=[]))


def by_row_order(page_rows: list[tuple[int, int]], dos: set[date]) -> dict[int, date]:
    """{row index: date of service} the ledger's ROW ORDER settles.

    Rows whose readings are statement dates in order are confirmed
    (``_confirmed``). The rows between two confirmed rows take the unread
    statement dates between those two dates, in order, when the counts are
    equal: a row sits between its neighbours. Above the first or below the
    last confirmed row only one side bounds the interval, so each row must
    also plausibly be a misreading of its date (``_near``). Unequal counts
    leave the interval's rows unsettled."""
    conf = _confirmed(page_rows, dos)
    out = dict(conf)
    used = set(conf.values())
    bounds = [(-1, date.min), *sorted(conf.items()), (len(page_rows), date.max)]
    for (a, lo), (b, hi) in zip(bounds, bounds[1:]):
        mine = list(range(a + 1, b))
        free = sorted(d for d in dos if lo < d < hi and d not in used)
        if not mine or len(mine) != len(free):
            continue
        if (lo == date.min or hi == date.max) and not all(_near(page_rows[i], d) for i, d in zip(mine, free)):
            continue
        out.update(zip(mine, free))
    return out


def row_verdict(
    reading: date, ledger: list[tuple[int, int]], dos: set[date], on: set[int] | None = None
) -> tuple[str, date | None] | None:
    """The ledger's verdict on a reading taken off one of its rows (`on`: the
    row indexes on the entry's cited pages; every row when None). None when
    the reading is no such row, or more than one (``decide`` stands)."""
    at = [i for i, md in enumerate(ledger) if md == (reading.month, reading.day) and (on is None or i in on)]
    if len(at) != 1:
        return None
    got = by_row_order(ledger, dos).get(at[0])
    if got is None:
        return "open", None
    return ("confirmed" if got == reading else "redated"), got


_MONTH = re.compile(r"(?i)\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})\b")
_MD = re.compile(r"(?<![\d/.-])(\d{1,2})[/-](\d{1,2})(?![\d/.-])")
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


def on_page(d: date, text: str | None, year: int | None = None) -> bool:
    """True when the page's text states `d`: as a full date, or, on a
    HANDWRITTEN page whose rows carry no year, as its month and day when
    `year` (the entry's own) is `d`'s year."""
    t = text or ""
    if d in _dates(t):
        return True
    for mon, dd, yy in _MONTH.findall(t):
        if (int(yy), _MONTHS.index(mon.lower()[:3]) + 1, int(dd)) == (d.year, d.month, d.day):
            return True
    if not is_handwritten(t) or year != d.year:
        return False
    return any((int(mo), int(dd)) == (d.month, d.day) for mo, dd in _MD.findall(t))


def _mdy(d: date) -> str:
    return f"{d.month:02d}/{d.day:02d}/{d.year}"


def reading_of(entry: str) -> date | None:
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", entry)
    try:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2))) if m else None
    except ValueError:
        return None


_YEARLESS = re.compile(r"(\d{1,2})/(\d{1,2})(?![/\d])(.*)")
_FULL = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
#: A yearless heading's own note about its date ("(log date as written; year
#: not stated)"): replaced by ``NO_YEAR``; any other parenthetical is kept.
_DATE_NOTE = re.compile(r"(?i)\b(?:year|date|as written)\b")


def _exhibits(e: str) -> set[str]:
    return set(re.findall(r"\(Exhibit (\d+)", e))


def _year_for(i: int, entries: list[str], md: tuple[int, int], fallback: int) -> int:
    """The year of entry `i`'s month and day `md`: that of the nearest EARLIER
    dated entry citing one of its exhibits (a ledger runs in order, so the next
    year when `md` falls before it), else of the nearest later one, else
    `fallback`."""
    mine = _exhibits(entries[i])
    near = [
        j
        for j in range(len(entries))
        if j != i and _FULL.match(entries[j]) and B.is_service(entries[j]) and mine & _exhibits(entries[j])
    ]  # a printout's as-of date is no visit's neighbour
    before = [j for j in near if j < i]
    j = before[-1] if before else (near[0] if near else None)
    m = _FULL.match(entries[j]) if j is not None else None
    if j is None or m is None:
        return fallback
    y, prev = int(m.group(3)), (int(m.group(1)), int(m.group(2)))
    return y + 1 if j < i and md < prev else y


def with_years(entries: list[str], fallback: int) -> tuple[list[str], int]:
    """(entries, how many were yearless). Each entry whose date line has a
    month and day only takes the year of the entries around it
    (``_year_for``) and the mark ``NO_YEAR`` in place of compose's own note on
    the date; the re-date then settles it from the ledger's row order."""
    out, n = [], 0
    for i, e in enumerate(entries):
        head, nl, rest = e.partition("\n")
        m = _YEARLESS.match(head)
        if not m:
            out.append(e)
            continue
        md = (int(m.group(1)), int(m.group(2)))
        y = _year_for(i, entries, md, fallback)
        try:
            date(y, *md)
        except ValueError:
            out.append(e)  # no such day: left as composed, never moved onto another
            continue
        keep = "".join(" " + p for p in re.findall(r"\([^)]*\)", m.group(3)) if not _DATE_NOTE.search(p))
        out.append(f"{md[0]:02d}/{md[1]:02d}/{y} {NO_YEAR}{keep}{nl}{rest}")
        n += 1
    return out, n


def redate(
    entry: str,
    dos: set[date],
    readings: tuple[date, ...] = (),
    settled: tuple[str, date | None] | None = None,
) -> tuple[str, dict[str, str]]:
    """(the entry with its date settled, a log row). The entry's own date is
    the handwritten reading; `readings` are the provider's others (``decide``).
    `settled` is a verdict already reached from the ledger's row order
    (``row_verdict``), which then stands instead of ``decide``."""
    head, _, rest = entry.partition("\n")
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", head)
    if not m:
        return entry, {"entry": head, "verdict": "no date"}
    reading = date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    yearless = NO_YEAR in head
    tail = head[m.end() :]
    for mark in (LEGACY_OPEN, MARK_OPEN, NO_YEAR):
        tail = tail.replace(" " + mark, "")  # the rest is kept
    if settled is None and yearless:
        settled = ("open", None)  # its year is the neighbours': only the row order settles it
    verdict, got = settled if settled is not None else decide(reading, dos, readings)
    if got is not None:
        return _mdy(got) + tail + "\n" + rest, {"reading": _mdy(reading), "verdict": verdict, "date": _mdy(got)}
    shown = f"{MARK_OPEN} {NO_YEAR}" if yearless else MARK_OPEN
    log = {"reading": _mdy(reading), "verdict": "open", **({"year": "not stated"} if yearless else {})}
    return f"{_mdy(reading)} {shown}{tail}\n" + rest, log


def statement_block(
    pages: list[str], provider: str, limit: int = 12_000, same_doc: list[str] | tuple[str, ...] = ()
) -> str:
    """The audit's extra evidence for a claim cited to a handwritten page."""
    got = statements(pages, provider, same_doc)
    if not got:
        return ""
    body = "\n\n".join(t.strip() for t in got)[:limit]
    return (
        "Typed statements and bills of the same provider follow, for the date of service only; the claim's "
        "other facts are judged on the cited page.\n\n" + body
    )
