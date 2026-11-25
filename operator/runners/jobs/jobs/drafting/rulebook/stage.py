"""The firm-rules stage: read the rules into clauses, decide each item, write
the firm's words by code, check again (design C).

``run`` for a response document, drafted (``first_draft``, at the end of the
gate) or filed (``revise``), the same steps:

1. the job's frozen rules (``firm_wording.FILE``); none, the document as given;
2. the rule read (``reading.read``: one call per job, cached), the book grown
   by the rules' own words;
3. the clauses for this document: standing, of its type, an act code makes;
   plus the request's own instructions (``StageDoc.instructions``) for their
   numbers only. A one-matter clause is never applied here;
4. the grid (``grid.ask``: one call per document, cached) for judged clauses;
   ``always`` clauses decided by code for every item;
5. the makers (``makers.py``), one cell at a time on the document's own
   blocks, a ground with no book sentence written for the request
   (``written.py``) or left;
6. the check again: every sentence made is in its response, every sentence
   removed is gone.

Holds (``endings.py``): a refused rule read or grid is ``rule_unchecked``; a
``conflict`` on a clause, or a made sentence the re-check cannot find, is
``rule_unmet``. A ground with no sentence, an exhibit the index cannot name,
and the rest of the makers' reasons are LEFT: the response stays as filed and
the notes name it, from :data:`NOTES`, never model text.

A propounded set is checked, never written (``sets_check``): count and topic
clauses return findings for the set lane's repair, the markdown as given.
An amended response is a response document like any other. The result is kept at ``<data>/rulebook/<doc key>.json`` for the lane's notes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from ... import firm_wording
from ..endings import FIRM_MESSAGES
from ..outcome import DraftingFailed
from ..revise import responses as rs
from . import book as book_mod, grid, makers, parts as parts_mod, reading, sets_check, written
from .book import Book
from .types import (
    ITEM_WORD,
    MADE,
    MODES,
    SET_DOC,
    Applied,
    Cell,
    Clause,
    Fresh,
    Left,
    Part,
    StageDoc,
    StageJob,
    StageResult,
)

DIR = "rulebook"
WRITTEN = "written"
#: The notes line for each outcome, by code. ``{item}`` is the response number,
#: ``{by}`` where the clause came from (:func:`by`).
NOTES: dict[str, str] = {
    "add_objection": "Response {item}: the firm's objection added, {by}.",
    "use_phrase": "Response {item}: the firm's wording used, {by}.",
    "remove_part": "Response {item}: a sentence removed, {by}.",
    WRITTEN: (
        "Response {item}: an objection was written for this request, {by}; the firm's standard wording has no "
        "sentence for it."
    ),
    "phrase_missing": (
        "Response {item}: left as drafted; the firm's standard wording has no sentence for this objection, and none "
        "written for this request passed the firm's checks."
    ),
    makers.EXHIBIT: "Response " + FIRM_MESSAGES["exhibit_unresolved"],
    makers.NO_TRANSITION: (
        "Response {item}: not changed ({by}); the response has no transition and the firm's standard wording has none."
    ),
    makers.NO_PARAGRAPH: "Response {item}: not changed ({by}); the response has no sentence to place it by.",
    makers.EMPTIES: "Response {item}: not changed ({by}); removing the sentence would leave a paragraph empty.",
}
#: Where a clause that is not a recorded rule came from (``Instruction.source``).
ASKED_BY = {"email": "as the request asks", "letter": "as the other side's letter asks"}


def source(c: Clause) -> str:
    """``rule:<id>``, ``email`` or ``letter``: what an Applied or Left names."""
    return c.rule if c.rule in ASKED_BY or c.rule.startswith("rule:") else f"rule:{c.rule}"


def by(c: Clause) -> str:
    return ASKED_BY.get(c.rule) or f"by rule {c.rule.removeprefix('rule:')}"


def frozen(data: Path) -> list[dict[str, Any]]:
    p = Path(data) / firm_wording.FILE
    return list(json.loads(p.read_text(encoding="utf-8")).get("rules") or []) if p.is_file() else []


#: An instruction's act (``revise/instructions.ACTS``) as the clause act code makes.
INSTRUCTION_ACTS = {
    "add_objection": "add_objection",
    "use_phrase": "use_phrase",
    "remove_objection": "remove_part",
    "remove_sentence": "remove_part",
}


def _instructions(doc: StageDoc, dtype: str, book: Book) -> list[Clause]:
    """The request's own instructions for this document, as clauses for their
    numbers only. An instruction for another document type, with no number, or
    of an act code does not make here (``add_fact``) is not one."""
    out = []
    for n, ins in enumerate(doc.instructions, 1):
        act = INSTRUCTION_ACTS.get(ins.act, "")
        kind = SET_DOC.get(ins.doc_type, ins.doc_type)
        if not act or (kind and kind != dtype) or not ins.numbers:
            continue
        keys = tuple(k for k in ins.keys if k in book.entries)
        numbers = tuple(str(x) for x in ins.numbers)
        grounds = ins.grounds if act == "add_objection" else ()
        out.append(Clause(ins.source, n, ins.quote, True, (dtype,), act, keys, grounds, "numbers", numbers))
    return out


def _decided(c: Clause, book: Book) -> bool:
    """A clause code makes with no judgment: ``always`` or ``numbers``, and an
    objection or a removal by book key, or one phrase that names no exhibit."""
    if c.when == "judged":
        return False
    if c.act == "use_phrase":
        return len(c.keys) == 1 and "{exhibit}" not in book.entries[c.keys[0]].text
    if c.act == "remove_part":
        return bool(c.keys)
    return bool(c.keys or c.grounds)


def _makeable(c: Clause) -> bool:
    """A clause code has something to write or remove for: an objection with a
    key or a ground, a phrase with a key; a removal names its sentences in the grid."""
    return c.act == "remove_part" or bool(c.keys if c.act == "use_phrase" else c.keys or c.grounds)


def _unkeyed(c: Clause, book: Book) -> tuple[str, ...]:
    named = {n.casefold() for k in c.keys if k in book.entries for n in book.entries[k].names}
    return tuple(g for g in c.grounds if g not in named)


def _code_cells(clauses: list[Clause], items: list[str], book: Book) -> list[Cell]:
    return [
        Cell(i, c.id, "apply", c.keys, _unkeyed(c, book))
        for c in clauses
        for i in items
        if not c.numbers or i in c.numbers
    ]


class _Doc:
    """The document being made: its reader's structure over blocks that change."""

    def __init__(self, md: str, hint: str | None, book: Book) -> None:
        self.base = rs.read(md, hint)
        self.blocks = list(self.base.blocks)
        self.book = book

    @property
    def doc(self) -> rs.Document:
        return rs.Document(self.blocks, self.base.responses, self.base.index, self.base.notes)

    def own(self, item: str) -> tuple[Part, ...]:
        d = self.doc
        return parts_mod.item_parts(d, d.by_number()[item], self.book)


