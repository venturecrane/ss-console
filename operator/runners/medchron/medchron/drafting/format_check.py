"""`format_check`: the rendered Word file against the firm's authored format.

The rules come from ``drafting-firm.yaml``'s ``format`` block and nowhere
else: the effective class rules are the renderer's product defaults with the
firm's house style laid over them (``house.rules_for``), the same override the
render used, so the check reads the document back against what the firm wrote.
The specimen briefs are exemplars and review aids, never measured. Each
assertion reads the rendered .docx:

* every run with text is the authored font at the authored size;
* plain US Letter paper with no line numbering, when the firm says so;
* headings at each level: alignment, bold, underline and indent per the rules;
* body paragraphs: line spacing (exact points or a multiple), justification and
  first-line indent per the rules; front matter (the attorney block and the
  court lines) and bold-italic provider headings per the rules;
* footer: the PAGE field and the title line, when the rules carry them;
* mediation brief: the authored sections in order; settlement authority,
  target and bracket only as ``{{ATTORNEY}}`` markers;
* discovery: item labels in the authored label style, labels and requests at
  the rules' spacing; a set's Definitions section; the proof of service last;
  the section 2030.050 declaration iff the cumulative rule (``render``) says so.

Two kinds of finding, because they are fixed in different places: CONTENT
(the model's document lacks a section, the court lines, the item labels, the
Definitions: one bounded repair pass can fix them) and everything else, OUR
render (code), which fails the job at once.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .house import rules_for
from .render import DECL_LINE, FIGURE, RESERVED, _outside_markers, decl_decision

LETTER = (12240, 15840)
_ROMAN = re.compile(r"^\s*[IVXLC]+\.\s*", re.I)
_NUMBERED = re.compile(r"^\s*\S{1,6}\t")
_ALIGN = {"center": "CENTER", "left": "LEFT"}


@dataclass
class Result:
    fails: list[str] = field(default_factory=list)
    #: The fails the MODEL's content causes (a repair pass can fix them).
    content: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.fails

    @property
    def ours(self) -> list[str]:
        return [f for f in self.fails if f not in self.content]

    def model(self, finding: str) -> None:
        self.fails.append(finding)
        self.content.append(finding)


def _style_flag(run: Any, para: Any, attr: str) -> bool:
    v = getattr(run, attr)
    if v is not None:
        return bool(v)
    style = para.style
    while style is not None:
        if getattr(style.font, attr) is not None:
            return bool(getattr(style.font, attr))
        style = style.base_style
    return False


def _underlined(run: Any, para: Any) -> bool:
    return _style_flag(run, para, "underline")


def _bold(run: Any, para: Any) -> bool:
    return _style_flag(run, para, "bold")


def _eff(p: Any, attr: str) -> Any:
    """A paragraph-format value as Word resolves it: the paragraph's own, else
    its style chain's."""
    v = getattr(p.paragraph_format, attr)
    style = p.style
    while v is None and style is not None:
        v = getattr(style.paragraph_format, attr)
        style = style.base_style
    return v


def _text_runs(p: Any) -> list[Any]:
    return [r for r in p.runs if r.text.strip()]


def _prose_runs(p: Any) -> list[Any]:
    """The runs that are words, not a marker (a marker renders unstyled)."""
    return [r for r in _text_runs(p) if "{{" not in r.text and "}}" not in r.text]


def _paras(doc: Any) -> list[Any]:
    return [p for p in doc.paragraphs if p.text.strip()]


def _level(p: Any) -> int:
    name = p.style.name if p.style is not None else ""
    m = re.search(r"Heading (\d)$", name)
    return int(m.group(1)) if m else 0


def _front_count(doc: Any) -> int:
    """How many of ``doc.paragraphs`` (top-level, in body order) come before
    the caption table and the first heading."""
    n = 0
    for el in doc.element.body.iterchildren():
        if el.tag.endswith("}tbl"):
            break
        if el.tag.endswith("}p"):
            if "Heading" in (el.style or ""):
                break
            n += 1
    return n


def check_font(doc: Any, fmt: dict[str, Any], res: Result) -> None:
    from docx.oxml.ns import qn
    from docx.shared import Pt

    runs = [r for p in doc.paragraphs for r in _text_runs(p)]
    runs += [r for t in doc.tables for c in t._cells for p in c.paragraphs for r in _text_runs(p)]
    bad = 0
    for r in runs:
        rpr = r._r.rPr
        fonts = rpr.find(qn("w:rFonts")) if rpr is not None else None
        if fonts is None or fonts.get(qn("w:ascii")) != fmt["font"] or r.font.size != Pt(float(fmt["size_pt"])):
            bad += 1
    if bad:
        res.fails.append(f"font: {bad} run(s) are not {fmt['font']} {fmt['size_pt']}")


