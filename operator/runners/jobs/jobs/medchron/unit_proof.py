"""The chronology driver's half of proven delivery (2026-10-13): the proof
block a delivered unit carries to the daemon, and the reasons it pages on.

Kept out of ``driver.py`` (which sits at the module-size ceiling) and apart
from ``proof/`` (lane-agnostic): this module knows the chronology's own files,
``runs/<unit>/delivery.json`` rows as ``jobs/filing.prove`` leaves them, the
unit's ``review.json`` and its usage ledger.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..proof import review as review_mod
from .update import outcome as outcome_mod

#: The reasons the daemon pages SMD on (proof/watch.py). A stage that ends on
#: one says so in its own log line, and the driver carries that line as the
#: reason verbatim: the exit_map's fixed text would bury the prefix the page
#: keys on, and the findings with it. ``update_held:`` (2026-10-13): the
#: stage's own line is what ``proof/holds.classify`` reads to tell a filed
#: update whose earlier version is still beside it from one that filed nothing.
PAGED_PREFIXES = ("review_held:", "readback_mismatch:", "update_held:")


def crash_reason(stage: str, exc: BaseException) -> str:
    """A failed row's reason when the stage RAISED rather than returned an
    exit code (2026-11-22). Every uncaught exception reads as exit 1, and an
    exit_map's exit-1 sentence names one cause (``update_review``: "the
    grader ... gave no verdict"), so a refused request read as a grader's
    silence. The exception's CLASS only: a message can quote record text, and
    the row and SMD's page carry this reason. The trace is in the stage's log.
    ``requeue.MODEL_API`` matches the class name; a transient API error whose
    class does not say so (an overload sent mid-stream is a plain
    ``APIStatusError``) is no longer matched by its message's wording."""
    cls = type(exc)
    name = cls.__qualname__ if cls.__module__ == "builtins" else f"{cls.__module__}.{cls.__qualname__}"
    return f"{stage}: the stage stopped on an unexpected {name}; the trace is in log-{stage}.txt"


def paged_line(lines: list[str]) -> str | None:
    """The stage's last line naming a paged reason, verbatim (capped), or None."""
    for ln in reversed(lines):
        if ln.startswith(PAGED_PREFIXES):
            return ln[:480]
    return None


def block(rd: Path, files: list[dict[str, Any]], pricing: Any) -> dict[str, Any]:
    """The proof block for a delivered unit. ``confirmed`` only when EVERY
    delivery row was proven by hash: a package delivered by a runner that
    predates the hash read-back says ``unproven``, and the daemon fails it
    rather than believe it."""
    proven = [f for f in files if f.get("proven") and f.get("file_id")]
    rec = review_mod.load(rd)
    return {
        "readback": "confirmed" if files and len(proven) == len(files) else "unproven",
        "files": [
            {"name": f["name"], "file_id": str(f["file_id"]), "size": int(f.get("bytes") or 0), "sha256": f["sha256"]}
            for f in proven
        ],
        "review": review_mod.status(rec),
        "review_cents": review_mod.stage_cents(unit_by_stage(rd, pricing)),
        "review_model": str((rec or {}).get("model") or ""),
    }


def for_outcome(o: Any, rd: Path, pricing: Any) -> dict[str, Any] | None:
    """The proof block a delivered outcome carries; None on any other (the
    driver sits at its module-size ceiling, so the branch lives here). An
    update delivered ALREADY CURRENT filed nothing, so it has nothing to read
    back: its block is the zero-file kind, proven by the outcome record
    (``update/outcome.proof``) and accepted by the gate only on that."""
    if o.outcome != "delivered":
        return None
    if getattr(o, "current", None) is not None:
        return outcome_mod.proof(rd, o.files)
    return block(rd, o.files, pricing)


def unit_by_stage(rd: Path, pricing: Any) -> dict[str, float]:
    """Spend by stage off ONE unit's ledger. ``Budget.by_stage`` sums every
    unit's, which on a joint matter would book each client's review cost on
    both outcomes (and the daemon adds them)."""
    out: dict[str, float] = {}
    ledger = rd / "usage-ledger.jsonl"
    if not ledger.is_file():
        return out
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        stage = str(row.get("stage") or "?")
        out[stage] = out.get(stage, 0.0) + pricing.price_row(row)
    return out


__all__ = ["PAGED_PREFIXES", "block", "for_outcome", "paged_line", "unit_by_stage"]
