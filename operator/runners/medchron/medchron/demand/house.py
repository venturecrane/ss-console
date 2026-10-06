"""Render a demand INTO the firm's own house demand file (.docx).

Ported from the engagements laptop tool ``house_docx.py`` (2026-09-28). The
format is not re-created here, it is COPIED: the firm's reference document is
one of its own demands with every word of client content removed and one
prototype of each paragraph and table kind left behind, tagged ``PROTO:<kind>``.
The renderer clones those prototypes, so the firm's fonts, spacing, banner,
table borders and footer arrive byte for byte. The reference is a firm input
(``inputs.house_reference``), never a file in this repo.

What changed from the laptop tool: the firm's signature line and the signer's
title come from the firm config instead of being written into the code, and the
renderer is split so each piece stays under the function-size ceiling.

Input is markdown with a front-matter block (date, attn, client, insured,
claim, dol, banner, expires, salutation, signer), then ``## Section``,
``### Sub-head``, ``- bullet``, ``> quote``, the firm's three tables, and
paragraphs. ``## Exhibits`` starts the exhibit list and the signature block is
inserted before it. Everything after ``=== ATTORNEY NOTES ===`` goes to a
separate notes text, never into the demand.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

KINDS = (
    "date",
    "blank",
    "attn",
    "re_first",
    "re_line",
    "banner",
    "salute",
    "body",
    "heading",
    "icd",
    "subhead",
    "bullet",
    "quote",
    "totalhead",
    "specials",
    "demand",
    "sign",
    "signfirm",
    "exh_head",
    "exh_intro",
    "exh_item",
)
NOTES_MARK = "=== ATTORNEY NOTES ==="
FRONT_KEYS = ("date", "attn", "client", "insured", "claim", "dol", "banner", "expires", "salutation", "signer")


class RenderError(ValueError):
    pass


def _qn(tag: str) -> str:
    from docx.oxml.ns import qn

    return qn(tag)


def _first_rpr(p_el: Any) -> Any:
    r = p_el.find(_qn("w:r"))
    rpr = r.find(_qn("w:rPr")) if r is not None else None
    return copy.deepcopy(rpr) if rpr is not None else None


def _run(p_el: Any, rpr: Any, piece: str, bold: bool, italic: bool) -> Any:
    r = p_el.makeelement(_qn("w:r"), {})
    rp = copy.deepcopy(rpr) if rpr is not None else r.makeelement(_qn("w:rPr"), {})
    for tag in ("w:b", "w:bCs", "w:i", "w:iCs"):
        e = rp.find(_qn(tag))
        if e is not None:
            rp.remove(e)
    for flag, tags in ((bold, ("w:b", "w:bCs")), (italic, ("w:i", "w:iCs"))):
        if flag:
            for tag in tags:
                rp.append(rp.makeelement(_qn(tag), {}))
    r.append(rp)
    te = r.makeelement(_qn("w:t"), {_qn("xml:space"): "preserve"})
    te.text = piece
    r.append(te)
    return r


def set_runs(p_el: Any, text: str, rpr: Any = None) -> Any:
    rpr = rpr if rpr is not None else _first_rpr(p_el)
    for r in list(p_el):
        if r.tag != _qn("w:pPr"):
            p_el.remove(r)
    for seg in re.split(r"(\*\*.+?\*\*|\*.+?\*)", text):
        if not seg:
            continue
        b = seg.startswith("**")
        i = (not b) and seg.startswith("*")
        t = seg[2:-2] if b else (seg[1:-1] if i else seg)
        for j, piece in enumerate(t.split("\t")):
            if j:
                tab = p_el.makeelement(_qn("w:r"), {})
                tab.append(tab.makeelement(_qn("w:tab"), {}))
                p_el.append(tab)
            if piece:
                p_el.append(_run(p_el, rpr, piece, b, i))
    return p_el


def _keep_next(p_el: Any) -> None:
    ppr = p_el.find(_qn("w:pPr"))
    if ppr is None:
        ppr = p_el.makeelement(_qn("w:pPr"), {})
        p_el.insert(0, ppr)
    if ppr.find(_qn("w:keepNext")) is None:
        ppr.insert(0, ppr.makeelement(_qn("w:keepNext"), {}))


def _text(el: Any) -> str:
    return "".join(t.text or "" for t in el.iter(_qn("w:t")))


class HouseWriter:
    def __init__(self, reference: Path) -> None:
        import docx

        self.doc = docx.Document(str(reference))
        body = self.doc.element.body
        self.body = body
        kids = list(body.iterchildren())
        self.sect = kids[-1] if kids and kids[-1].tag == _qn("w:sectPr") else None
        self.proto: dict[str, Any] = {}
        for k in kids:
            m = re.match(r"PROTO:(\w+)", _text(k).strip())
            if m:
                self.proto[m.group(1)] = copy.deepcopy(k)
        missing = [k for k in KINDS if k not in self.proto]
        if missing:
            raise RenderError(f"the house reference lacks prototypes: {missing}")
        self.body_rpr = _first_rpr(self.proto["body"])
        for k in kids:
            if k is not self.sect:
                body.remove(k)

    def _insert(self, el: Any) -> None:
        self.body.insert(len(self.body) - 1, el)

    def para(self, kind: str, text: str = "") -> Any:
        el = copy.deepcopy(self.proto[kind])
        if kind == "blank":
            for r in list(el):
                if r.tag != _qn("w:pPr"):
                    el.remove(r)
        else:
            use_body = kind in ("body", "bullet", "exh_item", "exh_intro")
            set_runs(el, text, self.body_rpr if use_body else None)
        self._insert(el)
        return el

    def _cell(self, tc: Any, text: str) -> None:
        ps = tc.findall(_qn("w:p"))
        for extra in ps[1:]:
            tc.remove(extra)
        set_runs(ps[0], text)

    def table(self, kind: str, header: list[str], rows: list[list[str]], total_last: bool = False) -> None:
        t = copy.deepcopy(self.proto[kind])
        trs = t.findall(_qn("w:tr"))
        head, body_row, total_row = copy.deepcopy(trs[0]), trs[1], trs[-1]
        for tr in trs:
            t.remove(tr)
        for tc, val in zip(head.findall(_qn("w:tc")), header):
            self._cell(tc, val)
        for p_el in head.iter(_qn("w:p")):
            _keep_next(p_el)
        prev = self.body[len(self.body) - 2] if len(self.body) >= 2 else None
        if prev is not None and prev.tag == _qn("w:p"):
            _keep_next(prev)
        t.append(head)
        for n, row in enumerate(rows):
            tr = copy.deepcopy(total_row if (total_last and n == len(rows) - 1) else body_row)
            cells = tr.findall(_qn("w:tc"))
            if len(cells) != len(row):
                raise RenderError(f"a {kind} table row has {len(row)} cells; the firm's table has {len(cells)}")
            for tc, val in zip(cells, row):
                self._cell(tc, val)
            t.append(tr)
        self._insert(t)
        self.para("blank")

    def banner(self, line1: str, line2: str) -> None:
        t = copy.deepcopy(self.proto["banner"])
        for tr, val in zip(t.findall(_qn("w:tr")), (line1, line2)):
            self._cell(tr.find(_qn("w:tc")), val)
        self._insert(t)

    def save(self, out: Path, title: str, author: str) -> None:
        cp = self.doc.core_properties
        cp.title, cp.subject, cp.keywords, cp.comments, cp.category = title, "", "", "", ""
        cp.author, cp.last_modified_by = author, author
        strip_custom_properties(self.doc)
        self.doc.save(str(out))


def strip_custom_properties(doc: Any) -> None:
    """docProps/custom.xml carries the SOURCE document's matter and file ids."""
    pkg = doc.part.package
    for rid, rel in list(pkg.rels.items()):
        if rel.reltype.endswith("/custom-properties"):
            del pkg.rels[rid]


