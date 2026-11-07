"""The litigation variant of a house demand: a letter to defense counsel in a
filed action. Its RE block is the action's caption over its court, where a
pre-suit letter names the carrier's insured and claim number. Below the RE
block it is the same house letter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from demand_testkit import (
    DRAFT,
    SIGNATURE,
    ScriptedClient,
    make_inputs,
    make_job,
    make_pdf,
    make_reference,
    seat_with,
    standard_docs,
)
from jobs_testkit import PRICING
from jobs.demand import crosscheck, format_check, house
from jobs.demand.run import DemandRun

LIT_FRONT = """---
date: October 6, 2026
attn: Dana Counsel, Esq. | Example Defense LLP | 2 Example Plaza | Exampletown, CA 90000
client: Alpha Example
caption: Alpha Example vs. Beta Driver, et al.,
court: Example County Superior Court Case No. 24CV0001
dol: January 15, 2026
banner: Time-Limited Settlement Demand
expires: This Demand Expires at 5:00 P.M. Pacific Time on Friday, November 6, 2026
salutation: Dear Ms. Counsel:
signer: Example Attorney
---
"""
LIT_DRAFT = LIT_FRONT + DRAFT.split("---\n", 2)[2]
LIT_VARIANT = {
    "skeleton": "skeleton_house",
    "house_reference": "house_reference_house",
    "headings": ["Summary of Injuries", "Liability", "Damages", "Demand"],
    "re_required": [],
    "re_one_of": [],
    "mail_line": "",
    "banner": True,
    "forbidden": ["CERTIFIED MAIL"],
    "re_vs": True,
}
CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"
PLEADING = (
    "d12",
    "Pleading.pdf",
    make_pdf(["SUPERIOR COURT OF CALIFORNIA. COMPLAINT FOR DAMAGES. Case No. 24CV0001."]),
    "f-corr",
)


@pytest.fixture(autouse=True)
def _checker(monkeypatch):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


@pytest.fixture
def pricing(tmp_path: Path) -> Path:
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    return p


def _texts(path: Path) -> list[str]:
    import docx

    return [p.text for p in docx.Document(str(path)).paragraphs if p.text.strip()]


def _render(tmp_path: Path, md: str, **kw):
    ref = tmp_path / "ref.docx"
    make_reference(ref)
    return house.render(
        md, ref, tmp_path / "out", signature=SIGNATURE, signer_title="Attorney at Law", author="Example", **kw
    )


def test_a_litigation_letter_opens_with_the_caption_and_its_court(tmp_path):
    path, _ = _render(tmp_path, LIT_DRAFT, litigation=True)
    texts = _texts(path)
    dear = texts.index("Dear Ms. Counsel:")
    head = texts[:dear]
    assert "RE:\tAlpha Example vs. Beta Driver, et al.," in head
    assert "\tExample County Superior Court Case No. 24CV0001" in head
    assert "\tDate of Loss: January 15, 2026" in head
    assert head[1] == "Dana Counsel, Esq."
    assert not any("Your Insured" in t or "Claim Number" in t or "CERTIFIED MAIL" in t for t in head)


def test_the_litigation_letter_passes_its_own_shape_and_fails_the_pre_suit_one(tmp_path):
    path, _ = _render(tmp_path, LIT_DRAFT, litigation=True)
    kw = dict(signature=SIGNATURE, signer_title="Attorney at Law", footer_markers=["1119, 1152"])
    lit = format_check.check(path, variant=format_check.Variant.from_config(LIT_VARIANT), **kw)
    assert lit.ok, lit.fails
    pre = format_check.check(path, **kw)  # the default shape is the pre-suit one
    assert any("Claim Number" in f for f in pre.fails)


def test_a_pre_suit_letter_is_rendered_as_it_always_was(tmp_path):
    path, _ = _render(tmp_path, DRAFT, mail_line="CERTIFIED MAIL")
    head = _texts(path)[:10]
    assert "RE:\tOur Client:\tAlpha Example" in head
    assert "\tYour Insured:\tBeta Driver" in head and "\tClaim Number:\tCLM-0001" in head
    assert "\tDate of Loss:\tJanuary 15, 2026" in head


def test_litigation_front_matter_without_a_caption_or_court_does_not_render(tmp_path):
    with pytest.raises(house.RenderError, match="caption"):
        _render(tmp_path, DRAFT, litigation=True)
    with pytest.raises(house.RenderError, match="insured"):
        _render(tmp_path, LIT_DRAFT)


def test_the_cross_check_reads_the_caption_for_the_client_and_never_holds_on_it():
    facts = {"client_name": "Alpha Example", "date_of_loss": "01/15/2026"}
    keys = house.front_keys(litigation=True)
    ok = crosscheck.check(LIT_DRAFT, facts, keys)
    assert ok["mismatches"] == []
    assert any(u.startswith("court and case number") for u in ok["unchecked"])
    assert not any(u.startswith("caption") for u in ok["unchecked"])
    other = LIT_DRAFT.replace("caption: Alpha Example vs.", "caption: Gamma Stranger vs.")
    out = crosscheck.check(other, facts, keys)
    assert out["mismatches"] == [] and any("surname" in u for u in out["unchecked"])
    wrong_client = LIT_DRAFT.replace("client: Alpha Example", "client: Gamma Stranger")
    assert crosscheck.check(wrong_client, facts, keys)["mismatches"]
    assert not any(u.startswith(("court", "caption")) for u in crosscheck.check(DRAFT, facts)["unchecked"])


def _litigation_inputs(root: Path) -> Path:
    make_inputs(root)
    cfg = root / "demand-firm.yaml"
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    data["variants"]["litigation"] = dict(LIT_VARIANT)
    cfg.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return root


def test_a_filed_action_with_an_authored_litigation_format_delivers_the_litigation_letter(tmp_path, pricing):
    inputs = _litigation_inputs(tmp_path / "inputs")
    seat = seat_with(standard_docs() + [PLEADING])
    client = ScriptedClient(draft=LIT_DRAFT)
    run = DemandRun(
        make_job(tmp_path / "job"),
        inputs_dir=str(inputs),
        pricing=str(pricing),
        seat_factory=lambda: seat,
        client=client,
        log=lambda _line: None,
        readback_pause=0.0,
        vendor_factory=lambda: None,
    )
    v = run.run()
    assert v.outcome == "delivered", v.reason
    texts = _texts(tmp_path / "job" / "data" / "out" / "demand" / "Demand.Alpha Example.docx")
    assert "RE:\tAlpha Example vs. Beta Driver, et al.," in texts
    assert not any("CERTIFIED MAIL" in t or "Your Insured:" in t for t in texts[:10])
    i = texts.index("Cordially,")
    assert texts[i + 2 : i + 5] == ["Example A. Lawyer", "Attorney at Law", "EAL/dm"]
    notes = _texts(tmp_path / "job" / "data" / "out" / "demand" / "Demand.Alpha Example - attorney notes.docx")
    assert any("court and case number" in t for t in notes)


def test_a_pre_suit_draft_on_a_filed_action_is_refused_before_filing(tmp_path, pricing):
    """The drafter answered in the pre-suit front matter: the letter has no
    caption, so nothing is rendered and nothing is filed."""
    inputs = _litigation_inputs(tmp_path / "inputs")
    seat = seat_with(standard_docs() + [PLEADING])
    run = DemandRun(
        make_job(tmp_path / "job"),
        inputs_dir=str(inputs),
        pricing=str(pricing),
        seat_factory=lambda: seat,
        client=ScriptedClient(),
        log=lambda _line: None,
        readback_pause=0.0,
        vendor_factory=lambda: None,
    )
    v = run.run()
    assert v.outcome != "delivered" and seat.sent == []


def _runs(path: Path, text: str) -> list[tuple[str, bool, bool]]:
    import docx

    p = next(p for p in docx.Document(str(path)).paragraphs if p.text.strip() == text or p.text.startswith(text))
    return [(r.text, bool(r.bold), bool(r.underline)) for r in p.runs if r.text.strip()]


def test_section_headings_are_bold_in_both_variants_and_only_the_caption_is_underlined(tmp_path):
    (tmp_path / "lit").mkdir()
    (tmp_path / "pre").mkdir()
    lit, _ = _render(tmp_path / "lit", LIT_DRAFT, litigation=True)
    pre, _ = _render(tmp_path / "pre", DRAFT, mail_line="CERTIFIED MAIL")
    for path in (lit, pre):
        for head in ("Summary of Injuries", "Liability", "Damages", "Demand", "Exhibits"):
            assert [bold for _t, bold, _u in _runs(path, head)] == [True], head
    assert _runs(lit, "RE:") == [("RE:", True, False), ("Alpha Example vs. Beta Driver, et al.,", True, True)]
    assert all(not underlined for _t, _b, underlined in _runs(pre, "RE:"))
    body = _runs(lit, "This letter is submitted")
    assert body and not any(bold for _t, bold, _u in body)  # a body paragraph is as it was
