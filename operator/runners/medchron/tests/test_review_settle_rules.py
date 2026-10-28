"""The pre-delivery review settles the system's own words and confirmed
record disagreements by rule (2026-10-24, the Pamplona hold: 18 MUST-FIX on a
chronology whose paid audit confirmed every claim; 7 were the system's own
words in the entries, 11 were check 2 disagreements between two confirmed
statements). Deletion and disclosure only, nothing written.

Every rule is a falsifier pair: a settled case, and the case it must not
settle (an entry it would empty, a table row that is no claim, a statement
the audit did not confirm). Names and values are synthetic.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from medchron import wording
from medchron.audit import claims as CL
from medchron.proof import review as proof_review
from medchron.stages import review, review_policy, review_settle
from medchron.stages.base import StageRun
from test_final_stages import DOC
from test_review_proof import FakeDoorway, _manifest_names, _package

TODAY = date(2026, 10, 13)
SYS = "The image carries no transcribed text."
LINE_0120 = "Neck pain after the collision. (Exhibit 1 - p. 2)"
TRIAGE = "Pain was rated 7 of 10 at triage."
DISCHARGE = "Pain was rated 4 of 10 at discharge."
DIFFER_DOC = DOC.replace(
    "Cervical strain. (Exhibit 1 - p. 2)",
    f"Cervical strain. (Exhibit 1 - p. 2)\n\n{TRIAGE} (Exhibit 1 - p. 2)\n\n{DISCHARGE} (Exhibit 1 - p. 3)",
)
PLACEHOLDER = "(inpatient flowsheets, orders and medication administration record, multiple documenting staff)"


def _md(sr: StageRun) -> str:
    return (sr.slug_dir / "runs" / "alpha" / "final-chronology.md").read_text()


def _doc(sr: StageRun) -> Path:
    return sr.slug_dir / "runs" / "alpha" / "final-chronology.md"


def _chron(sr: StageRun) -> Path:
    return sr.slug_dir / "out" / "alpha" / "Alpha Example - Medical Chronology 10-13-26.docx"


def _code(sr: StageRun) -> list[proof_review.Finding]:
    """The code findings on the rendered package, check 3 only."""
    found = review.code_findings(
        review.docx_text(_chron(sr)), review.entries(_chron(sr)), [], {}, today=TODAY, dob=None
    )
    return [f for f in found if f.check.startswith("3")]


def _model(check: str, finding: str, cite: str = "entry 01/20/2026 Example Clinic") -> proof_review.Finding:
    return proof_review.Finding(proof_review.MUST, check, finding, cite, "model")


def _settle(sr: StageRun, found: list[proof_review.Finding]) -> review_policy.Outcome:
    return review_policy.settle(sr, _doc(sr), found, repair=False, today=TODAY)


def _policy(sr: StageRun) -> dict:
    return json.loads((sr.slug_dir / "runs" / "alpha" / review_policy.POLICY_RECORD).read_text())


# ---- the code check -----------------------------------------------------------------
@pytest.mark.parametrize(
    "sentence",
    [
        "In the transcription, the boxes for refusing were blank.",
        "Per the transcription, the score was 7.",
        "No transcribed text was available for the page.",
        "Text on the following page reads as follows: rest.",
        "The image carries no legible words.",
    ],
)
def test_each_extraction_phrase_is_must_fix(sentence):
    found = review.code_findings(f"x\n{sentence} (Exhibit 1 - p. 2)", [], [], {}, today=TODAY, dob=None)
    hits = [f for f in found if f.check == "3 system language"]
    assert hits and all(f.grade == proof_review.MUST for f in hits), found


def test_the_machine_transcription_citation_tail_is_not_system_language():
    text = "Neck pain after the collision. (Exhibit 1 - p. 2, machine transcription)"
    found = review.code_findings(text, [], [], {}, today=TODAY, dob=None)
    assert not [f for f in found if f.check.startswith("3")], found


def test_a_provider_heading_naming_document_types_is_must_fix_and_a_specialty_is_not():
    rows = [("01/20/2026", f"Example Hospital {PLACEHOLDER}"), ("02/01/2026", "Example Clinic (orthopedics)")]
    hits = review.heading_findings(rows)
    assert len(hits) == 1 and hits[0].grade == proof_review.MUST and PLACEHOLDER in hits[0].finding
    assert hits[0].cite.startswith("entry 01/20/2026")


# ---- scrub_system ---------------------------------------------------------------------
def test_a_placeholder_heading_loses_its_parenthetical_and_keeps_the_provider(job_dir, firm_config_path, data_root):
    doc = DOC.replace(
        "Example Clinic | Patient Complaints & Limitations",
        f"Example Clinic {PLACEHOLDER} | Patient Complaints & Limitations",
    )
    sr, _ = _package(job_dir, firm_config_path, data_root, doc)
    found = _code(sr)
    assert [f.grade for f in found] == [proof_review.MUST], found
    out = _settle(sr, found)
    md = _md(sr)
    assert PLACEHOLDER not in md and "01/20/2026\nExample Clinic | Patient Complaints & Limitations" in md
    assert out.findings[0].grade == proof_review.NOTE and out.changed
    assert out.findings[0].finding.startswith("removed before delivery, system wording: ")
    assert _policy(sr)["system_scrubbed"] == 1


def test_a_model_finding_quoting_the_whole_placeholder_heading_settles_too(job_dir, firm_config_path, data_root):
    head = f"Example Clinic {PLACEHOLDER}"
    doc = DOC.replace("Example Clinic | Patient Complaints & Limitations", f"{head} | Patient Complaints & Limitations")
    sr, _ = _package(job_dir, firm_config_path, data_root, doc)
    out = _settle(sr, [_model("3 system language", f'the heading "{head}" is a placeholder title')])
    assert out.findings[0].grade == proof_review.NOTE and PLACEHOLDER not in _md(sr)
    assert "\nExample Clinic | Patient Complaints & Limitations" in _md(sr)


def test_the_system_sentence_goes_and_the_rest_and_its_citation_stay(job_dir, firm_config_path, data_root):
    doc = DOC.replace(LINE_0120, f"Neck pain after the collision. {SYS} (Exhibit 1 - p. 2)")
    sr, _ = _package(job_dir, firm_config_path, data_root, doc)
    (sr.slug_dir / "runs" / "alpha" / "entries_scoped_final.md").write_text(
        f"01/20/2026\nExample Clinic | X\n\nNeck pain after the collision. {SYS} (Exhibit 1 - p. 2)\n"
    )
    found = [*_code(sr), _model("3 system language", f'"{SYS}" describes the text extraction')]
    assert len(found) == 3 and all(f.grade == proof_review.MUST for f in found), found
    out = _settle(sr, found)
    md = _md(sr)
    assert SYS not in md and LINE_0120 in md and "Cervical strain. (Exhibit 1 - p. 2)" in md
    assert all(f.grade == proof_review.NOTE for f in out.findings), out.findings
    assert SYS not in (sr.slug_dir / "runs" / "alpha" / "entries_scoped_final.md").read_text()


def test_an_entrys_only_paragraph_is_never_deleted_and_the_finding_is_flagged(job_dir, firm_config_path, data_root):
    """Falsifier: the 03/30 entry has one cited paragraph and it is the
    system sentence. Deleting it would empty the entry: left unedited, and
    since 2026-10-28 flagged for the attorney rather than held."""
    doc = DOC.replace("Continue therapy. (Exhibit 1 - p. 3)", f"{SYS} (Exhibit 1 - p. 3)")
    sr, _ = _package(job_dir, firm_config_path, data_root, doc)
    found = _code(sr)
    assert found
    out = _settle(sr, found)
    assert f"{SYS} (Exhibit 1 - p. 3)" in _md(sr) and _md(sr) == doc
    assert all(f.grade == proof_review.NOTE for f in out.findings) and not out.changed
    assert all(f.finding.startswith(review_settle.RESIDUAL_NOTES[3]) for f in out.findings)
    assert _policy(sr)["system_scrubbed"] == 0 and _policy(sr)["residual_flagged"] == len(found)


# ---- the three Pamplona residuals (2026-10-25), synthetic names --------------------
ROLE_HEAD = "Example Clinic, hand RN triage supporting Jane Roe, MD"
ROLE_DOC = DOC.replace(
    "| Example Clinic | 01/20/2026 - 03/30/2026 | 2 | Exhibit 1 - p. 2 |",
    f"| Example Clinic | 01/20/2026 | 1 | Exhibit 1 - p. 2 |\n| {ROLE_HEAD} | 03/30/2026 | 1 | Exhibit 1 - p. 3 |",
).replace("Example Clinic | Treatment Recommendations", f"{ROLE_HEAD} | Treatment Recommendations")
ROLE_FINDING = _model(
    "3 system language",
    f'The provider row "{ROLE_HEAD}" describes the record\'s role instead of naming a provider.',
    "Provider table, 03/30/2026, Exhibit 1, p. 3",
)


def test_a_role_clause_in_a_provider_heading_is_cut_in_the_entry_and_the_table(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root, ROLE_DOC)
    out = _settle(sr, [ROLE_FINDING])
    md = _md(sr)
    assert "hand RN triage" not in md
    assert "03/30/2026\nExample Clinic | Treatment Recommendations" in md
    assert "| Example Clinic | 03/30/2026 | 1 | Exhibit 1 - p. 3 |" in md
    assert out.findings[0].grade == proof_review.NOTE and out.renamed == 1


def test_a_credential_after_a_name_is_no_role_and_stays(job_dir, firm_config_path, data_root):
    """Falsifier: "Jane Roe, RN" is a name and a credential, never cut."""
    head = "Jane Roe, RN"
    doc = DOC.replace("Example Clinic | Treatment Recommendations", f"{head} | Treatment Recommendations")
    sr, _ = _package(job_dir, firm_config_path, data_root, doc)
    out = _settle(sr, [_model("3 system language", f'the provider row "{head}" names a role')])
    assert f"03/30/2026\n{head} | Treatment Recommendations" in _md(sr)
    assert out.findings[0].grade == proof_review.NOTE and out.renamed == 0
    assert out.findings[0].finding.startswith(review_settle.RESIDUAL_NOTES[3])


NO_PROVIDER_HEAD = "Outside radiology report, facility not stated"
NO_PROVIDER_DOC = DOC.replace(
    "| Example Clinic | 01/20/2026 - 03/30/2026 | 2 | Exhibit 1 - p. 2 |",
    f"| Example Clinic | 01/20/2026 | 1 | Exhibit 1 - p. 2 |\n| {NO_PROVIDER_HEAD} | 03/30/2026 | 1 | Exhibit 1 - p. 3 |",
).replace("Example Clinic | Treatment Recommendations", f"{NO_PROVIDER_HEAD} | Treatment Recommendations")


def test_a_heading_that_names_no_provider_becomes_the_firms_not_stated_wording(job_dir, firm_config_path, data_root):
    """A joint matter, 2026-10-28: held on a heading that is a document type and a
    placeholder. The whole quoted name goes; the firm's wording takes its
    place in the entry and the table, and nothing else is written."""
    sr, _ = _package(job_dir, firm_config_path, data_root, NO_PROVIDER_DOC)
    f = _model("3 system language", f'The provider heading "{NO_PROVIDER_HEAD}" names a document type, not a provider.')
    out = _settle(sr, [f])
    md = _md(sr)
    assert NO_PROVIDER_HEAD not in md
    assert f"03/30/2026\n{wording.PROVIDER_NOT_STATED} | Treatment Recommendations" in md
    assert f"| {wording.PROVIDER_NOT_STATED} | 03/30/2026 | 1 | Exhibit 1 - p. 3 |" in md
    assert out.findings[0].grade == proof_review.NOTE and out.renamed == 1


def test_a_phrase_already_removed_does_not_block_the_rest_of_the_finding(job_dir, firm_config_path, data_root):
    """A joint matter, 2026-10-28: the reviewer quoted the heading and "in
    this chunk", which code's own finding had already removed. The removed
    phrase is in no line, so the heading is still settled."""
    sr, _ = _package(job_dir, firm_config_path, data_root, NO_PROVIDER_DOC)
    f = _model(
        "3 system language",
        f'The provider heading "{NO_PROVIDER_HEAD}" is a placeholder, separate from the "in this chunk" wording.',
    )
    out = _settle(sr, [f])
    assert NO_PROVIDER_HEAD not in _md(sr) and out.findings[0].grade == proof_review.NOTE
    assert out.findings[0].finding.startswith("removed before delivery")


def test_a_phrase_elsewhere_in_the_document_still_blocks(job_dir, firm_config_path, data_root):
    """Falsifier: the second phrase is in Limitations, not removed: the body
    is not edited for it and the finding is flagged, never marked removed."""
    lim = "* One page was read in this chunk only."
    sr, _ = _package(job_dir, firm_config_path, data_root, NO_PROVIDER_DOC.rstrip("\n") + f"\n{lim}\n")
    f = _model("3 system language", f'"{NO_PROVIDER_HEAD}" and "read in this chunk only" are the system\'s')
    out = _settle(sr, [f])
    assert NO_PROVIDER_HEAD in _md(sr) and lim in _md(sr)
    assert out.findings[0].finding.startswith(review_settle.RESIDUAL_NOTES[3])


def test_a_placeholder_before_the_firms_own_wording_still_settles(job_dir, firm_config_path, data_root):
    """Seen live, 2026-10-28: the map wrote the placeholder AND the firm's
    phrase after it. The reviewer quoted the placeholder; the heading becomes
    the firm's phrase once, in the entry and the table."""
    head = f"{NO_PROVIDER_HEAD} {wording.PROVIDER_NOT_STATED}"
    doc = NO_PROVIDER_DOC.replace(NO_PROVIDER_HEAD, head)
    sr, _ = _package(job_dir, firm_config_path, data_root, doc)
    out = _settle(sr, [_model("3 system language", f'The provider heading "{NO_PROVIDER_HEAD}" names no provider.')])
    md = _md(sr)
    assert NO_PROVIDER_HEAD not in md and out.findings[0].grade == proof_review.NOTE
    assert f"03/30/2026\n{wording.PROVIDER_NOT_STATED} | Treatment Recommendations" in md
    assert f"| {wording.PROVIDER_NOT_STATED} | 03/30/2026 | 1 | Exhibit 1 - p. 3 |" in md


