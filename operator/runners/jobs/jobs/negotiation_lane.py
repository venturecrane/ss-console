"""The negotiation lane: a root daemon slot of its own, for the negotiation watch.

The drafting lane's loop (it subclasses ``DraftingLane`` for the claim, the
back-off and the cgroup join) with the negotiation swaps:

* its own queue (``SMD_NEGOTIATION_QUEUE_DIR``, written by the broker's
  ``negotiation_job_submit`` as ``<queue>/<job_id>.json``), job dirs
  ``<run_dir>/negotiation-jobs``, tick ``<run_dir>/negotiation-tick``, child pid
  ``<run_dir>/negotiation-child.pid``;
* the broker's NEGOTIATION verbs: ``negotiation_job_status`` (with no job id,
  the root view's ``month_cents``) and ``negotiation_job_record``;
* its child: ``jobs negotiate <job_dir>``, with the lane's state dir on the
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
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import spend, verdict as verdict_mod
from .broker_client import BrokerClient, BrokerError
from .daemon import RUNNER_BIN, Daemon
from .proof import watch as proof_watch
from .skill_load import load_line
from .drafting_lane import DraftingLane
from . import seat_layout

logger = logging.getLogger("jobs.negotiation")

QUEUE_ENV = "SMD_NEGOTIATION_QUEUE_DIR"
STATE_ENV = "JOBS_NEGOTIATION_STATE_DIR"
MEMORY_ENV = "SMD_NEGOTIATION_MEMORY_MAX_BYTES"
DEFAULT_MEMORY = 1024 * 1024 * 1024
SKILL = "negotiation-watch"
#: The demand cgroup's other children (the case file job shares it too).
BUSY_PIDS = ("demand-child.pid", "drafting-child.pid", "casefile-child.pid", "records-child.pid")
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

    def month_cents(self, exclude_job_id: str) -> int:
        """This Pacific month's negotiation spend, the job itself left out
        (``exclude_job_id``): its child adds its own spend, so a resumed job
        is counted once, not twice."""
        v = self.client._request({"action": "negotiation_job_status", "exclude_job_id": exclude_job_id}).get(
            "month_cents"
        )
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise BrokerError("negotiation_job_status carried no month_cents; the job cannot be metered")
        return v


def negotiation_runner_cmd() -> list[str]:
    privs = ["setpriv", "--reuid=jobs", "--regid=jobs", "--init-groups", "--no-new-privs"]
    return [*privs, "nice", "-n", "10", RUNNER_BIN, "negotiate"]


def wake_task(job_id: str, outcome: str, stage: str = "") -> str:
    """Code-authored only: the id, the kind, the trigger, the outcome. Never a
    matter, a name or a figure; the notice row holds those."""
    lines = [
        f"Run the {SKILL} skill's DELIVER mode for negotiation job {job_id}.",
        load_line(SKILL),
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
    queue_dir: Path = field(default_factory=lambda: Path(seat_layout.NEGOTIATION_QUEUE))
    state_dir: str | None = None
    heartbeat_seconds: float = HEARTBEAT_SECONDS
    LANE = "negotiation"
    HEARTBEAT = "negotiation-heartbeat"
    LOGGER = "jobs.negotiation"
    DRAFTING_STEPS = False
    ONE_VERDICT = ("delivered", "failed")
    BUSY_RETRY_SECONDS = BUSY_RETRY_SECONDS
    BUSY_PIDS = BUSY_PIDS

    def _write_job(self, job_id: str) -> Path:
        jd = self.job_dir(job_id)
        env = json.loads((jd / "envelope.json").read_text(encoding="utf-8"))
        doc = {
            **env,
            "job_id": job_id,
            "kind": "negotiation",
            "slug": self.customer_slug,
            "month_cents_used": self.broker.month_cents(job_id),
        }
        (jd / "data").mkdir(exist_ok=True)
        (jd / "job.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
        self._chown_child(jd)
        return jd

    def _inputs_error(self) -> Exception | None:
        return None  # the negotiation lane loads no firm inputs before it runs

    def _lane_env(self, job_id: str) -> dict[str, str]:
        return {STATE_ENV: str(self.state_dir)} if self.state_dir else {}

    def _report(self, job_id: str, code: int, out: str) -> str:
        v = verdict_mod.one(self.job_dir(job_id), out, self.ONE_VERDICT)
        if v is None:
            v = {
                "verdict": "failed",
                "stage": None,
                "reason": f"no_verdict: the runner exited {code} without a verdict",
            }
        state = "delivered" if v["verdict"] == "delivered" else "failed"
        cents = spend.recorded_cents(self.job_dir(job_id), self.LANE, int(v.get("cents") or 0))
        fields: dict[str, Any] = {"stage": v.get("stage"), "cents": cents}
        for k in ("matters_total", "matters_seeded", "docs_read", "docs_failed"):
            fields[k] = int(v.get(k) or 0)
        fields["notices"] = [n for n in v.get("notices") or [] if isinstance(n, dict)] if state == "delivered" else []
        if v.get("reason"):
            fields["reason"] = str(v["reason"])[:500]
        state, fields = proof_watch.settle(self, job_id, [v], state, fields)
        fields.setdefault("notices", [])
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