# ---- markdown in ------------------------------------------------------------------
def parse(md: str) -> tuple[dict[str, str], str, str]:
    notes = ""
    if NOTES_MARK in md:
        md, notes = md.split(NOTES_MARK, 1)
    m = re.match(r"\s*(?:```[a-z]*\n)?---\n(.*?)\n---\n", md, re.S)
    if not m:
        raise RenderError("the draft has no front-matter block (--- date/attn/client/... ---)")
    front = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            front[k.strip()] = v.strip()
    missing = [k for k in FRONT_KEYS if not front.get(k)]
    if missing:
        raise RenderError(f"front matter missing: {missing}")
    return front, md[m.end() :], notes.strip()


def table_kind(header: list[str]) -> str:
    h = [c.strip().lower() for c in header]
    if h and h[0].startswith("icd"):
        return "icd"
    if len(h) == 2 and h[0] == "provider":
        return "specials"
    if len(h) == 3 and h[0] == "category":
        return "demand"
    raise RenderError(
        f"no firm table for header {header}: the firm's tables are ICD-10 / Provider-Amount / Category-Description-Amount"
    )


def out_name(client: str) -> str:
    safe = re.sub(r'[\\/:*?"<>|]', "", client).strip()
    return f"Demand.{safe}.docx"


def _header(w: HouseWriter, front: dict[str, str]) -> None:
    w.para("blank")
    w.para("blank")
    w.para("date", front["date"])
    w.para("blank")
    for line in front["attn"].split("|"):
        w.para("attn", line.strip())
    w.para("blank")
    w.para("re_first", f"**RE:\tOur Client:\t{front['client']}**")
    w.para("re_line", f"\tYour Insured:\t{front['insured']}")
    w.para("re_line", f"\tClaim Number:\t{front['claim']}")
    w.para("re_line", f"\tDate of Loss:\t{front['dol']}")
    w.para("blank")
    w.banner(front["banner"], front["expires"])
    w.para("blank")
    w.para("salute", front["salutation"])


