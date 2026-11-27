"""A paper's definitions, read off the blocks of its Definitions region.

A definition opens with its term in quotes, numbered or not, the number in the
text or Word's own; the comma or period may sit inside the closing quote
("PERSON," means), the quotes may be straight or curly, and a plural form may
follow in quotes ("DOCUMENT" or "DOCUMENTS") or as a "(S)" tail. A block that
opens with no term continues the definition before it; one before the first
term is the region's introduction.
"""

from __future__ import annotations

import re

from .model import Block, Definition

#: Each opening quote mark and the marks that close it. A double quote closes
#: only on a double quote; a single quote closes only on a single quote that
#: is not followed by a letter, so an apostrophe inside a word ("PLAINTIFF'S")
#: never ends a term.
_PAIRS = {'"': '"”', "“": '”"', "'": "'’", "‘": "’'"}
_SINGLE = "'’"
_LEAD = re.compile(r"^[^\w\"“'‘]*(?:(\d+)[.)]\s*)?")
_OR = re.compile(r"^\s*,?\s*(?:or|and/or|and)\s+")
_PAREN_PLURAL = re.compile(r"^(.*?)\((E?S)\)$", re.IGNORECASE)
_MAX_TERM = 80


def _quoted(text: str, at: int) -> tuple[str, str, int] | None:
    """The quoted span opening at ``at``: (inside, opening mark, index past the
    closing mark), or None when no mark that pairs with the opening closes it."""
    if at >= len(text) or text[at] not in _PAIRS:
        return None
    opener, closers = text[at], _PAIRS[text[at]]
    for k in range(at + 1, min(len(text), at + 2 + _MAX_TERM)):
        c = text[k]
        if c == "\n" or (c in '"“”' and c not in closers):
            return None
        if c in closers:
            if c in _SINGLE and k + 1 < len(text) and text[k + 1].isalpha():
                continue  # an apostrophe inside a word
            inside = text[at + 1 : k]
            return (inside, opener, k + 1) if inside.strip() else None
    return None


def _number(b: Block, in_text: str | None) -> int | None:
    if in_text:
        return int(in_text)
    m = re.fullmatch(r"\(?(\d+)[.):]?", b.numbered or "")
    return int(m.group(1)) if m else None


def _plural_after(text: str) -> str | None:
    m = _OR.match(text)
    got = _quoted(text, m.end()) if m else None
    return got[0].strip().rstrip(",.;:").strip() if got else None


def term_of(b: Block) -> Definition | None:
    """The definition this block opens, or None when it opens none."""
    lead = _LEAD.match(b.text)
    assert lead is not None
    got = _quoted(b.text, lead.end())
    if got is None:
        return None
    inside, opener, past = got
    raw = inside.strip()
    punct_inside = raw[-1:] in ",.;:"
    term = raw.rstrip(",.;:").strip()
    paren = _PAREN_PLURAL.match(term)
    if paren:
        term = paren.group(1).strip()
        plural: str | None = term + paren.group(2)
    else:
        plural = _plural_after(b.text[past:])
    return Definition(
        term=term,
        plural=plural,
        number=_number(b, lead.group(1)),
        ids=[b.id],
        punct_inside=punct_inside,
        quote="“”" if opener in "“‘" else '"',
    )


def read(region: list[Block]) -> tuple[list[Definition], list[str]]:
    """The definitions in ``region`` (its title already removed), and the ids of
    the introduction before the first term."""
    defs: list[Definition] = []
    intro: list[str] = []
    for b in region:
        d = term_of(b)
        if d is not None:
            defs.append(d)
        elif defs:
            defs[-1].ids.append(b.id)
        else:
            intro.append(b.id)
    return defs, intro
