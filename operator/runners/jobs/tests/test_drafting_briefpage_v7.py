"""The attorney's permanent mediation-brief rules of 2026-11-04, 5:03 PM, as
the brief he accepted that evening (v7) lays them: two blank single lines from
the party represented to the court's name; the caption's bottom rule on its
left cell only, ending at the vertical line; the signature block single spaced
under two blank signature lines (firm, two blanks, "Dated: ... By: ...", the
name lines)."""

from __future__ import annotations

import copy

import docx
import pytest
from docx.oxml.ns import qn

from drafting_testkit import SECTIONS, firm_data
from jobs.drafting import briefpage, firm, format_check, frontpage, pleading, render

CLS = "mediation_brief"
FRONT = (
    "Alpha Example, Esq.\nExample & Example, LLP\n\nAttorneys for Plaintiff\nGAMMA EXAMPLE\n\n"
    "**SUPERIOR COURT OF THE STATE OF CALIFORNIA**\n**COUNTY OF EXAMPLETOWN**\n\n"
    "| GAMMA EXAMPLE, Plaintiff, v. DELTA EXAMPLE, Defendant. | Case No. CV-0001 |\n| --- | --- |\n"
    "| | Mediation Date: 11/17/26 |\n| | Time: 1:30 pm |\n\n"
)
SIGNATURE = (
    "\n\nDated: __________\n\n**ASHTON EXAMPLE, LLP**\n\nBy: ______________________________\n"
    "Alpha Example, Esq.\nAttorneys for Plaintiff\nGAMMA EXAMPLE\n"
)
V7 = {
    "front_single_spaced": True,
    "front_attorney_gap_lines": 1,
    "front_court_gap_lines": 2,
    "front_caption_gap_lines": 1,
    "caption_rule_below": "left",
    "signature_date_beside": True,
    "signature_single_spaced": True,
    "signature_gap_lines": 2,
}


def _fmt(**layout):
    f = copy.deepcopy(firm_data()["format"])
    f["layout"][CLS] = {**f["layout"][CLS], **layout}
    f["pleading"] = {"lines": 28, "line_pitch_pt": 24, "margins_in": [0.5, 0.5, 0.69, 1.5]}
    return f


def _render(tmp_path, fmt, paper="pleading"):
    md = (
        FRONT
        + "\n\n".join(f"# {s}\n\nBody text." for s in SECTIONS)
        + "\n\n{{ATTORNEY: settlement authority}}"
        + SIGNATURE
    )
    path, _ = render.render(md, CLS, tmp_path / "b.docx", fmt)
    if paper == "pleading":
        pleading.apply_for(path, fmt, CLS)
    return path


@pytest.mark.parametrize("paper", ["plain", "pleading"])
def test_the_v7_layout_renders_and_passes(tmp_path, paper):
    fmt = _fmt(**V7)
    path = _render(tmp_path, fmt, paper)
    res = format_check.check(path, CLS, fmt, "", None, paper)
    assert res.ok, res.fails
    d = docx.Document(str(path))
    texts = [p.text for p in d.paragraphs]
    g, c = texts.index("GAMMA EXAMPLE"), next(i for i, t in enumerate(texts) if t.startswith("SUPERIOR COURT"))
    assert texts[g + 1 : c] == ["", ""]  # two blank lines, party to court
    tbl = d.tables[0]._tbl
    last = tbl.findall(qn("w:tr"))[-1].findall(qn("w:tc"))
    assert last[0].find(qn("w:tcPr")).find(qn("w:tcBorders")).find(qn("w:bottom")) is not None
    assert tbl.tblPr.find(qn("w:tblBorders")).find(qn("w:bottom")) is None
    i = next(i for i, t in enumerate(texts) if "\tBy:" in t)
    assert texts[i - 3 : i] == ["ASHTON EXAMPLE, LLP", "", ""]  # firm, two blank signature lines
    sig = [d.paragraphs[j] for j in range(i, i + 4)]
    assert [p.text for p in sig[1:]] == ["Alpha Example, Esq.", "Attorneys for Plaintiff", "GAMMA EXAMPLE"]
    assert all(briefpage.single_ok(p) for p in sig)


@pytest.mark.parametrize(
    "broken, expect",
    [
        ({"front_court_gap_lines": 0}, "blank line(s) before the court's name"),
        ({"caption_rule_below": True}, "runs past its vertical line"),
        ({"caption_rule_below": False}, "no solid line under the caption's left cell"),
        ({"signature_gap_lines": 0}, "blank line(s) above the Dated/By line"),
        ({"signature_single_spaced": False}, "not single spaced"),
    ],
)
def test_each_v7_rule_fails_when_the_render_breaks_it(tmp_path, broken, expect):
    path = _render(tmp_path, _fmt(**{**V7, **broken}))
    res = format_check.check(path, CLS, _fmt(**V7), "", None, "pleading")
    assert any(expect in f for f in res.fails), res.fails


def test_the_firm_config_takes_the_v7_keys():
    lay = copy.deepcopy(firm_data()["format"]["layout"])
    lay[CLS].update(V7)
    assert firm._layout(lay) == []
    lay[CLS]["caption_rule_below"] = "right"
    assert firm._layout(lay)


def test_the_court_gap_is_counted_from_the_court_line():
    assert frontpage.gaps(_fmt(**V7), CLS) == (1, 2, 1)
