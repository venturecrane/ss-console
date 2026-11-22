"""An update never drops a fact the firm's new records hold (2026-11-22).

WHY. A paid proof failed: a reader called 16 of 137 known-different page
pairs copies, and a free text test settled 25 differing pages as copies. On
the largest saved update the rules that set non-identical pages aside took
558 pages, and 75 pages' facts never reached a delivered update. The Captain
approved: a new page is set aside as a copy ONLY when its picture is
pixel-identical to a page that was READ; every other suspected copy is read,
recorded with the page it may copy, and not charged; and an added entry that
only restates a delivered visit from such pages is trimmed or dropped at
collect, whatever its provider's spelling.

Each test names the rule it pins. Every name, date and value is invented.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import update_testkit as K
from jobs.medchron import config as config_mod, job as job_mod, pages_record as PR
from jobs.medchron.update import body as B, charged, keepers, pageid as PI, pageid_io as PG
from jobs.medchron.update import repeats as R, select as S, suspects as SUS
from jobs.stages import page_rows, update_pages as UP, update_select as US
from jobs.stages.base import StageRun
from jobs_testkit import FakeSeat, make_pdf

NOTE = (
    "Progress note. Date of service: 04/01/2025. Chief complaint: neck pain after the collision. "
    "Exam: cervical tenderness, range of motion reduced. Assessment: cervical strain. "
    "Plan: continue physical therapy and review in four weeks with the treating provider."
)
OTHER = (
    "Imaging report. Date of exam: 04/08/2025. MRI of the cervical spine shows a small disc bulge at the "
    "fifth and sixth level without cord compression. Impression: mild degenerative change."
)


def _sigs(names: list[str]) -> list[PI.Sig]:
    """Invented page pictures: each name one distinct, inked page (its pixels'
    hash is the name; its difference hash spaced far from every other)."""
    return [PI.Sig(int(hashlib.sha256(n.encode()).hexdigest(), 16), n, bool(n)) for n in names]


# ---- 1. the only set-aside: pixel-equal to a page that was READ --------------------------


def test_a_pixel_identical_page_of_a_read_keeper_is_set_aside_and_never_transcribed() -> None:
    delivered = [(4, _sigs(["ex4-p1", "ex4-p2"]))]
    new = [("d1", _sigs(["ex4-p2", "fresh-page"]))]
    ident = PI.identify(delivered, new)
    hit = ident.pages["d1"][1]
    assert (hit.reason, hit.other, hit.page, hit.exact, hit.keeper_read) == (PI.ALREADY, "exhibit:4", 2, True, True)
    assert hit.aside and ident.skipped({"d1": 2}) == {"d1": [1]}  # vision never reads it; page 2 is read


#: The delivered chronology: it cites Exhibit 4 p. 1 and Exhibit 5 pp. 2-3, nothing else.
DELIVERED_MD = (
    "## Medical Chronology\n\n03/04/2025\nExample Clinic | Visit\n\nVisit\nSeen. (Exhibit 4 - p. 1)\n\n"
    "04/01/2025\nExample Imaging | Imaging\n\nImaging\nMRI read. (Exhibit 5 - p. 2-3)\n"
)


def test_an_uncited_delivered_page_is_no_keeper() -> None:
    """The delivered chronology is the one durable record of what reached the
    firm: a past update's own record is wiped 72 hours after delivery."""
    assert keepers.uncited_pages(DELIVERED_MD, {4: 2, 5: 3, 6: 1}) == {4: {2}, 5: {1}, 6: {1}}
    assert keepers.uncited_pages("", {4: 2}) == {4: {1, 2}}  # nothing on record: no delivered page is a keeper


