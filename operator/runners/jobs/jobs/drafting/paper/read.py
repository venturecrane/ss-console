"""Blocks to a ``Paper``, or ``Unread`` (design.md section 2).

The reader tries its generic detectors (``detect.TITLE`` x ``detect.ITEMS``)
and keeps the first pair that reads the document with every invariant
holding; a profile may pin either. The invariants are the fail-closed test:

* every body block is assigned to exactly one region;
* items are numbered with no gap and no repeat, from 1 (a later set, by its
  "SET NO." line, may start where the last set ended);
* a response heading under an item names that item's number;
* every count the declaration states equals the item count;
* every declaration list names only numbers the set has.

When no pair holds, the Unread returned is the one from the pair that got
furthest (items found, then regions assigned, then numbering, then the
declaration), since that is the one that names the paper's real problem.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import decl, detect, terms, vocab
from .model import Block, Declaration, Item, Paper, Region, Unread

#: How far a read got: an Unread from a later stage names the real problem.
_FOUND, _ASSIGNED, _NUMBERED, _DECLARED = 1, 2, 3, 4
#: A response block that objects: "Responding Party objects ..." or one opening "Objection(s)".
_OBJECTS = re.compile(r"(?i)\bobjects?\b|^\W*objections?\b")
_CITE = re.compile(r"(?i)\b(?:bates|exhibit|ex\.)\s*(?:no\.?\s*)?[\w-]*\d")


@dataclass
class _Failed(Exception):
    stage: int
    unread: Unread


def read(blocks: list[Block], profile: dict[str, str] | None = None) -> Paper | Unread:
    """The paper these blocks are, or why they could not be read."""
    pins = profile or {}
    title_names = [pins["title"]] if "title" in pins else _by_titles_found(blocks)
    item_names = [pins["items"]] if "items" in pins else list(detect.ITEMS)
    best: _Failed | None = None
    for t in title_names:
        for it in item_names:
            try:
                return _read_with(blocks, t, it)
            except _Failed as f:
                if best is None or f.stage > best.stage:
                    best = f
    if best is not None and best.stage == _FOUND:
        alone = _index_alone(blocks)
        if alone is not None:
            return alone
    return best.unread if best else Unread("items", None, "no detector to try")


def _index_alone(blocks: list[Block]) -> Paper | None:
    """An index of exhibits filed as its own document: no items, an index
    title (bold or not); what precedes the title is its caption."""
    at = next((i for i, b in enumerate(blocks) if detect.plain_title(b) == "index"), None)
    if at is None:
        return None
    empty = {name: Region(name) for name in ("title", "ident", "front", "definitions", "instructions")}
    return Paper(
        type="index_of_exhibits",
        caption=Region("caption", [b.id for b in blocks[:at]]),
        title=empty["title"],
        ident=empty["ident"],
        front=empty["front"],
        definitions_region=empty["definitions"],
        definitions=[],
        instructions=empty["instructions"],
        items=[],
        closing=Region("closing"),
        declaration=None,
        verification=Region("verification"),
        index=Region("index", [b.id for b in blocks[at:]]),
        service=Region("service"),
        notes=Region("notes"),
        blocks=blocks,
        detectors={"title": "plain", "items": "none"},
    )


def _by_titles_found(blocks: list[Block]) -> list[str]:
    """Title detectors, the one that marks the most known titles first (a
    detector that misses a title breaks no invariant, so it must not win by
    order); on a tie the narrower detector, in ``detect.TITLE`` order."""
    names = list(detect.TITLE)
    return sorted(names, key=lambda n: -len(detect.titles(n, blocks)))


def _fail(stage: int, region: str, block: Block | None, why: str) -> _Failed:
    return _Failed(stage, Unread(region, None if block is None else block.id, why))


def _read_with(blocks: list[Block], title_det: str, item_det: str) -> Paper:
    titles = detect.titles(title_det, blocks)
    titles.update({i: "notes" for i, b in enumerate(blocks) if detect.plain_title(b) == "notes"})
    front_titles = [i for i, r in titles.items() if r in vocab.FRONT]
    found = detect.heads(item_det, blocks, max(front_titles, default=-1))
    if not found:
        raise _fail(_FOUND, "items", None, f"no items found by the {item_det} detector")
    first = found[0].index
    attach = sorted(i for i, r in titles.items() if r in vocab.ATTACHMENTS and i > first)
    zone_end = attach[0] if attach else len(blocks)
    found = [h for h in found if h.index < zone_end]
    caption, title, ident, header_end = _header(blocks, first, titles)
    fronts = _front(blocks, header_end + 1, first, titles)
    close_at = _closing_start(blocks, found[-1].index + 1, zone_end)
    closing = _closing(blocks, close_at, zone_end)
    items = [_item(blocks, h, (found[k + 1].index if k + 1 < len(found) else close_at)) for k, h in enumerate(found)]
    atts = _attachments(blocks, attach, titles)
    defs_blocks = [blocks[i] for i in _ids_to_idx(blocks, fronts["definitions"].ids)]
    definitions, _ = terms.read([b for b in defs_blocks if b.id not in _title_ids(blocks, titles)])
    declaration = decl.read(atts["declaration"]) if atts["declaration"] else None
    paper = Paper(
        type=_type(blocks, title, items),
        caption=caption,
        title=title,
        ident=ident,
        front=fronts["front"],
        definitions_region=fronts["definitions"],
        definitions=definitions,
        instructions=fronts["instructions"],
        items=items,
        closing=closing,
        declaration=declaration,
        verification=Region("verification", [b.id for b in atts["verification"]]),
        index=Region("index", [b.id for b in atts["index"]]),
        service=Region("service", [b.id for b in atts["service"]]),
        notes=Region("notes", [b.id for b in atts["notes"]]),
        blocks=blocks,
        detectors={"title": title_det, "items": item_det},
    )
    _check(paper, blocks, ident)
    return paper


def _title_ids(blocks: list[Block], titles: dict[int, str]) -> set[str]:
    return {blocks[i].id for i in titles}


def _ids_to_idx(blocks: list[Block], ids: list[str]) -> list[int]:
    want = set(ids)
    return [i for i, b in enumerate(blocks) if b.id in want]


def _names_type(b: Block) -> bool:
    t = vocab.norm(b.text)
    return any(w in t for _, words in vocab.TYPES for w in words)


def _is_type_title(b: Block) -> bool:
    return _names_type(b) and (b.caps or b.bold or b.heading > 0)


def _header(blocks: list[Block], first: int, titles: dict[int, str]) -> tuple[Region, Region, Region, int]:
    """Caption, title, identification, and the last header index: the header
    runs to the last identification line, or the title when there is none,
    before the first front title or item."""
    stop = min([i for i, r in titles.items() if r in vocab.FRONT and i < first] + [first])
    title_at = next((i for i in range(stop) if _is_type_title(blocks[i]) and not blocks[i].in_table), None)
    if title_at is None:  # the title written in a caption table cell
        title_at = next((i for i in range(stop) if blocks[i].in_table and _names_type(blocks[i])), None)
    ident_at = [i for i in range(stop) if vocab.IDENT.match(blocks[i].text)]
    end = max(ident_at + ([title_at] if title_at is not None else []), default=-1)
    caption, title, ident = Region("caption"), Region("title"), Region("ident")
    for i in range(end + 1):
        reg = ident if i in ident_at else title if i == title_at else caption
        reg.ids.append(blocks[i].id)
    return caption, title, ident, end


def _front(blocks: list[Block], start: int, stop: int, titles: dict[int, str]) -> dict[str, Region]:
    """The front between the header and the first item, split at its titles;
    untitled text goes to the general front."""
    regions = {name: Region(name) for name in vocab.FRONT}
    current = "front"
    for i in range(start, stop):
        r = titles.get(i)
        if r in vocab.FRONT:
            current = r
        regions[current].ids.append(blocks[i].id)
    return regions


def _closing_start(blocks: list[Block], start: int, stop: int) -> int:
    for i in range(start, stop):
        b = blocks[i]
        if b.in_table or b.in_textbox or vocab.CLOSING.match(b.text):
            return i
    return stop


def _closing(blocks: list[Block], start: int, stop: int) -> Region:
    reg = Region("closing")
    for i in range(start, stop):
        b = blocks[i]
        if not (
            b.in_table or b.in_textbox or vocab.CLOSING.match(b.text) or len(b.text.split()) <= vocab.CLOSING_MAX_WORDS
        ):
            raise _fail(_ASSIGNED, "closing", b, "a block after the signature belongs to no region")
        reg.ids.append(b.id)
    return reg


def _attachments(blocks: list[Block], starts: list[int], titles: dict[int, str]) -> dict[str, list[Block]]:
    out: dict[str, list[Block]] = {name: [] for name in vocab.ATTACHMENTS}
    for k, s in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(blocks)
        out[titles[s]] += blocks[s:end]
    return out


def _item(blocks: list[Block], head: detect.Head, end: int) -> Item:
    own = blocks[head.index : end]
    it = Item(number=head.number, label=head.label, ids=[b.id for b in own])
    resp = next((k for k, b in enumerate(own) if vocab.RESPONSE.match(b.text)), None)
    if resp is None:
        it.request = list(it.ids)
        return it
    m = vocab.RESPONSE.match(own[resp].text)
    assert m is not None
    if int(m.group(2)) != head.number:
        raise _fail(_NUMBERED, "items", own[resp], f"response heading No. {m.group(2)} under item {head.number}")
    it.request = [b.id for b in own[:resp]]
    it.response_head = own[resp].id
    rest = own[resp + 1 :]
    k = 0
    while k < len(rest) and _OBJECTS.search(rest[k].text):
        k += 1
    it.objections = [b.id for b in rest[:k]]
    it.answer = [b.id for b in rest[k:]]
    it.cites = [b.id for b in rest[k:] if _CITE.search(b.text)]
    return it


def _title_text(blocks: list[Block], title: Region) -> str:
    return vocab.norm(" ".join(b.text for b in blocks if b.id in set(title.ids)))


def _type(blocks: list[Block], title: Region, items: list[Item]) -> str:
    """The type the title names; a typed label ("REQUEST FOR ADMISSION NO.")
    naming another type is a type_conflict, never a guess. A generic label
    ("REQUEST NO.") takes the title's type."""
    text = _title_text(blocks, title)
    by_title = next((name for name, words in vocab.TYPES if any(w in text for w in words)), None)
    typed = [it for it in items if it.label in vocab.LABEL_TYPES and it.label not in vocab.GENERIC_LABELS]
    clash = next((it for it in typed if by_title and vocab.LABEL_TYPES[it.label] != by_title), None)
    if clash is not None:
        b = next(x for x in blocks if x.id == clash.ids[0])
        why = f"type_conflict: the title names {by_title}, item {clash.number} is labelled {clash.label}"
        raise _fail(_NUMBERED, "type", b, why)
    base = by_title or next((vocab.LABEL_TYPES[it.label] for it in typed), None)
    if base is None:
        base = next((vocab.LABEL_TYPES[it.label] for it in items if it.label in vocab.LABEL_TYPES), "unknown")
    response = "RESPONSE" in text or any(it.response_head for it in items)
    return f"responses_to_{base}" if response else base


