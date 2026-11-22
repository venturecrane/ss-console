"""Every page that leaves a run carries evidence (2026-11-17).

WHY. On a real 4,997-page update, 203 pages were read and never explained,
and both known misses carried a recorded reason nobody tested: a file
excluded by its NAME whose pages 2 to 4 were a signed clinical report, and a
file withheld as another person's when 24 of its 34 pages were the client's
own delivered exhibit, picture for picture. Each test below names what would
make it false and shows that it then fails. Every record here is invented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from dataclasses import replace
import yaml

from jobs.medchron import config as config_mod, job as job_mod, pages_record as PR
from jobs.medchron.gates import foreign_identity as FI
from jobs.medchron.update import billing_page_dates as BPD, pageid as PI, pageid_io as PG, select as S
from jobs.stages import page_rows
from jobs.stages.base import StageRun
from jobs_testkit import FakeSeat, make_pdf

NOTE = (
    "Progress note. Date of service: 04/01/2025. Chief complaint: neck pain after the collision. "
    "Exam: cervical tenderness, range of motion reduced. Assessment: cervical strain. "
    "Plan: continue physical therapy and review in four weeks with the treating provider."
)


# ---- A.1: a text match is read, and the page it may copy is recorded (2026-11-22) -----


def test_a_text_match_is_read_and_records_the_delivered_page_it_may_copy() -> None:
    seen = S.Delivered.of_docs([["cover sheet " * 20, NOTE]], ["exhibit:3"])
    suspects: dict[int, S.Cover] = {}
    out = S.classify_new({1: NOTE}, 1, seen, suspects_out=suspects)
    assert out[1] == (S.READ, S.SUSPECTED)  # read: no text test sets a page aside
    assert suspects[1] == S.Cover(S.DELIVERED_SRC, "exhibit:3", 2, S.BY_TEXT)
    assert suspects[1].as_dict() == {"exhibit": 3, "page": 2, "exact": False}


def test_a_text_match_that_names_no_page_is_read_as_new() -> None:
    nameless = S.Delivered.of_docs([[NOTE]], [""])
    suspects: dict[int, S.Cover] = {}
    assert S.classify_new({1: NOTE}, 1, nameless, suspects_out=suspects)[1] == (S.READ, S.NEW)
    assert suspects == {}


def test_a_service_date_the_matched_exhibit_never_states_is_not_a_suspicion() -> None:
    later = NOTE.replace("04/01/2025", "05/06/2025")  # the same template, a later visit
    seen = S.Delivered.of_docs([[NOTE]], ["exhibit:3"])
    assert S.classify_new({1: later}, 1, seen)[1] == (S.READ, S.NEW)
    assert S.classify_new({1: NOTE}, 1, seen)[1] == (S.READ, S.SUSPECTED)  # the same date: a suspected copy


def test_a_text_copy_of_an_earlier_new_document_is_read_and_names_that_document() -> None:
    earlier, suspects = S.Earlier(), {}
    S.classify_new({1: NOTE}, 1, S.Delivered(), earlier, doc_id="first")
    out = S.classify_new({1: NOTE}, 1, S.Delivered(), earlier, suspects_out=suspects, doc_id="second")
    assert out[1] == (S.READ, S.SUSPECTED)
    assert (suspects[1].doc, suspects[1].page) == ("first", 1)


#: the cover document's state in each case the review named
COVER_CASES = {
    "billing": ("Example Clinic Records", ["Itemized statement. Charges 120.00 payment 0.00 balance 120.00 " * 3]),
    "blank": ("Example Clinic Records", [""]),
    "expert_report": ("Independent Medical Examination Report", [NOTE]),
    "refiled_extract": (None, []),  # the cover became a re-filed extract: it is not among the candidates
    "withheld_first_time": (None, []),  # withheld in this run: not among the candidates
}


@pytest.mark.parametrize("case", sorted(COVER_CASES))
def test_a_picture_copy_keeps_mains_decision_whatever_its_cover_became(case, job_dir, firm_config_path, data_root):
    """The five cases the review named: the copy page (skipped by vision for its
    exact picture match) stays EXCLUDED, as on main, and nothing is unread, so
    the update cannot hold on it."""
    from jobs.stages import update_select as US

    job = job_mod.load(job_dir)
    sd = data_root / "example-matter"
    (sd / "text").mkdir(parents=True)
    sr = StageRun(job, config_mod.load(str(firm_config_path)), job.units[0], sd, {}, lambda _m: None,
                  lambda: FakeSeat([], [], {}))  # fmt: skip
    name, big_pages = COVER_CASES[case]
    rows = []
    for rid, pages, label in (("big", big_pages + [NOTE], name), ("small", [""], "Example Clinic Copy")):
        if rid == "big" and label is None:
            continue
        path = sd / f"{rid}.pdf"
        path.write_bytes(make_pdf([t or "x" for t in pages]))
        body = PI.SKIPPED if rid == "small" else None
        (sd / "text" / f"{rid}.txt").write_text(
            "".join(f"[p.{i}]\n{body or t}\n" for i, t in enumerate(pages, 1)), encoding="utf-8"
        )
        rows.append({"id": rid, "name": label, "ext": ".pdf", "path": str(path)})
    ident = PG.Loaded(pages={"small": {1: PG.Page(S.OVERLAP, "big", 1, True)}})
    got = US._classify(sr, rows, S.Delivered(), ident, {})
    assert got["small"].decisions[1] == (S.EXCLUDED, S.OVERLAP)
    assert S.row(9, "", got["small"].decisions, got["small"].pages)["unread"] == []


def test_a_text_layer_copy_of_a_billing_page_is_never_read() -> None:
    bill = "Itemized statement. Charges 120.00 payment 0.00 balance 120.00 " * 3
    earlier = S.Earlier()
    S.classify_new({1: bill}, 1, S.Delivered(), earlier, doc_id="first")
    assert S.classify_new({1: bill}, 1, S.Delivered(), earlier, doc_id="second")[1] == (S.EXCLUDED, S.BILLING)
    assert S.classify_new({1: bill}, 1, S.Delivered(), S.Earlier(), {1: S.OVERLAP})[1] == (S.EXCLUDED, S.OVERLAP)


# ---- A.2: one row per page --------------------------------------------------------


def _decided(n: int) -> dict:
    from jobs.stages.update_select import _Read

    pages = {p: NOTE for p in range(1, n + 1)}
    decisions = {p: (S.READ, "new document") for p in pages}
    decisions[2] = (S.EXCLUDED, S.BLANK)
    return {"d1": _Read(Path("x.pdf"), pages, decisions, False, None)}


def test_every_page_of_every_pulled_document_has_one_row() -> None:
    rows = page_rows.selection_rows(
        [{"id": "d1"}],
        _decided(4),
        {},
        {"byte-identical to a delivered exhibit": ["d2"], FI.RULE_REASONS[FI.RULE_ANOTHER_PERSON]: ["d3"]},
        PG.Loaded(),
        {"d1": "a" * 64, "d2": "b" * 64, "d3": "c" * 64},
        lambda rid: {"d2": 3, "d3": 2}.get(rid, 0),
    )
    pages = {"d1": 4, "d2": 3, "d3": 2}
    want = {(d, p) for d, n in pages.items() for p in range(1, n + 1)}
    assert len(rows) == sum(pages.values()) and {(r["doc"], r["page"]) for r in rows} == want
    by = {(r["doc"], r["page"]): r for r in rows}
    assert by[("d1", 2)]["evidence"]["kind"] == PR.BLANK
    assert by[("d2", 1)]["evidence"]["cover"] == {"sha256": "b" * 64, "exact": True}
    other = by[("d3", 1)]
    assert other["class"] == "withheld_other_person" and other["evidence"]["pointer"] == PR.IDENTITY_HOLDS


def test_a_picture_row_carries_its_cover_and_the_ink_measured_before_vision(tmp_path: Path) -> None:
    ud = tmp_path / "runs" / "u1" / "update"
    ud.mkdir(parents=True)
    ident = PI.Identity(pages={"d1": {1: PI.Hit(PI.ALREADY, "exhibit:4", 7, True)}})
    (ud / PG.NAME).write_text(json.dumps(PI.to_json(ident)), encoding="utf-8")
    rows = page_rows.candidate_rows([("d1", 2, [True, False])], PG.load(ud), {"d1": "a" * 64})
    assert rows[0]["evidence"]["cover"] == {"exhibit": 4, "page": 7, "exact": True}
    # An exact match to a delivered page is recorded as the client's own, with its cover: the case where
    # another person's page was wrongly delivered once and passes the own-delivered check is visible here.
    assert rows[0]["evidence"]["kind"] == PR.OWN_DELIVERED
    near = PG.Page(S.ALREADY, "exhibit:4", 7, False)
    assert PR.picture_kind(near) == PR.PICTURE and PR.picture_kind(PG.Page(S.OVERLAP, "d0", 1, True)) == PR.PICTURE
    assert [r["evidence"]["inked"] for r in rows] == [True, False]
    PR.write(tmp_path / "runs" / "u1", rows)
    later = [PR.row("d1", "", 2, PR.READ, "new", PR.NONE, "update_select")]
    assert page_rows.carry_ink(tmp_path / "runs" / "u1", later)[0]["evidence"]["inked"] is False


# ---- A.3: the other-person rule ---------------------------------------------------


def test_own_delivered_is_an_exact_picture_match_to_a_delivered_page_and_nothing_else() -> None:
    def loaded(hit: PI.Hit) -> PG.Loaded:
        return PG.Loaded(pages={"d1": {1: PG.Page(S.ALREADY, hit.other, hit.page, hit.exact)}})

    assert PR.own_delivered(loaded(PI.Hit(PI.ALREADY, "exhibit:4", 3, True)), "d1", 1)
    assert not PR.own_delivered(loaded(PI.Hit(PI.ALREADY, "exhibit:4", 3, False)), "d1", 1)  # near
    assert not PR.own_delivered(loaded(PI.Hit(PI.REPEAT, "d0", 3, True)), "d1", 1)  # another new document
    assert not PR.own_delivered(PG.Loaded(), "d1", 1)


def _theirs() -> list[PI.Sig]:
    return [PI.Sig(i * 104729, f"delivered-{i}", True) for i in range(1, 41)]


def _mine(client_pages: range) -> list[PI.Sig]:
    """A 34-page file whose `client_pages` are picture for picture delivered pages (page + 10)."""
    theirs = _theirs()
    return [theirs[n + 9] if n in client_pages else PI.Sig(n * 15485863, f"mine-{n}", True) for n in range(1, 35)]


def _redecide(sr: StageRun, client_pages: range = range(2, 26)) -> dict:
    from jobs.stages.identity_redecide import redecide

    import test_identity_delivered_page as T

    (sr.slug_dir / "raw_manifest.jsonl").write_text(
        json.dumps({"id": T.RID, "ok": True, "ext": ".pdf", "path": str(sr.slug_dir / "f.pdf")}) + "\n"
    )
    return redecide(sr, [(4, _theirs())], lambda _p: _mine(client_pages), "identity_redecided.json")


def test_a_rule_withhold_is_redecided_before_any_picture_and_cleared_by_the_delivered_exemption(
    job_dir, firm_config_path, data_root
) -> None:
    """The one behaviour change kept: the real 34-page file. Run 1 withholds it
    on a misread date of birth; this run re-tests it, with #4105's exemption for
    pages that are exactly delivered pages, BEFORE any candidate picture."""
    import test_identity_delivered_page as T

    sr = T._sr(job_dir, firm_config_path, data_root)
    rows, ud = T._scene(sr, (18, 24), None)
    assert T._withheld(sr, rows, ud) and FI.rule_tests(sr.slug_dir, T.RID) == {FI.TEST_DOB}
    assert _redecide(sr) == {T.RID: {"outcome": "cleared: pending selection", "tests": [FI.TEST_DOB]}}
    assert FI.matches(T.RID, FI.withheld(sr.slug_dir))  # nothing written yet: the clear waits on selection
    from jobs.stages import update_select as US

    assert [r["id"] for r in US._candidates(sr, {})[0]] == [T.RID]  # but it is a candidate like any other
    rows, ud = T._scene(sr, (18, 24), T._delivered())  # this run's picture record: its pages are delivered
    assert not T._withheld(sr, rows, ud)
    assert not FI.matches(T.RID, FI.withheld(sr.slug_dir))  # the clear is written once it survived selection
    assert json.loads((ud / "identity_redecided.json").read_text())[T.RID]["outcome"] == "cleared"
    # Mutation: the misread pages are NOT delivered pages: the finding stands and it stays withheld.
    sr2 = T._sr(job_dir, firm_config_path, data_root / "m2")
    rows, ud = T._scene(sr2, (18, 24), None)
    assert T._withheld(sr2, rows, ud)
    assert _redecide(sr2, range(26, 30))[T.RID]["outcome"] == "kept"
    assert FI.matches(T.RID, FI.withheld(sr2.slug_dir))


def test_a_staff_withhold_made_after_the_redecision_is_never_overridden(job_dir, firm_config_path, data_root) -> None:
    """The review of 54675de53: a file pending its clear, then a staff
    withhold on it (a held run, then a resume that skips update_pages), was a
    candidate against the firm's own decision and recorded as cleared."""
    import test_identity_delivered_page as T
    from jobs.stages import update_select as US

    sr = T._sr(job_dir, firm_config_path, data_root)
    rows, ud = T._scene(sr, (18, 24), None)
    assert T._withheld(sr, rows, ud)
    assert _redecide(sr)[T.RID]["outcome"] == "cleared: pending selection"
    assert [r["id"] for r in US._candidates(sr, {})[0]] == [T.RID]  # mutation: no staff decision, a candidate
    FI.record_decision(sr.slug_dir, action="withhold", ids=[T.RID], by="a person", source="email", at="now")
    cands, skipped = US._candidates(sr, {})
    assert cands == [] and T.RID in skipped[FI.WITHHELD_REASON]  # not a candidate, so never composed
    US._leave_out_foreign(sr, ud, cands, skipped)
    rec = json.loads((ud / "identity_redecided.json").read_text())[T.RID]
    assert rec["outcome"] == "kept: withheld by decision"
    assert FI.matches(T.RID, FI.withheld(sr.slug_dir)) and not FI.by_rule(sr.slug_dir, T.RID, FI.RULE_ANOTHER_PERSON)