def test_a_pixel_identical_page_of_an_uncited_delivered_page_is_read() -> None:
    """Exhibit 4 p. 2 is in the firm's folder, but the chronology never cites
    it: a past update may have set it aside, so its facts are not known to be
    written down."""
    delivered = [(4, _sigs(["ex4-p1", "ex4-p2"]))]
    new = [("d1", _sigs(["ex4-p2"]))]
    unread = keepers.uncited_pages(DELIVERED_MD, {4: 2})
    assert unread == {4: {2}}
    ident = PI.identify(delivered, new, unread=unread)
    hit = ident.pages["d1"][1]
    assert hit.exact and not hit.keeper_read and not hit.aside
    assert ident.skipped({"d1": 1}) == {}  # transcribed: its facts were never written down
    assert ident.extracts == {}  # a document of such pages is never a re-filed copy
    loaded = PG._page(PI.to_json(ident)["pages"]["d1"]["1"])
    assert loaded is not None and loaded.exact and not loaded.aside  # the record round-trips


def test_a_valid_keeper_elsewhere_still_sets_the_page_aside() -> None:
    """Pixel equality is transitive: a page equal to one never read and to one
    that was read is set aside against the one that was read."""
    delivered = [(4, _sigs(["same"])), (5, _sigs(["same"]))]
    ident = PI.identify(delivered, [("d1", _sigs(["same", "fresh"]))], unread={4: {1}})
    hit = ident.pages["d1"][1]
    assert hit.aside and hit.other == "exhibit:5"


def _sr(job_dir: Path, firm_config_path: Path, data_root: Path) -> StageRun:
    job = job_mod.load(job_dir)
    sd = data_root / "example-matter"
    (sd / "text").mkdir(parents=True, exist_ok=True)
    return StageRun(job, config_mod.load(str(firm_config_path)), job.units[0], sd, {}, lambda _m: None,
                    lambda: FakeSeat([], [], {}))  # fmt: skip


def test_update_pages_reads_the_keepers_off_the_delivered_chronology(
    job_dir: Path, firm_config_path: Path, data_root: Path, tmp_path: Path
) -> None:
    sr = _sr(job_dir, firm_config_path, data_root)
    ud = tmp_path / "update"
    ud.mkdir()
    (ud / "base.md").write_text(DELIVERED_MD, encoding="utf-8")
    assert UP._unread_keepers(sr, ud, [(4, _sigs(["a", "b"])), (5, _sigs(["c", "d", "e"]))]) == {4: {2}, 5: {1}}


# ---- a page of a new document that will not be read is no keeper (F2) -----------------------


def test_a_new_document_that_will_not_be_read_keeps_nothing_aside() -> None:
    """An expert's report or a file naming another person is never read, so a
    pixel copy of its page is transcribed and read, never set aside."""
    new = [("report", _sigs(["r1", "r2", "r3"])), ("copy", _sigs(["r2", "own"]))]
    hit = PI.identify([], new, no_keeper={"report"}).pages["copy"][1]
    assert hit.exact and not hit.aside
    assert PI.identify([], new).pages["copy"][1].aside  # without the rule: set aside


