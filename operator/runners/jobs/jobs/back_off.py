"""The job host's loop for a lane that BACKS_OFF (``daemon.Daemon.tick``):
the demand lane and the drafting family. No head-of-line starvation, and a
held job is final.

A mixin of ``daemon.Daemon`` (it holds no state of its own): every method
reads and writes the job's ``daemon.json`` through the host's own
``_daemon_state`` / ``_write_state``, and logs to the lane's loop logger.
"""

from __future__ import annotations

from typing import Any

from . import resume as resume_mod


class BackOff:
    def _refuse_held_resumes(self: Any) -> None:
        """A held job is FINAL. Its ledger has no held -> running edge, and a
        hold means a person must read the reason (a refused letter, a wrong
        destination); resuming it would replay the same refusal or, worse,
        file what was refused. A marker for one is removed, loudly."""
        if not self.queue.is_dir():
            return
        for marker in self.queue.glob(f"{resume_mod.MARKER_PREFIX}*.json"):
            job_id = marker.name[len(resume_mod.MARKER_PREFIX) : -len(".json")]
            if self._daemon_state(job_id).get("state") == "held":
                self._loop_log.error(self.HELD_RESUME_REFUSED, job_id)
                marker.unlink(missing_ok=True)

    def _ready(self: Any) -> list[str]:
        now = self.clock()
        return [j for j in self._in_progress() if float(self._daemon_state(j).get("retry_after") or 0) <= now]

    def _defer(self: Any, job_id: str) -> None:
        """Back the job off (1, 2, 4 ... 60 minutes) so a job that keeps
        deferring cannot hold the head of the queue: the next tick claims the
        job behind it (review of #3074)."""
        n = int(self._daemon_state(job_id).get("deferrals") or 0) + 1
        self._write_state(job_id, deferrals=n, retry_after=self.clock() + min(60.0 * 2 ** (n - 1), 3600.0))

    def _run_backing_off(self: Any, job_id: str) -> str:
        try:
            result = self.run_job(job_id)
        except Exception:  # noqa: BLE001 - one job's crash backs that job off (logged with its trace); it must not wedge the lane's head
            self._loop_log.exception("%s job %s could not be started; backing it off", self.JOB_WORD, job_id)
            result = "deferred"
        if result == "deferred":
            self._defer(job_id)
        elif result == "waiting":
            pass  # another lane's child is running; the lane set retry_after itself
        else:
            self._write_state(job_id, deferrals=0, retry_after=None)
        return result


__all__ = ["BackOff"]
