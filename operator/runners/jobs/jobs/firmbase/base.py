"""The base, its slots, and a fill.

A slot is marked in the adopted file's own body:

* ``para_proto``: a paragraph whose text reads ``[[name]]``, in one run or split across
  runs by a save in Word (``markers`` reads it, ``merge`` joins it). It may stand several
  times; ``put`` pours lines into its instances in order, extra lines clone the
  last, spare ones go (or stay as blank lines, ``spare="blank"``, where a blank line
  holds the layout).
* ``table_proto``: a table whose every written cell reads the same ``[[name]]``. Its
  rows are the header, the body rows taken in turn, and the total, last.
* ``field``: ``{{name}}`` in a paragraph's text, filled in place (the firm's form
  fields; ``form_docx``).
* ``region``: the span of protos named ``<region>.<member>``, one of each. ``put``
  replaces the span with a clone of the named member per item, in the order given.
  A blank paragraph right under a table proto in the span follows each clone of it.

Everything else in the file is the firm's and stays. ``Fill`` records an ``Allow``
per element a put touched, in the shape ``xmlproof.prove_body`` reads, and ``save``
proves the result (``proof.py``).
"""

from __future__ import annotations

import builtins
import copy
import io
import re
import zipfile
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Iterable

from lxml import etree
from smokeball_connector.form_docx import paragraph_text, replace_in_paragraph

from . import markers, merge, xmlproof
from .markers import RPR, SLOT, W
from .xmlproof import Allow

DOCUMENT = "word/document.xml"
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
FIELD = re.compile(r"\{\{([a-z][a-z0-9._]*)\}\}")
EMPHASIS = ("b", "bCs", "i", "iCs")


class FirmBaseUnfit(RuntimeError):
    """The content has what the base cannot place, or the file is no base."""


class FirmBaseLost(RuntimeError):
    """The result is not the base with its slots filled: nothing is filed."""


@dataclass(frozen=True)
class Source:
    """Where the firm's file came from. ``version`` and ``accepted`` record an attorney's
    accepted save: which saved version, and the evidence he accepted it (his own Word
    save, or his acceptance in writing). Only such a record lets a file with Operator
    lineage be a base (``adopt.may_adopt``)."""

    matter_id: str
    file_id: str
    name: str
    sha256: str
    version: str = ""
    accepted: str = ""


@dataclass(frozen=True)
class Slot:
    name: str
    kind: str  # para_proto | table_proto | field | region


@dataclass(frozen=True)
class Run:
    text: str
    bold: bool = False
    italic: bool = False
    marker: bool = False


#: One line of content: runs with their emphasis.
Line = tuple[Any, ...]


@dataclass(frozen=True)
class Rows:
    """A table's content. ``header``: the first row is the proto's header row.
    ``total(row)``: the row takes the proto's total row. ``narrow``: fewer columns than
    the proto may take columns from it."""

    rows: tuple[tuple[Line, ...], ...]
    header: bool = False
    total: Any = None
    narrow: bool = False


def slot_names(el: Any) -> list[str]:
    """The para slots an element's own text holds, in order, one text run each: the
    single-run reader. firmbase reads with ``markers.marked``, which also reads a
    marker Word split and refuses structure no Word-saved fixture shows."""
    return [m.group(1) for t in el.iter(f"{W}t") for m in [SLOT.match(t.text or "")] if m]


def is_blank(el: Any) -> bool:
    return el.tag == f"{W}p" and not "".join(t.text or "" for t in el.iter(f"{W}t")).strip()


def _is_table_proto(tbl: Any) -> str | None:
    names = set(markers.marked(tbl))
    return names.pop() if len(names) == 1 and "." in next(iter(names)) and _only_cells(tbl) else None


def _only_cells(tbl: Any) -> bool:
    """Every paragraph with words holds the slot: a table proto is all slot."""
    return all(markers.marked(p) or is_blank(p) for p in tbl.iter(f"{W}p"))


def _is_member(name: str) -> bool:
    return name.startswith("body.")


def _pour_table(tbl: Any) -> bool:
    """A table of per-column slots, poured in place: a caption."""
    return len(set(markers.marked(tbl))) > 1


def scan(root: Any) -> dict[str, Slot]:
    """Every slot the file's body marks."""
    body = root.find(f"{W}body")
    slots: dict[str, Slot] = {}
    for el in body:
        if el.tag == f"{W}tbl" and _is_member(_is_table_proto(el) or ""):
            slots[_is_table_proto(el)] = Slot(_is_table_proto(el), "table_proto")  # type: ignore[arg-type]
            continue
        for name in markers.marked(el):
            slots[name] = Slot(name, "para_proto")
        for p in el.iter(f"{W}p"):
            for name in FIELD.findall(paragraph_text(p)):
                slots[name] = Slot(name, "field")
    if any(_is_member(n) for n in slots):
        slots["body"] = Slot("body", "region")
    return slots


