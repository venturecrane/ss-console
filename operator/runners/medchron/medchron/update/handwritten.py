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
  reading as an OPEN date, "MM/DD/YYYY (handwritten date, unconfirmed)",
  never a guess between candidates.

A statement is the provider's when it names the provider, or when it is a
typed page of the SAME document as the handwritten page (a chiropractor's
"records and bills" file: the itemized statement prints the doctor and the
street address, never the trade name the chronology uses; 2026-10-14 parity
run, where no statement was found and every visit stayed unconfirmed). Dates
read as MM/DD/YY or MM-DD-YY, and a line item may sit in a table cell.

The audit of a claim cited to a handwritten page is given the same typed
statements as evidence (``statement_block``).
"""

from __future__ import annotations

import re
from datetime import date

MARK = "[handwritten]"
OPEN = "(handwritten date, unconfirmed)"
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


def _mdy(d: date) -> str:
    return f"{d.month:02d}/{d.day:02d}/{d.year}"


def reading_of(entry: str) -> date | None:
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", entry)
    try:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2))) if m else None
    except ValueError:
        return None


def redate(entry: str, dos: set[date], readings: tuple[date, ...] = ()) -> tuple[str, dict[str, str]]:
    """(the entry with its date settled, a log row). The entry's own date is
    the handwritten reading; `readings` are the provider's others (``decide``)."""
    head, _, rest = entry.partition("\n")
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", head)
    if not m:
        return entry, {"entry": head, "verdict": "no date"}
    reading = date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    tail = head[m.end() :].replace(" " + OPEN, "")  # whatever else the date line carried is kept
    verdict, got = decide(reading, dos, readings)
    if got is not None:
        return _mdy(got) + tail + "\n" + rest, {"reading": _mdy(reading), "verdict": verdict, "date": _mdy(got)}
    return f"{_mdy(reading)} {OPEN}{tail}\n" + rest, {"reading": _mdy(reading), "verdict": "open"}


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
