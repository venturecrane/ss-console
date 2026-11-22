"""What ``stages/update_pages`` settled by page picture, read back for
``update_select``. $0.

``update/page_identity.json`` is parsed, never cast: a missing or malformed
file reads as "nothing settled by picture", so every page is read (the record
is an addition, never a precondition).

Only a page pixel-equal to a page that was READ is set aside (``Page.aside``):
it was never transcribed. Every other picture match (a near one, or a
pixel-equal one whose keeper was never read) is a SUSPECTED copy: the page is
transcribed and read, and the match is recorded beside it. A near match once
had to be confirmed by its words; the words test called pages that differ the
same (2026-11-22, a paid proof: 25 differing pages settled as copies), so no
word test sets a page aside now.

A record written before 2026-11-22 carries no ``rule`` stamp
(``pageid.RULE``): its pixel-equal pages were set aside without their keeper
being tested, and were never transcribed. They stay set aside, because a
page the vision pass never read cannot be read now without it (``Loaded.rule``
says which rule wrote the record; ``update_select`` logs it).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import pageid as PI
from .select import ALREADY, OVERLAP
from .subsets import Subset

NAME = "page_identity.json"
_REASON = {PI.ALREADY: ALREADY, PI.REPEAT: OVERLAP}
_TIERS = (PI.EXACT, PI.EXACT_UNREAD, PI.NEAR)
#: ``Loaded.rule`` of a record written before the keeper test.
OLD_RULE = "every pixel-equal page set aside"


@dataclass(frozen=True)
class Page:
    reason: str  # select.ALREADY or select.OVERLAP
    other: str  # "exhibit:N" or a new document's id
    page: int
    exact: bool
    keeper_read: bool = True

    @property
    def aside(self) -> bool:
        """Pixel-equal to a page that was read: set aside, never transcribed."""
        return self.exact and self.keeper_read


@dataclass
class Loaded:
    pages: dict[str, dict[int, Page]] = field(default_factory=dict)
    extracts: dict[str, Subset] = field(default_factory=dict)
    #: ``pageid.RULE`` for a record that tested each keeper, else ``OLD_RULE`` ("" with no record).
    rule: str = ""


def delivered_exhibit(parent: str) -> int | None:
    """The delivered exhibit an extract re-files, when its parent is one."""
    n = parent[len(PI.DELIVERED) :]
    return int(n) if parent.startswith(PI.DELIVERED) and n.isdigit() else None


def _int(x: object) -> int | None:
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    return int(x) if isinstance(x, str) and x.isdigit() else None


def _page(v: object) -> Page | None:
    if not isinstance(v, list) or len(v) != 4:
        return None
    why, other, page, tier = v
    n = _int(page)
    if why not in _REASON or not isinstance(other, str) or n is None or tier not in _TIERS:
        return None
    return Page(_REASON[why], other, n, tier != PI.NEAR, tier != PI.EXACT_UNREAD)


def _pages(raw: object) -> dict[str, dict[int, Page]]:
    out: dict[str, dict[int, Page]] = {}
    for d, m in (raw if isinstance(raw, dict) else {}).items():
        got: dict[int, Page] = {}
        for p, v in (m if isinstance(m, dict) else {}).items():
            n, pg = _int(p), _page(v)
            if n is not None and pg is not None:
                got[n] = pg
        out[str(d)] = got
    return out


def _extracts(raw: object) -> dict[str, Subset]:
    out: dict[str, Subset] = {}
    for d, e in (raw if isinstance(raw, dict) else {}).items():
        if not isinstance(e, dict):
            continue
        ex, parent = _int(e.get("exhibit")), e.get("parent")
        pages, matched = _int(e.get("pages")), _int(e.get("matched"))
        if pages is None or matched is None:
            continue
        if ex is not None:
            out[str(d)] = Subset(str(d), f"{PI.DELIVERED}{ex}", pages, matched)
        elif isinstance(parent, str) and parent:
            out[str(d)] = Subset(str(d), parent, pages, matched)
    return out


def load(ud: Path) -> Loaded:
    p = ud / NAME
    try:
        raw = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    rule = PI.RULE if raw.get("rule") == PI.RULE else (OLD_RULE if raw else "")
    return Loaded(_pages(raw.get("pages")), _extracts(raw.get("extracts")), rule)


def aside(found: dict[int, Page]) -> dict[int, str]:
    """{page: select reason} for one document's pages set aside by picture."""
    return {p: f.reason for p, f in found.items() if f.aside}


def merge_extracts(by_text: dict[str, Subset], by_image: dict[str, Subset]) -> dict[str, Subset]:
    """Both kinds of extract, each cited to the outermost document: a text
    extract of a document that is itself an image extract is cited where
    that one is."""
    merged = {**by_text, **by_image}
    out: dict[str, Subset] = {}
    for d, s in merged.items():
        parent, seen = s.parent, {d}
        while parent in merged and parent not in seen:
            seen.add(parent)
            parent = merged[parent].parent
        if parent in merged:
            continue  # the two rules disagree on which is the whole: it stays an exhibit and is read
        out[d] = Subset(d, parent, s.pages, s.matched)
    return out
