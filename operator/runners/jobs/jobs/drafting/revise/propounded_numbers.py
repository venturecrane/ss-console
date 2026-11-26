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
#: A target the set is cut TO: "to 30", "down to 30", "reduce it to 30", never "up to 30".
_TO = re.compile(r"(?i)(?<!\bup\s)\b(?:down\s+)?to\s+(?:about\s+|around\s+|roughly\s+)?\d")
#: How many to remove: "by 5", "up to 5", "only 5", "remove 5", "drop a max of 5", "5 in total".
_BY = re.compile(
    r"(?i)\bby\s+\d|\bup\s+to\s+\d|\bonly\s+\d|\b\d+\s+in\s+total\b|"
    r"\b(?:remove|cut|drop|delete|eliminate|strike|lose|trim)\s+(?:a\s+max(?:imum)?\s+of\s+|up\s+to\s+|only\s+)?\d"
)
#: A cut may take at most half the set: more is the attorney's call to confirm, held for SMD.
MAX_CUT_SHARE = 0.5
#: How many requests an add writes when the email names no number.
DEFAULT_ADD = 10
_TOTAL = re.compile(r"(?i)\b(?:bring\w*|total|get\s+(?:it|them|the\s+set)\s+to|so\s+(?:it|there)\s+(?:has|are))\b")


def parse_number(text: str, count: int | None = None) -> int | None:
    """The one number ``text`` says; the top of a range. A digit run larger
    than ``count`` is the attorney's range with its dash lost ("120125") when
    exactly one split gives two ascending numbers, each no larger than the
    count, or, failing that, exactly one split into two ascending numbers of
    the same width (so "120125" reads as "120-125" whatever the set's size,
    as the dashed form does); otherwise None. None for no number, or several."""
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
    pairs = [(int(s[:i]), int(s[i:]), i) for i in range(1, len(s)) if s[0] != "0" and s[i] != "0"]
    ok = [b for a, b, _i in pairs if a < b <= count]
    if len(ok) == 1:
        return ok[0]
    even = [b for a, b, i in pairs if a < b and 2 * i == len(s)]
    return even[0] if not ok and len(even) == 1 else None


def cut_drop(number_quote: str, count: int) -> int | None:
    """How many requests a cut removes from a set of ``count``: TO a number
    ("down to 30", the top of "down to 120-125") or a number to remove ("by
    5", "remove 5", "up to 5", "only 5"). None when code cannot tell which
    (neither, both, a range or an "or" with no "to"), or the cut would take
    more than half the set: held, never guessed."""
    q = number_quote or ""
    target, removal = bool(_TO.search(q)), bool(_BY.search(q))
    if target == removal:
        return None
    if removal and _RANGE.search(q):
        return None  # "remove 5 or 6": which is the attorney's
    n = parse_number(q, count)
    if n is None:
        return None
    drop = max(count - n, 0) if target else n
    return drop if drop <= count * MAX_CUT_SHARE else None


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


__all__ = ["DEFAULT_ADD", "MAX_CUT_SHARE", "add_count", "crosses", "cut_drop", "parse_number"]
