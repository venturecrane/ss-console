"""ADR 0104 W8: a chronology's counts and codes come from the entries on disk, the
compose prompt pays only for judgment, and a job composed in the old shape still
reads. Each rule here has a mutation in the PR body that this file catches."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from jobs.medchron import rehearsal
from jobs.medchron.prompts import raw as raw_prompt
from jobs.medchron.update.collect import index_rows as update_index_rows
from jobs.stages import (
    assemble,
    billing,
    build_doc,
    compose as compose_stage,
    coverage,
    group,
    icd_claims,
    map_counts,
    review_tables,
    summarize,
)
from jobs.stages.base import StageRun
from test_final_stages import DOC
from test_finish_doc import ENTRIES, _icd_tables, _sr, firm  # noqa: F401 - firm is a pytest fixture this file uses by name
from test_review_proof import FakeDoorway, _package, _settle_review, _verdict

NEW_MAP = (
    "## ENTRIES\n"
    "01/02/2026\nExample Clinic | Medical Diagnoses\n\n"
    "Cervical strain. (FILE: clinic note.pdf, p. 2)\n\n"
    "ICD codes: S13.4XXA, M54.2\n\n"
    "01/09/2026\nExample Clinic | Treatment Recommendations\n\n"
    "Continue therapy. (FILE: clinic note.pdf, p. 3)\n\n"
    "A copy of the plan appears in the letter. (FILE: letter.pdf, p. 1)\n\n"
    "## FILES-SEEN\n"
    "=== FILE: signature.jpg (fileId sig) === | nothing extractable: an email signature image\n"
    "=== FILE: problem list.pdf (fileId plist) === | nothing extractable: no dated encounter\n"
)
OLD_MAP = (
    "## ENTRIES\n01/20/2026\nExample Clinic | Medical Diagnoses\n\n"
    "Cervical strain. (FILE: clinic note.pdf, p. 3)\n\n"
    "## INDEX\n2026-01-20 | Example Clinic | S13.4XXA | clinic note.pdf\n\n"
    "## BILLING-DATES\n2026-01-20 | Example Clinic | (FILE: bill.pdf, p. 1)\n\n"
    "## CONFLICTS / REFERENCED-BUT-ABSENT\nnone observed\n\n"
    "## FILES-SEEN\n"
    "=== FILE: clinic note.pdf (fileId note) === entries: 3\n"
    "=== FILE: bill.pdf (fileId bill) === entries: 0, billing-dates: 7\n"
    "=== FILE: old list.pdf (fileId olist) === entries: 0\n"
    "=== FILE: claimed.pdf (fileId claimed) === entries: 2\n"
)
FILES = [
    {"id": "note", "name": "clinic note", "ext": ".pdf"},
    {"id": "letter", "name": "letter", "ext": ".pdf"},
    {"id": "sig", "name": "signature", "ext": ".jpg"},
    {"id": "plist", "name": "problem list", "ext": ".pdf"},
    {"id": "silent", "name": "silent", "ext": ".pdf"},
]
OLD_FILES = [
    {"id": "note", "name": "clinic note", "ext": ".pdf"},
    {"id": "bill", "name": "bill", "ext": ".pdf"},
    {"id": "olist", "name": "old list", "ext": ".pdf"},
    {"id": "claimed", "name": "claimed", "ext": ".pdf"},
]


def _run_dir(tmp_path: Path, text: str, name: str = "map-01.md") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / name).write_text(text)
    return tmp_path


# ---- the prompt asks only for judgment -------------------------------------------
def test_the_prompt_no_longer_asks_for_what_code_holds_or_nothing_reads() -> None:
    text = raw_prompt("map-system")
    assert "exactly these two blocks" in text
    for gone in ("## INDEX", "BILLING-DATES", "CONFLICTS", "entries: N", "billing-dates: N"):
        assert gone not in text, gone
    assert "`ICD codes: <codes as recorded, comma-separated>`" in text
    assert "`no dated encounter`" in text and "`billing papers only`" in text


def test_a_two_block_map_is_well_formed_and_a_five_block_one_still_is() -> None:
    assert compose_stage.well_formed(NEW_MAP) and compose_stage.well_formed(OLD_MAP)
    assert not compose_stage.well_formed("## ENTRIES\nnone in this chunk\n")
    assert not compose_stage.well_formed("## INDEX\n\n## FILES-SEEN\nnone\n")


def test_an_unread_page_still_reads_as_explained(tmp_path: Path) -> None:
    hdr = "=== FILE: sane exam.pdf (fileId f8) ==="
    compose_stage.record_unread(tmp_path, "2", f"{hdr}\n[p.1]\nx\n", "refused", "map-02.md")
    text = (tmp_path / "map-02.md").read_text()
    assert compose_stage.well_formed(text) and "## INDEX" not in text
    files = [{"id": "f8", "name": "sane exam", "ext": ".pdf"}]
    assert coverage.composer_dispositions(tmp_path, files)["sane exam.pdf"].startswith("composer: not read (")


# ---- counts come from the entries --------------------------------------------------
def test_file_entry_counts_counts_entries_not_citations(tmp_path: Path) -> None:
    d = _run_dir(tmp_path, NEW_MAP)
    assert assemble.file_entry_counts(d, FILES) == {"clinic note.pdf": 2, "letter.pdf": 1}


def test_a_shortened_citation_counts_for_the_file_it_resolves_to(tmp_path: Path) -> None:
    body = "## ENTRIES\n01/02/2026\nExample Clinic | Medical Diagnoses\n\nStrain. (FILE: clinic note, p. 2)\n"
    assert assemble.file_entry_counts(_run_dir(tmp_path, body), FILES) == {"clinic note.pdf": 1}


def test_a_bare_citation_of_a_shared_name_means_the_copy_its_chunk_carried(tmp_path: Path) -> None:
    """The map no longer echoes a header it drew entries from, so the chunk text
    says which copy a bare citation of a shared name means."""
    files = [{"id": "a", "name": "image", "ext": ".jpg"}, {"id": "b", "name": "image", "ext": ".jpg"}]
    body = "## ENTRIES\n01/02/2026\nExample Clinic | Medical Diagnoses\n\nX. (FILE: image.jpg)\n\n## FILES-SEEN\nnone\n"
    d = _run_dir(tmp_path, body, "map-03-2.md")
    (d / "chunk-03-2.txt").write_text("=== FILE: image.jpg (fileId b) ===\n[p.1]\nX\n")
    (d / "units.json").write_text("{}")
    e = assemble.parse_maps(d, ["map-03-2.md"])[0]
    assert e["seen"] == {"image.jpg": {"b"}}
    assert assemble.file_entry_counts(d, files) == {"image.jpg": 1}
    r = assemble.Resolver(["image.jpg", "image.jpg"], {"a": "image.jpg", "b": "image.jpg"})
    r.scope = e["seen"]
    assert r("image.jpg") == "image.jpg"


def test_the_gate_reads_code_counts_and_only_a_stated_reason_from_the_composer(tmp_path: Path) -> None:
    d = _run_dir(tmp_path, NEW_MAP)
    assert coverage.entry_counts(d, FILES) == {"clinic note.pdf": 2, "letter.pdf": 1, "problem list.pdf": 0}
    said = coverage.composer_dispositions(d, FILES)
    assert said == {"signature.jpg": "composer: an email signature image"}, "no dated encounter is the rule's, not this"


def test_the_no_encounter_reason_is_read_however_it_was_stopped(tmp_path: Path) -> None:
    body = "## ENTRIES\nnone in this chunk\n\n## FILES-SEEN\n=== FILE: x.pdf (fileId x) === | nothing extractable: No dated encounter.\n"
    d = _run_dir(tmp_path, body)
    files = [{"id": "x", "name": "x", "ext": ".pdf"}]
    assert coverage.entry_counts(d, files) == {"x.pdf": 0} and coverage.composer_dispositions(d, files) == {}


def test_a_cited_file_a_composer_called_zero_is_a_source(tmp_path: Path) -> None:
    body = (
        "## ENTRIES\n01/02/2026\nExample Clinic | Medical Diagnoses\n\nStrain. (FILE: clinic note.pdf, p. 2)\n\n"
        "## FILES-SEEN\n=== FILE: clinic note.pdf (fileId note) === entries: 0\n"
    )
    assert coverage.entry_counts(_run_dir(tmp_path, body), FILES) == {"clinic note.pdf": 1}


def test_an_old_map_reads_as_it_did_where_no_entry_cites_the_file(tmp_path: Path) -> None:
    """The compatibility promise: a kept or in-flight job's `entries: N` is still
    its account of a file no entry cites, so an `entries: 0` is still the rule and
    an `entries: 2` nothing backs is still a source that holds if uncited."""
    d = _run_dir(tmp_path, OLD_MAP)
    assert coverage.entry_counts(d, OLD_FILES) == {
        "clinic note.pdf": 1,
        "bill.pdf": 0,
        "old list.pdf": 0,
        "claimed.pdf": 2,
    }
    assert coverage.composer_dispositions(d, OLD_FILES)["bill.pdf"].startswith("evidenced billing dates only (7)")


def test_a_job_in_flight_across_the_release_reads_both_shapes_at_once(tmp_path: Path) -> None:
    """Chunks composed before the release, and a repair or a resumed chunk after it."""
    d = _run_dir(tmp_path, OLD_MAP, "map-01.md")
    (d / "map-02.md").write_text(NEW_MAP)
    files = OLD_FILES + [f for f in FILES if f["id"] != "note"]
    counts = coverage.entry_counts(d, files)
    assert counts["clinic note.pdf"] == 3, "an entry in the old map, two in the new"
    assert counts["old list.pdf"] == 0 and counts["problem list.pdf"] == 0 and counts["letter.pdf"] == 1
    assert assemble.code_rows(d) == [
        "2026-01-20 | Example Clinic | S13.4XXA | clinic note.pdf",
        "2026-01-02 | Example Clinic | S13.4XXA, M54.2 | --",
    ]
    assert len(assemble.parse_maps(d, assemble.map_names(d))) == 3


def test_a_file_with_no_entry_and_no_account_is_absent_so_the_gate_holds_it(tmp_path: Path) -> None:
    d = _run_dir(tmp_path, NEW_MAP)
    assert "silent.pdf" not in coverage.entry_counts(d, FILES)
    assert "silent.pdf" not in coverage.composer_dispositions(d, FILES)


def test_rule_rows_names_a_no_encounter_file_and_keeps_a_cited_one_out(job_dir, firm, data_root) -> None:
    sr = _sr(job_dir, firm, data_root)
    rd = sr.slug_dir / "runs" / "alpha"
    rd.mkdir(parents=True)
    (sr.slug_dir / "units").mkdir(parents=True)
    (sr.slug_dir / "units" / "alpha.json").write_text(json.dumps(FILES))
    (rd / "map-01.md").write_text(NEW_MAP)
    rows = coverage.rule_rows(sr.slug_dir, "alpha", sr.cfg)
    assert [(r["name"], r["reason"]) for r in rows if r["name"] == "problem list.pdf"] == [
        ("problem list.pdf", coverage.NO_ENCOUNTER)
    ]


# ---- the codes come off the entry, and never reach a later stage --------------------
def test_the_codes_line_is_taken_off_the_entry_and_kept_for_the_table(tmp_path: Path) -> None:
    d = _run_dir(tmp_path, NEW_MAP)
    got = assemble.parse_maps(d, ["map-01.md"])
    assert got[0]["codes"] == "S13.4XXA, M54.2" and got[1]["codes"] == ""
    assert all("ICD codes" not in e["text"] for e in got)
    assert assemble.code_rows(d) == ["2026-01-02 | Example Clinic | S13.4XXA, M54.2 | --"]
    assert update_index_rows(d) == assemble.code_rows(d), "the update lane reads the same rows"


def test_a_cited_sentence_that_opens_with_the_words_is_prose(tmp_path: Path) -> None:
    text, codes = assemble.take_codes("01/02/2026\nX | Y\n\nICD codes: S13 per the note. (FILE: a.pdf, p. 1)")
    assert codes == "" and "ICD codes: S13 per the note." in text


def test_an_old_maps_index_rows_are_still_its_codes(tmp_path: Path) -> None:
    d = _run_dir(tmp_path, OLD_MAP)
    assert assemble.code_rows(d) == ["2026-01-20 | Example Clinic | S13.4XXA | clinic note.pdf"]
    assert build_doc.icd_codes(d, {"2026-01-20"})["S134XXA"] == ("2026-01-20", "S13.4XXA", "Example Clinic")


# ---- group attributes from parsed entries --------------------------------------------
def test_group_attributes_from_the_entries_when_there_is_no_index(tmp_path: Path) -> None:
    body = (
        "## ENTRIES\n01/02/2026\nExample Imaging | Medical Diagnoses\n\nMRI. (FILE: mri report, p. 2)\n\n"
        "## FILES-SEEN\nnone\n"
    )
    d = _run_dir(tmp_path, body)
    dates, provs = group.index_rows(d, {"m": "mri report.pdf"})
    assert provs["mri report.pdf"] == ["Example Imaging"], "the shortened citation resolved"
    assert dates["mri report.pdf"] == ["2026-01-02"]


def test_an_old_index_row_still_outvotes_a_citation(tmp_path: Path) -> None:
    body = OLD_MAP.replace("01/20/2026\nExample Clinic", "01/20/2026\nOther Name")
    _dates, provs = group.index_rows(_run_dir(tmp_path, body), {"note": "clinic note.pdf"})
    assert provs["clinic note.pdf"] == ["Example Clinic"]


# ---- a diagnosis code is an audited claim -------------------------------------------
NEW_CODES = (
    "## ENTRIES\n01/02/2026\nExample Clinic | Medical Diagnoses\n\nX. (FILE: a.pdf, p. 1)\n\n"
    "ICD codes: S13.4XXA, M54.2\n\n01/09/2026\nEx Clinic | Medical Diagnoses\n\nY. (FILE: a.pdf, p. 2)\n\n"
    "ICD codes: S523.3XXA\n\n## FILES-SEEN\nnone\n"
)


def _built(job_dir, firm_path, data_root) -> tuple[StageRun, Path]:
    sr = _sr(job_dir, firm_path, data_root)
    rd = sr.slug_dir / "runs" / "alpha"
    rd.mkdir(parents=True)
    (rd / "entries_scoped_final.md").write_text(ENTRIES)
    (rd / "map-01.md").write_text(NEW_CODES)
    out = sr.slug_dir / "out" / "alpha"
    out.mkdir(parents=True)
    (out / "page_map.json").write_text(
        json.dumps([{"exhibit": 1, "title": "Exhibit 1 - Example Clinic - 01-02-2026 (Medical Records)", "files": []}])
    )
    _icd_tables(sr.job.data_root)
    assert build_doc.run(sr) == 0
    return sr, rd


S13_CLAIM = "Diagnosis code S13.4XXA recorded on 01/02/2026. (Exhibit 1 - p. 2)"
M54_CLAIM = "Diagnosis code M54.2 recorded on 01/02/2026. (Exhibit 1 - p. 2)"


def test_build_doc_gives_the_audit_each_code_as_a_claim_and_settle_takes_them_out(job_dir, firm, data_root) -> None:
    sr, rd = _built(job_dir, firm, data_root)
    doc = rd / "final-chronology.md"
    md = doc.read_text()
    assert S13_CLAIM in md and M54_CLAIM in md and "S523.3XXA" not in md, "a garbled shape never becomes a claim"
    assert md.index(S13_CLAIM) > md.index("## Medical Chronology") and md.index(S13_CLAIM) < md.index(
        "01/02/2026\nExample"
    )
    icd_claims.settle(sr)
    md = doc.read_text()
    assert "Diagnosis code" not in md
    assert (
        "| S13.4XXA | Sprain of ligaments of cervical spine, initial encounter | 01/02/2026 | Exhibit 1 - p. 2 |" in md
    )
    assert "| M54.2 | Cervicalgia | 01/02/2026 | Exhibit 1 - p. 2 |" in md
    assert "## Medical Chronology\n\nPrior Medical History\n\nChronic neck pain" in md


def test_a_claim_the_audit_dropped_is_a_row_the_firm_does_not_get(job_dir, firm, data_root) -> None:
    sr, rd = _built(job_dir, firm, data_root)
    doc = rd / "final-chronology.md"
    doc.write_text(doc.read_text().replace(M54_CLAIM + "\n\n", ""))
    icd_claims.settle(sr)
    md = doc.read_text()
    assert "M54.2" not in md and "| S13.4XXA |" in md


def test_every_claim_dropped_leaves_an_empty_table_not_the_unaudited_one(job_dir, firm, data_root) -> None:
    sr, rd = _built(job_dir, firm, data_root)
    doc = rd / "final-chronology.md"
    doc.write_text(doc.read_text().replace(M54_CLAIM + "\n\n", "").replace(S13_CLAIM + "\n\n", ""))
    icd_claims.settle(sr)
    md = doc.read_text()
    assert "S13.4XXA" not in md and "M54.2" not in md
    assert "| ICD Code | Description | First Diagnosed | Reference |" in md


def test_settle_is_a_no_op_on_a_document_built_before_it_and_on_a_second_run(job_dir, firm, data_root) -> None:
    sr, rd = _built(job_dir, firm, data_root)
    icd_claims.settle(sr)
    doc = rd / "final-chronology.md"
    once = doc.read_text()
    icd_claims.settle(sr)
    assert doc.read_text() == once, "a second settle finds nothing pending"
    (rd / icd_claims.MARKER).unlink()
    doc.write_text(DOC)
    icd_claims.settle(sr)
    assert doc.read_text() == DOC, "no marker: the table stands as built"


def test_the_audit_stage_settles_only_when_it_passes(monkeypatch, job_dir, firm, data_root) -> None:
    from jobs.stages import audit_loop

    sr, rd = _built(job_dir, firm, data_root)
    doc = rd / "final-chronology.md"
    monkeypatch.setattr(audit_loop, "_rekey", lambda *_a: None)
    monkeypatch.setattr(audit_loop, "Round", lambda *_a, **_k: SimpleNamespace(execute=lambda: 0))
    monkeypatch.setattr(audit_loop, "_record_drops", lambda *_a: None)
    monkeypatch.setattr(audit_loop.coverage, "check", lambda *_a: (False, {"controls_bad": 1}))
    assert audit_loop.run(sr) == 1
    assert S13_CLAIM in doc.read_text(), "a held audit keeps its claims for the rerun"
    monkeypatch.setattr(audit_loop.coverage, "check", lambda *_a: (True, {}))
    assert audit_loop.run(sr) == 0
    assert "Diagnosis code" not in doc.read_text()


def test_codes_with_no_tables_refuse_and_no_codes_need_none(job_dir, firm, data_root) -> None:
    sr = _sr(job_dir, firm, data_root)
    rd = sr.slug_dir / "runs" / "alpha"
    rd.mkdir(parents=True)
    (rd / "entries_scoped_final.md").write_text(ENTRIES)
    (rd / "map-01.md").write_text("## ENTRIES\nnone in this chunk\n\n## FILES-SEEN\nnone\n")
    out = sr.slug_dir / "out" / "alpha"
    out.mkdir(parents=True)
    (out / "page_map.json").write_text("[]")
    assert build_doc.run(sr) == 0, "no code: the tables are not needed"
    assert not (rd / icd_claims.MARKER).exists()
    (rd / "map-01.md").write_text(NEW_CODES)
    assert build_doc.run(sr) == 1


# ---- check 4 is decided in code ------------------------------------------------------
HIGHLIGHT = (
    '| MUST-FIX | 4 summary with no entry | the highlight "Sprain of ligaments of cervical spine" has no entry '
    "behind it | Diagnostic Highlights |"
)
TIMELINE_ROW = (
    '| MUST-FIX | 4 summary with no entry | the row "Example Clinic | 01/20/2026 - 03/30/2026" has no entry '
    "behind it | Treatment Timeline |"
)
HISTORY = (
    '| MUST-FIX | 4 summary with no entry | "chronic pain before the incident" has no entry behind it '
    "| Prior Medical History |"
)


def _md(sr: StageRun) -> str:
    return (sr.slug_dir / "runs" / "alpha" / "final-chronology.md").read_text()


def _findings(sr: StageRun) -> list[str]:
    from jobs.proof import review as proof_review

    return [f["finding"] for f in proof_review.load(sr.slug_dir / "runs" / "alpha")["findings"]]


def test_a_timeline_row_the_entries_compute_is_kept_whatever_the_reviewer_says(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    assert _settle_review(sr, FakeDoorway(_verdict(TIMELINE_ROW))) == 0, log
    assert "| Example Clinic | 01/20/2026 - 03/30/2026 | 2 | Exhibit 1 - p. 2 |" in _md(sr)
    assert any(f.startswith("checked against the entries and kept") for f in _findings(sr))


def test_a_timeline_row_the_entries_disagree_with_is_corrected_from_them(job_dir, firm_config_path, data_root):
    doc = DOC.replace("| 01/20/2026 - 03/30/2026 | 2 |", "| 01/20/2026 - 04/30/2026 | 3 |")
    sr, log = _package(job_dir, firm_config_path, data_root, doc)
    row = '| MUST-FIX | 4 summary with no entry | the row "Example Clinic | 01/20/2026 - 04/30/2026" is wrong | Timeline |'
    assert _settle_review(sr, FakeDoorway(_verdict(row))) == 0, log
    assert "| Example Clinic | 01/20/2026 - 03/30/2026 | 2 | Exhibit 1 - p. 2 |" in _md(sr)
    assert any(f.startswith("corrected from the entries before delivery") for f in _findings(sr))


def test_a_highlight_whose_date_carries_an_entry_is_kept(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    assert _settle_review(sr, FakeDoorway(_verdict(HIGHLIGHT))) == 0, log
    assert "| S13.4XXA | Sprain of ligaments of cervical spine | 01/20/2026 | Exhibit 1 - p. 2 |" in _md(sr)


def test_a_highlight_the_maps_date_later_is_moved_to_their_first_date(job_dir, firm_config_path, data_root):
    doc = DOC.replace(
        "| Sprain of ligaments of cervical spine | 01/20/2026 |",
        "| Sprain of ligaments of cervical spine | 03/30/2026 |",
    )
    sr, log = _package(job_dir, firm_config_path, data_root, doc)
    (sr.slug_dir / "runs" / "alpha" / "map-1.md").write_text(
        "## INDEX\n2026-01-20 | Example Clinic | S13.4XXA | c.pdf\n"
    )
    assert _settle_review(sr, FakeDoorway(_verdict(HIGHLIGHT))) == 0, log
    assert "| S13.4XXA | Sprain of ligaments of cervical spine | 01/20/2026 | Exhibit 1 - p. 2 |" in _md(sr)


def test_a_history_line_the_pre_incident_entries_back_is_kept(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    (sr.slug_dir / "runs" / "alpha" / summarize.PRE_ENTRIES).write_text(
        "01/02/2020\nExample Clinic | Medical History\n\nChronic pain. (Exhibit 1 - p. 1)\n"
    )
    assert _settle_review(sr, FakeDoorway(_verdict(HISTORY))) == 0, log
    assert "chronic pain before the incident. (Exhibit 1 - p. 1)" in _md(sr)
    assert any(f.startswith("checked against the entries and kept") for f in _findings(sr))


def test_a_history_line_citing_a_page_they_never_cite_goes(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    (sr.slug_dir / "runs" / "alpha" / summarize.PRE_ENTRIES).write_text(
        "01/02/2020\nExample Clinic | Medical History\n\nOther. (Exhibit 1 - p. 3)\n"
    )
    assert _settle_review(sr, FakeDoorway(_verdict(HISTORY))) == 0, log
    assert "chronic pain before the incident" not in _md(sr)


def test_a_job_built_before_the_file_existed_keeps_the_old_rule(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    assert _settle_review(sr, FakeDoorway(_verdict(HISTORY))) == 0, log
    assert "chronic pain before the incident" not in _md(sr)


def test_the_pre_incident_pages_are_carried_through_the_strip(job_dir, firm_config_path, data_root, monkeypatch):
    sr, _log = _package(job_dir, firm_config_path, data_root)
    (sr.slug_dir / "runs" / "alpha" / summarize.PRE_ENTRIES).write_text("Old. (Exhibit 1 - p. 5)\n")
    monkeypatch.setattr(review_tables.diag, "page_remap", lambda *_a: {1: {5: 2}})
    assert review_tables.prior_history_line(sr, "History. (Exhibit 1 - p. 2)")
    assert review_tables.prior_history_line(sr, "History. (Exhibit 1 - p. 5)") is None
    assert review_tables.prior_history_line(sr, "No citation at all.") is None


def test_summarize_keeps_the_entries_a_block_summarizes(job_dir, firm, data_root) -> None:
    sr = _sr(job_dir, firm, data_root)
    d = sr.slug_dir / "runs" / "alpha"
    d.mkdir(parents=True)
    pre = "01/02/2020\nExample Clinic | Medical History\n\nOld neck pain. (Exhibit 1 - p. 1)"
    post = "03/30/2026\nExample Clinic | Medical Diagnoses\n\nStrain. (Exhibit 1 - p. 2)"
    (d / "entries_final.md").write_text(pre + "\n\n" + post)
    sr._doorway = FakeDoorway("Neck pain before. (Exhibit 1 - p. 1)")  # type: ignore[assignment]
    assert summarize.run(sr) == 0
    assert (d / summarize.PRE_ENTRIES).read_text() == pre


# ---- the heading is code's in both lanes ----------------------------------------------
def test_summary_block_retries_an_empty_section_then_gives_up(job_dir, firm, data_root) -> None:
    sr = _sr(job_dir, firm, data_root)
    d = FakeDoorway("Prior Medical History\n", "History. (Exhibit 1 - p. 1)")
    sr._doorway = d  # type: ignore[assignment]
    got = summarize.summary_block(sr, ["01/02/2020\nX | Y\n\nA. (Exhibit 1 - p. 1)"], custom_id="update-summarize")
    assert got == "Prior Medical History\n\nHistory. (Exhibit 1 - p. 1)"
    assert [c["custom_id"] for c in d.calls] == ["update-summarize", "update-summarize-retry"]


# ---- billing: code holds the page and the range ---------------------------------------
def test_a_chunks_range_comes_from_its_visits_and_its_first_page_from_the_call() -> None:
    got = billing.settle_chunk(
        {
            "date_first": "01/01/2026",
            "date_last": "12/31/2026",
            "line_items": [
                {"date": "03/09/2026", "description": "visit", "charge": "$10.00"},
                {"date": "01/05/2026", "description": "visit", "charge": "$10.00"},
                {"date": "04/01/2026", "description": "insurance payment", "charge": "-$5.00"},
                {"date": "not legible", "description": "visit", "charge": "$10.00"},
            ],
        },
        7,
    )
    assert (got["date_first"], got["date_last"], got["page_first"]) == ("01/05/2026", "03/09/2026", 7)


def test_with_no_dated_visit_the_printed_range_stands() -> None:
    got = billing.settle_chunk({"date_first": "01/01/2026", "date_last": "01/31/2026", "line_items": []}, 2)
    assert (got["date_first"], got["date_last"], got["page_first"]) == ("01/01/2026", "01/31/2026", 2)


# ---- the rehearsal is the proof -------------------------------------------------------
def _slug(tmp_path: Path, text: str, files: list[dict]) -> Path:
    slug = tmp_path / "slug"
    (slug / "runs" / "alpha").mkdir(parents=True)
    (slug / "units").mkdir()
    (slug / "units" / "alpha.json").write_text(json.dumps(files))
    (slug / "runs" / "alpha" / "map-01.md").write_text(text)
    return slug


def test_the_report_names_every_difference_on_an_old_job(tmp_path: Path) -> None:
    slug = _slug(tmp_path, OLD_MAP, OLD_FILES)
    (slug / "billing_extract.jsonl").write_text(
        json.dumps({"file": "bill", "chunks": [{"line_items": [{"date": "01/20/2026"}, {"date": "01/21/2026"}]}]})
        + "\n"
    )
    lines = map_counts.report(slug, "alpha")
    assert "DIFFERS clinic note.pdf: composer entries: 3, code counts 1" in lines
    assert "DIFFERS claimed.pdf: composer entries: 2, code counts 0" in lines
    assert "DIFFERS bill.pdf: composer billing-dates: 7, billing stage holds 2" in lines
    assert lines[0].endswith("3 difference(s)")
    assert not any(ln.startswith("DIFFERS old list.pdf") for ln in lines), "it agrees"
    assert any("INDEX" in ln and "% of it" in ln for ln in lines)
    table = json.loads((slug / "runs" / "alpha" / map_counts.REPORT).read_text())
    assert {r["file"] for r in table["files"]} == {f["name"] + f["ext"] for f in OLD_FILES}


def test_the_report_on_a_new_job_names_only_the_file_nothing_accounts_for(tmp_path: Path) -> None:
    lines = map_counts.report(_slug(tmp_path, NEW_MAP, FILES), "alpha")
    differing = [ln for ln in lines if ln.startswith("DIFFERS")]
    assert len(differing) == 1 and differing[0].startswith("DIFFERS silent.pdf: no entry cites it")
    assert "1 stated no dated encounter" in lines[0]


def test_a_report_with_no_maps_says_so_rather_than_passing(tmp_path: Path) -> None:
    assert map_counts.report(tmp_path, "alpha") == ["no composed maps on this job; nothing to compare"]


def test_the_answer_key_is_checked_file_by_file(tmp_path: Path) -> None:
    key = {
        "units": {
            "alpha": {
                "files": {
                    "clinic note.pdf": {"entries": 1},
                    "bill.pdf": {"entries": 1, "billing_dates": 9},
                    "gone.pdf": {"entries": 1},
                }
            }
        }
    }
    lines = map_counts.report(_slug(tmp_path, OLD_MAP, OLD_FILES), "alpha", key)
    assert "answer key: 3 file(s) checked, 2 missed, 2 not listed" in lines
    assert "KEY MISS bill.pdf: entries key 1, code 0; billing dates key 9, stage 0" in lines
    assert "KEY MISS gone.pdf: not a file of this unit" in lines
    assert "KEY NOT CHECKED old list.pdf" in lines
    assert not any(ln.startswith("KEY MISS clinic note.pdf") for ln in lines)


def test_a_bad_key_refuses_before_the_copy_and_the_observer_never_raises(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(rehearsal.RehearsalError, match="cannot be read"):
        rehearsal._read_key(str(tmp_path / "missing.json"))
    (tmp_path / "bad.json").write_text("[]")
    with pytest.raises(rehearsal.RehearsalError, match="is not"):
        rehearsal._read_key(str(tmp_path / "bad.json"))
    (tmp_path / "runs" / "u").mkdir(parents=True)
    (tmp_path / "runs" / "u" / "map-01.md").write_text("## ENTRIES\n")
    (tmp_path / "units").mkdir()
    (tmp_path / "units" / "u.json").write_text('[{"no": "name"}]')
    lines = rehearsal.count_lines(tmp_path, "u", None)
    assert lines[0].startswith("could not run: KeyError")


def _delivered_slug(tmp_path: Path, data_root: Path, firm: Path, pricing: Path) -> tuple[Path, Path]:
    from test_rehearse import _delivered

    job_dir, _client = _delivered(tmp_path, data_root, firm, pricing)
    return job_dir, data_root / "example-matter"


def test_a_delivered_old_shape_job_rehearses_and_the_counts_are_on_the_report(
    tmp_path: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    """A delivered job walks to its end with every stage skipped, so no stage
    probe runs. The comparison has to be there regardless: it is the proof."""
    from test_rehearse import _NoNetwork, _no_seat

    job_dir, sd = _delivered_slug(tmp_path, data_root, firm_config_path, pricing_path)
    maps = sd / "runs" / "alpha" / "map-01.md"
    maps.write_text(maps.read_text().replace("entries: 1", "entries: 4"))
    copy, outs = rehearsal.run(
        job_dir,
        firm_config=str(firm_config_path),
        pricing=str(pricing_path),
        log=lambda *_: None,
        seat_factory=_no_seat,
        client=_NoNetwork(),
    )
    notes = [n for n in outs[0].notes if n.startswith("rehearse counts: ")]
    assert any("DIFFERS f1.pdf: composer entries: 4, code counts 1" in n for n in notes), notes
    assert (copy / "data" / "example-matter" / "runs" / "alpha" / map_counts.REPORT).is_file()
    assert not (sd / "runs" / "alpha" / map_counts.REPORT).exists(), "the copy, never the real tree"


def test_the_answer_key_reaches_the_report_from_the_command_line(
    tmp_path: Path, data_root: Path, firm_config_path: Path, pricing_path: Path, capsys, monkeypatch
) -> None:
    from jobs import __main__ as cli

    job_dir, _sd = _delivered_slug(tmp_path, data_root, firm_config_path, pricing_path)
    key = tmp_path / "key.json"
    key.write_text(json.dumps({"units": {"alpha": {"files": {"f1.pdf": {"entries": 9}}}}}))
    rc = cli.main(
        [
            "rehearse",
            str(job_dir),
            "--firm-config",
            str(firm_config_path),
            "--pricing",
            str(pricing_path),
            "--answer-key",
            str(key),
            "--json",
        ]
    )
    verdict = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert any("KEY MISS f1.pdf: entries key 9, code 1" in n for n in verdict[0]["notes"])


def test_settling_carries_the_live_verdicts_and_leaves_the_removed_claims_behind(job_dir, firm, data_root) -> None:
    from jobs.medchron.audit import claims as CL

    sr, rd = _built(job_dir, firm, data_root)
    out = sr.slug_dir / "out" / "alpha"
    (out / "Exhibit 1 - Example Clinic - 01-02-2026 (Medical Records).pdf").write_bytes(b"%PDF")
    body = CL.body_of((rd / "final-chronology.md").read_text())
    every = CL.extract_claims(body, {1})
    sha = CL.doc_sha_of(body)
    results = out / "audit-results.jsonl"
    results.write_text("".join(json.dumps({"key": c["key"], "kind": "real", "doc_sha": sha}) + "\n" for c in every))
    icd_claims.settle(sr)
    now = CL.body_of((rd / "final-chronology.md").read_text())
    new_sha = CL.doc_sha_of(now)
    live = {c["key"] for c in CL.extract_claims(now, {1})}
    rows = CL.read_rows(results)
    assert live and {r["key"] for r in rows if r["doc_sha"] == new_sha} == live, "the live verdicts are not re-billed"
    assert {r["doc_sha"] for r in rows if r["key"] not in live} == {sha}
    assert CL.lineage_orphans(rows, live, new_sha) == [], "the double-sweep guard does not trip on a re-audit"
