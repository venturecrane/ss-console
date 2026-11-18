"""Where the medical chronology's writer cuts its reading windows. $0, no model.

WHY (2026-11-18, measured on a real 509-page update). The writer (``map``,
``stages/compose``) was sent up to 240,000 characters per request: the 509
pages went in five requests of 96, 78, 131, 140 and 64 pages, and one visit's
five pages, read inside such a request, were never written up. A plain-code
pass over the same pages found a date of service on 383 of the 509 pages and
a note-start heading on 129; the visits those signals mark ran a median of 2
pages, 6 to 7 at the 90th percentile and 26 to 31 at most.

So a request holds at most WINDOW_PAGES pages of ONE file, and a window is
cut only where a note starts:

* a page opens a note when the dates of service its top share states
  (``select.service_dates``) share none with the note before it, or when
  its top share states a note heading (``noteheading.opens_note``: an
  author, a date of service, a signer) that repeats none of that note's
  dates (an EMR prints its note header, date and all, on every page);
* a page whose top share says "(continued)" carries its date into the note
  it continues and never opens one;
* a page with no signal joins the note before it;
* notes are packed into a window while they fit; a note longer than the
  window is cut where the window fills and runs on into the next one, which
  opens with the foot of the page before as context (``chunking.context_block``);
* a window never crosses a file: each file is cut alone.

Every character of the file's text is in exactly one window, and a page's
note heading and context blocks (``select.selected_text`` writes them ahead
of its page mark) travel with the page they belong to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..stages.chunking import CONTEXT_BLOCK, char_parts, context_block
from .update import noteheading as NH
from .update.select import PAGE_MARK, service_dates

#: Pages per writer request. A window this size holds a median visit (2
#: pages) six times over and a 90th-percentile visit (6 to 7) whole; only the
#: longest notes (26 to 31 pages on the measured update) are cut, at a page
#: break, with the neighbouring page as context.
WINDOW_PAGES = 12

#: A banner that continues a note on a later page ("12/08 Admission (continued)").
CONTINUED = re.compile(r"(?i)\(continued\)")

#: The blocks ``select.selected_text`` writes ahead of a page mark: a note
#: heading block, then the foot of the page before. Anchored at a line start
#: and running to the mark, so a page's own text is never taken for one.
_PRE = re.compile(
    r"(?s)(?:^|\n)((?:\[note heading, p\.\d+: [^\n]*\n(?:(?!\[end note heading\]).)*?\[end note heading\]\n"
    r"|\[note heading: [^\n]*\n)?"
    r"(?:\[context, the foot of the page before[^\n]*\n(?:(?!\[end context\]).)*?\[end context\]\n)?)\Z"
)


@dataclass(frozen=True)
class Page:
    """One page of a file's text: its number, where its segment opens and
    ends in the text, and its own words (no mark line, no block)."""

    number: int
    start: int
    end: int
    text: str


def pages_of(txt: str) -> list[Page]:
    """Every page-marked page of `txt` in order. Text ahead of the first mark rides with the first page."""
    marks = list(PAGE_MARK.finditer(txt))
    opens: list[int] = []
    floor = 0
    for m in marks:
        pre = _PRE.search(txt, floor, m.start())
        opens.append(pre.start(1) if pre else m.start())
        floor = m.end()
    if opens:
        opens[0] = 0
    out = []
    for k, m in enumerate(marks):
        end = opens[k + 1] if k + 1 < len(marks) else len(txt)
        own = NH.BLOCK.sub("", CONTEXT_BLOCK.sub("", txt[m.end() : end]))
        out.append(Page(int(m.group(1)), opens[k], end, own))
    return out


def note_starts(pages: list[Page]) -> list[bool]:
    """Per page, whether it opens a note (the module's rule)."""
    out: list[bool] = []
    dates: set[str] = set()
    for i, p in enumerate(pages):
        top = "\n".join(NH.top_lines(p.text))
        said = service_dates(top)
        if i == 0:
            start = True
        elif CONTINUED.search(top):
            start = False
        elif said and dates and not said & dates:
            start = True  # the date of service changed
        else:  # a heading, unless it repeats a date of the note it sits in (a per-page header)
            start = NH.opens_note(p.text) and not said & dates
        dates = set(said) if start else dates | said
        out.append(start)
    return out


def windows(pages: list[Page], chunk: int, limit: int = WINDOW_PAGES) -> list[list[Page]]:
    """The pages in windows of at most `limit` pages and ~`chunk` characters,
    cut only where a note starts, except inside a note too long for one."""
    notes: list[list[Page]] = []
    for p, start in zip(pages, note_starts(pages)):
        if start or not notes:
            notes.append([p])
        else:
            notes[-1].append(p)

    def size(ps: list[Page]) -> int:
        return sum(p.end - p.start for p in ps)

    out: list[list[Page]] = []
    cur: list[Page] = []
    for note in notes:
        if cur and (len(cur) + len(note) > limit or size(cur) + size(note) > chunk):
            out.append(cur)
            cur = []
        for p in note:  # a note longer than a window is cut where the window fills
            if cur and (len(cur) >= limit or size(cur) + p.end - p.start > chunk):
                out.append(cur)
                cur = []
            cur.append(p)
    if cur:
        out.append(cur)
    return out


def _with_context(txt: str, wins: list[list[Page]]) -> list[str]:
    """Each window's text; where the page beside a cut is the next page of
    the file, its foot or head rides along as context."""
    out = []
    for k, w in enumerate(wins):
        part = txt[w[0].start : w[-1].end]
        pre = post = ""
        if k > 0 and wins[k - 1][-1].number + 1 == w[0].number:
            prev = wins[k - 1][-1]
            pre = context_block(prev.text, prev.number, before=True)
        if k + 1 < len(wins) and w[-1].number + 1 == wins[k + 1][0].number:
            nxt = wins[k + 1][0]
            post = context_block(nxt.text, nxt.number, before=False)
        if post and not part.endswith("\n"):
            part += "\n"
        out.append(pre + part + post)
    return out


def cut(txt: str, chunk: int) -> list[str]:
    """One file's text as the writer's requests (``chunking.build_chunks``'s
    `cut`). Text with no page marks is cut by characters, as before."""
    pages = pages_of(txt)
    if not pages:
        return [txt] if len(txt) <= chunk else char_parts(txt, chunk)
    return _with_context(txt, windows(pages, chunk))