def test_a_failed_redecision_leaves_no_earlier_pending_entry_in_force(
    job_dir, firm_config_path, data_root, monkeypatch
) -> None:
    """The review of 54675de53: when the re-decision raised, an earlier
    attempt's pending record survived and made the file a candidate."""
    import test_identity_delivered_page as T
    from jobs.stages import update_pages as UP, update_select as US

    sr = T._sr(job_dir, firm_config_path, data_root)
    rows, ud = T._scene(sr, (18, 24), None)
    assert T._withheld(sr, rows, ud)
    _redecide(sr)  # an earlier attempt: pending its clear
    (sr.slug_dir / "f.pdf").write_bytes(make_pdf(["Follow-up examination."] * 3))
    (ud / "update_plan.json").write_text(json.dumps({"exhibits": [], "top_exhibit": 0}))
    assert [r["id"] for r in US._candidates(sr, {})[0]] == [T.RID]  # mutation: the earlier record left in force

    def boom(*_a, **_k):
        raise RuntimeError("re-decision failed")

    monkeypatch.setattr(UP, "redecide", boom)
    assert UP.run(sr) == 0
    assert not (ud / "identity_redecided.json").exists()
    assert US._candidates(sr, {})[0] == [] and FI.matches(T.RID, FI.withheld(sr.slug_dir))


