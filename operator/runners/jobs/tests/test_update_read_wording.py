"""The update says what it did with a file it found nothing new in: pages
read beside pages already delivered, never "read in full" when it skipped
pages (2026-11-17, ``update/outcome.read_wording``).

WHY. A firm was told a 681-page file was "read in full" when 608 of its pages
were identical to delivered pages (not read again) and 73 were read. Every
name and value here is synthetic.
"""

from __future__ import annotations

from jobs.medchron.update import outcome as UO, review_list, select as S


def _row(pages: int, read: int, **excluded: int) -> dict:
    names = {"already": S.ALREADY, "blank": S.BLANK, "repeat": S.OVERLAP}
    return {
        "pages": pages,
        "read": list(range(1, read + 1)),
        "unread": [],
        "excluded": {names[k]: v for k, v in excluded.items()},
    }


def test_a_file_mostly_identical_to_delivered_pages_says_how_many_pages_were_read() -> None:
    text = UO.read_wording(_row(681, 73, already=608))
    assert text == (
        "73 of 681 pages read; the other pages were not read again (608 already in the delivered exhibits); "
        "no visit or finding the delivered chronology does not already list"
    )
    assert "in full" not in text


def test_a_file_every_page_of_which_was_read_is_still_read_in_full() -> None:
    assert UO.read_wording(_row(9, 9)) == UO.WORDING[UO.NO_NEW_VISIT]
    assert UO.read_wording(_row(9, 9)).startswith("read in full;")


def test_the_records_review_row_carries_the_pages_read_wording() -> None:
    f = {"id": "n1", "name": "Imaging.pdf", "reason": UO.NO_NEW_VISIT, "pages": 12}
    f["text"] = UO.read_wording(_row(12, 2, already=8, blank=1, repeat=1))
    (row,) = review_list.rows([f], {})
    assert row["finding"].startswith("2 of 12 pages read; the other pages were not read again (")
    assert "8 already in the delivered exhibits" in row["finding"] and "1 blank" in row["finding"]
    assert "1 repeating another file read in this update" in row["finding"]
