"""format_check against the firm's authored ``format`` block: each assertion
passes on the renderer's own output under the firm's house style and FAILS on
a document rendered under a house style that breaks it (the falsifier is a
different authored value, checked against the right one)."""

from __future__ import annotations

import copy
import io
from pathlib import Path
from typing import Any

import pytest

from drafting_testkit import COURT, SECTIONS, firm_data
from medchron.drafting import format_check, render

FMT = firm_data()["format"]


def _fmt(cls: str | None = None, **layout: Any) -> dict[str, Any]:
    f = copy.deepcopy(FMT)
    if cls:
        f["layout"][cls] = {**f["layout"][cls], **layout}
    return f


def _brief(sections=SECTIONS, extra: str = "") -> str:
    body = "\n\n".join(f"# {s}\n\nBody text for this section." for s in sections)
    return (
        COURT
        + body.replace("Body text for this section.", "Body text.\n\n## A. Past Medical\n\nMore.", 1)
        + "\n\n{{ATTORNEY: settlement authority, the target figure, and any bracket}}"
        + extra
    )


def _set(n: int = 3, pos: bool = True, definitions: bool = True) -> str:
    items = "\n\n".join(f"**SPECIAL INTERROGATORY NO. {i}**\n\nState each fact." for i in range(1, n + 1))
    return (
        "| | |\n| --- | --- |\n| PROPOUNDING PARTY: | GAMMA EXAMPLE |\n| RESPONDING PARTY: | DELTA EXAMPLE |\n\n"
        + ('# DEFINITIONS\n\n"INCIDENT" means the collision.\n\n' if definitions else "")
        + items
        + ("\n\n**PROOF OF SERVICE**\n\nServed.\n" if pos else "\n")
    )


def _check(tmp_path: Path, md: str, cls: str, digest: str = "", render_fmt=None, check_fmt=None):
    path, _ = render.render(md, cls, tmp_path / f"{cls}.docx", render_fmt or FMT)
    return format_check.check(path, cls, check_fmt or FMT, digest)


@pytest.mark.parametrize(
    "cls, md",
    [
        ("mediation_brief", _brief()),
        ("discovery_set", _set()),
        ("discovery_response", _set(definitions=False).replace("**SPECIAL", "**RESPONSE TO SPECIAL")),
        ("memo", "# Question\n\nA memo body."),
        ("depo_outline", "# Background\n\n1. Name."),
    ],
)
def test_the_renderers_own_output_passes(tmp_path, cls, md):
    res = _check(tmp_path, md, cls)
    assert res.ok, res.fails


def test_a_wrong_font_fails(tmp_path):
    res = _check(tmp_path, "Body.", "memo", check_fmt={**FMT, "font": "Arial"})
    assert any(f.startswith("font:") for f in res.fails)


LEVEL3 = (
    "\n\n### 1. Northfield Physical Therapy\n\n***Northfield Physical Therapy***\n\n"
    "**Dates of service:** 02/01/2026. ***Northfield*** treated plaintiff."
)


@pytest.mark.parametrize(
    "layout, expect",
    [
        ({"heading_underline": [True, True, True]}, "level 1 should be not underlined"),
        ({"heading_underline": [False, True, False]}, "level 3 should be underlined"),
        ({"heading_indent_in": [0, 0.5, 0.5]}, "level 3 not indented 1.0 inch"),
        ({"first_line_indent_in": 0.25}, "first-line indent"),
        ({"line_spacing_pt": 12}, "not exactly 24 point"),
        ({"justify": False}, "not justified"),
        ({"centered_court_lines": False}, "court lines are not centered"),
        ({"bold_italic_heading_indent_in": 0.5}, "provider line not bold italic at 1.0 inch"),
        ({"footer_title": "Brief"}, "footer: the title"),
    ],
)
def test_each_authored_brief_rule_fails_when_the_render_breaks_it(tmp_path, layout, expect):
    md = _brief(extra=LEVEL3)
    assert _check(tmp_path, md, "mediation_brief").ok
    res = _check(tmp_path, md, "mediation_brief", render_fmt=_fmt("mediation_brief", **layout))
    assert any(expect in f for f in res.fails), res.fails
    assert res.ours  # a render defect, never a content repair


