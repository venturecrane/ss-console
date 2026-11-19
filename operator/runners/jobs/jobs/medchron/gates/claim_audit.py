"""medchron_claim_audit: the document is deliverable only when it was read.

A full chronology audited by the entry read (``stages/entry_audit.py``,
2026-11-18) carries ``runs/<unit>/entry_read.json``: it passes only when the
record says every unit was read (``complete``, no unit ``unread``) and the
stage did not hold. Anything else (the update lane, or a job audited by the
per-claim loop before the entry read) keeps the claim rule: every live claim
has a final verdict and it is SUPPORTED, and no control was wrongly
supported. Registered probe: a document whose claim is UNSUPPORTED (or never
audited) fails the gate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ..audit import coverage
from ..audit.run import AuditPaths

RECORD = "entry_read.json"


def entry_read(slug_dir: Path, unit: str) -> dict[str, Any] | None:
    """The unit's entry-read record, or None when it was never written."""
    p = slug_dir / "runs" / unit / RECORD
    if not p.is_file():
        return None
    try:
        got = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return got if isinstance(got, dict) else {}


def check(slug_dir: Path, unit: str, log: Callable[[str], None] = lambda _m: None) -> tuple[bool, dict[str, Any]]:
    rec = entry_read(slug_dir, unit)
    if rec is None:
        return coverage.check(AuditPaths(slug_dir, unit), log)
    rows = [r for r in rec.get("rows") or [] if isinstance(r, dict)]
    unread = [str(r.get("key")) for r in rows if r.get("status") != "read"]
    ok = rec.get("complete") is True and not unread and not rec.get("held")
    log(f"GATE {'PASS' if ok else 'FAIL'}: entry read {len(rows) - len(unread)} of {len(rows)} unit(s) read")
    return ok, {"entry_read": True, "units": len(rows), "unread": unread, "held": rec.get("held") or ""}
