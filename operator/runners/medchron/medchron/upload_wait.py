"""A short upload read-back re-runs the upload before the hold parks.

Stop audit row 20 (2026-10-24). The upload stage polls the folder for about
two minutes; Smokeball's index has been slower than that (live, 2026-09-24),
and a held job is PARKED (``Daemon._in_progress``): nobody re-runs it, so a
delivery whose files were simply still indexing waited on a person. The
upload is idempotent through delivery.json (it resumes into its recorded
folder, never resends a name it sent, never deletes), so running it again
after a delay is safe. It is bounded: after ``MAX_UPLOAD_REQUEUES`` the hold
parks and pages as before, its reason saying the upload was re-run. Only that
one hold is a wait; a refusal over changed bytes, or a read-back that found
other bytes than ours, stays a stop.

Same shape as ``requeue.py`` (a network failure's one retry): the job stays
claimed and non-terminal, the ledger row gets a same-state note, and
``requeue.due`` runs it again once ``retry_after`` has passed.
"""

from __future__ import annotations

import logging
from typing import Any

from .broker_client import BrokerError

logger = logging.getLogger("medchron.daemon")

#: The upload's read-back-short hold (dag.py's exit 2 reason).
UPLOAD_SHORT = "upload: the folder read back short"
#: Seconds between upload re-runs, and how many a job gets (about an hour).
UPLOAD_REQUEUE_DELAY_S = 900.0
MAX_UPLOAD_REQUEUES = 4


def maybe_requeue(
    d: Any, job_id: str, outcomes: list[Any], state: str, fields: dict[str, Any], stage: str | None
) -> bool:
    """Requeue instead of parking the read-back-short hold. Returns True when
    it did (the caller records nothing more). On the last attempt the hold's
    reason gains a note saying the upload was re-run."""
    if state != "held" or stage != "upload" or not str(fields.get("reason") or "").startswith(UPLOAD_SHORT):
        return False
    if any(isinstance(o, dict) and str(o.get("outcome")) == "dry_run" for o in outcomes):
        return False
    done = int(d._daemon_state(job_id).get("upload_requeues") or 0)
    if done >= MAX_UPLOAD_REQUEUES:
        # Appended, never prefixed: the hold's class and page code key on the
        # reason's leading "upload:" (proof/holds.py).
        note = f" (the daemon re-ran the upload {done} time(s) over {done * UPLOAD_REQUEUE_DELAY_S / 60:.0f} min)"
        fields["reason"] = (str(fields.get("reason") or "")[: 500 - len(note)] + note)[:500]
        return False
    why = f"upload read back short; running it again in {UPLOAD_REQUEUE_DELAY_S / 60:.0f} min ({done + 1})"
    counts = {k: fields[k] for k in ("documents", "pages", "cents") if k in fields}
    try:
        d.broker.record(job_id, "running", {**counts, "reason": why})
    except BrokerError as exc:
        logger.warning("could not record the upload requeue note for %s: %s (the state file keeps it)", job_id, exc)
    d._write_state(
        job_id,
        state="running",
        upload_requeues=done + 1,
        retry_after=d.clock() + UPLOAD_REQUEUE_DELAY_S,
        reason=why,
    )
    logger.warning("%s job %s: %s", d.LANE, job_id, why)
    return True