def test_a_named_provider_quoted_whole_is_never_replaced(job_dir, firm_config_path, data_root):
    """Falsifier: a real name with no placeholder words stays; the finding is
    the attorney's to weigh (flagged), never a rename."""
    head = "Example Imaging Center"
    doc = DOC.replace("Example Clinic | Treatment Recommendations", f"{head} | Treatment Recommendations")
    sr, _ = _package(job_dir, firm_config_path, data_root, doc)
    out = _settle(sr, [_model("3 system language", f'the provider heading "{head}" is not a provider')])
    assert f"03/30/2026\n{head} | Treatment Recommendations" in _md(sr)
    assert out.findings[0].grade == proof_review.NOTE and not out.changed


def test_a_placeholder_quoted_only_in_part_keeps_the_heading(job_dir, firm_config_path, data_root):
    """Falsifier: the reviewer quoted "facility not stated" alone; the name
    before it may be a provider, so nothing is replaced; the finding is flagged."""
    sr, _ = _package(job_dir, firm_config_path, data_root, NO_PROVIDER_DOC)
    out = _settle(sr, [_model("3 system language", 'the heading says "facility not stated"')])
    assert f"03/30/2026\n{NO_PROVIDER_HEAD} | Treatment Recommendations" in _md(sr)
    assert out.findings[0].grade == proof_review.NOTE and not out.changed


