"""Render a demand in an attorney's NUMBERED format (``layout: numbered``).

The firm's default demand (``house.py``) is Doug Mead's format. On 2026-10-14
the firm asked that Craig Ashton's demands follow one of his own instead (its
office manager, sending his signed demand on a matter of his: "Please use this
as an example demand for all of Craig's cases"). That format is different
enough in shape that it is its own layout rather than a set of options on the
house one:

* a bold mail line, then the addressee's lines with no "Attn:" prefix;
* a two-column RE table (Re, Your Insured, Our Client, Claim No., Date of Loss,
  Location of Loss), not tabbed RE lines;
* centered banner LINES (the time-limited demand title, then the firm's fixed
  statute and settlement-communication lines from the variant config), not a
  shaded banner table carrying the expiry;
* a centered lead heading ("CASE DISTILLED"), then roman-numbered sections
  (``I. TITLE``) the renderer numbers itself, so a model cannot misnumber them;
* bullets, sub-bullets, numbered lists that restart at 1, bold-lead terms,
  right-aligned source lines, and the format's own tables, matched by header;
* "Cordially," / the firm / a script signature line / the printed name / the
  title, with the typist-initials line only when the attorney row carries one;
* "Enclosures: Index of Exhibits", a caption, the exhibit table, and the
  supporting-document list;
* a footer naming the client and claim, filled from the front matter.

The mechanism is ``house.py``'s: the attorney's reference document is one of
his own demands with every word of client content removed and one prototype
of each kind left behind, tagged ``PROTO:<kind>``; the renderer clones them,
so his fonts, spacing, numbering, table shading and letterhead arrive as he
uses them. A prototype's own bold/italic is the kind's base style; markdown
emphasis adds to it. Case photographs are not part of the format: they are
case-specific and chosen by the attorney, so the reference carries no image
and the letter carries none.

Input is markdown with a front-matter block (``FRONT_KEYS``), then
``## Section`` (numbered by the renderer), ``### Sub-head``, ``- bullet``,
``  - sub-bullet``, ``1. numbered item``, ``> quotation`` (italic),
``>> callout`` (bold italic), ``^ source or attribution line`` (right-aligned),
the format's tables, and paragraphs. ``## Enclosures: Index of Exhibits``
starts the enclosures and the signature block is inserted before it.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

from .house import HouseWriter, RenderError, _keep_next, _qn, out_name, parse, set_runs

KINDS = (
    "date",
    "mail",
    "addr",
    "re",
    "banner",
    "banner_sub",
    "banner_note",
    "salute",
    "lead",
    "section",
    "subhead",
    "body",
    "callout",
    "quote",
    "note",
    "bullet",
    "bullet2",
    "numbered",
    "kv",
    "summary",
    "grid",
    "chronology",
    "specials",
    "future",
    "causation",
    "sign",
    "signfirm",
    "signscript",
    "signname",
    "exh_head",
    "exh_intro",
    "exhibits",
    "exh_sub",
    "exh_item",
)
FRONT_KEYS = (
    "date",
    "mail",
    "attn",
    "re",
    "insured",
    "client",
    "claim",
    "dol",
    "location",
    "banner",
    "salutation",
    "footer_name",
)
RE_ROWS = (
    ("Re:", "re"),
    ("Your Insured:", "insured"),
    ("Our Client:", "client"),
    ("Claim No.:", "claim"),
    ("Date of Loss:", "dol"),
    ("Location of Loss:", "location"),
)
#: The reference footer's slots, filled from the front matter.
FOOTER_SLOTS = (("{name}", "footer_name"), ("{claim}", "claim"))
ENCLOSURES = "enclosures"
TABLES_HELP = (
    "the format's tables are: a two-column table with an empty header (label | value); Item | Summary; "
    "Provider | Amount Billed | Net of Adjustments; Category | Low | High; (empty) | Before | After; "
    "Date | Provider | Findings and Treatment | Exh.; Exh. | Provider / Description | Dates of Service | Contents; "
    "and any other four-column table"
)


def roman(n: int) -> str:
    out = ""
    for value, sym in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L")):
        while n >= value:
            out, n = out + sym, n - value
    for value, sym in ((40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= value:
            out, n = out + sym, n - value
    return out


def table_kind(header: list[str]) -> str:
    h = [c.strip().strip("*").strip().lower() for c in header]
    n = len(h)
    if n == 2 and not any(h):
        return "kv"
    if n == 2 and h[0] == "item":
        return "summary"
    if n == 3 and h[0] == "provider":
        return "specials"
    if n == 3 and h[0] == "category":
        return "future"
    if n == 3 and not h[0] and all(h[1:]):
        return "causation"
    if n == 4 and h[0].startswith("exh"):
        return "exhibits"
    if n == 4 and h[0] == "date" and h[1] == "provider":
        return "chronology"
    if n == 4:
        return "grid"
    raise RenderError(f"no table in this format for header {header}: {TABLES_HELP}")


def _flags(el: Any) -> tuple[bool, bool]:
    r = el.find(".//" + _qn("w:r"))
    rpr = r.find(_qn("w:rPr")) if r is not None else None
    if rpr is None:
        return False, False
    return rpr.find(_qn("w:b")) is not None, rpr.find(_qn("w:i")) is not None


def _force(el: Any, bold: bool, italic: bool) -> None:
    """Give every run the prototype's base emphasis (``house._run`` sets
    emphasis from the markdown alone)."""
    for r in el.iter(_qn("w:r")):
        rpr = r.find(_qn("w:rPr"))
        if rpr is None:
            continue
        for flag, tags in ((bold, ("w:b", "w:bCs")), (italic, ("w:i", "w:iCs"))):
            for tag in tags if flag else ():
                if rpr.find(_qn(tag)) is None:
                    rpr.append(rpr.makeelement(_qn(tag), {}))


class NumberedWriter(HouseWriter):
    def __init__(self, reference: Path) -> None:
        super().__init__(reference, KINDS)
        self.base = {k: _flags(v) for k, v in self.proto.items()}
        self.list_num: str | None = None

    def para(self, kind: str, text: str = "") -> Any:
        el = copy.deepcopy(self.proto[kind])
        set_runs(el, text)
        _force(el, *self.base[kind])
        if kind == "numbered":
            self._number(el)
        self._insert(el)
        return el

    def end_list(self) -> None:
        self.list_num = None

    def _number(self, el: Any) -> None:
        """A numbered list restarts at 1: each list gets its own numbering
        instance over the prototype's list definition."""
        num_id = el.find(".//" + _qn("w:numId"))
        if num_id is None:
            return
        if self.list_num is None:
            numbering = self.doc.part.numbering_part.element
            nums = numbering.findall(_qn("w:num"))
            src = next(n for n in nums if n.get(_qn("w:numId")) == num_id.get(_qn("w:val")))
            new = str(max(int(n.get(_qn("w:numId"))) for n in nums) + 1)
            num = numbering.makeelement(_qn("w:num"), {_qn("w:numId"): new})
            num.append(copy.deepcopy(src.find(_qn("w:abstractNumId"))))
            over = num.makeelement(_qn("w:lvlOverride"), {_qn("w:ilvl"): "0"})
            over.append(over.makeelement(_qn("w:startOverride"), {_qn("w:val"): "1"}))
            num.append(over)
            numbering.append(num)
            self.list_num = new
        num_id.set(_qn("w:val"), self.list_num)

    def _fill(self, tc: Any, text: str) -> None:
        ps = tc.findall(_qn("w:p"))
        for extra in ps[1:]:
            tc.remove(extra)
        base = _flags(ps[0])
        set_runs(ps[0], text)
        _force(ps[0], *base)

    def _row(self, template: Any, row: list[str], kind: str) -> Any:
        tr = copy.deepcopy(template)
        cells = tr.findall(_qn("w:tc"))
        if len(cells) != len(row):
            raise RenderError(f"a {kind} table row has {len(row)} cells; the format's table has {len(cells)}")
        for tc, val in zip(cells, row):
            self._fill(tc, val)
        return tr

    def table(self, kind: str, header: list[str], rows: list[list[str]], total_last: bool = False) -> None:
        """A header-row table (``header`` empty: a label/value table, no header)."""
        t = copy.deepcopy(self.proto[kind])
        trs = t.findall(_qn("w:tr"))
        for tr in trs:
            t.remove(tr)
        prev = self.body[len(self.body) - 2] if len(self.body) >= 2 else None
        if prev is not None and prev.tag == _qn("w:p"):
            _keep_next(prev)
        if header:
            head = self._row(trs[0], header, kind)
            for p_el in head.iter(_qn("w:p")):
                _keep_next(p_el)
            t.append(head)
            body_row, total_row = trs[1], trs[-1]
        else:
            body_row = total_row = trs[0]
        for n, row in enumerate(rows):
            last = total_last and n == len(rows) - 1
            t.append(self._row(total_row if last else body_row, row, kind))
        self._insert(t)

    def footer(self, front: dict[str, str]) -> None:
        for section in self.doc.sections:
            for part in (section.footer, section.first_page_footer, section.even_page_footer):
                if part.is_linked_to_previous:  # not defined; touching it would create one
                    continue
                for t in part._element.iter(_qn("w:t")):
                    for slot, key in FOOTER_SLOTS:
                        if t.text and slot in t.text:
                            t.text = t.text.replace(slot, front[key])


