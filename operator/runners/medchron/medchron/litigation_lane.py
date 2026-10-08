"""The litigation lane: the root daemon's fourth slot, for litigation status jobs.

The drafting lane with the litigation swaps (it subclasses ``DraftingLane`` for
the loop, the held-is-final rule, the back-off and the cgroup join):

* its own queue (``SMD_LITIGATION_QUEUE_DIR``, written by the broker's
  ``litigation_job_submit`` as ``<queue>/<job_id>.json``), job dirs
  ``<run_dir>/litigation-jobs``, tick ``<run_dir>/litigation-tick``, child pid
  ``<run_dir>/litigation-child.pid``;
* the broker's LITIGATION verbs: ``litigation_job_status`` (a job's row; with
  no job id, the root view's ``month_cents``) and ``litigation_job_record``;
* its child: ``medchron litigate <job_dir>``, with the litigation inputs and
  the lane's state dir on the allow-listed env, and ``job.json`` stamped fresh
  before every run with the slug, the month's litigation spend and the two
  caps from ``litigation-firm.yaml``;
* its wake: the ``litigation-status`` skill's DELIVER mode, counts only.

Memory: the child joins the DEMAND lane's memory cgroup and waits while a
demand OR a drafting child is live, so the Machine's caps do not grow by a
fourth slot. A litigation job runs for a long time, so a heartbeat thread keeps
the lane's tick fresh while the child runs (the drafting lane's tick stalls for
a whole job; boot smoke and monitoring read this tick).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import resume as resume_mod, verdict as verdict_mod
from .broker_client import BrokerClient, BrokerError
from .daemon import CHILD_ENV_PASS, RUNNER_BIN, Daemon
from .drafting_lane import DraftingLane

logger = logging.getLogger("medchron.litigation")

QUEUE_ENV = "SMD_LITIGATION_QUEUE_DIR"
INPUTS_ENV = "MEDCHRON_LITIGATION_INPUTS"
STATE_ENV = "MEDCHRON_LITIGATION_STATE_DIR"
MEMORY_ENV = "SMD_LITIGATION_MEMORY_MAX_BYTES"
DEFAULT_MEMORY = 1024 * 1024 * 1024
SKILL = "litigation-status"
BUSY_PIDS = ("demand-child.pid", "drafting-child.pid")
CHILD_PID = "litigation-child.pid"
BUSY_RETRY_SECONDS = 30.0
HEARTBEAT_SECONDS = 30.0


class LitigationBroker:
    def __init__(self, client: BrokerClient) -> None:
        self.client = client

    def status(self, job_id: str) -> dict[str, Any] | None:
        return self.client._request({"action": "litigation_job_status", "job_id": job_id}).get("job")

    def record(self, job_id: str, state: str, fields: dict[str, Any]) -> dict[str, Any]:
        return self.client._request(
            {"action": "litigation_job_record", "job_id": job_id, "state": state, "fields": fields}
        )

    def month_cents(self) -> int:
        """This Pacific month's litigation spend. A missing value raises: the
        lane defers rather than run unmetered."""
        v = self.client._request({"action": "litigation_job_status"}).get("month_cents")
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise BrokerError("litigation_job_status carried no month_cents; the job cannot be metered")
        return v


def litigation_runner_cmd() -> list[str]:
    privs = ["setpriv", "--reuid=medchron", "--regid=medchron", "--init-groups", "--no-new-privs"]
    return [*privs, "nice", "-n", "10", RUNNER_BIN, "litigate"]


def _live(pidfile: Path) -> bool:
    """A pid file names a running child. A stale file (a crashed child the
    lane never cleaned up) must not stall this lane forever."""
    try:
        pid = int(pidfile.read_text().strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_verdict(job_dir: Path, out: str) -> dict[str, Any] | None:
    """The runner's one JSON object: the file first, then stdout's last line."""
    texts = []
    try:
        texts.append((job_dir / verdict_mod.NAME).read_text(encoding="utf-8"))
    except OSError:
        pass
    lines = [ln for ln in (out or "").splitlines() if ln.strip()]
    if lines:
        texts.append(lines[-1])
    for t in texts:
        try:
            v = json.loads(t)
        except ValueError:
            continue
        if isinstance(v, dict) and v.get("verdict") in ("delivered", "held", "failed"):
            return v
    return None


@dataclass
class LitigationLane(DraftingLane):
    queue_dir: Path = field(default_factory=lambda: Path("/run/smd-medchron/litigation-queue"))
    inputs_dir: str | None = None
    state_dir: str | None = None
    heartbeat_seconds: float = HEARTBEAT_SECONDS
    LANE = "litigation"
    HEARTBEAT = "litigation-heartbeat"

    @property
    def jobs(self) -> Path:
        return self.run_dir / "litigation-jobs"

    def _write_job(self, job_id: str) -> Path:
        from .litigation import firm as lit_firm

        jd = self.job_dir(job_id)
        env = json.loads((jd / "envelope.json").read_text(encoding="utf-8"))
        firm = lit_firm.load(self.inputs_dir)
        doc = {
            **env,
            "job_id": job_id,
            "kind": "litigation",
            "slug": self.customer_slug,
            "month_cents_used": self.broker.month_cents(),
            "per_job_cap_usd": float(firm.data["per_job_cap_usd"]),
            "monthly_budget_usd": float(firm.data["monthly_budget_usd"]),
        }
        (jd / "data").mkdir(exist_ok=True)
        (jd / "job.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
        self._chown_child(jd)
        return jd

    def _busy(self) -> bool:
        return any(_live(self.run_dir / p) for p in BUSY_PIDS)

    def run_job(self, job_id: str) -> str:
        from .litigation import firm as lit_firm

        try:
            lit_firm.load(self.inputs_dir)
        except lit_firm.LitigationConfigError as exc:
            if not self._daemon_state(job_id).get("config_deferred"):
                logger.warning("litigation inputs not usable yet, deferring %s once: %s", job_id, exc)
                self._write_state(job_id, config_deferred=True)
                return "deferred"
            return self._fail_unconfigured(job_id, exc)
        if self._busy():
            self._write_state(job_id, retry_after=self.clock() + BUSY_RETRY_SECONDS)
            return "waiting"
        try:
            jd = self._write_job(job_id)
            self.broker.record(job_id, "running", {})
        except BrokerError as exc:
            logger.warning("broker not ready for %s, deferring: %s", job_id, exc)
            return "deferred"
        return self._spawn(job_id, jd)

    def _child_env(self, job_id: str, jd: Path) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k in CHILD_ENV_PASS}
        env.update(self.child_env, MEDCHRON_DAEMON_JOB_ID=str(job_id))
        if self.inputs_dir:
            env[INPUTS_ENV] = str(self.inputs_dir)
        if self.state_dir:
            env[STATE_ENV] = str(self.state_dir)
        env.setdefault("MEDCHRON_SEAT", "client")
        env.setdefault("PATH", "/usr/bin:/bin")
        env.setdefault("HOME", str(jd))
        return env

    def _spawn(self, job_id: str, jd: Path) -> str:
        verdict_mod.clear(jd)
        stop = threading.Event()
        beat = threading.Thread(target=self._beat, args=(job_id, stop), name="litigation-heartbeat", daemon=True)
        with (jd / "daemon.log").open("a", encoding="utf-8") as log:
            proc = subprocess.Popen(  # noqa: S603 - argv is the configured runner command plus the job dir, no shell; env filtered
                resume_mod.start_run(self, job_id, [*self.runner_cmd, str(jd)]),
                cwd=str(jd),
                env=self._child_env(job_id, jd),
                stdout=subprocess.PIPE,
                stderr=log,
                text=True,
                preexec_fn=self._cgroup_preexec(),
            )
            (self.run_dir / CHILD_PID).write_text(str(proc.pid))
            beat.start()
            try:
                out, _ = proc.communicate()
            finally:
                stop.set()
                beat.join(timeout=5)
        (self.run_dir / CHILD_PID).unlink(missing_ok=True)
        self.jobs_run += 1
        return self._report(job_id, proc.returncode, out or "")

    def _beat(self, job_id: str, stop: threading.Event) -> None:
        while not stop.wait(self.heartbeat_seconds):
            self.heartbeat(running=job_id)

    def _fail_unconfigured(self, job_id: str, exc: Exception) -> str:
        reason = (
            "config_missing: the litigation firm inputs are not usable on this seat: " + str(exc).split(";")[0][:300]
        )
        try:
            self.broker.record(job_id, "running", {})
            self.broker.record(job_id, "failed", {"cents": 0, "reason": reason, "stage": "config"})
        except BrokerError as err:
            logger.error("could not record config_missing for %s: %s", job_id, err)
            return "deferred"
        self._write_state(job_id, state="failed", finished_at=self.clock(), reason=reason)
        task = self._compose_wake(job_id, "failed", {"stage": "config"})
        self._write_state(job_id, wake={"pending": True, "attempts": 0, "task": task})
        return "failed"

    def _report(self, job_id: str, code: int, out: str) -> str:
        v = read_verdict(self.job_dir(job_id), out)
        if v is None:
            v = {
                "verdict": "failed",
                "stage": None,
                "reason": f"no_verdict: the runner exited {code} without a verdict",
            }
        state = str(v["verdict"])
        fields: dict[str, Any] = {k: v.get(k) for k in ("stage", "folder_id")}
        for k in ("cents", "matters_total", "matters_reread", "flags_new"):
            fields[k] = int(v.get(k) or 0)
        fields["files"] = [f for f in v.get("files") or [] if isinstance(f, dict)]
        if v.get("reason"):
            fields["reason"] = str(v["reason"])[:500]
        try:
            self.broker.record(job_id, state, fields)
        except BrokerError as exc:
            logger.error("could not record %s for %s: %s (the state file keeps it)", state, job_id, exc)
        self._write_state(job_id, state=state, finished_at=self.clock(), reason=fields.get("reason"))
        self._write_state(
            job_id, wake={"pending": True, "attempts": 0, "task": self._compose_wake(job_id, state, fields)}
        )
        return state

    def _compose_wake(self, job_id: str, state: str, fields: dict[str, Any], *, stage: str | None = None) -> str:
        """Code- and broker-authored text only: counts, ids, sizes. Never a
        file name, a matter fact, or the runner's free-text reason."""
        env: dict[str, Any] = {}
        try:
            env = json.loads((self.job_dir(job_id) / "envelope.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        trig = str(env.get("trigger") or "")
        files = (
            "; ".join(f"{f.get('role') or 'file'} ({int(f.get('size') or 0)} bytes)" for f in fields.get("files") or [])
            or "none"
        )
        lines = [
            f"Run the {SKILL} skill's DELIVER mode for litigation job {job_id}.",
            "Kind: litigation.",
            f"Trigger: {trig if trig in ('request', 'scheduled') else 'unknown'}.",
            f"Outcome: {state}.",
            f"Matters: {int(fields.get('matters_total') or 0)}; re-read this run: {int(fields.get('matters_reread') or 0)}; new flags: {int(fields.get('flags_new') or 0)}.",
            f"Folder id: {fields.get('folder_id') or 'none'}.",
            f"Files: {files}.",
            f"Requested by: {env.get('requester') or ''}.",
        ]
        if state != "delivered":
            where = str(fields.get("stage") or stage or "")
            lines.append(
                f"Reason: stopped at {where if re.fullmatch(r'[a-z0-9_]{1,24}', where) else 'the runner (no verdict)'}."
            )
        return "\n".join(lines)


def build_lane(d: Daemon) -> LitigationLane | None:
    raw = os.environ.get(QUEUE_ENV, "").strip()
    if not raw:
        logger.info("no %s; the litigation lane is not started", QUEUE_ENV)
        return None
    from .litigation.manifest import state_dir

    return LitigationLane(
        run_dir=d.run_dir,
        broker=LitigationBroker(d.broker.client if hasattr(d.broker, "client") else d.broker),
        runner_cmd=litigation_runner_cmd(),
        customer_slug=d.customer_slug,
        sticky_db=d.sticky_db,
        cgroup_root=d.cgroup_root,
        memory_max=int(os.environ.get(MEMORY_ENV) or DEFAULT_MEMORY),
        wipe_hours=d.wipe_hours,
        held_wipe_hours=d.held_wipe_hours,
        child_uid=d.child_uid,
        queue_dir=Path(raw),
        inputs_dir=os.environ.get(INPUTS_ENV) or None,
        state_dir=str(state_dir()),
    )


def start_lane(d: Daemon, *, stop: Callable[[], bool], poll_seconds: float) -> threading.Thread | None:
    lane = build_lane(d)
    if lane is None:
        return None
    t = threading.Thread(target=lane.run_forever, args=(stop, poll_seconds), name="litigation-lane", daemon=True)
    t.start()
    logger.info("litigation lane up: queue=%s jobs=%s", lane.queue, lane.jobs)
    return t


__all__ = ["LitigationBroker", "LitigationLane", "build_lane", "read_verdict", "start_lane"]
