"""The case file lane: the root daemon's slot for Case File Manager jobs (a
caseload's ``full`` read, and the ``apply`` of lines a person approved).

The litigation lane's shape (it subclasses ``DraftingLane`` for the loop, the
held-is-final rule, the back-off and the cgroup join):

* its own queue (``SMD_CASEFILE_QUEUE_DIR``, written by the broker's
  ``casefile_job_submit`` / ``casefile_apply_submit`` as
  ``<queue>/<job_id>.json``), job dirs ``<run_dir>/casefile-jobs``, tick
  ``<run_dir>/casefile-tick``, child pid ``<run_dir>/casefile-child.pid``;
* the broker's CASEFILE verbs: ``casefile_job_status`` (a job's row; with no
  job id, the root view's ``month_cents``) and ``casefile_job_record``;
* its child: ``jobs casefile <job_dir>``, with the case file inputs, the
  lane's state dir and the SHARED litigation text cache on the allow-listed
  env, and ``job.json`` stamped fresh before every run with the slug, the
  month's case file spend and the two caps from ``casefile-firm.yaml``;
* its wake: the ``case-file-manager`` skill's DELIVER mode, counts only.

SEAT CONTENTION. The child joins the DEMAND lane's memory cgroup. A job does
not START while a demand, drafting, chronology or litigation child is live,
and once running it YIELDS between matters to any client-requested demand,
drafting or chronology child (the pid files ride ``JOBS_CASEFILE_YIELD_PIDS``
to the child, ``casefile/matter.py``): a client waiting never waits on more
than the matters already in flight. A heartbeat thread keeps the lane's tick
fresh while the long child runs.
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
from .firm_inputs import pin_env
from .daemon import RUNNER_BIN, Daemon
from .drafting_lane import DraftingLane
from .proof import holds as proof_holds, watch as proof_watch
from .skill_load import load_line
from . import seat_layout

logger = logging.getLogger("jobs.casefile")

QUEUE_ENV = "SMD_CASEFILE_QUEUE_DIR"
INPUTS_ENV = "JOBS_CASEFILE_INPUTS_DIR"
STATE_ENV = "JOBS_CASEFILE_STATE_DIR"
#: The text cache is the litigation lane's: one paid read serves both lanes.
TEXTCACHE_ENV = "JOBS_LITIGATION_TEXTCACHE_DIR"
YIELD_ENV = "JOBS_CASEFILE_YIELD_PIDS"
MEMORY_ENV = "SMD_CASEFILE_MEMORY_MAX_BYTES"
DEFAULT_MEMORY = 1024 * 1024 * 1024
SKILL = "case-file-manager"
#: A job does not START while any of these is live.
BUSY_PIDS = ("demand-child.pid", "drafting-child.pid", "child.pid", "litigation-child.pid", "records-child.pid")
#: A running job waits between matters while any of these is live: a client waiting.
YIELD_PIDS = ("demand-child.pid", "drafting-child.pid", "child.pid")
BUSY_RETRY_SECONDS = 30.0
HEARTBEAT_SECONDS = 30.0
#: An apply line made and read back, or already so on the record (``casefile/apply.py``).
DONE_OUTCOMES = ("completed", "written", "invoice_added", "already_complete", "already_present")
#: The litigation inputs dir: the text cache key is that lane's (``casefile/run.cache_method_version``).
LIT_INPUTS_ENV = "JOBS_LITIGATION_INPUTS"
COUNT_FIELDS = ("cents", "matters_total", "matters_read", "matters_current", "matters_held", "lines_proposed")
_STAGE = re.compile(r"[a-z0-9_]{1,24}")
#: A Smokeball matter status as the billing count names it (Open, Pending).
_STATUS = re.compile(r"[A-Za-z][A-Za-z ]{0,23}")


class CasefileBroker:
    def __init__(self, client: BrokerClient) -> None:
        self.client = client

    def status(self, job_id: str) -> dict[str, Any] | None:
        return self.client._request({"action": "casefile_job_status", "job_id": job_id}).get("job")

    def record(self, job_id: str, state: str, fields: dict[str, Any]) -> dict[str, Any]:
        return self.client._request(
            {"action": "casefile_job_record", "job_id": job_id, "state": state, "fields": fields}
        )

    def owned_lines(self, job_id: str) -> set[int]:
        """The line numbers an apply job holds now (root's status view). A
        missing list raises: the lane defers rather than write unowned lines."""
        job = self.client._request({"action": "casefile_job_status", "job_id": job_id}).get("job")
        v = job.get("owned_lines") if isinstance(job, dict) else None
        if not isinstance(v, list):
            raise BrokerError("casefile_job_status carried no owned_lines; the apply cannot be scoped")
        return {int(n) for n in v if isinstance(n, int)}

    def month_cents(self, exclude_job_id: str) -> int:
        """This Pacific month's case file spend. A missing value raises: the
        lane defers rather than run unmetered.
        The job itself is left out (``exclude_job_id``): its child adds its
        own spend, so a resumed job is counted once, not twice."""
        v = self.client._request({"action": "casefile_job_status", "exclude_job_id": exclude_job_id}).get("month_cents")
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise BrokerError("casefile_job_status carried no month_cents; the job cannot be metered")
        return v


def casefile_runner_cmd() -> list[str]:
    privs = ["setpriv", "--reuid=jobs", "--regid=jobs", "--init-groups", "--no-new-privs"]
    return [*privs, "nice", "-n", "10", RUNNER_BIN, "casefile"]


@dataclass
class CasefileLane(DraftingLane):
    queue_dir: Path = field(default_factory=lambda: Path(seat_layout.CASEFILE_QUEUE))
    inputs_dir: str | None = None
    state_dir: str | None = None
    textcache_dir: str | None = None
    heartbeat_seconds: float = HEARTBEAT_SECONDS
    LANE = "casefile"
    HEARTBEAT = "casefile-heartbeat"
    LOGGER = "jobs.casefile"
    DRAFTING_STEPS = False
    ONE_VERDICT = ("delivered", "held", "failed")
    INPUTS_DEFERRED = "case file inputs not usable yet, deferring %s once: %s"
    BUSY_RETRY_SECONDS = BUSY_RETRY_SECONDS
    BUSY_PIDS = BUSY_PIDS

    def _write_job(self, job_id: str) -> Path:
        from .casefile import firm as cf_firm

        jd = self.job_dir(job_id)
        env = json.loads((jd / "envelope.json").read_text(encoding="utf-8"))
        firm = cf_firm.load(self.inputs_dir)
        doc = {
            **env,
            "job_id": job_id,
            "kind": "casefile",
            "slug": self.customer_slug,
            "month_cents_used": self.broker.month_cents(job_id),
            "per_job_cap_usd": float(firm.data["per_job_cap_usd"]),
            "monthly_budget_usd": float(firm.data["monthly_budget_usd"]),
        }
        if doc.get("mode") == "apply" and isinstance(doc.get("apply"), dict):
            # Only the lines the broker still holds for THIS job: a resumed apply
            # never races a newer apply that took a line this one released.
            owned = self.broker.owned_lines(job_id)
            doc["apply"] = {**doc["apply"], "lines": [ln for ln in doc["apply"]["lines"] if ln.get("n") in owned]}
        (jd / "data").mkdir(exist_ok=True)
        (jd / "job.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
        self._chown_child(jd)
        return jd

    def _inputs_error(self) -> Exception | None:
        from .casefile import firm as cf_firm

        try:
            cf_firm.load(self.inputs_dir)
        except cf_firm.CasefileConfigError as exc:
            return exc
        return None

    def _lane_env(self, job_id: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, value in (
            (INPUTS_ENV, pin_env(self.inputs_dir)),
            (STATE_ENV, self.state_dir),
            (TEXTCACHE_ENV, self.textcache_dir),
        ):
            if value:
                out[key] = str(value)
        out[YIELD_ENV] = ",".join(str(self.run_dir / p) for p in YIELD_PIDS)
        if os.environ.get(LIT_INPUTS_ENV):
            out[LIT_INPUTS_ENV] = str(pin_env(os.environ[LIT_INPUTS_ENV]))
        return out

    def _fail_unconfigured(self, job_id: str, exc: Exception) -> str:
        reason = "config_missing: the case file inputs are not usable on this seat: " + str(exc).split(";")[0][:300]
        try:
            self.broker.record(job_id, "running", {})
            cents = spend.recorded_cents(self.job_dir(job_id), self.LANE, 0)
            self.broker.record(job_id, "failed", {"cents": cents, "reason": reason, "stage": "config"})
        except BrokerError as err:
            logger.error("could not record config_missing for %s: %s", job_id, err)
            return "deferred"
        self._write_state(job_id, state="failed", finished_at=self.clock(), reason=reason)
        task = self._compose_wake(job_id, "failed", {"stage": "config"})
        self._write_state(job_id, wake={"pending": True, "attempts": 0, "task": task})
        return "failed"

    @staticmethod
    def report_fields(v: dict[str, Any]) -> dict[str, Any]:
        """The verdict, reduced to what the broker records (ids, counts, hashes)."""
        fields: dict[str, Any] = {"stage": v.get("stage"), "folder_id": v.get("folder_id")}
        for k in COUNT_FIELDS:
            fields[k] = int(v.get(k) or 0)
        fields["files"] = [f for f in v.get("files") or [] if isinstance(f, dict)]
        for k in ("lines", "line_outcomes", "writes", "intents"):
            if isinstance(v.get(k), list):
                fields[k] = [x for x in v[k] if isinstance(x, dict)]
        if isinstance(v.get("metrics"), dict):
            fields["metrics"] = v["metrics"]
        if v.get("reason"):
            fields["reason"] = str(v["reason"])[:500]
        return fields

    def _report(self, job_id: str, code: int, out: str) -> str:
        v = verdict_mod.one(self.job_dir(job_id), out, self.ONE_VERDICT)
        if v is None:
            v = {
                "verdict": "failed",
                "stage": None,
                "reason": f"no_verdict: the runner exited {code} without a verdict",
            }
        state = str(v["verdict"])
        fields = self.report_fields(v)
        fields["cents"] = spend.recorded_cents(self.job_dir(job_id), self.LANE, fields["cents"])
        state, fields = proof_watch.settle(self, job_id, [v], state, fields)
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
        trig, mode = str(env.get("trigger") or ""), str(env.get("mode") or "full")
        outcomes = [o for o in fields.get("line_outcomes") or [] if isinstance(o, dict)]
        lines = [
            f"Run the {SKILL} skill's DELIVER mode for casefile job {job_id}.",
            load_line(SKILL),
            "Kind: casefile.",
            f"Mode: {mode if mode in ('full', 'recheck', 'apply') else 'unknown'}.",
            f"Trigger: {trig if trig in ('request', 'scheduled') else 'unknown'}.",
            f"Outcome: {state}.",
        ]
        if mode == "apply":
            done = sum(1 for o in outcomes if o.get("outcome") in DONE_OUTCOMES)
            lines.append(f"Changes applied: {done} of {len(outcomes)}; numbers applied: "
                         + (", ".join(str(int(o["n"])) for o in outcomes if o.get("outcome") in DONE_OUTCOMES) or "none")
                         + "; numbers not applied: "
                         + (", ".join(str(int(o["n"])) for o in outcomes if o.get("outcome") not in DONE_OUTCOMES) or "none")
                         + ".")  # fmt: skip
        else:
            lines.append(
                f"Matters: {int(fields.get('matters_total') or 0)}; read this run: {int(fields.get('matters_read') or 0)}; "
                f"already current: {int(fields.get('matters_current') or 0)}; not reviewed: {int(fields.get('matters_held') or 0)}; "
                f"File Status filed: {sum(1 for f in fields.get('files') or [] if f.get('role') == 'file_status')}; "
                f"changes proposed: {int(fields.get('lines_proposed') or 0)}."
            )
            lines.append(f"Folder id: {fields.get('folder_id') or 'none'}.")
            count = (fields.get("metrics") or {}).get("billing_count")
            if isinstance(count, dict) and count:
                # The month's billing snapshot (casefile/billing.py): counts by
                # Smokeball status only, code-composed, never a name.
                lines.append(
                    "Count on the 1st: "
                    + ", ".join(f"{int(n)} {s if _STATUS.fullmatch(str(s)) else 'other'}" for s, n in count.items())
                    + "."
                )
        lines.append(f"Requested by: {env.get('requester') or ''}.")
        if state != "delivered":
            where = str(fields.get("stage") or stage or "")
            lines.append(f"Reason: stopped at {where if _STAGE.fullmatch(where) else 'the runner (no verdict)'}.")
        if state == "held":
            lines.append(proof_holds.wake_line("casefile", fields.get("reason")))
        return "\n".join(lines)


def build_lane(d: Daemon) -> CasefileLane | None:
    raw = os.environ.get(QUEUE_ENV, "").strip()
    if not raw:
        logger.info("no %s; the case file lane is not started", QUEUE_ENV)
        return None
    from .casefile.state import state_dir
    from .litigation.textcache import cache_dir

    return CasefileLane(
        run_dir=d.run_dir,
        broker=CasefileBroker(d.broker.client if hasattr(d.broker, "client") else d.broker),
        runner_cmd=casefile_runner_cmd(),
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
    t = threading.Thread(target=lane.run_forever, args=(stop, poll_seconds), name="casefile-lane", daemon=True)
    t.start()
    logger.info("case file lane up: queue=%s jobs=%s", lane.queue, lane.jobs)
    return t


__all__ = ["CasefileBroker", "CasefileLane", "build_lane", "start_lane"]
