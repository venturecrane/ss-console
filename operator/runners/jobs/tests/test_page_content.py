"""The within-page measure, a recording instrument (2026-11-17).

WHY. A real update kept half of one visit: ten clinical items on pages it
read never reached the chronology, and the page-image review missed them
too. Measured on three real job copies, the first version of this measure did
not catch that visit (two of its pages were cited by nothing; the measured
pages read present because ANY entry citing the document counted) and its
three flags were false. So it records and never acts; these tests pin what it
can and cannot see. Every record here is invented.
"""

from __future__ import annotations

import json
from pathlib import Path

from jobs.medchron.update import page_content as PC

SHA = "c0ffee" * 10 + "abcd"
VISIT = """Progress note 03/04/2025
Chief complaint: neck pain after a collision.
Vitals: BP 132/96, pulse 88, weight 180 lbs, temp 98.6
Diagnoses:
1. M54.2 Cervicalgia
2. S13.4XXA Sprain of ligaments of cervical spine
3. M54.5 Lumbago with sciatica
Plan: physical therapy three times weekly, ibuprofen 800 mg, follow up 04/01/2025
"""
#: the entry keeps the visit's complaint and vitals, nothing of its diagnoses
HALF = (
    "03/04/2025\nExample Clinic\nNeck pain after a collision. Seen at the clinic for a visit with the treating "
    "provider. BP 132/96, pulse 88, weight 180 lbs, temp 98.6. Physical therapy three times weekly, "
    "ibuprofen 800 mg, follow up 04/01/2025. (Exhibit 9 - p. 1)"
)
DIAGNOSES = (
    " Diagnoses: M54.2 cervicalgia; S13.4XXA sprain of ligaments of the cervical spine; M54.5 lumbago with sciatica."
)
WHOLE = HALF.replace(" (Exhibit 9", DIAGNOSES + " (Exhibit 9")


def _measure(entry: str, page: str = VISIT, context: str = "") -> list[PC.PageResult]:
    return PC.measure([entry], lambda ex, p: page if (ex, p) == (9, 1) else None, {9: SHA}, context).pages


def test_a_diagnoses_block_absent_from_the_citing_text_flags_the_visit() -> None:
    (r,) = _measure(HALF)
    assert r.flagged
    assert [b.heading for b in r.dropped()] == ["diagnoses"]


def test_the_same_block_carried_by_the_entry_clears_it() -> None:
    (r,) = _measure(WHOLE)
    assert not r.flagged


def test_a_half_kept_visit_above_40_percent_word_share_still_flags() -> None:
    page_words = set(VISIT.lower().split())
    kept = page_words & set(HALF.lower().split())
    assert len(kept) / len(page_words) > 0.4  # a word share would call this visit kept
    assert _measure(HALF)[0].flagged


def test_presence_is_judged_on_the_same_page_and_the_document_column_is_kept_beside_it() -> None:
    """The measured miss: an entry citing ANOTHER page of the document carried
    the items, so the same-document column read the page as present."""
    other = "03/05/2025\nExample Clinic\nFollow-up." + DIAGNOSES + " (Exhibit 9 - p. 2)"
    res = PC.measure([HALF, other], lambda ex, p: VISIT if p == 1 else "", {9: SHA}).pages
    (diag,) = next(r for r in res if r.page == 1).dropped()
    assert diag.present == 0 and diag.present_doc == diag.items  # judged absent; the second column says present


def test_an_icd_code_a_table_broke_across_lines_is_joined_before_comparison() -> None:
    split = VISIT.replace("M54.2 Cervicalgia", "M54.\n2 Cervicalgia").replace("S13.4XXA ", "S13.4XX\nA\n")
    assert PC.join_codes("M54.\n2") == "M54.2" and PC.join_codes("M54\n.5") == "M54.5"
    assert not _measure(WHOLE, split)[0].flagged  # the joined codes are found in the entry
    diag = next(b for b in PC.blocks(split) if b.heading == "diagnoses")
    assert {i.value for i in diag.items if i.kind == PC.CODE} == {"M54.2", "S13.4XXA", "M54.5"}
    assert PC.join_codes("M54.2 Cervicalgia\nLow back") == "M54.2 Cervicalgia\nLow back"  # a whole code is left


def test_index_cover_and_blank_pages_never_flag() -> None:
    index = "INDEX OF RECORDS\nTab 1 ........ 1\nTab 2 ........ 14\nCertification of records custodian 04/04/2025"
    cover = "FAX COVER SHEET\nTo: Records\nPages: 12\nDate 05/01/2025 3:14 PM"
    for page in (index, cover, "", "   \n  "):
        (r,) = _measure("05/01/2025\nExample\nRecords received. (Exhibit 9 - p. 1)", page)
        assert not r.flagged and r.blocks == []


