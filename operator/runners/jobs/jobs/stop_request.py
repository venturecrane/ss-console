"""The Machine-stop checkpoint a job child honours: ``jobs/<id>/.stop-request``.

WHY A FILE AND NOT A SIGNAL. A job child runs in its own session
(``child.run_child`` starts it with ``start_new_session=True``), so the
Machine's stop signal, which tini sends to the entrypoint's process group,
never reaches it. The root daemon learns of the stop and drops this file into
each running job's dir; the child looks for it at the points where stopping
loses nothing: between DAG stages, between vision batch groups, and inside a
Batch poll (the batch id is already on disk, so the next attempt re-attaches
to the same batch and pays for nothing twice). Finding it, the child raises
:class:`StopRequested`, writes no verdict, and the CLI exits :data:`STOP_EXIT`.

WHY 76. The Hermes gateway's service-restart exit is 75 (EX_TEMPFAIL,
``gateway/restart.py``); every other code a runner returns is 0-4 (the CLI's
``_exit_code`` table) or 2 (a refused envelope). 76 is unused anywhere in the
runner and reads as "stopped at a checkpoint, resume me".
``resume.cut_short_by_stop`` treats it as keep-claimed.

WHY BaseException. Every stage wraps its body in ``except Exception`` and turns
whatever it catches into a failed stage. A stop is not a failure: deriving from
BaseException, like KeyboardInterrupt, carries it past those handlers to the
CLI, so a stop can never be recorded as the stage's own defect.

The file is armed once per process by the CLI (:func:`arm`); a process that
never armed (a test, a laptop run, a rehearsal) never stops. The daemon clears
the file before every attempt (``resume.start_run``), so a resumed job does not
stop on the request that cut its predecessor.

SMD'S JOB STOP IS A SECOND FILE, NOT THIS ONE (2026-11-04). ``.stop-request``
means "the Machine is stopping, resume me": the daemon keeps the job claimed
and the next boot runs it again. ``.smd-stop.json`` (``smd_stop.py``, written
by ``python -m jobs.stop``) means "SMD decided this job ends, for good". It is
checked everywhere this file is AND before every paid model call
(``llm.Doorway``, :func:`check_smd`), because the drafting finish (compose,
audit, repair, gate, render, review) runs for many minutes with no stage
boundary: a duplicate A&P drafting job spent $56.64 at about $1 a minute while
its stop request sat unread. :class:`SmdStopRequested` subclasses
:class:`StopRequested`, so every handler that already lets a stop through lets
this one through too; which file was present, not the exception, decides
whether the daemon resumes the job or ends it ``stopped_by_smd``. The model
call checks only SMD's file: a Machine stop keeps its own checkpoints, so a
seat restart drains a job exactly as it did before.
"""

from __future__ import annotations

import time
from pathlib import Path

NAME = ".stop-request"
#: SMD's job stop (``smd_stop.py``): final, never resumed by the daemon.
SMD_NAME = ".smd-stop.json"
STOP_EXIT = 76


class StopRequested(BaseException):
    """The Machine is stopping and this job reached a checkpoint."""


class SmdStopRequested(StopRequested):
    """SMD stopped this job (``python -m jobs.stop``): it ends here, for good."""


class _Armed:
    path: Path | None = None
    smd: Path | None = None


def arm(job_dir: Path | str | None) -> None:
    """Watch ``<job_dir>/.stop-request`` (and SMD's stop) for the rest of this process."""
    _Armed.path = Path(job_dir) / NAME if job_dir else None
    arm_smd(job_dir)


def arm_smd(job_dir: Path | str | None) -> None:
    """Watch only SMD's stop: for a lane whose runner keeps no Machine-stop
    checkpoint (demand, negotiation, litigation), so arming it changes nothing
    about how a seat restart drains that lane."""
    _Armed.smd = Path(job_dir) / SMD_NAME if job_dir else None


def smd_requested() -> bool:
    p = _Armed.smd
    return p is not None and p.exists()


def requested() -> bool:
    p = _Armed.path
    return (p is not None and p.exists()) or smd_requested()


def check_smd(where: str = "") -> None:
    """Raise :class:`SmdStopRequested` when SMD has stopped this job. Called
    before every paid model call, so a stop costs at most the call in flight."""
    if smd_requested():
        raise SmdStopRequested(f"stopped by SMD{f' at {where}' if where else ''}")


def check(where: str = "") -> None:
    """Raise :class:`StopRequested` when the daemon has asked this job to stop."""
    check_smd(where)
    p = _Armed.path
    if p is not None and p.exists():
        raise StopRequested(f"stop requested{f' at {where}' if where else ''}")


def sleep(seconds: float) -> None:
    """``time.sleep`` that wakes for a stop request: a Batch poll waits up to
    its interval between checks, so a stop is honoured within a second."""
    # Counted in steps, not against a clock: a test that stubs time.sleep
    # gets an instant poll, never a busy wait for the real interval.
    left = max(0.0, float(seconds))
    while True:
        check("batch poll")
        if left <= 0:
            return
        step = min(1.0, left)
        time.sleep(step)
        left -= step


__all__ = [
    "NAME",
    "SMD_NAME",
    "STOP_EXIT",
    "SmdStopRequested",
    "StopRequested",
    "arm",
    "arm_smd",
    "check",
    "check_smd",
    "requested",
    "sleep",
    "smd_requested",
]
