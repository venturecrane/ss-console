"""The matter records lane: the root daemon's slot for records jobs (ADR 0102
amendment 4). A staff member's email asked for named records on a matter; the
Operator's turn queued this job (``records_job_submit``) and said one fixed
sentence. Nothing else in a turn touches the records.

The case file lane's shape (it subclasses ``DraftingLane`` for the loop, the
back-off and the demand cgroup):

* its own queue (``SMD_RECORDS_QUEUE_DIR``), job dirs ``<run_dir>/records-jobs``,
  tick ``records-tick``, child pid ``records-child.pid``;
* the broker's RECORDS verbs (``records_job_status`` / ``records_job_record``),
  and ``attachment_put``, which the broker opens to root for this lane;
* its child: ``jobs records <job_dir>`` as uid jobs, with the records
  inputs, the lane's state dir and the page index on the allow-listed env, and
  ``job.json`` stamped fresh with the slug, the month's records spend and the
  two caps from ``records-firm.yaml``.

NO DELIVER WAKE. The reply is an SMD REVIEW ROW (``records_contract.review_row``),
which the gateway's review sweeper sends once it is approved. ``_report``, as
root: spool each cut through the broker (chunked, as the reply plugin does),
split the cuts into replies, write the row (chowned to the gateway's uid,
0600), write ROOT's approve decision only when the verdict is delivered and the
firm's ``matter_file_delivery`` no longer asks for SMD review, then record the
job ``review`` on the broker (which raises the ops card). Every step is saved
in the job's daemon state before the next, so a crash between the spool and
the row re-enters and writes exactly one row. The lane then watches the row:
``sent`` -> delivered (with its proof record); ``rejected`` / ``expired`` /
``failed`` -> failed, and SMD is paged; still waiting after four hours -> SMD
is paged.

SAME MATTER, ONE AT A TIME. A job does not start while an earlier records job
on the same matter was started and has not reported (an interrupted run
resumes first), so two jobs never read or cut one matter's files at once.
Never refused: it waits.

A PROOF RUN (``proof_run.twin``) writes its row into the job dir, spools
nothing, decides nothing and records nothing: the real review dir and the
broker's spool are untouched.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import resume as resume_mod, seat_layout, verdict as verdict_mod
from .broker_client import BrokerClient, BrokerError
from .child import run_child
from .firm_inputs import pin_env
from .daemon import CHILD_ENV_PASS, RUNNER_BIN, TERMINAL, Daemon
from .drafting_lane import DraftingLane
from .proof import holds as proof_holds, watch as proof_watch
from . import seat_layout

logger = logging.getLogger("jobs.records")

#: The skill that queues this lane's jobs (lane_proof.SKILLS["records"]; the
#: overlay's capabilities.JOB_LANES reads it). No DELIVER wake runs it.
SKILL = "matter-records"
QUEUE_ENV = "SMD_RECORDS_QUEUE_DIR"
INPUTS_ENV = "JOBS_RECORDS_INPUTS_DIR"
STATE_ENV = "JOBS_RECORDS_STATE_DIR"
INDEX_ENV = "SMOKEBALL_PAGE_INDEX_DIR"
MEMORY_ENV = "SMD_RECORDS_MEMORY_MAX_BYTES"
DEFAULT_MEMORY = 1024 * 1024 * 1024
CUSTOMER_YAML_ENV = "SMD_CUSTOMER_YAML"
DEFAULT_CUSTOMER_YAML = seat_layout.CUSTOMER_YAML
CONFIG_BLOCK = "matter_file_delivery"
#: The env the child gets beyond the daemon's allow-list.
CHILD_ENV = (INPUTS_ENV, STATE_ENV, INDEX_ENV, "SMOKEBALL_PAGE_FINDER_MODEL", "SMOKEBALL_VISION_MODEL")
CHILD_PID = "records-child.pid"
#: A job does not START while any of these is live: the case file lane's set
#: (it shares the demand cgroup, and a chronology or litigation child shares
#: the Machine), plus the case file child itself, which waits on this lane's.
BUSY_PIDS = (
    "demand-child.pid",
    "drafting-child.pid",
    "child.pid",
    "litigation-child.pid",
    "casefile-child.pid",
)
BUSY_RETRY_SECONDS = 30.0
REVIEW_ALERT_SECONDS = 4 * 3600.0
GATEWAY_USER = "hermes"
CHUNK_BYTES = 3 * 1024 * 1024
_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
#: The broker's records_job_record keys (records_ledger.RECORD_FIELDS).
RECORD_FIELDS = ("state", "verdict", "reason", "stage", "cents", "delivery", "items")
VERDICTS = ("delivered", "held", "failed", "dry_run")


class RecordsBroker:
    def __init__(self, client: BrokerClient) -> None:
        self.client = client

    def status(self, job_id: str) -> dict[str, Any] | None:
        return self.client._request({"action": "records_job_status", "job_id": job_id}).get("job")

    def record(self, job_id: str, state: str | None, fields: dict[str, Any]) -> dict[str, Any]:
        body = {k: v for k, v in fields.items() if k in RECORD_FIELDS and k != "state"}
        if state is not None:
            body["state"] = state
        return self.client._request({"action": "records_job_record", "job_id": job_id, **body})

    def month_cents(self) -> int:
        """This Pacific month's records spend (root's status view). A missing
        value raises: the lane defers rather than run unmetered."""
        v = self.client._request({"action": "records_job_status"}).get("month_cents")
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise BrokerError("records_job_status carried no month_cents; the job cannot be metered")
        return v

    def put(self, data: bytes, sha256: str) -> str:
        """``data`` into the broker's own spool, chunked as the reply plugin's
        ``push_to_broker``; returns its handle."""
        handle, offset = "", 0
        while True:
            chunk = data[offset : offset + CHUNK_BYTES]
            final = offset + len(chunk) >= len(data)
            payload: dict[str, Any] = {
                "offset": offset,
                "chunk_b64": base64.b64encode(chunk).decode("ascii"),
                "final": final,
            }
            if handle:
                payload["handle"] = handle
            if final:
                payload["sha256"] = sha256
            answer = self.client._request({"action": "attachment_put", "payload": payload})
            handle = str(answer.get("handle") or "")
            if not _TOKEN_RE.fullmatch(handle):
                raise BrokerError("the broker did not take the file")
            offset += len(chunk)
            if final:
                if answer.get("sha256") != sha256:
                    raise BrokerError("the broker's copy did not hash to the cut")
                return handle


def records_runner_cmd() -> list[str]:
    privs = ["setpriv", "--reuid=jobs", "--regid=jobs", "--init-groups", "--no-new-privs"]
    return [*privs, "nice", "-n", "10", RUNNER_BIN, "records"]


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
    """The runner's one JSON object: the file first, then stdout's last line."""
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
        if isinstance(v, dict) and v.get("verdict") in VERDICTS:
            return v
    return None


def review_by_smd(doc: Any, released_so_far: int) -> bool:
    """The reply plugin's own rule (``matter_file_reply.review_by_smd``): ``review: smd``
    holds every files reply; ``review_first: N`` holds until SMD released N."""
    block = doc.get(CONFIG_BLOCK) if isinstance(doc, dict) else None
    if not isinstance(block, dict):
        return True  # unauthored: nothing goes without SMD
    if str(block.get("review") or "").strip().lower() == "smd":
        return True
    first = block.get("review_first")
    return isinstance(first, int) and not isinstance(first, bool) and released_so_far < first


def _seat_doc() -> Any:
    import yaml

    try:
        with open(os.environ.get(CUSTOMER_YAML_ENV) or DEFAULT_CUSTOMER_YAML, encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except Exception:  # noqa: BLE001 - an unreadable config means SMD reviews
        return None


def _gateway_ids(name: str = GATEWAY_USER) -> tuple[int, int]:
    import pwd

    pw = pwd.getpwnam(name)
    return pw.pw_uid, pw.pw_gid


@dataclass
class RecordsLane(DraftingLane):
    queue_dir: Path = field(default_factory=lambda: Path(seat_layout.RECORDS_QUEUE))
    inputs_dir: str | None = None
    state_dir: str | None = None
    index_dir: str | None = None
    #: Injectable for tests: ``chown(path, uid, gid)`` and the gateway's ids.
    chown: Callable[[str, int, int], None] = os.chown
    gateway_ids: Callable[[], tuple[int, int]] = _gateway_ids
    LANE = "records"
    HEARTBEAT = "records-heartbeat"

    @property
    def jobs(self) -> Path:
        return self.run_dir / "records-jobs"

    def _envelope(self, job_id: str) -> dict[str, Any]:
        try:
            return json.loads((self.job_dir(job_id) / "envelope.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    # -- the loop: a job in review is not re-run --------------------------------------------
    def _in_progress(self) -> list[str]:
        return [j for j in super()._in_progress() if self._daemon_state(j).get("state") != "review"]

    def _same_matter_busy(self, job_id: str) -> bool:
        """An earlier records job on this matter started and has not reported."""
        mine = str(self._envelope(job_id).get("matter_id") or "").lower()
        if not mine or not self.jobs.is_dir():
            return False
        for d in sorted(self.jobs.iterdir()):
            if d.name == job_id or not d.is_dir():
                continue
            st = self._daemon_state(d.name)
            if st.get("state") in TERMINAL or st.get("state") == "review" or not st.get("started"):
                continue
            if str(self._envelope(d.name).get("matter_id") or "").lower() == mine:
                return True
        return False

    # -- one job ---------------------------------------------------------------------------
    def _write_job(self, job_id: str) -> Path:
        from .records import firm as rec_firm

        jd = self.job_dir(job_id)
        firm = rec_firm.load(self.inputs_dir)
        doc = {
            **self._envelope(job_id),
            "job_id": job_id,
            "kind": "records",
            "slug": self.customer_slug,
            "month_cents_used": self.broker.month_cents(),
            "per_job_cap_usd": firm.per_job_cap_usd,
            "monthly_budget_usd": firm.monthly_budget_usd,
        }
        (jd / "data").mkdir(exist_ok=True)
        (jd / "job.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
        self._chown_child(jd)
        return jd

    def run_job(self, job_id: str) -> str:
        from .records import firm as rec_firm

        try:
            rec_firm.load(self.inputs_dir)
        except rec_firm.RecordsConfigError as exc:
            if not self._daemon_state(job_id).get("config_deferred"):
                logger.warning("records inputs not usable yet, deferring %s once: %s", job_id, exc)
                self._write_state(job_id, config_deferred=True)
                return "deferred"
            return self._fail(job_id, "config", "config_missing: the records inputs are not usable on this seat")
        if any(_live(self.run_dir / p) for p in BUSY_PIDS) or self._same_matter_busy(job_id):
            self._write_state(job_id, retry_after=self.clock() + BUSY_RETRY_SECONDS)
            return "waiting"
        reported = (self._daemon_state(job_id).get("reported") or {}).get("verdict") or {}
        if str(reported.get("verdict")) in ("delivered", "held", "dry_run"):
            # A reply was composed and its report did not finish (the broker
            # was down, or the Machine stopped): finish THAT report, never run
            # the child again for it.
            return self._report_reentry(job_id)
        if reported:
            # A failed attempt being resumed (resume.take_requests re-queued
            # it): nothing of its report is reused, so the child runs again.
            self._write_state(job_id, reported=None, row=None, spooled=None, review_watch=None)
        try:
            jd = self._write_job(job_id)
            self.broker.record(job_id, "running", {})
        except BrokerError as exc:
            logger.warning("broker not ready for %s, deferring: %s", job_id, exc)
            return "deferred"
        self._write_state(job_id, started=True)
        return self._spawn(job_id, jd)

    def _child_env(self, job_id: str, jd: Path) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k in CHILD_ENV_PASS or k in CHILD_ENV}
        env.update(self.child_env, JOBS_DAEMON_JOB_ID=str(job_id))
        for key, value in (
            (INPUTS_ENV, pin_env(self.inputs_dir)),
            (STATE_ENV, self.state_dir),
            (INDEX_ENV, self.index_dir),
        ):
            if value:
                env[key] = str(value)
        env.setdefault("JOBS_SEAT", "client")
        env.setdefault("PATH", "/usr/bin:/bin")
        env.setdefault("HOME", str(jd))
        return env

    def _spawn(self, job_id: str, jd: Path) -> str:
        verdict_mod.clear(jd)
        with (jd / "daemon.log").open("a", encoding="utf-8") as log:
            code, out = run_child(
                self,
                job_id,
                resume_mod.start_run(self, job_id, [*self.runner_cmd, str(jd)]),
                cwd=jd,
                env=self._child_env(job_id, jd),
                log=log,
                pidfile=self.run_dir / CHILD_PID,
                preexec=self._cgroup_preexec(),
            )
        self.jobs_run += 1
        finished = resume_mod.finished_verdict(read_verdict(jd, out))
        if not finished and resume_mod.cut_short_by_stop(self, job_id, code, out):
            return "interrupted"
        v = read_verdict(jd, out) or {
            "verdict": "failed",
            "stage": None,
            "reason": f"no_verdict: the runner exited {code} without a verdict",
        }
        self._write_state(job_id, reported={"verdict": v})
        return self._report(job_id, v)

    def _report_reentry(self, job_id: str) -> str:
        v = (self._daemon_state(job_id).get("reported") or {}).get("verdict") or {}
        return self._report(job_id, v)

    def _fail(self, job_id: str, stage: str, reason: str, cents: int = 0) -> str:
        try:
            self.broker.record(
                job_id, "failed", {"verdict": "failed", "reason": reason, "stage": stage, "cents": cents}
            )
        except BrokerError as exc:
            logger.error("could not record failed for %s: %s", job_id, exc)
            return "deferred"
        self._write_state(job_id, state="failed", finished_at=self.clock(), reason=reason)
        proof_watch.alert(self, job_id, "gate_held", reason)
        return "failed"

    # -- the report: spool, row, decision, record ---------------------------------------
    def _report(self, job_id: str, v: dict[str, Any]) -> str:
        verdict = str(v.get("verdict") or "failed")
        proof = proof_watch.in_proof_run(self)
        if verdict == "dry_run" and not proof:
            # proof/watch.settle's rule: a dry run is never a delivery.
            return self._fail(
                job_id, "verdict", "unexpected: a dry run reached the daemon as a verdict; nothing was sent"
            )
        if verdict == "failed":
            if proof:
                self._write_state(job_id, state="failed", finished_at=self.clock())
                return "failed"
            return self._fail(
                job_id,
                str(v.get("stage") or "runner"),
                str(v.get("reason") or "unexpected: failed"),
                int(v.get("cents") or 0),
            )
        jd = self.job_dir(job_id)
        facts = json.loads((jd / "data" / "facts.json").read_text(encoding="utf-8"))
        # A held job whose verdict carries cuts held only some records (records/
        # search.py): the found ones ride its row, which SMD reviews either way.
        cuts = self._cuts(jd) if verdict in ("delivered", "dry_run") or v.get("cuts") else []
        try:
            files = [self._spooled(job_id, jd, c, proof) for c in cuts]
        except (BrokerError, OSError) as exc:
            logger.error("could not spool %s's cuts: %s (re-entered next tick)", job_id, exc)
            return "deferred"
        row = self._row(job_id, v, facts, files, proof)
        if proof:
            self._write_state(job_id, state=verdict, finished_at=self.clock())
            return verdict
        decision = None
        if verdict == "delivered" and not self._review_by_smd():
            decision = self._decide(job_id, row)
        fields = {
            "verdict": verdict,
            "stage": v.get("stage"),
            "cents": int(v.get("cents") or 0),
            "items": [i for i in v.get("items") or [] if isinstance(i, dict)],
            "delivery": {
                "review_id": row["id"],
                "digest": row["digest"],
                **({"decision": decision} if decision else {}),
            },
        }
        if verdict != "delivered":
            fields["reason"] = str(v.get("reason") or "")[:500]
        try:
            self.broker.record(job_id, None, fields)
        except BrokerError as exc:
            logger.error("could not record review for %s: %s (re-entered next tick)", job_id, exc)
            return "deferred"
        self._write_state(
            job_id, state="review", review_watch={"id": row["id"], "since": self.clock(), "alerted": False}
        )
        if verdict == "held":
            code = proof_holds.classify(self.LANE, fields.get("reason"))[1]
            if code is not None:
                proof_watch.alert(self, job_id, code, fields["reason"])
        return "review"

    @staticmethod
    def _cuts(jd: Path) -> list[dict[str, Any]]:
        p = jd / "data" / "cuts.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []

    def _spooled(self, job_id: str, jd: Path, cut: dict[str, Any], proof: bool) -> dict[str, Any]:
        """One cut as a review-row file, spooled ONCE (the handle is saved)."""
        key = f"{cut['n']}-{cut['k']}"
        handles = dict(self._daemon_state(job_id).get("spooled") or {})
        if key not in handles:
            if proof:
                handles[key] = ""
            else:
                blob = (jd / "data" / cut["path"]).read_bytes()
                handles[key] = self.broker.put(blob, cut["sha256"])
            self._write_state(job_id, spooled=handles)
        return {
            "file_id": cut["file_id"],
            "name": cut["name"],
            "sha256": cut["sha256"],
            "size": int(cut["size"]),
            "handle": handles[key],
            "pages": cut["pages"],
            "page_count": int(cut["page_count"]),
        }

    def _review_dirs(self, job_id: str) -> tuple[Path, Path]:
        from records_contract import review_row as rr

        if proof_watch.in_proof_run(self):
            jd = self.job_dir(job_id)
            return jd / "proof-review", jd / "proof-decisions"
        return rr.review_dir(), rr.decisions_dir()

    def _row(
        self, job_id: str, v: dict[str, Any], facts: dict[str, Any], files: list[dict[str, Any]], proof: bool
    ) -> dict[str, Any]:
        """The one review row: rebuilt from the saved dict on re-entry, never a second id."""
        from records_contract import batches as batches_mod, review_row as rr

        st = self._daemon_state(job_id)
        row = st.get("row")
        if not isinstance(row, dict):
            env = self._envelope(job_id)
            delivered = str(v.get("verdict")) == "delivered" or (
                str(v.get("verdict")) == "dry_run" and v.get("would_be") != "held"
            )
            row = rr.hold_row(
                message_id=str(env.get("message_ref") or ""),
                to=str(env.get("requester") or ""),
                text=str(facts.get("reply_text") or ""),
                html=str(facts.get("reply_html") or ""),
                batches=batches_mod.batches(files) if delivered or files else [],
                matter_id=self._resolved_matter(job_id) or str(env.get("matter_id") or ""),
                session_id=f"records:{job_id}",
                reason="" if delivered else str(v.get("reason") or "")[: rr.REASON_CHARS],
                now=self.clock(),
            )
            self._write_state(job_id, row=row)
        review, _ = self._review_dirs(job_id)
        created = not review.is_dir()
        path = review / f"{row['id']}.json"
        if path.exists() or path.is_symlink():
            # Written on an earlier pass of this report. The gateway's sweeper
            # may have moved it on since (sending, sent), and ROOT's approve
            # still names its id and digest: rewriting it as `review` would
            # send the records a second time. A row this job wrote is never
            # written again.
            return row
        path = rr.write_row(row, review)
        if not proof:
            uid, gid = self.gateway_ids()
            if created:
                self.chown(str(review), uid, gid)
            self.chown(str(path), uid, gid)
        return row

    def _resolved_matter(self, job_id: str) -> str:
        """The matter's Smokeball id as the runner resolved it (the envelope may
        carry only its number)."""
        try:
            data = json.loads((self.job_dir(job_id) / "data" / "matter.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ""
        return str(data.get("matter_id") or "") if isinstance(data, dict) else ""

    def _decide(self, job_id: str, row: dict[str, Any]) -> str | None:
        from records_contract import review_row as rr

        _, decisions = self._review_dirs(job_id)
        try:
            rr.decision_file(decisions, row["id"], row["digest"], "approve")
        except FileExistsError:
            pass
        return "approve"

    def _review_by_smd(self) -> bool:
        from records_contract import review_row as rr

        return review_by_smd(_seat_doc(), rr.released_count())

    # -- the review watch ------------------------------------------------------------------
    def dispatch_wakes(self) -> None:
        """No DELIVER wakes on this lane: the review watch instead."""
        self.check_review_watches()

    def check_review_watches(self) -> None:
        if not self.jobs.is_dir() or proof_watch.in_proof_run(self):
            return
        for d in sorted(self.jobs.iterdir()):
            st = self._daemon_state(d.name) if d.is_dir() else {}
            w = st.get("review_watch")
            if st.get("state") == "review" and isinstance(w, dict):
                self._watch_one(d.name, st, w)

    def _watch_one(self, job_id: str, st: dict[str, Any], w: dict[str, Any]) -> None:
        from records_contract import review_row as rr

        review, _ = self._review_dirs(job_id)
        try:
            row = json.loads((review / f"{w['id']}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            row = {}
        status = str(row.get("status") or "")
        if status == rr.STATUS_REVIEW and rr.expired(row, self.clock()):
            status = rr.STATUS_EXPIRED
        if status == rr.STATUS_SENT:
            self._finish(job_id, st, "delivered", status)
        elif status in (rr.STATUS_REJECTED, rr.STATUS_EXPIRED, rr.STATUS_FAILED):
            self._finish(job_id, st, "failed", status)
        elif not w.get("alerted") and self.clock() - float(w.get("since") or 0) >= REVIEW_ALERT_SECONDS:
            if proof_watch.alert(self, job_id, "review_held", "a records reply has waited four hours in SMD review"):
                self._write_state(job_id, review_watch={**w, "alerted": True})

    def _finish(self, job_id: str, st: dict[str, Any], state: str, status: str) -> None:
        w = st["review_watch"]
        try:
            # The digest rides every review record: the broker's close reads it
            # to find ROOT's decision on exactly this row.
            digest = str((st.get("row") or {}).get("digest") or "")
            delivery = {"review_id": w["id"], **({"digest": digest} if digest else {}), "status": status}
            self.broker.record(job_id, None, {"delivery": delivery})
        except BrokerError as exc:
            if not str(exc).startswith("broker refused"):
                logger.error("could not record review %s for %s: %s (retried next tick)", status, job_id, exc)
                return
            # The broker answered and refused (SMD closed the job, or the row
            # no longer matches its ledger): retrying cannot change that. Said
            # once, and the watch ends.
            logger.error("the broker refused review %s for %s: %s; the watch ends here", status, job_id, exc)
            proof_watch.alert(
                self, job_id, "reply_unproven", f"review row ended {status}; the broker refused the record"
            )
            self._write_state(
                job_id, state="failed", finished_at=self.clock(), review_watch={**w, "done": status, "refused": True}
            )
            return
        v = (st.get("reported") or {}).get("verdict") or {}
        if state == "delivered" and v.get("verdict") == "delivered":
            proof_watch.settle(self, job_id, [v], "delivered", {})
        if state == "failed":
            proof_watch.alert(self, job_id, "reply_unproven", f"the records reply's review row ended {status}")
        self._write_state(job_id, state=state, finished_at=self.clock(), review_watch={**w, "done": status})


def build_lane(d: Daemon) -> RecordsLane | None:
    raw = os.environ.get(QUEUE_ENV, "").strip()
    if not raw:
        logger.info("no %s; the records lane is not started", QUEUE_ENV)
        return None
    from .records.parse import state_dir

    return RecordsLane(
        run_dir=d.run_dir,
        broker=RecordsBroker(d.broker.client if hasattr(d.broker, "client") else d.broker),
        runner_cmd=records_runner_cmd(),
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
        index_dir=os.environ.get(INDEX_ENV) or None,
    )


def start_lane(d: Daemon, *, stop: Callable[[], bool], poll_seconds: float) -> threading.Thread | None:
    lane = build_lane(d)
    if lane is None:
        return None
    t = threading.Thread(target=lane.run_forever, args=(stop, poll_seconds), name="records-lane", daemon=True)
    t.start()
    logger.info("records lane up: queue=%s jobs=%s", lane.queue, lane.jobs)
    return t


__all__ = ["RecordsBroker", "RecordsLane", "build_lane", "read_verdict", "review_by_smd", "start_lane"]
