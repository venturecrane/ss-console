"""A mediation brief's first draft in the firm's own Word file (``firmbase``), its
first user.

Why (A&P, 2026-11-04): every first draft's layout came from our renderer and
hand-typed config keys, the firm's specimens were read for their words only, and
the attorney corrected layout four times in one day. With a base in
``format.firm_bases.<class>`` the firm's file comes back with only the words changed;
the config layout keys stay for a firm with none.

``adopt`` marks the attorney's accepted save (else a firm's past brief) as a base: every line becomes a slot, one paragraph of
each role and one chart per column count stay, every word goes. ``fill`` reads the
draft's markdown and puts it:

* the front's attorney, party and court lines, gaps held;
* the caption poured column by column, so its rows, blank lines and rule hold however
  the draft divides its rows; a slot with no line stays a blank line;
* the body as a region: a clone of its role per block, of the chart with its column
  count (else the widest it can grow from) per table;
* the signature's firm line, date, "By:" and name lines where the firm has them.
"""

from __future__ import annotations

import copy
import io
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from lxml import etree
from smokeball_connector import docgrammar as g
from smokeball_connector.docx_classes import is_total_row

from .. import firmbase
from ..firmbase import FirmBase, FirmBaseLost, FirmBaseUnfit, Rows
from ..firmbase import xmlproof
from ..firmbase.base import W, _text_el, is_blank
from ..firmbase.markers import marked
from .outcome import DraftingFailed
from .revise import layout as lay

CLS = "mediation_brief"
FRONT_SLOTS = ("attorney", "party", "court")
#: What the format check reads from the config and the base now owns.
BASE_OWNS = ("table_font_pt", "table_header_fill", "table_total_fill", "table_border_pt")
_BR = re.compile(r"<br\s*/?>", re.I)
_FOR = re.compile(r"(?i)^\s*attorneys?\s+for\b")
_DATED = re.compile(r"(?i)^\s*dated\s*:")
_BY = re.compile(r"(?i)^\s*by\s*:")
#: The signing line, however the firm lays it: "By:", beside the date, or a bare rule.
_BY_ANY = re.compile(r"(?i)^\s*by\s*:|\tby\s*:|^\s*_{5,}\s*$")
_DATED_BY = re.compile(r"(?i)^\s*(dated\s*:.*?)\s+(by\s*:.*)$")
_ROMAN = re.compile(r"^[IVXLC]+\.\s")
_LETTER = re.compile(r"^[A-Z]\.\s")
_NUMBER = re.compile(r"^\d+\.\s")


def unfit(why: str) -> DraftingFailed:
    return DraftingFailed(f"firm_base_unfit: {why}; nothing was filed")


# ---- adoption ---------------------------------------------------------------------------
def _to_slot(p: Any, name: str, *more: str, field: bool = False) -> None:
    """The paragraph's runs become one run of its slot(s) on its first run's rPr, its
    first tab kept: between two slots on one line, before a lone slot."""
    runs = [r for r in p if r.tag != f"{W}pPr"]
    first = next((r for r in runs if r.tag == f"{W}r" and r.find(f"{W}t") is not None), None)
    rpr = first.find(f"{W}rPr") if first is not None else None
    tabbed = any(True for r in runs for _ in r.iter(f"{W}tab"))
    for r in runs:
        p.remove(r)
    run = etree.SubElement(p, f"{W}r")
    if rpr is not None:
        run.append(rpr)
    if tabbed and not more:
        etree.SubElement(run, f"{W}tab")
    for n, slot in enumerate((name, *more)):
        if n:
            etree.SubElement(run, f"{W}tab")
        run.append(_text_el("{{" + slot + "}}" if field else f"[[{slot}]]"))


def _text(p: Any) -> str:
    return "".join(t.text or "" for t in p.iter(f"{W}t")).strip()


def _flags(p: Any, attr: str) -> bool:
    runs = [r for r in p.runs if r.text.strip()]
    return bool(runs) and all(lay.flag(r, p, attr) for r in runs)


def _heading(p: Any) -> int:
    """A heading by role, or by the firm's direct formatting: a bold line opening
    with its numeral. A firm's brief often has no heading style at all."""
    level = lay.heading_level(p)
    if level:
        return level
    text = p.text.strip()
    if not text or len(text) > 120 or not _flags(p, "bold") or _flags(p, "italic"):
        return 0
    if _ROMAN.match(text):
        return 1
    return 2 if _LETTER.match(text) else 3 if _NUMBER.match(text) else 0


