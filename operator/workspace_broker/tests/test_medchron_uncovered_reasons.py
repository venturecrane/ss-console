"""Why each uncovered document went unread, on the coverage record (`covered.why`).

2026-10-26 on the A&P seat: asked about two files his update had not read, the
Operator had only `uncovered_count: 2`, guessed they were scans with no text
layer, and was wrong (they were password-protected, and the update knew). The
record now carries a fixed reason token per uncovered id, and the single-job
read exposes it. Tokens only: the ledger holds ids and counts (ADR 0052).
Synthetic ids.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker.medchron_coverage import WHY_TOKENS, parse_why, validate_covered
from workspace_broker.medchron_ledger import MedchronLedger
from workspace_broker.medchron_verbs import _counted

RECORD = {"covered": ["a"], "uncovered": ["b", "c"], "pulled": 3}


def test_a_reason_per_uncovered_id_is_stored() -> None:
    stored = json.loads(validate_covered({**RECORD, "why": {"c": "password_protected", "b": "no_text"}}))
    assert stored["why"] == {"b": "no_text", "c": "password_protected"}
    assert set(WHY_TOKENS) >= set(stored["why"].values())


def test_no_why_is_still_a_valid_record() -> None:
    assert "why" not in json.loads(validate_covered(RECORD))


@pytest.mark.parametrize(
    "why",
    [
        {"a": "no_text"},  # a covered id: a reason for a document the record says was read
        {"z": "no_text"},  # an id in neither set
        {"b": "scan with no text layer"},  # free text, the exact guess this exists to stop
        {"b": "Hospital Encounters.pdf"},  # a file name
        {"b": None},
        ["b"],
    ],
)
def test_a_reason_outside_the_record_or_the_vocabulary_is_refused(why) -> None:
    with pytest.raises(ValueError, match=r"covered\.why"):
        validate_covered({**RECORD, "why": why})


def test_the_projection_exposes_the_reasons() -> None:
    raw = validate_covered({**RECORD, "why": {"c": "password_protected"}})
    out = MedchronLedger.project({"covered_json": raw})
    assert out["uncovered_document_ids"] == ["b", "c"]
    assert out["uncovered_reasons"] == {"c": "password_protected"}


def test_a_row_written_before_reasons_existed_reads_as_none() -> None:
    old = json.dumps({"covered": ["a"], "uncovered": ["b"]})
    assert MedchronLedger.project({"covered_json": old})["uncovered_reasons"] is None
    assert MedchronLedger.project({"covered_json": None})["uncovered_reasons"] is None
    assert parse_why("not json") is None


def test_list_views_carry_counts_and_no_reasons() -> None:
    raw = validate_covered({**RECORD, "why": {"c": "password_protected"}})
    row = _counted(MedchronLedger.project({"covered_json": raw}))
    assert "uncovered_reasons" not in row
    assert row["uncovered_count"] == 2 and row["covered_count"] == 1
