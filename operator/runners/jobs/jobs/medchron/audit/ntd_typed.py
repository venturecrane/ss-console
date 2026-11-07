"""The handwritten-records note never stands on a typed page. $0, no model.

WHY (2026-11-07, the chronology answer key). The map prompt inserts the
firm's house note (``wording.NTD_ILLEGIBLE``: "a portion of the records were
handwritten and illegible...") where a page or portion is handwritten or
illegible. On one client chronology it stood on 62 entries, and SMD deleted
54 of them by hand because the page they cited was typed. Run on that
chronology with the saved exhibit PDFs' text, this rule removes 48 of the 54
SMD deleted and none of the 8 SMD kept; the 6 it leaves cite pages with no
text layer.

THE RULE. A note is removed when it cites at least one page and EVERY page it
cites is typed: its transcript has ``TYPED_WORDS`` or more real words (three
letters or more, with a vowel) and the vision pass did not mark it
``[handwritten]`` (``update/handwritten.MARK``). A note citing any page that
is not typed, or a page with no transcript, or no page at all, stays. The
note's words are the firm's and are never changed; only where it may stand
is decided here. It is an ``[NTD: ...]`` span the audit never grades
(``claims.NTD``), so removing it changes no claim and no verdict.
"""

from __future__ import annotations

import re
from typing import Callable

from ... import wording
from ..update import handwritten as H
from . import claims as CL

#: Real words a page's transcript must carry to count as typed (at this line
#: none of the notes SMD kept was removed).
TYPED_WORDS = 40
#: The note as the paragraph carries it (its first words, so a wrapped or
#: re-flowed copy still matches).
_NOTE = wording.NTD_ILLEGIBLE[:60]
_REAL = re.compile(r"\b[a-z]{3,}\b")
_VOWEL = re.compile(r"[aeiouy]")
#: Pages read per cited range at most: a note cites the pages it stands on.
_SPAN = 30


def real_words(text: str | None) -> int:
    return sum(1 for t in _REAL.findall((text or "").lower()) if _VOWEL.search(t))


def typed(text: str | None) -> bool:
    """A transcript that reads as typed text, not a handwritten page."""
    return text is not None and not H.is_handwritten(text) and real_words(text) >= TYPED_WORDS


def _cited(paragraph: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for m in CL.CITE.finditer(paragraph):
        pages = CL.parse_pages(m.group(2))[:_SPAN] if m.group(2) else []
        out += [(int(m.group(1)), p) for p in pages]
    return out


def _without_note(paragraph: str) -> str:
    """The paragraph with its note gone; empty when only citations remained."""
    rest = CL.NTD.sub("", paragraph)
    return "" if not CL.CITE.sub("", rest).strip(" \t\n.;,") else rest.strip()


def drop(body: str, page: Callable[[int, int], str | None]) -> tuple[str, int]:
    """(``body`` without each note every cited page of which is typed, how
    many were removed). ``page(exhibit, page)``: that page's transcript."""
    paras = body.split("\n\n")
    out: list[str] = []
    n = 0
    for p in paras:
        cites = _cited(p) if _NOTE in p else []
        if cites and all(typed(page(ex, pg)) for ex, pg in cites):
            n += 1
            rest = _without_note(p)
            if rest:
                out.append(rest)
            continue
        out.append(p)
    return "\n\n".join(out), n


__all__ = ["TYPED_WORDS", "drop", "real_words", "typed"]
