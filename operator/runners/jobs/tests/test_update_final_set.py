"""The update's final set (2026-11-17): one step after the update review where
repeats and stray citation lines leave the added entries, every rule
delete-only, and the proofs of what the firm was told read the rendered
document. A real 799-page update filed about 34 repeated visit rows and 64
citation-only lines. Each rule is tested with the mutation that makes it
fail. Every name, date and value here is invented."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

import update_testkit as K
from jobs.medchron import config as config_mod, dag, graded_proof as GP, job as job_mod
from jobs.medchron.audit import claims as CL
from jobs.medchron.update import (
    assemble as A,
    body as B,
    collect as C,
    encounter as E,
    final_set as FS,
    final_set_io as FIO,
    heading as HD,
    proofs_final as PF,
    repeats as R,
    uncited as U,
)
from jobs.stages import final_proof, update_final_set
from jobs.stages.base import StageRun

D = "03/04/2026"
ROE_A = (
    f"{D}\nRoe, Jane (M.D.) | Medical Diagnoses\n\nMedical Diagnoses\n"
    "Neck pain after the collision rated 6 of 10; examination revealed paraspinal tenderness and reduced "
    "rotation to the left. Assessment cervical strain with muscle spasm. Blood pressure 128/82. (Exhibit 2 - p. 4)\n\n"
    "All Other Information\nPlan physical therapy twice weekly and ibuprofen 600 mg as needed. (Exhibit 2 - p. 4)"
)
#: The same visit, from the clinic's second export, under the clinic's heading: every word of it is in ROE_A.
COPY = (
    f"{D}\nExample Clinic Orthopedics, Jane Roe | Medical Diagnoses\n\nMedical Diagnoses\n"
    "Neck pain after the collision rated 6 of 10; examination revealed paraspinal tenderness and reduced "
    "rotation to the left. (Exhibit 2 - p. 4)"
)
#: The same visit, adding a time of day and nothing else: not covered (the time is a value).
TIMED = COPY.replace("Neck pain after", "Seen at 10:32 for neck pain after")
NEW_POST = (
    "02/10/2026\nExample Imaging | Medical Diagnoses\n\nMedical Diagnoses\n"
    "MRI showed a disc protrusion measuring 3 mm at the fifth and sixth level. (Exhibit 2 - p. 2)"
)
STRAY = (
    f"{D}\nExample Clinic | Medical Diagnoses\n\nMedical Diagnoses\n"
    "Neck pain rated 6 of 10 since the collision. (Exhibit 2 - p. 3)\n(Exhibit 2 - p. 3)\n\n"
    "All Other Information\n(Exhibit 2 - p. 9)\nReturn in four weeks for a recheck of the neck. (Exhibit 2 - p. 5)"
)
NEX = [
    {"exhibit": 2, "title": "Exhibit 2 - Example Clinic (Medical Records)", "pages": 9},
    {"exhibit": 3, "title": "Exhibit 3 - Example Clinic (Medical Records)", "pages": 9},
]


def _doc(entries: list[str]) -> str:
    return C.audit_document(K.base_md(), entries, NEX)


def _supported(md: str) -> list[dict]:
    body = CL.body_of(md)
    return [
        {"key": c["key"], "kind": "real", "verdict": "SUPPORTED", "exhibit": c["exhibit"]}
        for c in CL.extract_claims(body, B.cited(body))
    ]


def _inputs(entries: list[str], composed: list[str] | None = None, **kw) -> FS.Inputs:
    md = _doc(entries)
    return FS.Inputs(
        md=md,
        base_md=K.base_md(),
        composed=list(entries) if composed is None else composed,
        headings=K.HEADINGS,
        audit_rows=_supported(md),
        **kw,
    )


def _added(o: FS.Outcome) -> list[str]:
    return B.entries_of(o.md)[1]


# ---- rule 3: repeats ------------------------------------------------------------------------


def test_a_covered_repeat_under_another_heading_is_dropped_whole() -> None:
    o = FS.run(_inputs([ROE_A, COPY]))
    assert _added(o) == [ROE_A]
    assert o.counts["repeats: safe drops (added)"] == 1 and o.counts["distinct visits merged"] == 0


def test_an_uncovered_repeat_stays_whole_and_the_pair_is_recorded() -> None:
    """The shorter copy states a value (a time of day) the longer lacks."""
    o = FS.run(_inputs([ROE_A, TIMED]))
    assert _added(o) == [ROE_A, TIMED] and o.md == _doc([ROE_A, TIMED])
    assert o.counts["repeats: kept whole, not covered (it states a value or finding the other lacks)"] == 1
    assert o.counts["pair: covered no, dates present yes, dropped no"] == 1
    assert any(op["op"] == "repeat" and not op["applied"] for op in o.ops)
    timed_keeper = ROE_A.replace("Neck pain after", "Seen at 10:32 for neck pain after")
    assert _added(FS.run(_inputs([timed_keeper, TIMED]))) == [timed_keeper]  # mutation: the keeper states it, it goes


def test_a_covered_copy_worded_otherwise_is_dropped() -> None:
    """Two exports word one visit otherwise: covered and every date written,
    it goes (no run-of-words or picture test, which dropped none on a real job)."""
    reordered = COPY.replace(
        "Neck pain after the collision rated 6 of 10; examination revealed",
        "Examination revealed, after the collision, neck pain rated 6 of 10 and",
    )
    assert R._covered(reordered, ROE_A) and not E.held(reordered, ROE_A)
    o = FS.run(_inputs([ROE_A, reordered]))
    assert _added(o) == [ROE_A] and o.counts["pair: covered yes, dates present yes, dropped yes"] == 1


RANGE_COPY = COPY.replace(
    "(Exhibit 2 - p. 4)", "Physical therapy was ordered 03/05/2026 through 04/02/2026. (Exhibit 2 - p. 4)"
)
RANGE_KEEPER = ROE_A.replace(
    "ibuprofen 600 mg as needed.",
    "ibuprofen 600 mg as needed. Physical therapy was ordered; therapy began 03/05/2026 and a recheck is set 04/02/2026.",
)


def test_a_covered_pair_with_a_date_range_the_keeper_does_not_write_stays() -> None:
    assert R._covered(RANGE_COPY, RANGE_KEEPER) and not FS.dates_present(RANGE_COPY, RANGE_KEEPER)
    o = FS.run(_inputs([RANGE_KEEPER, RANGE_COPY]))
    assert len(_added(o)) == 2
    assert o.counts["repeats: kept whole, a date or date range the other does not write"] == 1
    written = RANGE_KEEPER.replace("therapy began 03/05/2026", "therapy runs 03/05/2026 through 04/02/2026; begun")
    assert _added(FS.run(_inputs([written, RANGE_COPY]))) == [written]  # mutation: the keeper writes the range


def test_a_single_date_the_keeper_lacks_keeps_the_copy() -> None:
    dated = COPY.replace("(Exhibit 2 - p. 4)", "Seen again on March 18, 2026. (Exhibit 2 - p. 4)")
    keeper = ROE_A.replace("as needed.", "as needed. Seen again on 03/18/2026.")
    assert FS.dates_written(dated)[0] == {"2026-03-18"} and FS.dates_present(dated, keeper)
    assert not FS.dates_present(dated, ROE_A)


def test_the_second_condition_is_one_named_predicate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FS, "dates_present", lambda *_a: True)
    assert _added(FS.run(_inputs([RANGE_KEEPER, RANGE_COPY]))) == [RANGE_KEEPER]


def test_a_drop_that_would_leave_an_exhibit_uncited_is_skipped() -> None:
    only_three = COPY.replace("(Exhibit 2 - p. 4)", "(Exhibit 3 - p. 1)")
    o = FS.run(_inputs([ROE_A, only_three]))
    assert _added(o) == [ROE_A, only_three]
    assert o.counts["repeats: kept whole, an exhibit would be uncited"] == 1


def test_a_covered_copy_of_a_delivered_visit_is_dropped() -> None:
    copy = K.ENTRY_POST.replace("Exhibit 1", "Exhibit 2")
    o = FS.run(_inputs([copy, NEW_POST]))
    assert _added(o) == [NEW_POST] and o.counts["repeats: safe drops (delivered)"] == 1


def test_a_delivered_row_is_never_dropped() -> None:
    """A longer added entry covering a delivered row drops nothing: the
    delivered row is not the final set's to drop, and the added one is not
    covered by the shorter delivered row."""
    longer = K.ENTRY_POST.replace("Exhibit 1", "Exhibit 2").replace(
        "at intake.", "at intake; cyclobenzaprine 7.5 mg was prescribed."
    )
    o = FS.run(_inputs([longer]))
    assert _added(o) == [longer] and B.entries_of(o.md)[1] == [longer]
    assert sum(v for k, v in o.counts.items() if k.startswith("repeats: safe drops")) == 0
    assert B.entries_of(K.base_md())[1][1] == K.ENTRY_POST  # the delivered row is where it was
    shorter = K.ENTRY_POST.replace("Exhibit 1", "Exhibit 2")
    assert _added(FS.run(_inputs([longer, shorter, NEW_POST]))) == [longer, NEW_POST]  # mutation: added, it goes


def _main_dedupe_new(kept: list[str], canon) -> tuple[list[str], int]:
    """``repeats.dedupe_new`` exactly as main had it before the final set
    (eb2490b45), kept here as the reference collect must still equal."""
    order = sorted(range(len(kept)), key=lambda i: -len(kept[i]))
    out: list[int] = []
    dropped = 0
    for i in order:
        e = kept[i]
        twin = [
            kept[j]
            for j in out
            if B.iso(kept[j]) == B.iso(e) and canon(B.provider_of(kept[j])) == canon(B.provider_of(e))
        ]
        if any(R._covered(e, t) for t in twin):
            dropped += 1
            continue
        out.append(i)
    return [kept[i] for i in sorted(out)], dropped


def _collect_fixtures() -> list[list[str]]:
    from test_update_text import NEW_POST as T_POST, NEW_PRE as T_PRE, SAME_VISIT, SAME_VISIT_NEW_VALUE

    one = [ROE_A, COPY, TIMED, NEW_POST, STRAY, T_PRE, T_POST, SAME_VISIT, SAME_VISIT_NEW_VALUE]
    one += [K.ENTRY_PRE.replace("Exhibit 1", "Exhibit 2"), K.ENTRY_POST.replace("Exhibit 1", "Exhibit 2")]
    one += [ROE_A.replace("Roe, Jane (M.D.)", "Example Clinic Orthopedics"), COPY.replace("Jane Roe", "Sam Poe")]
    return [[a, b] for a in one for b in one if a != b] + [one]


def test_collect_is_byte_identical_to_main_over_the_update_fixtures() -> None:
    """SAFE ALONE: collect calls the shared encounter key on the canonical
    provider only, so what it keeps is what main keeps, on every pair of the
    update fixtures and on all of them at once."""
    for ents in _collect_fixtures():
        assert R.dedupe_new(ents, str) == _main_dedupe_new(ents, str)
        base = B.entries_of(K.base_md())[1]
        kept, rep = C.collect(ents, base, K.INCIDENT, str)
        assert C.collect(ents, base, K.INCIDENT, str) == (kept, rep)


def test_falsifier_the_widened_key_at_collect_would_differ_from_main(monkeypatch: pytest.MonkeyPatch) -> None:
    """The identity test above can fail: with the final set's whole key at
    collect, the visit under the clinic's heading would be dropped there."""
    real = E.same_encounter
    monkeypatch.setattr(
        E, "same_encounter", lambda a, b, canon=str, page_id=None, by_name=True: real(a, b, canon, page_id)
    )
    assert R.dedupe_new([ROE_A, COPY], str) != _main_dedupe_new([ROE_A, COPY], str)
    assert real(ROE_A, COPY) and not real(ROE_A, COPY, by_name=False)
    assert not real(ROE_A, COPY.replace("Jane Roe", "Sam Poe"))


