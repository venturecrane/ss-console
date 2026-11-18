"""A chronology run's record, carried from the runner's report to its audit row.

WHY (2026-11-17). No real chronology run of 29 had ever finished with nobody at
SMD stepping in, and nothing said so: the per-job row keeps only its latest
state, the job dir that held the attempts is wiped at 72 hours, and a stop's
stage was computed by the runner and dropped before the record. So "did anyone
step in" was a question a person answered by reading a day of audit rows. It is
a number now (``operator/bin/lib/work_health_runs.py`` folds these blocks).

The block rides the transition's audit row as ``run`` (the ``page_store``
precedent, ``page_store_meta.py``): no new table column, no file on the volume.
:func:`take` removes it from a record's fields before the ledger sees them, and
:func:`add` puts the checked copy on the row.

CODES AND COUNTS ONLY. Every key is whitelisted and every value is shape
checked; anything else is dropped, so no matter name, file name, patient name
or record text can ride a block (``tests/test_run_meta.py``). A ``code`` that
is not a short leading token becomes ``uncoded``, and the report counts those.
"""

from __future__ import annotations

import re
from typing import Any

FIELD = "run"
VERSION = 1

#: What happened. ``start``/``end``/``stop`` are the runner's own; the rest are
#: a step someone (or something) took: SMD's resume, redo and release, each
#: ``*_decide`` tool, a document rebuilt by hand, SMD's hand delivery, an
#: infrastructure retry, and a submission that replaces an earlier job.
EVENTS = frozenset(
    {
        "start",
        "end",
        "stop",
        "resume",
        "redo",
        "release",
        "decide_audit",
        "decide_coverage",
        "decide_identity",
        "decide_update",
        "redo_doc",
        "hand_delivery",
        "infra_requeue",
        "replaces",
        "resume_refused",
        "record_gave_up",
    }
)
#: Who took the step. ``code`` is the runner itself; anything else makes the run aided.
BY = frozenset({"firm", "smd", "code"})
#: A stop at a check (a gate, a proof, a review) or at plumbing (everything else).
KINDS = frozenset({"check", "plumbing"})
UNCODED = "uncoded"
UNKNOWN = "unknown"

_CODE = re.compile(r"\A[a-z][a-z0-9_.-]{0,47}\Z")
_STAGE = re.compile(r"\A[a-z][a-z0-9_]{0,47}\Z")
_JOB = re.compile(r"\A[0-9A-HJKMNP-TV-Z]{26}\Z")
#: ``img:<commit>`` from the image, optionally ``+jobs:<bundle version>``.
_RELEASE = re.compile(r"\Aimg:[0-9a-f]{7,40}(\+jobs:[0-9a-f-]{1,40})?\Z|\Ajobs:[0-9a-f-]{1,40}\Z")
_COUNT_KEY = re.compile(r"\A[a-z][a-z0-9_]{0,47}\Z")
MAX_COUNTS = 40


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 10**9 else None


def _counts(raw: Any) -> dict[str, int] | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, int] = {}
    for k in sorted(raw)[:MAX_COUNTS]:
        n = _count(raw[k])
        if isinstance(k, str) and _COUNT_KEY.match(k) and n is not None:
            out[k] = n
    return out or None


def code_of(value: Any) -> str:
    """A code as the block keeps it: the token itself, or ``uncoded``."""
    return value if isinstance(value, str) and _CODE.match(value) else UNCODED


def check(raw: Any) -> dict[str, Any] | None:
    """The block as the row may carry it, or None when it is not a run block
    at all (no known ``event``). Unknown keys are dropped; a ``by`` the
    whitelist does not hold reads ``unknown``, which grades the run aided (a
    step nobody can attribute is never counted as the runner's own)."""
    if not isinstance(raw, dict) or raw.get("event") not in EVENTS:
        return None
    out: dict[str, Any] = {"v": VERSION, "event": raw["event"]}
    out["by"] = raw.get("by") if raw.get("by") in BY else UNKNOWN
    stage = raw.get("stage")
    if isinstance(stage, str) and _STAGE.match(stage):
        out["stage"] = stage
    if "code" in raw:
        out["code"] = code_of(raw.get("code"))
    if raw.get("kind") in KINDS:
        out["kind"] = raw["kind"]
    release = raw.get("release")
    if release is not None:
        out["release"] = release if isinstance(release, str) and _RELEASE.match(release) else UNKNOWN
    replaces = raw.get("replaces")
    if isinstance(replaces, str) and _JOB.match(replaces):
        out["replaces"] = replaces
    for key in ("pages", "checks"):
        counts = _counts(raw.get(key))
        if counts is not None:
            out[key] = counts
    return out


def take(fields: dict[str, Any]) -> dict[str, Any] | None:
    """Remove ``run`` from a record's fields; its checked copy, or None."""
    return check(fields.pop(FIELD, None))


def add(meta: dict[str, Any], run: dict[str, Any] | None) -> dict[str, Any]:
    """``meta`` with the record's run block on it, when it carried one."""
    if run is not None:
        meta[FIELD] = run
    return meta


__all__ = ["BY", "EVENTS", "FIELD", "KINDS", "UNCODED", "UNKNOWN", "VERSION", "add", "check", "code_of", "take"]
