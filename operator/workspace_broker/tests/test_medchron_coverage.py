"""A matter's cumulative coverage: per document, the newest delivery decides.

An update REPLACES the delivered chronology, so what the newest delivered row
says about a document is what the firm's document holds. Before 2026-10-13
`uncovered` won across every row, so an id an early delivery could not use
stayed uncovered for good and every later update read it again. Synthetic ids.
"""

from __future__ import annotations

import json

from workspace_broker.medchron_coverage import union_coverage


def _row(created: str, covered: list[str], uncovered: list[str]) -> dict:
    return {
        "matter_number": "900044",
        "created_at": created,
        "updated_at": created,
        "covered_json": json.dumps({"covered": covered, "uncovered": uncovered}),
    }


def test_the_newest_delivery_decides_a_document_and_older_ones_still_count() -> None:
    older = _row("2026-08-28T10:00:00Z", ["b"], ["a"])
    newer = _row("2026-10-13T10:00:00Z", ["a"], ["c"])
    for rows in ([older, newer], [newer, older]):  # the caller's order never decides
        got = union_coverage(rows)
        assert got is not None
        assert got["covered_document_ids"] == ["a", "b"]  # a: the newer row covers it; b: only the older names it
        assert got["uncovered_document_ids"] == ["c"]
        assert got["deliveries"] == 2


def test_a_document_the_newest_delivery_could_not_use_is_read_again() -> None:
    got = union_coverage([_row("2026-08-27", ["shared"], []), _row("2026-09-01", [], ["shared"])])
    assert got is not None and got["covered_document_ids"] == [] and got["uncovered_document_ids"] == ["shared"]
