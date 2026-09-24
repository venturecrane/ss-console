"""Resume requests: a marker the broker drops, resolved by the root daemon.

WHY THE WORK IS SPLIT THIS WAY (ss#2903). The broker cannot re-queue a job
itself. `jobs/` is root:medchron 0710 and the broker runs as workspace-broker
(uid 10001, groups workspace-connectors and audit-readers), so it can neither
read a job's envelope nor tell a wiped job dir from a present one -- it would
get EACCES, which is a third state indistinguishable from both "gone" and
"fine". It writes `queue/.resume-<id>.json`, a directory it owns, and the
daemon -- root, the only process that traverses `jobs/`, and the only writer of
`daemon.json` -- resolves it. Same division as the sticky-stop release: an
external condition the daemon polls, never a flag pushed into its state.

The marker leads with a dot because `Daemon._queued()` skips dotfiles, so it
can never be claimed as an envelope and run as a job named `.resume-...`.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("medchron.daemon")

MARKER_PREFIX = ".resume-"


def take_requests(d: Any) -> list[str]:
    """Re-queue every job a marker asks for. Returns the ids actually re-queued.

    Three fields are cleared or carried on the way through, each closing a hole
    that is real rather than hypothetical:

    * `finished_at` is cleared BEFORE `wipe_expired` runs in the same tick.
      `tick` wipes before it claims and the marker leaves `daemon.json`
      otherwise untouched, so a job that crossed the 72 h window between the
      request and the claim would have its `data/` deleted and then silently
      restart from stage 1 at full price -- indistinguishable, from outside,
      from a successful resume.
    * `wake` is cleared. `_report` left one pending that describes the FAILURE,
      and `dispatch_wakes` runs before the claim; firing it against a job that
      is running again is the duplicate deliver turn the daemon holds to be
      worse than a lost one.
    * `attempts` is left alone and `resumes` counted separately, so the
      per-attempt number stays usable for a future "resumed six times" guard.

    A marker whose job dir or envelope is gone is refused and REMOVED, not left
    to be retried every tick: the artifacts a resume needs are gone, and the
    honest answer is the one the ledger already gives -- still failed.
    """
    if not d.queue.is_dir():
        return []
    resumed: list[str] = []
    for marker in sorted(d.queue.glob(f"{MARKER_PREFIX}*.json")):
        try:
            req = json.loads(marker.read_text(encoding="utf-8"))
            job_id = str(req.get("job_id") or "")
        except (OSError, ValueError) as exc:
            logger.error("unreadable resume marker %s: %s", marker.name, exc)
            marker.unlink(missing_ok=True)
            continue
        env = d.job_dir(job_id) / "envelope.json"
        if not job_id or not env.is_file():
            logger.error("resume %s refused: the job dir or its envelope is gone (wiped?)", job_id or marker.name)
            marker.unlink(missing_ok=True)
            continue
        st = d._daemon_state(job_id)
        (d.queue / f"{job_id}.json").write_text(env.read_text(encoding="utf-8"), encoding="utf-8")
        d._write_state(
            job_id,
            finished_at=None,
            wake=None,
            resumes=int(st.get("resumes") or 0) + 1,
            resume_reason=str(req.get("reason") or "")[:500],
            resume_redo=[str(s) for s in (req.get("redo") or [])],
        )
        marker.unlink(missing_ok=True)
        logger.info("resume %s: re-queued, redo=%s", job_id, req.get("redo") or [])
        resumed.append(job_id)
    return resumed


def start_run(d: Any, job_id: str, base: list[str]) -> list[str]:
    """Mark the job running and return the runner argv for this attempt.

    One call because the two are the same decision: `resume_redo` is CONSUMED
    as the child is launched, so one request buys exactly one attempt and a job
    that fails again parks again -- which is what stops a deterministic defect
    re-running every tick the way a cap-refused hold once did.

    `--redo` rides along when the request named stages. Without it the driver
    skips every `done` stage, including the one the fix changed, because
    `is_done` is `status == "done"` and `input_sha` is recorded but never
    compared -- and the run would ship a document built by the old code,
    cheaply and with a green row.
    """
    st = d._daemon_state(job_id)
    redo = [str(x) for x in (st.get("resume_redo") or [])]
    d._write_state(job_id, state="running", attempts=int(st.get("attempts", 0)) + 1, held_paused=False, resume_redo=[])
    if not redo:
        return base
    logger.info("resuming %s with --redo %s", job_id, ",".join(redo))
    return [*base, "--redo", ",".join(redo)]