def test_a_held_no_provider_heading_settles_on_resume_with_no_model_call(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root, NO_PROVIDER_DOC)
    rd = sr.slug_dir / "runs" / "alpha"
    rows = json.loads((sr.slug_dir / "out" / "alpha" / "upload_manifest.json").read_text())
    f = _model("3 system language", f'The provider heading "{NO_PROVIDER_HEAD}" names a document type.')
    proof_review.save(rd, [f], model="m", lane="chronology")
    (rd / review.INPUT_SHA).write_text(review._input_sha(rows))
    sr._doorway = FakeDoorway()  # type: ignore[assignment]  # any model call fails the test
    assert review.run(sr, today=TODAY) == 0, log
    assert proof_review.load(rd)["must_fix"] == 0
    assert NO_PROVIDER_HEAD not in review.docx_text(_chron(sr))
    assert sr._doorway.calls == []  # type: ignore[attr-defined]


def test_a_list_number_opens_its_sentence_and_the_item_goes(job_dir, firm_config_path, data_root):
    doc = DOC.replace(
        "Cervical strain. (Exhibit 1 - p. 2)",
        "Review of systems:\n1. Constitutional: no fever.\n7. Remaining systems as printed.\nCervical strain. (Exhibit 1 - p. 2)",
    )
    sr, _ = _package(job_dir, firm_config_path, data_root, doc)
    out = _settle(sr, [_model("3 system language", '"7. Remaining systems as printed." is a placeholder')])
    md = _md(sr)
    assert "Remaining systems as printed" not in md and "1. Constitutional: no fever." in md
    assert "Cervical strain. (Exhibit 1 - p. 2)" in md and out.findings[0].grade == proof_review.NOTE


