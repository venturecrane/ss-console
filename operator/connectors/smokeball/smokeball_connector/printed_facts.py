"""Facts as a letter prints them, made comparable with the firm's record: a date
of loss the page's transcription garbled, and a court case number written in
the court's form rather than the firm's. Pure functions, no client, no package
imports, so the matter resolver can use them without a cycle.

THE CASES THEY CLOSE (a client seat's post, 2026-10-30). A carrier letter
printed its date of loss as ``99/99/9999``; the seat's transcription read it as
``99/9l/999C`` (a lowercase l and a capital C where digits are), the Operator
could not turn that into an ISO date, and left the fact out, so a letter for a
client with two matters, whose date would have told them apart, came back as a
question. A court notice printed its case number as ``CV-9999-9999999``; the
firm recorded the same case as ``XXX-CV-XXX-9999-99999`` (a county code, an
inner code, and the sequence without its leading zeros), so an exact comparison
called them different cases.
"""

from __future__ import annotations

import itertools
import re
from datetime import date

#: Characters a page transcription puts where a digit was printed, and the
#: digits each can stand for. Closed on purpose: a character not here is not a
#: repair, and the date is reported unused rather than guessed.
_CONFUSABLE: dict[str, tuple[str, ...]] = {
    **dict.fromkeys("lIi|!L", ("1",)),
    **dict.fromkeys("OoQD", ("0",)),
    **dict.fromkeys("Cc", ("0", "6")),
    **dict.fromkeys("Ss", ("5",)),
    **dict.fromkeys("Zz", ("2",)),
    "B": ("8",),
    "G": ("6",),
    "b": ("6",),
    "g": ("9",),
    "q": ("9",),
}

#: At most this many characters of one date may be repaired, and a repair may
#: leave at most this many candidate dates. Past either, the page is too
#: garbled for its date to corroborate anything.
MAX_REPAIRED_CHARS = 2
MAX_DATE_CANDIDATES = 4

_PRINTED_DATE = re.compile(r"^(\S{1,2})[/.\-](\S{1,2})[/.\-](\S{4}|\S{2})$")


def _digit_options(part: str) -> list[tuple[str, ...]] | None:
    options: list[tuple[str, ...]] = []
    for ch in part:
        if ch.isdigit():
            options.append((ch,))
        elif ch in _CONFUSABLE:
            options.append(_CONFUSABLE[ch])
        else:
            return None
    return options


def printed_date_candidates(text: str) -> list[str]:
    """The ISO dates a date printed as month/day/year can be, after repairing
    the characters a transcription mistakes for digits. ``[]`` when it does not
    read as one date: an unknown character, more than ``MAX_REPAIRED_CHARS``
    repairs, more than ``MAX_DATE_CANDIDATES`` results, or no real calendar
    date among them. A clean printed date gives exactly one candidate.

    A two-digit year is read as 20YY: a date of loss on a letter in this
    firm's post is this century."""
    match = _PRINTED_DATE.match(re.sub(r"\s+", "", text or ""))
    if not match:
        return []
    parts = [_digit_options(p) for p in match.groups()]
    if any(p is None for p in parts):
        return []
    if sum(1 for ch in "".join(match.groups()) if not ch.isdigit()) > MAX_REPAIRED_CHARS:
        return []
    month_opts, day_opts, year_opts = (p for p in parts if p is not None)
    found: set[str] = set()
    for m, d, y in itertools.product(
        ("".join(c) for c in itertools.product(*month_opts)),
        ("".join(c) for c in itertools.product(*day_opts)),
        ("".join(c) for c in itertools.product(*year_opts)),
    ):
        year = int(y) + (2000 if len(y) == 2 else 0)
        try:
            found.add(date(year, int(m), int(d)).isoformat())
        except ValueError:
            continue
    return sorted(found) if 0 < len(found) <= MAX_DATE_CANDIDATES else []


def case_number_key(text: str) -> tuple[int, ...]:
    """A court case number as compared across the court's form and the firm's:
    its digit groups, in order, each without leading zeros. Letters (county,
    case-type and department codes) are dropped, because the firm and the
    court write them differently for one case, while the year and the sequence
    are what identify it. ``()`` when it carries no digits."""
    return tuple(int(group) for group in re.findall(r"\d+", text or ""))
