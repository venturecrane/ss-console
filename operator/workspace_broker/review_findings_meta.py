"""A demand review hold's findings, for the job's audit row only.

The demand ledger keeps a ``reason`` of at most 500 characters (``demand_ledger``),
and until 2026-11-07 a review hold put every must-fix finding in it: a hold
with five findings showed work health and triage one and a half of them, and
two triages on the same job met the cut. The runner now sends a one-line
reason and the findings beside it (``jobs/demand/review.py`` ``held_findings``);
this module takes them off the record's fields, so the row never stores them,
and checks their shape before ``demand_verbs`` writes them onto the
transition's audit row (``DEMAND_JOB_FAILED``), where work health and the
next triage read them through the seat's audit export.

What the row carries: per finding its code (the review check's number and
name), the finding, its citation and who found it (code or model), each
bounded. Nothing names a client by field: there is no client, matter or
requester key here, only the review's own words.
"""

from __future__ import annotations

from typing import Any

FIELD = "findings"
KEYS = ("code", "finding", "cite", "by")
#: The runner's own bounds (``jobs/demand/review.py``), re-applied here: a
#: record is the runner's word, and the broker bounds what it writes.
TEXT_MAX = 2000
COUNT_MAX = 60


def take(fields: dict[str, Any]) -> list[dict[str, str]] | None:
    """Remove ``findings`` from a record's fields; its checked copy, or None
    when it is absent or carries no finding in the runner's shape."""
    raw = fields.pop(FIELD, None)
    if not isinstance(raw, list):
        return None
    out = []
    for f in raw[:COUNT_MAX]:
        if not isinstance(f, dict) or not isinstance(f.get("finding"), str):
            continue
        out.append({k: str(f.get(k) or "")[:TEXT_MAX] for k in KEYS})
    return out or None


def add(meta: dict[str, Any], findings: list[dict[str, str]] | None) -> dict[str, Any]:
    """``meta`` with the record's findings on it, when it carried any."""
    if findings:
        meta[FIELD] = findings
    return meta


__all__ = ["COUNT_MAX", "FIELD", "KEYS", "TEXT_MAX", "add", "take"]
