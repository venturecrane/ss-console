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
import signal
import time
from typing import Any

from . import verdict as verdict_mod

logger = logging.getLogger("medchron.daemon")

MARKER_PREFIX = ".resume-"
#: The signals a Machine stop delivers. A child that died of one of these while
#: the daemon was stopping was cut short by the stop, not by its own defect.
STOP_SIGNALS = frozenset({-signal.SIGTERM, -signal.SIGINT})
#: How long a lane waits for the daemon's own stop flag after its child died of
#: a stop signal: the same signal reaches the daemon's main thread, which sets
#: the flag between bytecodes, so this is a race window and not a timeout.
STOP_FLAG_GRACE_SECONDS = 5.0
#: The outcomes that are a job's real ending. Recorded even during a stop:
#: the work happened, and a re-run would only repeat it.
FINISHED_OUTCOMES = frozenset({"delivered", "dry_run", "held", "refused"})


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


def cut_short_by_stop(d: Any, job_id: str, code: int, out: str) -> bool:
    """True when the Machine's stop ended this attempt, so the job must be
    left claimed for the next boot to resume rather than recorded failed.

    A deploy stops the Machine: the stop signal reaches the child and the
    daemon together, the child dies without a verdict, and recording that
    as ``failed`` turns a restart into a dead request that only a root
    resume can revive, while the requester holds a reply saying the work
    is underway (a demand queued seconds before a release restarted the
    seat, 2026-10-08). Left claimed and
    non-terminal, the job is picked up by ``_in_progress`` on the next boot
    and the driver's state file skips the stages that finished, which is
    the daemon's stated contract for a crash mid-job (daemon.py header).

    Only a stop counts. A child the cgroup OOM-killed dies of SIGKILL with
    the daemon running; that is a real failure, recorded, and a re-run
    would die the same way. A real ending (delivered, held, refused) is
    recorded even mid-stop: the work happened."""
    if code in STOP_SIGNALS:
        waited = 0.0
        while not d.stopping() and waited < STOP_FLAG_GRACE_SECONDS:
            time.sleep(0.1)
            waited += 0.1
    if not d.stopping():
        return False
    outcomes = verdict_mod.read(d.job_dir(job_id), out)
    if any(isinstance(o, dict) and str(o.get("outcome")) in FINISHED_OUTCOMES for o in outcomes):
        return False
    logger.warning(
        "%s job %s cut short by the Machine's stop (exit %s); left claimed for the next boot",
        d.LANE,
        job_id,
        code,
    )
    return True