def region_span(kids: list[Any]) -> tuple[int, int] | None:
    """(first, last) index of the region's members and the blanks under its tables."""
    at = [i for i, e in enumerate(kids) if any(_is_member(n) for n in markers.marked(e))]
    if not at:
        return None
    last = at[-1]
    if kids[last].tag == f"{W}tbl" and last + 1 < len(kids) and is_blank(kids[last + 1]):
        last += 1
    return at[0], last


@dataclass(frozen=True)
class FirmBase:
    cls: str
    source: Source
    bytes: builtins.bytes
    slots: dict[str, Slot] = field(default_factory=dict)

    @classmethod
    def load(cls, doc_class: str, source: Source, blob: builtins.bytes) -> "FirmBase":
        root = etree.fromstring(zipfile.ZipFile(io.BytesIO(blob)).read(DOCUMENT))
        slots = scan(root)
        if not slots:
            raise FirmBaseUnfit("the file marks no slots")
        return cls(doc_class, source, blob, slots)


def _repeated_points(body: Any) -> list[str]:
    """A bookmark in a line the region repeats would stand once per copy, or not at all."""
    span = region_span(list(body))
    zone = list(body)[span[0] : span[1] + 1] if span else []
    return [
        "a bookmark sits in a line the Operator repeats" for el in zone if el.find(f".//{W}bookmarkStart") is not None
    ][:1]


def open(base: FirmBase) -> "Fill":
    return Fill(base)


# ---- writing a line -------------------------------------------------------------------
def _text_el(text: str) -> Any:
    t = etree.Element(f"{W}t")
    t.text = text
    t.set(_XML_SPACE, "preserve")
    return t


def _put_child(parent: Any, tag: str, order: tuple[str, ...]) -> None:
    for old in parent.findall(f"{W}{tag}"):
        parent.remove(old)
    later = {f"{W}{t}" for t in order[order.index(tag) + 1 :]}
    el = etree.Element(f"{W}{tag}")
    for child in parent:
        if child.tag in later:
            child.addprevious(el)
            return
    parent.append(el)


def _styled(base: Any, run: Any, carry: bool) -> Any:
    """The slot's rPr with the run's emphasis. ``carry``: the slot's own emphasis
    stays (a chart's bold total, a heading) and the content's is added to it."""
    rpr = copy.deepcopy(base) if base is not None else etree.Element(f"{W}rPr")
    if not carry:
        for tag in EMPHASIS:
            for old in rpr.findall(f"{W}{tag}"):
                rpr.remove(old)
    if not getattr(run, "marker", False):
        for tag, on in (("b", run.bold), ("i", run.italic)):
            if on and rpr.find(f"{W}{tag}") is None:
                _put_child(rpr, tag, RPR)
    return rpr


def _slot_run(p: Any) -> Any:
    for r in p.findall(f"{W}r"):
        if r.find(f"{W}t") is not None:
            return r
    raise FirmBaseUnfit("a slot paragraph has no run")


def fill_para(p: Any, runs: Iterable[Any], carry: bool = False) -> None:
    """The slot run becomes these runs, each on the slot's rPr, its tabs kept on the
    first. No runs: a blank line on the paragraph's own properties. A marker Word
    split is merged into the run holding its first character first (``merge``)."""
    reading = markers.read(p)
    if reading.refusal:
        raise FirmBaseUnfit(markers.unwritable(p)[0])
    slot = merge.collapse(p, reading) if reading.merge else _slot_run(p)
    base, tabs = slot.find(f"{W}rPr"), [copy.deepcopy(c) for c in slot if c.tag == f"{W}tab"]
    at = slot
    for n, run in enumerate(runs):
        r = etree.Element(f"{W}r")
        rpr = _styled(base, run, carry)
        if len(rpr):
            r.append(rpr)
        for tab in tabs if n == 0 else []:
            r.append(tab)
        r.append(_text_el(run.text))
        at.addnext(r)
        at = r
    slot.getparent().remove(slot)


def _column_widths(grid: list[int], n: int) -> list[int]:
    """The table's width held, its first column keeping its ratio to the others."""
    total, ratio = sum(grid), grid[0] / grid[-1]
    other = round(total / (ratio + n - 1))
    return [total - other * (n - 1), *[other] * (n - 1)]


def _set_ppr(p: Any, ppr: Any) -> None:
    """``p`` on a copy of ``ppr``; a proto cell with no properties leaves it none."""
    for old in p.findall(f"{W}pPr"):
        p.remove(old)
    if ppr is not None:
        p.insert(0, copy.deepcopy(ppr))


