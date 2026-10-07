"""The demand lane: the root daemon's second slot, for demand jobs.

A demand reads a whole matter and runs several model stages; a chronology can
run for hours. One slot for both would let either starve the other, so the
daemon runs this lane on its own thread (review of the plan, 2026-10-06): one
chronology and one demand at a time, each child in its own memory cgroup, the
two caps summing under the Machine's memory (2.5 GB + 1 GB defaults).

It is the chronology daemon with four things swapped, and nothing else:

* its own queue (``SMD_DEMAND_QUEUE_DIR``, written by the broker's
  ``demand_job_submit``) and its own job dirs (``<run_dir>/demand-jobs``);
* the broker's DEMAND verbs (``demand_job_status`` / ``demand_job_record``,
  root only, the shape ``workspace_broker/demand_ledger.py`` defines);
* its child: ``medchron demand <job_dir>``, with the demand firm inputs on the
  allow-listed env, and ``job.json`` stamped fresh before every run with the
  month's demand spend (a resume is never metered against its own cents);
* its wake: the ``demand-letter-drafter`` skill's DELIVER mode, carrying the
  job id, outcome, folder id and the read-back file names and sizes.

A schema miss in ``demand-firm.yaml`` DEFERS demand jobs only; the chronology
lane never reads that file. A seat at HARD_STOP runs nothing here either, but
records nothing: the demand ledger has no held-and-resume edge, so the job just
stays claimed until the stop clears.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import resume as resume_mod, verdict as verdict_mod
from .broker_client import BrokerClient, BrokerError
from .daemon import CHILD_ENV_PASS, RUNNER_BIN, TERMINAL, Daemon, sticky_level

logger = logging.getLogger("medchron.demand")

QUEUE_ENV = "SMD_DEMAND_QUEUE_DIR"
INPUTS_ENV = "MEDCHRON_DEMAND_INPUTS"
MEMORY_ENV = "SMD_DEMAND_MEMORY_MAX_BYTES"
DEFAULT_MEMORY = 1024 * 1024 * 1024
SKILL = "demand-letter-drafter"
VENDOR_ENV_PASS = ("RECORDS_VENDOR_API_TOKEN", "RECORDS_VENDOR_API_URL", "RECORDS_VENDOR_NAME")


class DemandBroker:
    """The chronology daemon speaks ``status``/``record``; this maps them onto
    the demand verbs, and adds the month's demand spend."""

    def __init__(self, client: BrokerClient) -> None:
        self.client = client

    def status(self, job_id: str) -> dict[str, Any] | None:
        return self.client._request({"action": "demand_job_status", "job_id": job_id}).get("job")

    def record(self, job_id: str, state: str, fields: dict[str, Any]) -> dict[str, Any]:
        return self.client._request({"action": "demand_job_record", "job_id": job_id, "state": state, "fields": fields})

    def month_state(self, exclude_job_id: str) -> tuple[int, int]:
        """(the Pacific calendar month's demand spend in cents, the demands
        left this cycle), both with this job excluded, so a resume is metered
        against neither its own cents nor its own reservation. A missing or
        malformed field raises: the lane defers rather than run unmetered."""
        resp = self.client._request({"action": "demand_allowance", "exclude_job_id": exclude_job_id})
        cents, remaining = resp.get("cents_used"), resp.get("remaining")
        for name, v in (("cents_used", cents), ("remaining", remaining)):
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                raise BrokerError(f"demand_allowance carried no {name}; the job cannot be metered")
        return int(cents), int(remaining)  # type: ignore[arg-type]

    def month_cents(self, exclude_job_id: str) -> int:
        return self.month_state(exclude_job_id)[0]


def demand_runner_cmd() -> list[str]:
    return [
        "setpriv",
        "--reuid=medchron",
        "--regid=medchron",
        "--init-groups",
        "--no-new-privs",
        "nice",
        "-n",
        "10",
        RUNNER_BIN,
        "demand",
    ]


