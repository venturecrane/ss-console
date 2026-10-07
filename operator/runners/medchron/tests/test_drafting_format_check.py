"""format_check against the authored ``format`` block: each assertion passes on
the renderer's own output and FAILS on a document built to break it."""

from __future__ import annotations

import dataclasses
import io
from pathlib import Path

import pytest

from drafting_testkit import SECTIONS, firm_data
from medchron.drafting import format_check, render

FMT = firm_data()["format"]


def _brief(sections=SECTIONS, extra: str = "") -> str:
    body = "\n\n".join(f"# {s}\n\nBody text for this section." for s in sections)
    return (
        "| GAMMA EXAMPLE, Plaintiff, v. DELTA EXAMPLE, Defendant. | Case No. CV-0001 |\n| --- | --- |\n\n"
        + body.replace("Body text for this section.", "Body text.\n\n## A. Past Medical\n\nMore.", 1)
        + "\n\n{{ATTORNEY: settlement authority, the target figure, and any bracket}}"
        + extra
    )


def _set(n: int = 3, pos: bool = True, definitions: bool = True) -> str:
    items = "\n\n".join(f"**SPECIAL INTERROGATORY NO. {i}**\n\nState each fact." for i in range(1, n + 1))
    return (
        "| | |\n| --- | --- |\n| PROPOUNDING PARTY: | GAMMA EXAMPLE |\n| RESPONDING PARTY: | DELTA EXAMPLE |\n\n"
        + ("# DEFINITIONS\n\n\"INCIDENT\" means the collision.\n\n" if definitions else "")
        + items
        + ("\n\n**PROOF OF SERVICE**\n\nServed.\n" if pos else "\n")
    )


def _check(tmp_path: Path, md: str, cls: str, digest: str = "", fmt=FMT):
    path, _ = render.render(md, cls, tmp_path / f"{cls}.docx")
    return format_check.check(path, cls, fmt, digest)


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
    res = _check(tmp_path, "Body.", "memo", fmt={**FMT, "font": "Arial"})
    assert any(f.startswith("font:") for f in res.fails)


def test_an_underlined_level_1_heading_fails(tmp_path, monkeypatch):
    from smokeball_connector.docx_format import CLASS_RULES

    bad = dataclasses.replace(CLASS_RULES["mediation_brief"], heading_underline=(True, True, False))
    monkeypatch.setitem(CLASS_RULES, "mediation_brief", bad)
    res = _check(tmp_path, _brief(), "mediation_brief")
    assert any("level 1 underlined" in f for f in res.fails)


def test_a_single_spaced_brief_body_fails(tmp_path, monkeypatch):
    from smokeball_connector.docx_format import CLASS_RULES

    bad = dataclasses.replace(CLASS_RULES["mediation_brief"], body_line_spacing=1.0)
    monkeypatch.setitem(CLASS_RULES, "mediation_brief", bad)
    res = _check(tmp_path, _brief(), "mediation_brief")
    assert any(f.startswith("spacing:") for f in res.fails)


def test_sections_out_of_order_or_missing_fail(tmp_path):
    swapped = [SECTIONS[1], SECTIONS[0], *SECTIONS[2:]]
    assert any("out of the authored order" in f for f in _check(tmp_path, _brief(swapped), "mediation_brief").fails)
    assert any("CAUSATION" in f for f in _check(tmp_path, _brief([s for s in SECTIONS if "CAUSATION" not in s]), "mediation_brief").fails)


def test_a_settlement_figure_outside_a_marker_fails(tmp_path):
    res = _check(tmp_path, _brief(extra="\n\nThe settlement target figure is $250,000."), "mediation_brief")
    assert any(f.startswith("reserved:") for f in res.fails)


def test_discovery_spacing_beyond_one_double_line_fails(tmp_path, monkeypatch):
    from smokeball_connector.docx_format import CLASS_RULES

    bad = dataclasses.replace(CLASS_RULES["discovery_set"], item_space_after_pt=12.0)
    monkeypatch.setitem(CLASS_RULES, "discovery_set", bad)
    res = _check(tmp_path, _set(), "discovery_set")
    assert any("double-spaced between items only" in f for f in res.fails)


def test_a_missing_proof_of_service_or_definitions_fails(tmp_path):
    assert any("proof of service" in f for f in _check(tmp_path, _set(pos=False), "discovery_set").fails)
    assert any("Definitions" in f for f in _check(tmp_path, _set(definitions=False), "discovery_set").fails)


def test_the_declaration_check_uses_the_cumulative_rule(tmp_path):
    prior = (
        "## DISCOVERY-SETS\nSpecial Interrogatories, Set One | One | Gamma Example -> Delta Example | "
        "special interrogatories | 20 | 03/01/2026 | cite\n"
    )
    md = _set(20)
    # 20 in this set + 20 before: the declaration is required, and absent here
    assert any("declaration required" in f for f in _check(tmp_path, md, "discovery_set", prior).fails)
    # attached by the job: passes
    class F:
        def attachment(self, k):
            return "**DECLARATION FOR ADDITIONAL DISCOVERY**\n\nI declare."

    attached, _ = render.attach_decl(md, "discovery_set", F(), prior)
    assert _check(tmp_path, attached, "discovery_set", prior).ok
    # the same document with no prior sets: the declaration is not allowed
    assert any("declaration not allowed" in f for f in _check(tmp_path, attached, "discovery_set", "").fails)


def test_a_depo_outline_without_a_page_field_fails(tmp_path, monkeypatch):
    from smokeball_connector.docx_format import CLASS_RULES

    bad = dataclasses.replace(CLASS_RULES["depo_outline"], page_numbers=False)
    monkeypatch.setitem(CLASS_RULES, "depo_outline", bad)
    res = _check(tmp_path, "# Background\n\n1. Name.", "depo_outline")
    assert any(f.startswith("footer:") for f in res.fails)


def test_pleading_paper_fails(tmp_path):
    import docx
    from docx.oxml import OxmlElement

    path, _ = render.render("Body.", "memo", tmp_path / "m.docx")
    d = docx.Document(str(path))
    d.sections[0]._sectPr.append(OxmlElement("w:lnNumType"))
    buf = io.BytesIO()
    d.save(buf)
    path.write_bytes(buf.getvalue())
    assert any("pleading paper" in f for f in format_check.check(path, "memo", FMT).fails)
