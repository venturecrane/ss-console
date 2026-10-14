"""Parity F9: a handwritten row is stated as it can be read, and then settled.

On a real update 30 of 40 reverted corrections were on a handwritten
chiropractic ledger: which modality column each mark sits in, and handwritten
numbers, that neither the finisher nor the grader could read with certainty.
The finisher now names the dated row, quotes what is legible, writes circled
legend letters with their meaning, "modality columns marked as shown on the
page" for the marks, and "a value as written on the page" for an unreadable
number; the review treats such a sentence as settled and does not raise the
same mark again. Typed pages copy paired fields together (each goal with its
own date). Every value is invented; each test fails on the code before F9.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import update_testkit as K
from medchron import prompts
from medchron.update import finisher as FN, review_repair as RR
from test_update_finisher import _finding, _record, _review, _run

ROW = (
    "10/01/2026\nSample Chiropractic | All Other Information\n\nAll Other Information\n"
    "The 10/01 row records cervical and lumbar circled in the adjust column and modality columns marked as "
    "shown on the page. (Exhibit 2 - p. 1)"
)


def test_a_finding_on_a_row_stated_as_marked_is_settled_and_never_raised_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = _review(_finding("N1.c1", "1 wrong value", "which columns the marks are in cannot be confirmed"))
    rc, sr, _md = _run(tmp_path, monkeypatch, [ROW], [found], [])
    assert rc == 0, [f["outcome"] for f in _record(sr)["open"]]
    assert all(kw.get("system") != "update-finish-system" for kw in sr.doorway.seen)  # nothing to finish
    assert any("settled, not raised again" in m for m in sr.logged)


def test_a_row_stated_as_marked_still_answers_for_a_wrong_visit_or_a_copy() -> None:
    md = (
        "Alpha Example - Medical Chronology\n\n## Treatment Timeline\n\n## Diagnostic Highlights\n\n"
        "## Medical Chronology\n\n" + ROW + "\n\n## Exhibit List\n"
    )
    ents = RR.anchor_entries(md, {2}, K.HEADINGS)
    raised = RR.parse_reply(
        _review(_finding("N1.c1", "4 duplicate", "repeats N2"), _finding("N1.c1", "1 wrong date", "x"))
    )
    kept, settled = FN.settled_as_read(raised, ents)
    assert settled == 0 and len(kept) == 2


def test_the_finisher_is_told_how_to_state_a_handwritten_row_and_paired_fields() -> None:
    finish = prompts.load("update-finish-system", K.cfg())
    for words in (
        "Always name the dated row",
        "Quote legible handwriting",
        "with their meaning",
        "modality columns marked as shown on the page",
        "a value as written on the page",
        "each goal with the date printed beside it",
    ):
        assert words in finish, words
    review = prompts.load("update-review-system", K.cfg())
    assert "it is settled, so do not report its marks or that value again" in review
