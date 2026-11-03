"""The records parse: one item per record the sender wrote, and the code-side fragment merge.

Every name, provider and date here is invented."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from medchron.records import parse as P

LETTERED = (
    "Hi team, for the Fenwick matter please pull:\n"
    "8-A. Larkspur Orthopedics intake paperwork\n"
    "8-B. Quillon Valley Imaging MRI report of 4/12/2025\n"
    "8-C. Dr. Ormsby most recent clinic notes (PM&R visit of 5/6/2025 and neurology visit of 6/1/2025)\n"
    "8-D. Brightwater Physical Therapy discharge summary\n"
    "8-E. Saltmarsh Pharmacy billing ledger\n"
    "Thanks"
)
ITEM_LINES = [line for line in LETTERED.splitlines() if line[:2] == "8-"]


def _item(asked: str, *, dates: list[str] | None = None, provider: str = "", other: bool = False) -> dict[str, Any]:
    return {
        "asked": asked,
        "hints": {"dates": dates or [], "provider": provider, "pages": ""},
        "for_another_person": other,
    }


FIVE = {
    "items": [
        _item("Larkspur Orthopedics intake paperwork", provider="Larkspur Orthopedics"),
        _item("Quillon Valley Imaging MRI report of 4/12/2025", dates=["4/12/2025"], provider="Quillon Valley Imaging"),
        _item(
            "Dr. Ormsby most recent clinic notes (PM&R visit of 5/6/2025 and neurology visit of 6/1/2025)",
            dates=["5/6/2025", "6/1/2025"],
            provider="Dr. Ormsby",
        ),
        _item("Brightwater Physical Therapy discharge summary", provider="Brightwater Physical Therapy"),
        _item("Saltmarsh Pharmacy billing ledger", provider="Saltmarsh Pharmacy"),
    ]
}

#: The 2026-11-03 shape: the parenthetical split on its "and", the tail with no provider and
#: the words between the pieces dropped by the model.
OVER_SPLIT = {
    "items": [
        *FIVE["items"][:2],
        _item("Dr. Ormsby most recent clinic notes (PM&R visit of 5/6/2025", dates=["5/6/2025"], provider="Dr. Ormsby"),
        _item("visit of 6/1/2025)", dates=["6/1/2025"]),
        *FIVE["items"][3:],
    ]
}


def test_a_plain_five_item_lettered_list_stays_five() -> None:
    rows = P.check(FIVE, LETTERED)
    assert [r["n"] for r in rows] == [1, 2, 3, 4, 5]
    assert len(rows) == len(ITEM_LINES)
    assert not any(r.get("fragment") for r in rows)
    assert rows[2]["hints"]["dates"] == ["5/6/2025", "6/1/2025"]
    assert rows[2]["hints"]["date"] == "5/6/2025"


def test_a_parenthetical_split_on_and_merges_back_into_one_record_with_both_dates() -> None:
    rows = P.check(OVER_SPLIT, LETTERED)
    assert len(rows) == 5 == len(ITEM_LINES)
    assert [r["n"] for r in rows] == [1, 2, 3, 4, 5]
    third = rows[2]
    # The request's own words, end to end: the dropped "and neurology" comes back.
    assert third["asked"] == (
        "Dr. Ormsby most recent clinic notes (PM&R visit of 5/6/2025 and neurology visit of 6/1/2025)"
    )
    assert third["hints"]["dates"] == ["5/6/2025", "6/1/2025"]
    assert third["hints"]["date"] == "5/6/2025"
    assert third["hints"]["provider"] == "Dr. Ormsby"
    assert rows[3]["asked"] == "Brightwater Physical Therapy discharge summary"


def test_records_on_their_own_lines_never_merge_even_when_short_and_lowercase() -> None:
    text = "Please send:\n- x-rays\n- the ER bill\n- Dr. Pell notes"
    raw = {"items": [_item("x-rays"), _item("the ER bill"), _item("Dr. Pell notes", provider="Dr. Pell")]}
    rows = P.check(raw, text)
    assert [r["asked"] for r in rows] == ["x-rays", "the ER bill", "Dr. Pell notes"]
    assert rows[0].get("fragment") is True  # first row reads as a piece: kept and flagged
    assert "fragment" not in rows[1]


def test_two_records_named_in_one_sentence_stay_two() -> None:
    text = "Please send the 3/3/2021 Thornfield Clinic visit and the discharge summary. Thanks"
    raw = {
        "items": [
            _item("the 3/3/2021 Thornfield Clinic visit", dates=["3/3/2021"], provider="Thornfield Clinic"),
            _item("the discharge summary"),
        ]
    }
    rows = P.check(raw, text)
    assert [r["asked"] for r in rows] == ["the 3/3/2021 Thornfield Clinic visit", "the discharge summary"]


def test_a_lowercase_tail_glued_on_with_no_separator_merges() -> None:
    text = "Need the Corvane Hospital operative report from 2/2/2024 including anesthesia notes please"
    raw = {
        "items": [
            _item(
                "the Corvane Hospital operative report from 2/2/2024", dates=["2/2/2024"], provider="Corvane Hospital"
            ),
            _item("including anesthesia notes"),
        ]
    }
    rows = P.check(raw, text)
    assert len(rows) == 1
    assert rows[0]["asked"] == "the Corvane Hospital operative report from 2/2/2024 including anesthesia notes"


def test_a_fragment_first_item_is_kept_and_flagged_not_merged_into_nothing() -> None:
    text = "visit of 6/1/2025) and the Saltmarsh Pharmacy billing ledger"
    raw = {
        "items": [
            _item("visit of 6/1/2025)", dates=["6/1/2025"]),
            _item("the Saltmarsh Pharmacy billing ledger", provider="Saltmarsh Pharmacy"),
        ]
    }
    rows = P.check(raw, text)
    assert len(rows) == 2
    assert rows[0]["fragment"] is True and rows[0]["asked"] == "visit of 6/1/2025)"
    assert rows[0]["hints"]["dates"] == ["6/1/2025"]
    assert "fragment" not in rows[1]


def test_a_legacy_single_date_hint_still_reads() -> None:
    raw = {"items": [{"asked": "Dr. Pell notes", "hints": {"date": "1/1/2020", "provider": "Dr. Pell", "pages": ""}}]}
    rows = P.check(raw, "Dr. Pell notes")
    assert rows[0]["hints"]["date"] == "1/1/2020" and rows[0]["hints"]["dates"] == ["1/1/2020"]


def test_a_merged_record_keeps_for_another_person() -> None:
    text = "Records for her husband from Dr. Ormsby (visit of 5/6/2025 and visit of 6/1/2025)"
    raw = {
        "items": [
            _item("Records for her husband from Dr. Ormsby (visit of 5/6/2025", dates=["5/6/2025"], other=True),
            _item("visit of 6/1/2025)", dates=["6/1/2025"]),
        ]
    }
    rows = P.check(raw, text)
    assert len(rows) == 1 and rows[0]["for_another_person"] is True


def test_the_prompt_pins_one_item_per_record_and_dates_as_a_list() -> None:
    ins = P.INSTRUCTION
    assert "One entry per record the colleague wrote" in ins
    assert 'never split on "and" inside parentheses' in ins
    assert "belongs to the record before it" in ins
    hints = P.SCHEMA["properties"]["items"]["items"]["properties"]["hints"]
    assert hints["properties"]["dates"] == {"type": "array", "items": {"type": "string"}}
    assert "dates" in hints["required"] and "date" not in hints["properties"]


def test_the_version_bump_means_an_old_cached_parse_is_not_reused(tmp_path: Path) -> None:
    assert P.PARSE_VERSION >= 2
    calls: list[str] = []

    def call(model: str, text: str) -> dict[str, Any]:
        calls.append(model)
        return OVER_SPLIT

    old_key = P.cache_key(LETTERED, "m")
    stale = [{"n": i, "asked": "stale"} for i in range(1, 8)]
    (tmp_path / f"{old_key}.json").write_text(__import__("json").dumps(stale), encoding="utf-8")
    original = P.PARSE_VERSION
    try:
        P.PARSE_VERSION = original + 1  # what the bump does: the key moves off the old entry
        rows = P.parse(LETTERED, "m", call, cache=tmp_path)
    finally:
        P.PARSE_VERSION = original
    assert calls == ["m"] and len(rows) == 5


@pytest.mark.parametrize("bad", ["Dr. Ormsby notes (reworded)", ""])
def test_the_own_words_check_still_refuses_after_the_merge(bad: str) -> None:
    with pytest.raises(P.ParseRefused):
        P.check({"items": [_item(bad)]}, LETTERED)
