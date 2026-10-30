"""The report kinds' half of the litigation ledger (2026-10-15), held apart so
``litigation_ledger`` stays under the module ceiling: the envelope's optional
keys, the runner's named counts and a staff reassignment's write list.

A status-list envelope carries none of the keys, so its envelope (and its
digest) is byte for byte what it was before the reports existed. The counts are
a closed key set of integers: nothing in them can name a matter, a party or a
date. The write list (a reassignment's ``writes``) carries methods, paths of
the form ``/matters/<uuid>`` or ``/tasks/<uuid>`` and HTTP statuses only; it
rides the job's audit row and is never stored on the row the agent reads.
"""

from __future__ import annotations

import re
from typing import Any

from .litigation_reassign import validate as _reassign

#: The report kinds, and the deadlines report's workbooks in filing order.
REPORTS = ("status", "deadlines", "reassign", "discovery12")
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
#: The staff reassignment's named counts (``litigation/reassign/run.py``).
REASSIGN_COUNTS = (
    "matters_read",
    "cases_to_move",
    "tasks_naming_from",
    "cases_moved",
    "cases_not_moved",
    "cases_skipped",
    "tasks_moved",
    "tasks_auto_moved",
    "tasks_swept",
    "tasks_not_moved",
    "cases_outside_scope",
)
#: The discovery breakdown's named counts (``discovery12/run.py``): chunk
#: results kept per pass, and cases the reading service declined. Missing
#: here, the broker refused the run's delivered record and the job row stayed
#: ``running`` with its files already filed (2026-10-30).
DISCOVERY12_COUNTS = (
    "d12_read_chunks",
    "d12_verify_chunks",
    "d12_audit_chunks",
    "d12_declined",
    "d12_reused",
    "d12_read",
)
ALL_COUNTS = tuple(dict.fromkeys((*DEADLINES_COUNTS, *REASSIGN_COUNTS, *DISCOVERY12_COUNTS)))
#: A reassignment's writes: at most this many ride one audit row.
MAX_WRITES = 2000
_WRITE_PATH = re.compile(r"^/(matters|tasks)/[0-9a-fA-F-]{32,40}$")


class EnvelopeError(ValueError):
    """A request the ledger will not queue. The message names the field."""


def report_keys(req: dict[str, Any]) -> dict[str, Any]:
    """``{}`` for the status list (its envelope is unchanged, byte for byte);
    ``{"report": "deadlines", "workbooks": [...]}`` for the deadlines report;
    ``{"report": "reassign", "reassign": {...}}`` for a staff reassignment."""
    extra = set(req) & {"report", "workbooks", "reassign"}
    if not extra:
        return {}
    if req.get("report") == "discovery12" and extra == {"report"}:
        return {"report": "discovery12"}
    if req.get("report") == "reassign" and extra == {"report", "reassign"}:
        try:
            return {"report": "reassign", "reassign": _reassign(req["reassign"])}
        except ValueError as exc:
            raise EnvelopeError(str(exc)) from exc
    if req.get("report") != "deadlines" or extra != {"report", "workbooks"}:
        raise EnvelopeError(
            'report must be "deadlines" with workbooks, "reassign" with reassign, or "discovery12" alone'
        )
    books = req["workbooks"]
    if not (isinstance(books, list) and books and set(books) <= set(WORKBOOKS) and len(set(books)) == len(books)):
        raise EnvelopeError(f"workbooks must be a non-empty subset of {list(WORKBOOKS)}")
    return {"report": "deadlines", "workbooks": [w for w in WORKBOOKS if w in books]}


def counts(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict) or set(raw) - set(ALL_COUNTS):
        raise ValueError(f"counts carries only {list(ALL_COUNTS)}")
    for v in raw.values():
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise ValueError("each count must be a non-negative int")
    return {k: int(raw[k]) for k in ALL_COUNTS if k in raw}


def writes(raw: Any) -> list[dict[str, Any]]:
    """A reassignment's write list, checked: method, path, status, ok. Never a
    body, a subject or a name."""
    if not isinstance(raw, list) or len(raw) > MAX_WRITES:
        raise ValueError(f"writes must be a list of at most {MAX_WRITES} entries")
    out = []
    for w in raw:
        shaped = isinstance(w, dict) and set(w) <= {"method", "path", "status", "ok"}
        if not shaped or w.get("method") not in ("PATCH", "PUT") or not _WRITE_PATH.match(str(w.get("path") or "")):
            raise ValueError("each write is {method: PATCH|PUT, path: /matters/<id>|/tasks/<id>, status, ok}")
        status = w.get("status")
        ok = w.get("ok") is True
        out.append(
            {"method": w["method"], "path": w["path"], "status": status if isinstance(status, int) else None, "ok": ok}
        )
    return out


__all__ = [
    "ALL_COUNTS",
    "DEADLINES_COUNTS",
    "REASSIGN_COUNTS",
    "REPORTS",
    "WORKBOOKS",
    "EnvelopeError",
    "counts",
    "report_keys",
    "writes",
]
