"""The closed set of generic detectors the reader chooses from per document.

A TITLE detector says whether a block is marked as a section title (a heading
style, a bold-only line, a caps line, or any of them); the block's words then
say which title (``vocab.TITLES``). An ITEM detector finds where each numbered
item starts and its number: the label word and number in the text, the label
in Word's own numbering, or Word's decimal numbering alone (the number not in
the text). No detector knows any firm: the reader keeps the first that reads
the document with every invariant holding, and a profile may pin one.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from . import vocab
from .model import Block

_TITLE_MAX_WORDS = 12


def _short(b: Block) -> bool:
    return not b.in_table and not b.in_textbox and len(b.text.split()) <= _TITLE_MAX_WORDS


TITLE: dict[str, Callable[[Block], bool]] = {
    "heading": lambda b: _short(b) and b.heading > 0,
    "bold": lambda b: _short(b) and b.bold,
    "caps": lambda b: _short(b) and b.caps,
    "any": lambda b: _short(b) and (b.heading > 0 or b.bold or b.caps),
}


@dataclass(frozen=True)
class Head:
    """Where an item starts."""

    index: int
    number: int
    label: str


def _by_label(blocks: list[Block]) -> list[Head]:
    out = []
    for i, b in enumerate(blocks):
        m = None if b.in_table or b.in_textbox else vocab.LABEL.match(b.text)
        if m:
            out.append(Head(i, int(m.group(2)), vocab.label_word(m.group(1))))
    return out


def _by_numbered_label(blocks: list[Block]) -> list[Head]:
    out = []
    for i, b in enumerate(blocks):
        m = vocab.LABEL.match(b.numbered or "")
        if m and not vocab.RESPONSE.match(b.text):
            out.append(Head(i, int(m.group(2)), vocab.label_word(m.group(1))))
    return out


_DECIMAL = re.compile(r"\(?(\d+)[.):]?")


def _by_number(blocks: list[Block], after: int) -> list[Head]:
    """Word's decimal numbering alone: the list (numId and level) with the most
    paragraphs after ``after`` (the last front title), its paragraphs the items."""
    groups: Counter[tuple[str, int]] = Counter()
    for b in blocks[after + 1 :]:
        if b.num_id and _DECIMAL.fullmatch(b.numbered or ""):
            groups[(b.num_id, b.num_level or 0)] += 1
    if not groups:
        return []
    best = max(groups.items(), key=lambda kv: (kv[1], -kv[0][1]))[0]
    out = []
    for i, b in enumerate(blocks):
        m = _DECIMAL.fullmatch(b.numbered or "")
        if i > after and m and (b.num_id, b.num_level or 0) == best:
            out.append(Head(i, int(m.group(1)), ""))
    return out


ITEMS = ("label", "numbered_label", "numbered")


def heads(name: str, blocks: list[Block], after: int) -> list[Head]:
    if name == "label":
        return _by_label(blocks)
    if name == "numbered_label":
        return _by_numbered_label(blocks)
    return _by_number(blocks, after)


def titles(name: str, blocks: list[Block]) -> dict[int, str]:
    """Index to region for each block the detector marks as a known title."""
    test = TITLE[name]
    out = {}
    for i, b in enumerate(blocks):
        region = vocab.title_region(b.text) if test(b) else None
        if region:
            out[i] = region
    return out
