"""A checked list is expanded only against the numbers the form carries."""

from __future__ import annotations

import pytest

from jobs.drafting.forms import checked


def test_expand_ranges_in_form_order() -> None:
    got = checked.expand("6.1-6.7, 2.1-2.13, 9.1")
    assert got[:13] == [f"2.{i}" for i in range(1, 14)]
    assert got[13:20] == [f"6.{i}" for i in range(1, 8)]
    assert got[-1] == "9.1" and len(got) == 21


def test_a_range_runs_over_form_numbers_not_arithmetic() -> None:
    assert checked.expand("4.2-6.2") == ["4.2", "6.1", "6.2"]  # 5.0 is reserved: no 5.x
    assert checked.expand("16.9-16.10") == ["16.9", "16.10"]


@pytest.mark.parametrize(
    "spec,needle",
    [("5.1", "5.1 is not an interrogatory"), ("6.7-6.1", "runs backwards"), ("6.1, 6.1", "more than once"),
     ("6.1-6.3, 6.2", "6.2 is listed more than once"), ("six", "is not an interrogatory number"), ("", "empty"),
     ("2.1-2.14", "2.14 is not an interrogatory")],
)  # fmt: skip
def test_expand_errors(spec: str, needle: str) -> None:
    with pytest.raises(checked.CheckedError) as e:
        checked.expand(spec)
    assert any(needle in p for p in e.value.problems)


def test_validate_count_checksum() -> None:
    nums = checked.expand("1.1, 2.1-2.13")
    assert checked.validate(nums, 14) == nums
    with pytest.raises(checked.CheckedError, match="14 numbers are checked but the set's count is 15"):
        checked.validate(nums, 15)
    with pytest.raises(checked.CheckedError, match="not in the form's order"):
        checked.validate(["2.2", "2.1"], 2)
    with pytest.raises(checked.CheckedError, match="9.9 is not"):
        checked.validate(["9.9"], 1)


def test_compact_is_the_inverse() -> None:
    spec = "1.1, 2.1-2.13, 4.1-4.2, 6.1-6.7, 8.1-8.8, 20.11"
    assert checked.compact(checked.expand(spec)) == spec
