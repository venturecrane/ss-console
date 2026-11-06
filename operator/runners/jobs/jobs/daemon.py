"""The on-Machine jobs runner daemon (routine 11, ss#2614): `python -m jobs.daemon`.

Runs as root under the entrypoint's respawn loop, forked before the exec-drop.
It starts every lane in ``lanes.LANES`` on its own thread and then only waits
on the main thread (``supervise``), where the stop signal lands. ``Daemon`` is
what every lane shares: it watches the lane's queue the broker writes
(``<queue>/<job_id>.json``), runs one job at a time as the dedicated `jobs`
uid under the lane's memory cap, and reports every transition back through
the broker (each lane's ``*_job_record`` verb, root only), which writes the
ledger row and the audit row under its own uid. What a job IS (its input file,
its child, its verdict, its wake) is the lane's: ``run_job``, ``_report`` and
``_compose_wake`` are hooks each lane defines. This module imports no lane.

What every lane holds itself to:

* One job at a time per lane, oldest first (job ids are ULIDs; the file name sorts).
* An envelope with no ledger row is quarantined, never run.
* A seat at HARD_STOP runs nothing: the job is recorded `held` once, stays
  queued, and is re-checked every poll without a new record.
* A crash mid-job resumes at the same job: the driver's state file skips the
  stages that finished; the daemon re-runs `jobs run` on restart.
* The child gets an allow-listed env: the Anthropic key (the seat's workspace,
  ADR 0062), the Smokeball credentials, the firm config path, and the job
  marker JOBS_DAEMON_JOB_ID (the Smokeball connector's audit-record
  exemption keys on it). Nothing else.
* Workdirs are wiped 72 h after a terminal state, and after a longer window
  when a job stopped and waited (`retention.py`) -- never before either.
* Liveness is a tick file and a heartbeat json (`memory_cap` says whether the
  cgroup controller was present; boot smoke reads it). No Sentry here or in
  the child: exception locals carry the envelope.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .broker_client import BrokerClient, BrokerError
from .seat_probes import CGROUP_ROOT, memory_cap_mode, sticky_level

from . import child as child_mod, lane_proof, proof_run, quiesce, requeue, rewake, smd_stop, wake_grace
from . import resume as resume_mod, retention
from .proof import watch as proof_watch

logger = logging.getLogger("jobs.daemon")

RUN_DIR_ENV = "SMD_JOBS_RUN_DIR"
DEFAULT_RUN_DIR = "/run/smd-jobs"
SOCKET_ENV = "SMD_WORKSPACE_BROKER_SOCKET"
STICKY_DB_ENV = "SMD_STICKY_STOP_DB_PATH"
DEFAULT_STICKY_DB = "/opt/data/smd/sticky_stop.db"
POLL_ENV = "SMD_JOBS_POLL_SECONDS"
DEFAULT_MEMORY_MAX = 2560 * 1024 * 1024
WIPE_HOURS_ENV = "SMD_JOBS_WIPE_HOURS"
DEFAULT_WIPE_HOURS = 72
RUNNER_BIN = "/opt/jobs/.venv/bin/jobs"
GATE_URL_ENV = "SMD_JOBS_GATE_URL"
DEFAULT_GATE_URL = "http://127.0.0.1:8643"
WAKE_SECRET_ENV = "WEBHOOK_SECRET_MCP"
WAKE_MAX_ATTEMPTS = 5
#: The lanes whose DELIVER wake first asks the broker whether the job's thread
#: already holds a reply sent after it finished (2026-11-06): the drafting
#: lane, whose completion reply the seat sends in code whatever the turn does.
THREAD_CHECKED_LANES = frozenset({"drafting"})
#: ``main``'s exit when the Machine's stop signal ended the loop (128 + SIGINT,
#: the shell's own convention). ``operator/templates/entrypoint.sh`` does not
#: respawn on it; every other exit is a crash and is respawned.
STOPPED_EXIT = 130
CHILD_UID_NAME = "jobs"
CHILD_ENV_PASS = (
    "JOBS_DATA_DIR",
    "ANTHROPIC_API_KEY",
    "SMOKEBALL_REGION",
    "SMOKEBALL_ENVIRONMENT",
    "SMOKEBALL_CLIENT_ID",
    "SMOKEBALL_CLIENT_SECRET",
    "SMOKEBALL_API_KEY",
    "SMOKEBALL_AUTH_MODE",
    "SMOKEBALL_ACCOUNT_ID",
    "SMOKEBALL_REFRESH_TOKEN_FILE",
    "MEDCHRON_FIRM_CONFIG",
    "JOBS_PRICING_JSON",
    "CUSTOMER_SLUG",
)
#: ``stopped_by_smd`` is SMD's job stop (``smd_stop.py``): final, so a stopped
#: job is never resumed, requeued or claimed again by any lane.
TERMINAL = frozenset({"delivered", "failed", "stopped_by_smd"})


@dataclass
class Daemon:
    run_dir: Path
    broker: Any
    runner_cmd: list[str]
    customer_slug: str
    sticky_db: str = DEFAULT_STICKY_DB
    cgroup_root: Path = CGROUP_ROOT
    memory_max: int = DEFAULT_MEMORY_MAX
    wipe_hours: float = DEFAULT_WIPE_HOURS
    held_wipe_hours: float = retention.DEFAULT_HELD_WIPE_HOURS
    child_uid: str | None = None
    clock: Callable[[], float] = time.time
    child_env: dict[str, str] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    #: Wake id -> when THIS process first found the gateway unreachable for it
    #: (``wake_grace``). Never written to the volume.
    _unreachable_since: dict[str, float] = field(default_factory=dict)
    jobs_run: int = 0
    wakes_failed: int = 0
    #: The loop's stop flag, bound by ``run_forever``; a lane reads it when its
    #: child exits to tell a Machine stop from a failure.
    stopping: Callable[[], bool] = lambda: False
    #: How often a running job's beat thread rewrites the heartbeat
    #: (``child.run_child``), and how long a stop waits for a child to
    #: checkpoint before terminating it.
    heartbeat_seconds: float = 30.0
    drain_seconds: float = field(default_factory=child_mod.drain_seconds)
    #: The quiesce lines this lane already logged (``quiesce.py``: once each).
    _quiesce_logged: set[str] = field(default_factory=set)
    gate_url: str = field(default_factory=lambda: os.environ.get(GATE_URL_ENV) or DEFAULT_GATE_URL)
    wake_secret: str = field(default_factory=lambda: os.environ.get(WAKE_SECRET_ENV, ""))
    #: What a lane names itself by: the wake's handoff id prefix, the memory
    #: cgroup, the heartbeat file and (unless ``TICK`` says otherwise) the tick
    #: file ``<LANE>-tick``. Every lane sets them; the base is no lane, only the
    #: seat's shared settings each lane is built from (``build``). The drafting
    #: lane also joins the demand lane's memory cgroup.
    LANE = ""
    HEARTBEAT = ""
    #: The tick file's name when it is not ``<LANE>-tick`` (the chronology's is
    #: ``tick``, from when it was this class).
    TICK = ""

    # -- paths -------------------------------------------------------------
    @property
    def queue(self) -> Path:
        return self.run_dir / f"{self.LANE}-queue"

    @property
    def jobs(self) -> Path:
        return self.run_dir / f"{self.LANE}-jobs"

    def job_dir(self, job_id: str) -> Path:
        return self.jobs / job_id

    # -- liveness ------------------------------------------------------------
    def heartbeat(self, *, running: str | None) -> None:
        cap = memory_cap_mode(self.cgroup_root)
        payload = {
            "pid": os.getpid(),
            "started_at": self.started_at,
            "last_poll_at": self.clock(),
            "jobs_run": self.jobs_run,
            "running": running,
            "memory_cap": cap,
            "queued": len(self._queued()),
            "wakes_pending": len(self._wakes_pending()),
            "wakes_failed": self.wakes_failed,
            "quiesced": quiesce.active(self),
        }
        tmp = self.run_dir / f".{self.HEARTBEAT}.tmp"
        try:
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.chmod(tmp, 0o644)
            tmp.replace(self.run_dir / f"{self.HEARTBEAT}.json")
            (self.run_dir / (self.TICK or f"{self.LANE}-tick")).touch()
        except OSError as exc:  # the daemon IS the thing the heartbeat reports on
            logger.warning("heartbeat write failed: %s", exc)

    # -- queue ---------------------------------------------------------------
    def _queued(self) -> list[Path]:
        if not self.queue.is_dir():
            return []
        return sorted(p for p in self.queue.glob("*.json") if not p.name.startswith("."))

    def _daemon_state(self, job_id: str) -> dict[str, Any]:
        p = self.job_dir(job_id) / "daemon.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}

    def _write_state(self, job_id: str, **fields: Any) -> dict[str, Any]:
        st = self._daemon_state(job_id)
        st.update(fields)
        p = self.job_dir(job_id) / "daemon.json"
        p.write_text(json.dumps(st, indent=1), encoding="utf-8")
        return st

    def _beat(self, job_id: str, stop: Any) -> None:
        while not stop.wait(self.heartbeat_seconds):
            self.heartbeat(running=job_id)

    def _in_progress(self) -> list[str]:
        # A quiesced lane resumes nothing either: a resume starts a child.
        if not self.jobs.is_dir() or quiesce.active(self):
            return []
        out = []
        for d in sorted(self.jobs.iterdir()):
            st = self._daemon_state(d.name)
            if not (d.is_dir() and st.get("state") not in TERMINAL and st.get("claimed")):
                continue
            # SMD stopped it: ``smd_stop.take_unclaimed`` records it; it never
            # starts again (a lane's run_job records ``running`` before its child).
            if smd_stop.read(d) is not None:
                continue
            # A runner-held job (a refusal, an unexplained file, an unmatched
            # folder) is PARKED: it needs a person, and re-running it changes
            # nothing but the audit log (live-caught 2026-08-31: a cap-refused
            # job re-ran every tick, one RUNNING/HELD pair per cycle). Only a
            # seat-pause hold resumes by itself, via the sticky-stop path.
            if st.get("state") == "held" and not st.get("held_paused"):
                continue
            out.append(d.name)
        return out

    def claim_next(self) -> str | None:
        """Move the oldest envelope into its job dir. A file the broker has no
        row for is quarantined. An idle lane runs a requested proof first; a
        job whose kind is not proven on this seat waits (``lane_proof.py``)."""
        smd_stop.take_unclaimed(self)
        if quiesce.active(self) or proof_run.run_pending(self):
            return None
        for env_path in self._queued():
            job_id = env_path.stem
            try:
                row = self.broker.status(job_id)
            except BrokerError as exc:
                logger.warning("broker unreachable while claiming %s: %s", job_id, exc)
                return None
            if row is None:
                q = self.queue / "quarantine"
                q.mkdir(exist_ok=True, mode=0o700)
                env_path.replace(q / env_path.name)
                retention.mark_set_aside(q / env_path.name)
                logger.error("quarantined %s: the broker has no ledger row for it", job_id)
                continue
            # Before the lane-proof gate, which may release a hold: an ended
            # job's envelope never runs, whenever its lane is proven.
            why = smd_stop.stale_envelope(self, job_id, row)
            if why is not None:
                smd_stop.set_aside(self, env_path, job_id, why)
                continue
            if not lane_proof.admits(self, job_id, env_path, row):
                continue
            jd = self.job_dir(job_id)
            jd.mkdir(parents=True, exist_ok=True, mode=0o700)
            env_path.replace(jd / "envelope.json")
            self._write_state(job_id, claimed=True, state="submitted", claimed_at=self.clock(), attempts=0)
            return job_id
        return None

    # -- one job: the lane's own (each lane defines these) -------------------
    def run_job(self, job_id: str) -> str:
        """HOOK. Run one claimed job to a ledger state: write the job's input
        file, record running, spawn the child (``child.run_child``) and report.
        Returns the state, or ``deferred`` / ``interrupted`` / ``requeued``."""
        raise NotImplementedError(f"the {self.LANE or 'base'} lane defines no job")

    def _report(self, job_id: str, code: int, out: str) -> str:
        """HOOK. Map the child's verdict onto the ledger and the wake."""
        raise NotImplementedError(f"the {self.LANE or 'base'} lane defines no verdict mapping")

    def _compose_wake(self, job_id: str, state: str, fields: dict[str, Any], *, stage: str | None = None) -> str:
        """HOOK. The DELIVER turn's task text for a finished job."""
        raise NotImplementedError(f"the {self.LANE or 'base'} lane defines no wake")

    def _chown_child(self, path: Path) -> None:
        if not self.child_uid:
            return
        try:
            import pwd

            pw = pwd.getpwnam(self.child_uid)
        except KeyError:
            logger.error("child uid %s does not exist; the child would run as root, refusing", self.child_uid)
            raise
        for p in [path, *path.rglob("*")]:
            os.chown(p, pw.pw_uid, pw.pw_gid)
        # The child owns its job dir outright; nobody else needs a mode bit
        # (root reads regardless, and the broker never traverses jobs/).
        # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions - owner-only; the rule fires on any chmod, and 0700 is the tightest mode that lets the child write its own workdir.
        os.chmod(path, 0o700)

    def _cgroup_preexec(self) -> Callable[[], None] | None:
        mode = memory_cap_mode(self.cgroup_root)
        if mode == "none":
            return None
        if mode == "cgroup2":
            cg = self.cgroup_root / self.LANE
            limit_file, limit = cg / "memory.max", str(self.memory_max)
        else:  # cgroup1: the hybrid layout Fly guests run
            cg = self.cgroup_root / "memory" / self.LANE
            limit_file, limit = cg / "memory.limit_in_bytes", str(self.memory_max)
        try:
            cg.mkdir(exist_ok=True)
            limit_file.write_text(limit)
        except OSError as exc:
            logger.error("cgroup setup failed (%s); running uncapped", exc)
            return None

        def preexec() -> None:
            (cg / "cgroup.procs").write_text(str(os.getpid()))

        return preexec

    # -- the deliver wake (ss#2616) --------------------------------------------
    def _wakes_pending(self) -> list[str]:
        if not self.jobs.is_dir():
            return []
        return [
            d.name for d in sorted(self.jobs.iterdir()) if (self._daemon_state(d.name).get("wake") or {}).get("pending")
        ]

    def dispatch_wakes(self) -> None:
        """At-most-once with a loud loss: consumed on a 2xx answer AND on a
        timeout after the request was sent (a duplicate deliver turn is worse
        than a lost one). Consuming a wake is NOT counting the reply as sent:
        a consumed wake arms the reply watch (``proof/watch.py``), which pages
        SMD when the requester's reply is not seen in Sent Items in time. A
        paused seat's 503 retries without consuming an attempt, and so does a
        gateway that cannot be reached at all, for
        ``wake_grace.WAKE_UNREACHABLE_GRACE_SECONDS`` of this process; five real failures
        write a same-state ledger note (an audit row) and page."""
        self._dispatch_wakes()
        proof_watch.check_reply_watches(self)
        proof_watch.check_reroutes(self)

    def _thread_already_answered(self, job_id: str, wake: dict[str, Any]) -> bool:
        """True when this lane's DELIVER wake must not start, because the job's
        thread already holds a reply sent after the job finished (an SMD hand
        release counts): the completion would be the requester's second email
        (A&P, 2026-11-06). The wake is closed, not retried, and the daemon log
        says why in one line. Asked once per wake; a broker that could not
        answer lets the wake go (the broker's bind re-checks at the send)."""
        if self.LANE not in THREAD_CHECKED_LANES or wake.get("thread_checked"):
            return False
        broker = getattr(self.broker, "client", self.broker)
        seen = rewake.thread_check(broker)(rewake.reply_kind(self.LANE), job_id)
        if seen.get("checked") and seen.get("answered"):
            wake.update(pending=False, outcome="refused: thread_answered")
            self._write_state(job_id, wake=wake)
            logger.warning("%s job %s: DELIVER wake not sent: %s", self.LANE, job_id, seen["answered"])
            return True
        if seen.get("checked"):
            wake["thread_checked"] = True
            self._write_state(job_id, wake=wake)
        return False

    def _dispatch_wakes(self) -> None:
        import http.client
        from urllib.parse import urlparse

        for job_id in self._wakes_pending():
            wake = dict(self._daemon_state(job_id).get("wake") or {})
            if not self.wake_secret:
                logger.warning("no %s in the daemon env; wake for %s stays pending", WAKE_SECRET_ENV, job_id)
                return
            if self._thread_already_answered(job_id, wake):
                continue
            # Each distinct wake gets its own id and every re-post of one wake the
            # same id (`rewake.handoff_id`): the gateway drops a reused id with the
            # same text and refuses one with different text (admission_conflict).
            # The number is persisted BEFORE the post (`rewake.post_id`).
            body = json.dumps({"handoff_id": rewake.post_id(self, job_id, wake), "task": wake["task"]})
            url = urlparse(self.gate_url)
            sent = False
            outcome = "error"
            try:
                conn = http.client.HTTPConnection(url.hostname or "127.0.0.1", url.port or 8643, timeout=15)
                try:
                    conn.connect()
                    sent = True
                    conn.request(
                        "POST",
                        "/webhooks/handoff",
                        body=body,
                        headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.wake_secret}"},
                    )
                    resp = conn.getresponse()
                    raw = resp.read()
                    if 200 <= resp.status < 300:
                        outcome = "delivered"
                    elif resp.status == 503 and b"paused" in raw:
                        outcome = "paused"
                    else:
                        outcome = f"status {resp.status}"
                finally:
                    conn.close()
            except Exception as exc:  # noqa: BLE001 - each shape maps to a wake decision
                outcome = wake_grace.failed_outcome(exc, sent=sent)
            # Before every other branch, so any answer (delivered, paused, a
            # timeout) ends this wake's grace and a later outage starts afresh.
            if wake_grace.waiting(self, job_id, outcome):
                continue
            if outcome == "delivered" or (sent and outcome.startswith("timeout")):
                wake.update(pending=False, sent_at=self.clock(), outcome=outcome)
                self._write_state(job_id, wake=wake)
                proof_watch.arm_reply_watch(self, job_id)
                continue
            if outcome == "paused":
                logger.info("seat paused; wake for %s stays pending without consuming an attempt", job_id)
                continue
            wake["attempts"] = int(wake.get("attempts", 0)) + 1
            logger.warning(
                "wake for %s failed (%s), attempt %d/%d", job_id, outcome, wake["attempts"], WAKE_MAX_ATTEMPTS
            )
            if wake["attempts"] >= WAKE_MAX_ATTEMPTS:
                wake.update(pending=False, outcome=f"failed: {outcome}")
                self.wakes_failed += 1
                try:
                    state = self._daemon_state(job_id).get("state") or "failed"
                    self.broker.record(job_id, state, {"wake": {"wake_failed": True, "outcome": outcome}})
                except BrokerError as exc:
                    logger.error("could not record the lost wake for %s: %s", job_id, exc)
                proof_watch.wake_lost(self, job_id, outcome)
            self._write_state(job_id, wake=wake)

    # -- the loop ------------------------------------------------------------------
    def _paused(self, job_id: str) -> bool:
        level = sticky_level(self.sticky_db)
        if level != "HARD_STOP":
            return False
        if not self._daemon_state(job_id).get("held_paused"):
            try:
                self.broker.record(job_id, "held", {"reason": "seat paused (sticky stop HARD_STOP)"})
            except BrokerError as exc:
                logger.warning("could not record the pause hold for %s: %s", job_id, exc)
            self._write_state(job_id, state="held", held_paused=True)
        return True

    def wipe_expired(self) -> list[str]:
        """Terminal jobs at `wipe_hours`, parked ones at `held_wipe_hours`
        (`retention.py` carries the policy and why it is two windows)."""
        retention.prune_set_aside(self)
        return retention.wipe_expired(self, TERMINAL)

    def poll_requests(self) -> None:
        """HOOK. What a lane answers while it would otherwise only wait: called
        once a second from the idle sleep and from a running child's wait. It
        must not raise (``request_additions.take`` does not)."""

    def tick(self) -> str | None:
        """One iteration: resume requests, heartbeat, wipe, wakes, then one job."""
        resume_mod.take_requests(self)
        self.wipe_expired()
        self.dispatch_wakes()
        current = requeue.due(self, self._in_progress())
        job_id = current[0] if current else self.claim_next()
        self.heartbeat(running=job_id)
        if job_id is None:
            return None
        if self._paused(job_id):
            return "paused"
        if self._daemon_state(job_id).get("held_paused"):
            self._write_state(job_id, held_paused=False)
        return self.run_job(job_id)

    def run_forever(self, stop: Callable[[], bool], poll_seconds: float) -> None:
        self.stopping = stop
        while not stop():
            try:
                self.tick()
            except Exception:
                logger.exception("tick failed")
            slept = 0.0
            while slept < poll_seconds and not stop():
                time.sleep(min(1.0, poll_seconds - slept))
                slept += 1.0
                smd_stop.take_unclaimed(self)
                self.poll_requests()


