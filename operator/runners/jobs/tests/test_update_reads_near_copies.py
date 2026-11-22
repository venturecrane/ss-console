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
from dataclasses import replace
from pathlib import Path

import pytest

import update_testkit as K
from jobs.medchron import config as config_mod, job as job_mod, pages_record as PR
from jobs.medchron.update import body as B, charged, keepers, pageid as PI, pageid_io as PG
from jobs.medchron.update import repeats as R, select as S
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


def test_a_pixel_identical_page_whose_keeper_a_past_update_set_aside_is_read() -> None:
    """Exhibit 4 was filed by a past update that read only its page 1: its
    page 2 was set aside there, so the delivered chronology never read it."""
    delivered = [(4, _sigs(["ex4-p1", "ex4-p2"]))]
    new = [("d1", _sigs(["ex4-p2"]))]
    past = [[{"title": "Exhibit 4 - Example Clinic (Medical Records)", "pages": 2, "read": [1], "sha256": "x"}]]
    unread = keepers.unread_pages([{"exhibit": 4, "name": "Exhibit 4 - Example Clinic (Medical Records).pdf"}], past)
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


def test_a_delivered_exhibit_no_past_update_filed_reads_whole(tmp_path: Path) -> None:
    """The full chronology read every page of its exhibits; a past update's
    job directory that is gone settles nothing (``update/keepers``)."""
    job = tmp_path / "j1" / "data" / "m"
    ud = job / "runs" / "alpha" / "update"
    ud.mkdir(parents=True)
    rec = {"title": "Exhibit 7 - Example Imaging (Medical Records)", "pages": 3, "read": [1, 3], "sha256": "s7"}
    (ud / keepers.FINAL).write_text(json.dumps([rec]), encoding="utf-8")
    delivered = [
        {"exhibit": 1, "name": "Exhibit 1 - Example Clinic.pdf", "sha256": "s1"},
        {"exhibit": 7, "name": "renamed by the firm.pdf", "sha256": "s7"},  # matched by its bytes
    ]
    assert keepers.from_jobs(delivered, [job, tmp_path / "gone"], "alpha") == {7: {2}}


def _sr(job_dir: Path, firm_config_path: Path, data_root: Path, **job_fields: object) -> StageRun:
    job = job_mod.load(job_dir)
    if job_fields:
        job = replace(job, **job_fields)  # type: ignore[arg-type]
    sd = data_root / "example-matter"
    (sd / "text").mkdir(parents=True, exist_ok=True)
    return StageRun(job, config_mod.load(str(firm_config_path)), job.units[0], sd, {}, lambda _m: None,
                    lambda: FakeSeat([], [], {}))  # fmt: skip


def test_update_pages_reads_the_keepers_off_the_delivered_update_jobs(
    job_dir: Path, firm_config_path: Path, data_root: Path, tmp_path: Path
) -> None:
    root = tmp_path / "install"
    past = root / "jobs" / "j-past" / "data" / "example-matter" / "runs" / "alpha" / "update"
    past.mkdir(parents=True)
    rec = {"title": "Exhibit 3 - Example Clinic (Medical Records)", "pages": 4, "read": [2], "sha256": "s3"}
    (past / keepers.FINAL).write_text(json.dumps([rec]), encoding="utf-8")
    sr = _sr(job_dir, firm_config_path, data_root, install_root=root, prior_delivered=["j-past"])
    read = [{"exhibit": 3, "name": "Exhibit 3 - Example Clinic (Medical Records).pdf", "sha256": "other"}]
    assert UP._unread_keepers(sr, read) == {3: {1, 3, 4}}


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


# ---- 4. a restated delivered visit, its provider spelled another way -----------------------

DELIVERED = K.ENTRY_POST  # 01/20/2026, Example Clinic, cites Exhibit 1 p. 2
#: The same visit composed from a second copy of its record, its provider spelled another way.
RESTATED = (
    "01/20/2026\nRivera, Ana (D.C.) - Spine Associates of the Valley | Office Visit\n\nOffice Visit\n"
    "Seen in the office for follow up of injuries sustained in the motor vehicle collision on the stated date. "
    "Cervical strain after the collision, pain rated 6 of 10 at intake. (Exhibit 5 - p. 3)"
)
LONGER = RESTATED + " Blood pressure 138/88 was recorded at the visit. (Exhibit 5 - p. 3)"
#: A genuinely new visit on the same date: its page is no suspected copy.
NEW_VISIT = (
    "01/20/2026\nDesert Valley Radiology | Imaging\n\nImaging\n"
    "Radiographs of the lumbar spine showed no fracture and preserved disc heights at every level. (Exhibit 6 - p. 1)"
)
SUSPECTS: R.Suspects = {(5, 3): (1, 2)}


def test_the_pages_suspected_copies_of_a_delivered_entrys_pages_make_it_that_visit() -> None:
    _, base = B.entries_of(K.base_md())
    assert B.score(RESTATED, DELIVERED) < 0.5 and B.provider_of(RESTATED) != B.provider_of(DELIVERED)
    assert R.keep_decision(RESTATED, base, str) == ("keep", RESTATED)  # the old key cannot see it
    assert R.keep_decision(RESTATED, base, str, SUSPECTS) == (R.SAME_VISIT, None)  # nothing new: dropped
    verdict, row = R.keep_decision(LONGER, base, str, SUSPECTS)
    assert verdict == "keep: additional findings" and row is not None
    assert "138/88" in row and "pain rated 6 of 10" not in row  # trimmed to what it adds


def test_a_genuinely_new_visit_on_the_same_date_is_kept() -> None:
    _, base = B.entries_of(K.base_md())
    assert R.keep_decision(NEW_VISIT, base, str, SUSPECTS) == ("keep", NEW_VISIT)
    assert R.keep_decision(NEW_VISIT, base, str) == ("keep", NEW_VISIT)
    # one cited page a suspected copy and another not: not that visit
    mixed = RESTATED.replace("(Exhibit 5 - p. 3)", "(Exhibit 5 - p. 3-4)")
    assert R.keep_decision(mixed, base, str, SUSPECTS) == ("keep", mixed)


def test_the_suspects_are_read_off_the_page_record() -> None:
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
        PR.row(
            "d6",
            "",
            1,
            PR.READ,
            "suspected_copy",
            PR.TEXT,
            "update_select",
            {"doc": "d5", "page": 4},
            **{PR.SUSPECTED: page_rows.TEXT_OVERLAP},
        ),  # fmt: skip
    ]
    assert R.suspected_copies(rows, {"d5": 5, "d6": 6}) == {(5, 3): (1, 2)}  # only a delivered page's copy