def _needs(cells: list[Cell], by_id: dict[str, Clause], d: _Doc, rules: dict[str, str]) -> list[written.Need]:
    out = []
    for cell in cells:
        c = by_id[cell.clause]
        r = d.base.by_number()[cell.item]
        out += [
            written.Need(cell.item, g, c.rule, d.base.request_of(r), rules.get(c.rule, c.quote)) for g in cell.grounds
        ]
    return out


def _written(job: StageJob, key: str, needs: list[written.Need], word: str, book: Book) -> dict[tuple[str, str], str]:
    if not needs:
        return {}
    sha = hashlib.sha256(json.dumps([vars(n) for n in needs], sort_keys=True).encode()).hexdigest()[:16]
    p = Path(job.data) / DIR / f"{key}-written-{sha}.json"
    if p.is_file():
        return written.from_json(p.read_text(encoding="utf-8"))
    got = written.write(needs, word, book, job.ask)
    reading.save(p, written.to_json(got))
    return got


class _Make:
    def __init__(self, d: _Doc, word: str, wrote: dict[tuple[str, str], str]) -> None:
        self.d, self.word, self.wrote = d, word, wrote
        self.applied: list[Applied] = []
        self.left: list[Left] = []
        self.notes: list[str] = []
        self.expect: list[tuple[str, str, bool]] = []

    def _done(self, c: Clause, item: str, key: str, how: str, sentence: str, present: bool) -> None:
        self.applied.append(Applied(source(c), item, key))
        self.notes.append(NOTES[how].format(item=item, by=by(c)))
        self.expect.append((item, sentence, present))

    def _leave(self, c: Clause, item: str, reason: str) -> None:
        self.left.append(Left(source(c), item, reason))
        self.notes.append(NOTES[reason].format(item=item, by=by(c)))

    def _land(self, c: Clause, cell: Cell, key: str, got: tuple[list[str], str], sentence: str, how: str) -> None:
        blocks, why = got
        if why == makers.ALREADY:
            return
        if why:
            self._leave(c, cell.item, why)
            return
        self.d.blocks = blocks
        self._done(c, cell.item, key, how, sentence, True)

    def cell(self, c: Clause, cell: Cell) -> None:
        book, d = self.d.book, self.d
        if c.act == "remove_part":
            own = d.own(cell.item)
            gone = cell.sentences or tuple(p.text for p in own if p.key and p.key in cell.keys)
            if not gone:
                return  # nothing of what it removes is in the response
            blocks, why = makers.cut(d.blocks, own, gone)
            if why:
                self._leave(c, cell.item, why)
                return
            d.blocks = blocks
            for s in gone:
                self._done(c, cell.item, "", "remove_part", s, False)
            return
        for key in cell.keys:
            sentence, why = makers.words(book, key, self.word, d.doc, cell.exhibits)
            if why:
                self._leave(c, cell.item, why)
                continue
            if c.act == "use_phrase":
                got = makers.put(d.blocks, d.own(cell.item), sentence, cell.sentences)
            else:
                got = makers.add(d.blocks, d.own(cell.item), sentence, book, book.entries[key].names)
            self._land(c, cell, key, got, sentence, c.act)
        for g in cell.grounds:
            sentence = self.wrote.get((cell.item, g), "")
            if not sentence:
                self._leave(c, cell.item, "phrase_missing")
                continue
            self._land(c, cell, "", makers.add(d.blocks, d.own(cell.item), sentence, book), sentence, WRITTEN)

    def recheck(self) -> list[str]:
        """What the made document does not hold that the makers wrote, or still holds that they removed."""
        out = []
        for item, sentence, present in self.expect:
            if bool(parts_mod.holds(self.d.own(item), sentence)) != present:
                out.append(f"response {item}: {'missing' if present else 'still holds'} {sentence[:80]!r}")
        return out


