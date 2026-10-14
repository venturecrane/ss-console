"""The update lane catches what an independent attorney-style review of a
laptop update caught (2026-10-13): nine defect classes, one test each, every
one failing on the code before this change. Every value is invented."""

from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import update_testkit as K
from medchron.update import body as B, collect as C

FAX_A = "FAX 555-0101 06/02/2026 09:14 PAGE 3 OF 12"
FAX_B = "FAX 555-0177 06/09/2026 16:40 PAGE 7 OF 30"
RECORD = (
    "Example Orthopedics follow up note. The patient reports persistent neck pain radiating to the right "
    "shoulder with numbness in the thumb. Range of motion is reduced on rotation and the plan is a course of "
    "physical therapy with a recheck after the therapy course is finished."
)


# ---- change 1: overlap BETWEEN new files ---------------------------------------
def test_a_page_repeating_an_earlier_new_page_is_skipped_unless_it_adds_a_clinical_value() -> None:
    """Before: a page was only checked against the DELIVERED exhibits, so two
    re-filed copies of one new record (different fax headers) were both
    composed. Now the second is skipped, unless it carries a lab, vital or
    dose no earlier page stated."""
    from medchron.update import select as S

    earlier = S.Earlier()
    first = S.classify_new({1: FAX_A + "\n" + RECORD}, 1, set(), earlier)
    assert first[1][0] == S.READ
    again = S.classify_new(
        {1: FAX_B + "\n" + RECORD, 2: FAX_B + "\n" + RECORD + " Hemoglobin 11.2 g/dL was drawn."}, 2, set(), earlier
    )
    assert again[1] == (S.EXCLUDED, S.OVERLAP)
    assert again[2][0] == S.READ  # a new lab value is read even on a repeated page
    # Fax, page and time digits are not clinical values.
    assert S.clinical_values(FAX_A) == set() and "hemo 11.2" in S.clinical_values("Hemoglobin 11.2 g/dL")


# ---- change 2: diagnosis codes ----------------------------------------------------
def _tables(root: Path) -> dict:
    from medchron import icd_tables

    icd = root / "controls" / "icd"
    icd.mkdir(parents=True, exist_ok=True)
    line = "{:<5} {:<7} {} {:<60} {}\n"
    (icd / "icd10cm_order.txt").write_text(
        line.format("00001", "Z798", "1", "Other long term drug therapy", "Other long term (current) drug therapy")
        + line.format(
            "00002", "M5020", "1", "Cervical disc", "Other cervical disc displacement, unspecified cervical region"
        )
    )
    (icd / "CMS32_DESC_LONG_DX.txt").write_text(
        "2798 Other specified disorders involving the immune mechanism\n7242 Lumbago\n"
    )
    (icd / "VERSION.json").write_text("{}")
    return icd_tables.load(root)


def test_a_scanned_icd10_code_is_not_given_an_icd9_label_and_a_real_icd9_code_is_kept(tmp_path: Path) -> None:
    """Before: "279.8" off a 2026 record (a Z79.8 whose letter the scan lost)
    rendered as an ICD-9 immune disorder. Now it is read as Z79.8 when the
    record names that diagnosis, and shown as not legible when it does not;
    a historical ICD-9 code the record names is kept."""
    from medchron import icd_tables

    t = _tables(tmp_path)
    shown, desc, _ = icd_tables.resolve("279.8", t, "2026-02-10", "Medication list reviewed; drug therapy continues.")
    assert (shown, desc) == ("Z79.8", "Other long term (current) drug therapy")
    shown, desc, _ = icd_tables.resolve("279.8", t, "2026-02-10", "Follow up visit, no change.")
    assert (shown, desc) == ("279.8", None)
    assert icd_tables.resolve("724.2", t, "2014-05-01", "Lumbago.")[1] == "Lumbago (ICD-9-CM)"
    assert icd_tables.resolve("724.2", t, "2026-02-10", "Low back pain, lumbago.")[1] == "Lumbago (ICD-9-CM)"


