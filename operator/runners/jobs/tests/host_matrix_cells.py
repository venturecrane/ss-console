"""One host matrix cell: build a seat, run the cell's steps through the real
lane, and record what every step left behind (``host_matrix_kit.py`` says
what is recorded and why)."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from host_matrix_kit import (
    CLOCK,
    ENDINGS,
    LANES,
    NOT_APPLICABLE,
    OPERATOR,
    PASSED_ENV,
    SLUG,
    WAKE_SECRET,
    Launches,
    Seat,
    broker_down,
    broker_up,
    build_broker,
    install_child,
    secret_names,
    serve,
)
from host_matrix_lanes import KITS, LaneKit, break_settings, remove

#: Every cell the matrix runs: lane x ending, less the declared not-applicable.
CELLS = tuple(f"{lane}/{ending}" for lane in LANES for ending in ENDINGS if f"{lane}/{ending}" not in NOT_APPLICABLE)
#: The endings a verdict-returning child stands for.
VERDICT_ENDINGS = (
    "delivered",
    "held",
    "failed",
    "no_verdict_usage",
    "stopped_by_smd_running",
    "machine_stop",
    "broker_down_mid_run",
)


# ---------------------------------------------------------------------------
# Normalising
# ---------------------------------------------------------------------------

_ULID = re.compile(r"\b[0-9A-HJKMNP-TV-Z]{26}\b")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
_HEX24 = re.compile(r"\b[0-9a-f]{24,}\b")
#: A process id in a sentence ("child pid 4242 alive"), and seconds a lane measured.
_PID = re.compile(r"\b(pid) \d+\b")
_SECONDS = re.compile(r"(\b)\d+s after")
#: The current month (the broker's month window reads the wall clock); a full
#: date is left alone.
_MONTH = re.compile(r"\b20\d\d-[01]\d\b(?!-\d)")


class Normal:
    """Volatile values to stable tokens, ULIDs numbered by first appearance."""

    def __init__(self, roots: list[Path], secrets: frozenset[str]) -> None:
        self.roots = [str(r) for r in roots] + [os.path.realpath(r) for r in roots]
        self.ids: dict[str, str] = {}
        self.secrets = secrets

    def text(self, s: str) -> str:
        for r in sorted(set(self.roots), key=len, reverse=True):
            s = s.replace(r, "<TMP>")
        s = s.replace(sys.executable, "<PYTHON>")
        s = _ULID.sub(lambda m: self.ids.setdefault(m.group(0), f"<ULID{len(self.ids) + 1}>"), s)
        s = _ISO.sub("<TS>", s)
        s = _HEX24.sub("<HEX>", s)
        s = _PID.sub(r"\1 <PID>", s)
        s = _SECONDS.sub(r"\1<N>s after", s)
        return _MONTH.sub("<MONTH>", s)

    def number(self, key: str, v: float) -> Any:
        if key in ("pid", "started_at") or key.endswith("_pid"):
            return "<PID>" if key != "started_at" else "<EPOCH>"
        if abs(v - CLOCK) < 1e6:
            return f"<CLOCK{v - CLOCK:+.1f}>"
        if 1e9 < v < 4e9:
            return "<EPOCH>"
        return v

    def __call__(self, doc: Any, key: str = "") -> Any:
        if (key == "pid" or key.endswith("_pid")) and doc not in (None, ""):
            return "<PID>"
        if isinstance(doc, dict):
            return {self.text(str(k)): self(v, str(k)) for k, v in sorted(doc.items(), key=lambda kv: str(kv[0]))}
        if isinstance(doc, (list, tuple)):
            return [self(v, key) for v in doc]
        if isinstance(doc, bool) or doc is None:
            return doc
        if isinstance(doc, (int, float)):
            return self.number(key, float(doc)) if isinstance(doc, float) or doc > 10**8 else doc
        return self.text(str(doc))

    def env(self, env: dict[str, str]) -> dict[str, str]:
        """A child's env: a registry secret is named with its presence only."""
        return {k: ("<SECRET:present>" if k in self.secrets else self.text(v)) for k, v in sorted(env.items())}


