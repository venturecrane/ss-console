"""Pleading paper's margin line numbers, removed from a page's extracted text.

Pleading paper prints 1 to 28 down the left margin of every page. A text
layer carries those numbers as words, so extraction interleaves them with the
pleading's own: a line opens (or closes) with one, or one stands alone between
two lines of text. Read as prose, a sentence that wraps across printed lines
then carries a number inside it, and a quotation taken from it does too
(2026-11-05: a served discovery set quoted the defendant's answer as "lease, 26
occupy" and "negligent or 8 otherwise"). The numbers say where a line sits on
the paper, never what the pleading says, so they are removed here, where the
text is written, and every later reader (the drafting corpus, the evidence
pass, the quote gate) sees the pleading's words alone: a quotation taken from
them is clean and still verbatim against the source.

A page is pleading paper when the numbers standing at a line's start or end
(or alone on a line) run 1, 2, 3 ... in order through at least ``MIN_RUN``. Only
the numbers of that run are removed, and only from those positions: an
answer's own paragraph numbers ("1. Defendant admits") and a number inside a
sentence ("within 30 days") are never touched. A deposition transcript also
numbers its lines, and a page:line cite depends on them, so a page carrying
``Q.`` / ``A.`` labels is never treated as pleading paper (a transcript runs 25
lines; ``MIN_RUN`` is above that as well).
"""

from __future__ import annotations

import re

#: The shortest run of margin numbers that marks pleading paper (28 is the
#: usual paper; 26-line paper exists; a transcript page runs 25).
MIN_RUN = 26
_LEAD = re.compile(r"^[ \t]*(\d{1,2})(?=[ \t]|$)")
_TRAIL = re.compile(r"(?:(?<=[ \t])|^)(\d{1,2})[ \t]*$")
_LABEL = re.compile(r"(?m)^[ \t]*(?:\d{1,2}[ \t]+)?[QA][.:][ \t]")
#: A page marker line: the stage extractor's ``[p.N]`` (a machine
#: transcription may follow it) or the litigation extractor's ``===== page N =====``.
_MARK = re.compile(r"(?m)^(?:\[p\.\d+\][^\n]*|===== page \d+ =====)$")


def _run(lines: list[str]) -> list[tuple[int, str]]:
    """(line index, ``lead`` | ``trail``) of each number in the 1, 2, 3 ... run."""
    found: list[tuple[int, str]] = []
    want = 1
    for i, line in enumerate(lines):
        lead = _LEAD.match(line)
        if lead and int(lead.group(1)) == want:
            found.append((i, "lead"))
            want += 1
        trail = _TRAIL.search(line)
        if trail and (not lead or trail.start() > lead.start()) and int(trail.group(1)) == want:
            found.append((i, "trail"))
            want += 1
    return found


def strip_page(text: str) -> str:
    """One page's text without its pleading-paper margin numbers; any other
    page comes back unchanged."""
    if not text or _LABEL.search(text):
        return text
    lines = text.split("\n")
    run = _run(lines)
    if len(run) < MIN_RUN:
        return text
    drop: set[int] = set()
    for i, where in sorted(run, key=lambda r: r[1] == "lead"):  # a line's trail first: its lead's span holds
        line = lines[i]
        m = _LEAD.match(line) if where == "lead" else _TRAIL.search(line)
        if m is None:
            continue
        line = (line[m.end() :] if where == "lead" else line[: m.start()]).strip(" \t")
        lines[i] = line
        if not line:
            drop.add(i)
    return "\n".join(line for i, line in enumerate(lines) if i not in drop)


def strip_document(text: str) -> str:
    """A paged document's text with each page cleaned (``strip_page``), its
    page markers kept. Idempotent, so a cached text cleans to itself."""
    out: list[str] = []
    at = 0
    for m in _MARK.finditer(text):
        out.append(strip_page(text[at : m.start()]))
        out.append(m.group(0))
        at = m.end()
    out.append(strip_page(text[at:]))
    return "".join(out)
