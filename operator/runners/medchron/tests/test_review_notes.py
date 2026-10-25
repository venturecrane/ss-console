"""The Review Notes file groups the review's findings for the attorney
(``stages/review_notes``). Shapes modelled on a real 172-item notes file
(2026-10-25), with synthetic names."""

from __future__ import annotations

from medchron.proof import review
from medchron.stages import review_notes


def _f(check: str, finding: str, grade: str = review.NOTE, cite: str = "Exhibit 1 - p. 3") -> dict:
    return {"by": "model", "check": check, "finding": finding, "grade": grade, "cite": cite}


def _rec(*findings: dict, released: bool = False) -> dict:
    return {"findings": list(findings), "released": released}


def _bullets(out: list[str]) -> list[str]:
    return [ln.removeprefix("- ").removeprefix(review_notes.FIRST).split(" (")[0] for ln in out if ln.startswith("- ")]


def test_findings_are_grouped_by_what_the_attorney_does_with_them_disagreements_first():
    rec = _rec(
        _f("5 other", "An age is stated without a date of birth."),
        _f("5 duplicate entry", "The same plan sentence appears twice.", review.SHOULD),
        _f("2 entries that contradict each other", "The two entries give different pain scores."),
        _f("5 garbled entry", "A medication name looks misread.", review.SHOULD),
    )
    out = review_notes.lines(rec)
    heads = [ln[3:] for ln in out if ln.startswith("## ")]
    assert heads == [
        "Places where the records disagree",
        "Statements that may repeat",
        "Text that may have been read imperfectly",
        "Other observations",
    ]
    assert "- Worth checking first: The same plan sentence appears twice. (Exhibit 1 - p. 3)" in out
    assert "- The two entries give different pain scores. (Exhibit 1 - p. 3)" in out
    assert not any("SHOULD-FIX" in ln or "NOTE:" in ln for ln in out), "no grade words reach the attorney"


def test_should_fix_comes_before_notes_within_a_group_in_the_reviews_order():
    rec = _rec(
        _f("5 other", "first note"),
        _f("5 other", "first should", review.SHOULD),
        _f("5 other", "second note"),
        _f("5 other", "second should", review.SHOULD),
    )
    assert _bullets(review_notes.lines(rec)) == ["first should", "second should", "first note", "second note"]


def test_a_settled_wording_disclosure_is_always_kept():
    """Falsifier for the tempting cut: the settlement deletes the sentence that
    carried system wording and turns the finding into this disclosure, so its
    quote is gone from the chronology by construction. The attorney must still
    be told a sentence was removed."""
    f = _f("3 the system's own language", 'removed before delivery, system wording: "as shown in the transcription"')
    out = review_notes.lines(_rec(f))
    assert "## Wording changed or to review" in out and any("removed before delivery" in ln for ln in out)


def test_every_shown_finding_is_listed_and_only_exact_repeats_merge():
    a = _f("5 other", "A date is outside the treatment window.")
    b = _f("5 other", "A  date is OUTSIDE the treatment window.")
    c = _f("5 other", "A date is outside the treatment window.", cite="Exhibit 2 - p. 9")
    d = _f("2 contradiction", "Left knee in one entry, right knee in another.")
    assert len(_bullets(review_notes.lines(_rec(a, b, c, d)))) == 3


def test_released_must_fix_findings_lead_the_file():
    rec = _rec(
        _f("5 other", "a note"),
        _f("2 entries that contradict each other", "a must", review.MUST),
        released=True,
    )
    out = review_notes.lines(rec)
    assert (
        out[1] == "## Findings the review graded must-fix, released by SMD" and out[3] == "- a must (Exhibit 1 - p. 3)"
    )


def test_held_must_fix_findings_are_not_listed_and_no_record_gives_nothing():
    rec = _rec(_f("2 entries that contradict each other", "a must", review.MUST))
    assert review_notes.lines(rec) == []
    assert review_notes.lines(None) == []
