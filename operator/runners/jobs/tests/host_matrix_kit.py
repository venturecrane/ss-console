"""The job host matrix: every lane host x every ending its code can reach,
run through the REAL lane class (built by its own ``build_lane``), the REAL
broker (``workspace_broker.server.Broker`` with every lane's verbs and
ledgers, answering over a real unix socket through the runner's real
``BrokerClient``), the real ``child.run_child`` spawning a real process, and
the real lane-proof gate. Only the child's RUN class is swapped
(``host_matrix_child.py``), for one returning the cell's verdict built by the
lane's own verdict class.

Each cell is a short list of steps. After every step the matrix records:

* ``result``: what the step returned (a tick's state word);
* ``wire``: every broker request and reply, as the bytes crossed the socket;
* ``audit``: every audit row the broker appended (action type and metadata);
* ``daemon``: every job dir's ``daemon.json``;
* ``job_files``: ``job.json`` / ``job.yaml`` as the lane wrote them;
* ``children``: each child launch: argv, env (registry secret values masked,
  key presence only), cwd, pid file, whether a cgroup preexec ran, and the
  cgroup tree after;
* ``wake_posts``: each DELIVER wake the gateway received;
* ``log``: the runner's warning and error lines;
* ``tree``: the run dir's file names;
* ``readers``: what the fleet busy probe (``bin/lib/fleet/remote_busy_probe``
  judged by ``busy.decide``) and the heartbeat reader
  (``hermes-smd-overlay/shared/fleet_facts.read_lane_counts``) say of the run dir.

Volatile values are normalised (temp paths, ULIDs in order of appearance,
timestamps, epochs, pids) so a golden is the same on any machine.
"""

from __future__ import annotations

import contextlib
import http.server
import importlib.util
import json
import os
import re
import socketserver
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

OPERATOR = Path(__file__).resolve().parents[3]
if str(OPERATOR) not in sys.path:
    sys.path.insert(0, str(OPERATOR))

from bin.lib.golden_diff import diffs, load_allowed, unexplained  # noqa: E402 - needs the tree root on sys.path, set just above

CHILD = Path(__file__).resolve().parent / "host_matrix_child.py"
GOLDENS = Path(__file__).resolve().parent / "goldens" / "host_matrix"
#: The lane clock. Far from the real date, so a value read from the lane clock
#: and one read from the wall clock never look alike after normalising.
CLOCK = 1_700_000_000.0
WAKE_SECRET = "host-matrix-wake-secret"
SLUG = "example"

LANES = ("medchron", "demand", "drafting", "library_setup", "litigation", "casefile", "negotiation", "records", "note")
ENDINGS = (
    "delivered",
    "held",
    "failed",
    "no_verdict_usage",
    "stopped_by_smd_queued",
    "stopped_by_smd_running",
    "machine_stop",
    "settings_missing",
    "broker_unreachable",
    "seat_paused",
    "busy",
    "resume_failed",
    "lane_unproven",
    "wake_paused",
    "wake_lost",
    "broker_down_mid_run",
    "orphan_envelope",
)

#: Cells the host code cannot reach, each with the reason read from the code.
NOT_APPLICABLE: dict[str, str] = {
    "medchron/busy": "the chronology waits on no other lane's child (medchron/lane.py run_job has no busy check)",
    "demand/busy": "the demand lane waits on no other lane's child (demand_lane.py run_job has no busy check)",
    "library_setup/busy": "library setup runs inside the drafting lane before its demand-child wait (drafting_lane.py run_job returns library_setup_lane.run first)",
    "library_setup/settings_missing": "library setup runs before the drafting inputs check (drafting_lane.py run_job) and loads no firm file itself",
    "library_setup/resume_failed": "a library setup row has no resume verb (library_setup_verbs.VERBS)",
    "negotiation/held": "the negotiation lane records only delivered or failed (negotiation_lane.py _report)",
    "negotiation/settings_missing": "the negotiation lane loads no firm inputs before it runs (negotiation_lane.py run_job)",
    "note/held": "the note lane runs no child and records no verdict (note_lane.py: tick only wipes and dispatches)",
    "note/failed": "the note lane runs no child (note_lane.py)",
    "note/no_verdict_usage": "the note lane runs no child (note_lane.py)",
    "note/stopped_by_smd_queued": "the note lane has no queue (note_lane.py queue is never written)",
    "note/stopped_by_smd_running": "the note lane runs no child (note_lane.py)",
    "note/machine_stop": "the note lane runs no child to drain (note_lane.py)",
    "note/settings_missing": "the note lane loads no firm inputs (note_lane.py)",
    "note/broker_unreachable": "the note lane claims nothing from the broker (note_lane.py tick)",
    "note/seat_paused": "the note lane's tick never reads the sticky stop (note_lane.py tick)",
    "note/busy": "the note lane runs no child (note_lane.py)",
    "note/resume_failed": "a note has no resume verb (note_verbs.VERBS)",
    "records/wake_paused": "the records lane sends no DELIVER wake: its reply is an SMD review row (records_lane.py dispatch_wakes)",
    "records/wake_lost": "the records lane sends no DELIVER wake: its reply is an SMD review row (records_lane.py dispatch_wakes)",
    "note/broker_down_mid_run": "the note lane runs no child (note_lane.py)",
    "note/orphan_envelope": "the note lane has no queue to find an envelope in (note_lane.py tick)",
    "library_setup/orphan_envelope": "an orphan envelope is quarantined before its kind is read; the drafting cell holds it (daemon.py claim_next)",
    "note/lane_unproven": "the note lane claims nothing, so the lane-proof gate never runs (note_lane.py tick)",
}