def _role(p: Any) -> str:
    level = _heading(p)
    if level:
        return f"h{level}"
    if _flags(p, "bold") and _flags(p, "italic"):
        return "provider"
    return "p"


def _key(el: Any) -> bytes:
    ppr = el.find(f"{W}pPr")
    return xmlproof.canon(ppr) if ppr is not None else b""


def _library(doc: Any, els: list[Any]) -> tuple[list[Any], list[tuple[Any, Any]], list[str]]:
    """(the paragraph each role is laid by most often, one (chart, blank under it) per
    column count, a note per role the file lays more than one way)."""
    from docx.text.paragraph import Paragraph

    roles: dict[str, list[Any]] = {}
    charts: dict[int, tuple[Any, Any]] = {}
    for i, el in enumerate(els):
        if el.tag == f"{W}tbl":
            n = len(el.find(f"{W}tblGrid").findall(f"{W}gridCol"))
            if n not in charts and len(el.findall(f"{W}tr")) >= 2:
                charts[n] = (el, els[i + 1] if i + 1 < len(els) and is_blank(els[i + 1]) else None)
        elif not is_blank(el):
            roles.setdefault(_role(Paragraph(el, doc)), []).append(el)
    keep, notes = [], []
    for role, members in roles.items():
        counts = Counter(_key(e) for e in members)
        if len(counts) > 1:
            notes.append(
                f"{role}: {len(counts)} ways, kept the commonest ({counts.most_common(1)[0][1]} of {len(members)})"
            )
        modal = counts.most_common(1)[0][0]
        el = next(e for e in members if _key(e) == modal)
        _to_slot(el, f"body.{role}")
        keep.append(el)
    return keep, list(charts.values()), notes


def _chart_slots(tbl: Any, n: int) -> None:
    """The header, up to two body rows, the total: one row of each kind. A chart with
    no total keeps its last body row as the proto's total."""
    rows = tbl.findall(f"{W}tr")
    for extra in rows[3:-1]:
        tbl.remove(extra)
    if len(rows) == 2:
        rows[-1].addnext(copy.deepcopy(rows[-1]))
    for tc in tbl.iter(f"{W}tc"):
        paras = tc.findall(f"{W}p")
        for extra in paras[1:]:
            tc.remove(extra)
        p = paras[0]
        if p.find(f"{W}r/{W}t") is None:
            etree.SubElement(etree.SubElement(p, f"{W}r"), f"{W}t").text = "x"
        _to_slot(p, f"body.chart{n}")


def _front(doc: Any, els: list[Any]) -> None:
    from docx.text.paragraph import Paragraph

    paras = [Paragraph(e, doc) for e in els]
    court = len(paras)
    while court > 0 and (not paras[court - 1].text.strip() or _flags(paras[court - 1], "bold")):
        court -= 1
    party = next((i for i, p in enumerate(paras[:court]) if _FOR.match(p.text)), court)
    for i, p in enumerate(paras):
        if p.text.strip():
            _to_slot(p._p, "attorney" if i < party else "party" if i < court else "court")


def _caption(tbl: Any) -> None:
    for tr in tbl.findall(f"{W}tr"):
        for c, tc in enumerate(tr.findall(f"{W}tc")):
            for p in tc.findall(f"{W}p"):
                if _text(p):
                    _to_slot(p, f"cap.{c}")


def _signature(doc: Any, paras: list[Any]) -> int:
    """Marks the block; returns the index of its first paragraph in ``paras``."""
    by = next((i for i in range(len(paras) - 1, -1, -1) if _BY_ANY.search(paras[i].text)), None)
    if by is None:
        raise unfit("the firm's brief has no signature block")
    dated = next((i for i in range(by, max(-1, by - 8), -1) if _DATED.match(paras[i].text)), None)
    top = by - 1
    while top >= 0 and not paras[top].text.strip():
        top -= 1
    start = min(top, dated if dated is not None else top)
    start = _firm_above(paras, start)
    while start > 0 and not paras[start - 1].text.strip():
        start -= 1
    for i, p in enumerate(paras[start:], start):
        if not p.text.strip():
            continue
        if i == by and dated == by:
            _to_slot(p._p, "sig.dated", "sig.by", field=True)
        elif i == by:
            _to_slot(p._p, "sig.by", field=True)
        elif i == dated:
            _to_slot(p._p, "sig.dated", field=True)
        else:
            _to_slot(p._p, "sig.firm" if i < by else "sig.name")
    return start


