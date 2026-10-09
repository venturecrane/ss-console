"""The negotiation lane: a root daemon slot of its own, for the negotiation watch.

The drafting lane's loop (it subclasses ``DraftingLane`` for the claim, the
back-off and the cgroup join) with the negotiation swaps:

* its own queue (``SMD_NEGOTIATION_QUEUE_DIR``, written by the broker's
  ``negotiation_job_submit`` as ``<queue>/<job_id>.json``), job dirs
  ``<run_dir>/negotiation-jobs``, tick ``<run_dir>/negotiation-tick``, child pid
  ``<run_dir>/negotiation-child.pid``;
* the broker's NEGOTIATION verbs: ``negotiation_job_status`` (with no job id,
  the root view's ``month_cents``) and ``negotiation_job_record``;
* its child: ``medchron negotiate <job_dir>``, with the lane's state dir on the
  env and ``job.json`` stamped fresh before every run with the slug and the
  month's negotiation spend (the caps, statuses and the firm's authored
  negotiation design ride the broker's envelope);
* its wakes: ONE PER NEW OFFER. The broker turns each notice the job reported
  into a row with its own id (``negotiation_job_record`` answers with them),
  and each gets its own completion wake, "... for negotiation job <notice id>",
  so the skill's DELIVER mode sends exactly one email per offer through the
  verified binding. A notice's wake rides a terminal job dir of its own, so the
  daemon's at-most-once wake dispatch and its retention apply unchanged. A job
  that FAILED wakes once on its own id (the skill raises SMD's shortfall alert
  and tells the firm nothing); a delivered job with no new offer wakes nobody.

Memory: the child joins the DEMAND lane's memory cgroup and waits while any
other lane's child is live, so the Machine's caps do not grow by a slot.
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

logger = logging.getLogger("medchron.negotiation")

QUEUE_ENV = "SMD_NEGOTIATION_QUEUE_DIR"
STATE_ENV = "MEDCHRON_NEGOTIATION_STATE_DIR"
MEMORY_ENV = "SMD_NEGOTIATION_MEMORY_MAX_BYTES"
DEFAULT_MEMORY = 1024 * 1024 * 1024
SKILL = "negotiation-watch"
BUSY_PIDS = ("demand-child.pid", "drafting-child.pid")
CHILD_PID = "negotiation-child.pid"
BUSY_RETRY_SECONDS = 30.0
HEARTBEAT_SECONDS = 30.0
_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


class NegotiationBroker:
    def __init__(self, client: BrokerClient) -> None:
        self.client = client

    def status(self, job_id: str) -> dict[str, Any] | None:
        return self.client._request({"action": "negotiation_job_status", "job_id": job_id}).get("job")

    def record(self, job_id: str, state: str, fields: dict[str, Any]) -> dict[str, Any]:
        return self.client._request(
            {"action": "negotiation_job_record", "job_id": job_id, "state": state, "fields": fields}
        )

    def month_cents(self) -> int:
        v = self.client._request({"action": "negotiation_job_status"}).get("month_cents")
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise BrokerError("negotiation_job_status carried no month_cents; the job cannot be metered")
        return v


def negotiation_runner_cmd() -> list[str]:
    privs = ["setpriv", "--reuid=medchron", "--regid=medchron", "--init-groups", "--no-new-privs"]
    return [*privs, "nice", "-n", "10", RUNNER_BIN, "negotiate"]


def _live(pidfile: Path) -> bool:
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
    """The runner's one JSON object: verdict.json first, then stdout's last line."""
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
        if isinstance(v, dict) and v.get("verdict") in ("delivered", "failed"):
            return v
    return None


def wake_task(job_id: str, outcome: str, stage: str = "") -> str:
    """Code-authored only: the id, the kind, the trigger, the outcome. Never a
    matter, a name or a figure; the notice row holds those."""
    lines = [
        f"Run the {SKILL} skill's DELIVER mode for negotiation job {job_id}.",
        "Kind: negotiation.",
        "Trigger: scheduled.",
        f"Outcome: {outcome}.",
    ]
    if outcome == "failed":
        where = stage if re.fullmatch(r"[a-z0-9_]{1,24}", stage or "") else "the runner (no verdict)"
        lines.append(f"Reason: stopped at {where}.")
    return "\n".join(lines)


@dataclass
class NegotiationLane(DraftingLane):
    queue_dir: Path = field(default_factory=lambda: Path("/run/smd-medchron/negotiation-queue"))
    state_dir: str | None = None
    heartbeat_seconds: float = HEARTBEAT_SECONDS
    LANE = "negotiation"
    HEARTBEAT = "negotiation-heartbeat"

    @property
    def jobs(self) -> Path:
        return self.run_dir / "negotiation-jobs"

    def _write_job(self, job_id: str) -> Path:
        jd = self.job_dir(job_id)
        env = json.loads((jd / "envelope.json").read_text(encoding="utf-8"))
        doc = {
            **env,
            "job_id": job_id,
            "kind": "negotiation",
            "slug": self.customer_slug,
            "month_cents_used": self.broker.month_cents(),
        }
        (jd / "data").mkdir(exist_ok=True)
        (jd / "job.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
        self._chown_child(jd)
        return jd

    def _busy(self) -> bool:
        return any(_live(self.run_dir / p) for p in BUSY_PIDS)

    def run_job(self, job_id: str) -> str:
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

    def _child_env(self, job_id: str) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k in CHILD_ENV_PASS}
        env.update(self.child_env, MEDCHRON_DAEMON_JOB_ID=str(job_id))
        if self.state_dir:
            env[STATE_ENV] = str(self.state_dir)
        env.setdefault("MEDCHRON_SEAT", "client")
        env.setdefault("PATH", "/usr/bin:/bin")
        env.setdefault("HOME", str(self.job_dir(job_id)))
        return env

    def _spawn(self, job_id: str, jd: Path) -> str:
        verdict_mod.clear(jd)
        stop = threading.Event()
        beat = threading.Thread(target=self._beat, args=(job_id, stop), name="negotiation-heartbeat", daemon=True)
        with (jd / "daemon.log").open("a", encoding="utf-8") as log:
            proc = subprocess.Popen(  # noqa: S603 - argv is the configured runner command plus the job dir, no shell; env filtered
                resume_mod.start_run(self, job_id, [*self.runner_cmd, str(jd)]),
                cwd=str(jd),
                env=self._child_env(job_id),
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

    def _report(self, job_id: str, code: int, out: str) -> str:
        v = read_verdict(self.job_dir(job_id), out)
        if v is None:
            v = {
                "verdict": "failed",
                "stage": None,
                "reason": f"no_verdict: the runner exited {code} without a verdict",
            }
        state = "delivered" if v["verdict"] == "delivered" else "failed"
        fields: dict[str, Any] = {"stage": v.get("stage"), "cents": int(v.get("cents") or 0)}
        for k in ("matters_total", "matters_seeded", "docs_read", "docs_failed"):
            fields[k] = int(v.get(k) or 0)
        fields["notices"] = [n for n in v.get("notices") or [] if isinstance(n, dict)] if state == "delivered" else []
        if v.get("reason"):
            fields["reason"] = str(v["reason"])[:500]
        notice_ids: list[str] = []
        try:
            answer = self.broker.record(job_id, state, fields)
            notice_ids = [str(i) for i in answer.get("notice_ids") or [] if _ULID.match(str(i))]
        except BrokerError as exc:
            logger.error("could not record %s for %s: %s (the state file keeps it)", state, job_id, exc)
        self._write_state(job_id, state=state, finished_at=self.clock(), reason=fields.get("reason"))
        if state == "failed":
            self._write_state(
                job_id,
                wake={"pending": True, "attempts": 0, "task": wake_task(job_id, "failed", str(v.get("stage") or ""))},
            )
        for nid in notice_ids:
            self._notice_wake(nid)
        return state

    def _notice_wake(self, notice_id: str) -> None:
        """A terminal job dir carrying only the notice's pending wake."""
        jd = self.job_dir(notice_id)
        jd.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._write_state(
            notice_id,
            state="delivered",
            notice=True,
            finished_at=self.clock(),
            wake={"pending": True, "attempts": 0, "task": wake_task(notice_id, "notice")},
        )