# ---------------------------------------------------------------------------
# The readers of the run dir
# ---------------------------------------------------------------------------


def _probe_module(run_dir: Path) -> Any:
    from host_matrix_kit import _load_path

    mod = _load_path("host_matrix_busy_probe", OPERATOR / "bin" / "lib" / "fleet" / "remote_busy_probe.py")
    mod.RUN = str(run_dir)
    # The process start time comes from /proc (Linux only); a golden must not
    # differ by platform, so the matrix reads it as unknown everywhere.
    mod.started_at = lambda pid: None
    return mod


def readers(run_dir: Path) -> dict[str, Any]:
    from bin.lib.fleet import busy

    probe = _probe_module(run_dir)
    errors: list[str] = []
    facts = {
        "lanes": probe.lanes(CLOCK, errors),
        "children": probe.children(errors),
        "turns": probe.turns(CLOCK, errors),
        "errors": errors,
    }
    verdict = busy.decide(facts)
    overlay = str(OPERATOR / "hermes-smd-overlay")
    if overlay not in sys.path:
        sys.path.insert(0, overlay)
    from shared import fleet_facts

    counts = fleet_facts.read_lane_counts(run_dir, clock=lambda: CLOCK)
    return {
        "busy_probe": {"busy": verdict.busy, "reasons": verdict.reasons, "facts": facts},
        "fleet_facts_lane_counts": counts._asdict(),
    }


# ---------------------------------------------------------------------------
# One cell
# ---------------------------------------------------------------------------