def test_a_file_cleared_then_rewithheld_in_the_same_run_records_one_stable_outcome(
    job_dir, firm_config_path, data_root
) -> None:
    """The review of 9b039830e: the re-decision exempted pages through its own
    picture pairing and cleared the file; selection, reading the run's picture
    record (where an exact extract of a delivered exhibit has no page rows),
    withheld it again. The record said "cleared" every run and the decision
    rows grew by a pair each time. Now the outcome is the final one."""
    import test_identity_delivered_page as T

    sr = T._sr(job_dir, firm_config_path, data_root)
    rows, ud = T._scene(sr, (18, 24), None)
    assert T._withheld(sr, rows, ud)
    release = sr.slug_dir / FI.RELEASE_FILE
    before = len(json.loads(release.read_text())["decisions"])
    outcomes = []
    for _ in range(3):
        assert _redecide(sr)[T.RID]["outcome"] == "cleared: pending selection"
        rows, ud = T._scene(sr, (18, 24), None)  # this run's picture record holds no exemption
        assert T._withheld(sr, rows, ud)
        outcomes.append(json.loads((ud / "identity_redecided.json").read_text())[T.RID])
    assert outcomes == [{"outcome": "kept: re-withheld this run", "tests": [FI.TEST_DOB]}] * 3
    assert len(json.loads(release.read_text())["decisions"]) == before  # no cleared/withheld pair each run
    assert FI.matches(T.RID, FI.withheld(sr.slug_dir)) and FI.by_rule(sr.slug_dir, T.RID, FI.RULE_ANOTHER_PERSON)