def build_lane(d: Daemon) -> NegotiationLane | None:
    raw = os.environ.get(QUEUE_ENV, "").strip()
    if not raw:
        logger.info("no %s; the negotiation lane is not started", QUEUE_ENV)
        return None
    from .negotiation.run import default_state_dir

    return NegotiationLane(
        run_dir=d.run_dir,
        broker=NegotiationBroker(d.broker.client if hasattr(d.broker, "client") else d.broker),
        runner_cmd=negotiation_runner_cmd(),
        customer_slug=d.customer_slug,
        sticky_db=d.sticky_db,
        cgroup_root=d.cgroup_root,
        memory_max=int(os.environ.get(MEMORY_ENV) or DEFAULT_MEMORY),
        wipe_hours=d.wipe_hours,
        held_wipe_hours=d.held_wipe_hours,
        child_uid=d.child_uid,
        queue_dir=Path(raw),
        state_dir=os.environ.get(STATE_ENV) or str(default_state_dir()),
    )


def start_lane(d: Daemon, *, stop: Callable[[], bool], poll_seconds: float) -> threading.Thread | None:
    lane = build_lane(d)
    if lane is None:
        return None
    t = threading.Thread(target=lane.run_forever, args=(stop, poll_seconds), name="negotiation-lane", daemon=True)
    t.start()
    logger.info("negotiation lane up: queue=%s jobs=%s", lane.queue, lane.jobs)
    return t


__all__ = ["NegotiationBroker", "NegotiationLane", "build_lane", "start_lane", "wake_task"]
