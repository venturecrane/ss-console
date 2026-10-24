"""A job that stopped on an INFRASTRUCTURE cause is retried after a delay, a
bounded number of times, instead of landing in terminal `failed` or a parked
hold.

WHY (2026-10-14). An offline run of a real update stopped at `update_review`
after $29.69 of work, with its audit already passed, because one
APIConnectionError outlived the doorway's retries. The API answered again
minutes later. On the seat that run records `failed`, and `failed` is terminal:
a paralegal's request would die on a network blip and wait for a person to
resubmit it. That first version retried a network failure once.

WIDENED (stop audit row 20, 2026-10-24). The same is true of every cause that
says nothing about the records or the work: the model API erroring or timing
out, the network, Smokeball indexing a delivery slower than the upload's own
two-minute read-back, and a runner killed by a signal ("the runner exited -15
without a verdict", 09-22, 10-13 and 10-23). Each is retried after a delay
(``delay_for``), up to ``MAX_REQUEUES`` times per job, all causes counted
together. A Machine stop is not counted here: ``resume.cut_short_by_stop``
already keeps that job claimed for the next boot. A defect or a policy stop
never retries: a held review, a refusal, an integrity falsifier, a cap.

A retry keeps the job claimed and non-terminal; the ledger row gets a
same-state ``running`` note naming the cause; the next run resumes in place,
and the driver's state file skips every stage already `done`, so nothing
completed is paid for or uploaded twice (tests/test_decisions_driver.py,
"the resume repays nothing"). The upload in particular is idempotent through
delivery.json: it resumes into its recorded folder and never resends a name it
sent. After the last retry the job lands where it would have without this
module, its reason saying it was retried.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .broker_client import BrokerError

logger = logging.getLogger("medchron.daemon")

#: Seconds before a requeued job runs again; the upload waits longer, for an index.
REQUEUE_DELAY_S = 300.0
UPLOAD_DELAY_S = 900.0
#: How many infrastructure requeues one job gets, every cause together.
MAX_REQUEUES = 4

#: The upload's read-back-short hold (dag.py's exit 2 reason).
UPLOAD_SHORT = "upload: the folder read back short"
#: A runner that died of a signal: the child's exit code is negative.
KILLED = re.compile(r"the runner exited -\d+ without a verdict")
#: A stage that crashed on the model API. A transient one is already marked
#: `transient` by the driver; these are the class names a crash's last output
#: carries when it was not, and the map stage's own infrastructure wording.
MODEL_API = re.compile(
    r"APIConnectionError|APITimeoutError|InternalServerError|OverloadedError|overloaded_error|"
    r"ServiceUnavailableError|RateLimitError|CallDeadlineError|\bmodel API\b"
)

CAUSE_NOTE = {
    "network": "network error",
    "model_api": "model API error",
    "upload_slow": "upload read back short",
    "killed": "runner killed by a signal",
}


def note(stage: str | None, cause: str = "network") -> str:
    return f"retried after {CAUSE_NOTE.get(cause, cause)} at {stage or 'an unnamed stage'}"


def transient_failure(outcomes: list[Any]) -> bool:
    """True when every failed outcome of the attempt was a network failure
    and at least one failed. A mixed result is a real failure."""
    failed = [o for o in outcomes if isinstance(o, dict) and str(o.get("outcome")) == "failed"]
    return bool(failed) and all(o.get("transient") is True for o in failed)


def cause(outcomes: list[Any], state: str, fields: dict[str, Any], stage: str | None) -> str | None:
    """The infrastructure cause of this attempt's ending, or None."""
    reason = str(fields.get("reason") or "")
    if any(isinstance(o, dict) and str(o.get("outcome")) == "dry_run" for o in outcomes):
        return None
    if state == "held":
        return "upload_slow" if stage == "upload" and reason.startswith(UPLOAD_SHORT) else None
    if state != "failed":
        return None
    if not outcomes:
        return "killed" if KILLED.search(reason) else None
    if transient_failure(outcomes):
        return "network"
    failed = [o for o in outcomes if isinstance(o, dict) and str(o.get("outcome")) == "failed"]
    if failed and all(MODEL_API.search(str(o.get("reason") or "")) for o in failed):
        return "model_api"
    return None


def delay_for(why: str) -> float:
    return UPLOAD_DELAY_S if why == "upload_slow" else REQUEUE_DELAY_S


def maybe_requeue(
    d: Any, job_id: str, outcomes: list[Any], state: str, fields: dict[str, Any], stage: str | None
) -> bool:
    """Requeue the job instead of recording it when its ending is an
    infrastructure cause and its retries are not spent. Returns True when it
    did; the caller then records nothing more. On the last attempt the reason
    says it was retried (appended to a hold, whose class keys on its prefix)."""
    why = cause(outcomes, state, fields, stage)
    if why is None:
        return False
    st = d._daemon_state(job_id)
    done = int(st.get("infra_requeues") or 0)
    msg = note(stage, why)
    if done >= MAX_REQUEUES:
        reason = str(fields.get("reason") or "")
        if state == "held":
            tail = f" ({msg}, {done} time(s))"
            fields["reason"] = (reason[: 500 - len(tail)] + tail)[:500]
        else:
            fields["reason"] = f"{msg} {done} time(s), failed again: {reason}".rstrip(": ")[:500]
        return False
    counts = {k: fields[k] for k in ("documents", "pages", "cents") if k in fields}
    try:
        # A same-state note on the running row: legal, an audit row of its own,
        # and what a person reading the ledger sees.
        d.broker.record(job_id, "running", {**counts, "reason": msg})
    except BrokerError as exc:
        logger.warning("could not record the requeue note for %s: %s (the state file keeps it)", job_id, exc)
    wait = delay_for(why)
    d._write_state(
        job_id,
        state="running",
        infra_requeues=done + 1,
        requeue_note=msg,
        retry_after=d.clock() + wait,
        reason=msg,
    )
    logger.warning(
        "%s job %s: %s; requeued (%d/%d), runs again in %.0fs", d.LANE, job_id, msg, done + 1, MAX_REQUEUES, wait
    )
    return True


def due(d: Any, job_ids: list[str]) -> list[str]:
    """The in-progress jobs whose requeue delay has passed."""
    now = d.clock()
    return [j for j in job_ids if float(d._daemon_state(j).get("retry_after") or 0) <= now]