def _widen(tbl: Any, n: int, align_right: list[bool], header: bool) -> None:
    """More columns than the proto: its first cell repeats up to its last, each body
    cell takes its first or last column's alignment by ``align_right``."""
    grid = tbl.find(f"{W}tblGrid")
    cols = grid.findall(f"{W}gridCol")
    widths = _column_widths([int(c.get(f"{W}w")) for c in cols], n)
    for col in cols:
        grid.remove(col)
    for w in widths:
        grid.append(etree.Element(f"{W}gridCol", {f"{W}w": str(w)}))
    for r, tr in enumerate(tbl.findall(f"{W}tr")):
        tcs = tr.findall(f"{W}tc")
        left, right = tcs[0].find(f"{W}p/{W}pPr"), tcs[-1].find(f"{W}p/{W}pPr")
        for _ in range(n - len(tcs)):
            tcs[-1].addprevious(copy.deepcopy(tcs[0]))
        for c, tc in enumerate(tr.findall(f"{W}tc")):
            width = tc.find(f"{W}tcPr/{W}tcW")
            if width is not None:
                width.set(f"{W}w", str(widths[c]))
            if not (r == 0 and header):
                _set_ppr(tc.find(f"{W}p"), right if align_right[c] else left)


def _narrow(tbl: Any, n: int) -> None:
    """Fewer columns than the proto: its width held, the columns before its last go."""
    grid = tbl.find(f"{W}tblGrid")
    cols = grid.findall(f"{W}gridCol")
    widths = _column_widths([int(c.get(f"{W}w")) for c in cols], n)
    for col in cols:
        grid.remove(col)
    for w in widths:
        grid.append(etree.Element(f"{W}gridCol", {f"{W}w": str(w)}))
    for tr in tbl.findall(f"{W}tr"):
        tcs = tr.findall(f"{W}tc")
        for tc in tcs[n - 1 : -1] if n > 1 else tcs[1:]:
            tr.remove(tc)
        for c, tc in enumerate(tr.findall(f"{W}tc")):
            width = tc.find(f"{W}tcPr/{W}tcW")
            if width is not None:
                width.set(f"{W}w", str(widths[c]))


_MONEY = re.compile(r"^\$?\s*[\d,]+(\.\d+)?$")


def _money_columns(content: Rows, n: int) -> list[bool]:
    """Columns whose every written value is an amount; a slot not yet filled says nothing."""
    out = []
    rows = content.rows[1 if content.header else 0 :]
    for c in range(n):
        vals = [
            "".join(x.text for x in r[c]).strip()
            for r in rows
            if c < len(r) and not any(getattr(x, "marker", False) for x in r[c])
        ]
        vals = [v for v in vals if v]
        out.append(bool(vals) and all(_MONEY.match(v) for v in vals))
    return out


def make_table(proto: Any, content: Rows) -> Any:
    """A clone of the proto, one row per row: header, the body rows in turn, total."""
    t = copy.deepcopy(proto)
    head, *bands, total = t.findall(f"{W}tr")
    if not bands:
        raise FirmBaseUnfit("a table proto has no body row")
    made, band = [], 0
    for i, row in enumerate(content.rows):
        if i == 0 and content.header:
            src = head
        elif content.total is not None and content.total(row):
            src = total
        else:
            src, band = bands[band % len(bands)], band + 1
        made.append(copy.deepcopy(src))
    for tr in [head, *bands, total]:
        t.remove(tr)
    for tr in made:
        t.append(tr)
    n = max(len(r) for r in content.rows)
    cols = len(t.find(f"{W}tblGrid").findall(f"{W}gridCol"))
    if n < cols and not content.narrow:
        raise FirmBaseUnfit(f"a table of {n} columns and a proto of {cols}")
    if n < cols:
        _narrow(t, n)
    if n > cols:
        _widen(t, n, _money_columns(content, n), content.header)
    for tr, row in zip(made, content.rows):
        for c, tc in enumerate(tr.findall(f"{W}tc")):
            paras = tc.findall(f"{W}p")
            for extra in paras[1:]:
                tc.remove(extra)
            fill_para(paras[0], row[c] if c < len(row) else (), carry=True)
    return t


