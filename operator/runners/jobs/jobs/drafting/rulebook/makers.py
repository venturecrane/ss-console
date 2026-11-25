"""Code writes the firm's words into a response (design C, "code makers").

Three makers, each on the document's own blocks (``responses.read``) at the
sentences ``parts.read`` found:

* ``add``: an objection sentence goes before the transition; with no
  transition, after the last objection; with neither, before the response's
  first sentence followed by the book's transition.
* ``put``: a disposition sentence takes the place of the sentences the grid
  named (or the response's own disposition sentences), the first replaced and
  the rest removed; with none, it ends the response.
* ``cut``: the named sentences are removed; a paragraph left empty is not made.

Each returns the new blocks and "" when made, else the reason code the notes
line is written from (``stage.NOTES``). A sentence the response already holds
is ``already``: nothing changes.
"""

from __future__ import annotations

import re
from dataclasses import replace

from ..revise import responses as rs
from . import parts as parts_mod
from .book import Book
from .types import Part

_SLOT = re.compile(r"\b(?:Exhibits?\s*)?\{exhibit\}")

ALREADY = "already"
NO_TRANSITION = "no_transition"
NO_PARAGRAPH = "no_paragraph"
EMPTIES = "empties_paragraph"
EXHIBIT = rs.EXHIBIT_UNRESOLVED


def words(book: Book, key: str, item_word: str, doc: rs.Document, titles: tuple[str, ...]) -> tuple[str, str]:
    """(the entry's sentence with its slots filled, "") or ("", ``EXHIBIT``)."""
    text = book.entries[key].text.replace("{item}", item_word)
    m = _SLOT.search(text)
    if not m:
        return text, ""
    filled, why = rs.fill(doc, "Exhibit", titles)
    if why:
        return "", EXHIBIT
    filled = filled.rstrip(".")
    if not m.group(0).startswith("Exhibit"):
        filled = filled.split(" ", 1)[1]
    return text[: m.start()] + filled + text[m.end() :], ""


def _insert(blocks: list[str], block: int, at: int, text: str) -> None:
    b = blocks[block]
    blocks[block] = f"{b[:at]}{text} {b[at:]}"


def _join(left: str, right: str) -> str:
    left, right = left.rstrip(" "), right.lstrip(" ")
    return f"{left} {right}" if left and right else left + right


def _drop(blocks: list[str], p: Part) -> None:
    b = blocks[p.block]
    blocks[p.block] = _join(b[: p.at], b[p.at + len(p.text) :])


def short_clause(sentence: str, names: tuple[str, ...]) -> str:
    """``sentence`` without a short-form clause stating one of ``names`` as one
    of several grounds (", and that it is overbroad", ", and overbroad"); as
    given when it has none, or when that ground is its only one."""
    for name in sorted(names, key=len, reverse=True):
        pat = rf"(?:\s*,\s*and|\s*,|\s+and)\s+(?:that\s+)?(?:it\s+is\s+)?(?:\w+\s+){{0,2}}?{re.escape(name)}\b"
        m = re.search(pat, sentence, re.I)
        if m:
            return (sentence[: m.start()] + sentence[m.end() :]).replace(" ,", ",")
    return sentence


def _drop_short_forms(out: list[str], own: tuple[Part, ...], names: tuple[str, ...]) -> tuple[Part, ...]:
    """The existing objection sentences without the short-form clause of the
    ground the long form just added states (2026-11-25: adding the long-form
    overbroad objection left ", and that it is overbroad" in the old sentence,
    so the response said it twice). Returns the parts where they now stand."""
    for p in own:
        trimmed = short_clause(p.text, names) if p.role == "objection" else p.text
        if trimmed != p.text and p.text in out[p.block]:
            out[p.block] = out[p.block].replace(p.text, trimmed, 1)
    moved = []
    for p in own:
        text = short_clause(p.text, names) if p.role == "objection" else p.text
        at = out[p.block].find(text) if 0 <= p.block < len(out) else -1
        moved.append(replace(p, text=text, at=at if at >= 0 else p.at))
    return tuple(moved)


def add(
    blocks: list[str], own: tuple[Part, ...], sentence: str, book: Book, names: tuple[str, ...] = ()
) -> tuple[list[str], str]:
    """``names``: the short names of the ground ``sentence`` states, whose
    short-form clause leaves the response's other objections."""
    if parts_mod.holds(own, sentence):
        return blocks, ALREADY
    if not own:
        return blocks, NO_PARAGRAPH
    out = list(blocks)
    if names:
        own = _drop_short_forms(out, own, names)
    trans = [p for p in own if p.role == "transition"]
    objs = [p for p in own if p.role == "objection"]
    if trans:
        _insert(out, trans[0].block, trans[0].at, sentence)
    elif objs:
        last = objs[-1]
        end = last.at + len(last.text)
        b = out[last.block]
        out[last.block] = f"{b[:end]} {sentence}{b[end:]}"
    else:
        link = book.of_kind("transition")
        if not link:
            return blocks, NO_TRANSITION
        _insert(out, own[0].block, own[0].at, f"{sentence} {link[0].text}")
    return out, ""


#: A placeholder left for the attorney in a filed answer: "[ATTORNEY TO CONFIRM]" or "{{ATTORNEY: ...}}".
_ATTORNEY_MARK = re.compile(r"(?:\[|\{\{)\s*ATTORNEY\b")


def put(blocks: list[str], own: tuple[Part, ...], sentence: str, sentences: tuple[str, ...]) -> tuple[list[str], str]:
    if parts_mod.holds(own, sentence):
        return blocks, ALREADY
    if not own:
        return blocks, NO_PARAGRAPH
    out = list(blocks)
    old = (
        [h for s in sentences for h in parts_mod.holds(own, s)]
        or [p for p in own if p.role == "disposition"]
        # The answer is still a placeholder for the attorney: the firm's phrase is the answer.
        or [p for p in own if p.role == "other" and _ATTORNEY_MARK.search(p.text)]
    )
    if not old:
        last = own[-1]
        end = last.at + len(last.text)
        b = out[last.block]
        out[last.block] = f"{b[:end]} {sentence}{b[end:]}"
        return out, ""
    old = sorted(set(old), key=lambda p: (p.block, p.at))
    for p in reversed(old[1:]):
        _drop(out, p)
    first = old[0]
    b = out[first.block]
    out[first.block] = b[: first.at] + sentence + b[first.at + len(first.text) :]
    return out, ""


def cut(blocks: list[str], own: tuple[Part, ...], sentences: tuple[str, ...]) -> tuple[list[str], str]:
    out = list(blocks)
    gone = sorted({h for s in sentences for h in parts_mod.holds(own, s)}, key=lambda p: (p.block, p.at))
    for p in reversed(gone):
        _drop(out, p)
    if any(not out[p.block].strip() for p in gone):
        return blocks, EMPTIES
    return out, ""
