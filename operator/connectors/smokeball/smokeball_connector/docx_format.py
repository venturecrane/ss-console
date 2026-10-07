"""Render drafting content INTO a firm's Word template: code owns typography.

This is the typographic half of ADR 0083 ("the model fills content into a
structure; code owns typography and required elements"). ``docgrammar`` gives
us blocks; this module decides how each block LOOKS for a given document class,
and it takes that decision from exactly one place when it can: **the firm's own
Word template**, a ``.docx`` in the firm's Document Library. The renderer opens
that file as the base document (page setup, headers/footers/letterhead, styles,
numbering and theme all survive), clears its body, and writes the content back
in using a small contract of NAMED PARAGRAPH STYLES. A template that defines a
named style wins; a template that lacks one gets the class's product default
applied inline, and the fallback is reported. Nothing is written back into the
firm's file, ever.

Why typography lives ONLY in the .docx: a firm edits a style in Word and the
next draft honors it. No config publish, no reboot, no SMD in the loop. That is
the difference between "formatted by us" and "formatted the way the firm
formats", and it is the same sentence as "the firm authors its own posture".

Why this module never invents legal content: item numbers come from the
propounded set; the 35-interrogatory rule is an aggregate across the matter and
attorney-reserved; a statute-bound declaration is jurisdiction-specific. The
model writes labels, numerals, captions, signature blocks, and proof of
service exactly as the skeletons do today; this module STYLES them (a line that
looks like an item label gets the label style) and adds nothing. Content stays
under the drafting gates; typography is code.

Named style contract (a firm's template may define any subset):
  SMD Body, SMD Item Label, SMD Item Text, SMD Heading 1/2/3, SMD Caption,
  SMD Signature

Refusals (the only ones): a multi-section base document (the body clear would
silently keep the LAST section's page setup and drop the rest; a wrong
letterhead on a client letter is worse than an honest "not yet").
"""

from __future__ import annotations

import io
from . import docgrammar as g
from .docx_base import (
    add_page_field,
    footer_has_any_field,
    footer_has_page_field,
    has_style,
    open_as_base,
    plain_letter_paper,
    set_run_font,
    set_table_borders,
    usable_paragraph_style,
)
from .docx_classes import _LABEL_MAX_CHARS, CLASS_RULES, ClassRules
from .docx_format_types import (
    DEFAULT_FONT,
    DEFAULT_SIZE_PT,
    NAMED_STYLES,
    ROLE_FALLBACK,
    FormatRefused,
    FormatReport,
)
from .letterhead import (
    LETTERHEAD_CLASSES,
    FirmIdentity,
    apply_letterhead,
    headers_empty,
    is_starter_derived,
    load_firm_identity,
)

# The classes the renderer knows (their rules: docx_classes.CLASS_RULES). Read
# out of THIS file by tests/skill-reply-rules.test.ts, so it stays here.
DOCUMENT_CLASSES = (
    "discovery_set",
    "discovery_response",
    "demand_letter",
    "mediation_brief",
    "memo",
    "depo_outline",
    "letter",
)

# ---- Rendering -----------------------------------------------------------------


def render_document(
    markdown: str,
    document_class: str,
    base_bytes: bytes | None,
    report: FormatReport | None = None,
    firm_identity: FirmIdentity | None = None,
) -> tuple[bytes, FormatReport]:
    """Render ``markdown`` for ``document_class`` into ``base_bytes`` (the firm's
    template) or the stock starter base. Pure: no network, no client.

    ``firm_identity`` is the AUTHORED letterhead (``customer.yaml``
    ``firm_identity``). It is printed only for the letter classes, on the
    starter or on a starter-derived base whose headers are empty; a firm's own
    base keeps whatever header the firm built.
    See ``letterhead``."""
    if document_class not in CLASS_RULES:
        raise ValueError(f"unknown document_class {document_class!r}; known: {', '.join(DOCUMENT_CLASSES)}")
    rules = CLASS_RULES[document_class]
    report = report or FormatReport(document_class=document_class)
    doc = open_as_base(base_bytes, report)
    if rules.letter_paper:
        plain_letter_paper(doc, report)
    _letterhead(doc, document_class, base_bytes is not None, firm_identity, report)
    writer = _Writer(doc, rules, report)
    for block in g.parse_document(markdown):
        writer.write(block)
    if rules.page_numbers:
        writer.ensure_page_number(always=rules.page_numbers_always)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue(), report