class _Body:
    """The letter body, line by line, into the writer."""

    def __init__(self, w: HouseWriter, front: dict[str, str], signature: str, title: str) -> None:
        self.w, self.front, self.signature, self.title = w, front, signature, title
        self.in_exhibits = self.exh_intro_done = self.signed = False

    def sign(self) -> None:
        w = self.w
        w.para("blank")
        w.para("sign", "Cordially,")
        w.para("signfirm", f"**{self.signature}**")
        w.para("sign", self.front["signer"])
        w.para("sign", self.front.get("signer_title") or self.title)
        self.signed = True

    def heading(self, line: str) -> None:
        if line.startswith("## "):
            title = line[3:].strip()
            if title.lower() == "exhibits":
                if not self.signed:
                    self.sign()
                self.w.para("exh_head", title)
                self.in_exhibits = True
            else:
                self.w.para("heading", title)
        else:
            title = line[4:].strip()
            self.w.para("totalhead" if title.startswith("Total Medical Specials") else "subhead", f"**{title}**")

    def table(self, block: list[list[str]]) -> None:
        block = [r for r in block if not all(re.fullmatch(r":?-{2,}:?", c) for c in r)]
        kind = table_kind(block[0])
        rows = block[1:]
        total = kind == "specials" and bool(rows) and rows[-1][0].strip("* ").lower().startswith("total")
        if total:
            rows[-1] = [c.replace("**", "") for c in rows[-1]]
        self.w.table(kind, block[0], rows, total_last=total)

    def paragraph(self, text: str) -> None:
        if not self.in_exhibits:
            self.w.para("body", text)
        elif not self.exh_intro_done and not text.startswith("**Exhibit"):
            self.w.para("exh_intro", text)
            self.exh_intro_done = True
        else:
            self.w.para("exh_item", text)

    def feed(self, lines: list[str]) -> None:
        i = 0
        while i < len(lines):
            line = lines[i].rstrip()
            if not line.strip():
                i += 1
            elif line.startswith(("## ", "### ")):
                self.heading(line)
                i += 1
            elif line.startswith("|"):
                block = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    block.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                    i += 1
                self.table(block)
            elif line.startswith(("> ", "- ")):
                kind = "quote" if line.startswith("> ") else "bullet"
                self.w.para(kind, ("*" + line[2:].strip().strip("*") + "*") if kind == "quote" else line[2:].strip())
                i += 1
            else:
                buf = [line.strip()]
                i += 1
                while i < len(lines) and lines[i].strip() and not re.match(r"(#{2,3} |\||> |- )", lines[i]):
                    buf.append(lines[i].strip())
                    i += 1
                self.paragraph(" ".join(buf))
        if not self.signed:
            self.sign()


def render(
    md: str, reference: Path, out_dir: Path, *, signature: str, signer_title: str, author: str
) -> tuple[Path, str]:
    """``(the demand file, the attorney notes text)``."""
    front, body, notes = parse(md)
    w = HouseWriter(reference)
    _header(w, front)
    _Body(w, front, signature, signer_title).feed(body.strip("\n").splitlines())
    out_dir.mkdir(parents=True, exist_ok=True)
    name = out_name(front["client"])
    out = out_dir / name
    w.save(out, title=name[:-5], author=author)
    return out, notes
