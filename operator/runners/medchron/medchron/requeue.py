"""A job whose stage failed on a network error is requeued once, not failed.

WHY (2026-10-14). An offline run of a real update stopped at `update_review`
after $29.69 of work, with its audit already passed, because one
APIConnectionError outlived the doorway's retries. The API answered again
minutes later. On the seat that run records `failed`, and `failed` is terminal:
a paralegal's request would die on a network blip and wait for a person to
resubmit it.

The doorway now retries a network error for ten minutes of wall clock
(deadline.RetryClock). A stage that still fails on one (driver.Outcome
.transient) is requeued here ONCE, after a short delay: the job stays claimed
and non-terminal, the ledger row gets a same-state note naming the stage, and
the next run resumes in place. The driver's state file skips every stage
already `done`, so nothing completed is paid for or uploaded twice. A second
network failure, or any other failure, is recorded `failed` as before: two
outages ten minutes long each are no longer a blip, and a defect re-run only
spends the same money to fail the same way.
"""

from __future__ import annotations

import logging
from typing import Any

from .broker_client import BrokerError

logger = logging.getLogger("medchron.daemon")

#: Seconds before a requeued job runs again.
REQUEUE_DELAY_S = 300.0
#: How many network-failure requeues one job gets.
MAX_REQUEUES = 1


def note(stage: str | None) -> str:
    return f"retried after network error at {stage or 'an unnamed stage'}"


def transient_failure(outcomes: list[Any]) -> bool:
    """True when every failed outcome of the attempt was a network failure
    and at least one failed. A mixed result is a real failure."""
    failed = [o for o in outcomes if isinstance(o, dict) and str(o.get("outcome")) == "failed"]
    return bool(failed) and all(o.get("transient") is True for o in failed)


def maybe_requeue(
    d: Any, job_id: str, outcomes: list[Any], state: str, fields: dict[str, Any], stage: str | None
) -> bool:
    """Requeue the job instead of failing it when this is its first network
    failure. Returns True when it did; the caller then records nothing more.
    On a second network failure the failure reason says it was retried."""
    if state != "failed" or not transient_failure(outcomes):
        return False
    st = d._daemon_state(job_id)
    done = int(st.get("network_requeues") or 0)
    if done >= MAX_REQUEUES:
        fields["reason"] = f"{note(stage)}, failed again: {fields.get('reason') or ''}".rstrip(": ")[:500]
        return False
    why = note(stage)
    counts = {k: fields[k] for k in ("documents", "pages", "cents") if k in fields}
    try:
        # A same-state note on the running row: legal, an audit row of its own,
        # and what a person reading the ledger sees.
        d.broker.record(job_id, "running", {**counts, "reason": why})
    except BrokerError as exc:
        logger.warning("could not record the requeue note for %s: %s (the state file keeps it)", job_id, exc)
    d._write_state(
        job_id,
        state="running",
        network_requeues=done + 1,
        requeue_note=why,
        retry_after=d.clock() + REQUEUE_DELAY_S,
        reason=why,
    )
    logger.warning("%s job %s: %s; requeued, runs again in %.0fs", d.LANE, job_id, why, REQUEUE_DELAY_S)
    return True


def due(d: Any, job_ids: list[str]) -> list[str]:
    """The in-progress jobs whose requeue delay has passed."""
    now = d.clock()
    return [j for j in job_ids if float(d._daemon_state(j).get("retry_after") or 0) <= now]
