"""The attorney's permanent mediation-brief rules of 2026-11-04, 3:28 PM:
the first page single spaced, a solid line under the case caption, the date
beside the "By:" line, and the opening demand computed by code from the
damages chart."""

from __future__ import annotations

import copy

import docx
import pytest

from drafting_testkit import COURT, SECTIONS, firm_data
from jobs.drafting import firm, format_check, opening_demand, pleading, render

CLS = "mediation_brief"
RULES = {"front_single_spaced": True, "caption_rule_below": True, "signature_date_beside": True}
SIGNATURE = (
    "\n\nDated: __________\n\n**ASHTON EXAMPLE, LLP**\n\nBy: ______________________________\n"
    "Alpha Example, Esq.\nAttorneys for Plaintiff\n"
)
SPEC = pleading.Spec(lines=28, pitch_pt=24, margins_in=(0.5, 0.5, 0.69, 1.5), font="Times New Roman", size_pt=12)


def _fmt(**layout):
    f = copy.deepcopy(firm_data()["format"])
    f["layout"][CLS] = {**f["layout"][CLS], **layout}
    f["pleading"] = {"lines": 28, "line_pitch_pt": 24, "margins_in": [0.5, 0.5, 0.69, 1.5]}
    return f


def _brief(extra: str = SIGNATURE) -> str:
    return (
        COURT
        + "PLAINTIFF'S MEDIATION BRIEF\n\nMediation: March 3, 2027, 10:00 a.m.\n\n"
        + "\n\n".join(f"# {s}\n\nBody text." for s in SECTIONS)
        + "\n\n{{ATTORNEY: settlement authority}}"
        + extra
    )


def _render(tmp_path, fmt, paper="plain"):
    path, _ = render.render(_brief(), CLS, tmp_path / "b.docx", fmt)
    if paper == "pleading":
        pleading.apply_for(path, fmt, CLS)
    return path


@pytest.mark.parametrize("paper", ["plain", "pleading"])
def test_the_authored_first_page_and_signature_pass_on_either_paper(tmp_path, paper):
    fmt = _fmt(**RULES)
    path = _render(tmp_path, fmt, paper)
    res = format_check.check(path, CLS, fmt, "", None, paper)
    assert res.ok, res.fails
    d = docx.Document(str(path))
    by = [p for p in d.paragraphs if "By:" in p.text]
    assert len(by) == 1 and by[0].text.startswith("Dated:") and "\tBy:" in by[0].text
    assert not [p for p in d.paragraphs if p.text.startswith("Dated:") and "By:" not in p.text]


@pytest.mark.parametrize(
    "rule, expect",
    [
        ("front_single_spaced", "not single spaced"),
        ("caption_rule_below", "no solid line under the case caption"),
        ("signature_date_beside", "the date is not on the line above"),
    ],
)
def test_each_rule_fails_when_the_render_breaks_it(tmp_path, rule, expect):
    without = _fmt(**{**RULES, rule: False})
    path = _render(tmp_path, without)
    res = format_check.check(path, CLS, _fmt(**RULES), "", None, "plain")
    assert any(expect in f for f in res.fails), res.fails


def test_the_firm_config_accepts_the_rules():
    lay = copy.deepcopy(firm_data()["format"]["layout"])
    lay[CLS].update(RULES)
    assert firm._layout(lay) == []
    lay[CLS]["caption_rule_below"] = "yes"
    assert firm._layout(lay)


DEMAND = {
    CLS: {
        "marker": "{{ATTORNEY: the opening demand}}",
        "chart": "III. DAMAGES SUMMARY",
        "column": "Billed",
        "sentence": "Plaintiff's opening demand is {total}.",
    }
}


def _chart(rows: list[tuple[str, str]], total: str = "") -> str:
    body = "".join(f"| {a} | {b} |\n" for a, b in rows)
    return (
        "# III. DAMAGES SUMMARY\n\n| Category | Billed |\n| --- | --- |\n"
        + body
        + (f"| TOTAL | {total} |\n" if total else "")
    )


def _md(chart: str) -> str:
    return chart + "\n# XI. CONCLUSION\n\nPlaintiff asks for a fair result.\n\n{{ATTORNEY: the opening demand}}\n"


def test_the_opening_demand_is_the_charts_total_computed_by_code():
    md = _md(
        _chart(
            [
                ("Past medical expenses", "$12,500.00"),
                ("Past lost income", "$4,250.50"),
                ("Future lost income", "Not claimed"),
            ],
            "$16,750.50",
        )
    )
    out, notes = opening_demand.fill(md, {"opening_demand": DEMAND}, CLS)
    assert "Plaintiff's opening demand is $16,750.50." in out and "{{ATTORNEY: the opening demand}}" not in out
    assert "computed by code" in notes[0]


@pytest.mark.parametrize(
    "rows, total, why",
    [
        (
            [
                ("Past medical expenses", "$12,500.00"),
                ("Past non-economic damages", "{{ATTORNEY: past non-economic value}}"),
            ],
            "",
            "rows without a figure: Past non-economic damages",
        ),
        ([("Past medical expenses", "$12,500.00")], "$13,000.00", "is not the sum of its rows"),
    ],
)
def test_a_chart_code_cannot_total_leaves_the_demand_to_the_attorney(rows, total, why):
    md = _md(_chart(rows, total))
    out, notes = opening_demand.fill(md, {"opening_demand": DEMAND}, CLS)
    assert out == md and why in notes[0]


def test_only_the_sections_first_table_is_the_chart():
    chart = (
        _chart([("Northfield PT", "$1,000.00")], "$1,000.00")
        + "\nFuture care:\n\n| Item | Billed |\n| --- | --- |\n| Surgery | $50,000.00 |\n"
    )
    out, _ = opening_demand.fill(_md(chart), {"opening_demand": DEMAND}, CLS)
    assert "Plaintiff's opening demand is $1,000.00." in out


def test_the_opening_demand_config_is_closed():
    assert opening_demand.validate(DEMAND, firm.CLASSES) == []
    bad = copy.deepcopy(DEMAND)
    bad[CLS]["sentence"] = "Plaintiff demands $1,000,000."
    assert opening_demand.validate(bad, firm.CLASSES)
