"""The DISC-001 catalogue is the Judicial Council PDF's own text, pinned.

The committed JSON is rebuilt from the PDF and compared whole, so the data
cannot drift from its source; a few numbers are also pinned verbatim against
the PDF's text layer read independently of the build's line parser."""

from __future__ import annotations

import hashlib
import json
import re

import pytest

from jobs.drafting.forms import build_catalog, catalog

PDF = build_catalog.SOURCE
EM, EN = chr(0x2014), chr(0x2013)  # the form prints both dashes


def _page_text(page: int) -> str:
    import pymupdf

    doc = pymupdf.open(stream=PDF.read_bytes(), filetype="pdf")
    return " ".join(doc[page].get_text().split())


def test_source_pdf_is_the_pinned_official_form() -> None:
    data = PDF.read_bytes()
    assert hashlib.sha256(data).hexdigest() == build_catalog.SOURCE_SHA256
    assert len(data) == 270459
    cat = catalog.load()
    assert cat.sha256 == build_catalog.SOURCE_SHA256
    assert (cat.form, cat.revision) == ("DISC-001", "Rev. January 1, 2024")


def test_committed_catalogue_equals_a_fresh_build_from_the_pdf() -> None:
    committed = json.loads(build_catalog.OUTPUT.read_text(encoding="utf-8"))
    assert committed == build_catalog.build()


def test_counts_and_section_titles_are_pinned() -> None:
    cat = catalog.load()
    assert len(cat.interrogatories) == 88
    assert sum(1 for i in cat.interrogatories if i.subparts) == 61
    assert [s.number for s in cat.sections] == [
        "1.0", "2.0", "3.0", "4.0", "6.0", "7.0", "8.0", "9.0", "10.0",
        "11.0", "12.0", "13.0", "14.0", "15.0", "16.0", "17.0", "20.0", "50.0",
    ]  # fmt: skip
    titles = {s.number: s.title for s in cat.sections}
    assert titles["6.0"] == "Physical, Mental, or Emotional Injuries"
    assert titles["2.0"] == f"General Background Information{EM}Individual"
    assert titles["20.0"] == f"How the Incident Occurred{EM}Motor Vehicle"
    raw = catalog.raw_data()
    assert raw["reserved_sections"] == ["5.0", "18.0", "19.0", "25.0", "30.0", "40.0", "60.0"]


def test_numbers_are_dotted_and_in_form_order() -> None:
    cat = catalog.load()
    keys = [tuple(int(p) for p in n.split(".")) for n in cat.numbers]
    assert all(re.fullmatch(r"\d{1,3}\.\d{1,2}", n) for n in cat.numbers)
    assert keys == sorted(keys)
    per_section: dict[int, list[int]] = {}
    for major, minor in keys:
        per_section.setdefault(major, []).append(minor)
    assert all(m == list(range(1, len(m) + 1)) for m in per_section.values())
    assert len(per_section[2]) == 13 and len(per_section[16]) == 10 and len(per_section[20]) == 11


@pytest.mark.parametrize(
    "number,letters",
    [("2.1", "abc"), ("2.13", "abcdefg"), ("4.1", "abcdefg"), ("6.4", "abcd"), ("6.5", "abcde"), ("6.6", "abcd"),
     ("6.7", "abc"), ("8.8", "abcd"), ("9.1", "abcd"), ("10.3", "abcde"), ("12.1", "abcd"), ("13.1", "abcd"),
     ("14.2", "abcd"), ("20.2", "abcdefg"), ("50.1", "abcdef"),
     ("1.1", ""), ("2.2", ""), ("6.1", ""), ("6.2", ""), ("8.3", ""), ("9.2", ""), ("20.11", ""), ("50.6", "")],
)  # fmt: skip
def test_subpart_letters(number: str, letters: str) -> None:
    assert "".join(catalog.load().get(number).letters) == letters


@pytest.mark.parametrize(
    "number,page",
    [("1.1", 1), ("2.2", 1), ("6.4", 3), ("8.4", 3), ("12.1", 4), ("17.1", 6), ("50.6", 7)],
)
def test_text_is_verbatim_from_the_pdf_text_layer(number: str, page: int) -> None:
    """Read independently of the build's parser: the whole text, whitespace
    normalised, appears in the page's plain text (no line-break repairs in these)."""
    text = catalog.load().get(number).text
    assert f"{number} {text}" in _page_text(page) or text in _page_text(page)


def test_pinned_texts() -> None:
    cat = catalog.load()
    assert cat.get("2.2").text == "State the date and place of your birth."
    assert cat.get("6.4").subparts[3] == ("d", "the charges to date.")
    assert cat.get("8.3").text == "State the last date before the INCIDENT that you worked for compensation."
    # a line-break hyphen repaired, read on the page: "com- / plaints"
    assert cat.get("10.1").stem.startswith("At any time before the INCIDENT did you have complaints or injuries")
    # a hyphen that is part of the word, kept: "self- / employment"
    assert "place of self-employment; and" in cat.get("2.6").subparts[0][1]
    assert f"sections 2034.210{EN}2034.310)" in cat.get("6.4").stem


def test_repairs_are_exactly_the_ones_read_on_the_pages() -> None:
    joined = [r["joined"] for r in catalog.raw_data()["repairs"]]
    assert joined == [
        "self-employment",
        "contained",
        "complaints",
        f"2034.210{EN}2034.310)",
        "surveillance",
        "unenforceable?",
    ]


def test_defined_terms() -> None:
    cat = catalog.load()
    assert set(cat.get("6.4").defined_terms) == {"HEALTH CARE PROVIDER", "INCIDENT", "ADDRESS"}
    assert set(cat.get("12.2").defined_terms) == {
        "YOU OR ANYONE ACTING ON YOUR BEHALF",
        "INCIDENT",
        "PERSON",
        "ADDRESS",
    }
    assert [k for k, _ in cat.definitions] == ["intro", "a", "a(1)", "a(2)", "b", "c", "d", "e", "f"]
    assert [k for k, _ in cat.instructions_answering] == list("abcdefgh")


def test_repeats_over_phrases_are_in_the_text() -> None:
    cat = catalog.load()
    for number, (kind, phrase) in build_catalog.REPEATS_OVER.items():
        assert cat.get(number).repeats_over == kind
        assert phrase in cat.get(number).text, number
    assert cat.get("6.4").repeats_over == "provider"
    assert cat.get("20.2").repeats_over == "vehicle"
    assert cat.get("12.1").repeats_over == "unknown"


def test_every_number_has_its_own_box_and_the_definition_boxes_exist() -> None:
    cat = catalog.load()
    rects = [(i.box.page, i.box.rect) for i in cat.interrogatories]
    assert len(set(rects)) == 88
    for i in cat.interrogatories:
        x0, y0, x1, y1 = i.box.rect
        assert 16 < x1 - x0 < 21 and 8 < y1 - y0 < 10, i.number
        wx0, wy0, wx1, wy1 = i.box.widget
        assert x0 <= wx0 and wx1 <= x1
    assert set(cat.definition_boxes) == {"4(a)(1)", "4(a)(2)"}
    assert catalog.raw_data()["field_name_notes"] == [
        "1.1: the widget beside it is named 'DISC-001[0].Page2[0].Page2[0].List1\\\\.0[0].item1\\\\.0[0].Identity[0]'"
    ]


def test_unknown_number_and_revision_refused() -> None:
    with pytest.raises(catalog.CatalogueError):
        catalog.load().get("5.1")
    with pytest.raises(catalog.CatalogueError):
        catalog.load(revision="Rev. January 1, 2008")
