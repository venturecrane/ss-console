"""The run record's counts about an update's core are derived from saved files.

They are read back by the hourly triage as facts about one run, so a stage
that runs twice on a resume must not double them, and a job with no update
files must add nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

from jobs import run_record
from jobs.medchron import core_counts, graded_proof as GP
from jobs.medchron.update import cite_read as CR


def _update_dir(job_dir: Path, unit: str = "alpha") -> Path:
    ud = job_dir / "data" / "firm" / "runs" / unit / "update"
    ud.mkdir(parents=True)
    return ud


def _collect(ud: Path, moved: int = 21) -> None:
    (ud / "compose_report.json").write_text(
        json.dumps({CR.MOVED: moved, CR.DELIVERED: 9, CR.MIXED: 0}), encoding="utf-8"
    )


def test_the_counts_come_from_the_saved_collect_record(tmp_path: Path) -> None:
    ud = _update_dir(tmp_path)
    _collect(ud)
    assert core_counts.derived(tmp_path) == {CR.MOVED: 21, CR.DELIVERED: 9, CR.MIXED: 0}


def test_an_update_reviewed_before_the_entry_read_adds_no_review_counts(tmp_path: Path) -> None:
    """The update review went on 2026-11-20; a job dir still holding its
    record (``review_repair.json``) is not read for counts any more."""
    ud = _update_dir(tmp_path)
    _collect(ud)
    (ud / "review_repair.json").write_text(json.dumps({"frozen": [{"finding": {"klass": "2"}}]}), encoding="utf-8")
    assert not [k for k in core_counts.derived(tmp_path) if k.startswith("review_")]


def test_reading_twice_gives_the_same_counts(tmp_path: Path) -> None:
    """The reason these are derived, not summed: a resumed stage would double a summed count."""
    _collect(_update_dir(tmp_path))
    first = core_counts.with_derived({"checks": {"other": 1}}, tmp_path)
    again = core_counts.with_derived(first, tmp_path)
    assert first == again and first["checks"]["other"] == 1 and first["checks"][CR.MOVED] == 21


def test_two_units_are_summed(tmp_path: Path) -> None:
    _collect(_update_dir(tmp_path, "alpha"))
    _collect(_update_dir(tmp_path, "beta"), moved=4)
    assert core_counts.derived(tmp_path)[CR.MOVED] == 25


def test_a_job_with_no_update_files_adds_nothing(tmp_path: Path) -> None:
    (tmp_path / "data" / "firm" / "runs" / "alpha").mkdir(parents=True)
    have = {"pages": {"read": 3}}
    assert core_counts.derived(tmp_path) == {} and core_counts.with_derived(have, tmp_path) is have


def test_a_damaged_record_is_read_as_nothing_and_never_raises(tmp_path: Path) -> None:
    ud = _update_dir(tmp_path)
    (ud / "review_repair.json").write_text("{not json", encoding="utf-8")
    (ud / "compose_report.json").write_text(json.dumps([1, 2]), encoding="utf-8")
    assert core_counts.derived(tmp_path) == {}


def test_every_count_name_is_one_the_run_record_accepts(tmp_path: Path) -> None:
    """The broker refuses a run block whose count keys do not match its pattern (``run_meta``)."""
    ud = _update_dir(tmp_path)
    (ud / "compose_report.json").write_text(json.dumps({CR.MOVED: 1, CR.DELIVERED: 1, CR.MIXED: 1}), encoding="utf-8")
    blk = run_record.block("end", extra=core_counts.with_derived({}, tmp_path))
    assert blk["checks"][CR.MOVED] == 1 and blk["checks"][CR.DELIVERED] == 1


def test_the_record_only_proof_findings_reach_the_run_record(tmp_path: Path) -> None:
    """Fails if the proofs' record-only findings are written and never read:
    until 2026-11-18 no caller carried them to the run record."""
    ud = _update_dir(tmp_path)
    GP.write(ud / GP.PROOFS, {"P15_ungraded_text": 2, "P12_mutated": 0}, {"name": "a.docx", "sha256": "0" * 64})
    got = core_counts.derived(tmp_path)
    assert got["proof_p15_ungraded_text"] == 2 and got["proof_p12_mutated"] == 0
    blk = run_record.block("end", extra=core_counts.with_derived({}, tmp_path))
    assert blk["checks"]["proof_p15_ungraded_text"] == 2  # a name the run record accepts


def test_a_unit_with_no_proofs_file_adds_no_proof_counts(tmp_path: Path) -> None:
    _collect(_update_dir(tmp_path))
    assert not [k for k in core_counts.derived(tmp_path) if k.startswith("proof_")]
