"""The Conclusion's opening demand, computed by code from the damages chart.

The attorney's permanent rule (A&P, 2026-11-04, 3:28 PM): the brief's opening
demand is the total case value from the damages chart. A model never types it.
``drafting-firm.yaml``::

    opening_demand:
      mediation_brief:
        marker: "{{ATTORNEY: the opening demand}}"   # where the skeleton reserves it
        chart: "III. DAMAGES SUMMARY"                 # the section whose chart is summed
        column: "Billed"                              # the chart's column summed
        sentence: "Plaintiff's opening demand is {total}."   # the firm's own words

Code sums every row of that column except the chart's TOTAL row (a row whose
first cell starts "Total"): a row reading "Not claimed" counts nothing; a row
still carrying a marker (an attorney's value, a fill, NOT IN RECORD) or a cell
that is not a dollar figure stops the computation, and the marker stays for
the attorney, with a note naming the rows. When the chart's own TOTAL row
states a figure, the sum must equal it, or nothing is filled. The figure is
written by code into the firm's authored sentence in place of the marker.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from . import howell

KEY = "opening_demand"
FIELDS = ("marker", "chart", "column", "sentence")
_MONEY = re.compile(r"^\$\s?(\d{1,3}(?:,\d{3})*|\d+)(\.\d{2})?$")
_NONE = re.compile(r"(?i)^(not claimed|none|n/?a|\$?0(\.00)?)$")
_ROMAN = re.compile(r"^\s*[IVXLC]+\.\s*", re.I)


def validate(body: Any, classes: tuple[str, ...]) -> list[str]:
    if not isinstance(body, dict):
        return [f"{KEY}: expected a map of class to rule"]
    out = []
    for cls, rule in body.items():
        where = f"{KEY}.{cls}"
        if cls not in classes:
            out.append(f"{where}: unknown class (expected {list(classes)})")
        elif not isinstance(rule, dict) or set(rule) != set(FIELDS):
            out.append(f"{where}: expected exactly {list(FIELDS)}")
        elif not all(isinstance(rule[k], str) and rule[k].strip() for k in FIELDS):
            out.append(f"{where}: every field is text")
        elif "{total}" not in rule["sentence"] or not rule["marker"].startswith("{{"):
            out.append(f"{where}: the sentence carries {{total}} and the marker is a {{{{...}}}} slot")
    return out


def _norm(s: str) -> str:
    return " ".join(_ROMAN.sub("", s).split()).casefold()


def _section(md: str, title: str) -> str:
    heads = list(re.finditer(r"(?m)^#\s+(.+)$", md))
    for i, h in enumerate(heads):
        if _norm(h.group(1)) == _norm(title):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(md)
            return md[h.end() : end]
    return ""


def _cells(line: str) -> list[str]:
    return [c.strip().strip("*`").strip() for c in line.strip().strip("|").split("|")]


def _money(cell: str) -> Decimal | None:
    m = _MONEY.match(cell.replace(" ", ""))
    if not m:
        return None
    try:
        return Decimal((m.group(1) + (m.group(2) or "")).replace(",", ""))
    except InvalidOperation:
        return None


def total(md: str, chart: str, column: str) -> tuple[Decimal | None, str]:
    """(the chart column's total, why not) from the markdown of the brief: the
    FIRST table in the section (a section may carry a second, a future-care
    table, which is not the chart)."""
    rows: list[str] = []
    for ln in _section(md, chart).splitlines():
        if ln.strip().startswith("|"):
            rows.append(ln)
        elif rows:
            break
    if len(rows) < 3:
        return None, f"no chart under {chart!r}"
    head = [c.casefold() for c in _cells(rows[0])]
    if column.casefold() not in head:
        return None, f"the chart under {chart!r} has no {column!r} column"
    col = head.index(column.casefold())
    s, stated, blocked = Decimal("0"), None, []
    for ln in rows[2:]:
        cells = _cells(ln)
        cell = cells[col] if col < len(cells) else ""
        if cells and re.match(r"(?i)^total\b", cells[0]):
            stated = _money(cell)
            continue
        if _NONE.match(cell):
            continue
        v = _money(cell)
        if v is None:
            blocked.append(cells[0] if cells else "?")
        else:
            s += v
    if blocked:
        return None, "rows without a figure: " + ", ".join(blocked)
    if stated is not None and stated != s:
        return None, f"the chart's TOTAL row ({howell.fmt(stated)}) is not the sum of its rows ({howell.fmt(s)})"
    return s, ""


def fill(md: str, firm_data: Mapping[str, Any], cls: str) -> tuple[str, list[str]]:
    """The markdown with the opening demand written by code, and a note."""
    rule = (firm_data.get(KEY) or {}).get(cls)
    if not rule or rule["marker"] not in md:
        return md, []
    figure, why = total(md, rule["chart"], rule["column"])
    if figure is None:
        return md, [f"opening demand: left for the attorney ({why})"]
    sentence = rule["sentence"].replace("{total}", howell.fmt(figure))
    return md.replace(rule["marker"], sentence, 1), [
        f"opening demand: {howell.fmt(figure)}, the {rule['column']} total of the chart in {rule['chart']}, computed by code"
    ]