def test_a_rule_withhold_that_cannot_be_retested_stays_withheld(job_dir, firm_config_path, data_root) -> None:
    import test_identity_delivered_page as T

    sr = T._sr(job_dir, firm_config_path, data_root)
    rows, ud = T._scene(sr, (18, 24), None)
    assert T._withheld(sr, rows, ud)
    # The later job's units carry no date of birth (built through the real dataclasses: ``job.load``
    # requires one today, and the rule must not depend on the parser).
    sr.job = replace(sr.job, units=[replace(u, dob="") for u in sr.job.units])
    assert _redecide(sr)[T.RID] == {"outcome": "kept: cannot re-test", "tests": [FI.TEST_DOB]}
    assert FI.matches(T.RID, FI.withheld(sr.slug_dir))
    # No text layer to test before vision: cannot re-test either.
    sr3 = T._sr(job_dir, firm_config_path, data_root / "m3")
    rows, ud = T._scene(sr3, (18, 24), None)
    assert T._withheld(sr3, rows, ud)
    (sr3.slug_dir / "text" / f"{T.RID}.txt").write_text("[p.1]\n\n", encoding="utf-8")
    assert _redecide(sr3)[T.RID]["outcome"] == "kept: cannot re-test"


def test_a_staff_withhold_is_never_redecided(job_dir, firm_config_path, data_root) -> None:
    import test_identity_delivered_page as T

    sr = T._sr(job_dir, firm_config_path, data_root)
    T._scene(sr, (18, 24), None)
    FI.record_decision(sr.slug_dir, action="withhold", ids=[T.RID], by="a person", source="email", at="now")
    assert _redecide(sr) == {}
    assert FI.matches(T.RID, FI.withheld(sr.slug_dir))
    assert FI.clear_by_rule(sr.slug_dir, [T.RID], ground=FI.RULE_ANOTHER_PERSON, can_run=set(FI.ALL_TESTS)) == (
        [],
        [],
    )


