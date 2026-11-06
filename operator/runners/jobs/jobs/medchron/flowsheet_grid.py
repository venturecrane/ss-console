"""A hospital flowsheet's time grid, read by code: which time row a printed
value belongs to, and the span of times a value held. $0, no model call.

WHY (2026-10-29). A flowsheet prints its times as a row (``Row Name 1630 1615
1601 ...``) and each score row under it. The machine transcription sometimes
puts the score line first and the time line after it, so a reader can take
each time row as the FOOTER of the values above it when it is the HEADER of
the values below it. An update's drafter and its audit both read one page
that way; the independent review read it right, the audit reverted the
finisher's correct edit, and the job held until SMD corrected the sentence
by hand. The span was wrong at both ends.

The page decides it, with its neighbours in the same file. A time row heads
its values when each value row has as many cells as the time row above it;
a page whose rows run short at the end of a block (five times, then one)
fits only one reading. A page that fits both (every row five wide) takes the
reading of its neighbours; neighbours that disagree, or no page that
decides, leave it undecided and nothing changes.

Only pairs printed on the cited page are the page's table: a time row whose
values run onto the next page is not this page's to state.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

#: (exhibit, page) -> the text the lane read off that page, or None
Evidence = Callable[[int, int], Optional[str]]

HHMM = re.compile(r"[0-2]\d[0-5]\d")
#: a flowsheet value cell: a number and the charting clinician's initials ("16 -BT")
CELL = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*-[A-Z]{2,3}\b")
#: "<value> ... from <time> through <time>": one stated span of a value
SPAN = re.compile(
    r"(?<![\d.:/])(\d{1,3}(?:\.\d+)?)(?![\d.:/])(?:(?!\d).){0,40}?\bfrom\s+([0-2]\d[0-5]\d)\s+"
    r"(?:through|to|until)\s+([0-2]\d[0-5]\d)\b"
)
MAX_LABEL = 3

Row = tuple[str, list[str]]  # ("T", times) or ("V", values), in print order


def rows(text: str | None) -> list[Row]:
    """The page's time rows and value rows, in print order (other lines skipped)."""
    out: list[Row] = []
    for line in (text or "").splitlines():
        vals = CELL.findall(line)
        if vals:
            out.append(("V", vals))
            continue
        cells = line.split()
        n = 0
        while n < len(cells) and HHMM.fullmatch(cells[len(cells) - 1 - n]):
            n += 1
        label = cells[: len(cells) - n]
        if n and len(label) <= MAX_LABEL and not any(ch.isdigit() for w in label for ch in w):
            out.append(("T", cells[len(cells) - n :]))
    return out


def _pairs(seq: list[Row], above: bool) -> tuple[list[tuple[str, str]], int]:
    """(time, value) pairs on the page under one reading, and the value rows
    whose time row (on that side) has a different number of cells."""
    got: list[tuple[str, str]] = []
    bad = 0
    for i, (kind, cells) in enumerate(seq):
        if kind != "V":
            continue
        j = i - 1 if above else i + 1
        if not 0 <= j < len(seq) or seq[j][0] != "T":
            continue
        if len(seq[j][1]) == len(cells):
            got += list(zip(seq[j][1], cells))
        else:
            bad += 1
    return got, bad


def is_grid(seq: list[Row]) -> bool:
    return sum(1 for k, c in seq if k == "V" and len(c) > 1) >= 2 and any(k == "T" and len(c) > 1 for k, c in seq)


def vote(text: str | None) -> bool | None:
    """True (time rows head the values below), False (they follow the values
    above), or None when this page alone fits both readings or has no grid."""
    seq = rows(text)
    if not is_grid(seq):
        return None
    above, below = _pairs(seq, True)[1], _pairs(seq, False)[1]
    return None if above == below else above < below


def orientation(page: str | None, neighbours: list[str | None]) -> bool | None:
    """The cited page's reading: its own vote, else its neighbours' when they
    agree; None when nothing decides or two pages disagree."""
    if not is_grid(rows(page)):
        return None
    votes = {v for v in (vote(t) for t in [page, *neighbours]) if v is not None}
    return votes.pop() if len(votes) == 1 else None


def table(page: str | None, above: bool) -> list[tuple[str, str]]:
    """The page's (time, value) pairs, in print order, under the reading."""
    return _pairs(rows(page), above)[0]


def _num(v: str) -> str:
    return str(float(v)).rstrip("0").rstrip(".")


def _span(tab: list[tuple[str, str]], value: str) -> tuple[str, str] | None:
    """(earliest, latest) time the page shows `value`, when its entries are
    one unbroken run and the page's times run one way (no midnight inside)."""
    times = [int(t) for t, _ in tab]
    if times != sorted(times) and times != sorted(times, reverse=True):
        return None
    idx = [i for i, (_, v) in enumerate(tab) if _num(v) == _num(value)]
    if not idx or idx != list(range(idx[0], idx[-1] + 1)):
        return None
    run = sorted((tab[i][0] for i in idx), key=int)
    return run[0], run[-1]


def correct(claim: str, tab: list[tuple[str, str]]) -> str | None:
    """`claim` with each stated span set to the table's, or None when it
    already agrees, states no span of a grid time, or the table cannot say."""
    if not tab:
        return None
    on_page = {t for t, _ in tab}
    out, changed, at = [], False, 0
    for m in SPAN.finditer(claim):
        value, a, b = m.groups()
        if a not in on_page and b not in on_page:
            continue
        got = _span(tab, value)
        if got is None:
            return None
        lo, hi = got if int(a) <= int(b) else got[::-1]
        out += [claim[at : m.start(2)], lo, claim[m.end(2) : m.start(3)], hi]
        at = m.end(3)
        changed |= (lo, hi) != (a, b)
    return "".join(out) + claim[at:] if changed else None


def reading(evidence: Evidence, exhibit: int, page: int) -> bool | None:
    """The cited page's reading, decided by it and its neighbours in the same
    file: True (time rows head the values below), False, or None."""
    text = evidence(exhibit, page)
    return orientation(text, [evidence(exhibit, page - 1), evidence(exhibit, page + 1)])


def check(claim: str, evidence: Evidence, exhibit: int, page: int) -> str | None:
    """The claim corrected to the page's flowsheet table (cited page plus its
    neighbours in the same file decide the reading), or None."""
    above = reading(evidence, exhibit, page)
    if above is None:
        return None
    return correct(claim, table(evidence(exhibit, page), above))


def agrees(claim: str, evidence: Evidence, exhibit: int, page: int) -> bool:
    """True when the claim states a span on the page's flowsheet and every
    stated span is the table's (the grid confirms it)."""
    above = reading(evidence, exhibit, page)
    if above is None:
        return False
    tab = table(evidence(exhibit, page), above)
    on_page = {t for t, _ in tab}
    stated = [m for m in SPAN.finditer(claim) if m.group(2) in on_page or m.group(3) in on_page]
    return bool(stated) and correct(claim, tab) is None and all(_span(tab, m.group(1)) for m in stated)


__all__ = ["Evidence", "agrees", "check", "correct", "orientation", "reading", "rows", "table", "vote"]
