"""Which item each judged clause applies to: one model call per document, values
only (design C, "Applicability grid").

Per item the model returns one ``Y``/``N`` mark per judged clause, in order, and
for each ``Y`` a cell: ``state`` (``apply``, ``complies``, ``conflict``), the
book keys it applies (one of the clause's own keys), the grounds with no
sentence in the book (one of the clause's own grounds), the response's own
sentences it replaces or removes, copied, and the exhibits by title, never a
letter. Code refuses an answer with an item missing, repeated or unknown, a
marks string of the wrong length, a ``Y`` with no cell, a key or ground the
clause does not carry, a sentence that is not one of that response's own, or a
letter for an exhibit; asked once more, then :class:`GridRefused`.

Clauses decided by code (``when`` ``always`` or ``numbers``) are never sent.
Answers are cached by the sha of what was asked, so a re-render asks nothing.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..revise import responses as rs
from ..verify.items import parse_json
from . import parts as parts_mod
from .book import Book
from .reading import save
from .types import Ask, Cell, Clause, Part

STAGE = "rulebook-grid"
MAX_TOKENS = 12_000
STATES = ("apply", "complies", "conflict")
_LETTER = re.compile(r"^(?:exhibits?\s+)?[A-Z]{1,2}$", re.I)

SYSTEM = (
    "You read a law firm's filed discovery responses and decide, for each response, whether each of the firm's "
    "rule clauses applies to it. You never write wording: code writes the firm's sentences. Return ONLY a JSON "
    'object {"items": [{"item": <the response number as headed>, "marks": <one Y or N per clause, in the '
    'clauses\' order>, "cells": {<clause number>: {"state": "apply" | "complies" | "conflict", '
    '"keys": [<the clause\'s phrase book keys that fit>], "grounds": [<the clause\'s grounds with no book key '
    "that fit>], \"sentences\": [<the response's own sentences the firm's words replace or remove, copied "
    "character for character>], \"exhibits\": [<when a key's words name an exhibit: each exhibit's title as the "
    "document's index lists it, never a letter>]}}}]}. Every response gets an entry; a cell for every Y. "
    '"complies": the response already does what the clause asks. "conflict": the clause would contradict '
    "what the response says."
)


def judged(clauses: list[Clause]) -> list[Clause]:
    return [c for c in clauses if c.when == "judged"]


def user(clauses: list[Clause], doc: rs.Document, book: Book, problems: list[str] | None = None) -> str:
    lines = ["THE RULE CLAUSES:"]
    for n, c in enumerate(clauses, 1):
        lines.append(f"\n[{n}] ({c.act}) {c.quote}")
        lines += [f"  key {k}: {book.entries[k].text}" for k in c.keys if k in book.entries]
        lines += [f"  ground with no book sentence: {g}" for g in c.grounds if not _keyed(c, g, book)]
    lines.append("\nTHE DOCUMENT'S INDEX OF EXHIBITS (titles):")
    lines += [f"- {t}" for t in doc.exhibit_titles()] or ["(none)"]
    lines.append("\nTHE RESPONSES:")
    for r in doc.responses:
        lines.append(
            f"\n=== REQUEST {r.number} ===\n{doc.request_of(r)}\n--- RESPONSE {r.number} ---\n{doc.text_of(r)}"
        )
    if problems:
        lines.append("\nYOUR LAST ANSWER WAS REFUSED BY CODE FOR:\n" + "\n".join(f"- {p}" for p in problems))
    return "\n".join(lines) + "\n"


def _keyed(c: Clause, ground: str, book: Book) -> bool:
    """A ground one of the clause's keys already carries."""
    return any(ground in {n.casefold() for n in book.entries[k].names} for k in c.keys if k in book.entries)


def _strs(raw: Any) -> tuple[str, ...] | None:
    if raw is None:
        return ()
    if isinstance(raw, list) and all(isinstance(x, str) and x.strip() for x in raw):
        return tuple(" ".join(x.split()) for x in raw)
    return None