def test_a_fresh_run_withhold_records_the_test_that_made_it(job_dir, firm_config_path, data_root) -> None:
    """The review's finding 3: build_units withheld with no test recorded, so a
    later update that could not run every test kept it as cannot re-test."""
    from jobs.stages import units as units_stage

    sd = data_root / "example-matter"
    (sd / "units").mkdir()
    (sd / "text").mkdir()
    (sd / "text" / "o2.txt").write_text("[p.1] (machine transcription)\nPatient Name: Gamma Other\nOffice visit.\n")
    job = job_mod.load(job_dir)
    sr = StageRun(job, config_mod.load(str(firm_config_path)), job.units[0], sd, {}, lambda _m: None,
                  lambda: FakeSeat([], [], {}))  # fmt: skip
    o2 = {"id": "o2", "name": "scan", "ext": ".pdf", "folder": "/MISC", "text_path": str(sd / "text" / "o2.txt")}
    assert [h["id"] for h in units_stage._resolve_orphans(sr, {"alpha": []}, [o2])] == ["o2"]
    assert FI.rule_tests(sd, "o2") == {FI.TEST_NAME}  # the name test: a later run with a surname can re-test it
    assert FI.clear_by_rule(sd, ["o2"], ground=FI.RULE_ANOTHER_PERSON, can_run={FI.TEST_NAME}) == (["o2"], [])


def test_a_withhold_with_no_recorded_test_needs_every_test(job_dir, firm_config_path, data_root) -> None:
    import test_identity_delivered_page as T

    sr = T._sr(job_dir, firm_config_path, data_root)
    FI.withhold_by_rule(sr.slug_dir, ["old1"], ground=FI.RULE_ANOTHER_PERSON)
    assert FI.rule_tests(sr.slug_dir, "old1") is None
    assert FI.clear_by_rule(sr.slug_dir, ["old1"], ground=FI.RULE_ANOTHER_PERSON, can_run={FI.TEST_DOB}) == (
        [],
        ["old1"],
    )
    assert FI.clear_by_rule(sr.slug_dir, ["old1"], ground=FI.RULE_ANOTHER_PERSON, can_run=set(FI.ALL_TESTS)) == (
        ["old1"],
        [],
    )


# ---- C: billing dates ---------------------------------------------------------------


def test_another_patients_ledger_date_is_absent_from_billed_dates(job_dir, data_root) -> None:
    from jobs.medchron import dos_check

    job = job_mod.load(job_dir)
    sd, ud = data_root / "example-matter", data_root / "example-matter" / "runs" / "u1" / "update"
    ud.mkdir(parents=True)
    (sd / "text").mkdir()
    mine = "Itemized statement\nDate of service: 03/02/2025 99213 120.00\nCharges 120.00 payment 0.00 balance 120.00"
    theirs = "Itemized statement\nPatient: Zed Otherperson DOB: 02/02/1960\nDate of service: 03/09/2025 99213 120.00\n" \
        "Charges 120.00 payment 0.00 balance 120.00"  # fmt: skip
    (sd / "text" / "d1.txt").write_text(f"[p.1]\n{NOTE}\n[p.2]\n{mine}\n[p.3]\n{theirs}\n", encoding="utf-8")
    rec = {"id": "d1", "exhibit": 9, "title": "Exhibit 9", "read": [1], "excluded": {"billing page": 2}}
    (ud / "new_exhibits.final.json").write_text(json.dumps([rec]))
    got = BPD.write(sd, ud, job.units)
    assert [r["date"] for r in got] == ["2025-03-02"]
    billed, _q = dos_check.billed_dates(sd, None)
    assert billed == {}  # record only: nothing the pages say reaches the billed dates


