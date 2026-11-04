"""The attorney's authored heading spacing and chart look (2026-11-04).

The firm asked for briefs double spaced including the headings, and for the
damages charts boxed, in a larger font, with some colour. Each option is a key
under ``format.layout.<class>``: the loader accepts it (closed schema), the
renderer applies it, and ``format_check`` reads the document back against it,
so a 14-point chart passes where the firm authored 14 point and fails where it
did not. Every falsifier renders under one authored value and checks against
another.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from drafting_testkit import COURT, SECTIONS, firm_data
from jobs.drafting import firm, format_check, render

FMT = firm_data()["format"]
STYLE = {
    "heading_line_spacing": 2.0,
    "heading_space_before_pt": 0,
    "heading_space_after_pt": 0,
    "table_font_pt": 14,
    "table_header_fill": "D9E2F3",
    "table_total_fill": "FFF2CC",
    "table_border_pt": 1.0,
}
CHART = (
    "\n\n| Provider | Billed |\n| --- | --- |\n| Northfield Physical Therapy | $1,200.00 |\n"
    "| Exampletown Imaging | $800.00 |\n| **Total** | **$2,000.00** |\n"
)


def _fmt(**layout: Any) -> dict[str, Any]:
    f = copy.deepcopy(FMT)
    f["layout"]["mediation_brief"] = {**f["layout"]["mediation_brief"], **layout}
    return f


def _brief() -> str:
    body = "\n\n".join(f"# {s}\n\nBody text for this section." for s in SECTIONS)
    body = body.replace("# III. DAMAGES SUMMARY\n\nBody text for this section.", "# III. DAMAGES SUMMARY" + CHART, 1)
    return COURT + body + "\n\n{{ATTORNEY: settlement authority, the target figure, and any bracket}}"


def _check(tmp_path: Path, render_fmt: dict, check_fmt: dict | None = None) -> format_check.Result:
    path, _ = render.render(_brief(), "mediation_brief", tmp_path / "brief.docx", render_fmt)
    return format_check.check(path, "mediation_brief", check_fmt or render_fmt, "", None)


def test_the_firm_config_accepts_the_style_keys_and_refuses_bad_values():
    layout = copy.deepcopy(FMT["layout"])
    layout["mediation_brief"].update(STYLE)
    assert firm._layout(layout) == []
    bads = (("table_font_pt", 30), ("table_header_fill", "blue"), ("table_border_pt", 0), ("heading_line_spacing", "2"))
    for key, bad in bads:
        lay = copy.deepcopy(layout)
        lay["mediation_brief"][key] = bad
        assert any(f"format.layout.mediation_brief.{key}" in e for e in firm._layout(lay)), key


def test_a_brief_under_the_authored_style_passes_and_carries_it(tmp_path):
    import docx
    from docx.shared import Pt

    res = _check(tmp_path, _fmt(**STYLE))
    assert res.ok, res.fails
    doc = docx.Document(str(tmp_path / "brief.docx"))
    heads = [p for p in doc.paragraphs if p.style is not None and p.style.name.startswith("SMD Heading")]
    heads = heads or [p for p in doc.paragraphs if p.text.strip().startswith("I. INTRODUCTION")]
    assert heads and all(p.paragraph_format.line_spacing == 2.0 for p in heads)
    chart = doc.tables[-1]
    assert {r.font.size for c in chart._cells for p in c.paragraphs for r in p.runs if r.text.strip()} == {Pt(14)}
    caption = doc.tables[0]
    assert {r.font.size for c in caption._cells for p in c.paragraphs for r in p.runs if r.text.strip()} <= {Pt(12)}


def test_exact_heading_spacing_wins_and_is_checked(tmp_path):
    exact = _fmt(**{**STYLE, "heading_line_spacing_pt": 24})
    assert _check(tmp_path, exact).ok
    res = _check(tmp_path, _fmt(**STYLE), exact)
    assert any("not exactly 24 point" in f for f in res.fails), res.fails


def test_without_the_style_a_14_point_chart_is_refused(tmp_path):
    res = _check(tmp_path, _fmt(**STYLE), FMT)
    assert any(f.startswith("font:") for f in res.fails), res.fails


@pytest.mark.parametrize(
    "rendered, expect",
    [
        ({"heading_line_spacing": None}, "not at 2.0 line spacing"),
        ({"heading_space_before_pt": 12}, "space before not 0 point"),
        ({"heading_line_spacing_pt": 24}, "not at 2.0 line spacing"),
        ({"table_font_pt": 12}, "font:"),
        ({"table_header_fill": "FFFFFF"}, "header row is not shaded D9E2F3"),
        ({"table_total_fill": None}, "total row is not shaded FFF2CC"),
        ({"table_border_pt": None}, "not boxed and ruled at 1.0 point"),
    ],
)
def test_each_authored_option_fails_when_the_render_breaks_it(tmp_path, rendered, expect):
    layout = {k: v for k, v in {**STYLE, **rendered}.items() if v is not None}
    res = _check(tmp_path, _fmt(**layout), _fmt(**STYLE))
    assert any(expect in f for f in res.fails), res.fails
    assert res.ours  # our render's defect, never a content repair
