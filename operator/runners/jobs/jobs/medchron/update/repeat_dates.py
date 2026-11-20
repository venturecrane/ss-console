"""The dates an entry WRITES, for the update final set's repeat rule (moved
from ``final_set``, 2026-11-19, to keep that module under its size ceiling).
``final_set`` re-exports both names; it reads ``dates_present`` through its
own namespace, so the rule stays one named predicate."""

from __future__ import annotations

import re

from . import body as B

_MONTHS: tuple[str, ...] = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_NUM_DATE = re.compile(r"(?<![\d/.-])(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})(?!\d|[/.-]\d)")
_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_WORD_DATE = re.compile(r"(?i)\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})\b")
_RANGE = re.compile(r"\s*(?:-|–|to|through|thru|until)\s*", re.I)


def _dates_at(text: str) -> list[tuple[int, int, str]]:
    """(start, end, normalised YYYY-MM-DD) of every date written in `text`."""
    out = []
    for m in _NUM_DATE.finditer(text):
        mo, d, y = int(m.group(1)), int(m.group(2)), m.group(3)
        out.append((m.start(), m.end(), f"{2000 + int(y) if len(y) == 2 else int(y):04d}-{mo:02d}-{d:02d}"))
    for m in _ISO_DATE.finditer(text):
        out.append((m.start(), m.end(), m.group(0)))
    for m in _WORD_DATE.finditer(text):
        mo = _MONTHS.index(str(m.group(1)).lower()[:3]) + 1
        out.append((m.start(), m.end(), f"{int(m.group(3)):04d}-{mo:02d}-{int(m.group(2)):02d}"))
    return sorted(out)


def dates_written(e: str) -> tuple[set[str], set[tuple[str, str]]]:
    """(every date, every date range) written in an entry's BODY (its date
    line aside), citations aside."""
    body = B.CITE.sub(" ", "\n".join(e.split("\n")[2:]))
    at = _dates_at(body)
    ranges = {(a[2], b[2]) for a, b in zip(at, at[1:]) if _RANGE.fullmatch(body[a[1] : b[0]])}
    return {d for _s, _e, d in at}, ranges


def dates_present(e: str, keeper: str) -> bool:
    """THE SECOND CONDITION of a safe drop, in its one place: every date and
    every date range written in `e`'s body is written in `keeper`'s body too.
    2026-11-17, on a real update: of 9 covered pairs of two exports, 2 held an
    order date range the kept entry only implied through other dates; this
    keeps them whole. (A run-of-words or a pixel-identity test dropped none:
    two exports are different prints, worded differently.)"""
    mine, my_ranges = dates_written(e)
    have, have_ranges = dates_written(keeper)
    return mine <= have and my_ranges <= have_ranges