def test_a_code_without_a_description_reads_not_legible_never_blank_and_is_reported() -> None:
    from medchron.update import assemble as A

    nex = [{"exhibit": 2, "title": "Exhibit 2 - Example Imaging (Medical Records)", "pages": 2}]
    new = "02/10/2026\nExample Imaging | Medical Diagnoses\n\nMedical Diagnoses\nFollow up imaging reviewed. (Exhibit 2 - p. 2)"
    claim = "Diagnosis code 279.8 recorded on 02/10/2026. (Exhibit 2 - p. 2)"
    md, rep = A.assemble(
        K.base_md(),
        C.audit_document(K.base_md(), [new], nex, icd_claims=[claim]),
        describe=lambda code, on, text: (code, None, "not legible as recorded"),
        canon=str,
        headings=K.HEADINGS,
        new_exhibits=nex,
    )
    assert "| 279.8 | code as recorded not legible | 02/10/2026 | Exhibit 2 - p. 2 |" in md
    assert "| 279.8 |  |" not in md and rep["icd_codes_not_legible"] == ["279.8"]
    assert claim not in md  # the claim was audited; the table row is what the firm reads


def test_a_new_diagnosis_code_is_audited_as_a_claim_cited_to_its_page() -> None:
    """Before: the Diagnostic Highlights section of the audit document was
    empty, so a new code reached the firm unaudited."""
    from medchron.audit import claims as CL
    from medchron.update import icd

    new = "02/10/2026\nExample Imaging | Medical Diagnoses\n\nMedical Diagnoses\nMRI reviewed. (Exhibit 2 - p. 2)"
    lines, log = icd.claims(
        ["2026-02-10 | Example Imaging | M50.20 | x.pdf", "2026-01-20 | Example Clinic | S13.4XXA | y.pdf"],
        [new],
        B.table_rows(B.sections(K.base_md())["## Diagnostic Highlights"]),
        lambda code, on, text: (code, "Other cervical disc displacement", "table"),
    )
    assert lines == ["Diagnosis code M50.20 recorded on 02/10/2026. (Exhibit 2 - p. 2)"]
    nex = [{"exhibit": 2, "title": "Exhibit 2 - Example Imaging (Medical Records)", "pages": 2}]
    doc = C.audit_document(K.base_md(), [new], nex, icd_claims=lines)
    got = [c["claim"] for c in CL.extract_claims(CL.body_of(doc), {1, 2})]
    assert "Diagnosis code M50.20 recorded on 02/10/2026." in got


# ---- change 3: exhibit numbering against the firm's own files -----------------------
def test_a_firm_numbered_file_takes_its_number_and_is_listed_without_a_gap() -> None:
    """Before: "Exhibit 2- Example Radiology.pdf" was invisible, so the
    update numbered its first new exhibit 2 as well."""
    from medchron.update import locate as L

    docs = [
        {"id": "c1", "name": "Alpha Example - Medical Chronology 09-01-26", "ext": ".docx", "folderId": "f"},
        {"id": "e1", "name": "Exhibit 1 - Example Clinic", "ext": ".pdf", "folderId": "f"},
        {"id": "e2", "name": "Exhibit 2- Example Radiology", "ext": ".pdf", "folderId": "f"},
        {"id": "e3", "name": "Exhibit 3: Example Lab", "ext": ".pdf", "folderId": "f"},
    ]
    where = L.locate(
        docs,
        [{"id": "f", "path": "/CHRON"}],
        template="{CLIENT} - Medical Chronology {MM-DD-YY}.docx",
        client="Alpha Example",
        file_id=None,
    )
    assert [e["exhibit"] for e in where["exhibits"]] == [1, 2, 3] and where["others"] == []
    rows = C.folder_rows(K.base_md(), where["exhibits"], {4})
    assert rows == ["| 2 | Exhibit 2- Example Radiology.pdf |", "| 3 | Exhibit 3: Example Lab.pdf |"]
    doc = C.audit_document(
        K.base_md(), [], [{"exhibit": 4, "title": "Exhibit 4 - X (Medical Records)"}], folder=where["exhibits"]
    )
    listed = [r.split("|")[1].strip() for r in B.table_rows(doc.split("## Exhibit List", 1)[1])[1:]]
    assert listed == ["1", "2", "3", "4"]  # no gap, in order


# ---- change 4: near-repeat rows --------------------------------------------------------
QUALITATIVE = (
    "01/20/2026\nExample Clinic | Medical Diagnoses\n\nMedical Diagnoses\n"
    "Cervical strain after the collision. Examination revealed paraspinal muscle spasm and tenderness. "
    "(Exhibit 2 - p. 3)"
)