def _clauses(job: StageJob, doc: StageDoc, rules: list[dict[str, Any]], dtype: str) -> tuple[list[Clause], Book]:
    clauses, book = _read_rules(job, rules)
    mine = [c for c in clauses if c.standing and c.act in MADE and dtype in c.doc_types and _makeable(c)]
    return mine + _instructions(doc, dtype, book), book


def _cells(job: StageJob, doc: StageDoc, d: _Doc, clauses: list[Clause]) -> list[Cell]:
    items = d.base.numbers
    decided = [c for c in clauses if _decided(c, d.book)]
    judged = [c for c in clauses if c not in decided]
    own = {i: d.own(i) for i in items}
    try:
        cells = grid.ask(Path(job.data) / DIR, judged, d.base, own, d.book, job.ask)
    except grid.GridRefused as exc:
        raise DraftingFailed(f"rule_unchecked: the rule grid for document {doc.key} was refused: {exc}"[:480]) from None
    clash = [c for c in cells if c.state == "conflict"]
    if clash:
        raise DraftingFailed(
            f"rule_unmet: document {doc.key}: response {clash[0].item} would contradict clause {clash[0].clause}"
        )
    order = {c.id: n for n, c in enumerate(clauses)}
    out = _code_cells(decided, items, d.book) + [c for c in cells if c.state == "apply"]
    return sorted(out, key=lambda c: (items.index(c.item), order[c.clause]))


