"""An entry cut into numbered lines for the entry read, and the ONE sentence
splitter both the labels and the edit tripwire's sentence count use. $0.

WHY (2026-11-18, round 3). A $0 proof showed the one-shot read let wrong
sentences pass unchecked: of 53 known errors it missed, 34 were sentences it
simply passed. The hand readers that caught them accounted for every
sentence. So the read is shown the entry as labelled lines, ``[D]`` its date
line, ``[P]`` its provider line, ``[S1]``, ``[S2]``... one sentence each with
its own citations, and its reply must give every label a verdict
(``entry_read.parse`` refuses a reply that skips one).

A heading-only line or a bare citation line gets no label and is shown as
it is, so the read sees the entry whole. Each labelled sentence is a slice
of the entry's own text, character for character, so a quote inside it and
an edit to it are found exactly. A prior-history block has S labels only.
"""

from __future__ import annotations

import re

from ..audit import claims as CL
from . import body as B

_END = re.compile(r"(?<=[.!?])\s+")
_WORDY = re.compile(r"[A-Za-z]{2,}")
_HIDE = "\x00"


def _is_heading(line: str) -> bool:
    """A heading or a bare citation. A line with words AND a citation is
    never a heading (round 4): "BP 128/84, pulse 72 (Exhibit 1 - p. 2)" is a
    statement the read must judge."""
    text = CL.CITE.sub("", line).strip()
    if not _WORDY.search(text):
        return True
    if CL.CITE.search(line):
        return False
    return len(text) <= 60 and not re.search(r"[.!?:;]$", text)


def _wordy(s: str) -> bool:
    return bool(_WORDY.search(CL.CITE.sub("", s)))


def _cuts(hidden: str) -> list[tuple[int, int]]:
    """(start, end) of each gap between sentences: whitespace after . ! or ?,
    moved past a citation that closes the sentence before it."""
    out: list[tuple[int, int]] = []
    for m in _END.finditer(hidden):
        a, b = m.start(), m.end()
        while b < len(hidden) and hidden[b] == _HIDE:
            while b < len(hidden) and hidden[b] == _HIDE:
                b += 1
            a = b
            while b < len(hidden) and hidden[b].isspace():
                b += 1
        if b < len(hidden) and (not out or a >= out[-1][1]):
            out.append((a, b))
    return out


def line_sentences(line: str) -> list[str]:
    """The sentences of one line, each with its own citations, exactly as
    the line has them; none for a heading or a bare citation."""
    if _is_heading(line):
        return []
    hidden = CL.CITE.sub(lambda m: _HIDE * len(m.group()), line)
    bounds, start = [], 0
    for a, b in _cuts(hidden):
        bounds.append((start, a))
        start = b
    bounds.append((start, len(line)))
    spans: list[list[int]] = []
    for s, e in bounds:
        if spans and not _wordy(line[s:e]):
            spans[-1][1] = e  # a citation-only piece closes the sentence before it
        elif spans and not _wordy(line[spans[-1][0] : spans[-1][1]]):
            spans[-1][1] = e  # a leading citation-only piece opens this one
        else:
            spans.append([s, e])
    return [line[s:e].strip() for s, e in spans if _wordy(line[s:e])]


def numbered(text: str, record_only: bool = False) -> tuple[str, dict[str, str]]:
    """(the entry as the read is shown it, label -> that line's exact text)."""
    shown: list[str] = []
    lines: dict[str, str] = {}
    k = 0
    for i, ln in enumerate(text.split("\n")):
        if not record_only and i < 2:
            label = "DP"[i]
            lines[label] = ln
            shown.append(f"[{label}] {ln}")
            continue
        sents = line_sentences(ln)
        if not sents:
            shown.append(ln)
        for s in sents:
            k += 1
            lines[f"S{k}"] = s
            shown.append(f"[S{k}] {s}")
    return "\n".join(shown), lines


def count_sentences(body: str) -> int:
    """Sentences of the chronology body: entry text past its date and
    provider lines, the preamble, never a heading or a bare citation."""
    pre, ents = B.split_body(body)
    lines = pre.splitlines() + [ln for e in ents for ln in e.splitlines()[2:]]
    return sum(len(line_sentences(ln)) for ln in lines)
