"""An update never drops a fact the firm's new records hold (2026-11-22).

WHY. A paid proof failed: a reader called 16 of 137 known-different page
pairs copies, and a free text test settled 25 differing pages as copies. On
the largest saved update the rules that set non-identical pages aside took
558 pages, and 75 pages' facts never reached a delivered update. Now, of the
pages the rules set aside as copies, only one pixel-identical to a page
already covered stays set aside; every other is read, recorded as a
suspected copy with the page it may copy, and not charged against the firm's
page allowance. A page the rules read, a near picture whose words disagree
among them, is read and charged as before
(``test_update_set_aside_matches_base`` holds the two trees side by side).

Each test names the rule it pins. Every name, date and value is invented.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from jobs.medchron import config as config_mod, job as job_mod, pages_record as PR
from jobs.medchron.update import charged, pageid as PI, pageid_io as PG, select as S
from jobs.stages import page_rows, update_select as US, update_suspects as SP
from jobs.stages.base import StageRun
from jobs_testkit import FakeSeat, make_pdf
from test_update_parity_b import _seed as words_seed, _visit as visit
from test_update_parity_h import _record

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
    assert loaded is not None and loaded.exact


def test_a_refiled_pixel_copy_of_a_delivered_exhibit_is_still_an_extract() -> None:
    """Every page pixel-equal to a delivered exhibit's: a re-filed copy, never a new exhibit (as on main)."""
    delivered = [(4, _sigs(["ex4-p1", "ex4-p2", "ex4-p3"]))]
    ident = PI.identify(delivered, [("refile", _sigs(["ex4-p1", "ex4-p2", "ex4-p3"]))])
    ex = ident.extracts["refile"]
    assert (ex.parent, ex.exhibit, ex.matched) == ("", 4, 3) and "refile" not in ident.pages
    assert ident.skipped({"refile": 3}) == {"refile": [1, 2, 3]}


# ---- 2. every other match is read, recorded and not charged ------------------------------


def _scene(sr: StageRun, small_hit: PG.Page, matched: str) -> dict:
    rows = []
    for rid, pages in (("large", [OTHER.replace("04/08", "04/09"), OTHER]), ("small", [NOTE + " Seen."])):
        path = sr.slug_dir / f"{rid}.pdf"
        path.write_bytes(make_pdf(pages))
        (sr.slug_dir / "text" / f"{rid}.txt").write_text(
            "".join(f"[p.{i}]\n{t}\n" for i, t in enumerate(pages, 1)), encoding="utf-8"
        )
        rows.append({"id": rid, "name": f"Example {rid}", "ext": ".pdf", "path": str(path)})
    ident = PG.Loaded(pages={"small": {1: small_hit}})
    got = US._classify(sr, rows, S.Delivered(), ident, lambda _o, _p: matched, {})
    page_record = page_rows.selection_rows(rows, got, {}, {}, ident, {}, lambda _r: 0)
    PR.write(sr.slug_dir / "runs" / sr.unit.unit, page_record)
    return {"got": got, "rows": {(r["doc"], r["page"]): r for r in page_record}}


