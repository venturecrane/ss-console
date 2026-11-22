"""An update never drops a fact the firm's new records hold (2026-11-22).

WHY. A paid proof failed: a reader called 16 of 137 known-different page
pairs copies, and a free text test settled 25 differing pages as copies. On
the largest saved update the rules that set non-identical pages aside took
558 pages, and 75 pages' facts never reached a delivered update. Now a new
page is set aside as a copy ONLY when its picture is pixel-identical to a
page already covered (the delivered pages and the new pages read before it,
as before); every other match is read, recorded as a suspected copy with
the page it may copy, and not charged against the firm's page allowance.

Each test names the rule it pins. Every name, date and value is invented.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from jobs.medchron import config as config_mod, job as job_mod, pages_record as PR
from jobs.medchron.update import charged, pageid as PI, pageid_io as PG, select as S
from jobs.stages import page_rows, update_select as US
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
    hash is the name; its difference hash far from every other)."""
    return [PI.Sig(int(hashlib.sha256(n.encode()).hexdigest(), 16), n, bool(n)) for n in names]


def _sr(job_dir: Path, firm_config_path: Path, data_root: Path) -> StageRun:
    job = job_mod.load(job_dir)
    sd = data_root / "example-matter"
    (sd / "text").mkdir(parents=True, exist_ok=True)
    return StageRun(job, config_mod.load(str(firm_config_path)), job.units[0], sd, {}, lambda _m: None,
                    lambda: FakeSeat([], [], {}))  # fmt: skip


# ---- 1. the only set-aside: pixel-identical to a page already covered ----------------------


def test_a_pixel_identical_page_is_set_aside_and_never_transcribed() -> None:
    delivered = [(4, _sigs(["ex4-p1", "ex4-p2"]))]
    ident = PI.identify(delivered, [("d1", _sigs(["ex4-p2", "fresh-page"]))])
    hit = ident.pages["d1"][1]
    assert (hit.reason, hit.other, hit.page, hit.exact) == (PI.ALREADY, "exhibit:4", 2, True)
    assert ident.skipped({"d1": 2}) == {"d1": [1]}  # vision never reads it; page 2 is read
    loaded = PG._page(PI.to_json(ident)["pages"]["d1"]["1"])
    assert loaded is not None and loaded.aside


