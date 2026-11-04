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
"""

from __future__ import annotations

import time
from pathlib import Path

NAME = ".stop-request"
STOP_EXIT = 76


class StopRequested(BaseException):
    """The Machine is stopping and this job reached a checkpoint."""


class _Armed:
    path: Path | None = None


def arm(job_dir: Path | str | None) -> None:
    """Watch ``<job_dir>/.stop-request`` for the rest of this process."""
    _Armed.path = Path(job_dir) / NAME if job_dir else None


def requested() -> bool:
    p = _Armed.path
    return p is not None and p.exists()


def check(where: str = "") -> None:
    """Raise :class:`StopRequested` when the daemon has asked this job to stop."""
    if requested():
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


__all__ = ["NAME", "STOP_EXIT", "StopRequested", "arm", "check", "requested", "sleep"]