class _Lines(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(f"{record.levelname} {record.name} {record.getMessage()}")


class Run:
    """The seat, the lane and the recorder for one cell."""

    def __init__(self, tmp_path: Path, mp: Any, lane_name: str, ending: str) -> None:
        self.lane_name, self.ending = lane_name, ending
        self.kit: LaneKit = KITS[lane_name]
        # A root of the same length on every machine: a lane cuts a reason to a
        # fixed length, and a path in it must cut at the same character
        # everywhere. Short, too: the broker's unix socket lives under it.
        self.sock_dir = Path(tempfile.mkdtemp(prefix="hm", dir="/tmp"))
        self.seat = Seat(root=self.sock_dir / "seat", sock_dir=self.sock_dir)
        self.mp = mp
        self.launches = Launches(ending=ending if ending in VERDICT_ENDINGS else "delivered")
        self.steps: list[dict[str, Any]] = []
        self.norm = Normal([tmp_path, self.sock_dir], secret_names())
        self.marks = {"wire": 0, "posts": 0, "launches": 0, "audit": 0, "log": 0}
        self.handler = _Lines()
        self.lane: Any = None
        self.job_id = ""

    # -- the seat -------------------------------------------------------------
    def setup(self) -> None:
        from jobs import job_lanes, lane_proof

        seat, mp = self.seat, self.mp
        seat.run_dir.mkdir(parents=True)
        for lane in job_lanes.QUEUED_LANES:
            (seat.run_dir / job_lanes.LANE_QUEUE_DIR[lane]).mkdir()
        cg = seat.root / "cgroup"
        cg.mkdir()
        (cg / "cgroup.controllers").write_text("cpu memory pids\n")
        for name in PASSED_ENV:
            mp.setenv(name, f"value-of-{name}")
        pricing = seat.root / "pricing.json"
        from jobs_testkit import PRICING

        pricing.write_text(json.dumps(PRICING), encoding="utf-8")
        mp.setenv("JOBS_PRICING_JSON", str(pricing))
        mp.setenv("CUSTOMER_SLUG", SLUG)
        mp.setenv("SMD_JOB_STOP_GRACE_SECONDS", "0")
        mp.setenv("SMD_JOBS_GATE_URL", "http://127.0.0.1:1")
        mp.setenv("WEBHOOK_SECRET_MCP", WAKE_SECRET)
        if self.kit.queue_env:
            mp.setenv(self.kit.queue_env, str(seat.run_dir / job_lanes.LANE_QUEUE_DIR[self.kit.queue_lane]))
        for k, v in self.kit.env(seat).items():
            mp.setenv(k, v)
        if self.ending != "lane_unproven":
            keys = sorted({lane_proof.lane_key(lane, {}) for lane in job_lanes.QUEUED_LANES} | self._own_key())
            (seat.run_dir / lane_proof.AUTHORED_NAME).write_text(
                "lanes:\n" + "".join(f"  {k}: {{verify_id: vfy_01KTJ0BX0000000000000000AA}}\n" for k in keys)
            )
        install_child(mp, self.launches, seat)
        logging.getLogger("jobs").addHandler(self.handler)

    def _own_key(self) -> set[str]:
        from jobs import lane_proof

        if self.lane_name == "library_setup":
            return {f"{lane_proof.SKILLS['drafting']}.{lane_proof.LIBRARY_SETUP_CLASS}"}
        if self.lane_name == "drafting":
            return {f"{lane_proof.SKILLS['drafting']}.mediation_brief"}
        return set()

    def build(self, port: int) -> Any:
        from jobs.broker_client import BrokerClient
        from jobs.daemon import Daemon

        self.mp.setenv("SMD_JOBS_GATE_URL", f"http://127.0.0.1:{port}")
        d = Daemon(
            run_dir=self.seat.run_dir,
            broker=BrokerClient(str(self.seat.socket_path)),
            runner_cmd=[],
            customer_slug=SLUG,
            sticky_db=str(self.seat.root / "sticky.db"),
            cgroup_root=self.seat.root / "cgroup",
            child_uid=None,
            clock=self.seat.clock,
        )
        lane = self.kit.module().build_lane(d)
        lane.clock = self.seat.clock
        lane.child_uid = None
        lane.drain_seconds = 0.0
        lane.heartbeat_seconds = 3600.0
        if self.lane_name == "records":
            lane.chown = lambda *a: None
            lane.gateway_ids = lambda: (os.getuid(), os.getgid())
        self.lane = lane
        return lane

    # -- recording --------------------------------------------------------------
    def _audit(self) -> list[dict[str, Any]]:
        try:
            conn = sqlite3.connect(self.seat.audit_db)
            rows = conn.execute(
                "SELECT rowid, action_type, actor, skill_name, matter_ref, metadata FROM audit_log WHERE rowid > ? ORDER BY rowid",
                (self.marks["audit"],),
            ).fetchall()
            conn.close()
        except sqlite3.Error:
            return []
        if rows:
            self.marks["audit"] = rows[-1][0]
        return [
            {"action_type": a, "actor": b, "skill": c, "matter_ref": m, "metadata": json.loads(md or "null")}
            for _r, a, b, c, m, md in rows
        ]

    def _since(self, key: str, rows: list[Any]) -> list[Any]:
        out = rows[self.marks[key] :]
        self.marks[key] = len(rows)
        return out

    def _jobs(self) -> tuple[dict[str, Any], dict[str, str]]:
        daemon: dict[str, Any] = {}
        files: dict[str, str] = {}
        for p in sorted(self.seat.run_dir.glob("*jobs/*/daemon.json")):
            daemon[f"{p.parent.parent.name}/{p.parent.name}"] = json.loads(p.read_text(encoding="utf-8"))
        for name in ("job.json", "job.yaml"):
            for p in sorted(self.seat.run_dir.glob(f"*jobs/*/{name}")):
                files[f"{p.parent.parent.name}/{p.parent.name}/{name}"] = p.read_text(encoding="utf-8")
        return daemon, files

    def _tree(self) -> list[str]:
        root = self.seat.root
        skip = ("inputs/", "state/")
        return sorted(
            str(p.relative_to(root)) + ("/" if p.is_dir() else "")
            for p in root.rglob("*")
            if not str(p.relative_to(root)).startswith(skip) and "__pycache__" not in p.parts
        )

    def _cgroup(self) -> dict[str, str]:
        cg = self.seat.root / "cgroup"
        return {
            str(p.relative_to(cg)): ("<PID>" if p.name == "cgroup.procs" else p.read_text().strip())
            for p in sorted(cg.rglob("*"))
            if p.is_file()
        }

    def snap(self, step: str, result: Any) -> None:
        daemon, files = self._jobs()
        launches = self._since("launches", self.launches.rows)
        children = [{**c, "env": self.norm.env(c["env"])} for c in launches]
        self.steps.append(
            self.norm(
                {
                    "step": step,
                    "result": result,
                    "wire": self._since("wire", self.seat.log),
                    "audit": self._audit(),
                    "daemon": daemon,
                    "job_files": files,
                    "children": [{**c, "env": c["env"]} for c in children],
                    "cgroup": self._cgroup() if children else {},
                    "wake_posts": self._since("posts", self.seat.posts),
                    "log": self._since("log", self.handler.lines),
                    "tree": self._tree(),
                    "readers": readers(self.seat.run_dir),
                }
            )
        )
        # The child env is normalised on its own (secret masking), never twice.
        for got, raw in zip(self.steps[-1]["children"], children):
            got["env"] = raw["env"]

    # -- steps ---------------------------------------------------------------------------
    def tick(self, label: str = "tick") -> None:
        self.snap(label, self.lane.tick())

    def submit(self) -> str:
        self.job_id = self.kit.submit(self.broker, self.seat)
        self.snap("submit", "queued")
        return self.job_id

    def advance(self, seconds: float) -> None:
        self.seat.now["t"] += seconds


def run_cell(tmp_path: Path, mp: Any, cell: str) -> list[dict[str, Any]]:
    lane_name, ending = cell.split("/")
    r = Run(tmp_path, mp, lane_name, ending)
    r.setup()
    r.broker = build_broker(r.seat)
    try:
        with serve(r.seat, r.broker) as port:
            r.build(port)
            STEPS[ending](r)
    finally:
        logging.getLogger("jobs").removeHandler(r.handler)
        remove(r.sock_dir)
    return r.steps


# ---------------------------------------------------------------------------
# The endings
# ---------------------------------------------------------------------------


def _note_job(r: Run) -> None:
    from jobs import note_by_smd, note_lane

    nid = "01KTJ0BX0000000000000000NT"
    note_by_smd._write_job(r.lane, nid, "admin@firm.example", "<req-1@firm.example>", note_lane.wake_task(nid), CLOCK)
    r.snap("note recorded", "written")


def _plain(r: Run) -> None:
    """Delivered, held, failed, a lost verdict, SMD's stop mid-run: one run, then the wake."""
    if r.lane_name == "note":
        _note_job(r)
    else:
        r.submit()
        r.tick("run")
    r.tick("wake")


def _stopped_queued(r: Run) -> None:
    from jobs import smd_stop

    jid = r.submit()
    smd_stop.stop_queued(r.lane.queue, r.lane.job_dir(jid), by="smd@example.test", why="matrix stop", at=CLOCK)
    r.snap("smd stop (queued)", "stopped")
    r.tick("sweep")
    r.tick("idle")


def _machine_stop(r: Run) -> None:
    r.submit()
    # The Machine stops once the child is under way (host_matrix_child writes the mark).
    r.lane.stopping = lambda: any(r.seat.run_dir.glob("*jobs/*/.host-matrix-running"))
    r.tick("run, Machine stopping")
    r.lane.stopping = lambda: False
    r.launches.ending = "delivered"
    r.tick("next boot")
    r.tick("wake")


def _settings_missing(r: Run) -> None:
    r.submit()
    break_settings(r.kit)
    r.snap("settings removed", "removed")
    r.tick("first")
    r.advance(3601)
    r.tick("second")
    r.tick("wake")


def _broker_unreachable(r: Run) -> None:
    r.submit()
    hidden = broker_down(r.seat)
    r.tick("broker down")
    broker_up(r.seat, hidden)
    r.tick("broker back")
    r.tick("wake")


def _seat_paused(r: Run) -> None:
    r.submit()
    db = r.seat.root / "sticky.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE sticky_stop_state (scope TEXT, level TEXT)")
    conn.execute("INSERT INTO sticky_stop_state VALUES ('seat', 'HARD_STOP')")
    conn.commit()
    conn.close()
    r.tick("paused")
    r.tick("still paused")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE sticky_stop_state SET level = 'OK'")
    conn.commit()
    conn.close()
    r.tick("cleared")
    r.tick("wake")