def _require(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise SystemExit(f"{name} is required")
    return v


def build() -> Daemon:
    """The seat's shared settings every lane is built from (each lane's
    ``build_lane``). It runs nothing itself: it has no lane, so no queue, no
    child and no beat; ``runner_cmd`` and ``memory_max`` are each lane's own."""
    run_dir = Path(os.environ.get(RUN_DIR_ENV) or DEFAULT_RUN_DIR)
    return Daemon(
        run_dir=run_dir,
        broker=BrokerClient(_require(SOCKET_ENV)),
        runner_cmd=[],
        customer_slug=_require("CUSTOMER_SLUG"),
        sticky_db=os.environ.get(STICKY_DB_ENV) or DEFAULT_STICKY_DB,
        wipe_hours=float(os.environ.get(WIPE_HOURS_ENV) or DEFAULT_WIPE_HOURS),
        held_wipe_hours=float(os.environ.get(retention.HELD_WIPE_HOURS_ENV) or retention.DEFAULT_HELD_WIPE_HOURS),
        child_uid=CHILD_UID_NAME,
    )


#: How often the supervisor wakes to look at the stop flag and the lanes.
SUPERVISE_SECONDS = 0.5


def supervise(threads: list[Any], *, stop: Callable[[], bool]) -> None:
    """The main thread's loop: it runs no lane, it only waits, so the signal
    handlers and ``STOPPED_EXIT`` stay on the main thread. It returns on the
    Machine's stop, or when every lane thread has ended without one (each
    lane's loop catches its ticks' errors, so that is a crash and the
    entrypoint respawns the daemon)."""
    while not stop():
        if not any(t.is_alive() for t in threads):
            logger.error("no lane is running; the jobs daemon exits for the entrypoint to respawn it")
            return
        time.sleep(SUPERVISE_SECONDS)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("SMD_JOBS_LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    stopped: dict[str, Any] = {"flag": False, "at": None}

    def _stop(*_: Any) -> None:
        stopped["flag"] = True
        stopped["at"] = stopped["at"] or time.monotonic()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        d = build()
    except SystemExit as exc:
        logger.error("jobs daemon not started: %s", exc)
        return 2
    if memory_cap_mode(d.cgroup_root) == "none":
        logger.error(
            "no cgroup memory controller at %s; jobs will run UNCAPPED (boot smoke fails on this)", d.cgroup_root
        )
    poll = float(os.environ.get(POLL_ENV) or 5)
    logger.info("jobs daemon up: run_dir=%s poll=%ss", d.run_dir, poll)
    # Every lane on its own thread, started the same way from one table, so no
    # lane waits on another's job (``lanes.LANES``). Imported here, not at the
    # top: the runner core imports no lane (test_runner_core_imports_no_lane).
    from .lanes import start_lanes

    threads = [t for t in start_lanes(d, stop=lambda: stopped["flag"], poll_seconds=poll) or [] if t is not None]
    supervise(threads, stop=lambda: stopped["flag"])
    # Each lane drains its own child under the same deadline, measured from the
    # stop; the process waits for them instead of abandoning them mid-checkpoint
    # (they are daemon threads).
    if stopped["flag"]:
        child_mod.join_lanes(threads, since=stopped["at"], budget=d.drain_seconds)
    # The Machine's stop is FINAL for this process: the entrypoint's loop
    # restarts the daemon on any other exit, and a daemon respawned inside a
    # dying Machine has no stop flag. On 2026-10-15 one did exactly that,
    # re-running claimed work and spending a DELIVER wake's five attempts on a
    # gateway that was already gone (proof:wake_lost, a page for a deploy).
    return STOPPED_EXIT if stopped["flag"] else 0


if __name__ == "__main__":
    sys.exit(main())
