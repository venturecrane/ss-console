"""One job child, run so that a Machine stop drains it instead of killing it.

Every lane that spawns a runner (chronology, demand, drafting, litigation,
negotiation) runs it through :func:`run_child`. Three things it holds:

* **Its own session.** ``start_new_session=True``: the child leads its own
  process group, so the Machine's stop signal (tini ``-g`` signals the
  entrypoint's process group) never reaches it. Before this, the child shared
  the daemon's group and died the instant the Machine stopped, mid-stage.
* **A drain, not a kill.** When the daemon's stop flag rises, the child's job
  dir gets ``.stop-request`` (``stop_request.py``); a chronology or drafting
  child checkpoints at its next safe point and exits 76. A child still running
  :func:`drain_seconds` after the request is terminated (SIGTERM to its group,
  then SIGKILL after :data:`TERM_GRACE_SECONDS`); its stages' own state files
  make the next boot resume it (``resume.cut_short_by_stop`` keeps it claimed).
* **A heartbeat while it runs.** The lane's ``_beat`` thread rewrites the lane
  heartbeat every ``heartbeat_seconds`` (30 s), so a chronology that runs for
  hours never reads as a stale lane to the boot smoke or the fleet busy gate.

The drain deadline is one budget for the whole Machine stop: the entrypoint's
supervisor waits 240 s for the daemon, which leaves 220 s for children plus the
terminate grace plus the daemon's own wind-down.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import stop_request

logger = logging.getLogger("medchron.daemon")

DRAIN_ENV = "SMD_DRAIN_SECONDS"
DEFAULT_DRAIN_SECONDS = 220.0
TERM_GRACE_SECONDS = 10.0
#: Past the terminate grace, the time a lane gets to record what its child left.
JOIN_SLACK_SECONDS = 5.0
#: How often the wait wakes to look at the stop flag.
POLL_SECONDS = 1.0


def drain_seconds() -> float:
    try:
        return max(0.0, float(os.environ.get(DRAIN_ENV) or DEFAULT_DRAIN_SECONDS))
    except ValueError:
        return DEFAULT_DRAIN_SECONDS


def request_stop(job_dir: Path, chown: Callable[[Path], None] | None = None) -> None:
    """Drop the checkpoint request into a running job's dir (root writes it,
    the child reads it: 0644 in the child's own 0700 dir)."""
    p = job_dir / stop_request.NAME
    try:
        p.write_text(f"{time.time():.0f}\n", encoding="utf-8")
        os.chmod(p, 0o644)
    except OSError as exc:  # the deadline still terminates it; resume covers it
        logger.error("could not write %s: %s", p, exc)


def _terminate(proc: subprocess.Popen[str], grace: float) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue


def run_child(
    lane: Any,
    job_id: str,
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log: Any,
    pidfile: Path,
    preexec: Callable[[], None] | None,
) -> tuple[int, str]:
    """Run one job child to its end (or the drain's) and return (returncode, stdout)."""
    proc = subprocess.Popen(  # noqa: S603 - argv is the lane's configured runner command plus the job dir, no shell; env filtered
        argv,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=log,
        text=True,
        preexec_fn=preexec,
        start_new_session=True,
    )
    pidfile.write_text(str(proc.pid))
    beat_stop = threading.Event()
    beat = threading.Thread(target=lane._beat, args=(job_id, beat_stop), name=f"{lane.LANE}-heartbeat", daemon=True)
    beat.start()
    requested_at: float | None = None
    out = ""
    try:
        while True:
            try:
                out, _ = proc.communicate(timeout=POLL_SECONDS)
                break
            except subprocess.TimeoutExpired:
                pass
            if not lane.stopping():
                continue
            now = time.monotonic()
            if requested_at is None:
                requested_at = now
                logger.warning("%s job %s: Machine stopping; asked it to checkpoint", lane.LANE, job_id)
                request_stop(cwd)
            elif now - requested_at >= lane.drain_seconds:
                logger.error(
                    "%s job %s still running %.0fs after the stop request; terminating it (resume covers it)",
                    lane.LANE,
                    job_id,
                    now - requested_at,
                )
                _terminate(proc, TERM_GRACE_SECONDS)
    finally:
        beat_stop.set()
        beat.join(timeout=5)
        pidfile.unlink(missing_ok=True)
    return int(proc.returncode if proc.returncode is not None else -signal.SIGKILL), out or ""


def join_lanes(threads: list[threading.Thread], *, since: float | None, budget: float) -> list[str]:
    """Wait for every lane thread to wind down, measured from the stop itself
    (``since``, a monotonic stamp), so the whole Machine stop fits the
    supervisor's window however long the chronology's own drain took. Returns
    the names of lanes still running at the deadline (logged; the process then
    exits and the next boot resumes their claimed jobs)."""
    end = (since if since is not None else time.monotonic()) + budget + TERM_GRACE_SECONDS + JOIN_SLACK_SECONDS
    for t in threads:
        t.join(timeout=max(0.0, end - time.monotonic()))
    late = [t.name for t in threads if t.is_alive()]
    if late:
        logger.error("lanes still running at the drain deadline: %s", ", ".join(late))
    return late


__all__ = [
    "DEFAULT_DRAIN_SECONDS",
    "DRAIN_ENV",
    "TERM_GRACE_SECONDS",
    "drain_seconds",
    "join_lanes",
    "request_stop",
    "run_child",
]