def _cell(item: str, c: Clause, raw: Any, own: tuple[Part, ...], book: Book) -> tuple[Cell | None, str]:
    if not isinstance(raw, dict) or raw.get("state") not in STATES:
        return None, f"state is one of {list(STATES)}"
    if raw["state"] != "apply":
        return Cell(item, c.id, raw["state"]), ""
    keys, grounds = _strs(raw.get("keys")), _strs(raw.get("grounds"))
    sents, exhibits = _strs(raw.get("sentences")), _strs(raw.get("exhibits"))
    if keys is None or grounds is None or sents is None or exhibits is None:
        return None, "keys, grounds, sentences and exhibits are lists of text"
    grounds = tuple(g.casefold() for g in grounds)
    bad = [k for k in keys if k not in c.keys] + [g for g in grounds if g not in c.grounds or _keyed(c, g, book)]
    if bad:
        return None, f"{bad[0]!r} is not one of the clause's keys or unkeyed grounds"
    if c.act == "add_objection" and not (keys or grounds):
        return None, "an objection clause applied names a key or a ground"
    if c.act == "use_phrase" and len(keys) != 1:
        return None, "a phrase clause applied names exactly one key"
    if c.act == "remove_part" and not sents:
        return None, "a removal names the response's own sentences to remove"
    missing = [s for s in sents if len(parts_mod.holds(own, s)) != 1]
    if missing:
        return None, f"the sentence {missing[0][:60]!r} is not one of the response's own, exactly once"
    if any(_LETTER.match(t) for t in exhibits):
        return None, "name each exhibit by its title as the index lists it, never by its letter"
    return Cell(item, c.id, "apply", keys, grounds, sents, exhibits), ""


def _row(row: Any, clauses: list[Clause], own: dict[str, tuple[Part, ...]], book: Book) -> tuple[list[Cell], list[str]]:
    item = str(row.get("item") or "").strip()
    marks, cells = row.get("marks"), row.get("cells") or {}
    if not isinstance(marks, str) or len(marks) != len(clauses) or set(marks) - {"Y", "N"}:
        return [], [f"item {item}: marks is {len(clauses)} characters, each Y or N"]
    if not isinstance(cells, dict):
        return [], [f"item {item}: cells is an object"]
    out: list[Cell] = []
    problems: list[str] = []
    for n, (m, c) in enumerate(zip(marks, clauses), 1):
        if m != "Y":
            continue
        if c.numbers and item not in c.numbers:
            problems.append(f"item {item} clause {n}: the clause is for response(s) {', '.join(c.numbers)} only")
            continue
        cell, bad = _cell(item, c, cells.get(str(n)), own[item], book)
        if bad:
            problems.append(f"item {item} clause {n}: {bad}")
        elif cell:
            out.append(cell)
    return out, problems


def check(
    raw: Any, clauses: list[Clause], own: dict[str, tuple[Part, ...]], book: Book
) -> tuple[list[Cell], list[str]]:
    """(the cells of every ``Y``, problems): nothing kept on a guess."""
    rows = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return [], ['the answer is an object {"items": [...]}']
    seen: list[str] = []
    cells: list[Cell] = []
    problems: list[str] = []
    for row in rows:
        item = str(row.get("item") or "").strip() if isinstance(row, dict) else ""
        if item not in own or item in seen:
            problems.append(f"item {item!r} is not one of the document's responses, or is listed twice")
            continue
        seen.append(item)
        got, bad = _row(row, clauses, own, book)
        cells += got
        problems += bad
    problems += [f"item {n}: missing from the answer" for n in own if n not in seen]
    return cells, problems


class GridRefused(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems)[:480])
        self.problems = problems


def _from_dict(d: dict[str, Any]) -> Cell:
    lists = ("keys", "grounds", "sentences", "exhibits")
    return Cell(str(d["item"]), str(d["clause"]), str(d["state"]), *(tuple(d.get(k) or ()) for k in lists))


def _cell_dict(c: Cell) -> dict[str, Any]:
    return {k: list(v) if isinstance(v, tuple) else v for k, v in vars(c).items()}


def ask(
    cache: Path, clauses: list[Clause], doc: rs.Document, own: dict[str, tuple[Part, ...]], book: Book, call: Ask | None
) -> list[Cell]:
    """The cells for one document; ``GridRefused`` after the one retry."""
    if not clauses:
        return []
    asked = user(clauses, doc, book)
    sha = hashlib.sha256(asked.encode("utf-8")).hexdigest()
    p = cache / f"grid-{sha[:16]}.json"
    if p.is_file():
        return [_from_dict(d) for d in json.loads(p.read_text(encoding="utf-8"))]
    if call is None:
        raise GridRefused(["the document is unchecked and no model call is available"])
    problems: list[str] | None = None
    for _ in range(2):
        cells, problems = check(
            parse_json(call(STAGE, SYSTEM, user(clauses, doc, book, problems), MAX_TOKENS)), clauses, own, book
        )
        if not problems:
            save(p, json.dumps([_cell_dict(c) for c in cells], indent=1))
            return cells
    raise GridRefused(problems or ["no answer"])


def plan_chars(clauses: list[Clause], doc: rs.Document, book: Book) -> int:
    """One document's calls, priced before any: its read, the retry, the answer."""
    return 2 * (len(SYSTEM) + len(user(clauses, doc, book)) + 5 * 80 * len(doc.responses) * max(1, len(clauses)))
