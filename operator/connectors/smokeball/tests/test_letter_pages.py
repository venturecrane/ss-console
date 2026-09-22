"""Tests for reading a combined post bundle as pages and cutting it per letter.

Each test here is written so that removing the line it defends makes it fail.
Three carry the weight:

* ``test_one_digital_cover_does_not_hide_the_scans`` is the defect that would
  have shipped. The connector's existing road test averages over the document,
  so one ordinary digital page routes a bundle of scans onto the pypdf road,
  where empty pages are dropped and nothing reports it.
* ``test_a_page_that_prints_a_marker_does_not_move_the_boundaries`` is the
  forged-marker case. Fax headers and pleading footers print things shaped like
  ``[p.7]`` and a transcription reproduces them.
* ``test_a_second_letter_cannot_claim_a_filed_page`` is the cross-client
  disclosure the resolution token cannot see, because a token binds a matter
  and says nothing about which pages went with it.
"""

from __future__ import annotations

import io

import pytest
from smokeball_connector import letter_pages as lp


def _blank_pdf(pages: int) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ---- the road decision ----------------------------------------------------


def test_one_digital_cover_does_not_hide_the_scans() -> None:
    """Per page, never averaged.

    Five pages: one dense cover sheet and four blank scans. Averaged over the
    document that is 2000 characters against a floor of 5 * 15 = 75, which
    reads as "not scanned" and sends four letters down a road that drops them.
    """
    pages = ["x" * 2000, "", "", "", ""]
    assert lp.scanned_indexes(pages) == [1, 2, 3, 4]


def test_a_fully_digital_bundle_has_no_scanned_pages() -> None:
    """The falsifier for the test above: this must NOT report scans."""
    assert lp.scanned_indexes(["x" * 2000] * 4) == []


def test_a_page_just_under_the_floor_counts_as_scanned() -> None:
    assert lp.scanned_indexes(["x" * (lp.PAGE_TEXT_FLOOR - 1)]) == [0]
    assert lp.scanned_indexes(["x" * lp.PAGE_TEXT_FLOOR]) == []


# ---- page extraction ------------------------------------------------------


def test_page_texts_returns_one_entry_per_page_and_drops_nothing() -> None:
    """A blank page is a real answer about a real page."""
    assert lp.page_texts(_blank_pdf(4)) == ["", "", "", ""]


def test_a_file_that_is_not_a_pdf_refuses_by_name() -> None:
    with pytest.raises(lp.PageReadError) as exc:
        lp.page_texts(b"MZ\x90\x00 not a pdf")
    assert exc.value.reason == "not_pdf"


# ---- markers --------------------------------------------------------------


def test_compose_marks_every_page_including_the_empty_ones() -> None:
    text = lp.compose(["first", "", "third"])
    assert text == "[p.1]\nfirst\n\n[p.2: no legible content]\n\n[p.3]\nthird"


def test_compose_and_parse_round_trip() -> None:
    pages = ["alpha", "", "gamma"]
    assert lp.parse_marked(lp.compose(pages), 3) == pages


def test_a_page_that_prints_a_marker_does_not_move_the_boundaries() -> None:
    """A marker is read only as a block's first line, never scanned for."""
    pages = ["letterhead\n[p.7] printed in the footer", "second"]
    parsed = lp.parse_marked(lp.compose(pages), 2)
    assert parsed[0].startswith("letterhead")
    assert "[p.7]" in parsed[0]
    assert len(parsed) == 2


@pytest.mark.parametrize(
    "text,count",
    [
        ("[p.1]\nalpha\n\n[p.3]\ngamma", 2),
        ("[p.2]\nalpha\n\n[p.1]\nbeta", 2),
        ("alpha\n\n[p.2]\nbeta", 2),
        ("[p.1]\nalpha", 2),
    ],
)
def test_numbering_we_cannot_vouch_for_is_refused(text: str, count: int) -> None:
    with pytest.raises(lp.PageReadError) as exc:
        lp.parse_marked(text, count)
    assert exc.value.reason == "marker_mismatch"


# ---- cutting --------------------------------------------------------------


def test_split_range_returns_only_the_pages_asked_for() -> None:
    out = lp.split_range(_blank_pdf(9), 4, 6)
    assert lp.page_texts(out) == ["", "", ""]


@pytest.mark.parametrize("first,last", [(0, 2), (3, 2), (1, 10), (10, 12)])
def test_a_range_outside_the_document_refuses(first: int, last: int) -> None:
    with pytest.raises(lp.PageReadError) as exc:
        lp.split_range(_blank_pdf(5), first, last)
    assert exc.value.reason == "range_out_of_bounds"


# ---- filenames ------------------------------------------------------------


def test_every_period_leaves_the_stem() -> None:
    """The vendor truncates a displayed name at the first period."""
    assert lp.safe_file_name("09.22.2026 State Farm.pdf", 1, 2) == "09 22 2026 State Farm.pdf"


def test_a_hostile_name_cannot_contribute_a_path_or_a_control_character() -> None:
    assert lp.safe_file_name("../../etc/passwd", 1, 1) == "passwd.pdf"
    assert lp.safe_file_name("re\x00port\x07", 1, 1) == "report.pdf"


def test_an_unusable_name_degrades_to_something_findable() -> None:
    assert lp.safe_file_name("", 4, 6) == "letter pp4-6.pdf"
    assert lp.safe_file_name("...", 4, 6) == "letter pp4-6.pdf"


def test_a_long_name_is_capped_and_still_ends_in_pdf() -> None:
    out = lp.safe_file_name("A" * 400, 1, 1)
    assert out.endswith(".pdf")
    assert len(out) <= lp._NAME_MAX_STEM + 4


# ---- the filed-page ledger ------------------------------------------------


def test_a_second_letter_cannot_claim_a_filed_page() -> None:
    ledger = lp.FiledPages()
    assert ledger.claim("tok", 1, 3) is None
    assert ledger.claim("tok", 3, 5) == 3


def test_a_disjoint_range_on_the_same_bundle_is_allowed() -> None:
    ledger = lp.FiledPages()
    assert ledger.claim("tok", 1, 3) is None
    assert ledger.claim("tok", 4, 6) is None


def test_another_bundle_is_a_different_ledger() -> None:
    ledger = lp.FiledPages()
    assert ledger.claim("tok-a", 1, 3) is None
    assert ledger.claim("tok-b", 1, 3) is None


def test_a_range_released_after_a_failed_write_can_be_claimed_again() -> None:
    ledger = lp.FiledPages()
    ledger.claim("tok", 2, 4)
    ledger.release("tok", 2, 4)
    assert ledger.claim("tok", 2, 4) is None