def _firm_above(paras: list[Any], start: int) -> int:
    """A firm's name in a short bold line above the date belongs to the block."""
    j = start - 1
    while j >= 0 and not paras[j].text.strip():
        j -= 1
    is_firm = j >= 0 and len(paras[j].text.strip()) < 60 and _flags(paras[j], "bold") and not _heading(paras[j])
    return j if is_firm else start


def adopt(blob: bytes, source: firmbase.Source) -> tuple[bytes, list[str]]:
    """(the firm's brief as a base, notes). A file with Operator lineage is refused
    unless ``source`` records the attorney's acceptance of exactly these bytes."""
    import docx
    from docx.text.paragraph import Paragraph

    if not firmbase.may_adopt(blob, source):
        raise unfit("the file is the Operator's own and no accepted save is recorded for it")
    doc = docx.Document(io.BytesIO(blob))
    body = doc.element.body
    kids = list(body)
    at = next((i for i, e in enumerate(kids) if e.tag == f"{W}tbl"), None)
    first = next((i for i, e in enumerate(kids) if e.tag == f"{W}p" and _heading(Paragraph(e, doc))), None)
    if at is None or first is None or at > first or kids[-1].tag != f"{W}sectPr":
        raise unfit("the firm's brief has no caption table above its first heading")
    paras = [Paragraph(e, doc) for e in kids[first:-1] if e.tag == f"{W}p"]
    start = _signature(doc, paras)
    tail_at = kids.index(paras[start]._p)
    keep, charts, notes = _library(doc, kids[first:tail_at])
    if not charts:
        raise unfit("the firm's brief has no chart to take a chart's look from")
    _front(doc, kids[:at])
    _caption(kids[at])
    for tbl, _blank in charts:
        _chart_slots(tbl, len(tbl.find(f"{W}tblGrid").findall(f"{W}gridCol")))
    placed = [x for t, sp in charts for x in (t, sp) if x is not None]
    for el in kids[first:tail_at]:
        if el not in {*keep, *placed}:
            body.remove(el)
    # The charts first, so the blank under one is never read as the signature's space.
    for el in placed:
        keep[0].addprevious(el)
    for t in body.iter(f"{W}t"):
        if (t.text or "").strip() and not re.fullmatch(r"\[\[[a-z0-9.]+\]\]|\{\{[a-z0-9._]+\}\}", t.text or ""):
            raise unfit("a word of the firm's brief survived the blanking")
    data = etree.tostring(doc.element, xml_declaration=True, encoding="UTF-8", standalone=True)
    return firmbase.clean_package(blob, data), notes


# ---- the draft --------------------------------------------------------------------------
def _plain(runs: tuple[g.Run, ...]) -> str:
    return "".join(r.text for r in runs)


def _all_bold(runs: tuple[g.Run, ...]) -> bool:
    live = [r for r in runs if r.text.strip()]
    return bool(live) and all(r.within_bold if r.marker else r.bold for r in live)


def _bold_italic(runs: tuple[g.Run, ...]) -> bool:
    live = [r for r in runs if r.text.strip()]
    return bool(live) and all(r.within_italic if r.marker else r.italic for r in live) and _all_bold(runs)


def _groups(lines: list[tuple[g.Run, ...]]) -> dict[str, list[tuple[g.Run, ...]]]:
    court = len(lines)
    while court > 0 and _all_bold(lines[court - 1]):
        court -= 1
    party = next((i for i, x in enumerate(lines[:court]) if _FOR.match(_plain(x))), court)
    return {"attorney": lines[:party], "party": lines[party:court], "court": lines[court:]}


def _broken(cell: tuple[g.Run, ...]) -> list[tuple[g.Run, ...]]:
    """One cell's lines, split at each ``<br>``; empty lines dropped."""
    lines: list[list[g.Run]] = [[]]
    for run in cell:
        for n, piece in enumerate([run.text] if run.marker else _BR.split(run.text)):
            if n:
                lines.append([])
            if piece:
                lines[-1].append(g.Run(piece, run.bold, run.italic, run.marker, run.within_bold, run.within_italic))
    return [tuple(x) for x in lines if "".join(r.text for r in x).strip()]