# ---- rule 1: stray lines --------------------------------------------------------------------


def test_stray_lines_go_and_the_heading_case_carries_its_verdict() -> None:
    x = _inputs([STRAY])
    o = FS.run(x)
    (e,) = _added(o)
    assert not [ln for ln in e.splitlines() if U.cite_only(ln)], e
    assert o.counts["stray lines before"] == 2 and o.counts["stray lines after"] == 0
    (carry,) = o.carries
    old = {r["key"] for r in x.audit_rows}
    assert carry["carried_from"] in old and carry["key"] not in old and carry["mode"] == FS.CARRY
    assert carry["claim"] == "All Other Information\nReturn in four weeks for a recheck of the neck."
    assert carry["verdict"] == "SUPPORTED"
    assert FS.keys_of(o.md) - old == {carry["key"]}


def test_falsifier_a_verdict_is_never_carried_to_other_words() -> None:
    """Without an audit verdict on the claim below, the line stays."""
    x = _inputs([STRAY])
    x.audit_rows = []
    o = FS.run(x)
    assert o.carries == [] and o.counts["stray lines after"] == 1


def test_no_operation_creates_a_claim_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A join that would change a claim's words is not applied."""
    real = U.join_stray
    monkeypatch.setattr(FS.U, "join_stray", lambda e, h, audited=False: real(e, h, audited).replace("rated", "graded"))
    x = _inputs([STRAY])
    o = FS.run(x)
    assert any(op["op"] == "stray join" and not op["applied"] for op in o.ops)
    assert FS.keys_of(o.md) <= {r["key"] for r in x.audit_rows} | {c["key"] for c in o.carries}
    assert "graded" not in o.md