def test_a_qualitative_finding_on_a_delivered_visit_is_kept_as_additional_findings() -> None:
    """Before: an entry restating a delivered visit was kept only for a new
    NUMBER, so a finding in words was dropped; and when kept it repeated the
    whole delivered visit."""
    _, base = B.entries_of(K.base_md())
    kept, rep = C.collect([QUALITATIVE], base, K.INCIDENT, str)
    assert kept == [
        "01/20/2026\nExample Clinic | Additional findings\n\nAdditional findings\n"
        "Examination revealed paraspinal muscle spasm and tenderness. (Exhibit 2 - p. 3)"
    ]
    assert rep["keep: additional findings"] == 1


def test_a_new_entry_another_new_entry_of_its_date_and_provider_covers_is_dropped() -> None:
    a = (
        "03/02/2026\nExample Therapy | Medical Diagnoses\n\nMedical Diagnoses\n"
        "Therapy session; pain 5 of 10; examination revealed guarding at the neck. (Exhibit 2 - p. 1)"
    )
    b = "03/02/2026\nExample Therapy | Medical Diagnoses\n\nMedical Diagnoses\nTherapy session; pain 5 of 10. (Exhibit 3 - p. 4)"
    _, base = B.entries_of(K.base_md())
    kept, rep = C.collect([a, b], base, K.INCIDENT, str)
    assert kept == [a] and rep["dropped: repeats another new entry of that date and provider"] == 1


# ---- change 5: handwritten dates -----------------------------------------------------
STATEMENT = "Example Chiropractic\nPatient statement\nDate of service {} Spinal manipulation charges 65.00"


def test_a_handwritten_date_is_settled_only_by_exactly_one_nearby_typed_date_of_service() -> None:
    from medchron.update import handwritten as H

    entry = "02/18/2026\nExample Chiropractic | Medical Diagnoses\n\nMedical Diagnoses\nAdjustment. (Exhibit 2 - p. 1)"
    one = H.service_dates([STATEMENT.format("02/13/2026")], "Example Chiropractic")
    assert H.redate(entry, one)[0].startswith("02/13/2026\n")
    assert H.redate(entry, set())[0].startswith("02/18/2026 (handwritten date, unconfirmed)\n")
    two = H.service_dates([STATEMENT.format("02/13/2026"), STATEMENT.format("02/20/2026")], "Example Chiropractic")
    assert H.redate(entry, two)[0].startswith("02/18/2026 (handwritten date, unconfirmed)\n")
    other = H.service_dates([STATEMENT.replace("Example", "Another").format("02/17/2026")], "Example Chiropractic")
    assert other == set()  # another provider's statement never settles the date
    _, ents = B.split_body("\n\n" + H.redate(entry, set())[0])
    assert B.iso(ents[0]) == "2026-02-18"  # still an entry, still dated by its reading


def test_the_vision_pass_marks_handwritten_pages() -> None:
    from medchron.stages import vision

    assert "HANDWRITING: yes" in vision.SYSTEM and "REQUIRED" in vision.SYSTEM
    assert vision.handwriting_field("HANDWRITING: yes\nBP 142/102").startswith("[handwritten]\n")


def test_update_collect_settles_an_entry_cited_to_a_handwritten_page(tmp_path: Path) -> None:
    from medchron.stages import update_collect as UC

    sd = tmp_path / "matter"
    (sd / "text").mkdir(parents=True)
    (sd / "text" / "n1.txt").write_text(
        "[p.1] (machine transcription)\n[handwritten]\nAdj C5 2/18/26\n[p.2]\n" + STATEMENT.format("02/13/2026")
    )
    ud = sd / "runs" / "alpha" / "update"
    ud.mkdir(parents=True)
    (ud / "update_plan.json").write_text(json.dumps({"exhibits": []}))
    sr = SimpleNamespace(slug_dir=sd, unit=SimpleNamespace(unit="alpha"))
    hand = (
        "02/18/2026\nExample Chiropractic | Medical Diagnoses\n\nMedical Diagnoses\nAdjustment C5. (Exhibit 2 - p. 1)"
    )
    typed = "02/13/2026\nExample Chiropractic | Medical Diagnoses\n\nMedical Diagnoses\nStatement. (Exhibit 2 - p. 2)"
    got, log = UC._settle_dates(sr, [hand, typed], [{"exhibit": 2, "id": "n1"}])  # type: ignore[arg-type]
    assert got[0].startswith("02/13/2026\n") and got[0].endswith("Adjustment C5. (Exhibit 2 - p. 1)")
    assert got[1] == typed and log == [
        {"reading": "02/18/2026", "verdict": "redated", "date": "02/13/2026", "provider": "Example Chiropractic"}
    ]