def test_a_date_a_ledger_and_a_billing_page_both_carry_is_one_row_and_counted_once(tmp_path: Path) -> None:
    """The review's finding: the same date under the ledger's provider and
    from a set-aside billing page was two worksheet rows and counted twice."""
    from jobs.medchron import dos_check

    sd, rd = tmp_path / "m", tmp_path / "m" / "runs" / "u1"
    rd.mkdir(parents=True)
    (sd / "billing").mkdir()
    item = {"date": "03/02/2025", "charge": "120.00", "page": 1, "description": "office visit"}
    chunk = {"doc_type": "MEDICAL_BILL", "provider": "Example Clinic", "line_items": [item]}
    (sd / "billing_extract.jsonl").write_text(json.dumps({"file": "Ledger.pdf", "chunks": [chunk]}) + "\n")
    page = {"date": "2025-03-02", "provider": "", "file": "Records.pdf", "page": 4}
    other = {"date": "2025-03-05", "provider": "", "file": "Records.pdf", "page": 5}
    (sd / BPD.NAME).write_text(json.dumps([page, other]))
    entries = "## Medical Chronology\n\n01/01/2025\nExample Clinic\nSeen. (Exhibit 1 - p. 1)\n"
    rep = dos_check.check(sd, rd, "2024-01-01", None, entries)
    # RECORD ONLY: the table, its count and the reply are main's (the ledger's one row);
    # the record says the shared date counts once and the page's new date would be one more.
    assert [r["date"] for r in rep["billed_no_record"]] == ["2025-03-02"]
    assert rep["billed_no_record_dates"] == {"as_filed": 1, "with_billing_pages": 2}
    # Mutation: no billing page recorded, no record (main's report exactly).
    (sd / BPD.NAME).unlink()
    assert "billed_no_record_dates" not in dos_check.check(sd, rd, "2024-01-01", None, entries)


# ---- the allowance charge: pages read, and no others ------------------------------

#: one row of each class the charge must leave out, and the rows it counts
NOT_CHARGED = [
    PR.row("d1", "", 3, PR.EXCLUDED, "already_delivered", PR.PICTURE, "update_select", {"exhibit": 2, "page": 1}),
    PR.row("d1", "", 4, PR.EXCLUDED, "repeat", PR.TEXT, "update_select", {"doc": "d0", "page": 4}),
    PR.row("d2", "", 1, PR.EXCLUDED, "refiled_extract", PR.FILE, "update_select", {"exhibit": 2, "page": None}),
    PR.row("d3", "", 1, PR.EXCLUDED, "byte_identical", PR.FILE, "update_select", {"sha256": "x", "exact": True}),
    PR.row("d4", "", 1, PR.EXCLUDED, "withheld_other_person", PR.IDENTITY, "update_select"),
    PR.row("d1", "", 5, PR.EXCLUDED, "blank", PR.BLANK, "update_select"),
    PR.row("d1", "", 6, PR.EXCLUDED, "billing", PR.BILLING_WORDS, "update_select"),
    PR.row("d1", "", 8, PR.UNREAD, "transcription_failed", PR.NONE, "update_select"),
    PR.row("d5", "", 1, PR.EXCLUDED, "password_protected", PR.RULE, "update_select"),
]
CHARGED = [
    PR.row("d1", "", 1, PR.READ, "new", PR.NONE, "update_select"),
    PR.row("d1", "", 2, PR.READ, "new", PR.NONE, "update_select"),  # read because no cover was named
    PR.row("d1", "", 9, PR.READ, "new", PR.NONE, "update_select"),
    PR.row("d1", "", 10, PR.READ, "new", PR.NONE, "update_select"),
]


def _charge_scene(tmp: Path, rows: list[dict]) -> Path:
    from jobs.medchron.update import charged

    sd = tmp / "m"
    (sd / "runs" / "u1").mkdir(parents=True)
    (sd / "extracted.jsonl").write_text("".join(json.dumps({"id": f"d{i}", "pages": 10}) + "\n" for i in range(6)))
    PR.write(sd / "runs" / "u1", rows)
    assert charged.figures(sd, "u1")["extracted"] == 60
    return sd