def test_every_fixture_keeps_or_removes_claim_keys_never_adds_one() -> None:
    for ents in ([ROE_A, COPY], [ROE_A, TIMED], [STRAY], [STRAY, NEW_POST], [NEW_POST]):
        x = _inputs(ents)
        o = FS.run(x)
        assert o.counts["new claim keys created"] == 0
        assert FS.keys_of(o.md) - FS.keys_of(x.md) == {c["key"] for c in o.carries}


# ---- rows, corrections, titles ----------------------------------------------------------------


def test_rows_name_origin_claim_verdict_and_source() -> None:
    finisher = ROE_A.replace("Roe, Jane (M.D.)", "Roe, Janet (M.D.)")
    page = "Visit 03/04/2026. Seen by Janet Roe, M.D. Imaging read 02/10/2026 at Example Imaging."
    pages = HD.Pages(text=lambda ex, p: page, transcribed=lambda ex, p: p == 4)
    o = FS.run(_inputs([finisher, NEW_POST], composed=[ROE_A, NEW_POST], pages=pages))
    by = {(r["date"], r["origin"]) for r in o.rows}
    assert by == {("2026-03-04", FS.AFTER_COMPOSE), ("2026-02-10", FS.COMPOSED)}
    assert all(r["verdict"] == "SUPPORTED" and r["mode"] == "model" for r in o.rows)
    assert {r["text_source"] for r in o.rows if r["date"] == "2026-03-04"} == {FS.TRANSCRIPT}
    assert {r["provider_norm"] for r in o.rows if r["date"] == "2026-02-10"} == {"example"}


