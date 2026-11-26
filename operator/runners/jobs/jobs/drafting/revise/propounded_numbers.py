"""The numbers in an attorney's reply about a propounded set, read by code
(``propounded.py``): "down to 120-125", "cut by 10", "five more", and a run of
digits a phone or a typo joined ("120125"), split only when exactly one
ascending pair fits the set. Anything code cannot read is None: the job holds,
it never guesses.
"""

from __future__ import annotations

import re

from ..counts import STATUTORY_LIMIT


_WORDS = {
    w: i
    for i, w in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
        "seventeen eighteen nineteen twenty".split()
    )
}
_RANGE = re.compile(r"(?<![\d.])(\d{1,4})\s*(?:-|\u2013|\u2014|to|through|or)\s*(\d{1,4})(?![\d.])", re.I)
_RUN = re.compile(r"(?<![\d.])\d+(?![\d.]\d)")
_WORD_NUM = re.compile(r"(?i)\b(" + "|".join(_WORDS) + r")\b")
_TO = re.compile(r"(?i)\b(?:to|down\s+to|at\s+most|no\s+more\s+than|max(?:imum)?|under|total|only)\b")
_BY = re.compile(r"(?i)\b(?:by|remove|cut|drop|delete|eliminate|strike|lose|trim)\b")
_TOTAL = re.compile(r"(?i)\b(?:bring\w*|total|get\s+(?:it|them|the\s+set)\s+to|so\s+(?:it|there)\s+(?:has|are))\b")


def parse_number(text: str, count: int | None = None) -> int | None:
    """The one number ``text`` says; the top of a range. A digit run larger
    than ``count`` is the attorney's range with its dash lost ("120125") when
    exactly one split gives two ascending numbers, each no larger than the
    count; otherwise None. None for no number, or several."""
    ranges = _RANGE.findall(text or "")
    if ranges:
        if len(ranges) != 1:
            return None
        a, b = int(ranges[0][0]), int(ranges[0][1])
        return b if a <= b else None
    runs = _RUN.findall(text or "")
    if not runs:
        words = _WORD_NUM.findall(text or "")
        return _WORDS[words[0].lower()] if len(words) == 1 else None
    if len(runs) != 1:
        return None
    s = runs[0]
    n = int(s)
    if count is None or n <= count:
        return n
    pairs = [(int(s[:i]), int(s[i:])) for i in range(1, len(s)) if s[0] != "0" and s[i] != "0"]
    ok = [b for a, b in pairs if a < b <= count]
    return ok[0] if len(ok) == 1 else None


def cut_drop(number_quote: str, count: int) -> int | None:
    """How many requests a cut removes from a set of ``count``: to a number
    (the top of a range) or by one. None when code cannot tell which, or the
    cut would leave nothing."""
    n = parse_number(number_quote, count)
    if n is None:
        return None
    if _RANGE.search(number_quote) or _TO.search(number_quote):
        drop = max(count - n, 0)
    elif _BY.search(number_quote):
        drop = n
    else:
        return None
    return drop if drop < count else None


def add_count(number_quote: str, count: int) -> int | None:
    """How many requests an add writes: the email's number ("five more"), the
    difference to a total it names ("bring it to 40"), else ``DEFAULT_ADD``.
    None when it names a number code cannot read."""
    if not number_quote.strip():
        return DEFAULT_ADD
    n = parse_number(number_quote)
    if n is None:
        return None
    if _TOTAL.search(number_quote):
        return max(n - count, 0)
    return n


def crosses(old: int, new: int) -> bool:
    return (old > STATUTORY_LIMIT) != (new > STATUTORY_LIMIT)


__all__ = ["add_count", "crosses", "cut_drop", "parse_number"]
