"""Shared types and constants for the format renderer (no python-docx import)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Product default typography (the starter look). One place, by design.
DEFAULT_FONT = "Times New Roman"
DEFAULT_SIZE_PT = 12

NAMED_STYLES = (
    "SMD Body",
    "SMD Item Label",
    "SMD Item Text",
    "SMD Heading 1",
    "SMD Heading 2",
    "SMD Heading 3",
    "SMD Caption",
    "SMD Signature",
)

# When a firm's own template does not define one of our named styles, fall back
# to the CONVENTIONAL Word style that means the same thing, so a style the firm
# edits in Word still reaches the draft. Deliberately three rows.
#
# Everything else is NOT here, each for cause:
#
#   SMD Body, SMD Item Text -> Normal
#       Pointless at best, destructive at worst. A plain paragraph already
#       resolves to Normal, so font and size follow the firm's template today;
#       what the inline branch adds is the class's LAYOUT (first-line indent,
#       the double-spacing between discovery items, space-after). Delegating
#       would suppress that and serve a single-spaced discovery set.
#   SMD Caption -> Caption
#       Different meanings that share a word. Ours is the pleading CASE-caption
#       table; Word's is a figure/table caption, typically 9pt italic and
#       theme-coloured. Delegating puts a pleading caption in 9pt italic blue.
#   SMD Item Label, SMD Signature
#       No conventional Word equivalent exists. Inline is the honest answer.
ROLE_FALLBACK: dict[str, str] = {
    "SMD Heading 1": "Heading 1",
    "SMD Heading 2": "Heading 2",
    "SMD Heading 3": "Heading 3",
}


class FormatRefused(RuntimeError):
    """The base document cannot be used as a template; nothing is rendered."""


@dataclass
class FormatReport:
    """What the renderer actually applied, for the delivery note. Honest by
    construction: every fallback is listed, the base's header/footer text is
    surfaced (it bypasses every content gate), and ``templateExpected`` lets
    the note say "the firm's template did not resolve" instead of a null that
    reads like "never configured".

    Three states per role, and the difference is what the firm can control:
    ``stylesHonored`` (the base defines our named style), ``stylesDelegated``
    (role -> the base's own conventional style; a firm edit to THAT style
    reaches the next draft), and ``fallbacks`` (formatted inline, so the
    typography is fixed in this document and a later template edit will not
    move it)."""

    document_class: str
    template_used: dict[str, Any] | None = None
    template_expected: bool = False
    class_template_name: str | None = None
    base_header_footer_text: list[str] = field(default_factory=list)
    styles_honored: list[str] = field(default_factory=list)
    styles_delegated: dict[str, str] = field(default_factory=dict)
    fallbacks: list[str] = field(default_factory=list)
    blocks_styled: dict[str, int] = field(default_factory=lambda: {"labels": 0, "tables": 0, "headings": 0})
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return {
            "class": d["document_class"],
            "templateUsed": d["template_used"],
            "templateExpected": d["template_expected"],
            "classTemplateName": d["class_template_name"],
            "baseHeaderFooterText": d["base_header_footer_text"],
            "stylesHonored": sorted(set(d["styles_honored"])),
            "stylesDelegated": d["styles_delegated"],
            "fallbacks": sorted(set(d["fallbacks"])),
            "blocksStyled": d["blocks_styled"],
            "notes": d["notes"],
        }