@dataclass
class DemandLane(Daemon):
    queue_dir: Path = field(default_factory=lambda: Path("/run/smd-medchron/demand-queue"))
    inputs_dir: str | None = None
    LANE = "demand"
    HEARTBEAT = "demand-heartbeat"

    @property
    def queue(self) -> Path:
        return self.queue_dir

    @property
    def jobs(self) -> Path:
        return self.run_dir / "demand-jobs"

    # -- one job -------------------------------------------------------------------
    def _write_job(self, job_id: str) -> Path:
        jd = self.job_dir(job_id)
        env = json.loads((jd / "envelope.json").read_text(encoding="utf-8"))
        doc = {
            **env,
            "job_id": job_id,
            "kind": "demand",
            "slug": self.customer_slug,
            **dict(zip(("month_cents_used", "allowance_remaining"), self.broker.month_state(job_id))),
        }
        (jd / "data").mkdir(exist_ok=True)
        (jd / "job.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
        self._chown_child(jd)
        return jd

    def run_job(self, job_id: str) -> str:
        from .demand import firm as demand_firm

        try:
            demand_firm.load(self.inputs_dir)
        except demand_firm.DemandConfigError as exc:
            logger.warning("demand inputs not usable yet, deferring %s: %s", job_id, exc)
            return "deferred"
        try:
            jd = self._write_job(job_id)
            self.broker.record(job_id, "running", {})
        except BrokerError as exc:
            logger.warning("broker not ready for %s, deferring: %s", job_id, exc)
            return "deferred"
        # The demand child also reads the records vendor's directory (the gap
        # audit's missing providers): its three per-seat values, nothing more.
        env = {k: v for k, v in os.environ.items() if k in CHILD_ENV_PASS or k in VENDOR_ENV_PASS}
        env.update(self.child_env, MEDCHRON_DAEMON_JOB_ID=str(job_id))
        if self.inputs_dir:
            env[INPUTS_ENV] = str(self.inputs_dir)
        env.setdefault("MEDCHRON_SEAT", "client")
        env.setdefault("PATH", "/usr/bin:/bin")
        env.setdefault("HOME", str(jd))
        verdict_mod.clear(jd)
        with (jd / "daemon.log").open("a", encoding="utf-8") as log:
            proc = subprocess.Popen(  # noqa: S603 - argv is the configured runner command plus the job dir, no shell; env filtered
                resume_mod.start_run(self, job_id, [*self.runner_cmd, str(jd)]),
                cwd=str(jd),
                env=env,
                stdout=subprocess.PIPE,
                stderr=log,
                text=True,
                preexec_fn=self._cgroup_preexec(),
            )
            (self.run_dir / "demand-child.pid").write_text(str(proc.pid))
            out, _ = proc.communicate()
        (self.run_dir / "demand-child.pid").unlink(missing_ok=True)
        self.jobs_run += 1
        return self._report(job_id, proc.returncode, out or "")

    def _report(self, job_id: str, code: int, out: str) -> str:
        outcomes = verdict_mod.read(self.job_dir(job_id), out)
        v = outcomes[0] if outcomes and isinstance(outcomes[0], dict) else None
        if v is None:
            state, v = "failed", {"reason": f"the runner exited {code} without a verdict", "stage": None}
        else:
            state = {"delivered": "delivered", "held": "held"}.get(str(v.get("outcome")), "failed")
        fields: dict[str, Any] = {"cents": int(round(float(v.get("dollars") or 0) * 100))}
        if v.get("reason"):
            fields["reason"] = str(v["reason"])[:500]
        if state == "delivered":
            fields["folder_id"] = str(v.get("folder_id") or "")
            fields["delivery"] = {
                "files": list(v.get("files") or []),
                "coverage_report": bool(v.get("coverage_report")),
                "cents": fields["cents"],
            }
        try:
            self.broker.record(job_id, state, fields)
        except BrokerError as exc:
            logger.error("could not record %s for %s: %s (the state file keeps it)", state, job_id, exc)
        now = self.clock()
        self._write_state(job_id, state=state, finished_at=now, reason=fields.get("reason"))
        task = self._compose_wake(
            job_id,
            state,
            {**fields, "files": v.get("files") or [], "coverage_report": bool(v.get("coverage_report"))},
            stage=v.get("stage"),
        )
        self._write_state(job_id, wake={"pending": True, "attempts": 0, "task": task})
        return state

    def _compose_wake(self, job_id: str, state: str, fields: dict[str, Any], *, stage: str | None = None) -> str:
        """The DELIVER turn's task. Only code-authored and broker-authored text
        rides it: the outcome word, the matter number and the requester from
        the envelope, the folder id, the files the read-back saw (named by this
        runner: ``Demand.<Client>.docx``, the dated gap audit), and WHERE it
        stopped as a stage name. Never the runner's free-text reason, which can
        quote a document name, and never a file name, which carries the
        client's: files ride as their role and byte count. The ledger row keeps
        the names and the reason for a person."""
        env: dict[str, Any] = {}
        try:
            env = json.loads((self.job_dir(job_id) / "envelope.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        # By ROLE, never by file name: the demand's file name carries the
        # client's name, and no tenant string rides the unfenced wake prompt.
        # The DELIVER turn reads the names from the folder (review of #3074).
        files = (
            "; ".join(f"{f.get('role') or 'file'} ({f.get('size')} bytes)" for f in fields.get("files") or []) or "none"
        )
        lines = [
            f"Run the {SKILL} skill's DELIVER mode for demand job {job_id}.",
            "Kind: demand.",
            f"Outcome: {state}.",
            # The job's actual model spend, recorded per matter (overage billing
            # reads it); the same figure is the ledger row's cents.
            f"Spend cents: {int(fields.get('cents') or 0)}.",
            f"Matter number: {(env.get('matter') or {}).get('number', '')}.",
            f"Folder id: {fields.get('folder_id') or 'none'}.",
            f"Files: {files}.",
            f"Requested by: {env.get('requested_by') or ''}.",
        ]
        if fields.get("coverage_report"):
            lines.append("Coverage report: yes (the premise gate failed; no demand was drafted).")
        if state != "delivered":
            lines.append(f"Reason: stopped at {stage or 'the runner (no verdict)'}.")
        return "\n".join(lines)

    def _paused(self, job_id: str) -> bool:
        return sticky_level(self.sticky_db) == "HARD_STOP"

    # -- the loop: no head-of-line starvation, and held is final ---------------------
    def _refuse_held_resumes(self) -> None:
        """A held demand is FINAL. The ledger has no held -> running edge, and a
        hold means a person must read the reason (a refused letter, a wrong
        destination); resuming it would replay the same refusal or, worse,
        file what was refused. A marker for one is removed, loudly."""
        if not self.queue.is_dir():
            return
        for marker in self.queue.glob(f"{resume_mod.MARKER_PREFIX}*.json"):
            job_id = marker.name[len(resume_mod.MARKER_PREFIX) : -len(".json")]
            if self._daemon_state(job_id).get("state") == "held":
                logger.error("resume of held demand %s refused: a held demand is final; ask again", job_id)
                marker.unlink(missing_ok=True)

    def _ready(self) -> list[str]:
        now = self.clock()
        return [j for j in self._in_progress() if float(self._daemon_state(j).get("retry_after") or 0) <= now]

    def _defer(self, job_id: str) -> None:
        """Back the job off (1, 2, 4 ... 60 minutes) so a job that keeps
        deferring cannot hold the head of the queue: the next tick claims the
        job behind it (review of #3074)."""
        n = int(self._daemon_state(job_id).get("deferrals") or 0) + 1
        self._write_state(job_id, deferrals=n, retry_after=self.clock() + min(60.0 * 2 ** (n - 1), 3600.0))

    def tick(self) -> str | None:
        self._refuse_held_resumes()
        resume_mod.take_requests(self)
        self.wipe_expired()
        self.dispatch_wakes()
        ready = self._ready()
        job_id = ready[0] if ready else self.claim_next()
        self.heartbeat(running=job_id)
        if job_id is None:
            return None
        if self._paused(job_id):
            return "paused"
        try:
            result = self.run_job(job_id)
        except (
            Exception
        ):  # one job's crash backs that job off; it must not wedge the lane's head (logged with its trace)
            logger.exception("demand job %s could not be started; backing it off", job_id)
            result = "deferred"
        if result == "deferred":
            self._defer(job_id)
        else:
            self._write_state(job_id, deferrals=0, retry_after=None)
        return result


def build_lane(d: Daemon) -> DemandLane | None:
    """The lane beside a built chronology daemon, or None when this seat has no
    demand queue (an image whose entrypoint predates the lane)."""
    raw = os.environ.get(QUEUE_ENV, "").strip()
    if not raw:
        logger.info("no %s; the demand lane is not started", QUEUE_ENV)
        return None
    lane = DemandLane(
        run_dir=d.run_dir,
        broker=DemandBroker(d.broker.client if isinstance(d.broker, DemandBroker) else d.broker),
        runner_cmd=demand_runner_cmd(),
        customer_slug=d.customer_slug,
        sticky_db=d.sticky_db,
        cgroup_root=d.cgroup_root,
        memory_max=int(os.environ.get(MEMORY_ENV) or DEFAULT_MEMORY),
        wipe_hours=d.wipe_hours,
        held_wipe_hours=d.held_wipe_hours,
        child_uid=d.child_uid,
        queue_dir=Path(raw),
        inputs_dir=os.environ.get(INPUTS_ENV) or None,
    )
    return lane


def start_lane(d: Daemon, *, stop: Callable[[], bool], poll_seconds: float) -> threading.Thread | None:
    lane = build_lane(d)
    if lane is None:
        return None
    t = threading.Thread(target=lane.run_forever, args=(stop, poll_seconds), name="demand-lane", daemon=True)
    t.start()
    logger.info("demand lane up: queue=%s jobs=%s", lane.queue, lane.jobs)
    return t


__all__ = ["DemandBroker", "DemandLane", "TERMINAL", "build_lane", "start_lane"]