def test_a_reviewer_quoting_with_other_quote_marks_still_matches(job_dir, firm_config_path, data_root):
    sentence = 'The start of the note preceding "Findings" is not printed on these pages.'
    doc = DOC.replace(LINE_0120, f"Neck pain after the collision. {sentence} (Exhibit 1 - p. 2)")
    sr, _ = _package(job_dir, firm_config_path, data_root, doc)
    f = _model(
        "3 system language",
        "\"The start of the note preceding 'Findings' is not printed on these pages\" reads the pages",
    )
    out = _settle(sr, [f])
    assert "is not printed on these pages" not in _md(sr) and LINE_0120 in _md(sr)
    assert out.findings[0].grade == proof_review.NOTE


def test_a_held_review_is_settled_again_on_resume_with_no_model_call(job_dir, firm_config_path, data_root):
    """Pamplona 2026-10-25: held on findings a later rule settles; a resume
    re-applies the $0 rules to the recorded findings and files."""
    sr, log = _package(job_dir, firm_config_path, data_root, ROLE_DOC)
    rd = sr.slug_dir / "runs" / "alpha"
    rows = [m for m in json.loads((sr.slug_dir / "out" / "alpha" / "upload_manifest.json").read_text())]
    proof_review.save(rd, [ROLE_FINDING], model="m", lane="chronology")
    (rd / review.INPUT_SHA).write_text(review._input_sha(rows))
    sr._doorway = FakeDoorway()  # type: ignore[assignment]  # any model call fails the test
    rc = review.run(sr, today=TODAY)
    assert rc == 0, log
    rec = proof_review.load(rd)
    assert rec["must_fix"] == 0 and "hand RN triage" not in review.docx_text(_chron(sr))
    assert sr._doorway.calls == []  # type: ignore[attr-defined]


