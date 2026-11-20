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


def test_every_page_has_a_text_layer_and_none_prints_it_no_claim(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sr = _sr(job_dir, firm_config_path, data_root)
    pt = _text(sr, {25: ("Assessment: cervical disc displacement", False), 26: ("Neck pain.", False)})
    lines, log = _claims(pt)
    assert lines == [] and log == [{"code": "M50.20", "first": "2026-07-21", "skipped": icd.NOT_LOCATED}]
    # a longer code is never matched inside another one
    pt = _text(sr, {25: ("M50.201", False), 26: ("x", False)})
    assert _claims(pt)[0] == []


def test_a_picture_page_is_cited_when_no_text_layer_prints_it(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """A scanned page has no text layer: the claim goes to the audit, which
    reads the picture, as before the text-layer lookup existed."""
    sr = _sr(job_dir, firm_config_path, data_root)
    # p. 26 (first cited) has a text layer without the code; p. 25 is a picture
    pt = _text(sr, {25: ("Assessment: M50.20", True), 26: ("Neck pain.", False)})
    lines, log = _claims(pt)
    assert lines == ["Diagnosis code M50.20 recorded on 07/21/2026. (Exhibit 3 - p. 25)"]
    assert log[0]["page"] == "no text layer"
    # a text layer that prints it wins over an earlier picture page
    pt = _text(sr, {25: ("Dx M50.20", False), 26: ("scan", True)})
    lines, log = _claims(pt)
    assert lines == ["Diagnosis code M50.20 recorded on 07/21/2026. (Exhibit 3 - p. 25)"]
    assert log[0]["page"] == "text layer"


def test_a_bare_code_and_an_undotted_print_are_matched(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    sr = _sr(job_dir, firm_config_path, data_root)
    pt = _text(sr, {25: ("Assessment: I10 essential hypertension", False), 26: ("Neck pain.", False)})
    lines, _ = _claims(pt, "2026-07-21 | Example Clinic | I10 | clinic.pdf")
    assert lines == ["Diagnosis code I10 recorded on 07/21/2026. (Exhibit 3 - p. 25)"]
    # the page prints the dotted code without its dot
    pt = _text(sr, {25: ("Dx: M5020 cervical disc", False), 26: ("Neck pain.", False)})
    assert _claims(pt)[0] == ["Diagnosis code M50.20 recorded on 07/21/2026. (Exhibit 3 - p. 25)"]
    assert icd.dotted("I10") is None and icd.dotted("M5020") == "M50.20"


# ---- the table row finds the entry its one-page claim came from (2026-11-20 review) ----
LUMBAGO = "07/21/2026\nExample Clinic | Medical Diagnoses\n\nMedical Diagnoses\nLumbago was assessed after the fall. "


def _row(job_dir: Path, firm: Path, data_root: Path, entry: str, pages: dict[int, tuple[str, bool]]) -> list[str]:
    """A numeric ICD-9 code on a 2026 record, through the stage's text-layer
    reader, `claims`, the audit document's claim line and `rows`, with the
    real tables (described only when its entry names the description)."""
    from jobs.medchron import icd_tables

    d = data_root / "controls" / "icd"
    d.mkdir(parents=True)
    (d / "icd10cm_order.txt").write_text("{:<5} {:<7} {} {:<60} {}\n".format("00001", "M542", "1", "Cx", "Cervicalgia"))
    (d / "CMS32_DESC_LONG_DX.txt").write_text("7242 Lumbago\n")
    t = icd_tables.load(data_root)

    def res(c: str, on: str, text: str) -> tuple[str, str | None, str]:
        return icd_tables.resolve(c, t, on, text)

    pt = _text(_sr(job_dir, firm, data_root), pages)
    lines, _ = icd.claims(["2026-07-21 | Example Clinic | 724.2 | clinic.pdf"], [entry], [], res, pt)
    _, audited = icd.split_claims("\n\n".join(lines))
    return icd.rows([], audited, [entry], res)[0][1:]


def test_a_page_inside_a_list_citation_finds_its_entry(job_dir: Path, firm_config_path: Path, data_root: Path) -> None:
    entry = LUMBAGO + "(Exhibit 3 - p. 3, 4)"
    pages = {3: ("Low back pain.", False), 4: ("Dx 724.2 lumbago", False)}
    rows = _row(job_dir, firm_config_path, data_root, entry, pages)
    assert rows == ["| 724.2 | Lumbago (ICD-9-CM) | 07/21/2026 | Exhibit 3 - p. 4 |"]


def test_the_code_in_a_second_citation_group_finds_its_entry(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    entry = LUMBAGO + "(Exhibit 3 - p. 3)\n\nAssessment\nBack strain. (Exhibit 3 - p. 5-6)"
    pages = {3: ("Low back pain.", False), 5: ("x", False), 6: ("724.2 lumbago", False)}
    rows = _row(job_dir, firm_config_path, data_root, entry, pages)
    assert rows == ["| 724.2 | Lumbago (ICD-9-CM) | 07/21/2026 | Exhibit 3 - p. 6 |"]
    assert icd.cites_page(entry, "Exhibit 3 - p. 6") and not icd.cites_page(entry, "Exhibit 3 - p. 7")
    assert not icd.cites_page(entry, "Exhibit 4 - p. 3")
