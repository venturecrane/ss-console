"""Marked places read from a paragraph's whole text, so a save in Word cannot hide one.

``base.slot_names`` reads a marked place, ``[[name]]``, only when it sits in ONE
text run. Word splits a paragraph's runs when it saves (a spelling mark, an
edit to part of the line, a revision id), so a firm that opens its library file
and saves it can leave ``[[`` + ``name`` + ``]]`` in three runs with a proofing
mark between, and the single-run reader then finds no marked place at all
(design 2026-11-06, piece 4).

:func:`slot_names` here reads each paragraph's own text with its runs joined.
For a paragraph the single-run reader already reads, it returns exactly what
that reader returns, so a file that has its markers in one run reads the same.
For a paragraph it does not, the paragraph's whole joined text is matched
against the same marker pattern.

Nothing calls this yet: filling a split marker needs its runs merged first,
which is the piece that wires it. ``tests/test_firmbase_markers.py`` proves it
on run-split fixtures built in code; fixtures saved by Word itself are still
owed (a person with Word saves a marked file and commits it).
"""

from __future__ import annotations

from typing import Any

from .base import SLOT, W

_P = f"{W}p"
_T = f"{W}t"


def _nearest_paragraph(el: Any) -> Any:
    node = el.getparent()
    while node is not None and node.tag != _P:
        node = node.getparent()
    return node


def own_texts(paragraph: Any) -> list[Any]:
    """The paragraph's own text elements, in order: not those of a paragraph
    nested inside it (a text box)."""
    return [t for t in paragraph.iter(_T) if _nearest_paragraph(t) is paragraph]


def paragraph_text(paragraph: Any) -> str:
    """The paragraph's own text with its runs joined."""
    return "".join(t.text or "" for t in own_texts(paragraph))


def _paragraph_names(paragraph: Any) -> list[str]:
    texts = own_texts(paragraph)
    single = [m.group(1) for t in texts for m in [SLOT.match(t.text or "")] if m]
    if single:
        return single
    whole = SLOT.match("".join(t.text or "" for t in texts))
    return [whole.group(1)] if whole else []


def slot_names(el: Any) -> list[str]:
    """The marked places an element's paragraphs hold, in order, read from
    each paragraph's whole text."""
    paragraphs = [el] if el.tag == _P else []
    paragraphs += [p for p in el.iter(_P) if p is not el]
    return [name for p in paragraphs for name in _paragraph_names(p)]


__all__ = ["own_texts", "paragraph_text", "slot_names"]