def test_a_held_review_the_rules_cannot_settle_holds_again_unchanged(job_dir, firm_config_path, data_root):
    """Falsifier: a finding no rule settles (another person's records, cited
    to no entry) still holds on resume, the entries untouched (Limitations is
    rewritten from the files as they stand either way)."""
    sr, log = _package(job_dir, firm_config_path, data_root, DOC)
    rd = sr.slug_dir / "runs" / "alpha"
    rows = json.loads((sr.slug_dir / "out" / "alpha" / "upload_manifest.json").read_text())
    stuck = _model("1 another person's records", "the summary may carry another person's visits", "Medical Summary")
    proof_review.save(rd, [stuck], model="m", lane="chronology")
    (rd / review.INPUT_SHA).write_text(review._input_sha(rows))
    sr._doorway = FakeDoorway()  # type: ignore[assignment]
    assert review.run(sr, today=TODAY) == review.EXIT_HELD
    assert CL.body_of(_md(sr)) == CL.body_of(DOC) and sr._doorway.calls == []  # type: ignore[attr-defined]
    assert proof_review.load(rd)["must_fix"] == 1


def test_limitations_are_never_edited_and_the_body_alone_decides(job_dir, firm_config_path, data_root):
    lim = "* The transcription of one scanned page was partial."
    doc = DOC.replace(
        LINE_0120, "Neck pain after the collision. In the transcription, a box was blank. (Exhibit 1 - p. 2)"
    )
    doc = doc.rstrip("\n") + f"\n{lim}\n"
    sr, _ = _package(job_dir, firm_config_path, data_root, doc)
    found = _code(sr)
    lim_only = _model("3 system language", '"of one scanned page was partial" describes the extraction', "Limitations")
    out = _settle(sr, [*found, lim_only])
    md = _md(sr)
    assert lim in md, "Limitations must never be edited"
    assert LINE_0120 in md and "a box was blank" not in md
    graded = {f.finding: f.grade for f in out.findings}
    assert all(g == proof_review.NOTE for t, g in graded.items() if "scanned page" not in t), graded
    # A phrase only in Limitations was never in the entries: not this step's to
    # edit, so it is flagged for the attorney (2026-10-28), never a stop.
    left = [f for f in out.findings if "scanned page" in f.finding]
    assert [f.grade for f in left] == [proof_review.NOTE] and left[0].finding.startswith(
        review_settle.RESIDUAL_NOTES[3]
    )