def test_a_fold_under_a_delivered_entry_is_a_correction_on_record_only() -> None:
    from jobs.medchron.update import entry_edits as EE

    fold = EE.fold_text(
        K.ENTRY_POST, "Cyclobenzaprine 7.5 mg prescribed. (Exhibit 2 - p. 3)", K.HEADINGS, today="11/17/2026"
    )
    o = FS.run(_inputs([fold, NEW_POST]))
    (c,) = o.corrections
    assert c["entry"] == "D2" and c["added_paragraphs"] == 1 and len(c["before_sha"]) == 64
    md, _ = A.assemble(
        K.base_md(), o.md, describe=lambda *_a: None, canon=str, headings=K.HEADINGS, new_exhibits=NEX[:1]
    )
    assert c["before_sha"] not in md and "corrections" not in md.lower()


def test_an_exhibit_title_names_the_provider_of_most_of_its_cited_pages_once_the_final_set_ran() -> None:
    """Counted by entries, two one-page lab rows outvoted a three-page visit.
    Without the final set the title is main's (counted by entries)."""
    visit = NEW_POST.replace("(Exhibit 2 - p. 2)", "(Exhibit 2 - p. 2-4)")
    lab = (
        "02/11/2026\nExample Lab | Medical Diagnoses\n\nMedical Diagnoses\nHemoglobin was 13.1 g/dL. (Exhibit 2 - p. 6)"
    )
    lab2 = lab.replace("02/11", "02/12").replace("13.1", "12.9").replace("p. 6", "p. 7")
    titles, _ = A.exhibit_titles_for(NEX[:1], [visit, lab, lab2], B.provider_of, by_pages=True)
    assert titles[2].startswith("Example Imaging - 02-10-2026 - 02-12-2026")
    main, _ = A.exhibit_titles_for(NEX[:1], [visit, lab, lab2], B.provider_of)
    assert main[2].startswith("Example Lab - 02-10-2026 - 02-12-2026")


def _assemble_raw(audit_md: str, ran: bool) -> str:
    md, _ = A.assemble(
        K.base_md(),
        audit_md,
        describe=lambda *_a: None,
        canon=str,
        headings=K.HEADINGS,
        new_exhibits=NEX[:1],
        final_set_ran=ran,
    )
    return md


def test_safe_alone_assemble_joins_stray_lines_when_the_final_set_has_not_run() -> None:
    """Main's assemble joined each added entry's stray lines after the
    finisher. With no final set it still does, byte for byte: the same as
    joining first and assembling with nothing left to join."""
    raw = _doc([STRAY, NEW_POST])
    pre = _doc([U.join_stray(STRAY, K.HEADINGS, audited=True), NEW_POST])
    not_run = _assemble_raw(raw, ran=False)
    assert not_run == _assemble_raw(pre, ran=True)
    lines = [ln for ln in B.chronology_lines(not_run) if U.cite_only(ln)]
    assert lines == ["(Exhibit 2 - p. 9)"]  # main's audited join leaves the heading case, as before


