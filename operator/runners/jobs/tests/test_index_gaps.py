"""The writer's INDEX rows against its own entries (``medchron/update/index_gaps.py``):
a code the index lists that the entry never states rides into that entry's
read as one line; a row with no entry is recorded; a malformed row is counted,
never raised on. $0. Every value invented."""

from __future__ import annotations

from jobs.medchron.update import entry_read as ER, index_gaps as IG
from test_entry_read import E1, E2, HEADINGS, doc

MAP = """## ENTRIES
01/05/2025
Example Clinic | Medical Diagnoses

Medical Diagnoses
Low back pain (M54.50) was assessed. (FILE: clinic.pdf, p. 4)

02/10/2025
Sample Imaging | All Other Information

All Other Information
Lumbar radiographs were taken. (FILE: imaging.pdf, p. 2)

## INDEX
2025-01-05 | Example Clinic | M54.50, S33.5XXA | clinic.pdf
2025-02-10 | Sample Imaging | -- | imaging.pdf
2025-03-03 | Other Therapy | M54.50 | therapy.pdf
this row is not a row
2025-13 | broken | M54.50

## BILLING-DATES
none in this chunk
"""


def test_a_code_the_entry_never_states_is_a_codes_gap() -> None:
    got = [g for g in IG.gaps(MAP) if g.kind == "codes"]
    assert [(g.date, g.provider, g.codes, g.file) for g in got] == [
        ("2025-01-05", "Example Clinic", ["S33.5XXA"], "clinic.pdf")
    ]


def test_a_row_matching_no_entry_is_a_no_entry_gap() -> None:
    got = [g for g in IG.gaps(MAP) if g.kind == "no_entry"]
    assert [(g.date, g.provider, g.file) for g in got] == [("2025-03-03", "Other Therapy", "therapy.pdf")]


def test_malformed_rows_are_counted_never_raised() -> None:
    found, bad = IG.scan(MAP)
    assert bad == 2 and len(found) == 2
    assert IG.scan("no sections at all") == ([], 0)


def test_a_stated_code_is_no_gap_dotted_or_not() -> None:
    ents = IG.map_entries(MAP)
    ents[0] = ents[0].replace("was assessed.", "and a lumbar sprain (S335XXA) were assessed.")
    assert [g for g in IG.gaps(MAP, ents) if g.kind == "codes"] == []


def test_the_gap_line_rides_into_its_entry_s_unit_only() -> None:
    us = ER.plan(doc([E1, E2]), {1, 2}, HEADINGS, lambda ex: 5)
    left = IG.attach(us, IG.gaps(MAP))
    by = {u.anchor: u.gaps for u in us}
    assert by["N1"] == ["The writer's own index for this visit lists codes S33.5XXA that the entry does not state."]
    assert by["N2"] == [] and by[ER.PRE] == [] and left == []