def check_paper(doc: Any, res: Result) -> None:
    from docx.oxml.ns import qn
    from docx.shared import Twips

    for s in doc.sections:
        if (s.page_width, s.page_height) != (Twips(LETTER[0]), Twips(LETTER[1])):
            res.fails.append("paper: not US Letter")
        if s._sectPr.find(qn("w:lnNumType")) is not None:
            res.fails.append("paper: line numbering present (pleading paper)")


def check_headings(doc: Any, rules: Any, res: Result) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches

    for p in _paras(doc):
        lv = _level(p)
        if not lv:
            continue
        i, runs = lv - 1, _text_runs(p)
        want_align = getattr(WD_ALIGN_PARAGRAPH, _ALIGN[rules.heading_align[i]])
        if (p.alignment or WD_ALIGN_PARAGRAPH.LEFT) != want_align:
            res.fails.append(f"heading: level {lv} not {rules.heading_align[i]}: {p.text[:40]}")
        if any(_bold(r, p) != rules.heading_bold[i] for r in runs):
            res.fails.append(f"heading: level {lv} bold should be {rules.heading_bold[i]}: {p.text[:40]}")
        if any(_underlined(r, p) != rules.heading_underline[i] for r in runs):
            word = "underlined" if rules.heading_underline[i] else "not underlined"
            res.fails.append(f"heading: level {lv} should be {word}: {p.text[:40]}")
        if (_eff(p, "left_indent") or 0) != Inches(rules.heading_indent_in[i]):
            res.fails.append(f"heading: level {lv} not indented {rules.heading_indent_in[i]} inch: {p.text[:40]}")


def check_body(doc: Any, rules: Any, res: Result) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
    from docx.shared import Inches, Pt

    n = _front_count(doc)
    everything = list(doc.paragraphs)
    front_paras = [p for p in everything[:n] if p.text.strip()]
    paras = [p for p in everything[n:] if p.text.strip()]
    if rules.front_matter_plain:
        court = [p for p in front_paras if re.search(r"(?i)\b(court|county)\b", p.text)]
        if len(court) < 2:
            res.model("caption: the court is not two lines above the caption table")
        elif not all(
            p.alignment == WD_ALIGN_PARAGRAPH.CENTER and all(_bold(r, p) for r in _prose_runs(p)) for p in court
        ):
            res.fails.append("caption: the court lines are not centered and bold")
    body = [p for p in paras if p.style is not None and p.style.name == "SMD Body"]
    providers: list[Any] = []
    if rules.bold_italic_heading_indent_in is not None:
        at = Inches(rules.bold_italic_heading_indent_in)
        providers = [p for p in body if _eff(p, "left_indent") == at]
        providers += [
            p
            for p in body
            if p not in providers
            and _prose_runs(p)
            and all(_bold(r, p) and r.italic for r in _prose_runs(p))
            and len(_prose_runs(p)) == len(_text_runs(p))
        ]
        for p in providers:
            if _eff(p, "left_indent") != at or not all(_bold(r, p) and r.italic for r in _prose_runs(p)):
                res.fails.append(
                    f"heading: a provider line not bold italic at {rules.bold_italic_heading_indent_in} inch"
                )
    prose = [p for p in body if p not in providers]
    if rules.body_exact_pt:
        bad = [
            p
            for p in prose
            if _eff(p, "line_spacing") != Pt(rules.body_exact_pt)
            or _eff(p, "line_spacing_rule") != WD_LINE_SPACING.EXACTLY
        ]
        if bad:
            res.fails.append(f"spacing: {len(bad)} body paragraph(s) not exactly {rules.body_exact_pt} point")
    elif rules.body_line_spacing != 1.0 and [p for p in prose if _eff(p, "line_spacing") != rules.body_line_spacing]:
        res.fails.append(f"spacing: body paragraph(s) not at {rules.body_line_spacing} line spacing")
    flowing = [p for p in prose if not _NUMBERED.match(p.text)]  # a numbered item hangs instead
    if rules.body_justify and [p for p in flowing if p.alignment != WD_ALIGN_PARAGRAPH.JUSTIFY]:
        res.fails.append("alignment: body paragraph(s) not justified")
    if rules.body_first_line_indent_in is not None:
        want = Inches(rules.body_first_line_indent_in)
        if [p for p in flowing if _eff(p, "first_line_indent") != want]:
            res.fails.append(
                f"indent: body paragraph(s) without the {rules.body_first_line_indent_in} inch first-line indent"
            )


def check_mediation(doc: Any, fmt: dict[str, Any], res: Result) -> None:
    paras = _paras(doc)
    heads = [_ROMAN.sub("", p.text).strip().casefold() for p in paras if _level(p) == 1]
    pos = -1
    for title in fmt["mediation_brief_sections"]:
        want = _ROMAN.sub("", title).strip().casefold()
        hit = next((i for i, h in enumerate(heads) if i > pos and want in h), None)
        if hit is None:
            res.model(f"sections: {title!r} missing or out of the authored order")
        else:
            pos = hit
    for p in paras:
        t = _outside_markers(p.text)
        if _level(p) or not RESERVED.search(t):
            continue
        if FIGURE.search(t):
            res.fails.append(f"reserved: a settlement figure outside an {{{{ATTORNEY}}}} marker: {p.text[:60]}")
        elif "{{ATTORNEY" not in p.text:
            res.fails.append(
                f"reserved: settlement authority/target/bracket without an {{{{ATTORNEY}}}} marker: {p.text[:60]}"
            )


