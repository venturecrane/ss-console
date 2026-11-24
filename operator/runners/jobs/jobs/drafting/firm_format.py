"""The ``format`` section's closed sub-schemas of ``drafting-firm.yaml``:
the per-class layout keys and their types, where each key belongs, and the
pleading paper. Split from ``firm.py`` at the module-size ceiling; ``firm.py``
re-exports every name, so callers are unchanged."""

from __future__ import annotations

import re

#: format.layout.<class> keys and their types (closed).
LAYOUT_KEYS = {
    "line_spacing": "num",  # a multiple: 2.0 is double
    "line_spacing_pt": "num",  # exact points: wins over line_spacing
    "justify": "bool",
    "first_line_indent_in": "num",
    "heading_indent_in": "num3",
    "heading_underline": "bool3",
    "item_line_spacing": "num",
    "item_space_after_pt": "num",
    "centered_court_lines": "bool",
    "bold_italic_heading_indent_in": "num",
    "page_numbers_always": "bool",
    "footer_title": "str",
    # The attorney's spacing of headings and his charts (2026-11-04, Chris
    # Price: double spaced including the headings; charts boxed, larger, with
    # some colour).
    "heading_line_spacing": "num",
    "heading_line_spacing_pt": "num",  # exact points: wins over the multiple
    "heading_space_before_pt": "num",
    "heading_space_after_pt": "num",
    "table_font_pt": "pt",  # every table but the caption
    "table_header_fill": "hex",  # RRGGBB
    "table_total_fill": "hex",  # a row whose first cell starts with "Total"
    "table_border_pt": "border",
    # The first page's gaps, in blank lines (frontpage.py; A&P, 2026-11-04).
    "front_attorney_gap_lines": "lines",
    "front_caption_gap_lines": "lines",
    # The first page single spaced, a rule under the caption, the date beside
    # the "By:" line (briefpage.py; A&P, 2026-11-04 3:28 PM).
    "front_single_spaced": "bool",
    "caption_rule_below": "rule",  # true (full width) or "left" (to the caption's vertical line)
    "signature_date_beside": "bool",
    # Chris, 2026-11-04 5:03 PM: the Dated/By and name lines single spaced, the
    # blank signature lines above them, two blank lines from "Attorneys for"
    # to the court (frontpage.py).
    "signature_single_spaced": "bool",
    "signature_gap_lines": "lines",
    "front_court_gap_lines": "lines",
}

#: Where each firm-authored format key belongs (ADR 0083 amendment 2026-11-10).
#: ``template``: a Word paragraph, character, table or section style can carry
#: it, so it belongs in the firm's template (item 1); the yaml copy is a known
#: violation that shrinks as the styles base (#3905) takes it over.
#: ``firm_yaml``: no style can express it, because it depends on the document's
#: structure (blank lines between front-page blocks, a row picked by its text,
#: one block placed beside another, a rule drawn to the caption grid) or is
#: per-document text; it stays in drafting-firm.yaml, listed here (item 4).
#: Rule-derived layout (pleading paper, line pitch) is not here: it is code in
#: ``pleading.py`` (item 2).
FORMAT_KEY_HOME: dict[str, str] = {
    "format.font": "template",
    "format.size_pt": "template",
    **{
        f"format.layout.{k}": "template"
        for k in (
            "line_spacing",
            "line_spacing_pt",
            "justify",
            "first_line_indent_in",
            "heading_indent_in",
            "heading_underline",
            "item_line_spacing",
            "item_space_after_pt",
            "centered_court_lines",
            "bold_italic_heading_indent_in",
            "page_numbers_always",
            "heading_line_spacing",
            "heading_line_spacing_pt",
            "heading_space_before_pt",
            "heading_space_after_pt",
            "table_font_pt",
            "table_header_fill",
            "table_border_pt",
            "front_single_spaced",
            "signature_single_spaced",
        )
    },
    **{
        f"format.layout.{k}": "firm_yaml"
        for k in (
            "footer_title",  # per-document text, not a style
            "table_total_fill",  # a row chosen by its first cell's text
            "front_attorney_gap_lines",  # blank lines between front-page blocks
            "front_caption_gap_lines",
            "front_court_gap_lines",
            "signature_gap_lines",
            "caption_rule_below",  # a rule drawn to the caption grid's vertical line
            "signature_date_beside",  # one block placed beside another
        )
    },
}


def _num(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0


def _layout_value_ok(value: object, kind: str) -> bool:
    num = _num
    if kind == "num":
        return num(value)
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "str":
        return isinstance(value, str) and bool(value.strip())
    if kind == "num3":
        return isinstance(value, list) and len(value) == 3 and all(num(v) for v in value)
    if kind == "pt":
        return num(value) and 8 <= float(value) <= 20  # type: ignore[arg-type]
    if kind == "border":
        return num(value) and 0.25 <= float(value) <= 6  # type: ignore[arg-type]
    if kind == "rule":
        return isinstance(value, bool) or value == "left"
    if kind == "lines":
        return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 6
    if kind == "hex":
        return isinstance(value, str) and bool(re.fullmatch(r"[0-9A-Fa-f]{6}", value))
    if kind == "bool3":
        return isinstance(value, list) and len(value) == 3 and all(isinstance(v, bool) for v in value)
    return False


PLEADING_KEYS = ("lines", "line_pitch_pt", "margins_in")


def _pleading(p: dict) -> list[str]:
    where = "format.pleading"
    out = [f"{where}.{k}: unknown key (closed key set)" for k in p if k not in PLEADING_KEYS]
    out += [f"{where}.{k}: required" for k in PLEADING_KEYS if k not in p]
    if out:
        return out
    if not (isinstance(p["lines"], int) and not isinstance(p["lines"], bool) and 20 <= p["lines"] <= 40):
        out.append(f"{where}.lines: expected an integer 20..40")
    if not (_num(p["line_pitch_pt"]) and 12 <= p["line_pitch_pt"] <= 36):
        out.append(f"{where}.line_pitch_pt: expected 12..36")
    m = p["margins_in"]
    if not (isinstance(m, list) and len(m) == 4 and all(_num(x) and x <= 3 for x in m)):
        out.append(f"{where}.margins_in: expected [top, right, bottom, left] in inches")
    elif 11.0 - m[0] - m[2] < p["lines"] * float(p["line_pitch_pt"]) / 72.0:
        out.append(f"{where}: {p['lines']} lines at {p['line_pitch_pt']} point do not fit between the margins")
    return out


def layout_errors(layout: dict, classes: tuple[str, ...]) -> list[str]:
    out = []
    for cls, body in layout.items():
        where = f"format.layout.{cls}"
        if cls not in classes:
            out.append(f"{where}: unknown class (expected {list(classes)})")
            continue
        if not isinstance(body, dict):
            out.append(f"{where}: expected a map")
            continue
        out += [f"{where}.{k}: unknown key (closed key set)" for k in body if k not in LAYOUT_KEYS]
        out += [
            f"{where}.{k}: expected {LAYOUT_KEYS[k]}"
            for k, v in body.items()
            if k in LAYOUT_KEYS and not _layout_value_ok(v, LAYOUT_KEYS[k])
        ]
    return out