def test_update_pages_names_an_expert_report_and_another_persons_file_before_vision(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sr = _sr(job_dir, firm_config_path, data_root)
    (sr.slug_dir / "text" / "ime.txt").write_text("[p.1]\n" + NOTE + "\n", encoding="utf-8")
    (sr.slug_dir / "text" / "rx.txt").write_text("[p.1]\n" + NOTE + "\n", encoding="utf-8")
    rows = [{"id": "ime", "name": "Independent Medical Examination Report"}, {"id": "rx", "name": "Example Rehab"}]
    assert UP._unread_docs(sr, rows, {}) == {"ime"}


def test_a_copy_of_a_page_of_a_file_withheld_after_vision_is_read_or_held(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """A file withheld only once its pages were transcribed: a copy with its
    own text is read as a suspected copy; one the vision pass skipped has no
    text, so the update holds rather than leave it out silently."""
    sr = _sr(job_dir, firm_config_path, data_root)
    rows = []
    for rid, pages in (("copy", [PI.SKIPPED, NOTE]),):
        path = sr.slug_dir / f"{rid}.pdf"
        path.write_bytes(make_pdf(["x", "y"]))
        (sr.slug_dir / "text" / f"{rid}.txt").write_text(
            "".join(f"[p.{i}]\n{t}\n" for i, t in enumerate(pages, 1)), encoding="utf-8"
        )
        rows.append({"id": rid, "name": "Example Copy", "ext": ".pdf", "path": str(path)})
    hit = PG.Page(S.OVERLAP, "withheld", 4, True)
    ident = PG.Loaded(pages={"copy": {1: hit, 2: hit}})
    got = US._classify(sr, rows, S.Delivered(), ident, {}, {"withheld"})["copy"]
    assert got.decisions[1][0] == S.UNREAD and got.decisions[2] == (S.READ, S.SUSPECTED)
    kept = US._classify(sr, rows, S.Delivered(), ident, {}, set())["copy"]
    assert kept.decisions[1] == (S.EXCLUDED, S.OVERLAP)  # a keeper that was read: set aside as before


# ---- 2. every other match is read, recorded and not charged ------------------------------


def _scene(sr: StageRun, small_hit: PG.Page) -> dict:
    rows = []
    for rid, pages in (("large", [NOTE, OTHER]), ("small", [NOTE])):
        path = sr.slug_dir / f"{rid}.pdf"
        path.write_bytes(make_pdf(pages))
        (sr.slug_dir / "text" / f"{rid}.txt").write_text(
            "".join(f"[p.{i}]\n{t}\n" for i, t in enumerate(pages, 1)), encoding="utf-8"
        )
        rows.append({"id": rid, "name": f"Example {rid}", "ext": ".pdf", "path": str(path)})
    ident = PG.Loaded(pages={"small": {1: small_hit}})
    got = US._classify(sr, rows, S.Delivered(), ident, {})
    page_record = page_rows.selection_rows(rows, got, {}, {}, ident, {}, lambda _r: 0)
    PR.write(sr.slug_dir / "runs" / sr.unit.unit, page_record)
    return {"got": got, "rows": {(r["doc"], r["page"]): r for r in page_record}}


@pytest.mark.parametrize(
    "hit,rule",
    [
        (PG.Page(S.OVERLAP, "large", 1, False), page_rows.NEAR_PICTURE),  # a twin in another new document
        (PG.Page(S.ALREADY, "exhibit:4", 2, True, keeper_read=False), page_rows.UNREAD_KEEPER),
    ],
    ids=["near-picture-twin", "pixel-equal-to-a-page-never-read"],
)
def test_a_picture_suspicion_is_read_recorded_and_not_charged(
    hit: PG.Page, rule: str, job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sr = _sr(job_dir, firm_config_path, data_root)
    out = _scene(sr, hit)
    assert out["got"]["small"].decisions[1] == (S.READ, S.SUSPECTED)
    row = out["rows"][("small", 1)]
    assert row["status"] == PR.READ and row["class"] == "suspected_copy" and row["evidence"][PR.SUSPECTED] == rule
    assert row["evidence"]["cover"] == PR.picture_cover(hit)  # "possible copy of <that page>"
    assert not PR.charged(row) and PR.charged(out["rows"][("large", 1)])
    sd = sr.slug_dir
    assert charged.read_pages(sd, sr.unit.unit) == 3 and charged.charged_pages(sd, sr.unit.unit, 0) == 2
    assert charged.figures(sd, sr.unit.unit)["pages_suspected_copies"] == 1


def test_an_exact_picture_of_a_read_page_is_excluded_not_read(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sr = _sr(job_dir, firm_config_path, data_root)
    out = _scene(sr, PG.Page(S.OVERLAP, "large", 1, True))
    assert out["got"]["small"].decisions[1] == (S.EXCLUDED, S.OVERLAP)
    assert out["rows"][("small", 1)]["status"] == PR.EXCLUDED


def test_an_eight_word_overlap_is_read_recorded_and_not_charged() -> None:
    seen = S.Delivered.of_docs([[OTHER, NOTE]], ["exhibit:3"])
    suspects: dict[int, S.Cover] = {}
    assert S.classify_new({1: "FAX 555-0100 06/01/2026 PAGE 4 OF 9\n" + NOTE}, 1, seen, suspects_out=suspects) == {
        1: (S.READ, S.SUSPECTED)
    }
    row = page_rows._decided_row("d1", "", 1, S.READ, S.SUSPECTED, suspects[1], None)
    assert row["evidence"][PR.SUSPECTED] == page_rows.TEXT_OVERLAP and row["evidence"]["kind"] == PR.TEXT
    assert row["evidence"]["cover"] == {"exhibit": 3, "page": 2, "exact": False} and not PR.charged(row)


def test_a_page_that_was_never_matched_is_charged() -> None:
    row = page_rows._decided_row("d1", "", 1, S.READ, S.NEW, None, None)
    assert row["class"] == "new" and PR.charged(row)


# ---- 3. resume across the release --------------------------------------------------------


def test_a_resumed_run_keeps_the_old_records_pixel_equal_pages_aside(tmp_path: Path) -> None:
    """``update_pages`` ran before 2026-11-22 (no rule stamp): its pixel-equal
    pages were never transcribed, so they stay set aside; its near pages, which
    were transcribed, are read under the current rule. A record written now
    carries the stamp and its keeper test."""
    ud = tmp_path / "update"
    ud.mkdir()
    old = {"pages": {"d1": {"1": ["already", "exhibit:4", 2, "exact"], "2": ["already", "exhibit:4", 3, "near"]}}}
    (ud / PG.NAME).write_text(json.dumps(old), encoding="utf-8")
    loaded = PG.load(ud)
    assert loaded.rule == PG.OLD_RULE and PG.aside(loaded.pages["d1"]) == {1: S.ALREADY}
    out = S.classify_new({1: PI.SKIPPED, 2: NOTE}, 2, S.Delivered(), image=PG.aside(loaded.pages["d1"]))
    assert out == {1: (S.EXCLUDED, S.ALREADY), 2: (S.READ, S.NEW)}  # nothing unread: the run cannot hold on it
    ident = PI.identify([(4, _sigs(["p"]))], [("d1", _sigs(["p"]))], unread={4: {1}})
    (ud / PG.NAME).write_text(json.dumps(PI.to_json(ident)), encoding="utf-8")
    now = PG.load(ud)
    assert now.rule == PI.RULE and PG.aside(now.pages["d1"]) == {}


# ---- 4. a restated visit, known by its pages, its provider spelled another way ---------------

DELIVERED = K.ENTRY_POST  # 01/20/2026, Example Clinic, cites Exhibit 1 p. 2
#: The same visit composed from a second copy of its record, its provider spelled another way.
RESTATED = (
    "01/20/2026\nRivera, Ana (D.C.) - Spine Associates of the Valley | Office Visit\n\nOffice Visit\n"
    "Seen in the office for follow up of injuries sustained in the motor vehicle collision on the stated date. "
    "Cervical strain after the collision, pain rated 6 of 10 at intake. (Exhibit 5 - p. 3-4)"
)
LONGER = RESTATED + " Blood pressure 138/88 was recorded at the visit. (Exhibit 5 - p. 3)"
#: A genuinely new visit on the same date: Exhibit 6 p. 1 was read on its own account.
NEW_VISIT = (
    "01/20/2026\nDesert Valley Radiology | Imaging\n\nImaging\n"
    "Radiographs of the lumbar spine showed no fracture and preserved disc heights at every level. (Exhibit 6 - p. 1)"
)
#: Exhibit 5 pp. 3-4 are suspected copies of delivered Exhibit 1 pp. 2 and 3; p. 5 was set aside by picture.
SUSPECTS = SUS.Suspects(covers={(5, 3): (1, 2, 2), (5, 4): (1, 3, 3)}, aside={(5, 5)}, new={5, 6})


def test_pages_copied_from_inside_a_delivered_entrys_span_make_it_that_visit() -> None:
    """Pages 3-4 copy delivered pp. 2-3; the delivered entry cites p. 2 only:
    p. 3 is inside its span, one page either side (bug review F3)."""
    _, base = B.entries_of(K.base_md())
    assert B.score(RESTATED, DELIVERED) < 0.5 and B.provider_of(RESTATED) != B.provider_of(DELIVERED)
    assert R.keep_decision(RESTATED, base, str) == ("keep", RESTATED)  # the old key cannot see it
    assert R.keep_decision(RESTATED, base, str, SUSPECTS) == (R.SAME_VISIT, None)  # nothing new: dropped
    verdict, row = R.keep_decision(LONGER, base, str, SUSPECTS)
    assert verdict == "keep: additional findings" and row is not None
    assert "138/88" in row and "pain rated 6 of 10" not in row  # trimmed to what it adds


def test_a_run_cover_starting_beside_the_delivered_page_is_that_visit() -> None:
    _, base = B.entries_of(K.base_md())
    run = SUS.Suspects(covers={(5, 3): (1, 1, 1), (5, 4): (1, 1, 3)}, new={5})
    assert R.keep_decision(RESTATED, base, str, run) == (R.SAME_VISIT, None)
    far = SUS.Suspects(covers={(5, 3): (1, 7, 9), (5, 4): (1, 7, 9)}, new={5})
    assert R.keep_decision(RESTATED, base, str, far) == ("keep", RESTATED)  # another stretch of the exhibit


def test_a_cite_of_a_page_set_aside_or_of_a_delivered_page_beside_the_copies_changes_nothing() -> None:
    _, base = B.entries_of(K.base_md())
    mixed = RESTATED.replace("(Exhibit 5 - p. 3-4)", "(Exhibit 5 - p. 3-5) (Exhibit 1 - p. 2)")
    assert R.keep_decision(mixed, base, str, SUSPECTS) == (R.SAME_VISIT, None)


def test_a_genuinely_new_visit_on_the_same_date_is_kept() -> None:
    _, base = B.entries_of(K.base_md())
    assert R.keep_decision(NEW_VISIT, base, str, SUSPECTS) == ("keep", NEW_VISIT)
    assert R.keep_decision(NEW_VISIT, base, str) == ("keep", NEW_VISIT)
    # a page read on its own account beside the copies: the entry says something of its own
    own = RESTATED.replace("(Exhibit 5 - p. 3-4)", "(Exhibit 5 - p. 3-4) (Exhibit 6 - p. 1)")
    assert R.keep_decision(own, base, str, SUSPECTS) == ("keep", own)


#: Exhibit 5 (read first, the larger file) and Exhibit 6, a rescan of its p. 3 (bug review F5).
FIRST = (
    "02/03/2026\nExample Orthopedics | Office Visit\n\nOffice Visit\n"
    "Knee effusion noted; aspiration performed and 30 mL of fluid removed. (Exhibit 5 - p. 3)"
)
RESCAN = (
    "02/03/2026\nOrtho Partners of the Valley, J. Roe | Visit\n\nVisit\n"
    "Aspiration of the knee with 30 mL removed for an effusion. (Exhibit 6 - p. 1)"
)


def test_an_entry_from_a_rescan_of_another_new_documents_page_is_deduplicated() -> None:
    sus = SUS.Suspects(covers={(6, 1): (5, 3, 3)}, new={5, 6})
    kept, n = R.dedupe_copies([FIRST, RESCAN], str, sus)
    assert kept == [FIRST] and n == 1
    more = RESCAN.replace("removed for an effusion.", "removed for an effusion. Fluid glucose was 42 mg/dL.")
    kept, n = R.dedupe_copies([FIRST, more], str, sus)
    assert n == 1 and kept[0] == FIRST and "42 mg/dL" in kept[1] and "Additional findings" in kept[1]
    assert R.dedupe_copies([FIRST, RESCAN], str, SUS.Suspects(new={5, 6})) == ([FIRST, RESCAN], 0)


def test_a_chain_of_copies_reaches_the_delivered_page() -> None:
    """Exhibit 6 p. 1 copies Exhibit 5 p. 3, itself a copy of delivered Exhibit 1 p. 2."""
    rows = [
        PR.row(
            "d5",
            "",
            3,
            PR.READ,
            "suspected_copy",
            PR.PICTURE,
            "update_select",
            {"exhibit": 1, "page": 2, "exact": False},
            **{PR.SUSPECTED: page_rows.NEAR_PICTURE},
        ),  # fmt: skip
        PR.row("d5", "", 4, PR.READ, "new", PR.NONE, "update_select"),
        PR.row("d5", "", 5, PR.EXCLUDED, "repeat", PR.PICTURE, "update_select", {"doc": "d4", "page": 1}),
        PR.row(
            "d6",
            "",
            1,
            PR.READ,
            "suspected_copy",
            PR.TEXT,
            "update_select",
            {"doc": "d5", "page": 3},
            **{PR.SUSPECTED: page_rows.TEXT_OVERLAP},
        ),  # fmt: skip
        PR.row(
            "d6",
            "",
            2,
            PR.READ,
            "suspected_copy",
            PR.CONTAINED,
            "update_select",
            {"doc": "d5", "page": 4, "last": 5},
            **{PR.SUSPECTED: page_rows.RUN_OF_PAGES},
        ),  # fmt: skip
    ]
    s = SUS.from_rows(rows, {"d5": 5, "d6": 6})
    assert s.covers == {(5, 3): (1, 2, 2), (6, 1): (1, 2, 2), (6, 2): (5, 4, 5)}
    assert s.aside == {(5, 5)} and s.new == {5, 6}
    _, base = B.entries_of(K.base_md())
    chained = RESTATED.replace("(Exhibit 5 - p. 3-4)", "(Exhibit 6 - p. 1)")
    assert R.keep_decision(chained, base, str, s) == (R.SAME_VISIT, None)


# ---- 5. the page question counts no page a picture already suspects (M1, M2) ----------------


def test_the_page_question_does_not_count_a_candidate_a_picture_suspects(tmp_path: Path) -> None:
    ud = tmp_path / "runs" / "u1" / "update"
    ud.mkdir(parents=True)
    ident = PI.Identity(pages={"d1": {1: PI.Hit(PI.ALREADY, "exhibit:4", 7, False)}})
    (ud / PG.NAME).write_text(json.dumps(PI.to_json(ident)), encoding="utf-8")
    rows = page_rows.candidate_rows([("d1", 3, [True, True, True])], PG.load(ud), {"d1": "a" * 64})
    assert rows[0]["evidence"][PR.SUSPECTED] == page_rows.NEAR_PICTURE
    PR.write(tmp_path / "runs" / "u1", rows)
    assert charged.read_pages(tmp_path, "u1") == 3 and charged.asked_pages(tmp_path, "u1", 0) == 2
    # once selected: four pages read, one more a suspected copy; two charged, the asked figure is not exceeded
    later = [*rows, PR.row("d1", "", 4, PR.READ, "suspected_copy", PR.TEXT, "update_select", None,
                           **{PR.SUSPECTED: page_rows.TEXT_OVERLAP})]  # fmt: skip
    PR.write(tmp_path / "runs" / "u1", later)
    assert charged.read_pages(tmp_path, "u1") == 4 and charged.capped(tmp_path, "u1") is None
    assert charged.charged_pages(tmp_path, "u1", 0) == 2 and charged.figures(tmp_path, "u1")["read_exceeds_asked"] == 0
