"""The drafting lane: the root daemon's third slot, for drafting jobs.

The chronology daemon with the demand lane's four swaps made again
(``demand_lane.py`` is the model; it is owned by the demand lane and is not
imported, so a change there cannot move this lane):

* its own queue (``SMD_DRAFTING_QUEUE_DIR``, written by the broker's
  ``drafting_job_submit`` as ``<queue>/<job_id>.json``) and its own job dirs
  (``<run_dir>/drafting-jobs``);
* the broker's DRAFTING verbs (``drafting_job_status`` / ``drafting_job_record``
  / ``drafting_allowance``, root only, the same shapes as demand's);
* its child: ``medchron draft <job_dir>``, with the drafting firm inputs on the
  allow-listed env, and ``job.json`` stamped fresh before every run with the
  seat's slug, the month's drafting spend and the jobs left this cycle;
* its wake: the ``document-drafter`` skill's DELIVER mode, carrying the job id,
  class, outcome, folder id, the read-back files by ROLE and size, and the
  count of caption discrepancies. Never a file name or a reason: those carry
  client text, and the DELIVER turn reads them from the ledger row.

A schema miss in ``drafting-firm.yaml`` DEFERS drafting jobs only. A failed
job (our machinery) wakes DELIVER too: the skill's status read raises SMD's
shortfall alert, and the broker refuses any reply to the client.
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
from .daemon import CHILD_ENV_PASS, RUNNER_BIN, TERMINAL, Daemon, sticky_level

logger = logging.getLogger("medchron.drafting")

QUEUE_ENV = "SMD_DRAFTING_QUEUE_DIR"
INPUTS_ENV = "MEDCHRON_DRAFTING_INPUTS"
MEMORY_ENV = "SMD_DRAFTING_MEMORY_MAX_BYTES"
DEFAULT_MEMORY = 1024 * 1024 * 1024
SKILL = "document-drafter"
_ULID = re.compile(r"[0-9A-HJKMNP-TV-Z]{26}")


class DraftingBroker:
    """``status``/``record`` mapped onto the drafting verbs, plus the month's
    drafting spend and the jobs left this cycle."""

    def __init__(self, client: BrokerClient) -> None:
        self.client = client

    def status(self, job_id: str) -> dict[str, Any] | None:
        return self.client._request({"action": "drafting_job_status", "job_id": job_id}).get("job")

    def record(self, job_id: str, state: str, fields: dict[str, Any]) -> dict[str, Any]:
        return self.client._request(
            {"action": "drafting_job_record", "job_id": job_id, "state": state, "fields": fields}
        )

    def month_state(self, exclude_job_id: str) -> tuple[int, int]:
        """(this Pacific month's drafting spend in cents, drafting jobs left this
        cycle), both with this job excluded. A missing field raises: the lane
        defers rather than run unmetered."""
        resp = self.client._request({"action": "drafting_allowance", "exclude_job_id": exclude_job_id})
        cents, remaining = resp.get("cents_used"), resp.get("remaining")
        for name, v in (("cents_used", cents), ("remaining", remaining)):
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                raise BrokerError(f"drafting_allowance carried no {name}; the job cannot be metered")
        return int(cents), int(remaining)  # type: ignore[arg-type]


def drafting_runner_cmd() -> list[str]:
    privs = ["setpriv", "--reuid=medchron", "--regid=medchron", "--init-groups", "--no-new-privs"]
    return [*privs, "nice", "-n", "10", RUNNER_BIN, "draft"]


@dataclass
class DraftingLane(Daemon):
    queue_dir: Path = field(default_factory=lambda: Path("/run/smd-medchron/drafting-queue"))
    inputs_dir: str | None = None
    LANE = "drafting"
    HEARTBEAT = "drafting-heartbeat"

    @property
    def queue(self) -> Path:
        return self.queue_dir

    @property
    def jobs(self) -> Path:
        return self.run_dir / "drafting-jobs"

    # -- one job -------------------------------------------------------------------
    def _write_job(self, job_id: str) -> Path:
        jd = self.job_dir(job_id)
        env = json.loads((jd / "envelope.json").read_text(encoding="utf-8"))
        cents, remaining = self.broker.month_state(job_id)
        doc = {
            **env,
            "job_id": job_id,
            "kind": "drafting",
            "slug": self.customer_slug,
            "month_cents_used": cents,
            "allowance_remaining": remaining,
        }
        (jd / "data").mkdir(exist_ok=True)
        self._seed_from_superseded(jd, str(env.get("supersedes") or ""))
        (jd / "job.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
        self._chown_child(jd)
        return jd

    def _seed_from_superseded(self, jd: Path, old_id: str) -> None:
        """A re-run starts where the job it supersedes stopped (demand's
        ``seed``, which is kind-agnostic: it copies ``data/`` minus the dates,
        the gate, the render and the spend ledger)."""
        from .demand import seed

        if not _ULID.fullmatch(old_id) or not (self.job_dir(old_id) / "data").is_dir():
            return
        carried = seed.seed(jd, self.job_dir(old_id))
        logger.info("drafting re-run %s seeded from %s: %s", jd.name, old_id, ", ".join(carried) or "nothing")

    def run_job(self, job_id: str) -> str:
        from .drafting import firm as drafting_firm

        try:
            drafting_firm.load(self.inputs_dir)
        except drafting_firm.DraftingConfigError as exc:
            logger.warning("drafting inputs not usable yet, deferring %s: %s", job_id, exc)
            return "deferred"
        try:
            jd = self._write_job(job_id)
            self.broker.record(job_id, "running", {})
        except BrokerError as exc:
            logger.warning("broker not ready for %s, deferring: %s", job_id, exc)
            return "deferred"
        env = {k: v for k, v in os.environ.items() if k in CHILD_ENV_PASS}
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
            (self.run_dir / "drafting-child.pid").write_text(str(proc.pid))
            out, _ = proc.communicate()
        (self.run_dir / "drafting-child.pid").unlink(missing_ok=True)
        self.jobs_run += 1
        return self._report(job_id, proc.returncode, out or "")

    def _report(self, job_id: str, code: int, out: str) -> str:
        outcomes = verdict_mod.read(self.job_dir(job_id), out)
        v = outcomes[0] if outcomes and isinstance(outcomes[0], dict) else None
        if v is None:
            state, v = "failed", {"reason": f"the runner exited {code} without a verdict", "stage": None}
        else:
            state = {"delivered": "delivered", "held": "held"}.get(str(v.get("outcome")), "failed")
        cents = int(round(float(v.get("dollars") or 0) * 100))
        fields: dict[str, Any] = {
            "cents": cents,
            "caption_discrepancies": list(v.get("caption_discrepancies") or []),
            "markers": list(v.get("markers") or []),
        }
        if v.get("reason"):
            fields["reason"] = str(v["reason"])[:500]
        if state == "delivered":
            fields["folder_id"] = str(v.get("folder_id") or "")
            fields["delivery"] = {"files": list(v.get("files") or []), "cents": cents}
        try:
            self.broker.record(job_id, state, fields)
        except BrokerError as exc:
            logger.error("could not record %s for %s: %s (the state file keeps it)", state, job_id, exc)
        self._write_state(job_id, state=state, finished_at=self.clock(), reason=fields.get("reason"))
        task = self._compose_wake(job_id, state, {**fields, "files": v.get("files") or []}, stage=v.get("stage"))
        self._write_state(job_id, wake={"pending": True, "attempts": 0, "task": task})
        return state

    def _compose_wake(self, job_id: str, state: str, fields: dict[str, Any], *, stage: str | None = None) -> str:
        """Only code- and broker-authored text rides the unfenced wake: never a
        file name, never the runner's free-text reason."""
        env: dict[str, Any] = {}
        try:
            env = json.loads((self.job_dir(job_id) / "envelope.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        files = (
            "; ".join(f"{f.get('role') or 'file'} ({f.get('size')} bytes)" for f in fields.get("files") or []) or "none"
        )
        cls = str(env.get("document_class") or "")
        lines = [
            f"Run the {SKILL} skill's DELIVER mode for drafting job {job_id}.",
            "Kind: drafting.",
            f"Document class: {cls if re.fullmatch(r'[a-z_]{1,40}', cls) else 'unknown'}.",
            f"Outcome: {state}.",
            f"Spend cents: {int(fields.get('cents') or 0)}.",
            f"Matter number: {env.get('matter_number', '')}.",
            f"Folder id: {fields.get('folder_id') or 'none'}.",
            f"Files: {files}.",
            f"Requested by: {env.get('requester') or ''}.",
            f"Caption discrepancies: {len(fields.get('caption_discrepancies') or [])}.",
        ]
        if state != "delivered":
            lines.append(f"Reason: stopped at {stage or 'the runner (no verdict)'}.")
        return "\n".join(lines)

    def _paused(self, job_id: str) -> bool:
        return sticky_level(self.sticky_db) == "HARD_STOP"

    # -- the loop: no head-of-line starvation, and held is final ---------------------
    def _refuse_held_resumes(self) -> None:
        if not self.queue.is_dir():
            return
        for marker in self.queue.glob(f"{resume_mod.MARKER_PREFIX}*.json"):
            job_id = marker.name[len(resume_mod.MARKER_PREFIX) : -len(".json")]
            if self._daemon_state(job_id).get("state") == "held":
                logger.error("resume of held drafting job %s refused: a held job is final; ask again", job_id)
                marker.unlink(missing_ok=True)

    def _ready(self) -> list[str]:
        now = self.clock()
        return [j for j in self._in_progress() if float(self._daemon_state(j).get("retry_after") or 0) <= now]

    def _defer(self, job_id: str) -> None:
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
        except Exception:  # one job's crash backs that job off; it must not wedge the lane's head
            logger.exception("drafting job %s could not be started; backing it off", job_id)
            result = "deferred"
        if result == "deferred":
            self._defer(job_id)
        else:
            self._write_state(job_id, deferrals=0, retry_after=None)
        return result


def build_lane(d: Daemon) -> DraftingLane | None:
    """The lane beside a built chronology daemon, or None when this seat has no
    drafting queue (an image whose entrypoint predates the lane)."""
    raw = os.environ.get(QUEUE_ENV, "").strip()
    if not raw:
        logger.info("no %s; the drafting lane is not started", QUEUE_ENV)
        return None
    return DraftingLane(
        run_dir=d.run_dir,
        broker=DraftingBroker(d.broker.client if isinstance(d.broker, DraftingBroker) else d.broker),
        runner_cmd=drafting_runner_cmd(),
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


def start_lane(d: Daemon, *, stop: Callable[[], bool], poll_seconds: float) -> threading.Thread | None:
    lane = build_lane(d)
    if lane is None:
        return None
    t = threading.Thread(target=lane.run_forever, args=(stop, poll_seconds), name="drafting-lane", daemon=True)
    t.start()
    logger.info("drafting lane up: queue=%s jobs=%s", lane.queue, lane.jobs)
    return t


__all__ = ["DraftingBroker", "DraftingLane", "TERMINAL", "build_lane", "start_lane"]
