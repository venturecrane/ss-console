"""The deadlines report's half of the litigation ledger (2026-10-15), held
apart so ``litigation_ledger`` stays under the module ceiling: the envelope's
two optional keys and the runner's named counts.

A status-list envelope carries neither key, so its envelope (and its digest)
is byte for byte what it was before the report existed. The counts are a
closed key set of integers: nothing in them can name a matter, a party or a
date.
"""

from __future__ import annotations

from typing import Any

#: The report kinds, and the deadlines report's workbooks in filing order.
REPORTS = ("status", "deadlines")
WORKBOOKS = ("deadlines", "statute")
#: The named counts the deadlines runner reports (``deadlines/book.py``).
DEADLINES_COUNTS = (
    "matters_read",
    "litigation",
    "dates_next_14",
    "dates_last_14",
    "dates_ahead",
    "tasks_due",
    "tasks_overdue",
    "cases_without_date",
    "statute_upcoming",
    "statute_passed_open",
)


class EnvelopeError(ValueError):
    """A request the ledger will not queue. The message names the field."""


def report_keys(req: dict[str, Any]) -> dict[str, Any]:
    """``{}`` for the status list (its envelope is unchanged, byte for byte);
    ``{"report": "deadlines", "workbooks": [...]}`` for the deadlines report."""
    if "report" not in req:
        return {}
    if req["report"] != "deadlines":
        raise EnvelopeError('report, when present, must be "deadlines"')
    books = req["workbooks"]
    if not (isinstance(books, list) and books and set(books) <= set(WORKBOOKS) and len(set(books)) == len(books)):
        raise EnvelopeError(f"workbooks must be a non-empty subset of {list(WORKBOOKS)}")
    return {"report": "deadlines", "workbooks": [w for w in WORKBOOKS if w in books]}


def counts(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict) or set(raw) - set(DEADLINES_COUNTS):
        raise ValueError(f"counts carries only {list(DEADLINES_COUNTS)}")
    for v in raw.values():
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise ValueError("each count must be a non-negative int")
    return {k: int(raw[k]) for k in DEADLINES_COUNTS if k in raw}


__all__ = ["DEADLINES_COUNTS", "REPORTS", "WORKBOOKS", "EnvelopeError", "counts", "report_keys"]