def test_a_refiled_pixel_copy_of_a_delivered_exhibit_is_still_an_extract() -> None:
    """Every page pixel-equal to a delivered exhibit's: a re-filed copy, never a new exhibit (as on main)."""
    delivered = [(4, _sigs(["ex4-p1", "ex4-p2", "ex4-p3"]))]
    ident = PI.identify(delivered, [("refile", _sigs(["ex4-p1", "ex4-p2", "ex4-p3"]))])
    ex = ident.extracts["refile"]
    assert (ex.parent, ex.exhibit, ex.matched) == ("", 4, 3) and "refile" not in ident.pages
    assert ident.skipped({"refile": 3}) == {"refile": [1, 2, 3]}


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
    "hit",
    [PG.Page(S.OVERLAP, "large", 1, False), PG.Page(S.ALREADY, "exhibit:4", 2, False)],
    ids=["near-picture-twin-in-another-new-document", "near-picture-of-a-delivered-page"],
)
def test_a_near_picture_is_read_recorded_and_not_charged(
    hit: PG.Page, job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sr = _sr(job_dir, firm_config_path, data_root)
    out = _scene(sr, hit)
    assert out["got"]["small"].decisions[1] == (S.READ, S.SUSPECTED)
    row = out["rows"][("small", 1)]
    assert row["status"] == PR.READ and row["class"] == "suspected_copy"
    assert row["evidence"][PR.SUSPECTED] == page_rows.NEAR_PICTURE
    assert row["evidence"]["cover"] == PR.picture_cover(hit)  # "possible copy of <that page>"
    assert not PR.charged(row) and PR.charged(out["rows"][("large", 1)])
    sd = sr.slug_dir
    assert charged.read_pages(sd, sr.unit.unit) == 3 and charged.charged_pages(sd, sr.unit.unit, 0) == 2
    assert charged.figures(sd, sr.unit.unit)["pages_suspected_copies"] == 1


def test_an_exact_picture_is_excluded_not_read(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    sr = _sr(job_dir, firm_config_path, data_root)
    out = _scene(sr, PG.Page(S.OVERLAP, "large", 1, True))
    assert out["got"]["small"].decisions[1] == (S.EXCLUDED, S.OVERLAP)
    assert out["rows"][("small", 1)]["status"] == PR.EXCLUDED


def test_an_eight_word_overlap_is_read_recorded_and_not_charged() -> None:
    seen = S.Delivered.of_docs([[OTHER, NOTE]], ["exhibit:3"])
    suspects: dict[int, S.Cover] = {}
    page = "FAX 555-0100 06/01/2026 PAGE 4 OF 9\n" + NOTE
    assert S.classify_new({1: page}, 1, seen, suspects_out=suspects) == {1: (S.READ, S.SUSPECTED)}
    row = page_rows._decided_row("d1", "", 1, S.READ, S.SUSPECTED, suspects[1], None)
    assert row["evidence"][PR.SUSPECTED] == page_rows.TEXT_OVERLAP and row["evidence"]["kind"] == PR.TEXT
    assert row["evidence"]["cover"] == {"exhibit": 3, "page": 2, "exact": False} and not PR.charged(row)


def test_a_run_of_pages_and_an_undated_neighbour_are_read_as_suspected_and_new() -> None:
    """The delivered export broke a visit across two pages; the new export puts
    its dated header on one page (held by the run of delivered pages: read,
    suspected) and its undated body on the next (the undated-neighbour rule
    that set it aside beside a held page is gone: read as new)."""
    head = "Example Clinic Orthopedics\nDate of service: 03/04/2026\nProvider: Roe, Jane (M.D.)\n"
    body = (
        "Chief complaint: neck pain after the collision, rated 6 of 10 at the visit today.\n"
        "Examination revealed paraspinal tenderness and reduced rotation to the left side of the neck.\n"
        "Blood pressure 128/82. Pulse 74. Assessment: cervical strain with muscle spasm.\n"
        "Plan: physical therapy twice weekly and ibuprofen 600 mg three times daily as needed for pain."
    )
    lines = body.split("\n")
    seen = S.Delivered.of_docs([[head + lines[0], "\n".join(lines[1:]) + "\nFollow up in four weeks."]], ["exhibit:2"])
    suspects: dict[int, S.Cover] = {}
    got = S.classify_new({1: head.rstrip("\n"), 2: body}, 2, seen, suspects_out=suspects)
    assert got == {1: (S.READ, S.SUSPECTED), 2: (S.READ, S.NEW)}
    assert suspects[1].doc == "exhibit:2" and 2 not in suspects
    run = S.Cover(S.DELIVERED_SRC, "exhibit:2", 1, S.BY_RUN, last=2)
    row = page_rows._decided_row("d1", "", 1, S.READ, S.SUSPECTED, run, None)
    assert row["evidence"][PR.SUSPECTED] == page_rows.RUN_OF_PAGES and row["evidence"]["kind"] == PR.CONTAINED
    assert row["evidence"]["cover"] == {"exhibit": 2, "page": 1, "last": 2, "exact": False} and not PR.charged(row)


def test_a_page_that_was_never_matched_is_charged() -> None:
    row = page_rows._decided_row("d1", "", 1, S.READ, S.NEW, None, None)
    assert row["class"] == "new" and PR.charged(row)


# ---- 3. the page question counts no page a picture already suspects ------------------------


def test_the_page_question_does_not_count_a_candidate_a_picture_suspects(tmp_path: Path) -> None:
    ud = tmp_path / "runs" / "u1" / "update"
    ud.mkdir(parents=True)
    ident = PI.Identity(pages={"d1": {1: PI.Hit(PI.ALREADY, "exhibit:4", 7, False)}})
    (ud / PG.NAME).write_text(json.dumps(PI.to_json(ident)), encoding="utf-8")
    rows = page_rows.candidate_rows([("d1", 3, [True, True, True])], PG.load(ud), {"d1": "a" * 64})
    assert rows[0]["evidence"][PR.SUSPECTED] == page_rows.NEAR_PICTURE
    PR.write(tmp_path / "runs" / "u1", rows)
    assert charged.read_pages(tmp_path, "u1") == 3 and charged.asked_pages(tmp_path, "u1", 0) == 2
    # once selected: four pages read, one more a suspected copy; two charged, never above the asked figure
    later = [*rows, PR.row("d1", "", 4, PR.READ, "suspected_copy", PR.TEXT, "update_select", None,
                           **{PR.SUSPECTED: page_rows.TEXT_OVERLAP})]  # fmt: skip
    PR.write(tmp_path / "runs" / "u1", later)
    assert charged.read_pages(tmp_path, "u1") == 4 and charged.capped(tmp_path, "u1") is None
    assert charged.charged_pages(tmp_path, "u1", 0) == 2 and charged.figures(tmp_path, "u1")["read_exceeds_asked"] == 0


# ---- 4. resume across the release --------------------------------------------------------


def test_a_resumed_run_keeps_its_pixel_equal_pages_aside_and_reads_its_near_ones(tmp_path: Path) -> None:
    """``update_pages`` ran before this release: the picture record is in the
    same format, its pixel-equal pages were never transcribed and stay set
    aside, and its near pages, which were transcribed, are read under the
    current rule. Nothing is unread, so the run cannot hold on it."""
    ud = tmp_path / "update"
    ud.mkdir()
    old = {"pages": {"d1": {"1": ["already", "exhibit:4", 2, "exact"], "2": ["already", "exhibit:4", 3, "near"]}}}
    (ud / PG.NAME).write_text(json.dumps(old), encoding="utf-8")
    loaded = PG.load(ud)
    assert PG.aside(loaded.pages["d1"]) == {1: S.ALREADY}
    out = S.classify_new({1: PI.SKIPPED, 2: NOTE}, 2, S.Delivered(), image=PG.aside(loaded.pages["d1"]))
    assert out == {1: (S.EXCLUDED, S.ALREADY), 2: (S.READ, S.NEW)}
