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

A demand in a filed action (the ``litigation`` variant, ``re_vs`` in the firm's
config) goes to defense counsel, and its RE block is the action's caption over
its court and case number, not a carrier's insured and claim number. Its front
matter carries ``caption`` and ``court`` in place of ``insured`` and ``claim``
(``LITIGATION_FRONT_KEYS``). Read 2026-11-06 off a firm's own letters to
defense counsel: everything below the RE block is the same house letter.
"""

from __future__ import annotations

import copy
import re
from datetime import date, timedelta
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
#: The litigation variant's front matter: the caption ("<plaintiff> vs.
#: <defendant>, et al.,") and the court line under it ("<County> County Superior
#: Court Case No. <number>"). ``client`` and ``dol`` stay: the cross-check reads
#: both against the matter record, and the client names the file.
LITIGATION_FRONT_KEYS = (
    "date",
    "attn",
    "client",
    "caption",
    "court",
    "dol",
    "banner",
    "expires",
    "salutation",
    "signer",
)


def front_keys(litigation: bool = False) -> tuple[str, ...]:
    return LITIGATION_FRONT_KEYS if litigation else FRONT_KEYS


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
    def __init__(self, reference: Path, kinds: tuple[str, ...] = KINDS) -> None:
        import docx

        self.doc = docx.Document(str(reference))
        body = self.doc.element.body
        self.body = body
        kids = list(body.iterchildren())
        self.sect = kids[-1] if kids and kids[-1].tag == _qn("w:sectPr") else None
        self.proto: dict[str, Any] = {}
        for k in kids:
            # The tag is the first paragraph's text: a box prototype (one cell,
            # several styled paragraphs) carries more text after it.
            first = k if k.tag == _qn("w:p") else next(k.iter(_qn("w:p")), k)
            m = re.match(r"PROTO:(\w+)", _text(first).strip())
            if m:
                self.proto[m.group(1)] = copy.deepcopy(k)
        missing = [k for k in kinds if k not in self.proto]
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
def parse(md: str, keys: tuple[str, ...] = FRONT_KEYS) -> tuple[dict[str, str], str, str]:
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
    missing = [k for k in keys if not front.get(k)]
    if missing:
        raise RenderError(f"front matter missing: {missing}")
    return front, md[m.end() :], notes.strip()


#: A long date as the letter writes it, with or without its weekday.
_LONG_DATE = re.compile(
    r"(?:(?P<wd>Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+)?"
    r"(?P<mon>January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(?P<day>\d{1,2}),\s+(?P<year>\d{4})"
)


def _long(d: date) -> str:
    return f"{d:%A}, {d:%B} {d.day}, {d.year}"


def business_day_expiry(md: str) -> tuple[str, list[str]]:
    """The demand's expiry on a business day. The drafter sets it thirty days
    out and a Saturday or Sunday comes up one week in three; on 2026-11-07 the
    review flagged a Sunday, a day the firm's and the carrier's offices are
    closed. The banner's date moves to the Monday, every statement of the same
    date in the letter moves with it, and a weekday word that disagrees with
    its date is corrected. Public holidays are not known here: a Monday that
    is one is the attorney's to move, and the note says so. Returns the letter
    and the attorney-notes lines (none when nothing changed)."""
    m = re.search(r"(?m)^expires:\s*(.*)$", md)
    if not m:
        return md, []
    hit = _LONG_DATE.search(m.group(1))
    if not hit:
        return md, []
    try:
        d = date(int(hit["year"]), _MONTHS[hit["mon"]], int(hit["day"]))
    except (KeyError, ValueError):
        return md, []
    moved = d
    while moved.weekday() >= 5:
        moved += timedelta(days=1)
    was, now = hit.group(0), _long(moved)
    if was == now:
        return md, []
    out = md.replace(was, now)
    bare = f"{d:%B} {d.day}, {d.year}"
    if moved != d:
        # the same date written without its weekday, in the body's closing sentence
        out = re.sub(rf"(?<![A-Za-z], ){re.escape(bare)}", f"{moved:%B} {moved.day}, {moved.year}", out)
        note = (
            f"The demand's expiry fell on a {d:%A} ({bare}); it is written as {now}, the next business day. "
            "Public holidays are not checked: move it again if that Monday is one."
        )
    else:
        note = f"The expiry's weekday was corrected to {now} (the letter had {was})."
    return out, [note]


_MONTHS = {
    m: i
    for i, m in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        start=1,
    )
}


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


def _underline_after_tab(p_el: Any) -> None:
    """The caption of a filed action is underlined as well as bold, as the
    firm's own letters to defense counsel write it; "RE:" is not."""
    seen_tab = False
    for r in p_el.findall(_qn("w:r")):
        if r.find(_qn("w:tab")) is not None:
            seen_tab = True
        elif seen_tab and r.find(_qn("w:t")) is not None:
            u = r.find(_qn("w:rPr")).makeelement(_qn("w:u"), {_qn("w:val"): "single"})
            r.find(_qn("w:rPr")).append(u)


