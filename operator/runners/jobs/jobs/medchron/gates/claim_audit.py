"""medchron_claim_audit: the document is deliverable only when it was read.

A chronology audited by the entry read (``stages/entry_audit.py``,
2026-11-18; an update's too since 2026-11-20) carries
``runs/<unit>/entry_read.json``: it passes only when the record says every
unit was read (``complete``, no unit ``unread``), the stage did not hold,
and (an update's record) no line found wrong was left uncorrected
(``unresolved``, ``stages/entry_audit_update``). An UPDATE with no such
record fails: an update audited by the per-claim loop before the entry read
must read again, never pass on the old rule. Anything else (a full
chronology audited by the per-claim loop before the entry read) keeps the
claim rule: every live claim has a final verdict and it is SUPPORTED, and no
control was wrongly supported. Registered probe: a document whose claim is
UNSUPPORTED (or never audited) fails the gate."""

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


def stale_update_audit(slug_dir: Path, unit: str, st: Any) -> bool:
    """True for an update whose ``audit`` is done with no entry-read record
    (the per-claim loop ran it, before 2026-11-20) and nothing yet filed: the
    driver reopens the audit so the entry read runs, never the old rule. A
    job that already filed (``upload`` done, or a send record with a row sent
    or started: cut mid-upload) is left alone: a re-read after the send would
    rebuild what the firm already has."""
    if not st.is_done("audit") or st.is_done("upload") or entry_read(slug_dir, unit) is not None:
        return False
    # A job cut mid-upload has sent rows in its send record: it is filed in
    # part, and a re-read would rebuild what the firm already holds.
    from ...filing import SendRecord

    rec = SendRecord.load(slug_dir / "runs" / unit / "delivery.json")
    return not (rec.recorded() or rec.doc.get("sent") or rec.doc.get("pending"))


def reopen_stale(slug_dir: Path, unit: str, st: Any, log: Callable[[str], None]) -> None:
    """Reopen a stale update audit (``stale_update_audit``) so the entry read runs."""
    if stale_update_audit(slug_dir, unit, st):
        log(f"[{unit}] audit: done by the per-claim audit, which no longer files an update; read again")
        st.invalidate(["audit"])


def check(
    slug_dir: Path, unit: str, log: Callable[[str], None] = lambda _m: None, *, update: bool = False
) -> tuple[bool, dict[str, Any]]:
    rec = entry_read(slug_dir, unit)
    if rec is None and update:
        log("GATE FAIL: the update has no entry-read record")
        return False, {"entry_read": False, "units": 0, "unread": [], "held": "", "unresolved": 0}
    if rec is None:
        return coverage.check(AuditPaths(slug_dir, unit), log)
    rows = [r for r in rec.get("rows") or [] if isinstance(r, dict)]
    unread = [str(r.get("key")) for r in rows if r.get("status") != "read"]
    unresolved = rec.get("unresolved") or 0
    ok = rec.get("complete") is True and not unread and not rec.get("held") and not unresolved
    log(f"GATE {'PASS' if ok else 'FAIL'}: entry read {len(rows) - len(unread)} of {len(rows)} unit(s) read")
    summary = {"entry_read": True, "units": len(rows), "unread": unread, "held": rec.get("held") or ""}
    return ok, summary | ({"unresolved": unresolved} if "unresolved" in rec else {})
