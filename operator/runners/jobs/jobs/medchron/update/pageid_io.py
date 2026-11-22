"""What ``stages/update_pages`` settled by page picture, read back for
``update_select``. $0.

``update/page_identity.json`` is parsed, never cast: a missing or malformed
file reads as "nothing settled by picture", so the text rules decide every
page exactly as before (the record is an addition, never a precondition).

A pixel-equal page is excluded as it stands (it was never transcribed). A
NEAR page was transcribed and is READ: it is a suspected copy (2026-11-22),
never set aside. ``confirmed`` says whether its words agree with its picture
(the same dates and the same clinical values as the page it matched, print
stamps aside, and most of the same words in any order); it once set the page
aside, and the rules below it still build on that answer. A re-printed hospital record once failed both: its "Generated on"
stamp carried a new date and its columns flowed in another order, so three
of its delivered pages were composed again as a new exhibit (2026-10-14). A form filled in with one other
number looks like its twin and says another thing; it is read.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import pageid as PI
from .select import ALREADY, FAILED_MARKS, MIN_CHARS, OVERLAP, _dates, without_furniture
from .subsets import Subset
from .values import clinical_values

NAME = "page_identity.json"
_REASON = {PI.ALREADY: ALREADY, PI.REPEAT: OVERLAP}
#: The share of the smaller page's words (counted as a bag) the two
#: transcriptions must share.
WORDS_FRAC = 0.8


@dataclass(frozen=True)
class Page:
    reason: str  # select.ALREADY or select.OVERLAP
    other: str  # "exhibit:N" or a new document's id
    page: int
    exact: bool

    @property
    def aside(self) -> bool:
        """Pixel-equal: the only match that sets a page aside as a copy."""
        return self.exact


@dataclass
class Loaded:
    pages: dict[str, dict[int, Page]] = field(default_factory=dict)
    extracts: dict[str, Subset] = field(default_factory=dict)
    #: {extract: {page: Page}}, for an extract whose whole document turns out never read.
    extract_pages: dict[str, dict[int, Page]] = field(default_factory=dict)
    #: The record asked which documents will not be read (2026-11-22). One written before never did.
    tested: bool = False


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
    if why not in _REASON or not isinstance(other, str) or n is None or tier not in ("exact", "near"):
        return None
    return Page(_REASON[why], other, n, tier == "exact")


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
    return Loaded(
        _pages(raw.get("pages")),
        _extracts(raw.get("extracts")),
        _pages(raw.get("extract_pages")),
        isinstance(raw.get("never_read"), list),
    )


def reopened(ident: Loaded, never_read: set[str]) -> Loaded:
    """`ident` with every extract of a document that is never read decided
    page by page (``extract_pages``) instead of set aside whole."""
    back = {d for d, s in ident.extracts.items() if s.parent in never_read and ident.extract_pages.get(d)}
    return Loaded(
        ident.pages | {d: ident.extract_pages[d] for d in back},
        {d: s for d, s in ident.extracts.items() if d not in back},
        ident.extract_pages,
        ident.tested,
    )


def _words(text: str) -> Counter[str]:
    """The page's words as a bag (digits stripped): word ORDER is how a text
    layer happens to flow, not what the page says. A record re-printed by its
    EMR, or re-OCRed, reads its columns in another order, so its 8-word
    shingles barely overlap its twin's while every word is the same."""
    return Counter(re.findall(r"[a-z]{2,}", re.sub(r"\d", " ", text.lower())))


def confirmed(mine: str, theirs: str) -> bool:
    """A near picture match has its words' agreement when both transcriptions state
    the same dates and the same clinical values (fax, print and export
    stamp lines aside, ``select.without_furniture``: a re-printed copy
    carries a new print date) and share most words, in any order."""
    a, b = (mine or "").strip(), (theirs or "").strip()
    if len(a) < MIN_CHARS or len(b) < MIN_CHARS or a in FAILED_MARKS or b in FAILED_MARKS:
        return False
    a, b = without_furniture(a), without_furniture(b)
    if _dates(a) != _dates(b) or set(clinical_values(a)) != set(clinical_values(b)):
        return False
    wa, wb = _words(a), _words(b)
    small = min(sum(wa.values()), sum(wb.values()))
    return small > 0 and sum((wa & wb).values()) >= WORDS_FRAC * small


def reasons(found: dict[int, Page], pages: dict[int, str], text_of: Callable[[str, int], str]) -> dict[int, str]:
    """{page: select reason} for one document: every pixel-equal page, and
    every near page whose words confirm it (`text_of(other, page)` reads the
    matched page's text: a delivered exhibit's text layer or a new
    document's transcription)."""
    out: dict[int, str] = {}
    for p, f in found.items():
        if f.exact or confirmed(pages.get(p) or "", text_of(f.other, f.page)):
            out[p] = f.reason
    return out


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