#: Child-env names each lane passes from the daemon's own environment, given
#: a placeholder value so the matrix records which names reach the child.
PASSED_ENV = (
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
    "RECORDS_VENDOR_API_TOKEN",
    "RECORDS_VENDOR_API_URL",
    "RECORDS_VENDOR_NAME",
    "NOT_PASSED_TO_ANY_CHILD",
)


# ---------------------------------------------------------------------------
# The secret-name registry (masking)
# ---------------------------------------------------------------------------


def _load_path(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def secret_names() -> frozenset[str]:
    """Every name the generated secret registry classifies: the custody rows of
    ``contracts/env-consumption.yaml`` (the source ``_env-arrays.generated.sh``
    is generated from) plus ``bin/lib/secret_custody``'s own tables."""
    custody = _load_path("host_matrix_secret_custody", OPERATOR / "bin" / "lib" / "secret_custody.py")
    names = set(custody.load_contract_custody())
    for attr in dir(custody):
        value = getattr(custody, attr)
        if attr.isupper() and isinstance(value, (dict, frozenset, set)) and all(isinstance(k, str) for k in value):
            names.update(k for k in value if re.fullmatch(r"[A-Z][A-Z0-9_]+", k))
    return frozenset(n for n in names if _classified(custody, n))


def _classified(custody: Any, name: str) -> bool:
    try:
        custody.classify(name)
    except KeyError:
        return False
    return True


# ---------------------------------------------------------------------------
# The broker: real verbs, real ledgers, a real unix socket
# ---------------------------------------------------------------------------


class _Handler(socketserver.StreamRequestHandler):
    """``server.RequestHandler`` without SO_PEERCRED (absent on macOS): the
    peer is the root daemon, uid 0, as on the seat."""

    def handle(self) -> None:
        from workspace_broker.canon import canonical
        from workspace_broker.request_errors import error_response_for

        raw = self.rfile.readline()
        try:
            request = json.loads(raw)
            response = self.server.broker.handle(request, os.getpid(), 0)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - the server's own mapping
            response = error_response_for(exc)
        out = canonical(response) + b"\n"
        self.server.log.append({"request": json.loads(raw), "reply": json.loads(out)})  # type: ignore[attr-defined]
        self.wfile.write(out)


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


@dataclass
class Seat:
    """One seat in a temp dir: run dir, queues, inputs, broker, gateway."""

    root: Path
    sock_dir: Path
    log: list[dict[str, Any]] = field(default_factory=list)
    posts: list[dict[str, Any]] = field(default_factory=list)
    gate_status: int = 200
    now: dict[str, float] = field(default_factory=lambda: {"t": CLOCK})

    @property
    def run_dir(self) -> Path:
        return self.root / "run"

    @property
    def socket_path(self) -> Path:
        return self.sock_dir / "b.sock"

    @property
    def audit_db(self) -> Path:
        return self.root / "audit" / "audit.db"

    def clock(self) -> float:
        return self.now["t"]


def build_broker(seat: Seat) -> Any:
    """The real broker (``bin/lib/golden_broker.py``), queueing into this seat's run dir."""
    from bin.lib.golden_broker import build_broker as build

    from jobs import job_lanes

    queues = {lane: seat.run_dir / job_lanes.LANE_QUEUE_DIR[lane] for lane in job_lanes.QUEUED_LANES}
    b = build(seat.root, seat.audit_db, queues, slug=SLUG)
    b.socket_path = seat.socket_path
    return b


@contextlib.contextmanager
def serve(seat: Seat, broker: Any):
    """The broker on its socket, and a gateway that records every wake."""
    srv = _Server(str(seat.socket_path), _Handler)
    srv.broker = broker  # type: ignore[attr-defined]
    srv.log = seat.log  # type: ignore[attr-defined]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    class Gate(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            seat.posts.append(
                {
                    "path": self.path,
                    "auth": self.headers.get("Authorization") == f"Bearer {WAKE_SECRET}",
                    "body": json.loads(body),
                }
            )
            self.send_response(seat.gate_status)
            self.end_headers()
            self.wfile.write(b"paused" if seat.gate_status == 503 else b"{}")

        def log_message(self, *a: Any) -> None:
            return None

    gate = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Gate)
    gt = threading.Thread(target=gate.serve_forever, daemon=True)
    gt.start()
    try:
        yield gate.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()
        gate.shutdown()
        gate.server_close()


