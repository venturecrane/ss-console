"""The proof of a fill: where the result differs from the base, only the content does.

``Proof``: four lists, any of the first three non-empty is ``firm_base_lost``.

* ``parts_differ``: every part but the document, by its bytes; the document's root
  and what sits beside the body;
* ``leaves_outside_allow``: ``xmlproof.prove_body`` against the Allow each put
  recorded, then a reading of the two files alone: what stands outside the region in
  the base's order, every blank and fixed line exact, each slot line on its slot's
  paragraph and run properties (emphasis aside); a table poured in place with its
  table, row and cell properties exact; every region element one the base has;
* ``slots_unfilled``: a slot no put reached, or its mark still in the file;
* ``slots_filled``: what was put.

``layout_diff`` is the same reading between any two files, words and run splits aside:
how a rehearsal holds a render against a document the attorney accepted.
"""

from __future__ import annotations

import copy
import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any

from lxml import etree

from . import xmlproof
from .base import DOCUMENT, EMPHASIS, FIELD, W, FirmBase, FirmBaseLost, is_blank, region_span, slot_names

_MARK = re.compile(r"\[\[[a-z0-9.]+\]\]")


@dataclass
class Proof:
    parts_differ: list[str] = field(default_factory=list)
    leaves_outside_allow: list[str] = field(default_factory=list)
    slots_unfilled: list[str] = field(default_factory=list)
    slots_filled: list[str] = field(default_factory=list)

    @property
    def lost(self) -> list[str]:
        return [*self.parts_differ, *self.leaves_outside_allow, *[f"slot {s} not filled" for s in self.slots_unfilled]]

    def require(self) -> None:
        if self.lost:
            raise FirmBaseLost("firm_base_lost: " + "; ".join(self.lost)[:400] + "; nothing was filed")


def _rpr(run: Any) -> bytes:
    rpr = run.find(f"{W}rPr")
    if rpr is None:
        return b""
    rpr = copy.deepcopy(rpr)
    for tag in EMPHASIS:
        for old in rpr.findall(f"{W}{tag}"):
            rpr.remove(old)
    return xmlproof.canon(rpr) if len(rpr) else b""


def _ppr(p: Any) -> bytes:
    ppr = p.find(f"{W}pPr")
    return xmlproof.canon(ppr) if ppr is not None else b""


def _para(p: Any) -> tuple[bytes, frozenset[bytes], int]:
    runs = [r for r in p.findall(f"{W}r") if r.find(f"{W}t") is not None]
    return _ppr(p), frozenset(_rpr(r) for r in runs), sum(len(r.findall(f"{W}tab")) for r in runs)


def _fits(p: Any, allowed: list[tuple[bytes, frozenset[bytes], int]]) -> bool:
    """On one of the allowed paragraphs, or that paragraph left blank."""
    ppr, rprs, tabs = _para(p)
    return any(ppr == a and rprs <= b and (tabs == c or not rprs) for a, b, c in allowed)


def _props(tbl: Any, tag: str) -> bytes:
    el = tbl.find(f"{W}{tag}")
    return xmlproof.canon(el) if el is not None else b""


def _no_width(tc_pr: Any) -> bytes:
    el = copy.deepcopy(tc_pr)
    for w in el.findall(f"{W}tcW"):
        el.remove(w)
    return xmlproof.canon(el)


def _poured_lost(was: Any, now: Any) -> list[str]:
    """A table filled in place: its properties, rows and cells exact."""
    out = [f"a table's {tag} changed" for tag in ("tblPr", "tblGrid") if _props(was, tag) != _props(now, tag)]
    a, b = was.findall(f"{W}tr"), now.findall(f"{W}tr")
    if len(a) != len(b):
        return [*out, f"a table has {len(b)} rows, the base {len(a)}"]
    for i, (x, y) in enumerate(zip(a, b)):
        xc, yc = x.findall(f"{W}tc"), y.findall(f"{W}tc")
        if len(xc) != len(yc) or any(_props(p, "tcPr") != _props(q, "tcPr") for p, q in zip(xc, yc)):
            out.append(f"a table's row {i + 1} changed its cells")
            continue
        for p, q in zip(xc, yc):
            allowed = [_para(e) for e in p.findall(f"{W}p")]
            if not all(_fits(e, allowed) for e in q.findall(f"{W}p")):
                out.append(f"a line in a table's row {i + 1} left its slot's layout")
    return out