def test_an_update_charges_exactly_the_pages_it_sent_to_be_read(tmp_path: Path) -> None:
    from jobs.medchron.update import charged

    sd = _charge_scene(tmp_path, NOT_CHARGED + CHARGED)
    f = charged.figures(sd, "u1")
    assert f["pages_read"] == f["pages_charged"] == len(CHARGED) == 4
    assert charged.charged_pages(sd, "u1", 60) == 4
    assert "equal" in charged.charge_line(sd, "u1", 4) and "NOT EQUAL" in charged.charge_line(sd, "u1", 60)


def test_the_record_and_the_extraction_are_reconciled_document_by_document(tmp_path: Path) -> None:
    """2026-11-17, a real job: one image file was a page in the record and no
    page in the extraction, so the totals were one apart with no reason given."""
    from jobs.medchron.update import charged

    sd = _charge_scene(tmp_path, [PR.row("img", "", 1, PR.READ, "new", PR.NONE, "update_select")])
    (sd / "extracted.jsonl").write_text(json.dumps({"id": "img", "pages": 0}) + "\n")
    f = charged.figures(sd, "u1")
    assert f["record_vs_extracted"] == [{"doc": "img", "record": 1, "extracted": 0}] and f["charged_from"] == "record"
    assert f["pages_charged"] == 1 and f["extracted"] == 0
    (sd / "extracted.jsonl").write_text(json.dumps({"id": "img", "pages": 1}) + "\n")
    assert charged.figures(sd, "u1")["record_vs_extracted"] == []  # mutation: agreeing counts name nothing


@pytest.mark.parametrize("row", NOT_CHARGED, ids=lambda r: f"{r['class']}-{r['status']}")
def test_each_not_charged_class_is_charged_once_it_is_read(tmp_path: Path, row: dict) -> None:
    """Mutation per class: the same page sent to be read is charged, so the
    charge follows what was read, never a page's label."""
    from jobs.medchron.update import charged

    sd = _charge_scene(tmp_path, NOT_CHARGED + CHARGED)
    assert charged.charged_pages(sd, "u1", 60) == 4
    flipped = [dict(r, status=PR.READ) if r is row else r for r in NOT_CHARGED] + CHARGED
    PR.write(sd / "runs" / "u1", flipped)
    assert charged.charged_pages(sd, "u1", 60) == 5


def test_with_no_page_record_the_earlier_rule_charges(tmp_path: Path) -> None:
    from jobs.medchron.update import charged

    sd = _charge_scene(tmp_path, [])
    (sd / "runs" / "u1" / PR.NAME).unlink(missing_ok=True)
    assert charged.read_pages(sd, "u1") is None
    assert charged.charged_pages(sd, "u1", 60) == charged.old_rule_pages(sd, "u1", 60) == 60


# ---- asked is an upper bound; charged never exceeds it ------------------------------


def _asked(rows: list[dict]) -> int:
    return sum(1 for r in rows if r["status"] in PR.READ_STATUSES)


def test_a_near_picture_hit_counts_as_to_be_read_when_asked() -> None:
    """A near hit is transcribed and confirmed by its words later; one that
    fails confirmation is read, so the page question must already count it."""
    ident = PG.Loaded(
        pages={"d1": {1: PG.Page(S.ALREADY, "exhibit:4", 7, False), 2: PG.Page(S.ALREADY, "exhibit:4", 8, True)}}
    )
    rows = page_rows.candidate_rows([("d1", 3, [True, True, True])], ident, {})
    assert _asked(rows) == 2  # p1 (near) and p3; only the exact p2 is certainly never read
    decided = {"d1": _decided(3)["d1"]}
    decided["d1"].decisions.update({1: (S.READ, "new document"), 2: (S.EXCLUDED, S.ALREADY), 3: (S.READ, "")})
    later = page_rows.selection_rows([{"id": "d1"}], decided, {}, {}, ident, {}, lambda _r: 0)
    assert _asked(rows) >= _asked(later) == 2
    # Mutation: counting the near hit as excluded would ask about 1 page and then read 2.
    assert _asked([r for r in rows if r["page"] != 1]) < _asked(later)


