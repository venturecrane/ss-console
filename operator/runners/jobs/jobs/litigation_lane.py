"""The litigation lane: the root daemon's fourth slot, for litigation status jobs.

The drafting lane with the litigation swaps (it subclasses ``DraftingLane`` for
the loop, the held-is-final rule, the back-off and the cgroup join):

* its own queue (``SMD_LITIGATION_QUEUE_DIR``, written by the broker's
  ``litigation_job_submit`` as ``<queue>/<job_id>.json``), job dirs
  ``<run_dir>/litigation-jobs``, tick ``<run_dir>/litigation-tick``, child pid
  ``<run_dir>/litigation-child.pid``;
* the broker's LITIGATION verbs: ``litigation_job_status`` (a job's row; with
  no job id, the root view's ``month_cents``) and ``litigation_job_record``;
* its child: ``jobs litigate <job_dir>``, with the litigation inputs and
  the lane's state dir on the allow-listed env, and ``job.json`` stamped fresh
  before every run with the slug, the month's litigation spend and the two
  caps from ``litigation-firm.yaml``;
* its wake: the ``litigation-status`` skill's DELIVER mode, counts only;
* the billing cycle's status-list allowance (2026-10-24), read when a status
  list starts, before anything is spent (the chronology page allowance's
  moment): a list past the cycle's included lists is held ``list_allowance:``
  without starting the child and SMD is paged ``limit_held`` to quote it. A
  deadlines report, a staff reassignment and a proof run's twin are never held
  by it;
* its stops (Captain, 2026-11-06): a held, failed or waiting list pages SMD
  once (``job_notes.tell``, ``job_stopped``) and the firm is told nothing; the
  DELIVER turn it wakes is refused its bind (broker ``stop_note``). A delivered
  list is replied on by that turn, in the skill's words, as before.

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
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import job_notes, spend, verdict as verdict_mod
from .broker_client import BrokerClient, BrokerError
from .firm_inputs import pin_env
from .daemon import RUNNER_BIN, Daemon
from .proof import holds as proof_holds, watch as proof_watch
from .skill_load import load_line
from .drafting_lane import DraftingLane
from . import seat_layout

logger = logging.getLogger("jobs.litigation")

QUEUE_ENV = "SMD_LITIGATION_QUEUE_DIR"
INPUTS_ENV = "JOBS_LITIGATION_INPUTS"
STATE_ENV = "JOBS_LITIGATION_STATE_DIR"
#: The text cache (``litigation/textcache.py``): a dir the child owns on the
#: volume, made by entrypoint-litigation.sh. Passed like the state dir, so the
#: child writes where the boot made room, never an unwritable default.
TEXTCACHE_ENV = "JOBS_LITIGATION_TEXTCACHE_DIR"
MEMORY_ENV = "SMD_LITIGATION_MEMORY_MAX_BYTES"
DEFAULT_MEMORY = 1024 * 1024 * 1024
SKILL = "litigation-status"
#: The demand cgroup's other children (the case file job shares it too).
BUSY_PIDS = ("demand-child.pid", "drafting-child.pid", "casefile-child.pid", "records-child.pid")
BUSY_RETRY_SECONDS = 30.0
HEARTBEAT_SECONDS = 30.0
#: The deadlines report's file names (``litigation/deadlines/book.py``), the
#: only names a wake ever carries: a fixed template, a date, a job suffix.
DEADLINES_FILE = re.compile(
    r"(Litigation deadlines|Statute report) \d{1,2}-\d{1,2}-\d{2} \(Operator [0-9A-Z]{6}\)\.xlsx"
)


class LitigationBroker:
    def __init__(self, client: BrokerClient) -> None:
        self.client = client

    def status(self, job_id: str) -> dict[str, Any] | None:
        return self.client._request({"action": "litigation_job_status", "job_id": job_id}).get("job")

    def record(self, job_id: str, state: str, fields: dict[str, Any]) -> dict[str, Any]:
        return self.client._request(
            {"action": "litigation_job_record", "job_id": job_id, "state": state, "fields": fields}
        )

    def list_allowance(self, job_id: str) -> dict[str, Any]:
        """The cycle's status-list allowance without this job. A missing value
        raises: the lane defers rather than run a list it cannot meter."""
        v = self.client._request({"action": "litigation_job_status", "exclude_job_id": job_id}).get("list_allowance")
        if not isinstance(v, dict) or not isinstance(v.get("authored"), bool):
            raise BrokerError("litigation_job_status carried no list_allowance; the list cannot be metered")
        return v

    def month_cents(self, exclude_job_id: str) -> int:
        """This Pacific month's litigation spend. A missing value raises: the
        lane defers rather than run unmetered.
        The job itself is left out (``exclude_job_id``): its child adds its
        own spend, so a resumed job is counted once, not twice."""
        v = self.client._request({"action": "litigation_job_status", "exclude_job_id": exclude_job_id}).get(
            "month_cents"
        )
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise BrokerError("litigation_job_status carried no month_cents; the job cannot be metered")
        return v


def litigation_runner_cmd() -> list[str]:
    privs = ["setpriv", "--reuid=jobs", "--regid=jobs", "--init-groups", "--no-new-privs"]
    return [*privs, "nice", "-n", "10", RUNNER_BIN, "litigate"]


@dataclass
class LitigationLane(DraftingLane):
    queue_dir: Path = field(default_factory=lambda: Path(seat_layout.LITIGATION_QUEUE))
    inputs_dir: str | None = None
    state_dir: str | None = None
    textcache_dir: str | None = None
    heartbeat_seconds: float = HEARTBEAT_SECONDS
    LANE = "litigation"
    HEARTBEAT = "litigation-heartbeat"
    LOGGER = "jobs.litigation"
    DRAFTING_STEPS = False
    ONE_VERDICT = ("delivered", "held", "failed")
    INPUTS_DEFERRED = "litigation inputs not usable yet, deferring %s once: %s"
    BUSY_RETRY_SECONDS = BUSY_RETRY_SECONDS
    BUSY_PIDS = BUSY_PIDS

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
            "month_cents_used": self.broker.month_cents(job_id),
            "per_job_cap_usd": float(firm.data["per_job_cap_usd"]),
            "monthly_budget_usd": float(firm.data["monthly_budget_usd"]),
        }
        (jd / "data").mkdir(exist_ok=True)
        (jd / "job.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
        self._chown_child(jd)
        return jd

    def _before_start(self, job_id: str) -> str | None:
        gated = self._gate(job_id)
        return gated if gated is not None else self._allowance_hold(job_id)

    def _inputs_error(self) -> Exception | None:
        from .litigation import firm as lit_firm

        try:
            lit_firm.load(self.inputs_dir)
        except lit_firm.LitigationConfigError as exc:
            return exc
        return None

    def _lane_env(self, job_id: str) -> dict[str, str]:
        out: dict[str, str] = {}
        if self.inputs_dir:
            out[INPUTS_ENV] = str(pin_env(self.inputs_dir))
        if self.state_dir:
            out[STATE_ENV] = str(self.state_dir)
        if self.textcache_dir:
            out[TEXTCACHE_ENV] = str(self.textcache_dir)
        return out

    def _allowance_reason(self, job_id: str) -> str | None:
        """The ``list_allowance:`` hold reason when this status list may not
        start, else None. Raises ``BrokerError`` when the broker cannot say."""
        if proof_watch.in_proof_run(self):
            return None  # a proof run's twin files nothing and is never delivered
        try:
            env = json.loads((self.job_dir(job_id) / "envelope.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            env = {}
        if str(env.get("report") or "status") != "status":
            return None  # the allowance is for status lists only
        state = self.broker.list_allowance(job_id)
        if not state["authored"]:
            return None  # unset: no cap
        row = (self.broker.status(job_id) or {}).get("row") or {}
        if row.get("over_allowance_ref"):
            return None  # SMD quoted this list and the firm accepted (root's resubmit)
        if not state.get("readable"):
            problem = str(state.get("problem") or "unreadable")[:200]
            return (
                f"list_allowance: the authored list allowance or its billing cycle cannot be read ({problem}); "
                "the list was not started"
            )
        used, allowance = int(state.get("used") or 0), int(state.get("allowance") or 0)
        if used < allowance:
            return None
        return (
            f"list_allowance: {used} of the {allowance} status lists included in {state.get('cycle') or 'this cycle'} "
            "are delivered; the list was not started and is held for SMD to quote"
        )

    def _allowance_hold(self, job_id: str) -> str | None:
        """Hold a status list past the cycle's allowance before anything runs:
        ``running -> held`` with nothing spent, SMD paged (``proof/watch.settle``
        pages ``limit_held`` for a ``list_allowance:`` reason, and
        ``job_notes`` its one ``job_stopped``). The firm is told nothing
        (Captain, 2026-11-06): the DELIVER turn it wakes is refused its bind."""
        try:
            reason = self._allowance_reason(job_id)
            if reason is None:
                return None
            self.broker.record(job_id, "running", {})
        except BrokerError as exc:
            logger.warning("broker could not meter %s's list allowance, deferring: %s", job_id, exc)
            return "deferred"
        cents = spend.recorded_cents(self.job_dir(job_id), self.LANE, 0)  # a resumed job keeps what it spent
        fields: dict[str, Any] = {"cents": cents, "reason": reason[:500], "stage": "allowance"}
        state, fields = proof_watch.settle(self, job_id, [], "held", fields)
        try:
            self.broker.record(job_id, state, fields)
        except BrokerError as exc:
            logger.error("could not record %s for %s: %s (the state file keeps it)", state, job_id, exc)
        self._write_state(job_id, state=state, finished_at=self.clock(), reason=fields.get("reason"))
        if state != "delivered":
            # A held or failed list pages SMD; the firm is told nothing
            # (job_notes, Captain 2026-11-06).
            job_notes.tell(
                self, job_id, why=f"the job ended {state}", recorded={"state": state, "reason": fields.get("reason")}
            )
        self._write_state(
            job_id, wake={"pending": True, "attempts": 0, "task": self._compose_wake(job_id, state, fields)}
        )
        return state

    def _fail_unconfigured(self, job_id: str, exc: Exception) -> str:
        reason = (
            "config_missing: the litigation firm inputs are not usable on this seat: " + str(exc).split(";")[0][:300]
        )
        try:
            self.broker.record(job_id, "running", {})
            cents = spend.recorded_cents(self.job_dir(job_id), self.LANE, 0)
            self.broker.record(job_id, "failed", {"cents": cents, "reason": reason, "stage": "config"})
        except BrokerError as err:
            logger.error("could not record config_missing for %s: %s", job_id, err)
            return "deferred"
        self._write_state(job_id, state="failed", finished_at=self.clock(), reason=reason)
        job_notes.tell(self, job_id, why="the job ended failed")
        task = self._compose_wake(job_id, "failed", {"stage": "config"})
        self._write_state(job_id, wake={"pending": True, "attempts": 0, "task": task})
        return "failed"

    def _report(self, job_id: str, code: int, out: str) -> str:
        v = verdict_mod.one(self.job_dir(job_id), out, self.ONE_VERDICT)
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
        fields["cents"] = spend.recorded_cents(self.job_dir(job_id), self.LANE, fields["cents"])
        fields["files"] = [f for f in v.get("files") or [] if isinstance(f, dict)]
        if isinstance(v.get("counts"), dict):  # the deadlines report's named counts (integers only)
            fields["counts"] = {str(k): int(n) for k, n in v["counts"].items() if isinstance(n, int)}
        if isinstance(v.get("writes"), list):  # a reassignment's writes, onto the job's audit row
            fields["writes"] = [w for w in v["writes"] if isinstance(w, dict)]
        if v.get("reason"):
            fields["reason"] = str(v["reason"])[:500]
        state, fields = proof_watch.settle(self, job_id, [v], state, fields)
        try:
            self.broker.record(job_id, state, fields)
        except BrokerError as exc:
            logger.error("could not record %s for %s: %s (the state file keeps it)", state, job_id, exc)
        self._write_state(job_id, state=state, finished_at=self.clock(), reason=fields.get("reason"))
        if state != "delivered":
            # A held or failed list pages SMD; the firm is told nothing
            # (job_notes, Captain 2026-11-06).
            job_notes.tell(
                self, job_id, why=f"the job ended {state}", recorded={"state": state, "reason": fields.get("reason")}
            )
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
        if env.get("report") == "deadlines":
            return self._deadlines_wake(job_id, state, fields, env, trig, stage)
        if env.get("report") == "reassign":
            from .litigation.reassign import wake as reassign_wake

            verdict = verdict_mod.one(self.job_dir(job_id), "", self.ONE_VERDICT) or {}
            return reassign_wake.compose(
                job_id=job_id, skill=SKILL, state=state, fields=fields, env=env, trig=trig, stage=stage,
                detail=verdict.get("reassign") or {},
                hold_line=proof_holds.wake_line("litigation", fields.get("reason")) if state == "held" else None,
            )  # fmt: skip
        lines = [
            f"Run the {SKILL} skill's DELIVER mode for litigation job {job_id}.",
            load_line(SKILL),
            "Kind: litigation." + (" Report: discovery breakdown." if env.get("report") == "discovery12" else ""),
            f"Trigger: {trig if trig in ('request', 'scheduled') else 'unknown'}.",
            f"Outcome: {state}.",
            f"Matters: {int(fields.get('matters_total') or 0)}; re-read this run: {int(fields.get('matters_reread') or 0)}; new flags: {int(fields.get('flags_new') or 0)}.",
            f"Folder id: {fields.get('folder_id') or 'none'}.",
            f"Files: {files}.",
            f"Requested by: {env.get('requester') or ''}.",
        ]
        if env.get("report") == "discovery12":
            # A count, never a matter: files the reading service declined twice,
            # printed in the breakdown for a hand check (discovery12/run.py).
            counts = (verdict_mod.one(self.job_dir(job_id), "", self.ONE_VERDICT) or {}).get("counts") or {}
            declined = int(counts.get("d12_declined") or 0)
            lines.append(f"Declined by the reading service (marked for a hand check): {declined}.")
        if state != "delivered":
            where = str(fields.get("stage") or stage or "")
            lines.append(
                f"Reason: stopped at {where if re.fullmatch(r'[a-z0-9_]{1,24}', where) else 'the runner (no verdict)'}."
            )
        if state == "held":  # the firm-safe token the skill words; the reason stays SMD's
            lines.append(proof_holds.wake_line("litigation", fields.get("reason")))
        return "\n".join(lines)

    def _deadlines_wake(
        self, job_id: str, state: str, fields: dict[str, Any], env: dict[str, Any], trig: str, stage: str | None
    ) -> str:
        """The deadlines report's wake. Code-authored text only: integers, the
        file names this code composed (a fixed template, the date and the job
        suffix, never a matter fact), and tokens."""
        raw = fields.get("counts")
        counts: dict[str, Any] = raw if isinstance(raw, dict) else {}
        folder = str(env.get("folder_name") or "")  # authored config, the broker's copy (ledger _FOLDER)
        names = [
            str(f.get("name") or "")
            for f in fields.get("files") or []
            if DEADLINES_FILE.fullmatch(str(f.get("name") or ""))
        ]
        lines = [
            f"Run the {SKILL} skill's DELIVER mode for litigation job {job_id}.",
            load_line(SKILL),
            "Kind: litigation. Report: deadlines.",
            f"Trigger: {trig if trig in ('request', 'scheduled') else 'unknown'}.",
            f"Outcome: {state}.",
            "Counts: " + ("; ".join(f"{k} {int(v)}" for k, v in sorted(counts.items())) or "none") + ".",
            f"Folder: {folder if re.fullmatch(r'[A-Za-z0-9 ._()-]{1,80}', folder) else 'none'}.",
            f"Files: {'; '.join(names) or 'none'}.",
            f"Requested by: {env.get('requester') or ''}.",
        ]
        if state != "delivered":
            where = str(fields.get("stage") or stage or "")
            lines.append(
                f"Reason: stopped at {where if re.fullmatch(r'[a-z0-9_]{1,24}', where) else 'the runner (no verdict)'}."
            )
        if state == "held":
            lines.append(proof_holds.wake_line("litigation", fields.get("reason")))
        return "\n".join(lines)


def build_lane(d: Daemon) -> LitigationLane | None:
    raw = os.environ.get(QUEUE_ENV, "").strip()
    if not raw:
        logger.info("no %s; the litigation lane is not started", QUEUE_ENV)
        return None
    from .litigation.manifest import state_dir
    from .litigation.textcache import cache_dir

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
        textcache_dir=str(cache_dir()),
    )


def start_lane(d: Daemon, *, stop: Callable[[], bool], poll_seconds: float) -> threading.Thread | None:
    lane = build_lane(d)
    if lane is None:
        return None
    t = threading.Thread(target=lane.run_forever, args=(stop, poll_seconds), name="litigation-lane", daemon=True)
    t.start()
    logger.info("litigation lane up: queue=%s jobs=%s", lane.queue, lane.jobs)
    return t


__all__ = ["LitigationBroker", "LitigationLane", "build_lane", "start_lane"]
