"""A date as printed, read in code into one date or none (``printed_dates``),
and a value confirmed in the source it was read from (``cited_facts``)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from smokeball_connector import cited_facts as cf
from smokeball_connector.printed_dates import date_from_printed


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2026-10-01", date(2026, 10, 1)),
        ("10/01/2026", date(2026, 10, 1)),
        ("10-01-2026", date(2026, 10, 1)),
        ("10.01.26", date(2026, 10, 1)),
        # A transcription's confusables, repaired to exactly one date.
        ("1O/0l/2026", date(2026, 10, 1)),
        ("October 1, 2026", date(2026, 10, 1)),
        ("Oct. 1, 2026", date(2026, 10, 1)),
        ("Sept 30 2026", date(2026, 9, 30)),
        ("1 October 2026", date(2026, 10, 1)),
        ("  10/01/2026  ", date(2026, 10, 1)),
    ],
)
def test_a_printed_date_reads_as_its_one_date(raw: str, expected: date) -> None:
    assert date_from_printed(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "07/1C/2026",  # the 10th or the 16th: two dates, so neither
        "2026-02-30",
        "13/45/2026",
        "99/9l/999C",
        "Octember 1, 2026",
        "2026-7-16",
        "",
        None,
        20261001,
    ],
)
def test_a_date_that_is_not_one_date_is_none(raw: object) -> None:
    assert date_from_printed(raw) is None


def test_a_two_digit_birth_year_is_never_guessed() -> None:
    assert date_from_printed("04/12/88") == date(2088, 4, 12), "a date of service reads 20YY"
    assert date_from_printed("04/12/88", two_digit_year=False) is None
    assert date_from_printed("04/12/1988", two_digit_year=False) == date(1988, 4, 12)


# ---- confirmed in the source ---------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "TOTAL CHARGES $1,250.00",
        "Balance due: 1250.00",
        "28. TOTAL CHARGE\n$ 1250 00",  # a split cents box under its label
        "Amount Due 1,250",
    ],
)
def test_a_figure_printed_beside_its_label_is_confirmed(text: str) -> None:
    assert cf.confirm_in_text(text, Decimal("1250.00"), cf.AMOUNT_LABELS) == cf.PRINTED


@pytest.mark.parametrize(
    "text",
    [
        "TOTAL CHARGES $125,000.00",  # digits contained, a different figure
        "TOTAL CHARGES $12.50",
        "Line 4 copies 1250.00",  # printed, but beside no total label
        "TOTAL 1250 45",  # a split box that reads 1250.45
        "",
    ],
)
def test_a_figure_not_printed_beside_its_label_is_not(text: str) -> None:
    assert cf.confirm_in_text(text, Decimal("1250.00"), cf.AMOUNT_LABELS) != cf.PRINTED


def test_a_missing_source_is_unreadable_not_unprinted() -> None:
    assert cf.confirm_in_text(None, Decimal("1"), cf.AMOUNT_LABELS) == cf.UNREADABLE
    assert cf.confirm_in_text("  ", Decimal("1"), cf.AMOUNT_LABELS) == cf.UNREADABLE


@pytest.mark.parametrize("text", ["DOS 10/01/2026", "Service: October 1, 2026", "from 1O/0l/2026 to 10/03/2026"])
def test_a_date_printed_anywhere_on_the_pages_is_confirmed(text: str) -> None:
    assert cf.confirm_in_text(text, date(2026, 10, 1), None) == cf.PRINTED


def test_a_date_not_printed_is_not() -> None:
    assert cf.confirm_in_text("DOS 10/02/2026", date(2026, 10, 1), None) == cf.NOT_PRINTED


def test_confirm_in_pages_reads_the_page_range_for_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    from smokeball_connector import letter_pages

    class Client:
        def download_file(self, matter_id: str, file_id: str) -> tuple[dict, bytes]:
            return {"name": "x.pdf"}, b"%PDF"

    monkeypatch.setattr(letter_pages, "span_text", lambda blob, a, b: f"[p.{a}]\nTotal 96.25")
    assert cf.confirm_in_pages(Client(), "m", "f", 2, 2, Decimal("96.25"), cf.AMOUNT_LABELS) == cf.PRINTED
    monkeypatch.setattr(letter_pages, "span_text", lambda blob, a, b: None)
    assert cf.confirm_in_pages(Client(), "m", "f", 2, 2, Decimal("96.25"), cf.AMOUNT_LABELS) == cf.UNREADABLE
