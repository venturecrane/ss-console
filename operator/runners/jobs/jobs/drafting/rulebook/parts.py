"""A response read into its sentences, each with its role, phrase-book key and
ground (design C, "Parts checker"): a pure function, no I/O.

The mechanics are the hand-filing gate's (engagements, ``hand_filing/
firm_rules_check.py``: ``sentences`` and ``role``), with the firm's words taken
out of the code: a sentence's key and ground come from the phrase book, never a
constant here. What stays in code is how English discovery prose divides into
sentences, and the lead-ins any objection or transition starts with.

Each sentence keeps where it sits (``Part.block``, ``Part.at``: the block of
``responses.read`` and the offset in it), so code can write before, over or
without it in the document's own text.
"""

from __future__ import annotations

import re

from ..revise import responses as rs
from .book import Book, bare
from .types import Part, PartsDoc

#: Where one sentence of a response ends and the next begins: after a full stop,
#: a closing bracket or quote, before the lead-ins discovery prose uses.
ANCHOR = re.compile(
    r"(?<=[.)”\"])\s+(?=(?:Responding Party|Without waiving|Subject to|As to|Plaintiff|Limited to|After a diligent"
    r"|No documents|The documents?|This request|These|Defendant|Pursuant|Per |Documents|Please|Exhibit)\b)"
)
#: The transition between the objections and the answer.
TRANSITION = re.compile(r"^(?:Without waiving|Subject to)\s+(?:said|the|these|its|all)\s+objections?\b[^.]*?:?$", re.I)
#: Where a transition that runs into the answer in one sentence ends.
_TRANS_LEAD = re.compile(
    r"^(Without waiving (?:said|the|these) objections,? \S+(?: \S+)? responds? as follows:?)\s+(?=\S)", re.I
)
OBJECTION = re.compile(r"^(?:Responding Party|Plaintiff|Defendant)\s+(?:further\s+|also\s+)?objects?\b", re.I)


def sentences(text: str) -> list[tuple[int, str]]:
    """(offset, sentence) in ``text``, each sentence an exact substring of it."""
    out: list[tuple[int, str]] = []
    pos = 0
    for piece in ANCHOR.split(text):
        at = text.find(piece, pos)
        pos = at + len(piece)
        s = piece.strip()
        if not s:
            continue
        at += piece.find(s)
        m = _TRANS_LEAD.match(s)
        if m:
            out.append((at, m.group(1)))
            at, s = at + m.end(), s[m.end() :]
        out.append((at, s))
    return out


def role(sentence: str, book: Book | None) -> tuple[str, str, str]:
    """(role, book key, ground) of one sentence."""
    key = book.find(sentence) if book else None
    if key and book:
        e = book.entries[key]
        r = {"objection": "objection", "transition": "transition"}.get(e.kind, "disposition")
        return r, key, ", ".join(e.names) if r == "objection" else ""
    s = " ".join(sentence.split())
    if TRANSITION.match(s) or _TRANS_LEAD.match(s + " x"):
        return "transition", "", ""
    if OBJECTION.match(s):
        return "objection", "", ""
    return "other", "", ""


def item_parts(doc: rs.Document, r: rs.Response, book: Book | None) -> tuple[Part, ...]:
    out: list[Part] = []
    for i in r.editable:
        text = doc.blocks[i]
        skip = 0
        if i == r.head:  # a heading carrying response words: only the words after it
            got = rs._HEAD.match(rs.plain(text))
            tail = got.group(4).strip() if got else ""
            skip = text.rfind(tail) if tail else -1
            if skip < 0:
                continue
        for at, s in sentences(text[skip:]):
            got_role, key, ground = role(s, book)
            out.append(Part(s, got_role, key, ground, i, skip + at))
    return tuple(out)


def read(md: str, doc_type: str, hint: str | None = None, book: Book | None = None) -> PartsDoc:
    """The document's items and their sentences; empty when it does not read
    as numbered responses."""
    del doc_type
    try:
        doc = rs.read(md, hint)
    except rs.ResponsesUnread:
        return PartsDoc()
    return PartsDoc({r.number: item_parts(doc, r, book) for r in doc.responses})


def holds(parts: tuple[Part, ...], sentence: str) -> list[Part]:
    """The parts that are ``sentence`` (quotes, spacing, case, end punctuation folded)."""
    want = bare(sentence)
    return [p for p in parts if bare(p.text) == want]