# ---- disclose_differ ------------------------------------------------------------------
def _audit_all(sr: StageRun, verdict: str = "SUPPORTED", only: str | None = None) -> None:
    """A real row for every live claim (or only the one containing `only`)."""
    out = sr.slug_dir / "out" / "alpha"
    body = CL.body_of(_md(sr))
    for c in CL.extract_claims(body, {1}):
        if only is None or only in c["claim"]:
            CL.append_row(out / "audit-results.jsonl", {"key": c["key"], "kind": "real", "verdict": verdict})


DIFFER = _model("2 entries contradict", f'"{TRIAGE[:-1]}" and "{DISCHARGE[:-1]}" give two pain scores')


def test_two_confirmed_statements_on_different_pages_are_disclosed(job_dir, firm_config_path, data_root):
    sr, _ = _package(job_dir, firm_config_path, data_root, DIFFER_DOC)
    _audit_all(sr)
    out = _settle(sr, [DIFFER])
    assert out.findings[0].grade == proof_review.NOTE
    assert out.findings[0].finding.startswith(review_settle.DIFFER_NOTE)
    assert _policy(sr)["differ_disclosed"] == 1 and _md(sr) == DIFFER_DOC


def test_two_cited_pages_in_the_dated_entry_are_disclosed(job_dir, firm_config_path, data_root):
    sr, _ = _package(job_dir, firm_config_path, data_root, DIFFER_DOC)
    _audit_all(sr)
    f = _model(
        "2 entries contradict", "the triage score at Exhibit 1, p. 2 and the discharge score at Exhibit 1, p. 3 differ"
    )
    assert _settle(sr, [f]).findings[0].grade == proof_review.NOTE


def _flagged(out: review_policy.Outcome, sr: StageRun) -> None:
    """Not confirmed, so never disclosed as the records differing: flagged for
    the attorney in wording that claims nothing was confirmed (2026-10-25: the
    Operator finishes the chronology; an unresolved disagreement is the
    attorney's to weigh, not a stop for a person)."""
    f = out.findings[0]
    assert f.grade == proof_review.NOTE and f.finding.startswith(review_settle.FLAG_NOTE)
    assert review_settle.DIFFER_NOTE not in f.finding and "confirmed on" not in f.finding
    assert _policy(sr)["differ_disclosed"] == 0 and _policy(sr)["differ_flagged"] == 1
    assert _md(sr) == DIFFER_DOC, "flagging never edits the chronology"


def test_a_table_row_side_is_no_claim_and_is_flagged_not_confirmed(job_dir, firm_config_path, data_root):
    """Falsifier for the confirmed wording: the diagnosis-table row is not an audited claim."""
    sr, _ = _package(job_dir, firm_config_path, data_root, DIFFER_DOC)
    _audit_all(sr)
    f = _model(
        "2 entries contradict",
        f'"Sprain of ligaments of cervical spine" contradicts "{TRIAGE[:-1]}" and "{DISCHARGE[:-1]}"',
    )
    _flagged(_settle(sr, [f]), sr)


def test_a_side_whose_latest_verdict_is_unsupported_is_flagged_not_confirmed(job_dir, firm_config_path, data_root):
    sr, _ = _package(job_dir, firm_config_path, data_root, DIFFER_DOC)
    _audit_all(sr)
    _audit_all(sr, "UNSUPPORTED", only="at discharge")  # the later row is the one that counts
    _flagged(_settle(sr, [DIFFER]), sr)