def test_the_audit_of_a_handwritten_page_is_given_the_providers_typed_statements() -> None:
    from medchron.audit.handwritten_evidence import Evidence, statements_for
    from medchron.audit.run import Round

    ev = Evidence.__new__(Evidence)
    ev.body = (
        "02/18/2026\nExample Chiropractic | Medical Diagnoses\n\nMedical Diagnoses\nAdjustment at C5 was performed."
    )
    ev._pool = ["[handwritten]\nAdj C5", STATEMENT.format("02/13/2026"), "Another Clinic statement charges 01/01/2026"]
    ev.index = None
    ev.page = lambda ex, p: "[handwritten]\nAdj C5" if p == 1 else "typed"  # type: ignore[method-assign]
    r = Round.__new__(Round)
    r._lock = threading.Lock()
    r.__dict__["hw_evidence"] = ev
    blocks = statements_for(r, {"exhibit": 2, "pages": [1], "claim": "Adjustment at C5 was performed."})
    assert len(blocks) == 1 and "Date of service 02/13/2026" in blocks[0]["text"]
    assert "Another Clinic" not in blocks[0]["text"]
    assert statements_for(r, {"exhibit": 2, "pages": [2], "claim": "Adjustment at C5 was performed."}) == []


# ---- change 7: Records Reviewed names every uncited new file ------------------------------
def test_records_reviewed_names_each_uncited_file_with_its_pages_and_a_cover_sheet() -> None:
    from medchron.stages.update_finish import uncited_name

    cover = {"source_name": "Records request.pdf", "pages": 1, "read": [], "excluded": {"no text on the page": 1}}
    repeat = {"source_name": "Refiled clinic notes.pdf", "pages": 9, "read": [], "excluded": {"already": 9}}
    assert uncited_name(cover) == "Records request.pdf (1 page, cover sheet only)"
    assert uncited_name(repeat) == "Refiled clinic notes.pdf (9 pages)"


# ---- change 8: provider spelling and labels ---------------------------------------------
def _entry(provider: str) -> str:
    return f"02/10/2026\n{provider} | Medical Diagnoses\n\nMedical Diagnoses\nSeen in follow up. (Exhibit 2 - p. 1)"


def test_a_near_provider_spelling_is_flagged_and_marked_likely_only_when_safe() -> None:
    """Review fix 6: never rewritten (two real clinicians can be one letter
    apart); flagged, with "likely the same person" only under the safe rule."""
    from medchron.update import providers as P

    base = [_entry("Rosa Bertelli, DC"), _entry("J. Moe, MD")]
    news = [_entry("Rosa Bertell, DC"), _entry("J. Poe, MD"), _entry("Rosa Bertell, MD")]
    got, log = P.snap(news, base)
    assert got == news  # the record's own spelling always stands
    assert [r["likely_same_person"] for r in log] == [["Rosa Bertelli, DC"], [], []]
    assert {r["action"] for r in log} == {"flagged"}


def test_a_second_credential_is_never_a_timeline_label() -> None:
    from medchron.stages.group import Canon

    c = Canon(SimpleNamespace(get=lambda *a, **k: None))
    assert c("Jane A. Roe, M.D., Ph.D.") != "Ph.D."
    assert c("Jane A. Roe, M.D., Ph.D.").startswith("Jane A. Roe")


# ---- change 9: the laptop's audit fixes ------------------------------------------------
def test_an_ntd_note_under_a_heading_is_never_a_claim() -> None:
    from medchron.audit import claims as CL

    body = (
        "09/10/2025\nExample Chiropractic | Treatments\n\nTreatments\n"
        "The visit ledger for 09/10/2025 records a cervical adjustment. (Exhibit 8 - p. 12)\n\n"
        "All Other Information\n"
        "[NTD: A portion of the records were handwritten and illegible.] (Exhibit 8 - p. 12)\n"
    )
    got = [c["claim"] for c in CL.extract_claims(body, {8})]
    assert len(got) == 1 and "cervical adjustment" in got[0]


def test_dates_compare_as_dates() -> None:
    from medchron.update import handwritten as H

    assert H.decide(date(2026, 2, 18), {date(2026, 2, 26)}) == ("open", None)  # 8 days: outside the window