def broker_down(seat: Seat) -> Path:
    """Take the socket away (the broker is not running): connect() fails."""
    hidden = seat.sock_dir / "b.sock.down"
    seat.socket_path.replace(hidden)
    return hidden


def broker_up(seat: Seat, hidden: Path) -> None:
    hidden.replace(seat.socket_path)


# ---------------------------------------------------------------------------
# Children: record what the lane launches, then launch the matrix child
# ---------------------------------------------------------------------------


@dataclass
class Launches:
    rows: list[dict[str, Any]] = field(default_factory=list)
    ending: str = "delivered"


def install_child(mp: Any, launches: Launches, seat: Seat) -> None:
    from jobs import child as child_mod
    from jobs.seat_layout import JOBS_BIN

    real = child_mod.run_child

    def run_child(lane: Any, job_id: str, argv: list[str], *, cwd: Path, env: dict[str, str], log: Any,
                  pidfile: Path, preexec: Any) -> tuple[int, str]:  # fmt: skip
        launches.rows.append(
            {
                "lane": lane.LANE,
                "job_id": job_id,
                "argv": list(argv),
                "env": dict(env),
                "cwd": str(cwd),
                "pidfile": str(pidfile),
                "cgroup_preexec": preexec is not None,
            }
        )
        rest = argv[argv.index(JOBS_BIN) + 1 :] if JOBS_BIN in argv else argv
        child_env = {**env, "HOST_MATRIX_ENDING": launches.ending, "PYTHONDONTWRITEBYTECODE": "1",
                     "HOST_MATRIX_SOCKET": str(seat.socket_path)}  # fmt: skip
        for k in ("JOBS_LITIGATION_TEXTCACHE_DIR", "JOBS_SPEC_DIR", "JOBS_TEST"):
            if k in os.environ:
                child_env.setdefault(k, os.environ[k])
        return real(lane, job_id, [sys.executable, str(CHILD), *rest], cwd=cwd, env=child_env, log=log,
                    pidfile=pidfile, preexec=preexec)  # fmt: skip

    for mod in _lane_modules():
        if hasattr(mod, "run_child"):
            mp.setattr(mod, "run_child", run_child)
    mp.setattr(child_mod, "run_child", run_child)


def _lane_modules() -> list[Any]:
    from jobs import (
        casefile_lane,
        demand_lane,
        drafting_lane,
        library_setup_lane,
        litigation_lane,
        negotiation_lane,
        note_lane,
        records_lane,
    )
    from jobs.medchron import lane as medchron_lane

    return [
        medchron_lane,
        demand_lane,
        drafting_lane,
        library_setup_lane,
        litigation_lane,
        casefile_lane,
        negotiation_lane,
        records_lane,
        note_lane,
    ]


__all__ = [
    "CHILD",
    "CLOCK",
    "ENDINGS",
    "GOLDENS",
    "LANES",
    "NOT_APPLICABLE",
    "OPERATOR",
    "Launches",
    "Seat",
    "broker_down",
    "broker_up",
    "build_broker",
    "diffs",
    "install_child",
    "load_allowed",
    "secret_names",
    "serve",
    "unexplained",
]