def _keep(job: StageJob, doc: StageDoc, res: StageResult) -> None:
    p = Path(job.data) / DIR / f"{doc.key or 'doc'}.json"
    body = {
        "applied": [vars(a) for a in res.applied],
        "left": [vars(x) for x in res.left],
        "findings": [vars(f) for f in res.findings],
        "notes": list(res.notes),
    }
    reading.save(p, json.dumps(body, indent=1))


def _read_rules(job: StageJob, rules: list[dict[str, Any]]) -> tuple[list[Clause], Book]:
    try:
        return reading.read(job.data, rules, book_mod.load(job.firm) or Book(), job.ask)
    except reading.ReadRefused as exc:
        raise DraftingFailed(f"rule_unchecked: the firm's rules could not be read into clauses: {exc}"[:480]) from None


def run(job: StageJob, doc: StageDoc) -> StageResult:
    """One document through the stage, a first draft or a revision alike.
    Unchanged with no frozen rule and no instruction, or for a document that
    reads as neither numbered responses nor a propounded set. A propounded set
    is checked only: its findings go back to the set lane (``sets_check``)."""
    rules = frozen(job.data)
    if job.mode not in MODES or not (rules or doc.instructions):
        return StageResult(md=doc.md)
    try:
        base = rs.read(doc.md, doc.hint)
    except rs.ResponsesUnread:
        base = None
    if base is None or not base.responses:
        if not rules or not sets_check.requests(doc.md)[1]:
            return StageResult(md=doc.md)
        res = sets_check.run(job.data, doc.md, _read_rules(job, rules)[0], job.ask)
        _keep(job, doc, res)
        return res
    return _responses(job, doc, rules, base)


def _responses(job: StageJob, doc: StageDoc, rules: list[dict[str, Any]], base: rs.Document) -> StageResult:
    """Responses, drafted or filed: read, grid, make, check again."""
    dtype = SET_DOC.get(base.set_type, "")
    if not dtype:
        return StageResult(md=doc.md)
    clauses, book = _clauses(job, doc, rules, dtype)
    if not clauses:
        return StageResult(md=doc.md)
    d = _Doc(doc.md, doc.hint, book)
    cells = _cells(job, doc, d, clauses)
    by_id = {c.id: c for c in clauses}
    texts = {str(r["rule_id"]): reading.rule_text(r) for r in rules}
    wrote = _written(job, doc.key or "doc", _needs(cells, by_id, d, texts), ITEM_WORD[dtype], book)
    make = _Make(d, ITEM_WORD[dtype], wrote)
    for cell in cells:
        make.cell(by_id[cell.clause], cell)
    off = make.recheck()
    if off:
        raise DraftingFailed(f"rule_unmet: document {doc.key}: {'; '.join(off)}"[:480])
    res = StageResult(d.doc.md, tuple(make.applied), tuple(make.left), (), tuple(make.notes))
    _keep(job, doc, res)
    return res


def plan_chars(job: StageJob, docs: Iterable[StageDoc]) -> int:
    """The characters the stage would send for these documents, for the
    estimate before spend: the rule read when uncached, and per document its
    grid with the retry. A rule not yet read counts as one clause."""
    rules = frozen(job.data) if job.mode in MODES else []
    if not rules:
        return 0
    book = book_mod.load(job.firm) or Book()
    total = reading.plan_chars(rules, book, Path(job.data))
    for doc in docs:
        try:
            base = rs.read(doc.md, doc.hint)
        except rs.ResponsesUnread:
            continue
        stand_in = [Clause(str(r["rule_id"]), 1, reading.rule_text(r), True, (), "add_objection") for r in rules]
        total += grid.plan_chars(stand_in, base, book)
    return total


def fresh(data: Path, cls: str, facts: dict[str, Any]) -> Fresh:
    """The rule ids that moved since the job froze its rules; none yet (no
    mid-job refreeze, the approved plan)."""
    del data, cls, facts
    return Fresh()
