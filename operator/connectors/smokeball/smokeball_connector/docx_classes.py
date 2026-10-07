"""Document classes, their product-default styling rules, and the optional
firm house-style override (no python-docx import).

Split out of ``docx_format`` for size only; ``docx_format`` re-exports every
name here. See that module's docstring for the doctrine.

Two layers, deliberately:

* ``CLASS_RULES`` are the PRODUCT DEFAULTS, the same for every seat and every
  in-turn skill: used only where the firm's own Word template lacks a named
  style, and never overriding one the template defines.
* ``HouseStyle`` is a FIRM-AUTHORED override, passed to ``render_document`` by
  a caller that holds an authored house style (the drafting job builds it from
  the firm's ``drafting-firm.yaml`` ``format`` block). With it, the stated
  typography and layout are enforced in-class, over the base's styles, because
  the firm's own written instructions say what a served document looks like.
  Without it, rendering is exactly the product default. No firm's values live
  in this file.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass

# ---- Document classes and their styling rules --------------------------------


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
    not pleading paper) and are labeled as starters the firm edits in Word.

    The fields after ``first_line_indent_in`` are neutral by default and are
    set only by a firm's ``HouseStyle`` (``apply_house``)."""

    label_patterns: tuple[re.Pattern[str], ...] = ()
    # Paragraphs following a label (until the next label/heading/table) are item
    # text: first-line indented, with the "between items" spacing as spacing
    # after the paragraph.
    item_text: bool = False
    caption_table_first: bool = False  # first table is the caption (court docs)
    heading_align: tuple[str, str, str] = ("center", "left", "left")
    heading_underline: tuple[bool, bool, bool] = (False, True, False)
    heading_indent_in: tuple[float, float, float] = (0.0, 0.5, 1.0)
    heading_bold: tuple[bool, bool, bool] = (True, True, True)
    page_numbers: bool = True
    body_line_spacing: float = 1.0  # multiple
    item_line_spacing: float = 2.0
    item_space_after_pt: float = 0.0
    first_line_indent_in: float = 0.5
    # ---- set only by a firm HouseStyle --------------------------------------------
    # Every run carries this font and size, and the class's layout and heading
    # emphasis are applied over a named or delegated style (enforce_layout).
    font: str | None = None
    font_size_pt: float | None = None
    enforce_layout: bool = False
    letter_paper: bool = False  # plain US Letter; line numbering removed
    page_numbers_always: bool = False  # a PAGE field even under a firm footer's text
    footer_title: str | None = None  # a centered line under the page number
    body_first_line_indent_in: float | None = None
    body_exact_pt: float | None = None  # exact line spacing in points
    body_justify: bool = False
    front_matter_plain: bool = False  # before the caption: left; all-bold lines centered
    bold_italic_heading_indent_in: float | None = None  # a bold-italic line alone is a heading


CLASS_RULES: dict[str, ClassRules] = {
    "discovery_set": ClassRules(
        label_patterns=(_DISCOVERY_LABEL, _DEFINITION_LABEL),
        item_text=True,
        caption_table_first=True,
        item_line_spacing=2.0,
        item_space_after_pt=12.0,
    ),
    "discovery_response": ClassRules(
        label_patterns=(_DISCOVERY_LABEL, _DEFINITION_LABEL),
        item_text=True,
        caption_table_first=True,
        item_line_spacing=2.0,
        item_space_after_pt=12.0,
    ),
    "demand_letter": ClassRules(page_numbers=True, heading_align=("left", "left", "left")),
    "mediation_brief": ClassRules(
        caption_table_first=True,
        heading_align=("center", "left", "left"),
        heading_underline=(False, True, False),
        body_line_spacing=2.0,
    ),
    "memo": ClassRules(heading_align=("left", "left", "left"), page_numbers=True),
    "depo_outline": ClassRules(heading_align=("left", "left", "left"), page_numbers=True),
    "letter": ClassRules(heading_align=("left", "left", "left"), page_numbers=False),
}


@dataclass(frozen=True)
class HouseStyle:
    """A firm's authored house style for one document class. Every field is
    optional: an unset field keeps the class's product default. Setting any
    field enforces the class's layout in-class (``enforce_layout``)."""

    font: str | None = None
    size_pt: float | None = None
    plain_letter_paper: bool = False
    heading_indent_in: tuple[float, float, float] | None = None
    heading_underline: tuple[bool, bool, bool] | None = None
    body_line_spacing: float | None = None  # a multiple (2.0 = double)
    body_line_spacing_pt: float | None = None  # exact points (wins over the multiple)
    body_justify: bool = False
    body_first_line_indent_in: float | None = None
    item_line_spacing: float | None = None
    item_space_after_pt: float | None = None
    centered_court_lines: bool = False
    bold_italic_heading_indent_in: float | None = None
    page_numbers_always: bool = False
    footer_title: str | None = None


def apply_house(rules: ClassRules, house: HouseStyle | None) -> ClassRules:
    """The class's rules with the firm's house style laid over them."""
    if house is None:
        return rules
    over: dict[str, object] = {"enforce_layout": True, "font": house.font, "font_size_pt": house.size_pt}
    pairs = (
        ("letter_paper", house.plain_letter_paper or None),
        ("heading_indent_in", house.heading_indent_in),
        ("heading_underline", house.heading_underline),
        ("body_line_spacing", house.body_line_spacing),
        ("body_exact_pt", house.body_line_spacing_pt),
        ("body_justify", house.body_justify or None),
        ("body_first_line_indent_in", house.body_first_line_indent_in),
        ("item_line_spacing", house.item_line_spacing),
        ("item_space_after_pt", house.item_space_after_pt),
        ("front_matter_plain", house.centered_court_lines or None),
        ("bold_italic_heading_indent_in", house.bold_italic_heading_indent_in),
        ("page_numbers_always", house.page_numbers_always or None),
        ("footer_title", house.footer_title),
    )
    over.update({k: v for k, v in pairs if v is not None})
    if house.page_numbers_always or house.footer_title:
        over["page_numbers"] = True
    return dataclasses.replace(rules, **over)  # type: ignore[arg-type]