def _header(w: NumberedWriter, front: dict[str, str], variant: dict[str, Any]) -> None:
    w.para("date", front["date"])
    w.para("mail", front["mail"])
    for line in front["attn"].split("|"):
        w.para("addr", line.strip())
    w.table("re", [], [[label, front[key]] for label, key in RE_ROWS])
    w.para("banner", front["banner"])
    lines = list(variant.get("banner_lines") or [])
    for n, line in enumerate(lines):
        w.para("banner_sub" if n == 0 else "banner_note", line)
    w.para("salute", front["salutation"])
    w.para("lead", str(variant["lead_heading"]))
    w.footer(front)


class _Body:
    """The letter body, line by line, into the writer."""

    def __init__(self, w: NumberedWriter, signature: str, signer: str, title: str, initials: str) -> None:
        self.w, self.signature, self.signer, self.title, self.initials = w, signature, signer, title, initials
        self.sections = 0
        self.in_exhibits = self.exh_intro_done = self.in_supporting = self.signed = False

    def sign(self) -> None:
        w = self.w
        w.para("sign", "Cordially,")
        w.para("signfirm", self.signature)
        w.para("signscript", self.signer)
        w.para("signname", self.signer)
        w.para("signname", self.title)
        if self.initials:
            w.para("signname", self.initials)
        self.signed = True

    def heading(self, line: str) -> None:
        if line.startswith("## "):
            title = line[3:].strip()
            if title.lower().startswith(ENCLOSURES):
                if not self.signed:
                    self.sign()
                self.w.para("exh_head", title)
                self.in_exhibits = True
            else:
                self.sections += 1
                self.w.para("section", f"{roman(self.sections)}. {title.upper()}")
        else:
            self.w.para("exh_sub" if self.in_exhibits else "subhead", line[4:].strip())
            self.in_supporting = self.in_exhibits

    def table(self, block: list[list[str]]) -> None:
        block = [r for r in block if not all(re.fullmatch(r":?-{2,}:?", c) for c in r)]
        kind = "exhibits" if self.in_exhibits else table_kind(block[0])
        if self.in_exhibits and len(block[0]) != 4:
            raise RenderError(
                "the exhibit index is a four-column table (Exh. | Provider / Description | Dates | Contents)"
            )
        rows = block[1:]
        total = kind in ("specials", "future") and bool(rows) and rows[-1][0].strip("* ").lower().startswith("total")
        if total:
            rows[-1] = [c.replace("**", "") for c in rows[-1]]
        self.w.table(kind, [] if kind == "kv" else block[0], rows, total_last=total)

    def paragraph(self, text: str) -> None:
        if not self.in_exhibits:
            self.w.para("body", text)
        elif not self.exh_intro_done and not self.in_supporting:
            self.w.para("exh_intro", text)
            self.exh_intro_done = True
        else:
            self.w.para("exh_item", text)

    def line(self, raw: str) -> None:
        stripped = raw.strip()
        m = re.match(r"\d+[.)]\s+(.*)", stripped)
        if m:
            self.w.para("numbered", m.group(1))
            return
        if not (stripped.startswith("- ") and raw.startswith((" ", "\t"))):
            self.w.end_list()  # a sub-bullet sits inside a numbered list; anything else ends it
        if stripped.startswith("- "):
            self.w.para("bullet2" if raw.startswith((" ", "\t")) else "bullet", stripped[2:].strip())
        elif stripped.startswith(">> "):
            self.w.para("callout", stripped[3:].strip())
        elif stripped.startswith("> "):
            self.w.para("quote", stripped[2:].strip())
        elif stripped.startswith("^ "):
            self.w.para("note", stripped[2:].strip())

    def feed(self, lines: list[str]) -> None:
        i = 0
        item = re.compile(r"\s*(- |>> |> |\^ |\d+[.)]\s)")
        while i < len(lines):
            line = lines[i].rstrip()
            if not line.strip():
                i += 1
            elif line.startswith(("## ", "### ")):
                self.w.end_list()
                self.heading(line)
                i += 1
            elif line.lstrip().startswith("|"):
                self.w.end_list()
                block = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    block.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                    i += 1
                self.table(block)
            elif item.match(line):
                self.line(line)
                i += 1
            else:
                self.w.end_list()
                buf = [line.strip()]
                i += 1
                while (
                    i < len(lines)
                    and lines[i].strip()
                    and not re.match(r"(#{2,3} |\s*\||" + item.pattern + ")", lines[i])
                ):
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
    variant: dict[str, Any],
    signature: str,
    signer: str,
    signer_title: str,
    author: str,
    client_label: str | None = None,
    initials: str = "",
) -> tuple[Path, str]:
    """``(the demand file, the attorney notes text)``; as ``house.render``."""
    front, body, notes = parse(md, FRONT_KEYS)
    w = NumberedWriter(reference)
    _header(w, front, variant)
    _Body(w, signature, signer, signer_title, initials).feed(body.strip("\n").splitlines())
    out_dir.mkdir(parents=True, exist_ok=True)
    name = out_name(client_label or front["client"])
    out = out_dir / name
    w.save(out, title=name[:-5], author=author)
    return out, notes