def _grid(tbl: Any) -> list[int]:
    return [int(c.get(f"{W}w")) for c in tbl.find(f"{W}tblGrid").findall(f"{W}gridCol")]


def _clone_lost(was: Any, now: Any) -> list[str]:
    """A clone of a table proto: properties and looks its own; more columns may hold its width."""
    if _props(was, "tblPr") != _props(now, "tblPr"):
        return ["a table clone's properties changed"]
    a, b = _grid(was), _grid(now)
    wider = len(a) != len(b)
    if (sum(a) != sum(b)) if wider else (a != b):
        return ["a table clone's column widths changed"]
    allowed = [_para(p) for p in was.iter(f"{W}p")]
    cells = {_no_width(tc) if wider else xmlproof.canon(tc) for tc in was.iter(f"{W}tcPr")}
    out = []
    if any((_no_width(tc) if wider else xmlproof.canon(tc)) not in cells for tc in now.iter(f"{W}tcPr")):
        out.append("a table clone's cell is not one of the proto's")
    if not all(_fits(p, allowed) for p in now.iter(f"{W}p")):
        out.append("a line in a table clone left the proto's layout")
    return out


def _is_slot_line(el: Any) -> bool:
    return el.tag == f"{W}p" and (
        bool(slot_names(el)) or bool(FIELD.search("".join(t.text or "" for t in el.iter(f"{W}t"))))
    )


def _walk(what: str, was: list[Any], now: list[Any]) -> tuple[list[str], int]:
    """``now`` read against ``was`` in order: fixed lines exact, each run of like slot
    lines at least one. (what was lost, how many of ``now`` it took.)"""
    out, i, n = [], 0, 0
    while i < len(was):
        el = was[i]
        if n >= len(now):
            return [*out, f"the {what} is short of the base"], n
        if el.tag == f"{W}tbl":
            out += _poured_lost(el, now[n]) if now[n].tag == f"{W}tbl" else [f"the {what} lost a table"]
            i, n = i + 1, n + 1
        elif _is_slot_line(el):
            like = [el]
            while (
                i + len(like) < len(was)
                and _is_slot_line(was[i + len(like)])
                and _para(was[i + len(like)]) == _para(el)
            ):
                like.append(was[i + len(like)])
            i += len(like)
            if now[n].tag != f"{W}p" or is_blank(now[n]) or not _fits(now[n], [_para(el)]):
                out.append(f"a line in the {what} left its slot's layout")
            n += 1
            while n < len(now) and now[n].tag == f"{W}p" and not is_blank(now[n]) and _fits(now[n], [_para(el)]):
                n += 1
        else:
            if xmlproof.canon(el) != xmlproof.canon(now[n]):
                out.append(f"a fixed line in the {what} changed")
            i, n = i + 1, n + 1
    return out, n


def _region_lost(zone: list[Any], els: list[Any]) -> list[str]:
    paras = [_para(e) for e in zone if slot_names(e) and e.tag == f"{W}p"]
    tables = [e for e in zone if e.tag == f"{W}tbl"]
    blanks = {xmlproof.canon(e) for e in zone if is_blank(e)}
    out: list[str] = []
    for el in els:
        if el.tag == f"{W}tbl":
            tries = [_clone_lost(t, el) for t in tables] or [["a table with no proto"]]
            out += min(tries, key=len)
        elif el.tag != f"{W}p" or (xmlproof.canon(el) not in blanks and not _fits(el, paras)):
            out.append("a region element is not one of the base's")
    return out


