"""A figure exactly as a document prints it, read into a ``Decimal`` in code.

WHY THIS EXISTS (the model-as-logic audit, 2026-11-04). Every connector tool
that takes a figure used to take it in one machine shape ("1250.00": no dollar
sign, no commas), so the model had to turn the page's "$1,250.00" into that
shape itself. That conversion is arithmetic on a fact the page already holds,
and it is where a figure changes: a split cents box ("8300 00" on a CMS-1500
or a UB-04) read as 830000, a comma dropped as a decimal point. So the tools
now take the figure AS PRINTED and this one function reads it, for every tool.

THE CONTRACT is the drafting runner's ``howell.money`` (2026-11-03, the split
cents defect), moved here because imports run runner to connector only: a
dollar sign, thousands commas, a leading minus or parentheses for a negative,
and dollars and a two-digit cents box separated by whitespace. Any other
whitespace inside the digits ("8 300", "1 234 56") is ambiguous and reads as
None: a dropped figure is reported to a person, an inflated one is a misstated
charge.

A positive-only reader is the callers' rule, not this one's: a payment or a
credit prints in parentheses and is a figure too.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

#: Dollars and cents printed in separate boxes extract as "8300 00": the two
#: digits after whitespace are the CENTS, and only in exactly that shape.
_SPLIT_CENTS = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)\s+(\d{2})")
_PLAIN = re.compile(r"\d+(\.\d{1,2})?")
_GROUPED = re.compile(r"\d{1,3}(?:,\d{3})+(\.\d{1,2})?")


def money(raw: Any) -> Decimal | None:
    """The amount a document prints, or None when the string is not one figure.

    Accepts "$1,250.00", "1250", "8300 00" (a split cents box), "(45.10)" and
    "-$45.10". A comma is read only as a thousands separator in its proper
    places, so "1,25" is None rather than a guess."""
    if isinstance(raw, bool):
        return None
    s = str(raw if raw is not None else "").strip()
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1].strip()
    if s.startswith("-"):
        neg, s = True, s[1:].strip()
    s = s.removeprefix("$").strip()
    if s.startswith("-"):  # "$-8300.00"
        neg, s = True, s[1:].strip()
    split = _SPLIT_CENTS.fullmatch(s)
    if split:
        s = f"{split.group(1)}.{split.group(2)}"
    if _GROUPED.fullmatch(s):
        s = s.replace(",", "")
    if not _PLAIN.fullmatch(s):
        return None
    try:
        value = Decimal(s)
    except InvalidOperation:
        return None
    return -value if neg else value


def positive_money(raw: Any) -> Decimal | None:
    """A charge, an invoice total or an offer: a figure above zero, read from a
    STRING (or an integer) as printed. A float is refused because "1250.0" is
    not a figure any document prints, and a bool is never a figure."""
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        return None
    value = money(raw)
    return value if value is not None and value > 0 else None


__all__ = ["money", "positive_money"]
