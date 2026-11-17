"""A chronology update shows each visit once and carries no stray lines
(2026-11-17). A firm filed two overlapping exports of one clinic's records;
the update composed the shared visits twice (19 visits as two new rows, 15
more as a repeat of a delivered row) and left 64 lines that were only a
citation. Each test here fails on the code before the fix unless it says it
pins behaviour that must not change. Every name and value is invented."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import update_testkit as K
from jobs.medchron.update import (
    body as B,
    collect as C,
    entry_edits as EE,
    measure_repeats as M,
    pageid_io as PG,
    proofs as P,
    repeats as R,
    select as S,
    uncited as U,
)
from jobs.medchron.update.values import content_numbers, numbers

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


# ---- 3. the same visit under two headings ---------------------------------------------------

D = "03/04/2026"
ROE_A = (
    f"{D}\nRoe, Jane (M.D.) | Medical Diagnoses\n\nMedical Diagnoses\n"
    "Neck pain after the collision rated 6 of 10; examination revealed paraspinal tenderness and reduced "
    "rotation to the left. Assessment cervical strain with muscle spasm. Blood pressure 128/82. (Exhibit 2 - p. 4)\n\n"
    "All Other Information\nPlan physical therapy twice weekly and ibuprofen 600 mg as needed. (Exhibit 2 - p. 4)"
)
ROE_B = (
    f"{D}\nExample Clinic Orthopedics, Jane Roe | Medical Diagnoses\n\nMedical Diagnoses\n"
    "Seen at 10:32 for neck pain rated 6 of 10 after the collision; examination revealed paraspinal tenderness "
    "with reduced rotation to the left. Assessment cervical strain with muscle spasm. (Exhibit 3 - p. 2)\n\n"
    "All Other Information\nBlood pressure 128/82. Physical therapy twice weekly; ibuprofen 600 mg as needed. "
    "(Exhibit 3 - p. 2)"
)


def _base(*entries: str) -> list[str]:
    return B.entries_of(K.base_md((K.ENTRY_PRE, *entries, K.ENTRY_POST)))[1]


def test_two_new_copies_of_one_visit_under_two_headings_are_one_row() -> None:
    kept, rep = C.collect([ROE_A, ROE_B], _base(), K.INCIDENT, str)
    assert len(kept) == 1, kept
    assert rep[R.REWORDED_NEW] == 1


def test_a_new_copy_of_a_delivered_visit_under_another_heading_is_not_added() -> None:
    kept, rep = C.collect([ROE_B], _base(ROE_A), K.INCIDENT, str)
    assert kept == [] and rep[R.REWORDED] == 1


def test_a_copy_that_adds_a_value_keeps_it() -> None:
    extra = ROE_B.replace(
        "(Exhibit 3 - p. 2)\n\nAll", "Cervical radiographs showed no fracture. (Exhibit 3 - p. 2)\n\nAll"
    )
    extra = extra.replace("ibuprofen 600 mg", "ibuprofen 600 mg and cyclobenzaprine 10 mg")
    kept, rep = C.collect([ROE_A, extra], _base(), K.INCIDENT, str)  # the longer one holds all of the other
    assert len(kept) == 1 and rep[R.REWORDED_NEW] == 1 and "10 mg" in kept[0] and "fracture" in kept[0]
    pulse = ROE_A.replace("Blood pressure 128/82.", "Blood pressure 128/82. Pulse 74.")
    kept, rep = C.collect([pulse, extra], _base(), K.INCIDENT, str)  # each adds one: folded into one row
    assert len(kept) == 1 and rep[R.FOLDED_NEW] == 1
    assert all(v in kept[0] for v in ("10 mg", "fracture", "Pulse 74", "(Exhibit 2 - p. 4)", "(Exhibit 3 - p. 2)"))
    kept, rep = C.collect([extra], _base(ROE_A), K.INCIDENT, str)
    assert len(kept) == 1 and kept[0].split("\n")[1].endswith(R.ADDITIONAL)
    assert "10 mg" in kept[0] and "10:32" not in kept[0]  # a time of day is not care


def test_two_encounters_of_one_day_and_two_clinicians_stay_two() -> None:
    later = ROE_B.replace("10:32", "16:05")
    early = ROE_A.replace("Neck pain after", "Seen at 08:10 for neck pain after")
    assert len(C.collect([early, later], _base(), K.INCIDENT, str)[0]) == 2
    other = ROE_B.replace("Jane Roe", "Sam Poe")
    assert len(C.collect([ROE_A, other], _base(), K.INCIDENT, str)[0]) == 2


VARIANTS = [
    ("ibuprofen 600 mg", "ibuprofen 800 mg"),
    ("ibuprofen 600 mg", "ibuprofen 600 mg and cyclobenzaprine 10 mg"),  # "10" is also in "6 of 10"
    ("128/82", "142/90"),
    ("rated 6 of 10", "rated 8 of 10"),
    ("cervical strain", "cervical radiculopathy"),
    ("(Exhibit 3 - p. 2)\n\nAll", "Follow up on 03/18/2026. (Exhibit 3 - p. 2)\n\nAll"),
    ("(Exhibit 3 - p. 2)\n\nAll", "Diagnosis code M54.2 recorded. (Exhibit 3 - p. 2)\n\nAll"),
    ("(Exhibit 3 - p. 2)\n\nAll", "Impression showed foraminal stenosis. (Exhibit 3 - p. 2)\n\nAll"),
]


@pytest.mark.parametrize("change", VARIANTS)
@pytest.mark.parametrize("delivered", [False, True])
def test_the_safety_property_no_dropped_entry_takes_a_value_with_it(change: tuple[str, str], delivered: bool) -> None:
    """Whatever one value or finding the second copy adds, it is in the
    output (or the delivered text) on that date: nothing is dropped unless
    what stays carries every content value and finding of it."""
    second = ROE_B.replace(*change)
    assert second != ROE_B
    comp = [second] if delivered else [ROE_A, second]
    base = _base(ROE_A) if delivered else _base()
    kept, _ = C.collect(comp, base, K.INCIDENT, str)
    assert M.safety_violations(comp, kept, base) == []
    on_day = [e for e in kept + base if B.iso(e) == B.iso(second)]
    assert content_numbers(second) <= set().union(*(content_numbers(e) for e in on_day))
    assert R.findings(second) <= set().union(*(R.vocab(e) for e in on_day))


def test_the_safety_check_lists_a_violation_when_one_is_planted() -> None:
    dropped = ROE_B.replace("600 mg", "750 mg")
    assert M.safety_violations([ROE_A, dropped], [ROE_A], []) == [{"entry": D, "values": ["750"], "findings": []}]


def test_proof_10_excuses_a_dropped_copys_time_of_day_but_never_an_edit_of_its_own_text() -> None:
    upd = [ROE_A]
    assert P.dropped_values([ROE_B], upd, {2, 3}, set()) == []  # the copy from Exhibit 3: only 10:32 is not carried
    edited = ROE_B.replace("Seen at 10:32 for", "Seen for")
    assert P.dropped_values([ROE_B], [edited], {3}, set()) == [(D, ["10:32"])]
    worse = ROE_A.replace("600 mg", "")
    assert P.dropped_values([ROE_B], [worse], {2, 3}, set())  # a content value is never excused


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


def test_two_additional_findings_rows_of_one_visit_are_one_row() -> None:
    head = f"{D}\nRoe, Jane (M.D.) | {R.ADDITIONAL}\n\n{R.ADDITIONAL}\n"
    a, b = head + "Hemoglobin 13.1 g/dL. (Exhibit 3 - p. 1)", head + "WBC 6.1. (Exhibit 4 - p. 1)"
    out, n = R.merge_additional([a, b])
    assert n == 1 and len(out) == 1 and out[0].count(R.ADDITIONAL) == 2  # the provider line and one heading
    assert "13.1" in out[0] and "6.1" in out[0]


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
    (ud / "base.md").write_text(K.base_md((K.ENTRY_PRE, ROE_A.replace("Exhibit 2", "Exhibit 1"), K.ENTRY_POST)))
    (ud / "update_plan.json").write_text(json.dumps({"exhibits": [{"exhibit": 1, "name": "Exhibit 1 - Example.pdf"}]}))
    (ud / "new_exhibits.json").write_text(json.dumps([{"id": "d2", "pages": 3, "exhibit": 2}]))
    (job / "text").mkdir()
    (job / "text" / "d2.txt").write_text("".join(f"[p.{i}]\n{t}\n" for i, t in enumerate(_export_b(), 1)))
    rd = job / "runs" / "alpha"
    (rd / "exhibit_map.json").write_text(json.dumps({"Exhibit 2 - Example (Medical Records)": 1}))
    (rd / "entries.md").write_text(
        ROE_B.replace("Exhibit 3", "Exhibit 1")
        + "\n\n"
        + STRAY.replace("Exhibit 5", "Exhibit 1").replace("Exhibit 6", "Exhibit 1")
    )
    (ud / "new_entries.md").write_text(STRAY)
    return job


def test_the_measuring_command_reports_pages_pairs_rows_stray_lines_and_safety(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    job = _job(tmp_path)
    assert M.main([str(job), "--incident", K.INCIDENT]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["pages"]["decisions"] == {f"{S.EXCLUDED}: {S.ALREADY}": 3}
    assert out["pages"]["held_by_text"] == {"held by a run of delivered pages": 1}  # the page broken mid-visit
    e = out["entries"]
    assert e["composed"] == 2 and e["rows_remaining"] == 1 and e["new_vs_delivered"][R.REWORDED] == 1
    assert e["citation_only_lines"] == {"in_the_delivered_new_entries": 4, "after_these_rules": 0}
    assert e["safety_violations"] == []