def _letterhead(doc, document_class: str, firm_base: bool, identity: FirmIdentity | None, report: FormatReport) -> None:
    """Decide and record the first page's letterhead for a letter class."""
    if document_class not in LETTERHEAD_CLASSES:
        return
    # A base the Operator itself rendered from the SMD starter is not a firm
    # file for letterhead purposes: while its headers are empty it gets the
    # authored letterhead, exactly as the starter would. Any header content, or
    # a base that is not starter-derived, is the firm's and stays untouched.
    starter_derived = firm_base and is_starter_derived(doc)
    if firm_base and not (starter_derived and headers_empty(doc)):
        report.letterhead = {"source": "firm_template", "lines": list(report.base_header_footer_text)}
        return
    lines = apply_letterhead(doc, identity) if identity is not None else []
    if lines:
        report.letterhead = {"source": "firm_identity", "lines": lines}
        if starter_derived:
            report.letterhead["base"] = "starter_derived"
        report.notes.append("letterhead printed on the first page from the firm's authored identity")
        return
    reason = (identity.source if identity is not None else "") or "firm_identity not authored in customer.yaml"
    report.letterhead = {"source": "none", "lines": [], "reason": reason}
    base = (
        "its class template is SMD's starter, which carries none,"
        if starter_derived
        else "the firm has no template file for this class"
    )
    report.notes.append(
        f"no letterhead: {base} and {reason}; the firm authors firm_identity once, or files its own letterhead template"
    )


