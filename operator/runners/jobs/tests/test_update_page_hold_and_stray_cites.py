"""An update reads each repeated page once and carries no stray citation
lines (2026-11-17). A firm filed two overlapping exports of one clinic's
records whose pages differed only in print stamps and pagination, so the
shared pages were composed twice, and the update left 64 lines that were
only a citation. Each test here fails on the code before the fix unless it
says it pins behaviour that must not change. Every name and value is
invented."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import update_testkit as K
from jobs.medchron.update import (
    body as B,
    entry_edits as EE,
    measure_repeats as M,
    pageid_io as PG,
    proofs as P,
    select as S,
    uncited as U,
)
from jobs.medchron.update.values import numbers

# ---- 1. print and export stamps --------------------------------------------------------------

NOTE = (
    "Example Clinic Orthopedics\nPatient: Alpha Example  DOB: 01/02/1980\n"
    "Date of service: 03/04/2026\nChief complaint: neck pain after the collision, rated 6 of 10.\n"
    "Examination revealed paraspinal tenderness and reduced rotation to the left. Blood pressure 128/82. "
    "Assessment: cervical strain. Plan: physical therapy twice weekly and ibuprofen 600 mg as needed. "
    "Follow up in the clinic in four weeks for reassessment of the neck and the work status."
)


def _stamped(stamp: str, note: str = NOTE) -> str:
    return f"{stamp}\n{note}\nPage 1 of 3"


@pytest.mark.parametrize(
    "a,b",
    [
        ("Run Date: 11/02/2026 3:14 PM", "Run Date: 11/09/2026 8:01 AM"),
        ("Report Date/Time: 11/02/2026 15:14", "Report Date/Time: 11/09/2026 08:01"),
        ("Exported 11/02/2026 by records staff", "Exported 11/09/2026 by records staff"),
        ("Requested by: Records Desk 11/02/2026", "Requested by: Records Desk 11/09/2026"),
        ("Generated on 11/02/2026", "Generated on 11/09/2026"),
        ("11/02/2026 3:14 PM", "11/09/2026 8:01 AM"),  # a bare date-time header
    ],
)
def test_a_page_differing_only_in_its_export_stamp_is_the_same_page(a: str, b: str) -> None:
    assert PG.confirmed(_stamped(a), _stamped(b))


def test_a_real_clinical_date_difference_is_never_a_stamp() -> None:
    other = NOTE.replace("Date of service: 03/04/2026", "Date of service: 03/11/2026")
    assert not PG.confirmed(_stamped("Run Date: 11/02/2026"), _stamped("Run Date: 11/02/2026", other))
    # a bare date-time line in the BODY is when something happened, not a print header
    mid_a = NOTE.replace("Blood pressure", "03/04/2026 10:15\nBlood pressure")
    mid_b = NOTE.replace("Blood pressure", "03/05/2026 10:15\nBlood pressure")
    assert not PG.confirmed(_stamped("Exported 11/02/2026", mid_a), _stamped("Exported 11/09/2026", mid_b))
    # a clinical "report" line is not a stamp
    assert S.strip_stamps("Report: MRI of the lumbar spine 03/04/2026") == "Report: MRI of the lumbar spine 03/04/2026"


# ---- 2. page identity across a pagination shift ---------------------------------------------

VISIT_1 = (
    "Example Clinic Orthopedics\nDate of service: 03/04/2026\nProvider: Roe, Jane (M.D.)\n"
    "Chief complaint: neck pain after the collision, rated 6 of 10 at the visit today.\n"
    "Examination revealed paraspinal tenderness and reduced rotation to the left side of the neck.\n"
    "Blood pressure 128/82. Pulse 74. Assessment: cervical strain with muscle spasm.\n"
    "Plan: physical therapy twice weekly and ibuprofen 600 mg three times daily as needed for pain."
)
VISIT_2 = (
    "Example Clinic Orthopedics\nDate of service: 04/01/2026\nProvider: Roe, Jane (M.D.)\n"
    "Chief complaint: neck pain improving with therapy, rated 3 of 10 at the visit today.\n"
    "Examination revealed mild paraspinal tenderness with improved rotation to the left side.\n"
    "Blood pressure 122/78. Pulse 70. Assessment: cervical strain, improving with treatment.\n"
    "Plan: continue physical therapy once weekly and stop ibuprofen when the pain resolves fully."
)


def _halves(t: str) -> tuple[str, str]:
    lines = t.split("\n")
    return "\n".join(lines[:4]), "\n".join(lines[4:])


def _export_a() -> list[str]:
    """The first export: one visit to a page, with its run stamp."""
    return [
        f"Run Date: 11/02/2026 3:14 PM\n{VISIT_1}\nPage 1 of 2",
        f"Run Date: 11/02/2026 3:14 PM\n{VISIT_2}\nPage 2 of 2",
    ]


def _export_b(visit_1: str = VISIT_1) -> list[str]:
    """The second export of the same records: other stamps, and pages that
    break mid-visit (the tail of one visit and the head of the next)."""
    a1, a2 = _halves(visit_1)
    b1, b2 = _halves(VISIT_2)
    stamp = "Exported 11/09/2026 by records staff"
    return [f"{stamp}\n{a1}\nPage 1 of 3", f"{stamp}\n{a2}\n{b1}\nPage 2 of 3", f"{stamp}\n{b2}\nPage 3 of 3"]


def _decide(pages: list[str], seen: S.Delivered, earlier: S.Earlier | None = None) -> dict:
    return S.classify_new(dict(enumerate(pages, 1)), len(pages), seen, earlier)


def test_a_second_export_of_delivered_pages_paginated_otherwise_is_already_read() -> None:
    seen = S.Delivered.of_docs([_export_a()])
    got = _decide(_export_b(), seen)
    assert {p: d for p, (d, _) in got.items()} == {1: S.EXCLUDED, 2: S.EXCLUDED, 3: S.EXCLUDED}
    assert {why for _, why in got.values()} == {S.ALREADY}


def test_a_second_export_overlapping_an_earlier_new_one_is_a_repeat_not_read_twice() -> None:
    earlier = S.Earlier()
    first = _decide(_export_a(), S.Delivered(), earlier)
    assert all(d == S.READ for d, _ in first.values())
    second = _decide(_export_b(), S.Delivered(), earlier)
    assert all(d == S.EXCLUDED and why == S.OVERLAP for d, why in second.values())


@pytest.mark.parametrize(
    "change",
    [
        ("600 mg", "800 mg"),  # a dose
        ("Pulse 74", "Pulse 94"),  # a vital
        ("Examination revealed", "Examination revealed no"),  # a negation
        ("cervical strain with muscle spasm", "cervical strain with radiculopathy"),  # a finding
    ],
)
def test_a_page_that_says_one_other_thing_is_read(change: tuple[str, str]) -> None:
    seen = S.Delivered.of_docs([_export_a()])
    changed = VISIT_1.replace(*change)
    assert changed != VISIT_1
    got = _decide(_export_b(changed), seen)
    assert got[1][0] == S.READ or got[2][0] == S.READ


def test_the_text_window_never_holds_a_page_of_another_visit_date() -> None:
    seen = S.Delivered.of_docs([_export_a()])
    page = _export_b()[1]
    assert seen.contained(page) is not None
    assert seen.contained(page.replace("04/01/2026", "04/02/2026")) is None


def test_pages_of_one_document_are_never_held_by_each_other() -> None:
    earlier = S.Earlier()
    pages = _export_a() + _export_b()
    assert all(d == S.READ for d, _ in _decide(pages, S.Delivered(), earlier).values())


MRI = (
    "Example Imaging Center\nMRI RIGHT SHOULDER WITHOUT CONTRAST\nDate of exam: 05/12/2026\n"
    "History: shoulder pain after the collision.\nTechnique: multiplanar multisequence images were obtained.\n"
    "Findings: There is moderate tendinosis of the supraspinatus tendon. The labrum is intact.\n"
    "Mild acromioclavicular joint arthrosis with a small subacromial effusion.\n"
    "No full thickness tear of the infraspinatus is seen; high grade partial thickness wear of its gross\n"
    "scattered bursal surface.\n"
    "The subscapularis tendon is intact without tendinosis. The biceps tendon is located in the bicipital groove\n"
    "and is normal in caliber. The glenohumeral joint cartilage is preserved without a focal chondral defect.\n"
    "There is no fracture or bone contusion. The deltoid and rotator cuff muscles are normal in bulk and signal\n"
    "without atrophy or fatty infiltration. The coracoacromial ligament is intact and the acromion is type two.\n"
    "The axillary pouch and rotator interval are unremarkable. There is no paralabral cyst in the spinoglenoid\n"
    "notch. The visualized portions of the scapula, clavicle and humeral head marrow are normal in signal.\n"
    "The subcutaneous soft tissues are unremarkable. No mass or abnormal fluid collection is identified.\n"
    "Impression: supraspinatus tendinosis and mild acromioclavicular arthrosis."
)
NEGATIVE = "No gross full thickness tear is seen. Scattered foci of high grade partial thickness wear."


def _mri_window() -> S.Delivered:
    """The delivered export: the report broken across two pages."""
    lines = MRI.split("\n")
    return S.Delivered.of_docs([["\n".join(lines[:5]) + "\nPage 1 of 2", "\n".join(lines[5:]) + "\nPage 2 of 2"]])


def test_a_page_identical_but_for_its_print_stamp_is_held() -> None:
    got = _decide([f"Printed 11/09/2026 4:02 PM by records\n{MRI}\nPage 1 of 1"], _mri_window())
    assert got == {1: (S.EXCLUDED, S.ALREADY)}


def test_a_page_identical_but_for_one_added_negative_finding_is_read() -> None:
    """The real shape (2026-11-17): every word of the added sentence but one
    ("foci") is elsewhere in the window, so the old allowance of missing
    words held the page. Now no token may be missing."""
    page = MRI.replace("The labrum is intact.", f"The labrum is intact. {NEGATIVE}")
    assert _decide([f"Printed 11/09/2026 4:02 PM by records\n{page}"], _mri_window())[1][0] == S.READ
    one_word = MRI.replace("moderate tendinosis", "moderate diffuse tendinosis")  # one plain word more
    assert _decide([one_word], _mri_window())[1][0] == S.READ


def test_two_deliveries_joined_keep_each_exhibit_its_own_document() -> None:
    both = S.Delivered.of_docs([_export_a()[:1]]) | S.Delivered.of_docs([_export_a()[1:]])
    assert both.contained(_export_b()[1]) is None  # its window would span two exhibits
    assert S.Delivered.of_docs([_export_a()]).contained(_export_b()[1]) is not None


SIGNED = (
    "Example Clinic Orthopedics\nDate of service: 03/04/2026\nFollow up visit for neck pain after the collision.\n"
    "Examination revealed paraspinal tenderness with improved rotation to the left side of the neck.\n"
    "Plan: continue physical therapy once weekly and return to the clinic in four weeks.\n"
    "Electronically signed on 03/04/2026 by Jane Roe, M.D."
)
OTHER_NOTE = (
    "Example Clinic Orthopedics\nTelephone note dated 03/11/2026: the patient called to confirm the therapy schedule.\n"
    "The schedule was confirmed and the patient was told to call with any questions."
)


def test_a_page_whose_tokens_are_all_in_the_window_but_not_its_sentence_is_read() -> None:
    """(a) The real shape: the window says "signed on <date B>" on one page
    and has date A in another note on the next; the page says "signed on
    <date A>". Every token and the date are in the window; the sentence is not."""
    seen = S.Delivered.of_docs([[SIGNED, OTHER_NOTE]])
    page = SIGNED.replace("signed on 03/04/2026", "signed on 03/11/2026")
    assert seen.contained(page) is None  # the text-window rule; the shingle rule is main's, unchanged
    assert seen.contained(SIGNED) is not None


