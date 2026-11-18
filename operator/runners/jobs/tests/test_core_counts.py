"""The run record's counts about an update's core are derived from saved files.

They are read back by the hourly triage as facts about one run, so a stage
that runs twice on a resume must not double them, and a job with no update
files must add nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

from jobs import run_record
from jobs.medchron import core_counts
from jobs.medchron.update import cite_read as CR


def _update_dir(job_dir: Path, unit: str = "alpha") -> Path:
    ud = job_dir / "data" / "firm" / "runs" / unit / "update"
    ud.mkdir(parents=True)
    return ud


def _review(ud: Path) -> None:
    rec = {
        "frozen": [
            {"finding": {"klass": "2 omitted content"}},
            {"finding": {"klass": "2. omitted content"}},
            {"finding": {"klass": "4 duplicate"}},
            {"finding": {"klass": "12 not a class"}},
        ],
        "rounds": [
            {"round": 1, "usage": [{"text_pages": 18, "image_pages": 2}, {"text_pages": 5, "image_pages": 0}]},
            {"round": 2, "usage": [{"text_pages": 1, "image_pages": 3}]},
        ],
    }
    (ud / "review_repair.json").write_text(json.dumps(rec), encoding="utf-8")


def test_the_counts_come_from_the_saved_review_and_collect_records(tmp_path: Path) -> None:
    ud = _update_dir(tmp_path)
    _review(ud)
    (ud / "compose_report.json").write_text(json.dumps({CR.MOVED: 21, CR.DELIVERED: 9, CR.MIXED: 0}), encoding="utf-8")
    assert core_counts.derived(tmp_path) == {
        "review_must_fix": 4,
        "review_must_fix_omitted": 2,  # "12 ..." is class 12, not class 2
        "review_calls": 3,
        "review_text_pages": 24,
        "review_image_pages": 5,
        CR.MOVED: 21,
        CR.DELIVERED: 9,
        CR.MIXED: 0,
    }


def test_reading_twice_gives_the_same_counts(tmp_path: Path) -> None:
    """The reason these are derived, not summed: a resumed stage would double a summed count."""
    _review(_update_dir(tmp_path))
    first = core_counts.with_derived({"checks": {"other": 1}}, tmp_path)
    again = core_counts.with_derived(first, tmp_path)
    assert first == again and first["checks"]["other"] == 1 and first["checks"]["review_must_fix"] == 4


def test_two_units_are_summed(tmp_path: Path) -> None:
    _review(_update_dir(tmp_path, "alpha"))
    _review(_update_dir(tmp_path, "beta"))
    assert core_counts.derived(tmp_path)["review_must_fix_omitted"] == 4


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
    _review(ud)
    (ud / "compose_report.json").write_text(json.dumps({CR.MOVED: 1, CR.DELIVERED: 1, CR.MIXED: 1}), encoding="utf-8")
    blk = run_record.block("end", extra=core_counts.with_derived({}, tmp_path))
    assert blk["checks"]["review_must_fix_omitted"] == 2 and blk["checks"][CR.MOVED] == 1