class _Writer:
    def __init__(self, doc, rules: ClassRules, report: FormatReport) -> None:
        self.doc = doc
        self.rules = rules
        self.report = report
        self.in_item = False
        self.tables_seen = 0

    # -- style application ------------------------------------------------------

    def _resolve_style(self, role: str) -> tuple[str | None, bool]:
        """``(style to apply, delegated?)`` for a role, or ``(None, False)`` to
        format inline.

        Three states, in order. Our named style if the base defines it. Else the
        base's OWN conventional equivalent, when the role has one — that is what
        makes "edit the style in Word and the next draft follows" true for a
        firm template that never heard of our names. Else inline.
        """
        if usable_paragraph_style(self.doc, role):
            self.report.styles_honored.append(role)
            return role, False
        alt = ROLE_FALLBACK.get(role)
        if alt and usable_paragraph_style(self.doc, alt):
            self.report.styles_delegated[role] = alt
            return alt, True
        self.report.fallbacks.append(role)
        if alt:
            # An instruction the firm can act on, not a diagnostic string.
            note = f"the base defines neither {role!r} nor {alt!r}; add {alt!r} in Word to control this level"
            if note not in self.report.notes:
                self.report.notes.append(note)
        return None, False

    def _para(self, style_name: str):
        """Add a paragraph for ``style_name``.

        Returns ``(paragraph, styled)`` where ``styled`` is True only when our
        OWN named style carried it. A delegated paragraph reports ``False`` on
        purpose: the firm's style supplies font and colour, but the class's
        layout (indents, the double-spacing between discovery items, a centered
        pleading heading) is a court requirement code still owns, so the caller
        must keep applying it. See ``_heading``.
        """
        applied, delegated = self._resolve_style(style_name)
        if applied is None:
            return self.doc.add_paragraph(), False
        try:
            return self.doc.add_paragraph(style=applied), not delegated
        except (KeyError, ValueError):
            # A style that resolved but will not apply degrades to inline; it
            # never kills the render.
            self.report.fallbacks.append(style_name)
            self.report.styles_delegated.pop(style_name, None)
            return self.doc.add_paragraph(), False

    def _font(self, run) -> None:
        if self.rules.font:
            set_run_font(run, self.rules.font, self.rules.font_size_pt)

    def _runs(
        self,
        para,
        runs: tuple[g.Run, ...],
        *,
        caps: bool = False,
        bold: bool = False,
        underline: bool = False,
        explicit: bool = False,
    ) -> None:
        """``explicit`` writes the emphasis as stated, False included, so a
        base style's underline cannot reach a heading the class says is bold
        only."""
        for r in runs:
            run = para.add_run(r.text)
            self._font(run)
            if r.marker:
                continue  # literal, unstyled, render-visible
            if r.bold or bold:
                run.bold = True
            elif explicit:
                run.bold = False
            if r.italic:
                run.italic = True
            if underline:
                run.underline = True
            elif explicit:
                run.underline = False
            if caps:
                run.font.all_caps = True

    # -- blocks ------------------------------------------------------------------

    def write(self, block: g.Block) -> None:
        if isinstance(block, g.Heading):
            self._heading(block)
        elif isinstance(block, g.Paragraph):
            self._paragraph(block)
        elif isinstance(block, g.Bullet):
            self._bullet(block)
        elif isinstance(block, g.Numbered):
            self._numbered(block)
        elif isinstance(block, g.Table):
            self._table(block)
        elif isinstance(block, g.HRule):
            self._hrule()

    def _heading(self, block: g.Heading) -> None:
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Inches, Pt

        self.in_item = False
        self.report.blocks_styled["headings"] += 1
        level = block.level
        role = f"SMD Heading {level}"
        para, styled = self._para(role)
        enforce = self.rules.enforce_layout
        if styled and not enforce:
            self._runs(para, block.runs)
            return
        delegated = role in self.report.styles_delegated
        # Layout stays ours even when the firm's own heading style carries the
        # typography: a mediation brief's level-1 heading is CENTERED because
        # the court expects it there, and a firm's built-in Heading 1 is
        # left-aligned. Font, size and colour come from their style; placement
        # does not.
        align = self.rules.heading_align[level - 1]
        para.alignment = {"center": WD_ALIGN_PARAGRAPH.CENTER, "left": WD_ALIGN_PARAGRAPH.LEFT}[align]
        para.paragraph_format.left_indent = Inches(self.rules.heading_indent_in[level - 1])
        para.paragraph_format.keep_with_next = True
        if delegated and not enforce:
            # Emphasis is the firm style's business; forcing bold/underline over
            # it would defeat the edit we just made possible. A house-style
            # class enforces it instead (enforce_layout).
            self._runs(para, block.runs)
            return
        if not styled:
            para.paragraph_format.space_before = Pt(12 if level == 1 else 6)
        self._runs(
            para,
            block.runs,
            bold=self.rules.heading_bold[level - 1],
            underline=self.rules.heading_underline[level - 1],
            explicit=enforce,
        )

    def _is_label(self, text: str) -> bool:
        # A label is a SHORT line that starts with a label phrase ("SPECIAL
        # INTERROGATORY NO. 7:"); a prose sentence that happens to open with the
        # same words is body text. The length cap is the tie-breaker.
        t = text.strip().strip("*").strip()
        return len(t) <= _LABEL_MAX_CHARS and any(p.match(t) for p in self.rules.label_patterns)

    def _paragraph(self, block: g.Paragraph) -> None:
        from docx.shared import Inches, Pt

        if self._is_label(block.text):
            self.report.blocks_styled["labels"] += 1
            self.in_item = True
            para, styled = self._para("SMD Item Label")
            enforce = self.rules.enforce_layout
            if styled and not enforce:
                self._runs(para, block.runs)
            elif enforce:
                pf = para.paragraph_format
                pf.space_before, pf.space_after = Pt(0), Pt(0)
                pf.line_spacing = self.rules.item_line_spacing
                self._runs(para, block.runs, caps=True, bold=True, underline=True, explicit=True)
            else:
                para.paragraph_format.space_before = Pt(12)
                self._runs(para, block.runs, caps=True, bold=True, underline=True)
            return
        if self.in_item and self.rules.item_text:
            para, styled = self._para("SMD Item Text")
            if not styled or self.rules.enforce_layout:
                pf = para.paragraph_format
                pf.first_line_indent = Inches(self.rules.first_line_indent_in)
                pf.line_spacing = self.rules.item_line_spacing
                pf.space_after = Pt(self.rules.item_space_after_pt)
                if self.rules.enforce_layout:
                    pf.space_before = Pt(0)
            self._runs(para, block.runs)
            return
        para, styled = self._para("SMD Body")
        if (not styled or self.rules.enforce_layout) and self.rules.body_line_spacing != 1.0:
            para.paragraph_format.line_spacing = self.rules.body_line_spacing
        if self.rules.enforce_layout:
            para.paragraph_format.space_after = Pt(0)
            if self.rules.body_first_line_indent_in is not None:
                para.paragraph_format.first_line_indent = Inches(self.rules.body_first_line_indent_in)
        self._runs(para, block.runs)

    def _bullet(self, block: g.Bullet) -> None:
        from docx.shared import Inches

        if usable_paragraph_style(self.doc, "List Bullet"):
            try:
                para = self.doc.add_paragraph(style="List Bullet")
                self.report.styles_honored.append("List Bullet")
                self._runs(para, block.runs)
                return
            except (KeyError, ValueError):
                self.report.fallbacks.append("List Bullet")
        para = self.doc.add_paragraph()
        para.paragraph_format.left_indent = Inches(0.5)
        para.paragraph_format.first_line_indent = Inches(-0.25)
        self._font(para.add_run("•\t"))
        self._runs(para, block.runs)

    def _numbered(self, block: g.Numbered) -> None:
        from docx.shared import Inches

        para, _ = self._para("SMD Body")
        para.paragraph_format.left_indent = Inches(0.5)
        para.paragraph_format.first_line_indent = Inches(-0.5)
        if self.rules.enforce_layout and self.rules.body_line_spacing != 1.0:
            para.paragraph_format.line_spacing = self.rules.body_line_spacing
        self._font(para.add_run(f"{block.label}\t"))
        self._runs(para, block.runs)

    def _table(self, block: g.Table) -> None:
        self.in_item = False
        self.tables_seen += 1
        self.report.blocks_styled["tables"] += 1
        ncols = max(len(row) for row in block.rows)
        table = self.doc.add_table(rows=len(block.rows), cols=ncols)
        caption = self.rules.caption_table_first and self.tables_seen == 1
        set_table_borders(table, inside_vertical_only=caption)
        for r_idx, row in enumerate(block.rows):
            for c_idx in range(ncols):
                cell = table.cell(r_idx, c_idx)
                runs = row[c_idx] if c_idx < len(row) else ()
                para = cell.paragraphs[0]
                if caption and usable_paragraph_style(self.doc, "SMD Caption"):
                    try:
                        para.style = self.doc.styles["SMD Caption"]
                        self.report.styles_honored.append("SMD Caption")
                    except (KeyError, ValueError):
                        self.report.fallbacks.append("SMD Caption")
                self._runs(para, runs, bold=(block.header and r_idx == 0))
        # Breathing room after a table: an empty body paragraph.
        self.doc.add_paragraph()

    def _hrule(self) -> None:
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn

        self.in_item = False
        para = self.doc.add_paragraph()
        ppr = para._p.get_or_add_pPr()
        pbdr = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), "6")
        bottom.set(qn("w:space"), "1")
        bottom.set(qn("w:color"), "auto")
        pbdr.append(bottom)
        ppr.append(pbdr)

    # -- footer --------------------------------------------------------------------

    def ensure_page_number(self, *, always: bool = False) -> None:
        """Add a centered PAGE field to the footer when the base has no footer
        text at all. A base with its own footer (a letterhead's address line, a
        firm's own page numbering) is left exactly as the firm built it, unless
        the class numbers its pages ``always``: then a centered PAGE paragraph
        is added below the firm's footer text when it carries no PAGE field."""
        footer = self.doc.sections[0].footer
        existing = "".join(p.text for p in footer.paragraphs).strip()
        if always:
            if footer_has_page_field(footer):
                return
            para = footer.add_paragraph() if existing or not footer.paragraphs else footer.paragraphs[0]
        else:
            if existing or footer_has_any_field(footer):
                return
            para = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
        self._font(add_page_field(para))
        self.report.notes.append("page number field added to the footer")


__all__ = [
    "CLASS_RULES",
    "DEFAULT_FONT",
    "DEFAULT_SIZE_PT",
    "DOCUMENT_CLASSES",
    "NAMED_STYLES",
    "ROLE_FALLBACK",
    "ClassRules",
    "FormatRefused",
    "FormatReport",
    "has_style",
    "load_firm_identity",
    "open_as_base",
    "render_document",
    "usable_paragraph_style",
]
