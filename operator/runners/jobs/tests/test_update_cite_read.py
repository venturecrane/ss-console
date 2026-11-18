"""An added entry's citation of an excluded COPY names the read page it
copies (``update/cite_read``); a citation of a delivered exhibit's page is
counted, never changed; nothing else of any entry changes.

Every name, date, id and value below is invented."""

from __future__ import annotations

import json

from jobs.medchron import pages_record as PR
from jobs.medchron.update import cite_read as CR

#: Two new documents: "big" (Exhibit 6) read in full, "copy" (Exhibit 7) whose
#: pages 2-3 repeat big's pages 10-11 and whose page 4 is delivered Exhibit 2 p. 5.
EXHIBIT_OF = {"big": 6, "copy": 7}


def _rows() -> list[dict]:
    rows = [PR.row("big", "s1", p, PR.READ, "new", PR.NONE, "update_select") for p in range(1, 13)]
    rows.append(PR.row("copy", "s2", 1, PR.READ, "new", PR.NONE, "update_select"))
    for p, q in ((2, 10), (3, 11)):
        cover = {"doc": "big", "page": q, "exact": True}
        rows.append(PR.row("copy", "s2", p, PR.EXCLUDED, "repeat", PR.PICTURE, "update_select", cover))
    delivered = {"exhibit": 2, "page": 5, "exact": True}
    rows.append(PR.row("copy", "s2", 4, PR.EXCLUDED, "already_delivered", PR.OWN_DELIVERED, "update_select", delivered))
    # page 5 repeats page 3, which itself repeats big p. 11: the cover is followed
    rows.append(PR.row("copy", "s2", 5, PR.EXCLUDED, "repeat", PR.TEXT, "update_select", {"doc": "copy", "page": 3}))
    return rows


def _entry(cite: str) -> str:
    return (
        "03/04/2025\nExample Clinic | Medical Diagnoses\n\n"
        f"Lumbar strain with reduced flexion of 40 degrees. {cite}\n\nPlan: physical therapy twice weekly. {cite}"
    )


def test_a_citation_of_a_copy_names_the_read_page_and_is_counted() -> None:
    out, counts = CR.to_read_pages([_entry("(Exhibit 7 - p. 2-3)")], _rows(), EXHIBIT_OF)
    assert out == [_entry("(Exhibit 6 - p. 10-11)")]
    assert counts == {CR.MOVED: 2, CR.DELIVERED: 0, CR.MIXED: 0}


def test_a_cover_that_is_itself_a_copy_is_followed_to_the_read_page() -> None:
    out, counts = CR.to_read_pages([_entry("(Exhibit 7 - p. 5)")], _rows(), EXHIBIT_OF)
    assert out == [_entry("(Exhibit 6 - p. 11)")] and counts[CR.MOVED] == 2


def test_a_citation_of_a_delivered_page_is_counted_and_left_as_written() -> None:
    e = _entry("(Exhibit 7 - p. 4)")
    out, counts = CR.to_read_pages([e], _rows(), EXHIBIT_OF)
    assert out == [e] and counts == {CR.MOVED: 0, CR.DELIVERED: 2, CR.MIXED: 0}


def test_a_citation_mixing_a_read_page_and_a_copy_is_left_and_counted() -> None:
    e = _entry("(Exhibit 7 - p. 1-2)")
    out, counts = CR.to_read_pages([e], _rows(), EXHIBIT_OF)
    assert out == [e] and counts[CR.MIXED] == 2 and counts[CR.MOVED] == 0


def test_entries_citing_read_pages_and_delivered_exhibits_are_byte_identical() -> None:
    """The delivered chronology is never an input, and an entry whose
    citations name only read pages, or a delivered exhibit's number, comes
    back byte for byte; the rows are never edited."""
    rows = _rows()
    before = json.loads(json.dumps(rows))
    entries = [
        _entry("(Exhibit 6 - p. 10)"),
        _entry("(Exhibit 7 - p. 1)"),
        _entry("(Exhibit 2 - p. 5)"),  # a delivered exhibit: no new document is numbered 2
        "04/05/2025\nExample Imaging | Medical Diagnoses\n\nMRI of the lumbar spine is unremarkable. (Exhibit 3)",
    ]
    out, counts = CR.to_read_pages(entries, rows, EXHIBIT_OF)
    assert out == entries
    assert counts == {CR.MOVED: 0, CR.DELIVERED: 0, CR.MIXED: 0} and rows == before


def test_with_no_page_record_nothing_moves() -> None:
    e = _entry("(Exhibit 7 - p. 2)")
    assert CR.to_read_pages([e], [], EXHIBIT_OF) == ([e], {CR.MOVED: 0, CR.DELIVERED: 0, CR.MIXED: 0})