def split_blocks(blocks: list[g.Block]) -> tuple[list[tuple[g.Run, ...]], g.Table, list[g.Block], dict[str, Any]]:
    """(front lines, the caption, the body, the signature) of a parsed draft."""
    first = next((i for i, b in enumerate(blocks) if isinstance(b, g.Heading)), None)
    cap = next((i for i, b in enumerate(blocks) if isinstance(b, g.Table)), None)
    if first is None or cap is None or cap > first:
        raise unfit("the draft has no caption table above its first heading")
    if not all(isinstance(b, g.Paragraph) for b in [*blocks[:cap], *blocks[cap + 1 : first]]):
        raise unfit("the draft's front holds something other than lines")
    start, sig = _signature_of(blocks, first)
    return [b.runs for b in blocks[:cap]], blocks[cap], blocks[first:start], sig  # type: ignore[union-attr,return-value]


def _signature_of(blocks: list[g.Block], first: int) -> tuple[int, dict[str, Any]]:
    def text(i: int) -> str:
        b = blocks[i]
        return _plain(b.runs).strip() if isinstance(b, g.Paragraph) else ""

    by = next((i for i in range(len(blocks) - 1, first, -1) if _BY.match(text(i)) or _DATED_BY.match(text(i))), None)
    if by is None:
        raise unfit("the draft has no signature block")
    names = [text(i) for i in range(by + 1, len(blocks)) if text(i)]
    both = _DATED_BY.match(text(by))
    if both:
        start = by
        while start - 1 > first and 0 < len(text(start - 1)) < 90 and _all_bold(blocks[start - 1].runs):  # type: ignore[union-attr]
            start -= 1
        firm = [text(i) for i in range(start, by)]
        return start, {"dated": both.group(1), "by": both.group(2), "firm": firm, "names": names}
    dated = next((i for i in range(by - 1, max(first, by - 6), -1) if _DATED.match(text(i))), None)
    start = dated if dated is not None else by
    firm = [text(i) for i in range(start + 1, by) if text(i)] if dated is not None else []
    return start, {"dated": text(dated) if dated is not None else "", "by": text(by), "firm": firm, "names": names}


def _plain_line(text: str) -> tuple[g.Run, ...]:
    return (g.Run(text),)


def _chart_member(base: FirmBase, block: g.Table) -> str:
    """The chart proto of this many columns, else the widest narrower one to grow from."""
    n = max(len(r) for r in block.rows)
    have = {int(s[10:]): s for s in base.slots if s.startswith("body.chart")}
    fits = [k for k in have if k <= n]
    if not fits:
        raise unfit(f"a chart of {n} columns and no proto it can grow from")
    return have[n] if n in have else have[max(fits)]


def _rows(block: g.Table) -> Rows:
    return Rows(block.rows, block.header, lambda row: is_total_row([_plain(row[0])]))


def _items(base: FirmBase, body: list[g.Block]) -> list[tuple[str, Any]]:
    items: list[tuple[str, Any]] = []
    for b in body:
        if isinstance(b, g.Table):
            items.append((_chart_member(base, b), _rows(b)))
        elif isinstance(b, g.Heading):
            items.append((f"body.h{b.level}", b.runs))
        elif isinstance(b, g.Paragraph):
            provider = _bold_italic(b.runs) and "body.provider" in base.slots
            items.append(("body.provider" if provider else "body.p", b.runs))
        else:
            raise unfit(f"the draft has a {type(b).__name__.lower()} and the base has no such paragraph")
    return items


def _words(text: str) -> Counter[str]:
    return Counter(text.split())


def _draft_words(md: str) -> Counter[str]:
    parts: list[str] = []
    for b in g.parse_document(md):
        if isinstance(b, g.Table):
            parts += [" ".join(_plain(x) for x in _broken(c)) for row in b.rows for c in row]
        elif isinstance(b, (g.Paragraph, g.Heading)):
            parts.append(b.text)
    return _words(" ".join(parts))