def _re_block(w: HouseWriter, front: dict[str, str], litigation: bool) -> None:
    if litigation:  # a filed action: the caption, then its court and case number
        _underline_after_tab(w.para("re_first", f"**RE:\t{front['caption']}**"))
        w.para("re_line", f"\t{front['court']}")
        w.para("re_line", f"\tDate of Loss: {front['dol']}")
        return
    w.para("re_first", f"**RE:\tOur Client:\t{front['client']}**")
    w.para("re_line", f"\tYour Insured:\t{front['insured']}")
    w.para("re_line", f"\tClaim Number:\t{front['claim']}")
    w.para("re_line", f"\tDate of Loss:\t{front['dol']}")


def _header(w: HouseWriter, front: dict[str, str], mail_line: str = "", litigation: bool = False) -> None:
    w.para("blank")
    w.para("blank")
    w.para("date", front["date"])
    w.para("blank")
    if mail_line:  # the pre-suit variant's bold "CERTIFIED MAIL" above the address block
        w.para("attn", f"**{mail_line}**")
    for line in front["attn"].split("|"):
        w.para("attn", line.strip())
    w.para("blank")
    _re_block(w, front, litigation)
    w.para("blank")
    w.banner(front["banner"], front["expires"])
    w.para("blank")
    w.para("salute", front["salutation"])


class _Body:
    """The letter body, line by line, into the writer."""

    def __init__(
        self, w: HouseWriter, front: dict[str, str], signature: str, title: str, signer: str = "", initials: str = ""
    ) -> None:
        self.w, self.front, self.signature, self.title = w, front, signature, title
        self.signer, self.initials = signer, initials
        self.in_exhibits = self.exh_intro_done = self.signed = False

    def sign(self) -> None:
        w = self.w
        w.para("blank")
        w.para("sign", "Cordially,")
        w.para("signfirm", f"**{self.signature}**")
        # The signer, title and typist initials are the firm's authored map for
        # the responsible attorney, never the model's front matter (review of
        # #3074: one attorney's initials on every demand a paralegal drafted for another).
        w.para("sign", self.signer or self.front["signer"])
        w.para("sign", self.title if self.signer else (self.front.get("signer_title") or self.title))
        if self.initials:
            w.para("sign", self.initials)
        self.signed = True

    def heading(self, line: str) -> None:
        if line.startswith("## "):
            title = line[3:].strip()
            if title.lower() == "exhibits":
                if not self.signed:
                    self.sign()
                self.w.para("exh_head", f"**{title}**")
                self.in_exhibits = True
            else:
                # Bold, as every section heading in the firm's own letters is.
                # The reference's prototype carries the rest (centered, its
                # caps and underline); a run's weight is set here, because
                # ``_run`` writes each run's bold from the text's own marks.
                self.w.para("heading", f"**{title}**")
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
    md: str,
    reference: Path,
    out_dir: Path,
    *,
    signature: str,
    signer_title: str,
    author: str,
    client_label: str | None = None,
    signer: str = "",
    initials: str = "",
    mail_line: str = "",
    litigation: bool = False,
) -> tuple[Path, str]:
    """``(the demand file, the attorney notes text)``. ``client_label`` names
    the file: on the seat it is the matter record's client name, read by code,
    so no model-written string becomes a file name (review of #3074); the
    laptop tool's behavior (the letter's own client line) is the fallback.
    ``litigation``: the RE block is the action's caption and court."""
    front, body, notes = parse(md, front_keys(litigation))
    w = HouseWriter(reference)
    _header(w, front, mail_line, litigation)
    _Body(w, front, signature, signer_title, signer, initials).feed(body.strip("\n").splitlines())
    out_dir.mkdir(parents=True, exist_ok=True)
    name = out_name(client_label or front["client"])
    out = out_dir / name
    w.save(out, title=name[:-5], author=author)
    return out, notes