def _busy(r: Run) -> None:
    r.submit()
    pid = r.seat.run_dir / str(r.kit.busy_pid)
    pid.write_text(str(os.getpid()))
    r.tick("busy")
    pid.unlink()
    r.advance(31)
    r.tick("free")
    r.tick("wake")


def _resume_failed(r: Run) -> None:
    from jobs.broker_client import BrokerClient

    r.launches.ending = "failed"
    jid = r.submit()
    r.tick("run")
    req: dict[str, Any] = {"action": f"{r.kit.verbs}_job_resume", "job_id": jid, "reason": "the matrix fixed it"}
    if r.lane_name == "medchron":
        req["redo"] = []  # the chronology's resume names the stages it reopens
    reply = BrokerClient(str(r.seat.socket_path))._request(req)
    r.snap("resume requested", reply.get("ok"))
    r.launches.ending = "delivered"
    r.tick("resumed run")
    r.tick("wake")


def _lane_unproven(r: Run) -> None:
    from jobs import lane_proof

    r.submit()
    r.tick("unproven")
    env = json.loads((r.lane.queue / f"{r.job_id}.json").read_text(encoding="utf-8"))
    key = lane_proof.lane_key(r.lane.LANE, env)
    lane_proof.append_event(
        r.seat.run_dir, {"event": "proven", "key": key, "verify_id": "vfy_01KTJ0BX0000000000000000AA"}
    )
    r.snap("proven", key)
    r.tick("proven run")
    r.tick("wake")


