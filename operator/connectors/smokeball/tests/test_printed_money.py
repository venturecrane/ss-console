"""The one money reader every connector tool shares (``printed_money``).

The cases are the drafting runner's ``howell.money`` cases
(``operator/runners/medchron/tests/test_drafting_units.py``), carried here
unchanged because this is now that contract's home, plus the cases where this
reader is stricter: a comma only as a thousands separator.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from smokeball_connector.printed_money import money, positive_money


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Dollars and cents in separate boxes (CMS-1500 box 28, UB-04 totals): the
        # 2026-11-03 defect read these at 100 times their printed amount.
        ("$ 8300 00", "8300.00"),
        ("8300 00", "8300.00"),
        ("91275 00", "91275.00"),
        ("0 00", "0.00"),
        ("8,300 00", "8300.00"),
        ("$8300  45", "8300.45"),
        ("(8300 00)", "-8300.00"),
        # The ordinary shapes.
        ("8,300.00", "8300.00"),
        ("$8,300.00", "8300.00"),
        ("$ 8,300.00", "8300.00"),
        ("8300.00", "8300.00"),
        ("8300", "8300"),
        ("8300.5", "8300.5"),
        ("-$45.10", "-45.10"),
        ("$-45.10", "-45.10"),
        ("(45.10)", "-45.10"),
        ("1,234,567.89", "1234567.89"),
        (1250.5, "1250.5"),
    ],
)
def test_a_printed_amount_reads_at_its_printed_value(raw: object, expected: str) -> None:
    assert money(raw) == Decimal(expected)


@pytest.mark.parametrize(
    "raw",
    # Ambiguous or not one figure: never guessed, so never inflated.
    [
        "1 234 56",
        "8 300",
        "8300 0",
        "8300 000",
        "8300.00 00",
        "8300 00.00",
        "$",
        "",
        None,
        "n/a",
        "8300 l 00",
        # Stricter than the runner's reader: a comma that is not a thousands
        # separator is a figure changing shape, not a figure.
        "1,25",
        "12,34.00",
        "1e3",
        True,
    ],
)
def test_an_ambiguous_amount_reads_as_none(raw: object) -> None:
    assert money(raw) is None


@pytest.mark.parametrize("raw, expected", [("$1,250.00", "1250.00"), ("96.25", "96.25"), (25000, "25000")])
def test_a_positive_figure_is_a_charge(raw: object, expected: str) -> None:
    assert positive_money(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", ["0", "0.00", "(5.00)", "-5", 1250.0, False, None, ""])
def test_zero_a_negative_and_a_float_are_never_a_charge(raw: object) -> None:
    assert positive_money(raw) is None
