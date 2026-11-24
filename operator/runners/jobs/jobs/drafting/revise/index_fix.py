"""The Index of Exhibits kept true to the responses, by code.

Each index line names its file and the requests that produce it:
``1. Valley Ortho ledger.pdf (Request Nos. 2, 6)``. When a revision changes
which exhibits a production response points to, the lines follow:

* a request number comes off every line of an exhibit its response no longer
  produces;
* a request number goes onto the line of an exhibit its response now
  produces, when that exhibit has one line and the number is not on it yet.
  An exhibit of several files is left as filed and named: which of its files
  answer the request is the attorney's call (2026-11-24: tagging every line
  put a number on 96 lines where the firm's final put it on one).

Only what the revision changed moves a number: a line the filed responses
already disagreed with stays as filed (the revision did not make it so). A line
whose last number would come off is left as filed and named, never written as
"(Request Nos. )". A letter a response now produces that the index does not
list is named (``exhibit_unresolved``), never invented.

The index may be the production document's own last region, or a file of its
own (4 of 7 real requests, 2026-11-23). A file of its own pairs with the
request's ONE production document; none or two is ``index_unpaired``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from . import responses as rs

#: "Exhibit B", "Exhibits A and C", "Exhibits A, B, and D", "Exhibit A & Exhibit B".
_LETTERS = re.compile(
    r"\bExhibits?\s+([A-Z]{1,2}\b(?:\s*(?:,\s*(?:and\s+)?|\band\s+|&\s*)(?:Exhibit\s+)?[A-Z]{1,2}\b)*)"
)
_REQ = re.compile(r"\((\s*Request\s+Nos?\.?\s*)([^)]*)\)", re.I)
_NUM = re.compile(r"\d{1,3}(?:\.\d{1,2})?")
_RANGE = re.compile(r"(\d{1,3})\s*(?:-|–)\s*(\d{1,3})")
PRODUCTION = "requests_for_production"
UNPAIRED = "index_unpaired"


def letters(text: str) -> set[str]:
    """The exhibit letters a response's words point to."""
    out: set[str] = set()
    for m in _LETTERS.finditer(rs.plain(text)):
        out |= set(re.findall(r"\b[A-Z]{1,2}\b", m.group(1)))
    return out


def produced(doc: rs.Document) -> dict[str, set[str]]:
    return {r.number: letters(doc.text_of(r)) for r in doc.responses}


def _key(n: str) -> tuple[int, ...]:
    return tuple(int(x) for x in n.split("."))


def line_numbers(line: str) -> list[str] | None:
    """The request numbers a line lists, in order; None when it lists none."""
    m = _REQ.search(line)
    if not m:
        return None
    body = _RANGE.sub(lambda r: ", ".join(str(k) for k in range(int(r.group(1)), int(r.group(2)) + 1)), m.group(2))
    return list(dict.fromkeys(_NUM.findall(body)))


def _write(line: str, nums: list[str]) -> str:
    m = _REQ.search(line)
    assert m is not None
    lead = re.sub(r"(?i)nos?\.?", "Nos." if len(nums) > 1 else "No.", m.group(1), count=1)
    if not lead.endswith(" "):
        lead += " "
    return f"{line[: m.start()]}({lead}{', '.join(sorted(nums, key=_key))}){line[m.end() :]}"


@dataclass(frozen=True)
class IndexChange:
    #: Block index in the index's own document -> its new words.
    edits: dict[int, str]
    did: tuple[str, ...]
    #: (response number, reason code, what) left as filed.
    left: tuple[tuple[str, str, str], ...]


def recompute(index_doc: rs.Document, before: dict[str, set[str]], after: dict[str, set[str]]) -> IndexChange:
    """The index lines' new words for what the revision changed; ``index_doc``
    holds the index (the production document itself, or its index file)."""
    if index_doc.index is None:
        return IndexChange({}, (), ())
    edits: dict[int, str] = {}
    did: list[str] = []
    left: list[tuple[str, str, str]] = []
    have = {e.letter for e in index_doc.index.exhibits}
    for n in after:
        for x in sorted(after[n] - before.get(n, set()) - have):
            left.append((n, "exhibit_unresolved", f"Exhibit {x} is not in the index"))
    for ex in index_doc.index.exhibits:
        gone = {n for n in after if ex.letter in before.get(n, set()) and ex.letter not in after[n]}
        new = {n for n in after if ex.letter in after[n] and ex.letter not in before.get(n, set())}
        lines = [(i, rs.plain(index_doc.blocks[i])) for i in ex.lines]
        listed = [(i, line, line_numbers(line)) for i, line in lines]
        on_any = {n for _i, _l, nums in listed for n in nums or []}
        add = sorted(new - on_any, key=_key)
        numbered = [x for x in listed if x[2] is not None]
        if add and len(numbered) > 1:
            left += [(n, "index_which_files", f"Exhibit {ex.letter}") for n in add]
            add = []
        for i, _line, nums in listed:
            if nums is None:
                continue
            want = sorted([n for n in nums if n not in gone] + [n for n in add if n not in nums], key=_key)
            if want == sorted(nums, key=_key):
                continue
            if not want:
                left.append((",".join(nums), "index_line_kept", f"Exhibit {ex.letter}: {_line[:80]}"))
                continue
            edits[i] = _write(index_doc.blocks[i], want)
            did.append(f"Exhibit {ex.letter}: Request No(s). {', '.join(want)}")
    return IndexChange(edits, tuple(did), tuple(left))


def pair(units: list[dict[str, Any]], k: int) -> tuple[int | None, str]:
    """The production document an index file ``k`` belongs to: (its unit
    number, "") or (None, why). ``units``: each unit's base record."""
    prods = [j for j, b in enumerate(units) if j != k and b.get("set_type") == PRODUCTION and not b.get("index")]
    if len(prods) != 1:
        return None, f"{UNPAIRED}: the index file belongs to one production document; the request filed {len(prods)}"
    return prods[0], ""


__all__ = ["IndexChange", "PRODUCTION", "UNPAIRED", "letters", "line_numbers", "pair", "produced", "recompute"]