def test_two_quotes_of_one_claim_are_one_page_and_are_flagged_not_confirmed(job_dir, firm_config_path, data_root):
    sr, _ = _package(job_dir, firm_config_path, data_root, DIFFER_DOC)
    _audit_all(sr)
    f = _model("2 entries contradict", '"Pain was rated 7 of 10" and "of 10 at triage" disagree')
    _flagged(_settle(sr, [f]), sr)


def test_a_disagreement_with_no_cite_is_flagged_not_confirmed(job_dir, firm_config_path, data_root):
    """Nothing names where to look: not the cited-pages flag (differ_flagged
    stays 0), but the residual flag, so it is never a stop (2026-10-28)."""
    sr, _ = _package(job_dir, firm_config_path, data_root, DIFFER_DOC)
    _audit_all(sr)
    f = _model("2 entries contradict", "two pain scores disagree somewhere", cite="")
    out = _settle(sr, [f])
    assert out.findings[0].grade == proof_review.NOTE and _policy(sr)["differ_flagged"] == 0
    assert _policy(sr)["residual_flagged"] == 1


def test_a_credential_does_not_end_the_sentence_the_scrub_deletes(job_dir, firm_config_path, data_root):
    """Pamplona 2026-10-25: split at "M.D." the system sentence matched neither
    half, and a deletion would have left "at 12:41 PM, reads as follows."."""
    sys_sentence = "Text on the following page, printed after a note by Thomas Roe, M.D. at 12:41 PM, reads as follows."
    line = f"Neck pain after the collision. {sys_sentence} (Exhibit 1 - p. 2)"
    sr, _ = _package(job_dir, firm_config_path, data_root, DOC.replace(LINE_0120, line))
    f = _model("3 system language", f'"{sys_sentence[:-1]}" describes how the pages were read')
    out = _settle(sr, [f])
    md = _md(sr)
    assert LINE_0120 in md and "reads as follows" not in md and "12:41" not in md
    assert out.findings[0].grade == proof_review.NOTE


# ---- the whole review ----------------------------------------------------------------
def test_a_review_whose_only_musts_settle_by_rule_files_with_the_notes(job_dir, firm_config_path, data_root):
    doc = DIFFER_DOC.replace(LINE_0120, f"Neck pain after the collision. {SYS} (Exhibit 1 - p. 2)")
    sr, log = _package(job_dir, firm_config_path, data_root, doc)
    _audit_all(sr)
    rows = [
        f'| MUST-FIX | 3 system language | "{SYS}" describes the text extraction | entry 01/20/2026 Example Clinic |',
        f'| MUST-FIX | 2 entries contradict | "{TRIAGE[:-1]}" and "{DISCHARGE[:-1]}" give two pain scores '
        "| entry 01/20/2026 Example Clinic |",
    ]
    verdict = "\n".join([*rows, "VERDICT: 2 MUST-FIX, 0 SHOULD-FIX, 0 NOTE"])
    decline = (
        '{"edits": [{"finding": "F1", "anchor": "N1", "action": "decline", '
        '"reason": "both statements are on their pages"}], "complete": true}'
    )
    sr._doorway = FakeDoorway(verdict, decline)  # type: ignore[assignment]
    rc = review.run(sr, today=TODAY, verify=lambda _b, _a: [], images=lambda _x, _p: None, audit=lambda _s: 0)
    assert rc == 0, log
    rec = proof_review.load(sr.slug_dir / "runs" / "alpha")
    assert rec["must_fix"] == 0, rec["findings"]
    assert SYS not in review.docx_text(_chron(sr)) and TRIAGE in review.docx_text(_chron(sr))
    names = _manifest_names(sr)
    notes = review.docx_text(sr.slug_dir / "out" / "alpha" / names[1])
    assert "the records differ on this point" in notes and "removed before delivery, system wording" in notes
    pol = _policy(sr)
    assert pol["system_scrubbed"] == 3 and pol["differ_disclosed"] == 1 and pol["must_fix_left"] == 0
