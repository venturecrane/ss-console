"""The month's billing count for a scheduled caseload: open versus Pending
files, taken once a month, from the matter list only.

WHEN. The first SCHEDULED job of a firm month (the first business day's
``full`` read, or whichever scheduled job runs first that month) takes it as
its very first step, before the library is resolved and before anything is
read or paid for. It is written at once, so it stands whether or not the read
that follows succeeds; a later job that month finds it and takes nothing.

WHAT. The caseload's matters as the inventory selects them (the firm's
``caseload_statuses``, the staff list, the person assisting) and nothing
else: no document, no task, no model call. Counted by Smokeball's own status
for each matter, with the matter ids kept beside the counts, so the number on
the invoice can be traced to the files it counted.

    <state>/billing/<caseload key>/<YYYY-MM>.json
        {month, taken_on, job_id, counts: {status: n}, matters: {status: [id]}}

Only the counts leave the volume (the job's metrics, then the delivered
email's one line). The ids stay here, with the rest of the lane's state.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

from . import inventory


def caseload_key(scope: Any) -> str:
    """The same key the broker names a scheduled caseload by
    (``casefile_schedule.caseload_key``) for a one-person scope; a digest of
    the scope otherwise."""
    names = tuple(getattr(scope, "names", ()) or ())
    if len(names) == 1 and not getattr(scope, "staff_ids", ()) and not getattr(scope, "numbers", ()):
        return hashlib.sha256(" ".join(names[0].split()).lower().encode("utf-8")).hexdigest()[:12]
    raw = json.dumps(
        {k: sorted(getattr(scope, k, ()) or ()) for k in ("names", "staff_ids", "numbers")}, sort_keys=True
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def path_of(state: Path, scope: Any, today: dt.date) -> Path:
    return Path(state) / "billing" / caseload_key(scope) / f"{today:%Y-%m}.json"


def load(state: Path, scope: Any, today: dt.date) -> dict[str, Any] | None:
    p = path_of(state, scope, today)
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def take(run: Any) -> dict[str, Any] | None:
    """The month's snapshot for this job's caseload: taken now when none
    exists (and this is a scheduled job), else the one already taken."""
    job = run.job
    if job.trigger != "scheduled" or job.scope is None:
        return None
    have = load(run.state, job.scope, run.today)
    if have is not None:
        return have
    chosen, _roster = inventory.select(job, run.firm, run.seat)
    statuses = [str(s) for s in run.firm.get("caseload_statuses")]
    matters: dict[str, list[str]] = {s: [] for s in statuses}
    for m in chosen:
        matters.setdefault(str(m.get("status") or ""), []).append(str(m["id"]))
    doc = {
        "month": f"{run.today:%Y-%m}",
        "taken_on": run.today.isoformat(),
        "job_id": job.job_id,
        "counts": {s: len(ids) for s, ids in matters.items()},
        "matters": {s: sorted(ids) for s, ids in matters.items()},
    }
    p = path_of(run.state, job.scope, run.today)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(p)
    run.log(f"[billing] {doc['month']}: " + ", ".join(f"{n} {s}" for s, n in doc["counts"].items()))
    return doc


def metric(run: Any) -> dict[str, int] | None:
    """The counts the delivered email quotes: the month's snapshot, on the job
    that took it and on the month's scheduled ``full`` read."""
    job = run.job
    if job.trigger != "scheduled" or job.scope is None:
        return None
    doc = load(run.state, job.scope, run.today)
    if doc is None or not (doc.get("job_id") == job.job_id or job.mode == "full"):
        return None
    counts = doc.get("counts")
    return {str(k): int(v) for k, v in counts.items()} if isinstance(counts, dict) else None


__all__ = ["caseload_key", "load", "metric", "path_of", "take"]
