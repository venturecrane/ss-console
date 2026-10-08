"""The litigation lane's config readers (litigation_intake.py), and the parity
of its library-number copy with the drafting lane's."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import drafting_intake, litigation_intake as li

REPO = Path(__file__).resolve().parents[3]

CASES = {
    "authored": "self_initiation:\n  document_library:\n    operator_matter:\n      number: ' OPS-LIB '\n",
    "blank": "self_initiation:\n  document_library:\n    operator_matter:\n      number: '  '\n",
    "missing": "self_initiation: {}\n",
    "not_yaml": ": : :\n",
    "empty": "",
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_library_number_copy_matches_the_drafting_lane(tmp_path, case) -> None:
    path = tmp_path / "customer.yaml"
    path.write_text(CASES[case])
    assert li.operator_library_number(path) == drafting_intake.operator_library_number(path)


def test_recipients_are_a_scalar_list() -> None:
    assert li.scheduled_recipients({"scheduled_recipients": " A@x.example , b@x.example"}) == [
        "a@x.example",
        "b@x.example",
    ]
    assert li.scheduled_recipients({"scheduled_recipients": ["A@x.example"]}) == ["a@x.example"]
    assert li.scheduled_recipients({}) == []


def test_attorney_resolution() -> None:
    roster = {"Avery Stone": "a", "Blake Stone": "b", "Casey Ng": "c"}
    assert li.resolve_attorneys(["casey ng", "Avery"], roster) == (["a", "c"], None)
    assert li.resolve_attorneys(["Stone"], roster)[1]
    assert li.resolve_attorneys(["Dana"], roster)[1]
    assert li.resolve_attorneys([], roster)[1]


def test_budget_must_be_positive() -> None:
    assert li.monthly_budget_cents({"monthly_budget_usd": 750}) == 75000
    for bad in (0, -1, True, "750", None):
        assert li.monthly_budget_cents({"monthly_budget_usd": bad}) is None


def test_the_shipped_seat_authors_the_skill_and_leaves_the_cron_commented() -> None:
    seat = REPO / "operator/customers/ashton-price/customer.yaml"
    entry = li.skill_entry(seat)
    assert entry is not None
    assert li.initiation_allows(entry, "request") and li.initiation_allows(entry, "scheduled")
    settings = li.settings_of(entry)
    assert settings["folder_name"] == "Litigation Status"
    assert li.scheduled_recipients(settings)
    # The weekday row ships commented out: the firm's enable act is the uncomment.
    assert li.cron_row_live(seat) is False
    assert "#   - skill: litigation-status" in seat.read_text() or "# - skill: litigation-status" in seat.read_text()
