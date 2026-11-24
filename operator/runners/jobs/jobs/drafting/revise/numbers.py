"""Where text names a response by its number, read by code: "No. 9", "Nos. 4,
7-9 and 15", "#3", "Response 12.1", "RFP 6", "On 11, ...".

Used twice by the revision of a request's filed responses: an email
instruction's numbers must be the numbers its own quote names
(``instructions.py``), and every number the email names must sit inside a
quote code kept, so nothing the attorney asked about is dropped on the floor.

A number is only ever read after a word that says it is a response number;
a bare figure ("$2,500", "2026", "5 visits") is never one.
"""

from __future__ import annotations

import re

#: The words that say a number after them is a response number.
_LEAD = (
    r"(?:\bnos?\b\.?|\bnumbers?\b|#|\bresponses?\b(?:\s+(?:to|for))?|\brequests?\b(?:\s+for\s+\w+)?"
    r"|\binterrogator(?:y|ies)\b|\brogs?\b|\bsrogs?\b|\bfrogs?\b|\brfps?\b|\brfas?\b|\bitems?\b)"
)
_NUM = r"\d{1,3}(?:\.\d{1,2})?"
_JOIN = r"(?:\s*(?:,|;|&|\band\b|\bor\b|-|–|—|\bthrough\b|\bthru\b|\bto\b)\s*(?:(?:nos?\.?|#)\s*)?)"
_LIST = rf"{_NUM}(?:{_JOIN}{_NUM})*"
#: A lead word, optionally another ("Request No."), then a list of numbers.
MENTION = re.compile(rf"(?i){_LEAD}(?:\s*{_LEAD})*\s*({_LIST})")
#: "On 11, add ..." / "For 9: ..." at the start of a sentence or line.
_ON = re.compile(rf"(?im)(?:^|(?<=[.!?:;\n])\s*)(?:on|for|in|re:?)\s+({_LIST})\s*[,:]")
_RANGE = re.compile(rf"({_NUM})\s*(?:-|–|—|\bthrough\b|\bthru\b|\bto\b)\s*({_NUM})", re.I)
#: The longest range code expands; a longer one is refused as a guess.
MAX_RANGE = 60


def _expand(listing: str) -> tuple[str, ...]:
    """A listing's numbers in order, ranges expanded ("7-9" is 7, 8, 9;
    "12.1-12.3" is 12.1, 12.2, 12.3). A range code cannot expand is kept as
    its two ends."""
    out: list[str] = []
    rest = listing
    for m in _RANGE.finditer(listing):
        a, b = m.group(1), m.group(2)
        out += _numbers_of(rest[: m.start() - (len(listing) - len(rest))])
        rest = listing[m.end() :]
        out += _span(a, b)
    out += _numbers_of(rest)
    return tuple(dict.fromkeys(out))


def _numbers_of(text: str) -> list[str]:
    return re.findall(_NUM, text)


def _span(a: str, b: str) -> list[str]:
    if "." in a and "." in b and a.split(".")[0] == b.split(".")[0]:
        head, lo, hi = a.split(".")[0], int(a.split(".")[1]), int(b.split(".")[1])
        if lo <= hi and hi - lo <= MAX_RANGE:
            return [f"{head}.{k}" for k in range(lo, hi + 1)]
    if a.isdigit() and b.isdigit() and int(a) <= int(b) and int(b) - int(a) <= MAX_RANGE:
        return [str(k) for k in range(int(a), int(b) + 1)]
    return [a, b]


def mentions(text: str) -> list[tuple[int, int, tuple[str, ...]]]:
    """Every place ``text`` names response numbers: (start, end, the numbers)."""
    found = [(m.start(), m.end(), _expand(m.group(1))) for m in MENTION.finditer(text or "")]
    found += [(m.start(1), m.end(1), _expand(m.group(1))) for m in _ON.finditer(text or "")]
    found.sort()
    out: list[tuple[int, int, tuple[str, ...]]] = []
    for s, e, nums in found:  # an "On 11," inside a lead-word mention is the same mention
        if out and s < out[-1][1]:
            continue
        out.append((s, e, nums))
    return out


def numbers_in(text: str) -> tuple[str, ...]:
    """Every response number ``text`` names, in order, once each."""
    return tuple(dict.fromkeys(n for _s, _e, nums in mentions(text) for n in nums))


__all__ = ["MAX_RANGE", "mentions", "numbers_in"]
