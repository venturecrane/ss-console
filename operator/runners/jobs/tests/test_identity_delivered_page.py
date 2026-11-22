"""A page already delivered as the client's own exhibit is never another
person's (2026-11-17).

WHY. An update withheld a whole new file by the identity rule because the
text layer over two page images misread the client's date of birth. Those two
pages, and 22 more, were pixel-equal to pages of an exhibit already delivered
on this matter as the client's own. The earlier page-read check could not
help: a page pixel-equal to a delivered page is never sent to vision, so no
transcription existed to clear the finding.

THE RULE. ``update_select._page_findings`` produces no finding for a page
whose picture record (``pageid_io``) is an EXACT match to a page of a
DELIVERED exhibit. A near match, an exact match to another new document's
page, and a missing record all leave the finding as it was.

Every name and date here is invented; the job's client is Alpha Example,
born 01/01/1970.
"""

from __future__ import annotations

import json
from pathlib import Path

from jobs.medchron import config as config_mod, job as job_mod
from jobs.medchron.gates import foreign_identity as FI
from jobs.medchron.update import pageid as PI, pageid_io as PG, select as S
from jobs.stages import update_select as US
from jobs.stages.base import StageRun
from jobs_testkit import FakeSeat

RID = "nf1"
NPAGES = 34
DELIVERED_PAGES = range(2, 26)  # pp.2-25: exact picture copies of delivered exhibit pages
PLAIN = "Follow-up examination. Patient: Alpha Example. Neck pain improving."
OCR_MISREAD = "Patient: Alpha Example DOB: 01/01/1976 Assessment: cervical strain."


def _sr(job_dir: Path, firm: Path, data_root: Path) -> StageRun:
    job = job_mod.load(job_dir)
    return StageRun(
        job=job,
        cfg=config_mod.load(str(firm)),
        unit=job.units[0],
        slug_dir=data_root / "example-matter",
        decided={},
        log=lambda _m: None,
        seat_factory=lambda: FakeSeat(),
    )


def _scene(sr: StageRun, hits: tuple[int, ...], picture: dict[int, PI.Hit] | None) -> tuple[list[dict], Path]:
    """A 34-page new file whose text layer names another person on `hits`;
    `picture` is its page picture record (None: no record written). No page
    has a vision transcript."""
    sd = sr.slug_dir
    (sd / "text").mkdir(parents=True, exist_ok=True)
    body = "".join(f"[p.{n}]\n{OCR_MISREAD if n in hits else PLAIN}\n" for n in range(1, NPAGES + 1))
    (sd / "text" / f"{RID}.txt").write_text(body, encoding="utf-8")
    ud = sd / "runs" / "alpha" / "update"
    ud.mkdir(parents=True, exist_ok=True)
    if picture is not None:
        ident = PI.Identity(pages={RID: picture})
        (ud / PG.NAME).write_text(json.dumps(PI.to_json(ident)), encoding="utf-8")
    return [{"id": RID, "name": "Example Clinic Records", "ext": ".pdf"}], ud


def _delivered(exact: bool = True, other: str = "exhibit:4") -> dict[int, PI.Hit]:
    return {n: PI.Hit(PI.ALREADY, other, n + 10, exact) for n in DELIVERED_PAGES}


def _withheld(sr: StageRun, rows: list[dict], ud: Path) -> bool:
    skipped: dict[str, list[str]] = {}
    kept = US._leave_out_foreign(sr, ud, rows, skipped)
    out = not kept
    assert out == (RID in skipped.get(FI.RULE_REASONS[FI.RULE_ANOTHER_PERSON], []))
    return out


def test_a_misread_on_pages_already_delivered_as_the_clients_does_not_withhold(
    job_dir, firm_config_path, data_root
) -> None:
    sr = _sr(job_dir, firm_config_path, data_root)
    rows, ud = _scene(sr, (18, 24), _delivered())
    assert not _withheld(sr, rows, ud)
    assert not FI.withheld(sr.slug_dir)
    ident = PG.load(ud)
    exempt = US._delivered_pages(ident, RID)
    assert exempt == set(DELIVERED_PAGES) and len(exempt) == 24
    assert 1 not in exempt and not exempt & set(range(26, NPAGES + 1))
    surnames, dobs = FI.identities(sr.job.units)
    assert US._page_findings(sr, RID, surnames, dobs, exempt) == []
    # The picture record still skips pp.2-25 as already read; pp.1 and 26-34 are read.
    already = PG.aside(ident.pages[RID])
    assert sorted(already) == list(DELIVERED_PAGES) and set(already.values()) == {S.ALREADY}
    assert sorted(set(range(1, NPAGES + 1)) - set(already)) == [1, *range(26, NPAGES + 1)]


def test_a_finding_on_a_page_not_delivered_still_withholds(job_dir, firm_config_path, data_root) -> None:
    for page in (1, 26):
        sr = _sr(job_dir, firm_config_path, data_root / f"p{page}")
        rows, ud = _scene(sr, (18, 24, page), _delivered())
        assert _withheld(sr, rows, ud)


def test_a_near_picture_match_does_not_exempt_the_page(job_dir, firm_config_path, data_root) -> None:
    sr = _sr(job_dir, firm_config_path, data_root)
    rows, ud = _scene(sr, (18, 24), _delivered(exact=False))
    assert _withheld(sr, rows, ud)
    assert US._delivered_pages(PG.load(ud), RID) == set()


def test_an_exact_match_to_another_new_document_does_not_exempt_the_page(job_dir, firm_config_path, data_root) -> None:
    sr = _sr(job_dir, firm_config_path, data_root)
    picture = {n: PI.Hit(PI.REPEAT, "nf0", n, True) for n in DELIVERED_PAGES}
    rows, ud = _scene(sr, (18, 24), picture)
    assert _withheld(sr, rows, ud)
    assert US._delivered_pages(PG.load(ud), RID) == set()


def test_no_picture_record_keeps_the_finding(job_dir, firm_config_path, data_root) -> None:
    sr = _sr(job_dir, firm_config_path, data_root)
    rows, ud = _scene(sr, (18, 24), None)
    assert _withheld(sr, rows, ud)
    sr2 = _sr(job_dir, firm_config_path, data_root / "bad")
    rows2, ud2 = _scene(sr2, (18, 24), None)
    (ud2 / PG.NAME).write_text("{not json", encoding="utf-8")
    assert _withheld(sr2, rows2, ud2)