def test_a_file_that_could_not_be_drawn_counts_whole_when_asked() -> None:
    rows = page_rows.candidate_rows([], PG.Loaded(), {}, {"d2": 3})
    assert _asked(rows) == 3 and {r["class"] for r in rows} == {"candidate_unrendered"}
    assert _asked(page_rows.candidate_rows([], PG.Loaded(), {})) == 0  # mutation: without it, 0 asked


def test_reading_more_than_was_asked_is_charged_the_asked_figure_and_recorded(tmp_path: Path) -> None:
    """A no-cover page turned READ after the page question is the case the cap exists for."""
    from jobs.medchron.update import charged

    sd = _charge_scene(tmp_path, CHARGED[:2])
    assert charged.asked_pages(sd, "u1", 60) == 2
    PR.write(sd / "runs" / "u1", CHARGED)  # two more pages read after the question (a cover found invalid)
    assert charged.read_pages(sd, "u1") == 4
    assert charged.charged_pages(sd, "u1", 60) == 2  # never more than asked
    assert charged.capped(sd, "u1") == {"code": charged.CHARGED_CAPPED, "read": 4, "asked": 2}
    f = charged.figures(sd, "u1")
    assert (f["pages_asked"], f["pages_read"], f["pages_charged"], f["charged_capped"]) == (2, 4, 2, 1)
    assert charged.CHARGED_CAPPED in charged.charge_line(sd, "u1", 2)
    # Mutation: with no recorded question the charge is the pages read.
    (sd / "runs" / "u1" / charged.ASKED).unlink()
    assert charged.charged_pages(sd, "u1", 60) == 4 and charged.capped(sd, "u1") is None


# ---- asked >= read over the update fixtures, and a damaged asked record ------------

from test_update_e2e import NEW_PAGES, _driver, _Model, _upload_pause, scene  # noqa: E402,F401 - pytest collects the e2e scene fixture from this import


@pytest.mark.parametrize("refiled", [False, True], ids=["one-new-file", "with-a-refiled-copy"])
def test_an_update_never_reads_more_than_it_was_asked_about(
    refiled, scene, data_root, firm_config_path, pricing_path, monkeypatch
) -> None:
    from jobs.medchron.update import charged
    from jobs_testkit import doc_row

    job_dir, seat = scene
    _upload_pause(monkeypatch)
    if refiled:  # a second file whose pages repeat the first one's (copies, excluded)
        copy = make_pdf(["FAX 555-0177 06/09/2026 16:40 PAGE 7 OF 30\n" + p for p in NEW_PAGES])
        seat.blobs["n4"] = copy
        seat.docs.append(doc_row("n4", "Imaging refax.pdf", "fold-med", len(copy)))
        body = yaml.safe_load((job_dir / "job.yaml").read_text())
        body["selection"]["include_file_ids"].append("n4")
        (job_dir / "job.yaml").write_text(yaml.safe_dump(body, sort_keys=False))
    o = _driver(job_dir, firm_config_path, pricing_path, seat, _Model()).run()[0]
    assert o.outcome == "delivered", o
    f = charged.figures(data_root / "example-matter", "alpha")
    assert f["asked_record"] == charged.ASKED_OK and f["pages_asked"] is not None
    assert f["pages_read"] <= f["pages_asked"] and f["read_exceeds_asked"] == 0 and f["charged_capped"] == 0
    # The refax is not pixel-equal (its fax line), so its pages are read as suspected copies, never charged.
    assert f["pages_suspected_copies"] == (len(NEW_PAGES) if refiled else 0)
    assert o.pages == f["pages_charged"] == f["pages_read"] - f["pages_suspected_copies"]


@pytest.mark.parametrize("body", ['{"pag', "", "[]", '{"pages": "seven"}', '{"pages": -1}'])
def test_a_damaged_asked_record_means_no_cap_and_never_an_exception(tmp_path: Path, body: str) -> None:
    from jobs.medchron.update import charged

    sd = _charge_scene(tmp_path, CHARGED)
    (sd / "runs" / "u1" / charged.ASKED).parent.mkdir(parents=True, exist_ok=True)
    (sd / "runs" / "u1" / charged.ASKED).write_text(body, encoding="utf-8")
    assert charged.asked_state(sd, "u1") == (None, charged.ASKED_UNREADABLE)
    assert charged.charged_pages(sd, "u1", 60) == 4 and charged.capped(sd, "u1") is None
    assert charged.figures(sd, "u1")["asked_record"] == charged.ASKED_UNREADABLE
    assert charged.asked_pages(sd, "u1", 60) == 4  # asking again rewrites it whole
    assert charged.asked_state(sd, "u1") == (4, charged.ASKED_OK)
