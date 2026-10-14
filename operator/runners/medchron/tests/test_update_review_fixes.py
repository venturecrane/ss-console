"""The code review of #3186 (2026-10-13): each defect it reproduced, as a
test that fails on the reviewed head (f9f6c0fc) and passes after the fix.
Plus the per-page vision routing the offline real-file run found. Every
value is invented."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import update_testkit as K
from medchron.update import body as B

PT_NOTE = (
    "Example Therapy daily note. Date of visit {d}. The patient performed cervical stabilization exercises "
    "and scapular retraction with a theraband, tolerated the session well, and was instructed to continue the "
    "home program between visits with ice after exercise."
)


# ---- fix 1: template visits are never excluded as repeats ---------------------
def test_template_visits_that_differ_only_in_date_are_both_read() -> None:
    from medchron.update import select as S

    within = S.classify_new(
        {1: PT_NOTE.format(d="06/03/2026"), 2: PT_NOTE.format(d="06/04/2026")}, 2, set(), S.Earlier()
    )
    assert [v[0] for v in within.values()] == [S.READ, S.READ]
    earlier = S.Earlier()
    S.classify_new({1: PT_NOTE.format(d="06/03/2026")}, 1, set(), earlier)
    other_day = S.classify_new({1: PT_NOTE.format(d="06/04/2026")}, 1, set(), earlier)
    assert other_day[1][0] == S.READ  # another visit on the same template
    worded = S.classify_new(
        {1: PT_NOTE.format(d="06/03/2026").replace("tolerated", "did not tolerate")}, 1, set(), earlier
    )
    assert worded[1][0] == S.READ  # a qualitative word differs
    refiled = S.classify_new(
        {1: "FAX 555-0101 07/01/2026 PAGE 2 OF 9\n" + PT_NOTE.format(d="06/03/2026")}, 1, set(), earlier
    )
    assert refiled[1] == (S.EXCLUDED, S.OVERLAP)  # the same page refiled under another fax header


# ---- fixes 2 and 3: dates of service only, and the date line kept --------------
STATEMENT = (
    "Example Chiropractic\nPatient statement\nStatement date 03/05/2026\nPayment due 03/20/2026\n"
    "Please remit the balance shown."
)


def test_a_statements_print_and_due_dates_never_settle_a_handwritten_date() -> None:
    from medchron.update import handwritten as H

    entry = "03/01/2026\nExample Chiropractic | Treatments\n\nTreatments\nAdjustment. (Exhibit 2 - p. 1)"
    dos = H.service_dates([STATEMENT], "Example Chiropractic")
    assert dos == set()
    assert H.redate(entry, dos)[0].startswith("03/01/2026 (handwritten date, unconfirmed)\n")
    item = STATEMENT + "\n03/02/2026 98940 Spinal manipulation 65.00"
    assert H.service_dates([item], "Example Chiropractic") == {date(2026, 3, 2)}


def test_redate_keeps_what_the_date_line_carried() -> None:
    from medchron.update import handwritten as H

    entry = "03/01/2026 (late entry)\nExample Chiropractic | Treatments\n\nTreatments\nAdjustment. (Exhibit 2 - p. 1)"
    assert H.redate(entry, {date(2026, 3, 2)})[0].startswith("03/02/2026 (late entry)\n")
    assert H.redate(entry, set())[0].startswith("03/01/2026 (handwritten date, unconfirmed) (late entry)\n")


# ---- fixes 4 and 5: additional findings and plain entries ---------------------------
def test_a_trailing_uncited_clause_travels_with_the_last_citation() -> None:
    from medchron.update import collect as C

    e = (
        "01/20/2026\nExample Clinic | Medical Diagnoses\n\nMedical Diagnoses\n"
        "Range of motion reduced to 40 degrees (Exhibit 4 - p. 1). Positive for radiculopathy."
    )
    _, base = B.entries_of(K.base_md())
    kept, _ = C.collect([e], base, K.INCIDENT, str)
    assert len(kept) == 1 and "radiculopathy" in kept[0]


def test_an_entry_with_no_value_or_finding_term_is_kept_unless_its_text_restates() -> None:
    from medchron.update import repeats as R

    _, base = B.entries_of(K.base_md())
    restates = "01/20/2026\nExample Clinic | All Other Information\n\nAll Other Information\nReturn to clinic in four weeks for a recheck of the neck. (Exhibit 2 - p. 3)"
    news = "01/20/2026\nExample Clinic | All Other Information\n\nAll Other Information\nIce and rest were advised for the neck between sessions. (Exhibit 2 - p. 3)"
    assert R.keep_decision(restates, base, str)[0] == R.SAME_VISIT
    assert R.keep_decision(news, base, str) == ("keep", news)
    a = "03/02/2026\nExample Therapy | Treatments\n\nTreatments\nCervical stretching was taught to the patient. (Exhibit 2 - p. 1)"
    b = "03/02/2026\nExample Therapy | Treatments\n\nTreatments\nA home program handout was reviewed with the patient. (Exhibit 2 - p. 2)"
    assert R.dedupe_new([a, b], str) == ([a, b], 0)


# ---- fix 6: a near name is flagged, never rewritten -------------------------------
def test_two_clinicians_one_letter_apart_are_never_merged() -> None:
    from medchron.update import providers as P

    def ent(p: str) -> str:
        return f"02/10/2026\n{p} | Medical Diagnoses\n\nMedical Diagnoses\nSeen in follow up. (Exhibit 2 - p. 1)"

    got, log = P.snap([ent("Daniel Pederson, M.D.")], [ent("Daniel Peterson, M.D.")])
    assert B.provider_of(got[0]) == "Daniel Pederson, M.D."
    assert log[0]["likely_same_person"] == ["Daniel Peterson, M.D."] and log[0]["action"] == "flagged"


# ---- fixes 7 and 11: the added history's date is authored, and a bad reply refuses --
def _summary_sr(tmp: Path, reply: str, created: str | None) -> tuple[SimpleNamespace, list[str]]:
    ud = tmp / "runs" / "alpha" / "update"
    ud.mkdir(parents=True)
    (ud / "base.md").write_text(K.base_md())
    (ud / "update_plan.json").write_text(json.dumps({"exhibits": []}))
    (ud / "new_exhibits.final.json").write_text(json.dumps([{"exhibit": 2, "id": "n1", "title": "Exhibit 2 - X"}]))
    pre = "11/03/2025\nExample Imaging | Medical Diagnoses\n\nMedical Diagnoses\nRadiographs showed straightening. (Exhibit 2 - p. 1)"
    (ud / "new_entries.md").write_text(pre + "\n")
    logs: list[str] = []
    sr = SimpleNamespace(
        slug_dir=tmp,
        unit=SimpleNamespace(unit="alpha"),
        job=SimpleNamespace(incident_date=K.INCIDENT),
        cfg=K.cfg(),
        doorway=SimpleNamespace(call=lambda *a, **k: SimpleNamespace(text=reply)),
        log=logs.append,
        manifest=lambda: [{"id": "n1", "created": created}, {"id": "zz", "created": "2026-12-31T00:00:00Z"}],
    )
    return sr, logs


def test_the_added_history_is_dated_by_the_filing_date_never_the_run_date(tmp_path: Path) -> None:
    from medchron.stages import update_collect as UC
    from medchron.update.assemble import ADDED_HISTORY

    reply = "Prior Medical History\nRadiographs before the incident showed straightening. (Exhibit 2 - p. 1)"
    sr, _ = _summary_sr(tmp_path / "a", reply, "2026-10-01T15:04:00Z")
    assert UC.run_summary(sr) == 0  # type: ignore[arg-type]
    pmh = (tmp_path / "a" / "runs" / "alpha" / "update" / "pmh.md").read_text()
    assert pmh.startswith("Additional prior history from records filed through 10-01-26\n")
    sr, _ = _summary_sr(tmp_path / "b", reply, None)
    assert UC.run_summary(sr) == 0  # type: ignore[arg-type]
    pmh = (tmp_path / "b" / "runs" / "alpha" / "update" / "pmh.md").read_text()
    assert pmh.startswith("Additional prior history from records filed since the prior chronology\n")
    assert ADDED_HISTORY.search(pmh)


def test_a_heading_only_summary_reply_is_refused_cleanly(tmp_path: Path) -> None:
    from medchron.stages import update_collect as UC

    sr, logs = _summary_sr(tmp_path, "Prior Medical History", "2026-10-01")
    assert UC.run_summary(sr) == 1  # type: ignore[arg-type]
    assert any("carries a heading and no text" in m for m in logs)


# ---- fix 8: an OCR swap needs the entry carrying the code to name the diagnosis --
def test_a_swap_needs_more_than_one_short_shared_word(tmp_path: Path) -> None:
    from medchron import icd_tables

    icd = tmp_path / "controls" / "icd"
    icd.mkdir(parents=True)
    line = "{:<5} {:<7} {} {:<60} {}\n"
    (icd / "icd10cm_order.txt").write_text(
        line.format("00001", "Z50", "1", "Rehab care", "Care involving use of rehabilitation procedures")
    )
    (icd / "CMS32_DESC_LONG_DX.txt").write_text("2500 Diabetes mellitus without mention of complication\n")
    t = icd_tables.load(tmp_path)
    assert icd_tables.resolve("250", t, "2016-05-01", "Routine medical care visit.")[1] is None
    assert icd_tables.resolve("250", t, "2016-05-01", "Referred to rehabilitation after the visit.")[0] == "Z50"


def test_the_swap_reads_only_the_entry_that_carries_the_code() -> None:
    from medchron.update import icd

    carrying = "02/10/2016\nExample Clinic | Medical Diagnoses\n\nMedical Diagnoses\nRoutine visit. (Exhibit 2 - p. 1)"
    other = "02/10/2016\nExample Rehab | Treatments\n\nTreatments\nRehabilitation session. (Exhibit 3 - p. 1)"
    seen: list[str] = []

    def resolve(code: str, on: str, text: str) -> tuple[str, str | None, str]:
        seen.append(text)
        return code, None, "x"

    icd.claims(["2016-02-10 | Example Clinic | 250 | x.pdf"], [carrying, other], [], resolve)
    assert seen == [carrying]


# ---- fix 9: what is left once the NTD spans are removed decides ---------------------
def test_a_claim_with_a_trailing_note_is_audited_and_a_multiline_note_is_not() -> None:
    from medchron.audit import claims as CL

    body = (
        "09/10/2025\nExample Chiropractic | Treatments\n\nTreatments\n"
        "The visit ledger records a cervical adjustment on this date.\n"
        "[NTD: the second page is faint.] (Exhibit 8 - p. 12)\n\n"
        "[NTD: A portion of the records\nwere handwritten and illegible.] (Exhibit 8 - p. 13)\n"
    )
    got = [c["claim"] for c in CL.extract_claims(body, {8})]
    assert len(got) == 1 and "cervical adjustment" in got[0]


# ---- fix 12: an image page inside a text-layer PDF goes to vision ---------------------
def _mixed_pdf(path: Path) -> Path:
    import pymupdf

    doc = pymupdf.open()
    typed = "Example Clinic progress note. The patient reports neck pain and stiffness after the collision. " * 4
    for _ in range(3):
        doc.new_page().insert_textbox(pymupdf.Rect(72, 72, 540, 720), typed, fontsize=10)
    page = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 60), 0)
    pix.clear_with(200)
    page.insert_image(pymupdf.Rect(72, 72, 400, 400), pixmap=pix)
    page.insert_text((72, 760), "Scanned page 4 of 4 rcvd", fontsize=8)  # a 27-character text layer
    doc.save(str(path))
    return path


def test_an_image_page_inside_a_text_layer_pdf_is_queued_for_vision_and_projected(tmp_path: Path) -> None:
    from medchron import budget
    from medchron.stages import extract

    (tmp_path / "text").mkdir()
    pdf = _mixed_pdf(tmp_path / "mixed.pdf")
    scans: list[dict] = []
    rec = extract._extract_one(
        tmp_path, {"id": "m1", "name": "mixed", "folder": "/MEDICAL", "ext": ".pdf", "path": str(pdf)}, scans
    )
    assert rec.get("vision_pages") == [4] and not rec.get("scan")
    assert scans and scans[0]["id"] == "m1"
    (tmp_path / "extracted.jsonl").write_text(json.dumps(rec) + "\n")
    assert budget.scanned_pages(tmp_path / "extracted.jsonl") == 1


def test_the_vision_pass_merges_an_image_page_into_the_document_text() -> None:
    from medchron.stages.transcript import merge_pages, split_pages

    native = "[p.1]\nTyped one\n[p.2]\nTyped two\n[p.3]\n\n"
    out = merge_pages(native, {3: "Handwritten visit note"}, [3])
    got = split_pages(out)
    assert got[1] == ("", "Typed one\n") and got[3][0] == " (machine transcription)"
    assert "Handwritten visit note" in got[3][1]


def test_the_vision_stage_reads_only_the_image_pages_of_a_text_layer_pdf(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    from medchron.stages import vision
    from medchron_testkit import make_pdf
    from test_paid_stages import Scripted, _msg, _seed_scan, _sr

    client = Scripted(lambda p: _msg("HANDWRITING: no\nImage page text read by vision."))
    sr = _sr(job_dir, firm_config_path, data_root, client)
    _seed_scan(sr, {"m1": ("mixed.pdf", make_pdf(["typed one", "", "typed three"]))})
    q = json.loads((sr.slug_dir / "scan_queue.json").read_text())
    q[0].pop("scan")
    q[0]["vision_pages"] = [2]
    (sr.slug_dir / "scan_queue.json").write_text(json.dumps(q))
    (sr.slug_dir / "text").mkdir(parents=True, exist_ok=True)
    (sr.slug_dir / "text" / "m1.txt").write_text("[p.1]\nTyped one\n[p.2]\n\n[p.3]\nTyped three\n")
    assert vision.run(sr) == 0
    assert len(client.calls) == 1  # page 2 only
    text = (sr.slug_dir / "text" / "m1.txt").read_text()
    assert "Typed one" in text and "Typed three" in text and "[p.2] (machine transcription)\nImage page text" in text


# ---- fix 12b: a garbled text layer goes to vision, a clean one does not -----------
CLEAN = (
    "Example Clinic progress note. Patient seen for follow up of neck pain after the collision. "
    "Blood pressure 128/82, pulse 76. Assessment cervical strain, improving. Plan physical therapy twice weekly "
    "and recheck in four weeks. Diagnosis codes S13.4XXA and M54.2 were recorded."
)
GARBLED = (
    "Exarnple Cl1nic pr0gress n0te. Pat1ent seen f0r f0llow up 0f neck pa1n. DOB 0l/2S/l98O. "
    "Bl00d pressure l28/82, pu1se 76. Assessrnent cerv1cal stra1n. Dx 294.0 and 110 recorded. "
    "Plan phys1cal therapy tw1ce weekly and recheck 1n f0ur weeks wlth thc pt."
)


def test_a_garbled_text_layer_page_goes_to_vision_and_a_clean_one_does_not(tmp_path: Path) -> None:
    import pymupdf
    from medchron.stages import extract

    assert extract.garbled(GARBLED) and not extract.garbled(CLEAN)
    doc = pymupdf.open()
    for text in (CLEAN, GARBLED):
        page = doc.new_page()
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), 0)
        pix.clear_with(220)
        page.insert_image(pymupdf.Rect(72, 400, 300, 700), pixmap=pix)  # a scan with an OCR layer
        page.insert_textbox(pymupdf.Rect(72, 72, 540, 380), text, fontsize=10)
    pdf = tmp_path / "ocr.pdf"
    doc.save(str(pdf))
    assert extract.vision_pages(pdf, extract.pdf_text(pdf)) == [2]


def _ocr_page(dob: str, code_line: str, visit: str) -> str:
    """A page in the measured profile of a real garbled OCR layer: a header
    with a date of birth, a dated visit, a lab line with decimals, and a
    problem list whose lines open with a code."""
    return (
        f"Example Medical Group\nPatient: Alpha Example DOB: {dob}, Age 45\nVisit date {visit}\n"
        "Hemoglobin 13.1 range 12.0 - 15.5 g/dL drawn at the visit.\n"
        "Problem list reviewed with the patient at today's visit and updated in the record.\n"
        f"{code_line}\n"
        "Continue the current plan and return for the next scheduled visit in three months for review.\n"
    )


def test_a_real_garble_profile_goes_to_vision_and_clean_pages_do_not() -> None:
    """Calibrated on a real 111-page OCR layer (2026-10-14) that every word-shape
    test scored clean: a DOB whose separators were lost, a date with an
    impossible year, or an ICD-9-shaped code opening a problem line on a page
    dated after ICD-10 began."""
    from medchron.stages import extract

    clean = _ocr_page("12/01/1980", "Z94.0 Kidney transplant status", "03/02/2026")
    assert not extract.garbled(clean)
    assert extract.garbled(_ocr_page("1201/1980", "Z94.0 Kidney transplant status", "03/02/2026"))
    assert extract.garbled(_ocr_page("12/01!1980", "Z94.0 Kidney transplant status", "03/02/2026"))
    assert extract.garbled(_ocr_page("12/01/1980", "(294.0) Kidney transplant status", "03/02/2026"))
    assert extract.garbled(_ocr_page("12/01/1980", "Z94.0 Kidney transplant status", "03/02/2074"))
    # An ICD-9 code on a record from the ICD-9 years is the record, not a misread.
    assert not extract.garbled(_ocr_page("12/01/1980", "724.2 Lumbago", "05/01/2014"))


# ---- fix 13: an identity hold survives the vision read, and a name is a name ---------
def _identity_sr(tmp: Path, native: str, vision_text: str) -> SimpleNamespace:
    (tmp / "text").mkdir(parents=True)
    (tmp / "partial").mkdir()
    (tmp / "text" / "n1.txt").write_text("[p.1]\n" + native + "\n[p.2]\nFollow up visit, no change.\n")
    (tmp / "partial" / "n1.jsonl").write_text(json.dumps({"page": 1, "text": vision_text}) + "\n")
    unit = SimpleNamespace(surname="Example", dob="05/15/1983")
    return SimpleNamespace(slug_dir=tmp, job=SimpleNamespace(units=[unit]))


def test_a_garbled_dob_does_not_hold_once_vision_reads_the_page_and_another_persons_still_does(
    tmp_path: Path,
) -> None:
    from medchron.stages import update_select as US

    garbled = _identity_sr(
        tmp_path / "a", "Patient Name: Alpha Example\nDOB: 05/15/1988", "Patient Name: Alpha Example\nDOB: 05/15/1983"
    )
    assert US._identity_holds(garbled, [{"id": "n1"}]) == []  # type: ignore[arg-type]
    other = _identity_sr(
        tmp_path / "b", "Patient Name: Alpha Example\nDOB: 05/05/1990", "Patient Name: Alpha Example\nDOB: 05/05/1990"
    )
    assert len(US._identity_holds(other, [{"id": "n1"}])) == 1  # type: ignore[arg-type]


def test_capitalised_words_after_patient_are_not_a_name() -> None:
    from medchron.gates import foreign_identity as FI

    assert FI.findings("Patient: Denies Neck Pain since the visit", {"Example"}, set()) == []
    assert FI.findings("Patient Name: Gamma Other", {"Example"}, set()) == [
        "a patient name without the client's surname"
    ]


# ---- fix 14: the handwriting field is required, and the audit re-reads values -------
def test_the_vision_reply_carries_a_required_handwriting_field(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    from medchron.stages import vision
    from medchron.stages.transcript import split_pages
    from medchron_testkit import make_pdf
    from test_paid_stages import Scripted, _msg, _seed_scan, _sr

    replies = iter(
        ["HANDWRITING: yes\nFlowsheet BP 142/102 pulse 88", "HANDWRITING: no\nTyped letter", "Typed, no field"]
    )
    client = Scripted(lambda p: _msg(next(replies)))
    sr = _sr(job_dir, firm_config_path, data_root, client)
    _seed_scan(sr, {"h1": ("flowsheet.pdf", make_pdf(["", "", ""]))})
    assert vision.run(sr) == 0
    pages = {n: t for n, (_, t) in split_pages((sr.slug_dir / "text" / "h1.txt").read_text()).items()}
    assert sum(t.startswith("[handwritten]\n") for t in pages.values()) == 2  # "yes" and the missing field
    assert not any("HANDWRITING:" in t for t in pages.values())


def test_the_audit_of_a_handwritten_page_rereads_its_values_even_with_no_statement() -> None:
    from medchron.audit.handwritten_evidence import Evidence
    from medchron.update import handwritten as H

    ev = Evidence.__new__(Evidence)
    ev.body = "02/18/2026\nExample Clinic | Vital Signs\n\nVital Signs\nBlood pressure 142/102 recorded."
    ev._pool, ev.index = [], None
    ev.page = lambda ex, p: "[handwritten]\nBP 142/102"  # type: ignore[method-assign]
    got = ev.block({"exhibit": 2, "pages": [1], "claim": "Blood pressure 142/102 recorded."})
    assert got.startswith(H.REREAD) and "vital sign" in got


# ---- fix 13b: the client's own first name on a billing label is the client --------
def test_a_label_with_the_clients_first_name_does_not_hold_and_another_person_still_does() -> None:
    from medchron.gates import foreign_identity as FI

    billing = (
        "Example Billing Summary\nPatient: Alpha Self Pay\nAccount for Alpha Example\n"
        "Guarantor statement for Alpha Example, DOB: 05/15/1983\n"
    )
    assert FI.findings(billing, {"Example"}, {"05/15/1983"}, {"Alpha"}) == []
    other = "Patient: Gamma Other\nDOB: 05/05/1990\nAccount for Alpha Example\n"
    got = FI.findings(other, {"Example"}, {"05/15/1983"}, {"Alpha"})
    assert "a patient name without the client's surname" in got
    assert "a date of birth 05/05/1990 that is not the client's" in got
    units = [SimpleNamespace(client_name="Alpha Example", surname="Example", dob="05/15/1983")]
    assert FI.first_names(units) == {"Alpha"}


# ---- fix 13c: a form question is not a patient label -------------------------------
def test_form_questions_about_the_patient_never_hold_and_another_person_still_does() -> None:
    from medchron.gates import foreign_identity as FI

    client = ({"Example"}, {"05/15/1983"}, {"Alpha"})
    for question in (
        "Does the patient: Yes No have a ride to the appointment",
        "Is the patient: Yes No able to bear weight",
        "Transportation was arranged for the patient:Yes IHSS",
        "Patient: Yes No",
    ):
        assert FI.findings(question + "\nAlpha Example seen today.", *client) == [], question
    for other in ("Patient: Jane Roe\nDOB: 05/05/1990", "Patient Name: Roe, Jane\nDOB: 05/05/1990"):
        got = FI.findings(other, *client)
        assert "a patient name without the client's surname" in got, other
        assert "a date of birth 05/05/1990 that is not the client's" in got, other
    assert FI.findings("Name: Alpha Example  Patient: Jane Roe", *client)  # a label after a field gap
