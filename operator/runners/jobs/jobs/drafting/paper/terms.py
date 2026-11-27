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

_Q_OPEN, _Q_CLOSE = "\"“'‘", "\"”'’"
_TERM = re.compile(rf"^[^\w\"“]*(?:(\d+)[.)]\s*)?([{_Q_OPEN}])([^\"“”\n]{{1,80}}?)([,.;:]?)([{_Q_CLOSE}])")
_PLURAL = re.compile(rf"^\s*,?\s*(?:or|and/or|and)\s+([{_Q_OPEN}])([^\"“”\n]{{1,80}}?)[,.;:]?[{_Q_CLOSE}]")
_PAREN_PLURAL = re.compile(r"^(.*?)\((E?S)\)$", re.IGNORECASE)


def _number(b: Block, in_text: str | None) -> int | None:
    if in_text:
        return int(in_text)
    m = re.fullmatch(r"\(?(\d+)[.):]?", b.numbered or "")
    return int(m.group(1)) if m else None


def term_of(b: Block) -> Definition | None:
    """The definition this block opens, or None when it opens none."""
    m = _TERM.match(b.text)
    if not m:
        return None
    term, plural = m.group(3).strip(), None
    paren = _PAREN_PLURAL.match(term)
    if paren:
        term = paren.group(1).strip()
        plural = term + paren.group(2)
    else:
        pm = _PLURAL.match(b.text[m.end() :])
        plural = pm.group(2).strip() if pm else None
    curly = m.group(2) in "“‘"
    return Definition(
        term=term,
        plural=plural,
        number=_number(b, m.group(1)),
        ids=[b.id],
        punct_inside=bool(m.group(4)),
        quote="“”" if curly else '"',
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
