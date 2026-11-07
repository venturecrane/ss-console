"""What the second review hold of 2026-11-07 taught the demand lane, each
piece generic:

* a marker the final pass writes into the letter never carries a cut-off
  sentence, a quotation or a parenthetical cite (that night: the check's whole
  reason, cut at 220 characters, a document's name and a chart note broken
  mid-word, inside the closing paragraph);
* the demand's expiry falls on a business day (that night: a Sunday);
* the system never calls itself "a model" in anything the firm reads (that
  night: a code-authored "Never read by a model" over the held-out list);
* SMD can ask for a repair of exactly the sections the review faulted, in the
  audit's own shape; the repaired draft is audited with every unchanged
  section's audit kept, settled and gated again, rendered and reviewed afresh,
  and nothing else is bought. Every name here is invented."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from demand_testkit import DRAFT, ScriptedClient, _echo_sections, make_inputs, make_job, seat_with, standard_docs
from jobs_testkit import PRICING
from jobs.demand import finalpass, house, review as rv
from jobs.demand.run import DemandRun
from jobs.proof import review as proof_review

CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"


@pytest.fixture(autouse=True)
def _checker(monkeypatch):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


@pytest.fixture
def pricing(tmp_path: Path) -> Path:
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    return p


# ---- the marker's reason: short and whole --------------------------------------------------
LONG_DETAIL = (
    "the record's own total (per the firm's 3/3/2025 letter) is $7,123.45 (Alpha $1,000.00 + Beta $200.50 + "
    "Gamma $300.25 + Delta $1,100.00 + Epsilon $2,222.70 + Zeta $1,300.00 + Eta $1,000.00 = $7,123.45) "
    "(NEGOTIATIONS | Settlement Demand Letter, 3/3/2025, DEMAND p.8); no combination of record figures yields $9,876.54"
)
NOTE_DETAIL = (
    "the record describing this dependency is dated 4/1/2025 and 6/2/2025, when the patient (DOB 2/2/1950) was 75, "
    'not 74 (Clinic Report [part 2/3], p.10-12); also the cited note states he does NOT have "enough strength to '
    'lift the box", not that he "carries it" successfully'
)


def test_the_marker_never_carries_a_cut_off_sentence_a_quotation_or_a_cite():
    for detail in (LONG_DETAIL, NOTE_DETAIL):
        why = finalpass.marker_why(detail)
        assert "..." not in why and "(NEGO" not in why and '"' not in why and "'" not in why.replace("'s", "")
        assert len(why) <= finalpass.MARKER_WHY_MAX + 3
        assert "[part" not in why and "p.10" not in why
    # a reason that fits whole is kept whole
    assert finalpass.marker_why("the bill's lines sum to $1,100.00") == " (the bill's lines sum to $1,100.00)"
    # a reason that is nothing but a quotation and a cite says nothing, and the marker still stands
    assert finalpass.marker_why('"a quoted sentence of the chart" (EMR Report, p. 3)') == ""
    assert finalpass.MARK[(finalpass.SQUARE, "ARITHMETIC")].format(why="") == "[CONFIRM this figure BEFORE TRANSMITTAL]"


def test_that_nights_markers_on_fixtures_are_whole_and_the_notes_carry_the_whole_reason():
    draft = "## Demand\n\nPast medical specials total $9,876.54 (the rows above).\n"
    audit = f'## AUDIT: Demand\n\n- Past medical specials total "$9,876.54" | ARITHMETIC | {LONG_DETAIL}\n'
    md, notes = finalpass.settle(draft, audit)
    marker = md[md.index("[CONFIRM") : md.index("TRANSMITTAL]") + 12]
    assert "..." not in marker and "NEGOTIATIONS" not in marker and "Settlement Demand Letter" not in marker
    assert marker.startswith("[CONFIRM this figure")
    assert len(marker) < 180
    (note,) = notes
    assert "..." not in note and "no combination of record figures yields" in note  # the whole reason, for the attorney
    assert rv.wording({"demand": md, "attorney_notes": note}) == []


# ---- the expiry on a business day ------------------------------------------------------------
SUNDAY = DRAFT.replace(
    "expires: This Demand Expires at 5:00 P.M. Pacific Time on Friday, November 6, 2026",
    "expires: This Demand Expires at 5:00 P.M. Pacific Time on Sunday, December 6, 2026",
).replace(
    "Demand is hereby made for the full available policy limits.",
    "Demand is hereby made for the full available policy limits. This demand expires at 5:00 p.m. Pacific Time on "
    "Sunday, December 6, 2026, and we ask that you respond on or before December 6, 2026.",
)


def test_a_sunday_expiry_moves_to_the_monday_in_the_banner_and_the_letter():
    md, notes = house.business_day_expiry(SUNDAY)
    assert "Sunday, December 6, 2026" not in md and "December 6, 2026" not in md
    assert md.count("Monday, December 7, 2026") == 2 and "on or before December 7, 2026" in md
    (note,) = notes
    assert "Sunday" in note and "Monday, December 7, 2026" in note and "holiday" in note.lower()
    assert rv.wording({"attorney_notes": note}) == []


def test_a_saturday_moves_two_days_and_a_weekday_stays():
    sat = DRAFT.replace("Friday, November 6, 2026", "Saturday, December 5, 2026")
    md, notes = house.business_day_expiry(sat)
    assert "Monday, December 7, 2026" in md and notes
    same, none = house.business_day_expiry(DRAFT)
    assert same == DRAFT and none == []


def test_a_wrong_weekday_word_is_corrected():
    wrong = DRAFT.replace("Friday, November 6, 2026", "Thursday, November 6, 2026")
    md, notes = house.business_day_expiry(wrong)
    assert "Friday, November 6, 2026" in md and "Thursday" not in md
    assert notes and "corrected" in notes[0]


def test_the_rendered_demand_carries_the_business_day(tmp_path, pricing):
    client = ScriptedClient(draft=SUNDAY)
    _r, v = _run(tmp_path, pricing, client)
    assert v.outcome == "delivered", v.reason
    letter = _letter_text(tmp_path / "job" / "data")
    assert "Monday, December 7, 2026" in letter and "December 6, 2026" not in letter
    kept = json.loads((tmp_path / "job" / "data" / "delivered-md.json").read_text())
    assert "next business day" in kept["attorney_notes"]


# ---- "a model" ----------------------------------------------------------------------------------
def test_the_system_never_calls_itself_a_model_in_the_firms_files():
    assert rv.wording({"gap_audit": "Never read by a model; clear any of them and ask again to include it."})
    assert rv.wording({"gap_audit": "Not read for this demand; clear any of them and ask again to include it."}) == []
    assert rv.wording({"demand": "The 2019 model year sedan; a model patient; the model number on the device."}) == []


# ---- the asked repair ----------------------------------------------------------------------------
class RepairingClient(ScriptedClient):
    """A repair that changes the one section it is given."""

    def _answer(self, params):
        system = json.dumps(params.get("system"))
        if "REPAIR-PROMPT" in system:
            user = params["messages"][-1]["content"]
            return _echo_sections(user).replace("diagnosed a cervical strain", "diagnosed a cervical sprain")
        return super()._answer(params)


def _letter_text(data: Path) -> str:
    """The rendered demand, read back as the review reads it."""
    from jobs.demand import deliver

    manifest = json.loads((deliver.out_dir(data) / "upload_manifest.json").read_text(encoding="utf-8"))
    (m,) = [m for m in manifest if m.get("role") == "demand"]
    return rv.docx_text(Path(m["local_path"]))


def _run(tmp_path, pricing, client):
    inputs = tmp_path / "inputs"
    if not inputs.is_dir():
        make_inputs(inputs)
    jd = make_job(tmp_path / "job")
    log: list[str] = []
    r = DemandRun(
        jd,
        inputs_dir=str(inputs),
        pricing=str(pricing),
        seat_factory=lambda: seat_with(standard_docs()),
        client=client,
        log=log.append,
        readback_pause=0.0,
        vendor_factory=lambda: None,
    )
    r.test_log = log
    return r, r.run()


HELD = (
    "| MUST-FIX | 1 contradicts the record | The ER paragraph says strain where the record says sprain | "
    "DEMAND letter, Exampletown ER; ER record 1-15-26, p. 1 |\nVERDICT: 1 MUST-FIX, 0 SHOULD-FIX, 0 NOTE"
)


def test_smd_asks_for_a_repair_of_the_faulted_sections_and_only_those_are_audited_again(tmp_path, pricing):
    verdicts = [HELD]  # the first review holds on the letter's content, as on 2026-11-07
    client = RepairingClient(
        review=lambda _p: verdicts.pop(0) if verdicts else "VERDICT: 0 MUST-FIX, 0 SHOULD-FIX, 0 NOTE"
    )
    _r, v = _run(tmp_path, pricing, client)
    assert v.outcome == "failed" and v.reason.startswith("review_held:"), v.reason
    data = tmp_path / "job" / "data"
    before = client.stages()
    audits_first = before.count("AUDIT")
    assert audits_first >= 3  # the letter has several auditable sections
    # SMD's request, in the audit's own shape, for the draft the review read
    (data / "repair-request-v1.md").write_text(
        "## AUDIT: Exampletown ER (January 15, 2026)\n\n"
        "- The emergency department diagnosed a cervical strain | DRIFTS | the ER record 1-15-26 says sprain, p. 1\n",
        encoding="utf-8",
    )
    st = json.loads((data / "state.json").read_text())
    st.pop("render")
    (data / "state.json").write_text(json.dumps(st), encoding="utf-8")
    (tmp_path / "job" / proof_review.REDO_MARKER).write_text("{}", encoding="utf-8")
    r2, v2 = _run(tmp_path, pricing, client)
    assert v2.outcome == "delivered", v2.reason
    again = client.stages()[len(before) :]
    assert again.count("REPAIR") == 1
    assert again.count("AUDIT") == 1  # the one changed section; every other section kept its audit
    assert "GAP" not in again and "DIGEST" not in again and "VISION" not in again and "COMPOSE" not in again
    assert again.count("REVIEW") == 1
    assert (data / "draft-v2.md").is_file()
    assert json.loads((data / "final-pass.json").read_text())["version"] == 2
    assert any("replaces v1" in ln for ln in r2.test_log)  # the letter was settled and gated again
    letter = _letter_text(data)
    assert "cervical sprain" in letter and "cervical strain" not in letter


# ---- two unsupported statements in one sentence -----------------------------------------------
def test_two_invented_findings_in_one_sentence_are_both_settled_by_the_first_removal():
    """2026-11-07, attempt 4: the audit of a repaired notes section returned three
    invented findings, two in the same sentence; the first removal took the
    sentence, the second matched nothing, and the final pass raised, stopping a
    job that had every paid stage done. FALSIFIER: on the code before the fix
    this raises Unlocated("... matches nothing in that section")."""
    draft = (
        "## Exhibits\n\nThe exhibits follow.\n\n"
        "The dispatcher signed the run report at the scene and the receipt numbered 99 was issued to the carrier.\n\n"
        "A second sentence that stays.\n"
    )
    audit = (
        "## AUDIT: Exhibits\n\n"
        "- The dispatcher signed the run report at the scene | INVENTED | no such signature in the record\n"
        "- the receipt numbered 99 was issued to the carrier | INVENTED | no such receipt in the record\n"
    )
    md, notes = finalpass.settle(draft, audit)
    assert "dispatcher signed" not in md and "receipt numbered 99" not in md
    assert md.count("[TO BE SUPPLIED: a statement here is not supported by the file") == 1
    assert "A second sentence that stays." in md
    assert len(notes) == 2 and any("taken out with the statement before it" in n for n in notes)
    assert finalpass.summary(notes)["removed"] == 1
    assert rv.wording({"demand": md, "attorney_notes": "\n".join(notes)}) == []


def test_a_finding_that_was_never_in_the_section_still_raises():
    draft = "## Exhibits\n\nThe exhibits follow.\n"
    audit = "## AUDIT: Exhibits\n\n- The dispatcher signed the run report at the scene | INVENTED | no such signature\n"
    with pytest.raises(finalpass.Unlocated):
        finalpass.settle(draft, audit)


# ---- law is not a record fact, and the notes to the attorney are not the letter ------------------
LAW_DRAFT = (
    "## Liability\n\nA carrier owes its passengers the highest duty (Civ. Code, §§ 2100, 2168; CACI No. 902.) "
    "Civil Code section 2100 provides:\n\n> A carrier of persons for reward must use the utmost care and diligence.\n\n"
    "The van pulled away (incident report, p. 1).\n\n"
    "## Prior Records\n\nYour client took him as it found him (CACI No. 3928.)\n\n"
    "## Exhibits\n\nExhibit 1: the report.\n\n=== ATTORNEY NOTES ===\n\n"
    "| ATTORNEY | Duty paragraph relies on Civil Code §§ 2100, 2168 and CACI 902 | confirm |\n"
    "| ATTORNEY | Specials $1,200.00 'sum of the rows' | confirm |\n"
)
LAW_AUDIT = (
    "## AUDIT: Liability\n\n"
    "- Quoted text of Civil Code §2100 ('A carrier of persons for reward must use the utmost care') | INVENTED | "
    "no cite in the record\n\n"
    "## AUDIT: Prior Records\n\n- CACI No. 3928 | INVENTED | no such instruction in the record\n\n"
    "## AUDIT: Exhibits\n\n"
    "- Duty paragraph relies on Civil Code §§ 2100, 2168 and CACI 902 | INVENTED | no cite in the record\n"
    "- Specials $1,200.00 'sum of the rows' | ARITHMETIC | the rows sum to $1,100.00\n"
)


def test_a_statute_or_instruction_is_left_in_the_letter_for_the_attorney_never_struck():
    """2026-11-07: the check called the letter's quotation of a Civil Code section
    "invented" and the final pass struck it, leaving the lead-in over a gap
    marker; the review then held on the contradiction between the notes
    ("taken out") and the letter (still citing it). FALSIFIER: on the code
    before, the quotation and the CACI citation are replaced by markers."""
    md, notes = finalpass.settle(LAW_DRAFT, LAW_AUDIT)
    body = md.split(finalpass.NOTES_MARK)[0]
    assert "must use the utmost care and diligence" in body and "(CACI No. 3928.)" in body
    assert "[TO BE SUPPLIED" not in body and "taken out" not in "\n".join(notes)
    assert sum("cites legal authority" in n for n in notes) == 2
    # the notes to the attorney are not the letter: never edited, told as what they are
    assert "Duty paragraph relies on Civil Code" in md and "Specials $1,200.00" in md
    assert sum(n.startswith("Attorney notes: a note to the attorney") for n in notes) == 2
    assert finalpass.summary(notes) == {"removed": 0, "flagged": 0, "cleared": 0, "kept": 0}
    assert rv.wording({"demand": body, "attorney_notes": "\n".join(notes)}) == []


def test_a_record_fact_called_invented_in_the_letter_is_still_struck():
    draft = "## Liability\n\nThe other driver admitted fault at the scene (cite). The light was red (report, p. 1).\n"
    audit = "## AUDIT: Liability\n\n- The other driver admitted fault at the scene | INVENTED | no such statement in the record\n"
    md, notes = finalpass.settle(draft, audit)
    assert "admitted fault" not in md and "[TO BE SUPPLIED: a statement here is not supported by the file" in md
    assert finalpass.summary(notes)["removed"] == 1


def test_the_authorities_the_letter_cites_are_listed_for_the_attorney():
    with_case = LAW_DRAFT.replace(
        "## Exhibits",
        "## Damages\n\n(*Howell v. Hamilton Meats & Provisions, Inc.* (2011) 52 Cal.4th 541, 566.)\n\n## Exhibits",
    )
    cited = finalpass.authorities_cited(with_case)
    assert cited == [
        "Civ. Code, §§ 2100, 2168",
        "CACI No. 902",
        "Civil Code section 2100",
        "CACI No. 3928",
        "Howell v. Hamilton Meats & Provisions, Inc. (2011) 52 Cal.4th 541",
    ]
    assert finalpass.is_authority("Quoted text of Civil Code §2100") and finalpass.is_authority(
        "CACI No. 3928 cited for the rule"
    )
    assert finalpass.is_authority("Firm's letters cite neither §998 nor §999")
    assert not finalpass.is_authority("The other driver admitted fault at the scene", "no such statement in the record")
