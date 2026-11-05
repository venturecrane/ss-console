"""A date exactly as a document prints it, read into a ``date`` in code.

WHY (the model-as-logic audit, 2026-11-04). The tools asked the model for ISO
dates, so the model converted "10/01/2026" itself, and a transcription's
"1O/0l/2026" was either left out or "corrected" from a guess. Reading a
printed date is code's job: ``printed_facts.printed_date_candidates`` already
repairs the characters a transcription puts where digits were printed, and
this module turns its answer into ONE date or none.

ONE READING OR NONE. A printed date that can be more than one real date after
repair is refused (None), and the tool that asked names the string back, so a
person reads the paper. An ISO date passes through unchanged, for code callers
and for a page that prints one. A date written out ("October 1, 2026",
"Oct. 1, 2026", "1 October 2026") has exactly one reading and is accepted.

A two-digit year reads as 20YY, the way ``printed_date_candidates`` reads it.
That is right for a date of service or a date of loss and wrong for a date of
birth, so a caller reading a birth date passes ``two_digit_year=False`` and a
two-digit birth year is refused.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .printed_facts import printed_date_candidates

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTHS = {
    name: number
    for number, names in enumerate(
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
    for name in names
}
_MONTH_FIRST = re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})$")
_DAY_FIRST = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})$")
_TWO_DIGIT_YEAR = re.compile(r"[/.\-]\s*\S{2}$")


def _real(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _written_out(text: str) -> date | None:
    month_first = _MONTH_FIRST.match(text)
    if month_first:
        month = _MONTHS.get(month_first.group(1).lower())
        return _real(int(month_first.group(3)), month, int(month_first.group(2))) if month else None
    day_first = _DAY_FIRST.match(text)
    if day_first:
        month = _MONTHS.get(day_first.group(2).lower())
        return _real(int(day_first.group(3)), month, int(day_first.group(1))) if month else None
    return None


def date_from_printed(raw: Any, *, two_digit_year: bool = True) -> date | None:
    """The one date ``raw`` prints, or None (absent, not a date, or more than
    one possible date). See the module docstring for the shapes."""
    if not isinstance(raw, str):
        return None
    text = " ".join(raw.split())
    if not text:
        return None
    if _ISO.match(text):
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    written = _written_out(text)
    if written is not None:
        return written
    if not two_digit_year and _TWO_DIGIT_YEAR.search(text):
        return None
    found = printed_date_candidates(text)
    return date.fromisoformat(found[0]) if len(found) == 1 else None


__all__ = ["date_from_printed"]
