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
* its child: ``jobs demand <job_dir>``, with the demand firm inputs on the
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
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import firm_wording, spend, verdict as verdict_mod
from .broker_client import BrokerClient, BrokerError
from .firm_inputs import pin_env
from .library_edit import handoff as library_edit_handoff
from .proof import watch as proof_watch
from .skill_load import load_line
from .daemon import RUNNER_BIN, TERMINAL, Daemon
from . import seat_layout

logger = logging.getLogger("jobs.demand")

QUEUE_ENV = "SMD_DEMAND_QUEUE_DIR"
INPUTS_ENV = "JOBS_DEMAND_INPUTS"
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
        "--reuid=jobs",
        "--regid=jobs",
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
    queue_dir: Path = field(default_factory=lambda: Path(seat_layout.DEMAND_QUEUE))
    inputs_dir: str | None = None
    LANE = "demand"
    HEARTBEAT = "demand-heartbeat"
    LOGGER = "jobs.demand"
    BACKS_OFF = True
    JOB_WORD = "demand"
    HELD_RESUME_REFUSED = "resume of held demand %s refused: a held demand is final; ask again"
    #: The demand child also reads the records vendor's directory (the gap
    #: audit's missing providers): its three per-seat values, nothing more.
    ENV_PASS = VENDOR_ENV_PASS

    @property
    def queue(self) -> Path:
        return self.queue_dir

    # -- one job (the host runs it: ``Daemon.run_job``) ---------------------------
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
        self._seed_from_superseded(jd, str(env.get("supersedes") or ""))
        (jd / "job.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
        self._chown_child(jd)
        return jd

    def _seed_from_superseded(self, jd: Path, old_id: str) -> None:
        """A re-run (demand_job_rerun) starts where the superseded job stopped:
        its paid, input-keyed stages are copied in (demand/seed.py). The old id
        comes from the broker's queued envelope; anything but a job id is
        ignored, and a superseded dir already wiped means a fresh start."""
        from .demand import seed

        if not re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", old_id):
            return
        old = self.job_dir(old_id)
        if not (old / "data").is_dir():
            logger.info("demand re-run %s: superseded job %s has no data left; starting fresh", jd.name, old_id)
            return
        carried = seed.seed(jd, old)
        logger.info("demand re-run %s seeded from %s: %s", jd.name, old_id, ", ".join(carried) or "nothing")

    def _before_start(self, job_id: str) -> str | None:
        from .demand import firm as demand_firm

        try:
            demand_firm.load(self.inputs_dir)
        except demand_firm.DemandConfigError as exc:
            logger.warning("demand inputs not usable yet, deferring %s: %s", job_id, exc)
            return "deferred"
        return None

    def _child_env(self, job_id: str, jd: Path) -> dict[str, str]:
        out = super()._child_env(job_id, jd)
        if self.inputs_dir:
            out[INPUTS_ENV] = str(pin_env(self.inputs_dir))
        firm_wording.alert_unusable(self, job_id)
        return out

    def _after_ending(self, job_id: str) -> None:
        library_edit_handoff.hand_over(self, job_id)  # a firm's own save of its library file (piece 4)

    def _report(self, job_id: str, code: int, out: str) -> str:
        outcomes = verdict_mod.read(self.job_dir(job_id), out)
        v = outcomes[0] if outcomes and isinstance(outcomes[0], dict) else None
        if v is None:
            state, v = "failed", {"reason": f"the runner exited {code} without a verdict", "stage": None}
        else:
            state = {"delivered": "delivered", "held": "held"}.get(str(v.get("outcome")), "failed")
        reported = int(round(float(v.get("dollars") or 0) * 100))
        fields: dict[str, Any] = {"cents": spend.recorded_cents(self.job_dir(job_id), self.LANE, reported)}
        if v.get("reason"):
            fields["reason"] = str(v["reason"])[:500]
        if state == "delivered":
            fields["folder_id"] = str(v.get("folder_id") or "")
            fields["delivery"] = {
                "files": list(v.get("files") or []),
                "coverage_report": bool(v.get("coverage_report")),
                "cents": fields["cents"],
            }
            edit = library_edit_handoff.delivery_note(self.job_dir(job_id) / "data")
            if edit is not None:
                fields["delivery"]["library_edit"] = edit
        state, fields = proof_watch.settle(self, job_id, outcomes or [v], state, fields)
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
            load_line(SKILL),
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
