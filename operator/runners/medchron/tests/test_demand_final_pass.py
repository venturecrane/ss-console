"""Findings the repairs leave are settled in code, never filed and never a stop
(a live demand job, 2026-10-07: 3 INVENTED and 6 ARITHMETIC after two repairs,
four of the six the auditor's own "correct" checks)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from demand_testkit import DRAFT, ScriptedClient, make_inputs, make_job, seat_with, standard_docs
from medchron_testkit import PRICING
from medchron.demand import finalpass
from medchron.demand.run import DemandRun

CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"
INVENTED_SENTENCE = "Your insured, Beta Driver, is named in the carrier's letter under claim CLM-0001"
FIGURE_SENTENCE = "The itemized statement lists total charges of $1,200.00"


@pytest.fixture(autouse=True)
def _checker(monkeypatch):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


@pytest.fixture
def pricing(tmp_path: Path) -> Path:
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    return p


class SectionAuditor(ScriptedClient):
    """Flags one Liability sentence INVENTED and one Damages figure ARITHMETIC,
    every time (the repairs never clear them), and files one passed check
    under ARITHMETIC, as the live auditor did."""

    def _answer(self, params):
        if "AUDIT-PROMPT" not in json.dumps(params.get("system")):
            return super()._answer(params)
        user = params["messages"][-1]["content"]
        if "SECTION UNDER AUDIT: Liability" in user:
            return (
                f"- {INVENTED_SENTENCE} | INVENTED | the carrier letter names no insured\n"
                "SUPPORTED=0 DRIFTS=0 INVENTED=1 ARITHMETIC=0"
            )
        if "SECTION UNDER AUDIT: Exampletown ER" in user:
            return (
                "- total charges of $1,200.00 | ARITHMETIC | the bill's lines sum to $1,100.00\n"
                "- one ER visit at $1,200.00 | ARITHMETIC | 1 x 1200.00 = 1200.00; correct\n"
                "SUPPORTED=1 DRIFTS=0 INVENTED=0 ARITHMETIC=2"
            )
        return "- claim | SUPPORTED | cite\nSUPPORTED=1 DRIFTS=0 INVENTED=0 ARITHMETIC=0"


def _run(tmp_path, pricing, client):
    inputs = tmp_path / "inputs"
    make_inputs(inputs)
    jd = make_job(tmp_path / "job")
    seat = seat_with(standard_docs())
    r = DemandRun(
        jd,
        inputs_dir=str(inputs),
        pricing=str(pricing),
        seat_factory=lambda: seat,
        client=client,
        log=lambda m: None,
        readback_pause=0.0,
        vendor_factory=lambda: None,
    )
    return r, r.run(), seat


def _docx_text(path: Path) -> str:
    import docx

    d = docx.Document(str(path))
    return (
        "\n".join(p.text for p in d.paragraphs)
        + "\n"
        + " ".join(c.text for t in d.tables for row in t.rows for c in row.cells)
    )


def test_an_invented_sentence_never_survives_into_the_render(tmp_path, pricing):
    client = SectionAuditor()
    _r, v, seat = _run(tmp_path, pricing, client)
    assert v.outcome == "delivered", v.reason  # the leftovers no longer stop the job
    assert client.stages().count("REPAIR") == 2
    out = tmp_path / "job" / "data" / "out" / "demand"
    letter = _docx_text(out / "Demand.Alpha Example.docx")
    assert "is named in the carrier's letter" not in letter
    assert "NOT IN RECORD" in letter and "not found in the file" in letter
    notes = _docx_text(out / "Demand.Alpha Example - attorney notes.docx")
    assert "Removed or flagged by the final audit" in notes and "removed" in notes


def test_an_arithmetic_figure_never_survives_unflagged(tmp_path, pricing):
    _r, v, _ = _run(tmp_path, pricing, SectionAuditor())
    assert v.outcome == "delivered", v.reason
    final = (tmp_path / "job" / "data" / "draft-final.md").read_text()
    assert FIGURE_SENTENCE not in final  # its figure is gone from that sentence
    assert (
        "The itemized statement lists total charges of {{ATTORNEY: verify arithmetic: the bill's lines sum to $1,100.00}}"
        in final
    )
    rec = json.loads((tmp_path / "job" / "data" / "final-pass.json").read_text())
    assert (rec["removed"], rec["flagged"], rec["cleared"]) == (1, 1, 1)  # the passed check is left as written


def test_settle_unit():
    md, notes = finalpass.settle(
        "## Damages\n\nA sentence that stays. The bill totals $500.00 (bill, p. 1). Another stays.\n",
        "## AUDIT: Damages\n\n- The bill totals $500.00 | ARITHMETIC | lines sum to $450.00\n",
    )
    assert "A sentence that stays." in md and "Another stays." in md
    assert "$500.00" not in md and "{{ATTORNEY: verify arithmetic: lines sum to $450.00}}" in md
    md2, _ = finalpass.settle(
        "## Liability\n\nTrue thing (cite). The defendant admitted fault at the scene (cite). More.\n",
        '## AUDIT: Liability\n\n- "The defendant admitted fault at the scene" | INVENTED | no cite in digest\n',
    )
    assert "admitted fault" not in md2.split("NOT IN RECORD")[0] and "True thing (cite)." in md2 and "More." in md2
    with pytest.raises(finalpass.Unlocated):
        finalpass.settle("## Liability\n\nNothing here.\n", "## AUDIT: Liability\n\n- zebra quartz | INVENTED | x\n")


def test_a_marker_never_carries_braces_or_pipes():
    md, _ = finalpass.settle(
        "## Damages\n\nThe bill totals $500.00 (bill).\n",
        "## AUDIT: Damages\n\n- The bill totals $500.00 | ARITHMETIC | sum {is} $450.00\n",
    )
    marker = md[md.index("{{") : md.index("}}") + 2]
    assert marker.count("{") == 2 and marker.count("}") == 2


_ = DRAFT


@pytest.mark.parametrize(
    "detail,cleared",
    [
        ("bill, p.2 - correct sum", True),
        ("sum = $6,005.72 - arithmetic checks out correctly (confirmed correct, not an error)", True),
        ("10 x 312.95 = 3,129.50; correct", True),
        ("lines sum to $450.00", False),
        ("incorrect: lines sum to $450.00", False),
        ("not correct; should be $450.00", False),
    ],
)
def test_an_auditors_passed_check_filed_as_arithmetic_is_left_as_written(detail, cleared):
    # the shapes a live auditor wrote on 2026-10-07, all five passed checks
    assert finalpass.self_cleared(detail) is cleared
