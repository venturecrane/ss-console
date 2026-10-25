"""Code checks every lane can run on what it is about to deliver.

Each returns facts, never grades: the lane decides what a hit means for its
output (a dollar figure absent from the record is MUST-FIX in a negotiation row
that claims the document offered it, and only SHOULD-FIX in a demand, whose
totals are sums the record never states). The 2026-10-13 critique of this plan
is why: a check that grades its own hits is the gate-false-positive class.

Normalization is deliberately generous on the SOURCE side (every way a figure
or a date can be written there is accepted) and exact on the OUTPUT side, so a
hit means "this value is nowhere in the record in any spelling", not "it was
spelled differently".
"""

from __future__ import annotations

import re
from datetime import date
from typing import Iterable

#: The system's own words, which never belong in what a firm reads. Each one
#: reached a firm at least once: "this chunk" in delivered chronologies
#: (eng#226), "Provider not identified in this chunk" as an exhibit title
#: (ss#2819), job/auditor vocabulary in attorney notes (2026-10-12).
#: Three phrases are fenced against the firm's own English, because a hit is a
#: MUST-FIX hold: "by Code of Civil Procedure" (statute citations), "the model
#: year" / "the make and model" (vehicle cases), "the job's duties" (a lost-wage
#: claim). Without the fences a correct declaration would hold on its citation.
_SYSTEM = re.compile(
    r"(?i)\b(this chunk|in this chunk|the pipeline|the auditor|the drafter|drafting gate|compose stage|"
    r"under the job|the job's(?=\s+(?:stage|run|rule|budget|ledger|verdict|folder|cap)\b)|job rule|"
    r"counted by code|by code(?!\s+(?:of|section|sections|§))|"
    r"the model(?!\s+(?:year|number|name|of)\b)(?!'s\s+(?:year|number|name)\b)|as an ai)\b"
)
#: How the system READ a page, which no record says of itself: the
#: chronology's own text-extraction narrated in the delivered entries (the
#: Pamplona review, 2026-10-24: "The image carries no transcribed text.", "In
#: the transcription, the boxes for ...", "Text on the following page ...
#: reads as follows"). Kept apart from ``_SYSTEM`` because the demand and
#: litigation lanes share that list, and a demand may properly speak of a
#: deposition's transcription. The citation tail ", machine transcription"
#: never matches: every phrase here needs its own article or noun.
_EXTRACTION = re.compile(
    r"(?i)\b(in the transcription|the transcription|transcribed text|text on the following page|the image carries no)\b"
)
_SENTINEL = re.compile(r"(?m)^\W*===\s*[A-Z][A-Z ]+[^=\n]*===\s*$")
_MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?")
#: A bare figure ends at a digit boundary that is not a thousands group: the
#: lookahead refuses ",\d" (still inside the number), never a plain comma, or
#: "12,500, plus costs" in a source read as 500 and left the real figure unknown.
_BARE_MONEY = re.compile(r"(?<![\d.])(?<!\d,)(\d{1,3}(?:,\d{3})+|\d{3,})(?:\.(\d{2}))?(?!\d|,\d)")
_MONTHS = {
    m: i
    for i, ms in enumerate(
        (
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ),
        start=1,
    )
    for m in ms
}
_D_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_D_US = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})\b")
_D_LONG = re.compile(r"(?i)\b([A-Z][a-z]{2,8})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b")


def system_vocabulary(text: str) -> list[str]:
    """Distinct system-vocabulary hits, in order of first appearance."""
    seen: list[str] = []
    for m in _SYSTEM.finditer(text or ""):
        w = m.group(0).lower()
        if w not in seen:
            seen.append(w)
    return seen


def extraction_vocabulary(text: str) -> list[str]:
    """Distinct hits of the system narrating how it read a page, in order of
    first appearance (``_EXTRACTION``)."""
    seen: list[str] = []
    for m in _EXTRACTION.finditer(text or ""):
        w = m.group(0).lower()
        if w not in seen:
            seen.append(w)
    return seen


def sentinels(text: str) -> list[str]:
    return [m.group(0).strip() for m in _SENTINEL.finditer(text or "")]


def _cents(whole: str, frac: str | None) -> int:
    return int(whole.replace(",", "")) * 100 + int(frac or 0)


def money(text: str) -> list[int]:
    """Every ``$`` figure in the text, in cents, in order."""
    return [_cents(m.group(1), m.group(2)) for m in _MONEY.finditer(text or "")]


def money_in_sources(sources: str) -> set[int]:
    """Every figure the sources state, with or without a ``$``, in cents."""
    out = {_cents(m.group(1), m.group(2)) for m in _MONEY.finditer(sources or "")}
    out |= {_cents(m.group(1), m.group(2)) for m in _BARE_MONEY.finditer(sources or "")}
    return out


def money_not_in(text: str, sources: str, *, also: Iterable[int] = ()) -> list[int]:
    """``$`` figures in the text that the sources state nowhere (``also``: the
    figures a lane derived in code, e.g. a ledger total)."""
    known = money_in_sources(sources) | set(also)
    out: list[int] = []
    for c in money(text):
        if c not in known and c not in out:
            out.append(c)
    return out


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def dates(text: str) -> list[date]:
    """Every calendar date written in the text (ISO, M/D/Y, Month D, Y)."""
    out: list[date] = []
    t = text or ""
    for m in _D_ISO.finditer(t):
        d = _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            out.append(d)
    for m in _D_US.finditer(t):
        y = int(m.group(3))
        y = y + 2000 if y < 100 else y
        d = _mk(y, int(m.group(1)), int(m.group(2)))
        if d:
            out.append(d)
    for m in _D_LONG.finditer(t):
        mon = _MONTHS.get(m.group(1).lower())
        if mon:
            d = _mk(int(m.group(3)), mon, int(m.group(2)))
            if d:
                out.append(d)
    return out


def dates_not_in(text: str, sources: str) -> list[date]:
    known = set(dates(sources))
    out: list[date] = []
    for d in dates(text):
        if d not in known and d not in out:
            out.append(d)
    return out


def future(ds: Iterable[date], today: date) -> list[date]:
    return sorted({d for d in ds if d > today})


def fmt_cents(c: int) -> str:
    return f"${c // 100:,}.{c % 100:02d}"


__all__ = [
    "dates",
    "dates_not_in",
    "extraction_vocabulary",
    "fmt_cents",
    "future",
    "money",
    "money_in_sources",
    "money_not_in",
    "sentinels",
    "system_vocabulary",
]