def test_a_negative_finding_whose_words_are_scattered_in_the_window_is_read() -> None:
    """(b) "No" and each word of the finding occur somewhere in the window,
    never together; the page states the finding."""
    elsewhere = (
        "Rotator interval reviewed. No swelling of the hand. Cuff and atrophy were both discussed with the patient."
    )
    lines = MRI.split("\n")
    seen = S.Delivered.of_docs([["\n".join(lines[:5]), "\n".join(lines[5:]), elsewhere]])
    page = MRI.replace("The labrum is intact.", "The labrum is intact. No rotator cuff atrophy.")
    assert _decide([page], seen)[1][0] == S.READ


def test_a_page_differing_only_by_its_print_stamp_and_line_wraps_is_held() -> None:
    """(c) The same words in the same order, broken into other lines, under another stamp."""
    words = MRI.split()
    rewrapped = "\n".join(" ".join(words[i : i + 9]) for i in range(0, len(words), 9))
    assert rewrapped.split() == MRI.split() and rewrapped != MRI
    got = _decide([f"Report Date/Time: 11/09/2026 08:01\n{rewrapped}\nPage 1 of 1"], _mri_window())
    assert got == {1: (S.EXCLUDED, S.ALREADY)}


def test_a_stamp_inside_a_page_breaks_its_runs() -> None:
    """A stamp line inside the text is no page break: the tokens on its two
    sides were never side by side, so no run joins them."""
    from jobs.medchron.update import contained as CT

    stream = CT.stream_of("one two three\nPrinted on 11/09/2026\nfour five six")
    assert stream == ("one", "two", "three", None, "four", "five", "six")
    assert CT.runs_of(stream) == []
    assert CT.stream_of("Run Date: 11/02/2026\none two\nPage 1 of 2") == ("one", "two")


