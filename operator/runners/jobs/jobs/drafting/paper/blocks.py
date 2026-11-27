"""A Word file as the ordered list of blocks the paper reader reads.

Read straight from the document XML (python-docx opens the package, lxml walks
it), never through markdown: a block keeps what Word stores and markdown
drops. Each non-empty body paragraph is a block; each table row is one block
(its cells joined by " | ", ``in_table``); each paragraph inside a text box is
a block of its own placed after its anchor paragraph (``in_textbox``), and its
text is not counted in the anchor's. Bold, caps and alignment are resolved the
way Word resolves them: the run or paragraph first, then the style chain.
An auto-numbered paragraph carries the number Word renders for it, computed
from numbering.xml (counters per list, a lower level restarting when a higher
one advances, ``startOverride`` honoured), so an item whose number is not in
its text still reads with its number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .model import Block

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
_OFF = {"0", "false", "off", "none"}


def _on(el: Any) -> bool | None:
    """A toggle property (w:b, w:caps): None when absent, else its value."""
    if el is None:
        return None
    return el.get(f"{_W}val", "true").lower() not in _OFF


@dataclass
class _Styles:
    by_id: dict[str, Any]
    default_para: str | None

    @classmethod
    def of(cls, styles_el: Any) -> _Styles:
        by_id, default = {}, None
        for s in styles_el.iter(f"{_W}style"):
            sid = s.get(f"{_W}styleId")
            by_id[sid] = s
            if s.get(f"{_W}type") == "paragraph" and s.get(f"{_W}default") in ("1", "true"):
                default = sid
        return cls(by_id, default)

    def chain(self, sid: str | None) -> list[Any]:
        out, seen = [], set()
        while sid and sid in self.by_id and sid not in seen:
            seen.add(sid)
            out.append(self.by_id[sid])
            based = self.by_id[sid].find(f"{_W}basedOn")
            sid = None if based is None else based.get(f"{_W}val")
        return out

    def name(self, sid: str | None) -> str:
        s = self.by_id.get(sid or "")
        n = None if s is None else s.find(f"{_W}name")
        from docx.styles import BabelFish

        return "Normal" if n is None else BabelFish.internal2ui(n.get(f"{_W}val", "Normal"))

    def first(self, sid: str | None, path: str) -> Any:
        for s in self.chain(sid):
            hit = s.find(path)
            if hit is not None:
                return hit
        return None


_FMT = {
    "lowerLetter": lambda n: _letters(n).lower(),
    "upperLetter": lambda n: _letters(n),
    "lowerRoman": lambda n: _roman(n).lower(),
    "upperRoman": lambda n: _roman(n),
}


def _letters(n: int) -> str:
    return chr(ord("A") + (n - 1) % 26) * ((n - 1) // 26 + 1)


def _roman(n: int) -> str:
    out = ""
    for v, s in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL")):
        while n >= v:
            out, n = out + s, n - v
    for v, s in ((10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= v:
            out, n = out + s, n - v
    return out


@dataclass
class _Numbering:
    """numbering.xml, and the running counters Word keeps while it renders."""

    abstract: dict[str, Any]
    num: dict[str, Any]
    counters: dict[str, list[int]] = field(default_factory=dict)

    @classmethod
    def of(cls, el: Any) -> _Numbering:
        if el is None:
            return cls({}, {})
        abstract = {a.get(f"{_W}abstractNumId"): a for a in el.iter(f"{_W}abstractNum")}
        return cls(abstract, {n.get(f"{_W}numId"): n for n in el.iter(f"{_W}num")})

    def _lvl(self, num_id: str, ilvl: int) -> tuple[Any, int | None]:
        num = self.num.get(num_id)
        if num is None:
            return None, None
        start = None
        for ov in num.iter(f"{_W}lvlOverride"):
            if ov.get(f"{_W}ilvl") == str(ilvl):
                so = ov.find(f"{_W}startOverride")
                start = None if so is None else int(so.get(f"{_W}val", "1"))
        aid = num.find(f"{_W}abstractNumId")
        abstract = None if aid is None else self.abstract.get(aid.get(f"{_W}val"))
        if abstract is None:
            return None, start
        lvl = next((lv for lv in abstract.iter(f"{_W}lvl") if lv.get(f"{_W}ilvl") == str(ilvl)), None)
        return lvl, start

    def _start(self, num_id: str, ilvl: int) -> int:
        lvl, override = self._lvl(num_id, ilvl)
        if override is not None:
            return override
        st = None if lvl is None else lvl.find(f"{_W}start")
        return 1 if st is None else int(st.get(f"{_W}val", "1"))

    def render(self, num_id: str, ilvl: int) -> str | None:
        """Advance the list's counter at ``ilvl`` and return the text Word shows."""
        lvl, _ = self._lvl(num_id, ilvl)
        if lvl is None or num_id == "0":
            return None
        fmt_el = lvl.find(f"{_W}numFmt")
        fmt = "decimal" if fmt_el is None else fmt_el.get(f"{_W}val", "decimal")
        if fmt in ("bullet", "none"):
            return None
        ctr = self.counters.setdefault(num_id, [0] * 9)
        ctr[ilvl] = self._start(num_id, ilvl) if ctr[ilvl] == 0 else ctr[ilvl] + 1
        for deeper in range(ilvl + 1, 9):
            ctr[deeper] = 0
        text_el = lvl.find(f"{_W}lvlText")
        text = f"%{ilvl + 1}." if text_el is None else text_el.get(f"{_W}val", "")
        for k in range(ilvl + 1):
            fmt_k = self._fmt_at(num_id, k)
            text = text.replace(f"%{k + 1}", _FMT.get(fmt_k, str)(max(ctr[k], 1)))
        return text.strip()

    def _fmt_at(self, num_id: str, ilvl: int) -> str:
        lvl, _ = self._lvl(num_id, ilvl)
        el = None if lvl is None else lvl.find(f"{_W}numFmt")
        return "decimal" if el is None else el.get(f"{_W}val", "decimal")


