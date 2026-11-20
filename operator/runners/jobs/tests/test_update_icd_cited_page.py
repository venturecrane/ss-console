"""A diagnosis claim is cited to the page whose text layer prints the code
(2026-11-20: a code on an entry's second cited page was cited to its first,
graded unsupported and dropped, so the firm's table lost the row). Through
the stage's own text-layer reader over real text/<id>.txt bodies; every value
is invented."""

from __future__ import annotations

from pathlib import Path

from jobs.medchron import config as config_mod, job as job_mod
from jobs.medchron.update import icd
from jobs.stages import update_collect as UC
from jobs.stages.base import StageRun

ENTRY = (
    "07/21/2026\nExample Clinic | Medical Diagnoses\n\nMedical Diagnoses\n"
    "Neck pain after the collision. (Exhibit 3 - p. 26)\n\n"
    "Assessment\nCervical disc displacement was assessed. (Exhibit 3 - p. 25)"
)
ROW = "2026-07-21 | Example Clinic | M50.20 | clinic.pdf"


def _sr(job_dir: Path, firm: Path, data_root: Path) -> StageRun:
    job = job_mod.load(job_dir)
    return StageRun(
        job=job,
        cfg=config_mod.load(str(firm)),
        unit=job.units[0],
        slug_dir=data_root / "example-matter",
        decided={},
        log=lambda _m: None,
        seat_factory=lambda: None,  # type: ignore[arg-type, return-value]
    )


def _text(sr: StageRun, pages: dict[int, tuple[str, bool]]) -> icd.PageText:
    d = sr.slug_dir / "text"
    d.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"[p.{n}]{' (machine transcription)' if mt else ''}\n{t}" for n, (t, mt) in sorted(pages.items()))
    (d / "doc3.txt").write_text(body, encoding="utf-8")
    return UC._text_layer(sr, [{"exhibit": 3, "id": "doc3"}])


def _claims(page_text: icd.PageText, row: str = ROW) -> tuple[list[str], list[dict]]:
    return icd.claims([row], [ENTRY], [], lambda code, on, text: (code, "Described", "table"), page_text)


def test_the_code_on_the_second_cited_page_is_cited_to_that_page(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sr = _sr(job_dir, firm_config_path, data_root)
    pt = _text(sr, {25: ("Assessment: M50.20 cervical disc displacement", False), 26: ("Neck pain.", False)})
    lines, _log = _claims(pt)
    assert lines == ["Diagnosis code M50.20 recorded on 07/21/2026. (Exhibit 3 - p. 25)"]


def test_two_pages_holding_it_cite_the_first_in_citation_order(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sr = _sr(job_dir, firm_config_path, data_root)
    pt = _text(sr, {25: ("Dx M50.20", False), 26: ("Problem list M50.20", False)})
    assert _claims(pt)[0] == ["Diagnosis code M50.20 recorded on 07/21/2026. (Exhibit 3 - p. 26)"]


def test_no_page_holds_it_no_claim_and_the_log_says_so(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    sr = _sr(job_dir, firm_config_path, data_root)
    # p. 25 prints it only in a machine transcription (a picture has no text layer)
    pt = _text(sr, {25: ("Assessment: M50.20", True), 26: ("Neck pain.", False)})
    lines, log = _claims(pt)
    assert lines == [] and log == [{"code": "M50.20", "first": "2026-07-21", "skipped": icd.NOT_LOCATED}]


def test_a_bare_three_character_code_is_not_matched(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    sr = _sr(job_dir, firm_config_path, data_root)
    pt = _text(sr, {25: ("Assessment: M54 neck pain", False), 26: ("M54", False)})
    lines, log = _claims(pt, "2026-07-21 | Example Clinic | M54 | clinic.pdf")
    assert lines == [] and log[0]["skipped"] == icd.NOT_LOCATED
    assert icd.dotted("M54") is None and icd.dotted("M5020") == "M50.20"
    # a longer code is never matched inside another one
    pt = _text(sr, {25: ("M50.201", False), 26: ("x", False)})
    assert _claims(pt)[0] == []
