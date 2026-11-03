"""The package reproduces the overlay's own output byte for byte (ADR 0102).

Every fixture under ``fixtures/`` was generated ONCE by running the overlay's
code as it stood at 7da235b02 (``shared.matter_file_facts.build`` over its
``record_from_result`` store, ``shared.matter_file_review.hold``), so the
expected values are the overlay's truth, not this package's. FALSIFIER: change
one character of any heading, line, digest canon or hold sentence in the
package and the matching case fails here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from records_contract import facts, review_row

FIXTURES = Path(__file__).parent / "fixtures"
FACTS = sorted((FIXTURES / "facts").glob("*.json"))
REVIEW = sorted((FIXTURES / "review").glob("*.json"))


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_there_are_enough_fixtures() -> None:
    # A glob that matched nothing would pass every parametrised case vacuously.
    assert len(FACTS) >= 6 and len(REVIEW) >= 4


@pytest.mark.parametrize("path", FACTS, ids=lambda p: p.stem)
def test_render_matches_the_overlay(path: Path) -> None:
    case = _load(path)
    given, want = case["input"], case["expected"]
    got = facts.render(
        given["results"],
        given["files"],
        staged_matter=given["staged_matter"],
        read_matters=set(given["read_matters"]),
    )
    assert got.text == want["text"]
    assert got.html == want["html"]
    assert got.hold_reason == want["hold_reason"]
    assert sorted(got.stated) == want["stated"]
    if want["hold_reason"] is None:
        assert list(got.echoed) == want["echoed"]
        assert list(got.names) == want["names"]
    else:
        assert got.items == [] and got.matter_id is None


@pytest.mark.parametrize("path", REVIEW, ids=lambda p: p.stem)
def test_review_digest_matches_the_overlay(path: Path) -> None:
    case = _load(path)
    assert review_row.digest(case["row"]) == case["digest"] == case["row"]["digest"]
    row = review_row.hold_row(**case["input"])
    # Everything but the random id is the overlay's row, and the digest is the same.
    assert {k: v for k, v in row.items() if k != "id"} == {k: v for k, v in case["row"].items() if k != "id"}
    assert row["digest"] == case["digest"]
    assert review_row.ID_RE.fullmatch(row["id"])