def _in_box(el: Any, p: Any) -> bool:
    """True when ``el`` sits in a text box anchored inside ``p`` (not when
    ``p`` is itself a text-box paragraph)."""
    for a in el.iterancestors():
        if a is p:
            return False
        if a.tag == f"{_W}txbxContent":
            return True
    return False


def _own_text(p: Any) -> str:
    """The paragraph's text, without any text box anchored in it."""
    parts: list[str] = []
    for el in p.iter(f"{_W}t", f"{_W}tab", f"{_W}br"):
        if _in_box(el, p):
            continue
        parts.append(el.text or "" if el.tag == f"{_W}t" else " ")
    return " ".join("".join(parts).split())


def _runs(p: Any) -> list[Any]:
    return [
        r for r in p.iter(f"{_W}r") if not _in_box(r, p) and "".join(t.text or "" for t in r.iter(f"{_W}t")).strip()
    ]


def _toggle(p: Any, styles: _Styles, prop: str, sid: str | None) -> bool:
    """True when every visible run is ``prop`` (bold, caps) as Word resolves it."""
    runs = _runs(p)
    if not runs:
        return False
    para_default = _on(styles.first(sid, f"{_W}rPr/{_W}{prop}")) or False
    for r in runs:
        rpr = r.find(f"{_W}rPr")
        direct = None if rpr is None else _on(rpr.find(f"{_W}{prop}"))
        if direct is None and rpr is not None:
            rs = rpr.find(f"{_W}rStyle")
            if rs is not None:
                direct = _on(styles.first(rs.get(f"{_W}val"), f"{_W}rPr/{_W}{prop}"))
        if not (para_default if direct is None else direct):
            return False
    return True


