"""The facts a piece of drafted text states, and which of them nothing known holds.

WHY (2026-11-05, a District discovery job). The audit flagged a request, and
the model's repair rewrote it on a premise that appears in no source and no
earlier text: a repair ADDED a fact. A repair may remove a defect or correct
it to what the file says; it may never bring in something new. Code checks
that, not a model: every proper name, date, number, dollar figure and quoted
string in repaired text must already appear in the job's sources (the digest
and the record text the lane holds) or in the text being repaired.

What counts as each kind, read with the parsers the lane already trusts:

* a **date**: a numeric or ISO date (``howell.parse_date``) or a month-name
  date (``evidence.verify.parse_date``); a month and day with no year is
  known when any known date or mention carries that month and day;
* a **dollar figure**: a ``$`` amount or a two-decimal figure, compared by
  value (``howell.money``): "$1,500" is known from a bill's "1500.00";
* a **number**: any other figure, compared by its digits; a request's own
  label number ("No. 7") is structure, never a fact;
* a **name**: a capitalized word that does not start a sentence or a line,
  known when the word appears anywhere known, in any case: a name recombined
  from known words is not caught here, an invented one is;
* a **quoted string**: known when it appears in the known text (case and
  whitespace loosened). A quoted defined-term label (``sets._TERM``: "INCIDENT")
  is a label, not a quotation; its words are names like any other.

Text inside a ``{{...}}`` marker states nothing: a repair that removes a fact
and leaves a marker for the attorney adds no fact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from functools import lru_cache

from . import howell, sets
from .evidence import verify

_MARKER = re.compile(r"\{\{.*?\}\}", re.S)
_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
    r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
_LONG_DATE = re.compile(rf"\b({_MONTH})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}})\b)?")
_NUM_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[/-]\d{1,2}[/-](?:\d{4}|\d{2})\b")
_MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d{1,2})?|\b\d[\d,]*\.\d{2}\b")
_NUMBER = re.compile(r"(?<![\w$])\d[\d,]*(?:\.\d+)?(?!\w)")
_LABEL_NO = re.compile(r"(?i)\bnos?\.?\s*\d+(?:\s*(?:-|through|to|and|,)\s*\d+)*")
_QUOTE = re.compile(r"\"([^\"\n]{2,}?)\"|“([^”\n]{2,}?)”")
_WORD = re.compile(r"[A-Za-z][A-Za-z'’]*")
_LINE_LEAD = re.compile(r"(?:^|\n)[ \t]*(?:[-*+>#|]+[ \t]*|\d+[.)][ \t]*|\(?[a-z0-9]{1,3}\)[ \t]*)*$")
_MONTHS = {m: i for i, m in enumerate(("jan feb mar apr may jun jul aug sep oct nov dec").split(), 1)}


def _strip(text: str) -> str:
    return _MARKER.sub(" ", text)


def _flat(s: str) -> str:
    return " ".join(s.replace("“", '"').replace("”", '"').replace("’", "'").lower().split())


def _word(w: str) -> str:
    w = w.replace("’", "'").lower()
    return w[:-2] if w.endswith("'s") else w.strip("'")


def _digits(s: str) -> str:
    return s.replace(",", "").lstrip("0") or "0"


def _initial(text: str, start: int) -> bool:
    """The word at ``start`` begins a sentence, a line, a list item or a quotation."""
    before = text[:start].rstrip(" \t\"“'‘([*_")
    if not before or before.endswith(("\n", ".", "!", "?", ":", ";")):
        return True
    return bool(_LINE_LEAD.search(text[:start].rstrip(" \t\"“'‘([*_")))


def _md(month: str, day: str) -> tuple[int, int] | None:
    m = _MONTHS.get(month[:3].lower())
    d = int(day)
    return (m, d) if m and 1 <= d <= 31 else None


@dataclass
class Found:
    """The facts one text states, each with how it was printed."""

    dates: list[tuple[date, str]] = field(default_factory=list)
    month_days: list[tuple[tuple[int, int], str]] = field(default_factory=list)
    money: list[tuple[Decimal, str]] = field(default_factory=list)
    numbers: list[tuple[str, str]] = field(default_factory=list)
    names: list[tuple[str, str]] = field(default_factory=list)
    quotes: list[tuple[str, str]] = field(default_factory=list)


def _blank(text: str, spans: list[tuple[int, int]]) -> str:
    """``text`` with each span replaced by spaces (positions kept)."""
    for a, b in sorted(spans, reverse=True):
        text = text[:a] + " " * (b - a) + text[b:]
    return text


def _long_date(m: re.Match[str]) -> date | None:
    for month in (m.group(1), m.group(1)[:3]):
        got = verify.parse_date(f"{month} {int(m.group(2))}, {m.group(3)}")
        if got is not None:
            return got
    return None


def _dates(t: str, out: Found) -> list[tuple[int, int]]:
    """The dates ``t`` states, into ``out``; the spans they occupy."""
    spans: list[tuple[int, int]] = []
    for m in _LONG_DATE.finditer(t):
        md = _md(m.group(1), m.group(2))
        if md is None:
            continue
        spans.append(m.span())
        got = _long_date(m) if m.group(3) else None
        if got is not None:
            out.dates.append((got, m.group(0)))
        else:
            out.month_days.append((md, m.group(0)))
    for m in _NUM_DATE.finditer(t):
        got = howell.parse_date(m.group(0))
        if got is not None:
            spans.append(m.span())
            out.dates.append((got, m.group(0)))
    return spans


def _figures(rest: str, out: Found) -> None:
    """The dollar figures, then every other number, of ``rest`` (dates blanked)."""
    rest = _LABEL_NO.sub(lambda m: " " * len(m.group(0)), rest)
    taken: list[tuple[int, int]] = []
    for m in _MONEY.finditer(rest):
        v = howell.money(m.group(0))
        if v is not None:
            out.money.append((v, m.group(0)))
            taken.append(m.span())
    out.numbers = [(_digits(m.group(0)), m.group(0)) for m in _NUMBER.finditer(_blank(rest, taken))]


def _quotes(t: str) -> list[tuple[str, str]]:
    out = []
    for m in _QUOTE.finditer(t):
        q = m.group(1) or m.group(2) or ""
        if sets._TERM.fullmatch(m.group(0).replace("“", '"').replace("”", '"')):
            continue  # a defined-term label; its words are read as names
        if q.strip():
            out.append((_flat(q), q))
    return out


def _names(nodates: str, t: str) -> list[tuple[str, str]]:
    """Capitalized words not starting a sentence or a line (a month's name is the date's)."""
    out = []
    for m in _WORD.finditer(nodates):
        w = m.group(0)
        if len(w) >= 2 and w[0].isupper() and not _initial(t, m.start()):
            out.append((_word(w), w))
    return out


def read(text: str) -> Found:
    """Every fact ``text`` states (``Known`` reads sources the same way)."""
    t = _strip(text)
    out = Found()
    nodates = _blank(t, _dates(t, out))
    _figures(nodates, out)
    out.quotes = _quotes(t)
    out.names = _names(nodates, t)
    return out


@dataclass
class Known:
    """What the job's sources and the text being repaired already hold."""

    dates: set[date]
    month_days: set[tuple[int, int]]
    money: set[Decimal]
    numbers: set[str]
    words: set[str]
    flat: str

    def plus(self, text: str) -> "Known":
        other = known(text)
        return Known(
            self.dates | other.dates,
            self.month_days | other.month_days,
            self.money | other.money,
            self.numbers | other.numbers,
            self.words | other.words,
            self.flat + " \n " + other.flat,
        )


