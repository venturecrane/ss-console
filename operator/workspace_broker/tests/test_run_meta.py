"""The run block the broker keeps on a chronology transition's audit row
(``workspace_broker/run_meta.py``): whitelisted keys, shape-checked values,
codes and counts only. A matter name, a file name or record text cannot ride it.
"""

from __future__ import annotations

import json

from workspace_broker import run_meta

JOB = "01KTJ0BX0000000000000000AA"
#: Strings a matter, a patient or a record would carry. None may survive a block.
FOREIGN = (
    "Example v. Example",
    "2026-PI-102",
    "Jane Doe 01/02/1980",
    "Records from Desert Clinic.pdf",
    "decision: two files could not be placed (Desert Clinic.pdf)",
    "DOE-JANE",
    "doe jane",
)


def test_a_full_block_keeps_every_whitelisted_key():
    raw = {
        "v": 1,
        "event": "stop",
        "by": "code",
        "stage": "coverage_gate",
        "code": "coverage",
        "kind": "check",
        "release": "img:0123456789ab+jobs:202611170000-abcdef12",
        "replaces": JOB,
        "pages": {"medical": 12, "billing": 3},
        "checks": {"date_of_service": 2},
    }
    assert run_meta.check(raw) == raw


def test_unknown_keys_are_dropped_and_an_unknown_event_is_no_block():
    got = run_meta.check({"event": "end", "by": "code", "matter": "Example v. Example", "text": "a page"})
    assert got == {"v": 1, "event": "end", "by": "code"}
    assert run_meta.check({"event": "anything", "by": "code"}) is None
    assert run_meta.check("end") is None


def test_an_unattributed_step_reads_unknown_never_code():
    """A missing or foreign ``by`` must never grade a run unaided."""
    assert run_meta.check({"event": "resume"})["by"] == "unknown"
    assert run_meta.check({"event": "resume", "by": "Jane"})["by"] == "unknown"


def test_a_matter_string_cannot_enter_a_block():
    """MUTATION: widen any value check in run_meta and one of these leaks."""
    for text in FOREIGN:
        raw = {
            "event": "stop",
            "by": "smd",
            "stage": text,
            "code": text,
            "kind": text,
            "release": text,
            "replaces": text,
            "pages": {text: 1, "medical": 2},
            "checks": {"ok": text},
        }
        got = run_meta.check(raw)
        blob = json.dumps(got)
        assert text not in blob and text.lower() not in blob.replace("uncoded", ""), (text, got)
        assert got["code"] == run_meta.UNCODED
        assert got["release"] == run_meta.UNKNOWN
        assert "stage" not in got and "replaces" not in got and "kind" not in got
        assert got["pages"] == {"medical": 2} and "checks" not in got


def test_take_removes_the_block_from_the_fields_and_add_puts_it_on_the_row():
    fields = {"pages": 3, "run": {"event": "end", "by": "code"}}
    run = run_meta.take(fields)
    assert "run" not in fields and fields == {"pages": 3}
    assert run_meta.add({"job_id": JOB}, run) == {"job_id": JOB, "run": {"v": 1, "event": "end", "by": "code"}}
    assert run_meta.add({"job_id": JOB}, None) == {"job_id": JOB}


def test_counts_refuse_negatives_booleans_and_too_many_names():
    raw = {"event": "end", "by": "code", "pages": {"a": -1, "b": True, "c": 2, **{f"k{i}": 1 for i in range(60)}}}
    pages = run_meta.check(raw)["pages"]
    assert "a" not in pages and "b" not in pages
    assert len(pages) <= run_meta.MAX_COUNTS