def doc_text(doc: Any) -> str:
    """The rendered document as text the shared rules read: paragraphs, and
    table rows as ``| a | b |`` (the identification block is a table)."""
    out = [p.text for p in doc.paragraphs]
    for t in doc.tables:
        out += ["| " + " | ".join(c.text for c in row.cells) + " |" for row in t.rows]
    return "\n".join(out)


def check_discovery(doc: Any, cls: str, fmt: dict[str, Any], rules: Any, res: Result, digest: str) -> None:
    from docx.shared import Inches, Pt

    paras = _paras(doc)
    labels = [p for p in paras if p.style is not None and p.style.name == "SMD Item Label"]
    items = [p for p in paras if p.style is not None and p.style.name == "SMD Item Text"]
    if not labels:
        res.model("discovery: no item labels (each request led by its own label line)")
    for p in labels:
        if not all(
            _bold(r, p) and _underlined(r, p) and (r.font.all_caps or r.text == r.text.upper()) for r in _text_runs(p)
        ):
            res.fails.append(f"label: not all-caps bold underlined: {p.text[:40]}")
    for p in labels + items:
        extra = [a for a in ("space_before",) if (_eff(p, a) or Pt(0)) != Pt(0)]
        if _eff(p, "line_spacing") != rules.item_line_spacing or extra:
            res.fails.append(f"spacing: not at {rules.item_line_spacing} with no space before: {p.text[:40]}")
    for p in items:
        if (_eff(p, "space_after") or Pt(0)) != Pt(rules.item_space_after_pt):
            res.fails.append(f"spacing: {p.text[:40]} carries space after other than {rules.item_space_after_pt} pt")
        if p.paragraph_format.first_line_indent != Inches(rules.first_line_indent_in):
            res.fails.append(f"request: not first-line indented: {p.text[:40]}")
    texts = [p.text for p in paras]
    pos_at = next((i for i, t in enumerate(texts) if "PROOF OF SERVICE" in t.upper()), None)
    decl_at = next((i for i, t in enumerate(texts) if DECL_LINE.search(t)), None)
    if pos_at is None:
        res.fails.append("attachments: proof of service missing")
    if pos_at is not None and decl_at is not None and decl_at > pos_at:
        res.fails.append("attachments: the declaration must precede the proof of service")
    if cls == "discovery_set":
        if not any(t.strip().strip(":").upper() == "DEFINITIONS" for t in texts):
            res.model("definitions: no Definitions section")
        d = decl_decision(doc_text(doc), digest)
        if (decl_at is not None) != d.attach:
            want = "required" if d.attach else "not allowed"
            res.fails.append(
                f"attachments: section 2030.050 declaration {want} ({d.total} special interrogatories to this "
                f"party, {d.this_set} in this set; {len(d.unreadable)} prior count(s) not readable)"
            )
    elif decl_at is not None:
        res.fails.append("attachments: a response carries no section 2030.050 declaration")


def check_footer(doc: Any, blob: bytes, rules: Any, res: Result) -> None:
    z = zipfile.ZipFile(io.BytesIO(blob))
    xml = "".join(z.read(n).decode("utf-8", "replace") for n in z.namelist() if n.startswith("word/footer"))
    if rules.page_numbers_always and not (re.search(r"<w:instrText[^>]*>\s*PAGE\b", xml) or 'w:instr=" PAGE' in xml):
        res.fails.append("footer: no PAGE field (page numbers at the bottom)")
    if rules.footer_title:
        lines = [p.text.strip() for p in doc.sections[0].footer.paragraphs if p.text.strip()]
        if rules.footer_title not in lines:
            res.fails.append(f"footer: the title {rules.footer_title!r} is not under the page number")


def check(path: Path, cls: str, fmt: dict[str, Any], digest: str = "") -> Result:
    """``digest`` carries the DISCOVERY-SETS index the cumulative section
    2030.050 rule reads (``render.decl_decision``, the rule the attach used)."""
    import docx

    blob = Path(path).read_bytes()
    doc = docx.Document(io.BytesIO(blob))
    rules = rules_for(fmt, cls)
    res = Result()
    check_font(doc, fmt, res)
    if fmt.get("plain_letter_paper"):
        check_paper(doc, res)
    check_headings(doc, rules, res)
    check_body(doc, rules, res)
    check_footer(doc, blob, rules, res)
    if cls == "mediation_brief":
        check_mediation(doc, fmt, res)
    if cls in ("discovery_set", "discovery_response"):
        check_discovery(doc, cls, fmt, rules, res, digest)
    return res