def test_an_undated_page_is_held_only_beside_a_held_page() -> None:
    """The delivered export broke the visit's body across two pages; the new
    export holds its header on one page and its whole undated body on the
    next, which no single delivered page covers."""
    lines = VISIT_1.split("\n")
    head, body = "\n".join(lines[:4]), "\n".join(lines[4:])
    seen = S.Delivered.of_docs([[f"{head}\n{lines[4]}", "\n".join(lines[5:]) + "\n" + VISIT_2]])
    assert _decide([body], seen)[1][0] == S.READ  # no date, no held neighbour: read
    got = _decide([head, body], seen)
    assert got[1] == (S.EXCLUDED, S.ALREADY) and got[2] == (S.EXCLUDED, S.ALREADY)


D = "03/04/2026"


# ---- 4. lines that are only a citation ------------------------------------------------------

STRAY = (
    f"{D}\nExample Clinic | Medical Diagnoses\n\nMedical Diagnoses\n"
    "Neck pain rated 6 of 10 since the collision. (Exhibit 5 - p. 3)\n(Exhibit 5 - p. 3)\n\n(Exhibit 6 - p. 2)\n\n"
    "Cervical radiographs showed straightening of the lordosis\n(Exhibit 5 - p. 4)\n\n"
    "All Other Information\n(Exhibit 5 - p. 9)\nReturn in four weeks for a recheck of the neck. (Exhibit 5 - p. 5)"
)