@lru_cache(maxsize=4)
def known(text: str) -> Known:
    """``text`` read as a source: every fact it holds, for ``new_facts``."""
    f = read(text)
    t = _strip(text)
    dates = {d for d, _ in f.dates}
    mds = {md for md, _ in f.month_days} | {(d.month, d.day) for d in dates}
    # Every figure a source prints is a known number, including a date's or an
    # amount's own parts ("2024", "1500"): the comparison is with what is new.
    numbers = {_digits(m.group(0)) for m in _NUMBER.finditer(t)} | {n for n, _ in f.numbers}
    money = {v for v, _ in f.money}
    grouped = (howell.money(m.group(0)) for m in re.finditer(r"\b\d{1,3}(?:,\d{3})+\b", t))
    money |= {v for v in grouped if v is not None}
    words = {_word(m.group(0)) for m in _WORD.finditer(t)}
    return Known(dates, mds, money, numbers, words, _flat(t))


def new_facts(text: str, base: Known) -> list[tuple[str, str]]:
    """(kind, as printed) for every fact ``text`` states that ``base`` does not hold."""
    f = read(text)
    out: list[tuple[str, str]] = []
    out += [("date", raw) for d, raw in f.dates if d not in base.dates]
    out += [("date", raw) for md, raw in f.month_days if md not in base.month_days]
    out += [("dollar figure", raw) for v, raw in f.money if v not in base.money]
    out += [("number", raw) for n, raw in f.numbers if n not in base.numbers]
    out += [("name", raw) for w, raw in f.names if w not in base.words]
    out += [("quoted string", raw) for q, raw in f.quotes if q not in base.flat]
    return list(dict.fromkeys(out))


__all__ = ["Found", "Known", "known", "new_facts", "read"]
