"""A Treatment Timeline lane is referenced by its own exhibit (2026-11-09)."""

from __future__ import annotations

from jobs.medchron.lane_ref import lane_reference


def test_a_lane_whose_first_entry_cites_another_providers_exhibit_first_uses_its_own():
    """An imaging lane's earliest entry cited the ordering physician's
    referral (Exhibit 7) before its own report (Exhibit 8); every later
    entry cites Exhibit 8. The lane is referenced by Exhibit 8."""
    texts = [
        "MRI ordered. (Exhibit 7 - p. 10) MRI of the shoulder performed. (Exhibit 8 - p. 1)",
        "MRI of the cervical spine. (Exhibit 8 - p. 3)",
        "MRI of the lumbar spine. (Exhibit 8 - p. 5-6)",
    ]
    assert lane_reference(texts) == "Exhibit 8 - p. 1"


def test_a_single_exhibit_lane_keeps_its_first_citation():
    assert lane_reference(["Seen. (Exhibit 3 - p. 2)", "Seen again. (Exhibit 3 - p. 9)"]) == "Exhibit 3 - p. 2"


def test_a_tie_goes_to_the_earliest_cited_exhibit_and_no_citation_is_empty():
    assert lane_reference(["A. (Exhibit 4 - p. 1)", "B. (Exhibit 5 - p. 2)"]) == "Exhibit 4 - p. 1"
    assert lane_reference(["No citation."]) == ""