def test_assemble_does_not_join_again_once_the_final_set_ran(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    real = U.join_stray
    monkeypatch.setattr(A.U, "join_stray", lambda e, h=None, audited=False: calls.append(e) or real(e, h, audited))
    out = FS.run(_inputs([STRAY, NEW_POST])).md
    calls.clear()
    once = _assemble_raw(out, ran=True)
    assert calls == []  # the stage ran: assemble leaves the entries as it set them
    assert _assemble_raw(out, ran=False) == once  # and joining again is harmless: the join is idempotent
    assert len(calls) == 2


def test_the_final_set_marker_is_the_stage_s_own_file(tmp_path: Path) -> None:
    ud = tmp_path / "runs" / "alpha" / "update"
    ud.mkdir(parents=True)
    assert not FIO.ran(ud)
    (ud / FS.ROWS).write_text("")  # rows alone are not the marker
    assert not FIO.ran(ud)
    (ud / FS.REPORT).write_text("{}")
    assert FIO.ran(ud)


# ---- the proofs of what the firm was told ---------------------------------------------------


def test_the_enforce_and_record_table_is_pinned() -> None:
    assert PF.ENFORCE == {"11"}
    assert PF.RECORD == {"12", "13", "14", "15"}
    assert not PF.ENFORCE & PF.RECORD and set(PF.CHECKS) | {"15"} == PF.ENFORCE | PF.RECORD


def _render_pair(tmp_path: Path, updated_md: str) -> tuple[list[str], list[str], list, list]:
    from jobs.medchron.update import docx_md

    dl, up = K.render(K.base_md(), tmp_path / "d.docx"), K.render(updated_md, tmp_path / "u.docx")
    lists = [[r for t in docx_md.section_tables(p, "Exhibit List") for r in t[1:]] for p in (dl, up)]
    return docx_md.entry_rows(dl), docx_md.entry_rows(up), lists[0], lists[1]


def _assembled(entries: list[str]) -> str:
    md, _ = A.assemble(
        K.base_md(), _doc(entries), describe=lambda *_a: None, canon=str, headings=K.HEADINGS, new_exhibits=NEX[:1]
    )
    return md


def _pf(tmp_path: Path, updated_md: str, **kw) -> PF.Inputs:
    d, u, dl, ul = _render_pair(tmp_path, updated_md)
    base = dict(
        delivered_rows=d,
        updated_rows=u,
        corrections=[],
        delivered_list=dl,
        updated_list=ul,
        delivered_max=1,
        new_numbers=[2],
        titles={},
        final_rows=[],
        added=[],
    )
    return PF.Inputs(**(base | kw))


def test_p11_holds_on_an_update_and_fails_when_one_character_of_a_delivered_entry_changes(tmp_path: Path) -> None:
    md = _assembled([NEW_POST])
    assert PF.run(_pf(tmp_path, md)).fails == []
    edited = md.replace("recheck of the neck", "recheck of the necks", 1)
    assert edited != md
    r = PF.run(_pf(tmp_path, edited))
    assert r.fails == ["11"]


def test_p11_lets_a_delivered_entry_grow_only_by_a_recorded_fold(tmp_path: Path) -> None:
    from jobs.medchron.update import entry_edits as EE

    fold = EE.fold_text(
        K.ENTRY_POST, "Cyclobenzaprine 7.5 mg prescribed. (Exhibit 2 - p. 3)", K.HEADINGS, today="11/17/2026"
    )
    md = _assembled([fold, NEW_POST])
    assert PF.run(_pf(tmp_path, md)).fails == ["11"]  # not on record: a changed delivered entry
    assert PF.run(_pf(tmp_path, md, corrections=[{"entry": "D2"}])).fails == []


def test_p12_records_a_renumbered_exhibit_and_never_fails_the_run(tmp_path: Path) -> None:
    md = _assembled([NEW_POST])
    renumbered = md.replace("| 1 | Example Clinic", "| 7 | Example Clinic")
    r = PF.run(
        _pf(
            tmp_path,
            renumbered,
            new_numbers=[1],
            manifest_names=["Exhibit 1 - x.pdf"],
            delivered_pdfs={"Exhibit 1 - x.pdf"},
        )
    )
    assert "12" not in r.fails
    assert r.recorded["P12_delivered_exhibit_row_changed"] == 1
    assert r.recorded["P12_new_number_not_above_delivered"] == 1 and r.recorded["P12_delivered_pdf_in_manifest"] == 1


def test_p13_records_a_covered_repeat_left_in_the_added_entries(tmp_path: Path) -> None:
    md = _assembled([NEW_POST])
    assert PF.run(_pf(tmp_path, md, added=[ROE_A, COPY])).recorded["P13_covered_repeat_left"] == 1
    assert PF.run(_pf(tmp_path, md, added=[ROE_A, TIMED])).recorded["P13_covered_repeat_left"] == 0
    only_three = COPY.replace("(Exhibit 2 - p. 4)", "(Exhibit 3 - p. 1)")
    kept = PF.run(_pf(tmp_path, md, added=[ROE_A, only_three])).recorded
    assert kept["P13_kept_for_an_exhibit"] == 1 and kept["P13_covered_repeat_left"] == 0
    # a pair a date range keeps whole is in no P13 count: covers requires every date and range
    ranged = PF.run(_pf(tmp_path, md, added=[RANGE_KEEPER, RANGE_COPY])).recorded
    assert ranged["P13_covered_repeat_left"] == ranged["P13_kept_for_an_exhibit"] == 0
    # only the shorter covers the longer: the rule keeps the longer, recorded apart
    short = COPY.replace("(Exhibit 2 - p. 4)", "Pulse 74. (Exhibit 2 - p. 4)")
    long_ = COPY.replace(
        "(Exhibit 2 - p. 4)", "It was a long and careful visit in the clinic that day. (Exhibit 2 - p. 4)"
    )
    assert len(long_) > len(short)
    both = PF.run(_pf(tmp_path, md, added=[long_, short])).recorded
    assert both["P13_shorter_covers_longer"] == 1 and both["P13_covered_repeat_left"] == 0


def test_p14_records_a_title_its_rows_do_not_carry(tmp_path: Path) -> None:
    md = _assembled([NEW_POST])
    rows = FS.run(_inputs([NEW_POST])).rows
    good = {2: "Example Imaging - 02-10-2026 (Medical Records)"}
    r = PF.run(_pf(tmp_path, md, titles=good, final_rows=rows))
    assert r.recorded["P14_title_provider_not_in_its_rows"] == 0 and r.recorded["P14_title_date_not_in_its_rows"] == 0
    bad = {2: "Example Laboratory - 02-11-2026 (Medical Records)"}
    r = PF.run(_pf(tmp_path, md, titles=bad, final_rows=rows))
    assert r.recorded["P14_title_provider_not_in_its_rows"] == 1 and r.recorded["P14_title_date_not_in_its_rows"] == 1
    assert r.fails == []


# ---- the stages, on a real job directory ----------------------------------------------------


def _sr(job_dir: Path, firm: Path, data_root: Path, log: list[str]) -> StageRun:
    job = job_mod.load(job_dir)
    return StageRun(
        job=job,
        cfg=config_mod.load(str(firm)),
        unit=job.units[0],
        slug_dir=data_root / "example-matter",
        decided={},
        log=log.append,
        seat_factory=lambda: None,  # type: ignore[arg-type, return-value]
        date_stamp="11-17-26",
    )


def _job(sd: Path, entries: list[str]) -> None:
    rd, ud, out = sd / "runs" / "alpha", sd / "runs" / "alpha" / "update", sd / "out" / "alpha"
    for d in (ud, out):
        d.mkdir(parents=True, exist_ok=True)
    md = _doc(entries)
    (rd / "final-chronology.md").write_text(md)
    (ud / "base.md").write_text(K.base_md())
    (ud / "new_entries.md").write_text("\n\n".join(entries) + "\n")
    (ud / "new_exhibits.final.json").write_text(json.dumps([{**NEX[0], "id": "d2", "read": [2, 3, 4, 5, 9]}]))
    (out / "audit-results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in _supported(md)))


def test_the_stage_writes_the_set_its_rows_and_carried_verdicts(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    log: list[str] = []
    sr = _sr(job_dir, firm_config_path, data_root, log)
    _job(sr.slug_dir, [ROE_A, COPY, STRAY])
    assert update_final_set.run(sr) == 0, log
    ud = sr.slug_dir / "runs" / "alpha" / "update"
    md = (sr.slug_dir / "runs" / "alpha" / "final-chronology.md").read_text()
    assert B.entries_of(md)[1][0] == ROE_A and len(B.entries_of(md)[1]) == 2
    rows = FIO.read_rows(ud)
    assert rows and all(r["verdict"] == "SUPPORTED" for r in rows if r["claim_key"])
    gate = GP.latest_supported(CL.read_rows(sr.slug_dir / "out" / "alpha" / "audit-results.jsonl"))
    body = CL.body_of(md)
    assert all(gate(c)[0] for c in CL.extract_claims(body, B.cited(body)))  # the coverage gate still passes
    assert json.loads((ud / FS.REPORT).read_text())["counts"]["new claim keys created"] == 0
    assert update_final_set.run(sr) == 0 and (sr.slug_dir / "runs" / "alpha" / "final-chronology.md").read_text() == md


def _files(sd: Path) -> dict[str, bytes]:
    rd = sd / "runs" / "alpha"
    names = [rd / "final-chronology.md", rd / FIO.PRE, rd / "update" / FS.ROWS, rd / "update" / FS.CORRECTIONS]
    names.append(sd / "out" / "alpha" / "audit-results.jsonl")
    return {p.name: p.read_bytes() for p in names if p.is_file()}


def test_a_rerun_after_a_completed_run_changes_nothing(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    sr = _sr(job_dir, firm_config_path, data_root, [])
    _job(sr.slug_dir, [ROE_A, COPY, STRAY])
    original = (sr.slug_dir / "runs" / "alpha" / "final-chronology.md").read_text()
    assert update_final_set.run(sr) == 0
    once = _files(sr.slug_dir)
    assert once[FIO.PRE].decode() == original  # the input is kept: a rerun starts from it
    assert update_final_set.run(sr) == 0
    assert _files(sr.slug_dir) == once  # the document, its rows, and no second carried verdict


def test_a_crash_between_the_temp_write_and_the_replace_leaves_the_original(
    job_dir: Path, firm_config_path: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jobs.matter import store

    sr = _sr(job_dir, firm_config_path, data_root, [])
    _job(sr.slug_dir, [ROE_A, COPY, STRAY])
    doc = sr.slug_dir / "runs" / "alpha" / "final-chronology.md"
    original = doc.read_text()
    real = store.os.replace

    def crash(src, dst):
        if str(dst).endswith("final-chronology.md"):
            raise OSError("the machine stopped")
        return real(src, dst)

    monkeypatch.setattr(store.os, "replace", crash)
    with pytest.raises(OSError):
        update_final_set.run(sr)
    assert doc.read_text() == original  # whole, never half
    monkeypatch.setattr(store.os, "replace", real)
    assert update_final_set.run(sr) == 0  # the rerun reads the original and finishes
    done = doc.read_text()
    assert done != original and len(B.entries_of(done)[1]) == 2


def test_a_document_rewritten_upstream_is_read_afresh(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    sr = _sr(job_dir, firm_config_path, data_root, [])
    _job(sr.slug_dir, [ROE_A, COPY, STRAY])
    assert update_final_set.run(sr) == 0
    doc = sr.slug_dir / "runs" / "alpha" / "final-chronology.md"
    newer = _doc([ROE_A, NEW_POST])  # a rerun of the update review wrote another document
    doc.write_text(newer)
    rows = sr.slug_dir / "out" / "alpha" / "audit-results.jsonl"
    rows.write_text(rows.read_text() + "".join(json.dumps(r) + "\n" for r in _supported(newer)))
    assert update_final_set.run(sr) == 0
    assert doc.read_text() == newer and (sr.slug_dir / "runs" / "alpha" / FIO.PRE).read_text() == newer


def test_a_document_that_lost_an_entry_the_step_did_not_drop_is_refused(
    job_dir: Path, firm_config_path: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log: list[str] = []
    sr = _sr(job_dir, firm_config_path, data_root, log)
    _job(sr.slug_dir, [STRAY, NEW_POST])  # the stray line changes the set, so the document is rewritten
    doc = sr.slug_dir / "runs" / "alpha" / "final-chronology.md"
    original = doc.read_text()
    real = FS.with_entries
    monkeypatch.setattr(FS, "with_entries", lambda md, ents: real(md, ents[:-1]))  # an entry silently lost
    assert update_final_set.run(sr) == update_final_set.EXIT_REFUSED
    assert doc.read_text() == original and any("lost" in ln for ln in log)
    monkeypatch.setattr(FS, "with_entries", real)
    assert update_final_set.run(sr) == 0


def test_the_proofs_read_the_rendered_update_and_one_changed_delivered_character_fails_it(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """The seam the update proofs call (``final_proofs``, ``record_final``),
    over a rendered update: proof 11 enforced, 12 to 15 recorded, the filed
    chronology's sha written beside them."""
    from jobs.stages.render import chronology_name

    log: list[str] = []
    sr = _sr(job_dir, firm_config_path, data_root, log)
    sd = sr.slug_dir
    _job(sd, [NEW_POST])
    assert update_final_set.run(sr) == 0
    ud, out = sd / "runs" / "alpha" / "update", sd / "out" / "alpha"
    (sd / "delivered").mkdir()
    K.render(K.base_md(), sd / "delivered" / "delivered.docx")
    (ud / "update_plan.json").write_text(
        json.dumps(
            {"chronology": {"name": "delivered.docx"}, "exhibits": [{"exhibit": 1, "name": "Exhibit 1 - x.pdf"}]}
        )
    )
    md = _assembled([NEW_POST])
    (ud / "updated-chronology.md").write_text(md)
    rows = _supported(md)
    (out / "audit-results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    K.render(md, out / chronology_name(sr))
    r = update_final_set.final_proofs(sr)
    assert r.fails == [], r.lines
    assert r.recorded["P15_unsupported_claim"] == 0 and r.recorded["P15_ungraded_text"] == 0
    update_final_set.record_final(sr, r)
    assert json.loads((ud / GP.PROOFS).read_text())["filed"]["name"] == chronology_name(sr)
    K.render(md.replace("recheck of the neck", "recheck of the necks", 1), out / chronology_name(sr))
    assert update_final_set.final_proofs(sr).fails == ["11"]


def test_the_borrowed_citation_measure_counts_where_each_values_are_printed() -> None:
    para = "Hemoglobin was 9.1 g/dL on the panel that morning before the transfusion."
    e = f"{D}\nExample Clinic | Medical Diagnoses\n\nMedical Diagnoses\n{para}\n\nWBC 6.1 was reported. (Exhibit 2 - p. 5)"
    cited, rows = U.cite_entry(e, K.HEADINGS)
    assert rows and rows[0]["cite"] == "(Exhibit 2 - p. 5)"
    on_five = {5: "Hemoglobin 9.1 g/dL. WBC 6.1"}
    got = U.measure_borrowed([cited], rows, lambda ex, p: on_five.get(p))
    assert got["borrowed"] == 1 and got["found"] == 1 and got["on_cited_page"] == 1
    assert U.measure_borrowed([cited], rows, lambda ex, p: "WBC 6.1")["on_no_cited_page"] == 1


def test_the_report_prints_counts_and_no_record_text(
    job_dir: Path, firm_config_path: Path, data_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sd = data_root / "example-matter"
    _job(sd, [ROE_A, COPY, STRAY])
    assert FS.main(["report", str(data_root.parent)]) == 0  # the copy root: it finds data/<slug>/runs/<unit>
    printed = capsys.readouterr().out
    got = json.loads(printed)
    assert got["no_added_entries"] is False
    assert got["stray_lines"] == {"before": 2, "after": 0}
    assert got["repeats"]["safe drops (added)"] == 1 and got["distinct_visits_merged"] == 0
    assert got["repeat_pairs_by_condition"] == {"covered yes, dates present yes, dropped yes": 1}
    assert got["new_claim_keys_created"] == 0 and got["delivered_entries"]["delivered"] == 2
    assert set(got["headings"]) == {
        HD.CITED,
        HD.SAME_NOTE,
        HD.NO_TEXT,
        HD.FAILED,
        "read on a transcript",
    }  # every count prints, 0 included
    assert got["record_only"]["P13_covered_repeat_left"] == 0
    for words in ("paraspinal", "Roe", "Neck pain", "recheck"):
        assert words not in printed


def _nothing_added(sd: Path) -> None:
    ud = sd / "runs" / "alpha" / "update"
    ud.mkdir(parents=True)
    (ud / "base.md").write_text(K.base_md())
    (ud / "new_entries.md").write_text("")


def test_an_update_that_added_nothing_is_a_clean_no_op(
    job_dir: Path, firm_config_path: Path, data_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """2026-11-17: on a real update that added no entry, final-chronology.md
    does not exist, and the report crashed. The stage exits 0, writes an
    empty rows file, and touches no chronology; the report says so."""
    log: list[str] = []
    sr = _sr(job_dir, firm_config_path, data_root, log)
    _nothing_added(sr.slug_dir)
    rd = sr.slug_dir / "runs" / "alpha"
    assert update_final_set.run(sr) == 0, log
    assert (rd / "update" / FS.ROWS).read_text() == "" and not (rd / "final-chronology.md").exists()
    assert json.loads((rd / "update" / FS.REPORT).read_text())["no_added_entries"] is True
    assert FS.main(["report", str(sr.slug_dir)]) == 0
    assert json.loads(capsys.readouterr().out)["no_added_entries"] is True
    empty = _doc([])  # an audit document with no entry: read, never rewritten
    (rd / "final-chronology.md").write_text(empty)
    assert update_final_set.run(sr) == 0 and (rd / "final-chronology.md").read_text() == empty


def test_falsifier_without_the_no_op_the_stage_crashes_on_that_job(
    job_dir: Path, firm_config_path: Path, data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sr = _sr(job_dir, firm_config_path, data_root, [])
    _nothing_added(sr.slug_dir)
    monkeypatch.setattr(FIO, "added_entries", lambda *_a: 1)
    with pytest.raises(FileNotFoundError):
        update_final_set.run(sr)


#: The declarations the PR asks the lead to wire (``dag_update`` and ``dag``),
#: here so the rehearsal's rule can be checked against them.
FINAL_SET = dag.Stage(
    "update_final_set",
    "",
    dag._slug_unit,
    requires=("update_review",),
    runner=update_final_set.run,
    exit_map={update_final_set.EXIT_REFUSED: (dag.FAILED, "update_final_set: a rule would have created a claim key")},
)
FINAL_PROOF = dag.Stage("final_proof", "", dag._slug_unit, requires=("review",), runner=final_proof.run)


def _wired(seq: tuple, new: dag.Stage, before: str) -> tuple:
    out = []
    for s in seq:
        if s.name == before:
            out.append(new)
            s = replace(s, requires=(new.name,))
        out.append(s)
    return tuple(out)


def test_the_new_stages_are_zero_cost_and_rehearsable_where_the_lead_wires_them() -> None:
    upd = _wired(dag.stages(update=True), FINAL_SET, "update_assemble")
    full = _wired(dag.STAGES, FINAL_PROOF, "upload")
    assert dag._validate(upd) == [] and dag._validate(full) == []
    for s in (FINAL_SET, FINAL_PROOF):
        assert not s.paid and not s.external  # driver.py refuses only these under --redo in a rehearsal
