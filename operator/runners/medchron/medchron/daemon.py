"""The on-Machine runner daemon (routine 11, ss#2614): `python -m medchron.daemon`.

Runs as root under the entrypoint's respawn loop, forked before the exec-drop.
Watches the queue the broker writes (`<run_dir>/queue/<job_id>.json`), runs
one job at a time as the dedicated `medchron` uid under a memory cap, and
reports every transition back through the broker (`medchron_job_record`, root
only), which writes the ledger row and the audit row under its own uid.

What it holds itself to:

* One job at a time, oldest first (job ids are ULIDs; the file name sorts).
* An envelope with no ledger row is quarantined, never run.
* A seat at HARD_STOP runs nothing: the job is recorded `held` once, stays
  queued, and is re-checked every poll without a new record.
* A crash mid-job resumes at the same job: the driver's state file skips the
  stages that finished; the daemon re-runs `medchron run` on restart.
* The child gets an allow-listed env: the Anthropic key (the seat's workspace,
  ADR 0062), the Smokeball credentials, the firm config path. Nothing else.
* Workdirs are wiped 72 h after a terminal state, never before.
* Liveness is a tick file and a heartbeat json (`memory_cap` says whether the
  cgroup controller was present; boot smoke reads it). No Sentry here or in
  the child: exception locals carry the envelope.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import config as config_mod

logger = logging.getLogger("medchron.daemon")

RUN_DIR_ENV = "SMD_MEDCHRON_RUN_DIR"
DEFAULT_RUN_DIR = "/run/smd-medchron"
SOCKET_ENV = "SMD_WORKSPACE_BROKER_SOCKET"
STICKY_DB_ENV = "SMD_STICKY_STOP_DB_PATH"
DEFAULT_STICKY_DB = "/opt/data/smd/sticky_stop.db"
POLL_ENV = "SMD_MEDCHRON_POLL_SECONDS"
MEMORY_MAX_ENV = "SMD_MEDCHRON_MEMORY_MAX_BYTES"
DEFAULT_MEMORY_MAX = 2560 * 1024 * 1024
WIPE_HOURS_ENV = "SMD_MEDCHRON_WIPE_HOURS"
DEFAULT_WIPE_HOURS = 72
RUNNER_BIN = "/opt/medchron/.venv/bin/medchron"
GATE_URL_ENV = "SMD_MEDCHRON_GATE_URL"
DEFAULT_GATE_URL = "http://127.0.0.1:8643"
WAKE_SECRET_ENV = "WEBHOOK_SECRET_MCP"
WAKE_MAX_ATTEMPTS = 5
CHILD_UID_NAME = "medchron"
CGROUP_ROOT = Path("/sys/fs/cgroup")
CHILD_ENV_PASS = (
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
    "MEDCHRON_PRICING_JSON",
    "CUSTOMER_SLUG",
)
TERMINAL = frozenset({"delivered", "failed"})


class BrokerError(RuntimeError):
    pass


class BrokerClient:
    """The two verbs the daemon speaks: status (does the row exist) and record."""

    def __init__(self, socket_path: str, timeout: float = 5.0) -> None:
        self.socket_path, self.timeout = socket_path, timeout

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
                c.settimeout(self.timeout)
                c.connect(self.socket_path)
                c.sendall(encoded)
                buf = bytearray()
                while not buf.endswith(b"\n"):
                    chunk = c.recv(65_536)
                    if not chunk:
                        break
                    buf.extend(chunk)
        except OSError as exc:
            raise BrokerError(f"broker socket: {exc}") from exc
        try:
            resp = json.loads(bytes(buf).decode() or "{}")
        except ValueError as exc:
            raise BrokerError("broker returned no JSON") from exc
        if resp.get("ok") is not True:
            raise BrokerError(f"broker refused: {resp.get('error')}: {resp.get('message')}")
        return resp

    def status(self, job_id: str) -> dict[str, Any] | None:
        return self._request({"action": "medchron_job_status", "job_id": job_id}).get("job")

    def record(self, job_id: str, state: str, fields: dict[str, Any]) -> dict[str, Any]:
        return self._request({"action": "medchron_job_record", "job_id": job_id, "state": state, "fields": fields})

    def allowance(self, exclude_job_id: str | None = None) -> dict[str, Any]:
        """The month's page allowance and spend as the broker counts them now.
        `exclude_job_id` leaves THIS job's own row out, so a resume does not
        meter a run against the cents it already recorded."""
        req: dict[str, Any] = {"action": "medchron_allowance"}
        if exclude_job_id:
            req["exclude_job_id"] = exclude_job_id
        return self._request(req)


def sticky_level(db_path: str) -> str | None:
    """The seat's worst persisted sticky-stop level, read-only; None when no
    state file exists yet (a fresh Machine); 'unknown' on a read error (never
    a fabricated OK)."""
    if not os.path.exists(db_path):
        return None
    order = ["OK", "WARN", "SOFT_STOP", "HARD_STOP"]
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            levels = [str(r[0]) for r in conn.execute("SELECT level FROM sticky_stop_state").fetchall()]
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - a read-only observer fails toward unknown
        return "unknown"
    if not levels:
        return "OK"
    return max(levels, key=lambda lv: order.index(lv) if lv in order else 0)


def memory_cap_mode(cgroup_root: Path = CGROUP_ROOT) -> str:
    """Which memory controller this guest offers: ``cgroup2`` (unified root),
    ``cgroup1`` (the hybrid layout Fly Machines run: v2 mounted bare at
    /sys/fs/cgroup/unified with no controllers, memory on the v1 mount), or
    ``none``. Probed live on hermes-ashton-price 2026-08-31: no
    ``cgroup.controllers`` at the root, ``cgroup ... memory`` in /proc/mounts."""
    try:
        if "memory" in (cgroup_root / "cgroup.controllers").read_text().split():
            return "cgroup2"
    except OSError:
        pass
    if (cgroup_root / "memory" / "cgroup.procs").exists():
        return "cgroup1"
    return "none"


def default_runner_cmd() -> list[str]:
    return [
        "setpriv",
        f"--reuid={CHILD_UID_NAME}",
        f"--regid={CHILD_UID_NAME}",
        "--init-groups",
        "--no-new-privs",
        "nice",
        "-n",
        "10",
        RUNNER_BIN,
        "run",
    ]


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
    child_uid: str | None = None
    clock: Callable[[], float] = time.time
    child_env: dict[str, str] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    jobs_run: int = 0
    wakes_failed: int = 0
    #: Explicit firm-config path; None resolves the env/default the seat uses.
    firm_config: str | None = None
    gate_url: str = field(default_factory=lambda: os.environ.get(GATE_URL_ENV) or DEFAULT_GATE_URL)
    wake_secret: str = field(default_factory=lambda: os.environ.get(WAKE_SECRET_ENV, ""))

    # -- paths -------------------------------------------------------------
    @property
    def queue(self) -> Path:
        return self.run_dir / "queue"

    @property
    def jobs(self) -> Path:
        return self.run_dir / "jobs"

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
        }
        tmp = self.run_dir / ".heartbeat.tmp"
        try:
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.chmod(tmp, 0o644)
            tmp.replace(self.run_dir / "heartbeat.json")
            (self.run_dir / "tick").touch()
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

    def _in_progress(self) -> list[str]:
        if not self.jobs.is_dir():
            return []
        out = []
        for d in sorted(self.jobs.iterdir()):
            st = self._daemon_state(d.name)
            if not (d.is_dir() and st.get("state") not in TERMINAL and st.get("claimed")):
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
        row for is quarantined."""
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
                logger.error("quarantined %s: the broker has no ledger row for it", job_id)
                continue
            jd = self.job_dir(job_id)
            jd.mkdir(parents=True, exist_ok=True, mode=0o700)
            env_path.replace(jd / "envelope.json")
            self._write_state(job_id, claimed=True, state="submitted", claimed_at=self.clock(), attempts=0)
            return job_id
        return None

    # -- one job ---------------------------------------------------------------
    def _write_job_yaml(self, job_id: str) -> Path:
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
        ):
            if env.get(key) is not None:
                doc[key] = env[key]
        # The month's state is read FRESH here, on every run and every resume,
        # never taken from the envelope: the envelope's copy was true when the
        # job was submitted and is stale the moment any other job records cents.
        # This job's own row is excluded so a resume is not metered against the
        # spend it already recorded.
        state = self.broker.allowance(exclude_job_id=job_id)
        doc["allowance_pages"] = int(state.get("allowance") or 0)
        doc["allowance_remaining_pages"] = int(state.get("remaining") or 0)
        doc["month_pages_used"] = int(state.get("pages_used") or 0)
        doc["month_cents_used"] = int(state.get("cents_used") or 0)
        if state.get("month"):
            doc["allowance_month"] = str(state["month"])
        import yaml

        (jd / "job.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        self._chown_child(jd)
        return jd

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
            cg = self.cgroup_root / "medchron"
            limit_file, limit = cg / "memory.max", str(self.memory_max)
        else:  # cgroup1: the hybrid layout Fly guests run
            cg = self.cgroup_root / "memory" / "medchron"
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

    def run_job(self, job_id: str) -> str:
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
        try:
            jd = self._write_job_yaml(job_id)
        except BrokerError as exc:
            logger.warning("could not read the month's allowance for %s, deferring: %s", job_id, exc)
            return "deferred"
        st = self._daemon_state(job_id)
        try:
            self.broker.record(job_id, "running", {})
        except BrokerError as exc:
            logger.warning("could not record running for %s: %s", job_id, exc)
            return "deferred"
        self._write_state(job_id, state="running", attempts=int(st.get("attempts", 0)) + 1, held_paused=False)
        env = {k: v for k, v in os.environ.items() if k in CHILD_ENV_PASS}
        env.update(self.child_env)
        env.setdefault("MEDCHRON_SEAT", "client")
        env.setdefault("PATH", "/usr/bin:/bin")
        env.setdefault("HOME", str(jd))
        log = (jd / "daemon.log").open("a", encoding="utf-8")
        pidfile = self.run_dir / "child.pid"
        try:
            proc = subprocess.Popen(
                [*self.runner_cmd, str(jd), "--json"],
                cwd=str(jd),
                env=env,
                stdout=subprocess.PIPE,
                stderr=log,
                text=True,
                preexec_fn=self._cgroup_preexec(),
            )
            pidfile.write_text(str(proc.pid))
            out, _ = proc.communicate()
            code = proc.returncode
        finally:
            log.close()
            pidfile.unlink(missing_ok=True)
        self.jobs_run += 1
        return self._report(job_id, code, out or "")

    def _report(self, job_id: str, code: int, out: str) -> str:
        try:
            outcomes = json.loads(out) if out.strip() else []
        except ValueError:
            outcomes = []
        stage: str | None = None
        if not isinstance(outcomes, list) or not outcomes:
            state, fields = "failed", {"reason": f"the runner exited {code} without a verdict"[:500]}
        else:
            rank = {"delivered": 0, "dry_run": 0, "held": 1, "refused": 2, "failed": 3}
            worst = max(outcomes, key=lambda o: rank.get(str(o.get("outcome")), 3))
            outcome = str(worst.get("outcome"))
            cents = int(round(sum(float(o.get("dollars") or 0) for o in outcomes) * 100))
            pages = int(sum(int(o.get("pages") or 0) for o in outcomes))
            documents = int(max(int(o.get("documents") or 0) for o in outcomes))
            reason = worst.get("reason")
            stage = str(worst.get("stage") or "") or None
            if outcome == "refused":
                state, reason = "held", f"refused: {reason}"
            elif outcome in ("delivered", "dry_run"):
                state = "delivered"
            elif outcome == "held":
                state = "held"
            else:
                state = "failed"
            fields = {"documents": documents, "pages": pages, "cents": cents}
            if reason:
                fields["reason"] = str(reason)[:500]
            if state == "delivered":
                fields["folder_id"] = worst.get("folder_id")
                fields["delivery"] = {"files": list(worst.get("files") or [])}
        try:
            self.broker.record(job_id, state, fields)
        except BrokerError as exc:
            logger.error("could not record %s for %s: %s (the state file keeps it)", state, job_id, exc)
        finished = self.clock() if state in TERMINAL or state == "held" else None
        self._write_state(job_id, state=state, finished_at=finished, reason=fields.get("reason"))
        if finished is not None:
            self._write_state(
                job_id,
                wake={"pending": True, "attempts": 0, "task": self._compose_wake(job_id, state, fields, stage=stage)},
            )
        return state

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
        lines = [
            f"Run the medical-chronology-maintainer skill's DELIVER mode for chronology job {job_id}.",
            f"Outcome: {state}. Matter number: {matter_no}.",
            f"Documents: {fields.get('documents', 0)}; pages: {fields.get('pages', 0)}; "
            f"spend cents: {fields.get('cents', 0)}.",
        ]
        if fields.get("folder_id"):
            lines.append(f"Delivered folder id: {fields['folder_id']}.")
        if state != "delivered":
            lines.append(f"Held at: {stage or 'runner (no verdict)'}.")
        requester = env.get("requested_by") or ""
        ref = env.get("request_ref") or ""
        none_note = "(none - a rehearsal submission; report in the memo, create no task, send nothing)"
        lines.append(f"Requester: {requester or none_note}.")
        if ref:
            lines.append(f"Request ref: {ref}.")
        return "\n".join(lines)

    def _wakes_pending(self) -> list[str]:
        if not self.jobs.is_dir():
            return []
        return [
            d.name for d in sorted(self.jobs.iterdir()) if (self._daemon_state(d.name).get("wake") or {}).get("pending")
        ]

    def dispatch_wakes(self) -> None:
        """At-most-once with a loud loss: consumed on a 2xx answer AND on a
        timeout after the request was sent (a duplicate deliver turn is worse
        than a lost one; the loss escalates). A paused seat's 503 retries
        without consuming an attempt; five real failures write a same-state
        ledger note (an audit row) and stop."""
        import http.client
        from urllib.parse import urlparse

        for job_id in self._wakes_pending():
            wake = dict(self._daemon_state(job_id).get("wake") or {})
            if not self.wake_secret:
                logger.warning("no %s in the daemon env; wake for %s stays pending", WAKE_SECRET_ENV, job_id)
                return
            body = json.dumps({"handoff_id": f"medchron-{job_id}", "task": wake["task"]})
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
                outcome = "timeout after send" if sent else f"unreachable ({type(exc).__name__})"
            if outcome == "delivered" or (sent and outcome.startswith("timeout")):
                wake.update(pending=False, sent_at=self.clock(), outcome=outcome)
                self._write_state(job_id, wake=wake)
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
        wiped = []
        if not self.jobs.is_dir():
            return wiped
        for d in list(self.jobs.iterdir()):
            st = self._daemon_state(d.name)
            done = st.get("finished_at")
            if st.get("state") in TERMINAL and done and self.clock() - float(done) >= self.wipe_hours * 3600:
                shutil.rmtree(d, ignore_errors=True)
                wiped.append(d.name)
        return wiped

    def tick(self) -> str | None:
        """One iteration: heartbeat, wipe, pending wakes, then at most one job."""
        self.wipe_expired()
        self.dispatch_wakes()
        current = self._in_progress()
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
        while not stop():
            try:
                self.tick()
            except Exception:
                logger.exception("tick failed")
            slept = 0.0
            while slept < poll_seconds and not stop():
                time.sleep(min(1.0, poll_seconds - slept))
                slept += 1.0


def _require(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise SystemExit(f"{name} is required")
    return v


def build() -> Daemon:
    run_dir = Path(os.environ.get(RUN_DIR_ENV) or DEFAULT_RUN_DIR)
    return Daemon(
        run_dir=run_dir,
        broker=BrokerClient(_require(SOCKET_ENV)),
        runner_cmd=default_runner_cmd(),
        customer_slug=_require("CUSTOMER_SLUG"),
        sticky_db=os.environ.get(STICKY_DB_ENV) or DEFAULT_STICKY_DB,
        memory_max=int(os.environ.get(MEMORY_MAX_ENV) or DEFAULT_MEMORY_MAX),
        wipe_hours=float(os.environ.get(WIPE_HOURS_ENV) or DEFAULT_WIPE_HOURS),
        child_uid=CHILD_UID_NAME,
    )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("SMD_MEDCHRON_LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    stopped = {"flag": False}

    def _stop(*_: Any) -> None:
        stopped["flag"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        d = build()
    except SystemExit as exc:
        logger.error("medchron daemon not started: %s", exc)
        return 2
    if memory_cap_mode(d.cgroup_root) == "none":
        logger.error(
            "no cgroup memory controller at %s; jobs will run UNCAPPED (boot smoke fails on this)", d.cgroup_root
        )
    poll = float(os.environ.get(POLL_ENV) or 5)
    logger.info("medchron daemon up: run_dir=%s poll=%ss", d.run_dir, poll)
    d.run_forever(stop=lambda: stopped["flag"], poll_seconds=poll)
    return 0


if __name__ == "__main__":
    sys.exit(main())
