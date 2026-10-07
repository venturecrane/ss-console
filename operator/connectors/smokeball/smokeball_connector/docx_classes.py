"""Document classes and their per-class styling rules (no python-docx import).

Split out of ``docx_format`` for size only; ``docx_format`` re-exports every
name here. See that module's docstring for the doctrine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .docx_format_types import DEFAULT_FONT, DEFAULT_SIZE_PT

# ---- Document classes and their styling rules --------------------------------

DOCUMENT_CLASSES = (
    "discovery_set",
    "discovery_response",
    "demand_letter",
    "mediation_brief",
    "memo",
    "depo_outline",
    "letter",
)

# Label shapes the renderer STYLES (it never writes them). Anchored at line
# start, whole-line by construction of the skeletons ("**SPECIAL INTERROGATORY
# NO. 7:**"). A mid-paragraph mention is prose and stays body text.
_DISCOVERY_LABEL = re.compile(
    r"^(?:RESPONSE TO |SUPPLEMENTAL RESPONSE TO )?"
    r"(?:SPECIAL INTERROGATOR(?:Y|IES)|FORM INTERROGATOR(?:Y|IES)|"
    r"REQUESTS? FOR (?:PRODUCTION|ADMISSION|INSPECTION)|INSPECTION DEMANDS?|"
    r"DEMANDS? FOR (?:PRODUCTION|INSPECTION))"
    r"\s+NO\.?\s*\S",
    re.IGNORECASE,
)
_DEFINITION_LABEL = re.compile(r"^DEFINITIONS?\b", re.IGNORECASE)
_LABEL_MAX_CHARS = 90


@dataclass(frozen=True)
class ClassRules:
    """Per-class styling decisions. Typography values here are the PRODUCT
    DEFAULTS used only when the base template lacks the named style; they are
    deliberately the authored standards of the first engagements (plain Word,
    not pleading paper) and are labeled as starters the firm edits in Word."""

    label_patterns: tuple[re.Pattern[str], ...] = ()
    # Paragraphs following a label (until the next label/heading/table) are item
    # text: first-line indented, with the "between items" spacing as spacing
    # after the paragraph (the authored standard: double-spaced BETWEEN requests,
    # not more).
    item_text: bool = False
    caption_table_first: bool = False  # first table is the caption (court docs)
    heading_align: tuple[str, str, str] = ("center", "left", "left")
    heading_underline: tuple[bool, bool, bool] = (False, True, False)
    heading_indent_in: tuple[float, float, float] = (0.0, 0.5, 1.0)
    heading_bold: tuple[bool, bool, bool] = (True, True, True)
    page_numbers: bool = True
    # A PAGE field is added even when the base footer carries text of its own
    # (a deposition outline is numbered at the bottom, always).
    page_numbers_always: bool = False
    body_line_spacing: float = 1.0  # multiple
    item_line_spacing: float = 2.0
    item_space_after_pt: float = 0.0
    first_line_indent_in: float = 0.5
    # The engaged firm's attorney's house style, enforced IN-CLASS: every run
    # carries this font and size, and the class's paragraph layout and heading
    # emphasis are applied even when a named or delegated style carries the
    # paragraph, so a base style cannot move a served document off the
    # attorney's standard. None leaves typography to the base (the letters).
    font: str | None = None
    font_size_pt: float | None = None
    enforce_layout: bool = False
    # Plain US Letter paper; line numbering (pleading paper) removed.
    letter_paper: bool = False


def _house(**kw) -> ClassRules:
    """A drafting class held to the house standard: Times New Roman 12 on every
    run, plain Letter paper, layout enforced in-class."""
    return ClassRules(font=DEFAULT_FONT, font_size_pt=DEFAULT_SIZE_PT, enforce_layout=True, letter_paper=True, **kw)


_DISCOVERY = dict(
    label_patterns=(_DISCOVERY_LABEL, _DEFINITION_LABEL),
    item_text=True,
    caption_table_first=True,
    # Double-spaced, and double-spaced BETWEEN items only: the label and the
    # request are double-spaced lines with no space before or after, so the
    # gap between two requests is one double-spaced line and never more.
    item_line_spacing=2.0,
    item_space_after_pt=0.0,
    body_line_spacing=2.0,
)


CLASS_RULES: dict[str, ClassRules] = {
    "discovery_set": _house(**_DISCOVERY),
    "discovery_response": _house(**_DISCOVERY),
    "demand_letter": ClassRules(page_numbers=True, heading_align=("left", "left", "left")),
    # Roman-numeral headings centered and bold only (never underlined);
    # lettered subsections indented, bold AND underlined; body double-spaced.
    "mediation_brief": _house(
        caption_table_first=True,
        heading_align=("center", "left", "left"),
        heading_underline=(False, True, False),
        heading_bold=(True, True, True),
        heading_indent_in=(0.0, 0.5, 1.0),
        body_line_spacing=2.0,
    ),
    "memo": _house(heading_align=("left", "left", "left"), heading_underline=(False, False, False)),
    "depo_outline": _house(
        heading_align=("left", "left", "left"),
        heading_underline=(False, False, False),
        heading_indent_in=(0.0, 0.0, 0.5),
        page_numbers_always=True,
    ),
    "letter": ClassRules(heading_align=("left", "left", "left"), page_numbers=False),
}