def test_sections_missing_or_out_of_order_are_content_findings(tmp_path):
    swapped = [SECTIONS[1], SECTIONS[0], *SECTIONS[2:]]
    res = _check(tmp_path, _brief(swapped), "mediation_brief")
    assert any("out of the authored order" in f for f in res.content) and not res.ours
    res = _check(tmp_path, _brief([s for s in SECTIONS if "CAUSATION" not in s]), "mediation_brief")
    assert any("CAUSATION" in f for f in res.content)


def test_missing_court_lines_are_a_content_finding(tmp_path):
    res = _check(tmp_path, _brief().replace(COURT, ""), "mediation_brief")
    assert any("court is not two lines" in f for f in res.content)


def test_a_settlement_figure_outside_a_marker_fails(tmp_path):
    res = _check(tmp_path, _brief(extra="\n\nThe settlement target figure is $250,000."), "mediation_brief")
    assert any(f.startswith("reserved:") for f in res.fails)


def test_discovery_spacing_beyond_the_authored_spacing_fails(tmp_path):
    res = _check(tmp_path, _set(), "discovery_set", render_fmt=_fmt("discovery_set", item_space_after_pt=12))
    assert any("space after" in f for f in res.fails)


def test_missing_labels_or_definitions_are_content_and_a_missing_proof_of_service_is_ours(tmp_path):
    res = _check(tmp_path, _set(pos=False), "discovery_set")
    assert any("proof of service" in f for f in res.ours)
    res = _check(tmp_path, _set(definitions=False), "discovery_set")
    assert any("Definitions" in f for f in res.content)
    res = _check(tmp_path, "| | |\n| --- | --- |\n\nNo labels here.\n\n**PROOF OF SERVICE**\n", "discovery_set")
    assert any("no item labels" in f for f in res.content)


def test_the_declaration_check_uses_the_cumulative_rule(tmp_path):
    prior = (
        "## DISCOVERY-SETS\nSpecial Interrogatories, Set One | One | Gamma Example -> Delta Example | "
        "special interrogatories | 20 | 03/01/2026 | cite\n"
    )
    md = _set(20)
    assert any("declaration required" in f for f in _check(tmp_path, md, "discovery_set", prior).fails)
    from drafting_testkit import DECL, POS

    class F:
        def attachment(self, k):
            return {"decl_2030_050": DECL, "pos": POS}[k]

    attached, _ = render.attach(md, "discovery_set", F(), prior)
    assert _check(tmp_path, attached, "discovery_set", prior).ok
    assert any("declaration not allowed" in f for f in _check(tmp_path, attached, "discovery_set", "").fails)


def test_a_declaration_after_the_proof_of_service_fails(tmp_path):
    md = _set(36) + "\n\n# DECLARATION FOR ADDITIONAL DISCOVERY\n\nI declare.\n"
    assert any("must precede the proof of service" in f for f in _check(tmp_path, md, "discovery_set").fails)


def _strip_footer_fields(path: Path) -> None:
    import docx

    d = docx.Document(str(path))
    for p in d.sections[0].footer.paragraphs:
        for r in list(p.runs):
            r._r.getparent().remove(r._r)
    buf = io.BytesIO()
    d.save(buf)
    path.write_bytes(buf.getvalue())


@pytest.mark.parametrize("cls, md", [("depo_outline", "# Background\n\n1. Name."), ("mediation_brief", _brief())])
def test_a_document_without_its_page_field_fails(tmp_path, cls, md):
    path, _ = render.render(md, cls, tmp_path / "x.docx", FMT)
    assert format_check.check(path, cls, FMT).ok
    _strip_footer_fields(path)
    assert any(f.startswith("footer: no PAGE") for f in format_check.check(path, cls, FMT).fails)


def test_pleading_paper_fails(tmp_path):
    import docx
    from docx.oxml import OxmlElement

    path, _ = render.render("Body.", "memo", tmp_path / "m.docx", FMT)
    d = docx.Document(str(path))
    d.sections[0]._sectPr.append(OxmlElement("w:lnNumType"))
    buf = io.BytesIO()
    d.save(buf)
    path.write_bytes(buf.getvalue())
    assert any("pleading paper" in f for f in format_check.check(path, "memo", FMT).fails)


def test_without_an_authored_layout_only_the_font_and_class_defaults_are_checked(tmp_path):
    bare = copy.deepcopy(FMT)
    bare.pop("layout")
    bare.pop("plain_letter_paper")
    res = _check(tmp_path, "# Question\n\nA memo body.", "memo", render_fmt=bare, check_fmt=bare)
    assert res.ok, res.fails
