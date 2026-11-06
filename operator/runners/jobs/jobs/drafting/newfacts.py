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
  whitespace loosened; in the job's record, every four-word run of it). A quoted defined-term label (``sets._TERM``: "INCIDENT")
  is a label, not a quotation; its words are names like any other.

Text inside a ``{{...}}`` marker states nothing: a repair that removes a fact
and leaves a marker for the attorney adds no fact.
"""

from __future__ import annotations

import hashlib
import json
import re
import zlib
from array import array
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable

from . import howell, sets
from .evidence import verify

#: A marker never holds a brace (``outcome.MARKERS``): an unclosed ``{{`` in
#: a scan's text then costs one scan to the next brace, never one to the end.
_MARKER = re.compile(r"\{\{[^{}]*\}\}")
_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
    r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
_LONG_DATE = re.compile(rf"\b({_MONTH})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}})\b)?")
_NUM_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[/-]\d{1,2}[/-](?:\d{4}|\d{2})\b")
#: A two-decimal figure starts only where a run of digits and commas starts:
#: with ``\b`` it could start after every comma, and on a scanned ledger read
#: across (tens of thousands of characters of comma-separated cells) each
#: start scanned the rest of the run. 6.1 s for one 85,000-character ledger;
#: the District job's cold index took 359 s (rehearsal 2, 2026-11-06).
_MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d{1,2})?|(?<![\w,.])\d[\d,]*\.\d{2}\b")
_NUMBER = re.compile(r"(?<![\w$])\d[\d,]*(?:\.\d+)?(?!\w)")
_LABEL_NO = re.compile(r"(?i)\bnos?\.?\s*\d+(?:\s*(?:-|through|to|and|,)\s*\d+)*")
_QUOTE = re.compile(r"\"([^\"\n]{2,}?)\"|“([^”\n]{2,}?)”")
_WORD = re.compile(r"[A-Za-z][A-Za-z'’]*")
#: One word of a line's lead-in: list and table markup, "3." / "3)", "(a)" / "a)".
#: Each piece is read one way only: the earlier pattern let "| --- | --- |..."
#: be split many ways, and a caption table row before a name took seconds per
#: extra column (exponential backtracking, found 2026-11-06).
_LEAD_WORD = re.compile(r"(?:[-*+>#|]|\d+[.)]|\([a-z0-9]{1,3}\)|[a-z][a-z0-9]{0,2}\))+")
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
    # Only the word's own line is read: the text before it is never copied.
    line = text[text.rfind("\n", 0, start) + 1 : start].rstrip(" \t\"“'‘([*_")
    if not line or line.endswith((".", "!", "?", ":", ";")):
        return True
    return all(_LEAD_WORD.fullmatch(w) for w in line.split())


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
    out, at = [], 0
    for a, b in sorted(spans):
        if a < at:
            a = at
        if a < b:
            out += [text[at:a], " " * (b - a)]
            at = b
    out.append(text[at:])
    return "".join(out)


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


# ---- what is known: an index built once per job, then set lookups -------------------------
#
# WHY (2026-11-05, the rehearsal on the District job). The first version read
# the whole record text as one string, through the same reader a repair goes
# through: its blanking and its sentence-start test copy the text once per
# date and per capitalized word, and a 524-file, 11 MB record ran past two
# minutes and out of memory on a 3.9 GB box. A source is now read once per
# job, a file at a time, into sets of normalized tokens (and its quotations
# as hashed four-word shingles); a repair's check is then a lookup of the few
# facts the repair states. The index is cached in the job's data directory,
# keyed by the sha256 of what it was read from.

#: Words per quotation shingle: a quotation of this many words or more is
#: known when every run of this many consecutive words is in a source.
SHINGLE = 4
_TOK = re.compile(r"[a-z0-9]+")
_GROUPED = re.compile(r"\b\d{1,3}(?:,\d{3})+\b")
CACHE = "known-index-{key}"


def _h(gram: str) -> int:
    b = gram.encode("utf-8")
    return zlib.crc32(b) | (zlib.crc32(b, 0x9E3779B9) << 32)


class Known:
    """What a source holds, by kind: normalized tokens, never its text (a
    small text, a draft being repaired, also keeps its flattened text)."""

    def __init__(
        self,
        dates: set[date],
        month_days: set[tuple[int, int]],
        money: set[Decimal],
        numbers: set[str],
        words: set[str],
        tokens: set[str],
        shingles: array,
        flat: str | None = None,
    ) -> None:
        self.dates, self.month_days, self.money, self.numbers = dates, month_days, money, numbers
        self.words, self.tokens, self.shingles, self.flat = words, tokens, shingles, flat

    def has_date(self, d: date) -> bool:
        return d in self.dates

    def has_month_day(self, md: tuple[int, int]) -> bool:
        return md in self.month_days

    def has_money(self, v: Decimal) -> bool:
        return v in self.money

    def has_number(self, n: str) -> bool:
        return n in self.numbers

    def has_word(self, w: str) -> bool:
        return w in self.words

    def has_quote(self, q: str) -> bool:
        """``q`` (flattened) is in the source: as written in a small text;
        in a large one, every four-word run of it (a shorter one, every word)."""
        if self.flat is not None:
            return q in self.flat
        toks = _TOK.findall(q)
        if len(toks) < SHINGLE:
            return bool(toks) and all(t in self.tokens for t in toks)
        s = self.shingles
        for i in range(len(toks) - SHINGLE + 1):
            g = _h(" ".join(toks[i : i + SHINGLE]))
            j = bisect_left(s, g)
            if j == len(s) or s[j] != g:
                return False
        return True

    def plus(self, text: str) -> "Both":
        return Both(self, known(text))

    # ---- the cache -------------------------------------------------------------------
    def save(self, path: Path) -> None:
        doc = {
            "dates": sorted(d.isoformat() for d in self.dates),
            "month_days": sorted(list(md) for md in self.month_days),
            "money": sorted(str(v) for v in self.money),
            "numbers": sorted(self.numbers),
            "words": sorted(self.words),
            "tokens": sorted(self.tokens),
        }
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        tmp.replace(path.with_suffix(".json"))
        tmp.write_bytes(self.shingles.tobytes())
        tmp.replace(path.with_suffix(".bin"))

    @classmethod
    def load(cls, path: Path) -> "Known | None":
        js, bn = path.with_suffix(".json"), path.with_suffix(".bin")
        if not (js.is_file() and bn.is_file()):
            return None
        doc = json.loads(js.read_text(encoding="utf-8"))
        sh = array("Q")
        sh.frombytes(bn.read_bytes())
        return cls(
            {date.fromisoformat(x) for x in doc["dates"]},
            {(int(a), int(b)) for a, b in doc["month_days"]},
            {Decimal(x) for x in doc["money"]},
            set(doc["numbers"]),
            set(doc["words"]),
            set(doc["tokens"]),
            sh,
        )


@lru_cache(maxsize=4096)
def _piece(text: str) -> Known:
    b = Builder()
    b.feed(text)
    return b.done(flat=None)


class Doc:
    """A draft read as a source a request (or section) at a time, and only
    when a fact is not in the record: a piece unchanged since the last round
    is never read again (``_piece``). Quotations are matched in the whole
    draft as written."""

    def __init__(self, pieces: list[str], text: str) -> None:
        self._src, self._text = pieces, text
        self._known: list[Known] | None = None
        self._flat: str | None = None

    def _all(self) -> list[Known]:
        if self._known is None:
            self._known = [_piece(p) for p in self._src if p.strip()]
        return self._known

    def has_date(self, d: date) -> bool:
        return any(k.has_date(d) for k in self._all())

    def has_month_day(self, md: tuple[int, int]) -> bool:
        return any(k.has_month_day(md) for k in self._all())

    def has_money(self, v: Decimal) -> bool:
        return any(k.has_money(v) for k in self._all())

    def has_number(self, n: str) -> bool:
        return any(k.has_number(n) for k in self._all())

    def has_word(self, w: str) -> bool:
        return any(k.has_word(w) for k in self._all())

    def has_quote(self, q: str) -> bool:
        if self._flat is None:
            self._flat = _flat(_strip(self._text))
        return q in self._flat


class Both:
    """Two ``Known`` read as one, without copying either."""

    def __init__(self, a: Known | Both, b: Known | Doc) -> None:
        self.a, self.b = a, b

    def __getattr__(self, name: str):  # the has_* lookups
        fa, fb = getattr(self.a, name), getattr(self.b, name)
        return lambda x: fa(x) or fb(x)

    def plus(self, text: str) -> "Both":
        return Both(self, known(text))


class Builder:
    """A source read a piece at a time; only normalized tokens are kept."""

    def __init__(self) -> None:
        self.dates: set[date] = set()
        self.month_days: set[tuple[int, int]] = set()
        self.money: set[Decimal] = set()
        self.numbers: set[str] = set()
        self.words: set[str] = set()
        self.tokens: set[str] = set()
        self.shingles = array("Q")

    #: A piece longer than this is read in slices cut at a line break: the live
    #: job passes its whole record text as one string, and every list a read
    #: makes (tokens, matches) is of one slice, never of the whole record.
    SLICE = 256 * 1024

    def feed(self, text: str) -> None:
        carry: list[str] = []  # a slice's last words, so a shingle spans the cut
        at = 0
        while at < len(text):
            end = len(text) if len(text) - at <= self.SLICE else text.find("\n", at + self.SLICE)
            end = len(text) if end < 0 else end + 1
            carry = self._piece(text[at:end], carry)
            at = end

    def _piece(self, text: str, carry: list[str]) -> list[str]:
        t = _strip(text)
        f = Found()
        _dates(t, f)
        self.dates |= {d for d, _ in f.dates}
        self.month_days |= {md for md, _ in f.month_days} | {(d.month, d.day) for d, _ in f.dates}
        # Every figure a source prints is a known number, including a date's or
        # an amount's own parts ("2024", "1500"): the comparison is with what is new.
        self.numbers |= {_digits(m.group(0)) for m in _NUMBER.finditer(t)}
        for rx in (_MONEY, _GROUPED):
            for m in rx.finditer(t):
                v = howell.money(m.group(0))
                if v is not None:
                    self.money.add(v)
        self.words |= {_word(m.group(0)) for m in _WORD.finditer(t)}
        toks = carry + _TOK.findall(_flat(t))
        self.tokens.update(toks)
        self.shingles.extend(_h(" ".join(toks[i : i + SHINGLE])) for i in range(len(toks) - SHINGLE + 1))
        return toks[-(SHINGLE - 1) :]

    def done(self, flat: str | None = None) -> Known:
        # Sorted then de-duplicated in one pass: a set of every shingle would
        # cost several times the array (measured, tests/bench_repair_guard.py).
        ordered, self.shingles = sorted(self.shingles), array("Q")
        sh, last = array("Q"), -1
        for g in ordered:
            if g != last:
                sh.append(g)
                last = g
        del ordered
        return Known(self.dates, self.month_days, self.money, self.numbers, self.words, self.tokens, sh, flat)


@lru_cache(maxsize=8)
def known(text: str) -> Known:
    """A small text (a draft, a test's source) read as a source; it keeps its
    flattened text, so a quotation of it is matched as written."""
    b = Builder()
    b.feed(text)
    return b.done(flat=_flat(_strip(text)))


#: The job index in this process, by key: a repair round reads it, never builds it.
_MEMO: dict[str, Known] = {}


def index(chunks: Callable[[], Iterable[str]], cache_dir: Path) -> Known:
    """The job's sources, read once: ``chunks()`` yields them a piece at a
    time (it is called twice: once for the key, once to build on a miss)."""
    h = hashlib.sha256()
    for c in chunks():
        h.update(c.encode("utf-8", "replace"))
        h.update(b"\0")
    key = h.hexdigest()[:24]
    if key in _MEMO:
        return _MEMO[key]
    path = cache_dir / CACHE.format(key=key)
    got = Known.load(path)
    if got is None:
        b = Builder()
        for c in chunks():
            b.feed(c)
        got = b.done()
        got.save(path)
    _MEMO.clear()  # one job's index at a time
    _MEMO[key] = got
    return got


def new_facts(text: str, base: Known | Both) -> list[tuple[str, str]]:
    """(kind, as printed) for every fact ``text`` states that ``base`` does not hold."""
    f = read(text)
    out: list[tuple[str, str]] = []
    out += [("date", raw) for d, raw in f.dates if not base.has_date(d)]
    out += [("date", raw) for md, raw in f.month_days if not base.has_month_day(md)]
    out += [("dollar figure", raw) for v, raw in f.money if not base.has_money(v)]
    out += [("number", raw) for n, raw in f.numbers if not base.has_number(n)]
    out += [("name", raw) for w, raw in f.names if not base.has_word(w)]
    out += [("quoted string", raw) for q, raw in f.quotes if not base.has_quote(q)]
    return list(dict.fromkeys(out))


__all__ = ["Both", "Builder", "Doc", "Found", "Known", "index", "known", "new_facts", "read"]
