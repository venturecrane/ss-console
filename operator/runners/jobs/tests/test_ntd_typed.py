"""The handwritten-records note never stands on a typed page (2026-11-07,
medchron/audit/ntd_typed.py). On a client chronology SMD deleted 54 of 62 such
notes by hand because their cited pages were typed. Invented page text."""

from __future__ import annotations

from jobs import wording
from jobs.medchron.audit import ntd_typed

TYPED = " ".join(["The patient reports lower back pain after lifting boxes at work today."] * 5)
HANDWRITTEN = "[handwritten]\n" + TYPED + " [illegible signature]"
UNREAD = "[handwritten]\n" + TYPED + " [illegible] then [illegible] again"
SCAN = "ll 1l || ,, 7 ."
NOTE = wording.NTD_ILLEGIBLE
PAGES = {(1, 4): TYPED, (1, 5): TYPED, (2, 9): UNREAD, (3, 2): SCAN, (5, 1): HANDWRITTEN}


def page(ex: int, pg: int) -> str | None:
    return PAGES.get((ex, pg))


def _body(*paras: str) -> str:
    return "\n\n".join(["01/02/2026\nExample Clinic | Office Visit", *paras])


def test_a_note_on_typed_pages_is_removed_and_the_rest_kept() -> None:
    """FALSIFIER: on main no stage removes it, and the firm reads that a typed
    record was handwritten and illegible."""
    kept = "Lumbar strain was assessed. (Exhibit 1 - p. 4)"
    body, n = ntd_typed.drop(_body(kept, f"{NOTE} (Exhibit 1 - p. 4-5)"), page)
    assert n == 1 and NOTE[:40] not in body and kept in body


def test_a_note_citing_an_unread_or_unknown_page_stays() -> None:
    for cite in ("(Exhibit 2 - p. 9)", "(Exhibit 3 - p. 2)", "(Exhibit 4 - p. 1)", "(Exhibit 1 - p. 4, 9)"):
        src = _body(f"{NOTE} {cite}")
        assert ntd_typed.drop(src, page) == (src, 0), cite


def test_a_note_with_no_citation_stays() -> None:
    src = _body(NOTE)
    assert ntd_typed.drop(src, page) == (src, 0)


def test_a_handwritten_page_that_was_read_does_not_keep_its_note() -> None:
    """FALSIFIER (2026-11-18): 13 notes stood in a filed chronology on pages
    marked handwritten for a signature, each transcribed in full."""
    body, n = ntd_typed.drop(_body(f"{NOTE} (Exhibit 5 - p. 1)"), page)
    assert n == 1 and NOTE[:40] not in body


def test_a_page_counts_as_read_by_its_words_and_its_unreadable_places() -> None:
    assert ntd_typed.typed(TYPED) and ntd_typed.typed(HANDWRITTEN)
    assert ntd_typed.typed(TYPED + " [illegible]") and not ntd_typed.typed(UNREAD)
    assert not ntd_typed.typed(SCAN) and not ntd_typed.typed(None)
    assert ntd_typed.real_words(TYPED) >= ntd_typed.TYPED_WORDS
