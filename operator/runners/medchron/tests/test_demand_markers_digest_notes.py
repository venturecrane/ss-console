"""Three defects of the 2026-10-15 demand re-run, each with its falsifier (every
test here fails on the code before the change). All names are invented.

1. The review held a correct demand because its letter carries the firm's own
   open-item markers ("the request not carried out"). The markers are the
   deliverable working as the firm asked; only a malformed one is a finding.
2. "digest", the system's word for the record, reached the letter through the
   final pass's marker reasons ("no cite in digest").
3. The attorney notes said six exhibit cites were "taken out" when one table
   cell had been replaced and all six were still in the letter: a note is now
   written only after the edit is verified in the final text."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from demand_testkit import ScriptedClient, make_inputs, make_job, seat_with, standard_docs
from medchron_testkit import PRICING
from medchron.demand import draft, finalpass, review as rv
from medchron.demand.run import DemandRun
from medchron.proof import review as R

CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"
WELL_FORMED = (
    "I. DAMAGES\n"
    "| Past medical | [TO BE SUPPLIED: the imaging bill; looked in the billing folder] |\n"
    "The clinic billed $1,200.00 [CONFIRM the clinic total BEFORE TRANSMITTAL].\n"
    "[ATTORNEY: demand figure] Proposed: the full available limits.\n"
    "Wages {{NOT IN RECORD: wage documents; see gap audit item 1}}.\n"
)
PLACEHOLDER_ROW = (
    "| MUST-FIX | 2 the request not carried out | The letter contains unresolved internal placeholders "
    '("[TO BE SUPPLIED...]", "[CONFIRM...]", "[ATTORNEY:...]") so no complete, sendable letter was produced. '
    "| DEMAND, Sections III and V |\nVERDICT: 1 MUST-FIX, 0 SHOULD-FIX, 0 NOTE"
)


@pytest.fixture(autouse=True)
def _checker(monkeypatch):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


@pytest.fixture
def pricing(tmp_path: Path) -> Path:
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    return p


# ---- 1. the firm's markers are the deliverable ---------------------------------------------
def test_well_formed_markers_are_never_a_finding():
    assert rv.malformed_markers(WELL_FORMED) == []
    found = rv.demand_code(
        {"demand": WELL_FORMED}, wanted=[], sources="", computed=[], facts={}, letter_md=None, drafted=None
    )
    assert not [f for f in found if f.check.startswith("2")]
    assert "their presence, their number or where they stand is never a finding" in rv.DEMAND_PROMPT


@pytest.mark.parametrize(
    "bad",
    [
        "The bill [TO BE SUPPLIED: the imaging bill; looked in billing\nNext line.",
        "Figure [ATTORNEY] here.",
        "Wages {{FILL: wage loss, no close\n",
        "Treatment [INSERT treating providers from the record].",
    ],
)
def test_a_malformed_marker_is_a_must_fix(bad):
    assert rv.malformed_markers(bad)
    found = rv.demand_code({"demand": bad}, wanted=[], sources="", computed=[], facts={}, letter_md=None, drafted=None)
    assert [f for f in found if f.grade == R.MUST and f.check == "2 open items"]


def test_a_model_finding_that_rests_on_markers_is_a_note_and_others_are_untouched():
    rows = R.parse(
        PLACEHOLDER_ROW
        + "\n| MUST-FIX | 2 the request not carried out | No gap audit was delivered | the request |"
        + "\n| MUST-FIX | 1 contradicts the record | The [TO BE SUPPLIED] marker asks for a bill the record has | FILE: bill |",
        rv.HOLD_DEMAND,
    )
    graded = rv.markers_by_design(rows)
    assert [f.grade for f in graded] == [R.NOTE, R.MUST, R.MUST]


def test_a_demand_whose_review_objects_only_to_its_markers_is_delivered(tmp_path, pricing):
    make_inputs(tmp_path / "inputs")
    make_job(tmp_path / "job")
    seat = seat_with(standard_docs())
    v = DemandRun(
        tmp_path / "job",
        inputs_dir=str(tmp_path / "inputs"),
        pricing=str(pricing),
        seat_factory=lambda: seat,
        client=ScriptedClient(review=PLACEHOLDER_ROW),
        log=lambda m: None,
        readback_pause=0.0,
        vendor_factory=lambda: None,
    ).run()
    assert v.outcome == "delivered", v.reason  # FALSIFIER: held on "unresolved placeholders"
    rec = json.loads((tmp_path / "job" / "data" / "review.json").read_text())
    assert rec["must_fix"] == 0 and rec["notes"] >= 1


# ---- 2. "digest" never reaches the firm -----------------------------------------------------
@pytest.mark.parametrize(
    "said",
    [
        "(no cite in digest)",
        "per the digest, the visit was 1/16",
        "The digest states two visits.",
        "digest entries show",
    ],
)
def test_the_systems_word_for_the_record_holds(said):
    assert rv.wording({"demand": said})


@pytest.mark.parametrize("clean", ["Upper digestive tract normal (GI note).", "Digestion was normal on exam."])
def test_digestive_never_holds(clean):
    assert rv.wording({"demand": clean}) == []


def test_a_settled_marker_calls_the_record_the_record():
    md, notes = finalpass.settle(
        "## Liability\n\nTrue thing (cite). The insured admitted fault at the scene (cite). More.\n",
        "## AUDIT: Liability\n\n- The insured admitted fault at the scene | INVENTED | no cite in digest — "
        "the digest contains no such statement\n",
    )
    assert "digest" not in md.lower() and "—" not in md
    assert all("digest" not in n.lower() for n in notes) and rv.wording({"demand": md}) == []
    assert "never the digest" in draft.FIRM_READS


# ---- 3. a note only after the edit is verified --------------------------------------------
SPREAD = (
    "## Medical Treatment\n\n"
    "| Current specials | $1,000.00 billed (Specials, below; Exhs. 4 through 9) |\n\n"
    "The clinic saw her twice (clinic record; Exh. 3). Imaging followed (MRI report; Exh. 4).\n\n"
    "Therapy ran six weeks (PT notes; Exh. 7).\n"
)
SPREAD_AUDIT = (
    '## AUDIT: Medical Treatment\n\n- Exhibit numbers "Exh. 3," "Exh. 4," "Exh. 7," "Exhs. 4 through 9" '
    "| INVENTED | no exhibit list in the record\n"
)


def test_a_list_spread_through_the_section_is_never_reported_taken_out():
    md, notes = finalpass.settle(SPREAD, SPREAD_AUDIT)
    # FALSIFIER: one cell was replaced and the note said every item was taken out
    assert md == SPREAD
    (note,) = notes
    assert "taken out" not in note and "still in the letter, for the attorney to decide" in note
    assert finalpass.summary(notes) == {"removed": 0, "flagged": 0, "cleared": 0, "kept": 1}


def test_a_list_inside_one_sentence_is_removed_and_verified():
    md, notes = finalpass.settle(
        "## Facts\n\nKeep this (cite). She saw Dr. Alpha, Dr. Beta and Dr. Gamma that week (cite). Keep too.\n",
        '## AUDIT: Facts\n\n- "Dr. Alpha," "Dr. Beta," "Dr. Gamma" | INVENTED | the record names one doctor\n',
    )
    assert "Dr. Beta" not in md and "Keep this (cite)." in md and "Keep too." in md
    assert finalpass.summary(notes)["removed"] == 1


def test_remaining_reports_what_an_edit_left():
    assert finalpass.remaining('"Exh. 3," "Exh. 4"', "cites Exh. 4 here") == ["exh. 4"]
    assert finalpass.remaining('"Exh. 3," "Exh. 4"', "no cites") == []
    assert (
        finalpass.remaining("The insured admitted fault at the scene", "The insured admitted fault at the scene.")
        is None
    )
    assert finalpass.remaining("The insured admitted fault at the scene", "[TO BE SUPPLIED: x]") == []
