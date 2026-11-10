"""A Treatment Timeline lane is referenced by its own exhibit (2026-11-09)."""

from __future__ import annotations

from jobs.medchron.lane_ref import lane_references


def test_a_lane_whose_first_citation_is_another_lanes_exhibit_skips_it():
    """The imaging lane's earliest entry cited the orthopaedist's referral
    (Exhibit 7, which the orthopaedist's lane cites throughout) before its
    own report (Exhibit 8)."""
    refs = lane_references(
        {
            "Orthopaedics": ["Seen. (Exhibit 7 - p. 1)", "Seen. (Exhibit 7 - p. 5)", "Referral. (Exhibit 7 - p. 10)"],
            "Imaging": ["MRI ordered. (Exhibit 7 - p. 10) MRI performed. (Exhibit 8 - p. 12-14)"],
        }
    )
    assert refs == {"Orthopaedics": "Exhibit 7 - p. 1", "Imaging": "Exhibit 8 - p. 12-14"}


def test_a_lane_with_one_exhibit_per_visit_keeps_its_earliest_visit():
    """A therapist with an exhibit per visit keeps the first visit's exhibit,
    however often a later one is cited."""
    refs = lane_references({"PT": ["V1. (Exhibit 19 - p. 1)", "V2. (Exhibit 21 - p. 1) (Exhibit 21 - p. 2)"]})
    assert refs == {"PT": "Exhibit 19 - p. 1"}


def test_a_lane_with_only_another_lanes_exhibit_keeps_its_first_citation():
    """A physician whose records sit inside the hospital's exhibit."""
    refs = lane_references(
        {
            "Hospital": ["A. (Exhibit 2 - p. 1)", "B. (Exhibit 2 - p. 9)"],
            "Physician": ["Consult. (Exhibit 2 - p. 96)"],
            "Nothing": ["No citation."],
        }
    )
    assert refs == {"Hospital": "Exhibit 2 - p. 1", "Physician": "Exhibit 2 - p. 96", "Nothing": ""}
