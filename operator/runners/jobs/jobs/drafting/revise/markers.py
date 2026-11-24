"""A drafted response's candidate-objection markers, settled by code.

A first draft marks each objection it proposes, never takes it:
``{{ATTORNEY: CANDIDATE OBJECTION: privacy | the basis ...}}`` (or the shell's
``{{CANDIDATE OBJECTION: ground | ...}}``). When the firm's rules or the
attorney's own instruction name that ground, the objection is taken: code
writes the phrase book's long form for it where the marker stood (once; a
response that already states it keeps its own) and the marker goes. Nothing
else is ever touched: every other ``{{ATTORNEY: ...}}`` and ``{{CLIENT: ...}}``
marker stays exactly as drafted.

A marker is left as drafted, and named, when its ground matches no objection
in the book or more than one (``phrase_missing``), or when no rule in force and
no instruction for that response names the ground (``ground_not_named``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from ..rulebook.types import ITEM_WORD, SET_DOC
from .checklist import _words

#: The opening of a candidate-objection marker, after its ``{{``.
_OPEN = re.compile(r"\{\{\s*(?:ATTORNEY\s*:\s*)?CANDIDATE\s+OBJECTION\b\s*[:\-–—]?\s*", re.I)
#: The word the book's ``{item}`` slot takes, per set type: the stage's own (``rulebook.types``).
ITEM = {kind: ITEM_WORD[doc] for kind, doc in SET_DOC.items()}
PHRASE_MISSING = "phrase_missing"
NOT_NAMED = "ground_not_named"


@dataclass(frozen=True)
class Settled:
    number: str
    key: str
    #: "" when converted; else the reason code it was left.
    left: str = ""
    ground: str = ""


def spans(text: str) -> list[tuple[int, int, str]]:
    """Each candidate-objection marker as (start, end, its ground words): the
    marker runs to its balanced ``}}`` (a basis may carry a marker of its
    own); the ground is its text before the first ``|``."""
    out = []
    for m in _OPEN.finditer(text):
        depth, i = 1, m.end()
        while i < len(text) and depth:
            if text.startswith("{{", i):
                depth, i = depth + 1, i + 2
            elif text.startswith("}}", i):
                depth, i = depth - 1, i + 2
            else:
                i += 1
        if depth:
            continue  # an unclosed marker is never cut
        body = text[m.end() : i - 2]
        out.append((m.start(), i, body.split("|", 1)[0].strip()))
    return out


def _named(text: str, names: Iterable[str]) -> bool:
    hay = f" {_words(text)} "
    return any(_words(n) and f" {_words(n)} " in hay for n in names)


def ground_keys(ground: str, book: Any) -> list[str]:
    """The book's objection entries whose short names the marker's ground states."""
    return [k for k, e in book.entries.items() if e.kind == "objection" and _named(ground, [*e.names])]


def named_by(texts: Iterable[str], book: Any) -> set[str]:
    """The objection keys any of ``texts`` (the rules in force) names by a short name."""
    joined = " ".join(texts)
    return {k for k, e in book.entries.items() if e.kind == "objection" and _named(joined, e.names)}


def _tidy(block: str) -> str:
    return re.sub(r"\s+([.;,])", r"\1", " ".join(block.split()))


def settle(
    number: str, blocks: list[str], book: Any, set_type: str, allowed: set[str]
) -> tuple[list[str], list[Settled]]:
    """One response's paragraphs with its candidate markers settled. ``allowed``:
    the objection keys a rule in force or this response's instruction names."""
    item = ITEM.get(set_type, "Request")
    whole = " ".join(_words(b) for b in blocks)
    out: list[str] = []
    done: list[Settled] = []
    for b in blocks:
        cuts = spans(b)
        if not cuts:
            out.append(b)
            continue
        new, at = [], 0
        for s, e, ground in cuts:
            keys = ground_keys(ground, book)
            if len(keys) != 1:
                done.append(Settled(number, "", PHRASE_MISSING, ground))
                continue
            if keys[0] not in allowed:
                done.append(Settled(number, keys[0], NOT_NAMED, ground))
                continue
            text = book.text(keys[0], item=item)
            already = f" {_words(text)} " in f" {whole} "
            new += [b[at:s], "" if already else text]
            at = e
            whole += f" {_words(text)}"
            done.append(Settled(number, keys[0], "", ground))
        new.append(b[at:])
        joined = _tidy("".join(new))
        if joined.strip(" .;,"):
            out.append(joined if joined != _tidy(b) else b)
    return out, done


__all__ = ["ITEM", "NOT_NAMED", "PHRASE_MISSING", "Settled", "ground_keys", "named_by", "settle", "spans"]
