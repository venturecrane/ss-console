"""The update's same-page reader reads only an exact "all on B" as same; every
other reply means the page is read (2026-11-22). Every value is invented."""

from __future__ import annotations

import pytest

from jobs.medchron.update import same_reader as SR


@pytest.mark.parametrize(
    ("reply", "want"),
    [
        ('{"answer": "all_on_b"}', SR.ALL_ON_B),
        ('Here: {"answer": "all_on_b"}', SR.ALL_ON_B),
        ('{"answer": "missing", "first": "pain 7/10"}', SR.MISSING),
        ('{"answer": "ALL_ON_B"}', SR.MISSING),
        ('{"verdict": "SAME"}', SR.MISSING),
        ("", SR.MISSING),
        (None, SR.MISSING),
        ("I cannot compare these pages.", SR.MISSING),
        ('{"answer": "all_on_b"', SR.MISSING),
    ],
)
def test_only_an_exact_all_on_b_sets_a_page_aside(reply, want) -> None:
    assert SR.verdict(reply) == want


def test_two_readers_must_both_say_all_on_b() -> None:
    yes, no = '{"answer": "all_on_b"}', '{"answer": "missing", "first": "x"}'
    assert SR.both([yes, yes]) == SR.ALL_ON_B
    assert SR.both([yes, no]) == SR.MISSING and SR.both([yes, None]) == SR.MISSING and SR.both([]) == SR.MISSING


def test_the_request_carries_page_a_then_one_to_three_keeper_pages() -> None:
    msgs = SR.request("AAAA", ["BBBB", "CCCC"])
    kinds = [c["type"] for c in msgs[0]["content"]]
    assert kinds == ["text", "image", "text", "image", "image"]
    assert msgs[0]["content"][1]["source"]["data"] == "AAAA"
    with pytest.raises(ValueError):
        SR.request("AAAA", [])
    with pytest.raises(ValueError):
        SR.request("AAAA", ["B"] * 4)


def test_the_question_runs_one_way_only() -> None:
    assert "PAGE B may hold more than PAGE A" in SR.SYSTEM and "differ between them" not in SR.SYSTEM