@pytest.mark.parametrize(
    "hit",
    [PG.Page(S.OVERLAP, "large", 1, False), PG.Page(S.ALREADY, "exhibit:4", 2, False)],
    ids=["a-page-of-another-new-document", "a-delivered-page"],
)
def test_a_near_picture_its_words_confirm_is_read_recorded_and_not_charged(
    hit: PG.Page, job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sr = _sr(job_dir, firm_config_path, data_root)
    out = _scene(sr, hit, NOTE)
    row = out["rows"][("small", 1)]
    _suspect(row, page_rows.NEAR_PICTURE)
    assert row["evidence"]["cover"] == PR.picture_cover(hit)  # "possible copy of <that page>"
    assert PR.charged(out["rows"][("large", 1)])
    sd, unit = sr.slug_dir, sr.unit.unit
    assert charged.read_pages(sd, unit) == 3 and charged.charged_pages(sd, unit, 0) == 2
    assert charged.figures(sd, unit)["pages_suspected_copies"] == 1


def test_a_near_picture_whose_words_disagree_is_read_and_charged(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """The same form on another day: no rule calls it a copy, so it is the firm's page."""
    sr = _sr(job_dir, firm_config_path, data_root)
    out = _scene(sr, PG.Page(S.OVERLAP, "large", 1, False), NOTE.replace("04/01/2025", "05/06/2025"))
    row = out["rows"][("small", 1)]
    assert row["class"] == "new" and PR.SUSPECTED not in row["evidence"] and PR.charged(row)
    assert charged.charged_pages(sr.slug_dir, sr.unit.unit, 0) == 3


def test_an_exact_picture_is_excluded_not_read(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    sr = _sr(job_dir, firm_config_path, data_root)
    out = _scene(sr, PG.Page(S.OVERLAP, "large", 1, True), "")
    assert out["got"]["small"].decisions[1] == (S.EXCLUDED, S.OVERLAP)
    assert out["rows"][("small", 1)]["status"] == PR.EXCLUDED


#: What ``update_select`` recorded: every page main's rules set aside, by rule.
def _rows_of(tmp: Path) -> dict[tuple[str, int], dict]:
    return {(r["doc"], r["page"]): r for r in PR.read(tmp / "runs" / "alpha")}


def _suspect(r: dict, rule: str) -> None:
    assert (r["status"], r["class"], r["evidence"][PR.SUSPECTED]) == (PR.READ, "suspected_copy", rule), r
    assert not PR.charged(r)


HEAD = "Example Clinic Orthopedics\nDate of service: 03/04/2026\nProvider: Roe, Jane (M.D.)\nVisit with the provider."
BODY = (
    "Chief complaint: neck pain after the collision, rated 6 of 10 at the visit today.\n"
    "Examination revealed paraspinal tenderness and reduced rotation to the left side of the neck.\n"
    "Blood pressure 128/82. Pulse 74. Assessment: cervical strain with muscle spasm.\n"
    "Plan: physical therapy twice weekly and ibuprofen 600 mg three times daily as needed for pain."
)
FILLER = "Follow up in four weeks with the treating provider for a recheck of the neck and the work status."


def test_the_words_rules_suspect_and_never_charge_what_they_set_aside(tmp_path: Path) -> None:
    """The probe (bug review 2026-11-22): an undated page continuing a visit the
    earlier document broke across two pages. The rules, run as they always
    were, set both pages aside; the update reads both and charges neither."""
    lines = BODY.split("\n")
    first = [HEAD + "\n" + lines[0], "\n".join(lines[1:]) + "\n" + FILLER, OTHER]
    again = [HEAD, BODY, NOTE]  # the same visit broken elsewhere, beside a visit of its own
    earlier = S.Earlier()
    S.classify_new(dict(enumerate(first, 1)), 3, S.Delivered(), earlier, doc_id="first")
    covers: dict[int, S.Cover] = {}
    main = S.classify_new(dict(enumerate(again, 1)), 3, S.Delivered(), earlier, covers_out=covers, doc_id="again")
    assert main == {1: (S.EXCLUDED, S.OVERLAP), 2: (S.EXCLUDED, S.OVERLAP), 3: (S.READ, "new document")}
    assert covers[2].kind == S.BY_NEIGHBOUR
    sr = words_seed(tmp_path, {"first": first, "again": again})
    assert US.run(sr) == 0, sr.logged  # type: ignore[arg-type]
    rows = _rows_of(tmp_path)
    _suspect(rows[("again", 2)], page_rows.UNDATED_NEIGHBOUR)
    assert rows[("again", 2)]["evidence"]["cover"]["doc"] == "first"
    assert not PR.charged(rows[("again", 1)]) and rows[("again", 1)]["status"] == PR.READ
    assert PR.charged(rows[("again", 3)]) and charged.charged_pages(tmp_path, "alpha", 0) == 4
    units = {u["id"]: Path(u["text_path"]).read_text() for u in json.loads((tmp_path / "units/alpha.json").read_text())}
    assert "ibuprofen 600 mg" in units["again"]  # read: a fact is never dropped on a suspicion


def test_each_words_rule_names_itself() -> None:
    seen = S.Delivered.of_docs([[OTHER, NOTE]], ["exhibit:3"])
    covers: dict[int, S.Cover] = {}
    page = "FAX 555-0100 06/01/2026 PAGE 4 OF 9\n" + NOTE
    assert S.classify_new({1: page}, 1, seen, covers_out=covers)[1] == (S.EXCLUDED, S.ALREADY)
    row = page_rows._decided_row("d1", "", 1, S.READ, S.SUSPECTED[S.ALREADY], covers[1], None)
    _suspect(row, page_rows.TEXT_OVERLAP)
    assert row["evidence"]["cover"] == {"exhibit": 3, "page": 2, "exact": False}
    run = page_rows._decided_row(
        "d1", "", 1, S.READ, S.SUSPECTED[S.ALREADY], S.Cover(S.DELIVERED_SRC, "exhibit:2", 1, S.BY_RUN, last=2), None
    )
    _suspect(run, page_rows.RUN_OF_PAGES)
    assert run["evidence"]["cover"] == {"exhibit": 2, "page": 1, "last": 2, "exact": False}
    assert set(page_rows._RULE) == {S.BY_PICTURE, S.BY_TEXT, S.BY_RUN, S.BY_NEIGHBOUR, S.BY_TWIN, S.BY_EXTRACT}
    nameless = page_rows._decided_row(
        "d1", "", 1, S.READ, S.SUSPECTED[S.OVERLAP], None, None
    )  # a match that names no page
    assert not PR.charged(nameless) and nameless["evidence"]["cover"] is None
    assert PR.charged(page_rows._decided_row("d1", "", 1, S.READ, "new document", None, None))


def test_a_twin_found_by_its_neighbours_is_a_suspect_and_no_transcript_changes(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """Page 2 of the copy has no picture match; it sits where its neighbour's
    twin puts it, and its reading adds nothing."""
    sr = _sr(job_dir, firm_config_path, data_root)
    records = {"large": [_record(5), _record(6), _record(7)]}
    missed = "\n".join(x for x in _record(6).split("\n") if not x.startswith("Pain"))
    records["small"] = [_record(5), missed + "\nillegible"]  # one value missed, one word of its own
    rows = []
    for rid, pages in records.items():
        path = sr.slug_dir / f"{rid}.pdf"
        path.write_bytes(make_pdf(pages))
        body = "".join(f"[p.{i}]\n{t}\n" for i, t in enumerate(pages, 1))
        (sr.slug_dir / "text" / f"{rid}.txt").write_text(body, encoding="utf-8")
        rows.append({"id": rid, "name": f"Example {rid}", "ext": ".pdf", "path": str(path)})
    ident = PG.Loaded(pages={"small": {1: PG.Page(S.OVERLAP, "large", 1, False)}})
    before = (sr.slug_dir / "text" / "large.txt").read_text()
    got = US._classify(sr, rows, S.Delivered(), ident, lambda _o, _p: "", {})
    record = page_rows.selection_rows(rows, got, {}, {}, ident, {}, lambda _r: 0)
    by = {(r["doc"], r["page"]): r for r in record}
    assert not PR.charged(by[("small", 1)]) and by[("small", 2)]["status"] == PR.READ
    _suspect(by[("small", 2)], page_rows.TWIN_BY_WORDS)
    assert by[("small", 2)]["evidence"]["cover"] == {"doc": "large", "page": 2, "exact": False}
    assert (sr.slug_dir / "text" / "large.txt").read_text() == before


def test_a_words_extract_is_read_whole_its_odd_page_too_and_charges_nothing(tmp_path: Path) -> None:
    """Nine of ten pages are the whole record's; the tenth states nothing of
    its own. The document was set aside; it is read, all ten pages suspects."""
    whole = [visit(i) for i in range(12)]
    odd = "Thank you for your request. These records are complete as of the date they were released to your office."
    sr = words_seed(tmp_path, {"ext": [*whole[:9], odd], "whole": whole})
    assert US.run(sr) == 0, sr.logged  # type: ignore[arg-type]
    rows = _rows_of(tmp_path)
    for p in range(1, 11):
        _suspect(rows[("ext", p)], page_rows.WORDS_EXTRACT)
        assert rows[("ext", p)]["evidence"]["cover"]["page"] == (p if p < 10 else None)
    assert charged.charged_pages(tmp_path, "alpha", 0) == 12


# ---- 2c. a run with no page record charges no more than it did -----------------------------


def test_the_fallback_charge_leaves_out_suspected_copies(tmp_path: Path) -> None:
    ud = tmp_path / "runs" / "alpha" / "update"
    ud.mkdir(parents=True)
    (tmp_path / "extracted.jsonl").write_text(json.dumps({"id": "d1", "pages": 100}) + "\n")
    rec = {"pages": 100, "read": list(range(1, 101)), "suspected": list(range(1, 31)), "excluded": {}}
    (ud / "selection.json").write_text(json.dumps({"exhibits": [rec]}))
    assert charged.read_pages(tmp_path, "alpha") is None  # no page record
    assert charged.charged_pages(tmp_path, "alpha", 100) == 70
    # every page a copy of delivered pages: re-filed whole before, left out whole still, blank pages too
    of_delivered = {1: (S.READ, S.SUSPECTED[S.ALREADY]), 2: (S.EXCLUDED, S.BLANK)}
    of_new = {1: (S.READ, S.SUSPECTED[S.OVERLAP]), 2: (S.EXCLUDED, S.BLANK)}
    for decisions, left in ((of_delivered, 98), (of_new, 99)):
        (ud / "selection.json").write_text(json.dumps({"exhibits": [S.row(2, "x", decisions, {})]}))
        assert charged.charged_pages(tmp_path, "alpha", 100) == left


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
    found = PG.load(ud).pages["d1"]
    c = US._Read(Path("x.pdf"), {1: PI.SKIPPED, 2: NOTE}, {}, False, None)
    c.decisions = S.classify_new(c.pages, 2, S.Delivered(), image=PG.reasons(found, c.pages, lambda _o, _p: NOTE))
    SP.read_suspects(c, found)
    assert c.decisions == {1: (S.EXCLUDED, S.ALREADY), 2: (S.READ, S.SUSPECTED[S.ALREADY])}
