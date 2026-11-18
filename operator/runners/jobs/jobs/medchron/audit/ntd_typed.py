"""The handwritten-records note never stands on a page that was read. $0, no model.

WHY (2026-11-07, the chronology answer key). The map prompt inserts the
firm's house note (``wording.NTD_ILLEGIBLE``: "a portion of the records were
handwritten and illegible...") where a page or portion is handwritten or
illegible. On one client chronology it stood on 62 entries, and SMD deleted
54 of them by hand because the page they cited was typed. Run on that
chronology with the saved exhibit PDFs' text, this rule removes 48 of the 54
SMD deleted and none of the 8 SMD kept; the 6 it leaves cite pages with no
text layer.

THE RULE. A note is removed when it cites at least one page and EVERY page it
cites was read: its transcript has ``TYPED_WORDS`` or more real words (three
letters or more, with a vowel) and at most ``ILLEGIBLE_MARKS`` places the
transcriber marked ``[illegible]``. A note citing any page that was not read,
or a page with no transcript, or no page at all, stays. The note's words are
the firm's and are never changed; only where it may stand is decided here. It
is an ``[NTD: ...]`` span the audit never grades (``claims.NTD``), so removing
it changes no claim and no verdict.

THE HANDWRITTEN MARK NO LONGER KEEPS A NOTE (2026-11-18, the first full run
on the small reading windows). Until then a page the vision pass opened with
``[handwritten]`` (``update/handwritten.MARK``) kept its note whatever its
transcript held. The pass sets that mark for any handwritten value, a
signature included, and 13 notes stood in a filed chronology on 14 marked
pages: each transcript carried 52 to 570 real words and at most one
``[illegible]`` place, and read against the page pictures none of the 13 was
right (8 had only a signature or a small field in handwriting, 5 were legible
throughout). The note says the record was handwritten AND illegible; a page
transcribed in full is not illegible. On the 2026-11-07 chronology, which has
no transcripts and so no marks, this rule and the one before it are the same:
48 of the 54 removed, none of the 8 kept.
"""

from __future__ import annotations

import re
from typing import Callable

from ... import wording
from . import claims as CL

#: Real words a page's transcript must carry to count as typed (at this line
#: none of the notes SMD kept was removed).
TYPED_WORDS = 40
#: The note as the paragraph carries it (its first words, so a wrapped or
#: re-flowed copy still matches).
_NOTE = wording.NTD_ILLEGIBLE[:60]
_REAL = re.compile(r"\b[a-z]{3,}\b")
_VOWEL = re.compile(r"[aeiouy]")
#: Places marked unreadable a read page may carry. A signature is marked
#: ``[illegible signature]`` and is not one of these. Every judged page of
#: 2026-11-18 carried none or one, and none was illegible; a page with more
#: has not been judged, so its note stays.
ILLEGIBLE_MARKS = 1
_ILLEGIBLE = re.compile(r"\[illegible\]", re.I)
#: Pages read per cited range at most: a note cites the pages it stands on.
_SPAN = 30


def real_words(text: str | None) -> int:
    return sum(1 for t in _REAL.findall((text or "").lower()) if _VOWEL.search(t))


def typed(text: str | None) -> bool:
    """A transcript of a page that was read: enough real words, and no more
    unreadable places than ``ILLEGIBLE_MARKS``. Handwriting that was read
    counts as read."""
    if text is None:
        return False
    return real_words(text) >= TYPED_WORDS and len(_ILLEGIBLE.findall(text)) <= ILLEGIBLE_MARKS


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


__all__ = ["ILLEGIBLE_MARKS", "TYPED_WORDS", "drop", "real_words", "typed"]