def _wake_run(r: Run) -> None:
    """A run that leaves a DELIVER wake pending: delivered, or for the
    negotiation lane (whose delivered job wakes per notice) failed."""
    if r.lane_name == "note":
        _note_job(r)
        return
    r.launches.ending = "failed" if r.lane_name == "negotiation" else "delivered"
    r.submit()
    r.tick("run")


def _wake_paused(r: Run) -> None:
    _wake_run(r)
    r.seat.gate_status = 503
    r.tick("gateway paused")
    r.seat.gate_status = 200
    r.tick("gateway back")


def _wake_lost(r: Run) -> None:
    _wake_run(r)
    r.seat.gate_status = 500
    for n in range(1, 6):
        r.tick(f"gateway refuses {n}")
    r.seat.gate_status = 200
    r.tick("gateway back")


def _broker_down_mid_run(r: Run) -> None:
    """The broker goes away while the child runs (the child takes the socket
    away): the ending cannot be recorded; the broker comes back."""
    r.submit()
    r.tick("run, broker gone mid-run")
    broker_up(r.seat, r.seat.sock_dir / "b.sock.down")
    r.tick("broker back")
    r.tick("wake")


def _orphan_envelope(r: Run) -> None:
    """An envelope in the queue with no ledger row: quarantined, never run."""
    from jobs import job_lanes

    q = r.seat.run_dir / job_lanes.LANE_QUEUE_DIR[r.kit.queue_lane]
    (q / "01KTJ0BX0000000000000000ZZ.json").write_text(json.dumps({"job_id": "01KTJ0BX0000000000000000ZZ"}))
    r.snap("orphan written", "written")
    r.tick("claim")


STEPS: dict[str, Callable[[Run], None]] = {
    "broker_down_mid_run": _broker_down_mid_run,
    "orphan_envelope": _orphan_envelope,
    "wake_paused": _wake_paused,
    "wake_lost": _wake_lost,
    "delivered": _plain,
    "held": _plain,
    "failed": _plain,
    "no_verdict_usage": _plain,
    "stopped_by_smd_running": _plain,
    "stopped_by_smd_queued": _stopped_queued,
    "machine_stop": _machine_stop,
    "settings_missing": _settings_missing,
    "broker_unreachable": _broker_unreachable,
    "seat_paused": _seat_paused,
    "busy": _busy,
    "resume_failed": _resume_failed,
    "lane_unproven": _lane_unproven,
}
assert set(STEPS) == set(ENDINGS)

__all__ = ["CELLS", "Run", "run_cell"]
_ = contextlib