# ---- the fill -------------------------------------------------------------------------
class Fill:
    """An open copy of the base. ``doc``, ``allowed``, ``added`` and ``removed`` are the
    shape ``xmlproof.prove_body`` reads."""

    def __init__(self, base: FirmBase) -> None:
        self.base = base
        self.root = etree.fromstring(zipfile.ZipFile(io.BytesIO(base.bytes)).read(DOCUMENT))
        self.body = self.root.find(f"{W}body")
        self.doc = SimpleNamespace(element=SimpleNamespace(body=self.body))
        self.allowed: dict[Any, list[Any]] = {}
        self.added: set[Any] = set()
        self.removed = 0
        self.filled: list[str] = []
        self.before = {el: xmlproof.canon(el).decode("utf-8") for el in self.body}
        refused = markers.unwritable(self.body) + _repeated_points(self.body)
        if refused:
            raise FirmBaseUnfit("the file cannot be written into: " + "; ".join(refused))

    def top(self, el: Any) -> Any:
        while el.getparent() is not None and el.getparent() is not self.body:
            el = el.getparent()
        return el

    def _mark(self, el: Any) -> None:
        top = self.top(el)
        self.allowed.setdefault(top, []).append(Allow.TABLE_TEXT if top.tag == f"{W}tbl" else Allow.RUNS)
        if markers.read(el).merge:
            self.allowed[top].append(Allow.MARKER_LINE)

    def put(self, name: str, content: Any, *, spare: str = "remove", carry: bool = False) -> None:
        slot = self.base.slots.get(name)
        if slot is None:
            raise FirmBaseUnfit(f"the base has no slot {name}")
        if slot.kind == "para_proto":
            self._put_lines(name, content, spare, carry)
        elif slot.kind == "field":
            self._put_field(name, content)
        elif slot.kind == "region":
            self._put_region(name, content)
        else:
            raise FirmBaseUnfit(f"{name} is a table proto: put it through its region")
        self.filled.append(name)

    def _instances(self, name: str) -> list[Any]:
        return [p for p in self.body.iter(f"{W}p") if markers.marked(p) == [name]]

    def _put_lines(self, name: str, lines: list[Line], spare: str, carry: bool) -> None:
        slots = self._instances(name)
        if not slots:
            raise FirmBaseUnfit(f"the base has no {name} line left")
        while len(slots) < len(lines):
            twin = copy.deepcopy(slots[-1])
            for point in list(twin.iter(f"{W}bookmarkStart", f"{W}bookmarkEnd")):
                point.getparent().remove(point)  # the firm's bookmark stays on its own line only
            slots[-1].addnext(twin)
            if twin.getparent() is self.body:
                self.added.add(twin)
            slots.append(twin)
        for p, runs in zip(slots, lines):
            self._mark(p)
            fill_para(p, runs, carry)
        for p in slots[len(lines) :]:
            self._mark(p)
            if spare == "blank":
                fill_para(p, ())
            else:
                top_level = p.getparent() is self.body
                p.getparent().remove(p)
                self.removed += 1 if top_level else 0

    def _put_field(self, name: str, value: str) -> None:
        token = "{{" + name + "}}"
        hits = [p for p in self.body.iter(f"{W}p") if token in paragraph_text(p)]
        for p in hits:
            self._mark(p)
            while token in paragraph_text(p):
                replace_in_paragraph(p, token, value)

    def _put_region(self, name: str, items: list[tuple[str, Any]]) -> None:
        kids = list(self.body)
        span = region_span(kids)
        if span is None:
            raise FirmBaseUnfit(f"the base has no region {name}")
        first, last = span
        zone = kids[first : last + 1]
        protos = {markers.marked(e)[0]: e for e in zone if markers.marked(e)}
        spacer = {
            markers.marked(e)[0]: zone[i + 1]
            for i, e in enumerate(zone[:-1])
            if e.tag == f"{W}tbl" and is_blank(zone[i + 1])
        }
        anchor = kids[last + 1]
        for member, content in items:
            if member not in protos:
                raise FirmBaseUnfit(f"the content uses {member} and the base has no such element")
            proto = protos[member]
            if proto.tag == f"{W}tbl":
                els = [make_table(proto, content)]
                els += [copy.deepcopy(spacer[member])] if member in spacer else []
            else:
                el = copy.deepcopy(proto)
                fill_para(el, content, carry=member.split(".")[-1].startswith("h"))
                els = [el]
            for el in els:
                anchor.addprevious(el)
                self.added.add(el)
            self.filled.append(member)
        for el in zone:
            self.body.remove(el)
            self.removed += 1

    def save(self) -> tuple[builtins.bytes, Any]:
        """(the file, its proof). The caller files nothing on a non-empty proof list."""
        from .proof import prove

        blob = package(self.base.bytes, self.root)
        return blob, prove(self.base, blob, self)


def package(blob: builtins.bytes, root: Any) -> builtins.bytes:
    """Every part of the base by its bytes, the new body beside them."""
    data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(blob)) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            out.writestr(info, data if info.filename == DOCUMENT else src.read(info.filename))
    return buf.getvalue()