def test_a_block_below_the_item_floor_is_never_judged() -> None:
    page = "Office visit 03/04/2025\nPlan: ibuprofen 800 mg"
    (r,) = _measure("03/04/2025\nExample\nSeen. (Exhibit 9 - p. 1)", page)
    assert r.blocks and not r.flagged


def test_an_undated_chart_page_is_measured_against_the_whole_chronology() -> None:
    meds = "Medication list:\nibuprofen 800 mg\ngabapentin 300 mg\ncyclobenzaprine 10 mg\nlisinopril 20 mg"
    entry = "03/04/2025\nExample\nSeen for neck pain. (Exhibit 9 - p. 1)"
    assert _measure(entry, meds)[0].flagged
    delivered = "Home medications: ibuprofen 800 mg, gabapentin 300 mg, cyclobenzaprine 10 mg, lisinopril 20 mg."
    assert not _measure(entry, meds, context=delivered)[0].flagged


def test_a_read_uncited_page_beside_a_cited_range_is_counted_as_a_sibling() -> None:
    """The two missed pages were read and cited by nothing, inside a visit
    whose other pages were cited: never measured, now counted."""
    pages = {1: VISIT, 2: VISIT.replace("03/04/2025", "03/04/2025 continued"), 3: "", 4: VISIT, 9: VISIT}
    pages[5] = "FAX COVER SHEET to records, twelve pages follow, sent from the clinic records office today."

    def text(ex: int, p: int) -> str | None:
        return pages.get(p)

    entry = HALF.replace("p. 1)", "p. 1, 4)")
    m = PC.measure([entry], text, {9: SHA}, read={9: [1, 2, 3, 4, 5, 9]})
    assert [(s.exhibit, s.page) for s in m.siblings] == [(9, 2), (9, 5)]  # 3 is blank, 9 is far from 1-4
    assert PC.totals(m)["uncited_siblings"] == 2

    def cover(t: str) -> str | None:
        return "cover" if "FAX COVER" in t else None

    m2 = PC.measure([entry], text, {9: SHA}, read={9: [1, 2, 3, 4, 5, 9]}, nonrecord=cover)
    assert [s.page for s in m2.siblings] == [2]  # the firm's non-record rule classes the cover


def test_the_report_carries_kinds_and_counts_never_a_value() -> None:
    m = PC.measure([HALF], lambda ex, p: VISIT, {9: SHA})
    rep = PC.report(m, "runs/u1/final-chronology.md")
    blob = json.dumps(rep)
    assert rep["totals"]["flagged_pages"] == 1 and rep["flagged"][0]["absent_kinds"]
    for value in ("M54.2", "cervicalgia", "lumbago", "sciatica", "132/96"):
        assert value not in blob
    assert rep["pages"][0]["doc"] == SHA[: PC.SHA_PREFIX]
    assert rep["compared_against"] == "runs/u1/final-chronology.md"
    assert any("invisible" in x for x in rep["limits"]) and any("boilerplate" in x for x in rep["limits"])


def _job(tmp: Path, entry: str) -> Path:
    """A copy root, as the workspace holds it: job.yaml beside data/<slug>/."""
    root = tmp / "copy"
    job = root / "data" / "example-slug"
    ud = job / "runs" / "u1" / "update"
    ud.mkdir(parents=True)
    (root / "job.yaml").write_text("{}\n", encoding="utf-8")
    (job / "text").mkdir()
    (job / "text" / "d9.txt").write_text(f"[p.1]\n{VISIT}", encoding="utf-8")
    (ud / "new_exhibits.final.json").write_text(json.dumps([{"exhibit": 9, "id": "d9", "sha256": SHA, "read": [1]}]))
    (ud / "new_entries.md").write_text(entry + "\n", encoding="utf-8")
    return root


def test_the_cli_takes_the_copy_root_and_writes_values_only_where_asked(tmp_path: Path, capsys) -> None:
    root = _job(tmp_path, HALF)
    out, vals = tmp_path / "r.json", tmp_path / "v.json"
    assert PC.main(["measure", str(root), "--out", str(out), "--show-values", str(vals)]) == 0
    printed = capsys.readouterr().out
    rep = json.loads(printed)
    assert rep["totals"]["flagged_pages"] == 1 and rep["compared_against"] == "runs/u1/update/new_entries.md"
    assert "cervicalgia" not in printed and "cervicalgia" not in out.read_text()
    assert "cervicalgia" in vals.read_text()


def test_the_cli_on_a_kept_visit_flags_nothing(tmp_path: Path, capsys) -> None:
    assert PC.main(["measure", str(_job(tmp_path, WHOLE) / "data" / "example-slug")]) == 0
    assert json.loads(capsys.readouterr().out)["totals"]["flagged_pages"] == 0
