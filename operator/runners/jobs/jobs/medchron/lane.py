"""The chronology lane: a lane of the jobs runner like the other seven.

Until 2026-11-04 this lane WAS the runner's base class (``daemon.Daemon``), so
the runner core imported the chronology's firm config, coverage, allowance and
update modules, and every other lane inherited them. It is a subclass now, and
the core imports no lane (``tests/test_runner_core_imports_no_lane.py``).

What is the chronology's own, and lives here:

* its child: ``jobs run <job_dir> --json`` (``default_runner_cmd``), with the
  pid file ``<run_dir>/child.pid``, under the ``medchron`` memory cgroup capped
  by ``SMD_MEDCHRON_MEMORY_MAX_BYTES``;
* ``job.yaml``, written fresh before every run and every resume from the
  envelope, the staged vision seed, and the month's allowance read from the
  broker at that moment (never the envelope's stale copy);
* the firm config check: a ``firm.yaml`` this runner cannot read DEFERS the
  job, recording nothing, so customer.yaml and firm.yaml may land in either
  order;
* the verdict's mapping onto the ledger (refused is held; units report job
  level figures, taken once as the largest any unit saw) and the delivery's
  covered/uncovered document ids;
* its deliver wake: the ``medical-chronology-maintainer`` skill's DELIVER mode.

Its on-disk names are UNCHANGED from when it was the base class: the queue is
``<run_dir>/queue``, the job dirs ``<run_dir>/jobs``, the beat
``heartbeat.json`` and the tick ``tick``. Held jobs and the seat self-test read
them; a later volume release renames them (ADR 0103).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .. import (
    daemon as daemon_mod,
    job_notes,
    requeue,
    run_record,
    spend,
    verdict as verdict_mod,
)
from ..broker_client import BrokerError
from ..daemon import RUNNER_BIN, TERMINAL, Daemon
from ..matter import store as page_store_mod
from ..proof import holds as proof_holds, watch as proof_watch
from ..skill_load import load_line
from ..stages import vision_seed
from . import allowance as allowance_mod, config as config_mod, job as job_mod, unrecorded
from .covered import delivery_fields, wake_folder_lines

logger = logging.getLogger("jobs.daemon")

MEMORY_MAX_ENV = "SMD_MEDCHRON_MEMORY_MAX_BYTES"
SKILL = "medical-chronology-maintainer"


def default_runner_cmd() -> list[str]:
    return [
        "setpriv",
        f"--reuid={daemon_mod.CHILD_UID_NAME}",
        f"--regid={daemon_mod.CHILD_UID_NAME}",
        "--init-groups",
        "--no-new-privs",
        "nice",
        "-n",
        "10",
        RUNNER_BIN,
        "run",
    ]


@dataclass
class MedchronLane(Daemon):
    #: Explicit firm-config path; None resolves the env/default the seat uses.
    firm_config: str | None = None
    LANE = "medchron"
    #: Every record carries a run block (``run_record.py``, ``bin/work-health.py --runs``).
    RUN_RECORD = True
    #: Whether this attempt's delivery proof landed (``proof/watch._record_proven``).
    proof_recorded: bool | None = None
    #: When ``poll_requests`` next tries an ending the broker could not take.
    record_retry_at: float = 0.0
    HEARTBEAT = "heartbeat"
    TICK = "tick"
    CHILD_ARGS = ("--json",)
    START_DEFERRED = (
        "could not read the month's allowance for %s, deferring: %s",
        "could not record running for %s: %s",
    )

    # -- one job (the host runs it: ``Daemon.run_job``) ---------------------------
    def _before_start(self, job_id: str) -> str | None:
        # The firm config first, before anything is recorded. A seat whose
        # firm.yaml has not caught up with this runner's schema DEFERS: the job
        # stays claimed, nothing is written to the ledger, and the next tick
        # tries again. That is what makes the rollout safe in either order --
        # customer.yaml and firm.yaml can land minutes apart without a job
        # failing in between.
        try:
            config_mod.load(self.firm_config)
        except config_mod.ConfigError as exc:
            logger.warning("firm config not usable yet, deferring %s: %s", job_id, exc)
            return "deferred"
        return None

    def _running_fields(self, job_id: str) -> dict[str, Any]:
        """The ``running`` record's run block: a resume's own event when
        ``resume.take_requests`` left one for this attempt, else ``start``."""
        pending = self._daemon_state(job_id).get("run_pending")
        if isinstance(pending, dict) and pending.get("event"):
            return {"run": {**pending, "release": run_record.release()}}
        return {"run": run_record.block("start", with_release=True)}

    def _before_child(self, job_id: str) -> None:
        # The running record landed (``_start``): a resume's event is spent.
        if self._daemon_state(job_id).get("run_pending") is not None:
            self._write_state(job_id, run_pending=None)
        super()._before_child(job_id)

    def run_stages(self, job_id: str) -> dict[str, Any]:
        """The job's stage table (a run's or an update's), for its run record."""
        from . import dag

        try:
            env = json.loads((self.job_dir(job_id) / "envelope.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            env = {}
        return dag.by_name(isinstance(env, dict) and env.get("update") is not None)

    def _lane_env(self, job_id: str) -> dict[str, str]:
        return page_store_mod.child_env()  # the store of paid page text (jobs/matter/store.py)

    def _write_job(self, job_id: str) -> Path:
        """``job.yaml``, from the envelope, the staged vision seed and the
        month's allowance read from the broker now."""
        jd = self.job_dir(job_id)
        env = json.loads((jd / "envelope.json").read_text(encoding="utf-8"))
        data_root = jd / "data"
        data_root.mkdir(exist_ok=True)
        doc: dict[str, Any] = {
            "slug": self.customer_slug,
            "matter": env["matter"],
            "units": env["units"],
            "incident": env["incident"],
            "data_root": str(data_root),
            # The install-level tree the entrypoint pre-seeds from the vault
            # every boot (`controls/controls.json` + PDFs, `controls/icd/`,
            # both staged there by provision-customer.sh): the run dir itself,
            # shared by every job, root-owned and read-only to the child. The
            # child never fetches into it (only a laptop install, where
            # install_root == data_root, fetches its own ICD tables). A fresh
            # per-job data_root can never carry the classifier's falsifier, so
            # a job that resolved controls there refused forever (2026-09-04).
            "install_root": str(self.run_dir),
        }
        for key in (
            "injuries",
            "cap_usd",
            "allowance_remaining_documents",
            "allowance_remaining_pages",
            "selection",
            "requested_by",
            "request_ref",
            "update",
        ):
            if env.get(key) is not None:
                doc[key] = env[key]
        doc.update(vision_seed.staged_fields(env, self.run_dir, job_id))
        # The month's state is read FRESH here, on every run and every resume,
        # never taken from the envelope: the envelope's copy was true when the
        # job was submitted and is stale the moment any other job records cents.
        # This job's own row is excluded so a resume is not metered against the
        # spend it already recorded.
        job_mod.stamp_period(doc, self.broker.allowance(exclude_job_id=job_id))
        import yaml

        (jd / "job.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        self._chown_child(jd)
        return jd

    @staticmethod
    def _ledger_fields(outcomes: list[dict[str, Any]]) -> tuple[str, dict[str, Any], str | None]:
        """(state, ledger fields, stage) from a non-empty verdict list."""
        rank = {"delivered": 0, "dry_run": 0, "held": 1, "refused": 2, "failed": 3}
        worst = max(outcomes, key=lambda o: rank.get(str(o.get("outcome")), 3))
        outcome = str(worst.get("outcome"))
        # Each unit reports JOB-level figures, never its own: `pages` is the
        # matter's one extracted file and `dollars` the job's whole ledger
        # set (driver.Budget reads every unit's). So the job's figure is
        # the largest any unit saw, taken once. Summed, a joint job was
        # debited its shared file once per client (2026-10-28).
        cents = int(round(max(float(o.get("dollars") or 0) for o in outcomes) * 100))
        pages = int(max(int(o.get("pages") or 0) for o in outcomes))
        documents = int(max(int(o.get("documents") or 0) for o in outcomes))
        reason = worst.get("reason")
        stage = str(worst.get("stage") or "") or None
        if outcome == "refused":
            state, reason = "held", f"refused: {reason}"
        elif outcome == "delivered":
            state = "delivered"
        elif outcome == "held":
            state = "held"
        else:
            state = "failed"
        fields: dict[str, Any] = {"documents": documents, "pages": pages, "cents": cents}
        if reason:
            fields["reason"] = str(reason)[:500]
        if state == "delivered":
            # The delivery's own fields, including the covered/uncovered
            # document id sets a later UPDATE reads its delta from.
            fields.update(delivery_fields(worst, outcomes))
        return state, fields, stage

    def _report(self, job_id: str, code: int, out: str) -> str:
        outcomes = verdict_mod.read(self.job_dir(job_id), out)
        stage: str | None = None
        fields: dict[str, Any]
        if not outcomes:  # `verdict.read` always returns a list; empty is "no verdict from either source"
            state, fields = "failed", {"reason": verdict_mod.lost(code)[:500]}
        else:
            state, fields, stage = self._ledger_fields(outcomes)
        # What the job spent (``spend.py``): a lost verdict still records its
        # usage, and adds no figure at all when there is none to add.
        reported = fields.get("cents")
        cents = spend.recorded_cents(self.job_dir(job_id), self.LANE, reported if isinstance(reported, int) else None)
        if cents or "cents" in fields:
            fields["cents"] = cents
        # An infrastructure ending (network, model API, a slow index, a runner
        # killed by a signal) is retried after a delay, bounded (requeue.py):
        # before settle, so a retry is not a page.
        if not proof_watch.in_proof_run(self) and requeue.maybe_requeue(self, job_id, outcomes, state, fields, stage):
            return "requeued"
        # Proven delivery: a dry run is never a delivery, a delivered claim
        # without its proof is a failure, and a hold or read-back failure pages.
        paged_before = dict(self._daemon_state(job_id).get("proof_alerts") or {})
        self.proof_recorded = None
        fields.update(page_store_mod.job_fields(self.job_dir(job_id)))  # the job's page-store counts, for its audit row
        state, fields = proof_watch.settle(self, job_id, outcomes, state, fields)
        fields["run"] = run_record.block(
            "end" if state == "delivered" else "stop",
            stage=stage,
            reason=fields.get("reason") if state != "delivered" and outcomes else None,
            code=None if outcomes or state == "delivered" else "no_verdict",
            with_release=state == "delivered",
            stages=self.run_stages(job_id),
            extra=run_record.counts(self.job_dir(job_id)),
        )
        missed = ""
        try:
            self.broker.record(job_id, state, fields)
        except BrokerError as exc:
            missed = str(exc) or type(exc).__name__
            logger.error("could not record %s for %s: %s (recorded on a later tick)", state, job_id, exc)
        finished = self.clock() if state in TERMINAL or state == "held" else None
        self._write_state(job_id, state=state, finished_at=finished, reason=fields.get("reason"))
        if state == "failed" and (self._daemon_state(job_id).get("proof_alerts") or {}) == paged_before:
            # A failure settle did not page (a lost verdict, a crashed stage,
            # retries spent) pages SMD here, once; the firm is told nothing and
            # no wake follows (job_notes, watch.wake_owed, 2026-11-06).
            job_notes.tell(self, job_id, why=str(fields.get("reason") or "the job ended failed"))
        if finished is not None and proof_watch.wake_owed(self, state, fields.get("reason")):
            self._write_state(
                job_id,
                wake={"pending": True, "attempts": 0, "task": self._compose_wake(job_id, state, fields, stage=stage)},
            )
        if missed:
            # The broker did not take the ending (2026-11-17, the lifecycle
            # matrix: the row said running for good). It is kept and recorded on
            # a later tick, with a bound past which SMD is paged; the firm's
            # message above goes exactly as it did before (``unrecorded.py``).
            proof = state == "delivered" and self.proof_recorded is False
            unrecorded.keep(self, job_id, state, fields, proof=proof, why=missed)
        return state

    def poll_requests(self) -> None:
        """Each tick and while idle: record any ending the broker did not take,
        at most once a minute while one is still waiting."""
        super().poll_requests()
        if self.clock() < self.record_retry_at:
            return
        try:
            if not unrecorded.retry(self):
                self.record_retry_at = self.clock() + unrecorded.RETRY_SECONDS
        except Exception:  # a poll hook must not raise (daemon.poll_requests); logged with its trace
            logger.exception("recording a pending ending failed")
            self.record_retry_at = self.clock() + unrecorded.RETRY_SECONDS

    def before_wipe(self, job_id: str) -> None:
        """Retention's last call before a workdir goes (``unrecorded.at_wipe``):
        an ending still not recorded is tried once more, then paged."""
        unrecorded.at_wipe(self, job_id)

    # -- the deliver wake (ss#2616) --------------------------------------------
    def _compose_wake(self, job_id: str, state: str, fields: dict[str, Any], *, stage: str | None = None) -> str:
        """The handoff task text. Only broker/envelope-authored fields ride it
        (requester, ref, matter number, counts, folder id) — never a file name,
        a client name, or a DOB: the deliver turn reads the folder itself, so
        no tenant-authored string reaches the unfenced wake prompt.

        A hold or failure names WHERE it stopped, never why: `stage` is a DAG
        stage name (code-authored vocabulary), whereas the runner's `reason` is
        free text that quotes matter file names and folder names (a decision
        hold lists the files it could not place). The ledger row and the
        console keep the full reason for a person reading their own tenant."""
        env: dict[str, Any] = {}
        try:
            env = json.loads((self.job_dir(job_id) / "envelope.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        matter_no = (env.get("matter") or {}).get("number", "")
        from .update import outcome as update_outcome

        # Code-authored: an update INTO the delivered chronology says so, plus
        # an already-current update's counts per reason.
        upd = update_outcome.wake_block(verdict_mod.read(self.job_dir(job_id), ""), state, env, self.job_dir(job_id))
        lines = [
            f"Run the {SKILL} skill's DELIVER mode for chronology job {job_id}.",
            load_line(SKILL),
            f"Outcome: {state}. Matter number: {matter_no}.",
            update_outcome.totals_line(fields, len(upd) > 1),
            *upd,
        ]
        lines.extend(wake_folder_lines(state, fields))
        lines.extend(allowance_mod.credit_wake_lines(getattr(self, "broker", None), job_id, state, fields))
        if state != "delivered":
            lines.append(f"Held at: {stage or 'runner (no verdict)'}.")
        if state == "held":  # the firm-safe token the skill words; the reason stays SMD's
            lines.append(proof_holds.wake_line(self.LANE, fields.get("reason")))
        requester = env.get("requested_by") or ""
        ref = env.get("request_ref") or ""
        none_note = "(none - a rehearsal submission; report in the memo, create no task, send nothing)"
        lines.append(f"Requester: {requester or none_note}.")
        if ref:
            lines.append(f"Request ref: {ref}.")
        return "\n".join(lines)


def build_lane(d: Daemon) -> MedchronLane:
    """The chronology lane on the seat's shared settings (``daemon.build``)."""
    return MedchronLane(
        run_dir=d.run_dir,
        broker=d.broker,
        runner_cmd=default_runner_cmd(),
        customer_slug=d.customer_slug,
        sticky_db=d.sticky_db,
        cgroup_root=d.cgroup_root,
        memory_max=int(os.environ.get(MEMORY_MAX_ENV) or daemon_mod.DEFAULT_MEMORY_MAX),
        wipe_hours=d.wipe_hours,
        held_wipe_hours=d.held_wipe_hours,
        child_uid=d.child_uid,
        started_at=d.started_at,
        gate_url=d.gate_url,
        wake_secret=d.wake_secret,
    )


def build() -> MedchronLane:
    """The chronology lane as the seat runs it, for the chronology's own CLIs
    (``allowance``, the ``*_decide`` tools, the backfills, deliver-by-SMD)."""
    return build_lane(daemon_mod.build())


def start_lane(d: Daemon, *, stop: Callable[[], bool], poll_seconds: float) -> threading.Thread:
    lane = build_lane(d)
    t = threading.Thread(target=lane.run_forever, args=(stop, poll_seconds), name="medchron-lane", daemon=True)
    t.start()
    logger.info("medchron lane up: queue=%s jobs=%s", lane.queue, lane.jobs)
    return t


__all__ = ["MEMORY_MAX_ENV", "SKILL", "MedchronLane", "build", "build_lane", "default_runner_cmd", "start_lane"]
