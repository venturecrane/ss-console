"""The firm's authored house style, as the renderer's override.

``drafting-firm.yaml``'s ``format`` block is the only source: the font and
size, plain Letter paper, and ``format.layout.<class>`` (spacing, indents,
heading emphasis per level, court lines, provider headings, page numbers, a
footer title). The renderer (``smokeball_connector.docx_classes.HouseStyle``)
holds no firm's values; this module builds the override the drafting job
passes, and ``format_check`` builds the same one to read the document back
against, so the rendered file and the check agree by construction on what the
firm wrote.
"""

from __future__ import annotations

from typing import Any


def house_for(fmt: dict[str, Any], cls: str) -> Any:
    from smokeball_connector.docx_classes import HouseStyle

    lay = dict((fmt.get("layout") or {}).get(cls) or {})

    def tup(key: str) -> tuple | None:
        v = lay.get(key)
        return tuple(v) if isinstance(v, list) else None

    return HouseStyle(
        font=str(fmt["font"]),
        size_pt=float(fmt["size_pt"]),
        plain_letter_paper=bool(fmt.get("plain_letter_paper")),
        heading_indent_in=tup("heading_indent_in"),  # type: ignore[arg-type]
        heading_underline=tup("heading_underline"),  # type: ignore[arg-type]
        body_line_spacing=lay.get("line_spacing"),
        body_line_spacing_pt=lay.get("line_spacing_pt"),
        body_justify=bool(lay.get("justify")),
        body_first_line_indent_in=lay.get("first_line_indent_in"),
        item_line_spacing=lay.get("item_line_spacing"),
        item_space_after_pt=lay.get("item_space_after_pt"),
        centered_court_lines=bool(lay.get("centered_court_lines")),
        bold_italic_heading_indent_in=lay.get("bold_italic_heading_indent_in"),
        page_numbers_always=bool(lay.get("page_numbers_always"))
        or (cls == "depo_outline" and bool(fmt.get("depo_outline_page_numbers"))),
        footer_title=lay.get("footer_title"),
    )


def rules_for(fmt: dict[str, Any], cls: str) -> Any:
    """The class's effective rules: the product defaults with the firm's
    house style laid over them (what the renderer applied)."""
    from smokeball_connector.docx_classes import CLASS_RULES, apply_house

    return apply_house(CLASS_RULES[cls], house_for(fmt, cls))