def test_a_citation_only_line_joins_the_text_it_cites_and_every_exhibit_stays_cited() -> None:
    out, _ = U.cite_entry(STRAY, K.HEADINGS)
    assert not [ln for ln in out.splitlines() if U.cite_only(ln)], out
    assert B.cited(out) == B.cited(STRAY)  # Exhibit 6 is still cited
    assert "lordosis (Exhibit 5 - p. 4)" in out  # the line that closed the text joins it
    assert out.count("(Exhibit 5 - p. 3)") == 1  # an exact repeat goes
    assert numbers(out) == numbers(STRAY)  # a citation carries no value: none is lost


def test_joining_after_the_audit_changes_no_claim() -> None:
    from jobs.medchron.audit import claims as CL

    joined = U.join_stray(STRAY, K.HEADINGS, audited=True)
    keys = {c["key"] for c in CL.extract_claims(STRAY, {5, 6})}
    assert keys and keys == {c["key"] for c in CL.extract_claims(joined, {5, 6})}
    left = [ln for ln in joined.splitlines() if U.cite_only(ln)]
    assert left == ["(Exhibit 5 - p. 9)"]  # under a heading, it keeps the heading out of an audited claim
    assert not [ln for ln in U.join_stray(STRAY, K.HEADINGS).splitlines() if U.cite_only(ln)]  # before the audit