def _set_number(blocks: list[Block], ident: Region) -> int | None:
    for b in blocks:
        if b.id in set(ident.ids):
            n = vocab.set_number(b.text)
            if n is not None:
                return n
    return None


def _check(paper: Paper, blocks: list[Block], ident: Region) -> None:
    by_id = {b.id: b for b in blocks}
    seen: dict[str, str] = {}
    for bid, region in paper.assignment():
        if bid in seen:
            raise _fail(_ASSIGNED, region, by_id.get(bid), f"block in both {seen[bid]} and {region}")
        seen[bid] = region
    for b in blocks:
        if b.id not in seen:
            raise _fail(_ASSIGNED, "unassigned", b, "block belongs to no region")
    partial = bool(vocab.PARTIAL.search(_title_text(blocks, paper.title)))
    _check_numbers(paper, by_id, _set_number(blocks, ident), partial)
    if paper.declaration is not None:
        _check_declaration(paper.declaration, paper.items, by_id)


def _check_numbers(paper: Paper, by_id: dict[str, Block], set_no: int | None, partial: bool) -> None:
    """Items 1..n with no gap; a later set may start past 1; an amended or
    supplemental paper answers only the challenged items, so its numbers need
    only rise. A repeat or a fall is never read."""
    nums = [it.number for it in paper.items]
    start = nums[0]
    if partial:
        for prev, it in zip(paper.items, paper.items[1:], strict=False):
            if it.number <= prev.number:
                why = f"item {it.number} follows item {prev.number}: a repeat or a fall"
                raise _fail(_NUMBERED, "items", by_id[it.ids[0]], why)
        return
    if start != 1 and (set_no is None or set_no <= 1):
        raise _fail(_NUMBERED, "items", by_id[paper.items[0].ids[0]], f"the first item is numbered {start}, not 1")
    for prev, it in zip(paper.items, paper.items[1:], strict=False):
        if it.number != prev.number + 1:
            why = "a repeat" if it.number <= prev.number else "a gap"
            raise _fail(_NUMBERED, "items", by_id[it.ids[0]], f"item {it.number} follows item {prev.number}: {why}")


def _check_declaration(d: Declaration, items: list[Item], by_id: dict[str, Block]) -> None:
    n = len(items)
    have = {it.number for it in items}
    for c in d.count_mentions:
        if c.value != n:
            raise _fail(_DECLARED, "declaration", by_id[c.id], f"the declaration states {c.value}; the set has {n}")
    for rl in d.reason_lists:
        extra = sorted(set(rl.numbers) - have)
        if extra:
            reason = f"({rl.reason})" if rl.reason else "the whole-set"
            raise _fail(_DECLARED, "declaration", by_id[rl.id], f"{reason} list names {extra}, not in the set")