def _read_back(blob: bytes) -> Counter[str]:
    """The file's words; a tab between two slots parts two words."""
    root = etree.fromstring(zipfile.ZipFile(io.BytesIO(blob)).read("word/document.xml"))
    parts: list[str] = []
    for p in root.iter(f"{W}p"):
        for r in p.findall(f"{W}r"):
            parts += [(e.text or "") if e.tag == f"{W}t" else " " for e in r.iter(f"{W}t", f"{W}tab")]
        parts.append(" ")
    return _words("".join(parts))


def _put_signature(fill: Any, sig: dict[str, Any]) -> None:
    for name, value in (("sig.dated", sig["dated"]), ("sig.by", sig["by"])):
        if name in fill.base.slots:
            fill.put(name, value)
        elif value:
            raise unfit(f"the draft has a {name} line and the base no slot for it")
    for name, values in (("sig.firm", sig["firm"]), ("sig.name", sig["names"])):
        if name not in fill.base.slots:
            raise unfit(f"the base has no slot {name}")
        if not values:
            raise unfit(f"the draft's signature has no {name} line")
        fill.put(name, [_plain_line(v) for v in values], carry=True)


def fill(base: FirmBase, md: str) -> tuple[bytes, firmbase.Proof]:
    """The draft in a copy of the base, and the proof."""
    front, caption, body, sig = split_blocks(g.parse_document(md))
    f = firmbase.open(base)
    groups = _groups(front)
    for name in FRONT_SLOTS:
        if not groups[name]:
            raise unfit(f"the draft has no {name} lines")
        f.put(name, groups[name])
    cols = {int(s.split(".")[1]) for s in base.slots if s.startswith("cap.")}
    if max(len(r) for r in caption.rows) > max(cols, default=-1) + 1:
        raise unfit("the draft's caption has more columns than the base's")
    for c in sorted(cols):
        f.put(f"cap.{c}", [x for row in caption.rows if c < len(row) for x in _broken(row[c])], spare="blank")
    f.put("body", _items(base, body))
    _put_signature(f, sig)
    blob, proof = f.save()
    if _read_back(blob) != _draft_words(md):
        proof.leaves_outside_allow.append("the file does not read as the draft")
    return blob, proof


def load_base(path: Path, source: dict[str, str], cls: str = CLS) -> FirmBase:
    return FirmBase.load(cls, firmbase.Source(**source), path.read_bytes())


def render_into(md: str, out: Path, base: FirmBase, paper: str, make: Any = None) -> tuple[Path, dict[str, Any]]:
    """The file at ``out``, proven against its base; nothing is written when the proof
    refuses. ``paper``: what the job was asked for, which the base must already be.
    ``make``: the class's own fill, the brief's when none is named."""
    from .revise.layout import paper_of

    if paper_of(base.bytes) != paper:
        raise unfit(f"the request is for {paper} paper and the base is {paper_of(base.bytes)}")
    try:
        blob, proof = (make or fill)(base, md)
        proof.require()
    except FirmBaseLost as exc:
        raise DraftingFailed(str(exc)) from exc
    except FirmBaseUnfit as exc:
        raise unfit(str(exc)) from exc
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(f".{out.name}.tmp")
    part.write_bytes(blob)
    part.replace(out)
    return out, {"notes": [f"rendered into the firm's base, {base.source.name}"]}


def _chart_size_pt(base: FirmBase) -> float | None:
    root = etree.fromstring(zipfile.ZipFile(io.BytesIO(base.bytes)).read("word/document.xml"))
    for tbl in root.find(f"{W}body").findall(f"{W}tbl"):
        if any(s.startswith("body.chart") for s in marked(tbl)):
            sz = tbl.find(f".//{W}r/{W}rPr/{W}sz")
            return int(sz.get(f"{W}val")) / 2 if sz is not None else None
    return None


def checked_format(fmt: dict[str, Any], cls: str, base: FirmBase) -> dict[str, Any]:
    """``fmt`` as the format check reads it for a brief on a base: the chart's look is
    proven against the base and its size read from it; the config's chart keys say
    nothing about this file."""
    layout = {k: v for k, v in ((fmt.get("layout") or {}).get(cls) or {}).items() if k not in BASE_OWNS}
    size = _chart_size_pt(base)
    if size:
        layout["table_font_pt"] = size
    return {**fmt, "layout": {**(fmt.get("layout") or {}), cls: layout}}