def _parts_lost(base: bytes, out: bytes) -> list[str]:
    a, b = zipfile.ZipFile(io.BytesIO(base)), zipfile.ZipFile(io.BytesIO(out))
    if a.namelist() != b.namelist():
        return ["the file's parts are not the base's"]
    lost = [f"{n} changed" for n in a.namelist() if n != DOCUMENT and a.read(n) != b.read(n)]
    if DOCUMENT in xmlproof.parts_differ(base, out):
        lost.append("the document's root changed")
    return lost


def body_lost(base: bytes, out: bytes) -> list[str]:
    """The two files alone, no record of the fill."""
    was = list(etree.fromstring(zipfile.ZipFile(io.BytesIO(base)).read(DOCUMENT)).find(f"{W}body"))
    now = list(etree.fromstring(zipfile.ZipFile(io.BytesIO(out)).read(DOCUMENT)).find(f"{W}body"))
    if now[-1].tag != f"{W}sectPr" or xmlproof.canon(now[-1]) != xmlproof.canon(was[-1]):
        return ["the section properties changed"]
    span = region_span(was)
    if span is None:
        return _walk("document", was[:-1], now[:-1])[0]
    first, last = span
    lost, head = _walk("head", was[:first], now[:-1])
    tail_lost, tail = _walk("tail", was[last + 1 : -1][::-1], now[head:-1][::-1])
    end = len(now) - 1 - tail
    return [*lost, *tail_lost, *_region_lost(was[first : last + 1], now[head:end])]


def prove(base: FirmBase, out: bytes, fill: Any = None) -> Proof:
    """``fill``: the record of what each put touched; a file read on its own gets the
    two-file reading only."""
    proof = Proof(parts_differ=_parts_lost(base.bytes, out))
    if fill is not None:
        proof.slots_filled = list(fill.filled)
        proof.leaves_outside_allow += xmlproof.prove_body(fill, fill.before, base.bytes, out)
        proof.slots_unfilled = [
            s.name for s in base.slots.values() if s.name not in fill.filled and not s.name.startswith("body.")
        ]
    proof.leaves_outside_allow += body_lost(base.bytes, out)
    root = etree.fromstring(zipfile.ZipFile(io.BytesIO(out)).read(DOCUMENT))
    text = "\n".join(t.text or "" for t in root.iter(f"{W}t"))
    names = {s.name for s in base.slots.values()}
    left = [m.strip("[]") for m in _MARK.findall(text)] + [f for f in FIELD.findall(text) if f in names]
    proof.slots_unfilled += [n for n in left if n not in proof.slots_unfilled]
    return proof


# ---- two files, words and run splits aside ---------------------------------------------
def _normal(el: Any) -> Any:
    """``el`` with no words and no page-break hints (Word's record of where the last
    pagination fell, not layout), adjacent runs of the same properties merged."""
    el = copy.deepcopy(el)
    for p in [el] if el.tag == f"{W}p" else list(el.iter(f"{W}p")):
        prev: Any = None
        for r in p.findall(f"{W}r"):
            for t in [*r.findall(f"{W}t"), *r.findall(f"{W}lastRenderedPageBreak")]:
                r.remove(t)
            key = xmlproof.canon(r.find(f"{W}rPr")) if r.find(f"{W}rPr") is not None else b""
            if prev is not None and prev[0] == key:
                for c in list(r):
                    if c.tag != f"{W}rPr":
                        prev[1].append(c)
                p.remove(r)
            else:
                prev = (key, r)
    return el


def layout_diff(a: bytes, b: bytes) -> list[str]:
    """Every place two files differ in layout: a part, or a body element's leaf.
    The document properties, and the package's pointers to them, are not layout."""
    out = [f"part: {n}" for n in xmlproof.parts_differ(a, b) if not n.startswith("docProps/") and n != "_rels/.rels"]
    xs, ys = xmlproof.body_of(a), xmlproof.body_of(b)
    if len(xs) != len(ys):
        out.append(f"body: {len(xs)} elements and {len(ys)}")
    for i, (x, y) in enumerate(zip(xs, ys)):
        out += [f"body[{i}] {leaf}" for leaf in xmlproof.leaves(_normal(x), _normal(y))]
    return out