def test_proof_6_fails_on_an_added_line_that_is_only_a_citation() -> None:
    from test_update_text import NEW_POST, NEW_PRE, _assemble, _inputs

    md, _ = _assemble([NEW_PRE, NEW_POST])
    assert "6" not in P.run(_inputs(md, [NEW_PRE, NEW_POST])).fails
    stray = md.replace("fifth and sixth level.", "fifth and sixth level.\n(Exhibit 2 - p. 3)", 1)
    assert stray != md
    assert "6" in P.run(_inputs(stray, [NEW_PRE, NEW_POST])).fails


# ---- 5. one "Additional findings" heading per visit -----------------------------------------


def test_two_folds_under_one_delivered_visit_read_one_heading() -> None:
    base = K.ENTRY_POST
    one = EE.fold_text(base, "Hemoglobin 13.1 g/dL. (Exhibit 3 - p. 1)", K.HEADINGS, today="11/17/2026")
    two = EE.fold_text(base, "Ibuprofen 600 mg prescribed. (Exhibit 3 - p. 2)", K.HEADINGS, today="11/17/2026")
    (merged,), rest = EE.fold_into_delivered([base], [one, two, one])
    assert rest == [] and merged.count(f"{EE.FOLD} 11/17/2026") == 1
    assert "13.1" in merged and "600 mg" in merged and merged.count("13.1") == 1


# ---- the $0 measuring command ---------------------------------------------------------------


def _job(tmp_path: Path) -> Path:
    import pymupdf

    job = tmp_path / "matter"
    ud = job / "runs" / "alpha" / "update"
    ud.mkdir(parents=True)
    (job / "delivered").mkdir()
    doc = pymupdf.open()
    for t in _export_a():
        doc.new_page().insert_textbox(pymupdf.Rect(36, 36, 576, 756), t, fontsize=9)
    doc.save(str(job / "delivered" / "Exhibit 1 - Example.pdf"))
    (ud / "base.md").write_text(K.base_md())
    (ud / "update_plan.json").write_text(json.dumps({"exhibits": [{"exhibit": 1, "name": "Exhibit 1 - Example.pdf"}]}))
    (ud / "new_exhibits.json").write_text(json.dumps([{"id": "d2", "pages": 3, "exhibit": 2}]))
    (job / "text").mkdir()
    (job / "text" / "d2.txt").write_text("".join(f"[p.{i}]\n{t}\n" for i, t in enumerate(_export_b(), 1)))
    (ud / "new_entries.md").write_text(STRAY + "\n")
    return job


def test_the_measuring_command_reports_held_pages_and_stray_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    job = _job(tmp_path)
    assert M.main([str(job)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["pages"]["decisions"] == {f"{S.EXCLUDED}: {S.ALREADY}": 3}
    assert out["pages"]["held_by_text"] == {"held by a run of delivered pages": 1}  # the page broken mid-visit
    assert out["pages"]["held_by_text_tokens_absent_from_window"] == {"d2 p.2": 0}
    assert out["pages"]["held_by_text_runs_absent_from_window"] == {"d2 p.2": 0}
    assert out["stray_citations"] == {
        "entries": 1,
        "citation_only_lines_before": 4,
        "citation_only_lines_after": 0,
        "entries_whose_content_the_join_changes": [],
    }