def _para_block(bid: str, p: Any, st: _Styles, nb: _Numbering, **flags: bool) -> Block | None:
    text = _own_text(p)
    ppr = p.find(f"{_W}pPr")
    ps = None if ppr is None else ppr.find(f"{_W}pStyle")
    sid = st.default_para if ps is None else ps.get(f"{_W}val")
    num_pr = None if ppr is None else ppr.find(f"{_W}numPr")
    if num_pr is None:
        num_pr = st.first(sid, f"{_W}pPr/{_W}numPr")
    numbered = num_id = level = None
    if num_pr is not None:
        nid, il = num_pr.find(f"{_W}numId"), num_pr.find(f"{_W}ilvl")
        num_id = None if nid is None else nid.get(f"{_W}val")
        level = 0 if il is None else int(il.get(f"{_W}val", "0"))
        if num_id is not None:
            numbered = nb.render(num_id, level)
    if not text and not numbered:
        return None
    jc = None if ppr is None else ppr.find(f"{_W}jc")
    jc = jc if jc is not None else st.first(sid, f"{_W}pPr/{_W}jc")
    outline = None if ppr is None else ppr.find(f"{_W}outlineLvl")
    outline = outline if outline is not None else st.first(sid, f"{_W}pPr/{_W}outlineLvl")
    name = st.name(sid)
    heading = int(outline.get(f"{_W}val", "9")) + 1 if outline is not None else 0
    if not heading and name.lower().startswith("heading") and name[-1:].isdigit():
        heading = int(name[-1])
    letters = [c for c in text if c.isalpha()]
    caps = bool(letters) and (all(c.isupper() for c in letters) or _toggle(p, st, "caps", sid))
    return Block(
        id=bid,
        text=text,
        bold=_toggle(p, st, "b", sid),
        caps=caps,
        style=name,
        numbered=numbered,
        num_id=num_id,
        num_level=level,
        align="left" if jc is None else jc.get(f"{_W}val", "left"),
        heading=heading if 0 < heading <= 9 else 0,
        **flags,
    )


def _textboxes(p: Any) -> list[Any]:
    """The text-box paragraphs anchored in ``p``, once each (the VML fallback
    copy Word writes beside a drawing is skipped)."""
    out = []
    for box in p.iter(f"{_W}txbxContent"):
        if any(a.tag == _MC_FALLBACK for a in box.iterancestors()):
            continue
        out += list(box.iter(f"{_W}p"))
    return out


def _row_block(bid: str, row: Any) -> Block | None:
    cells = [" ".join("".join(t.text or "" for t in c.iter(f"{_W}t")).split()) for c in row.iter(f"{_W}tc")]
    text = " | ".join(c for c in cells if c)
    if not text:
        return None
    letters = [c for c in text if c.isalpha()]
    return Block(id=bid, text=text, caps=bool(letters) and all(c.isupper() for c in letters), in_table=True)


def from_document(doc: Any) -> list[Block]:
    """The blocks of an open python-docx Document, in body order."""
    st = _Styles.of(doc.styles.element)
    nb = _Numbering.of(doc.part.numbering_part.element if _has_numbering(doc) else None)
    out: list[Block] = []
    for i, el in enumerate(doc.element.body.iterchildren()):
        if el.tag == f"{_W}p":
            b = _para_block(f"p{i}", el, st, nb)
            out += [b] if b else []
            for k, tp in enumerate(_textboxes(el)):
                tb = _para_block(f"p{i}.x{k}", tp, st, nb, in_textbox=True)
                out += [tb] if tb else []
        elif el.tag == f"{_W}tbl":
            for j, row in enumerate(el.iterchildren(f"{_W}tr")):
                rb = _row_block(f"t{i}.r{j}", row)
                out += [rb] if rb else []
    return out


def _has_numbering(doc: Any) -> bool:
    from docx.opc.constants import RELATIONSHIP_TYPE as RT

    return any(rel.reltype == RT.NUMBERING for rel in doc.part.rels.values())


def read_docx(path: Path) -> list[Block]:
    import docx

    return from_document(docx.Document(str(path)))
