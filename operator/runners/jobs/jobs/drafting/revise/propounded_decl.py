"""A revised set's declaration for additional discovery, rewritten by code
(``propounded.py`` holds the ops).

A firm's declaration states the set's count more than once (in prose, beside
the statute) and lists, under each statutory reason, the items that fall under
it: "(A) Nos. 1-14, 16, 17", "(E) Nos. 29-38, 43, 52", or one list of the
whole set. Every count statement is rewritten to the new count, and every list
is carried through the revision's numbering: each number and range expanded,
each kept item mapped to its new number, a removed item dropped, a follow-up
added to every list its parent is in, then written again as ranges in the
list's own separators. A list naming an item the set does not have before the
revision, a list the revision empties, a count also written in words, a count
that crosses 35, or a declaration that states nothing code reads, holds.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from .. import set_items
from ..counts import STATUTORY_LIMIT
from .propounded import LIMITED, Held, decl_block
from .propounded_numbers import crosses

_ITEM = r"\d+(?:[ \t]*(?:-|–|—|through|thru|to)[ \t]*\d+)?"
_SEP = r"(?:[ \t]*,[ \t]*(?:and[ \t]+)?|[ \t]+and[ \t]+)"
#: A list of item numbers after "No."/"Nos."/"Numbers" or the set's own word.
_LIST = re.compile(
    r"(?i)\b(?:Nos?\.?|Numbers?|(?:special\s+)?interrogator(?:y|ies)|requests?(?:\s+for\s+admission)?)[ \t]*"
    rf"({_ITEM}(?:{_SEP}{_ITEM})*)(?![\d.]\d)"
)
_RANGE_SEP = re.compile(r"(?i)[ \t]*(?:-|–|—|through|thru|to)[ \t]*")
_SPELLED = re.compile(
    r"(?i)\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
    r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)\b"
)


def _expand(body: str) -> list[int]:
    out: list[int] = []
    for part in re.split(_SEP, body):
        ends = [int(n) for n in re.findall(r"\d+", part)]
        out += list(range(ends[0], ends[-1] + 1)) if len(ends) == 2 else ends
    return out


def _compress(nums: list[int], body: str) -> str:
    """``nums`` as ranges (three or more in a row) in ``body``'s separators."""
    dash = (_RANGE_SEP.search(body).group(0) if _RANGE_SEP.search(body) else "-").strip()  # type: ignore[union-attr]
    dash = dash if dash in "-–—" else f" {dash} "
    runs: list[list[int]] = []
    for n in nums:
        if runs and n == runs[-1][-1] + 1:
            runs[-1].append(n)
        else:
            runs.append([n])
    parts: list[str] = []
    for r in runs:
        parts += [f"{r[0]}{dash}{r[-1]}"] if len(r) >= 3 else [str(n) for n in r]
    tail = re.search(r"(?i)(,?[ \t]*and[ \t]+)" + _ITEM + r"$", body)
    last = tail.group(1) if tail and len(parts) > 1 else ", "
    return ", ".join(parts[:-1]) + last + parts[-1] if len(parts) > 1 else parts[0]


def _carry(body: str, numbers: Mapping[int, int], kids: Mapping[int, list[int]], have: set[int]) -> str:
    nums = _expand(body)
    missing = [n for n in nums if n not in have]
    if missing:
        raise Held(
            "revise_declaration",
            f"the declaration lists No. {missing[0]}, which the set does not have; nothing was filed",
        )
    new = sorted({numbers[n] for n in nums if n in numbers} | {k for n in nums for k in kids.get(n, [])})
    if not new:
        raise Held("revise_declaration", f"every item the declaration lists as {body} is removed; nothing was filed")
    return _compress(new, body)


def _rewrite(
    text: str, old: int, new: int, numbers: Mapping[int, int], kids: Mapping[int, list[int]], have: set[int]
) -> str:
    edits = [(m.start(1), m.end(1), _carry(m.group(1), numbers, kids, have)) for m in _LIST.finditer(text)]
    for m in re.finditer(rf"(?<![\d.]){old}(?!\d|\.\d)", text):
        if any(a <= m.start() < b for a, b, _x in edits):
            continue
        if _SPELLED.search(text[max(0, m.start() - 40) : m.end() + 40]):
            raise Held(
                "revise_declaration",
                f"the declaration writes this set's count in words beside {old}, which code does not rewrite; "
                "nothing was filed",
            )
        edits.append((m.start(), m.end(), str(new)))
    if not edits:
        raise Held("revise_declaration", f"the declaration never says {old}, this set's count; nothing was filed")
    for a, b, words in sorted(edits, reverse=True):
        text = text[:a] + words + text[b:]
    return text


def declaration(
    md: str,
    old: int,
    new: int,
    variant: str,
    numbers: Mapping[int, int] | None = None,
    kids: Mapping[int, list[int]] | None = None,
    have: set[int] | None = None,
) -> tuple[str, str | None]:
    """This set's declaration carried through the revision: (the document, the
    line for the reply, or None when nothing changed). ``numbers``: each kept
    item's number before to its number after; ``kids``: each parent's
    follow-ups' new numbers; ``have``: the set's numbers before. Without them
    the set is read as numbered 1..n before and kept 1..new after."""
    if variant not in LIMITED or (old == new and not kids and numbers is None):
        return md, None
    if crosses(old, new):
        way = "over" if new > STATUTORY_LIMIT else "under"
        raise Held(
            "revise_declaration",
            f"the set goes from {old} to {new}, {way} {STATUTORY_LIMIT}, so whether it carries a declaration "
            "changes; nothing was filed",
        )
    block = decl_block(md, variant)
    if block is None:
        if new > STATUTORY_LIMIT and old != new:
            raise Held("revise_declaration", f"the set has {new} and no declaration code can find; nothing was filed")
        return md, None
    items = set_items.read(md, variant)
    first = items[0].number if items else 1
    have = have if have is not None else set(range(first, first + old))
    numbers = numbers if numbers is not None else {n: n for n in range(first, first + min(old, new))}
    a, b = block
    text = _rewrite(md[a:b], old, new, numbers, kids or {}, have)
    if text == md[a:b]:
        return md, None
    line = f"the declaration's count of this set is now {new} (was {old})" if new != old else (
        "the declaration's lists of items follow the new numbering"
    )  # fmt: skip
    return md[:a] + text + md[b:], line


__all__ = ["decl_block", "declaration"]
