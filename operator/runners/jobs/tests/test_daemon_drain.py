"""A Machine stop drains EVERY lane, the chronology among them (ADR 0103).

Every lane runs on its own thread, started from ``lanes.LANES`` the same way;
the main thread only supervises, so the stop signal lands there and the exit is
``STOPPED_EXIT``, which the entrypoint does not respawn on (the 2026-10-15
wake_lost class). This runs the REAL ``daemon.main`` in its own process with
every lane really built and started: only each lane's ``tick`` is a test double,
one that holds a "job" until the stop and then takes a while to checkpoint it.
The parent sends SIGTERM, as the entrypoint supervisor does to
``SMD_JOBS_LOOP_PID``.

FALSIFIER: drop ``child_mod.join_lanes`` from ``main`` and the process exits
before the lanes' checkpoints land (they are daemon threads): no ``drained-*``
file is written. Start a lane anywhere but ``start_lanes`` and its
``started-*`` file never appears.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from jobs import daemon, lanes, quiesce

#: What each lane double spends checkpointing after the stop, and the drain
#: budget the daemon is given (``SMD_DRAIN_SECONDS``).
CHECKPOINT_SECONDS = 0.5
DRAIN_SECONDS = 6.0

_CHILD = r"""
import inspect, json, os, sys, time
from pathlib import Path

from jobs import daemon, lanes

run = Path(sys.argv[1])
CHECKPOINT = float(sys.argv[2])


class Client:
    def _request(self, payload):
        return {}


def fake_tick(self):
    started = run / f"started-{self.LANE}"
    if started.exists():
        return None
    started.write_text("1")
    while not self.stopping():
        time.sleep(0.01)
    stopped_at = time.monotonic()
    time.sleep(CHECKPOINT)  # the lane's child checkpointing
    (run / f"drained-{self.LANE}").write_text(json.dumps({"after_stop": time.monotonic() - stopped_at}))
    return None


for name in lanes.LANES:
    mod = lanes._module(name)
    for _n, cls in inspect.getmembers(mod, inspect.isclass):
        if cls.__module__ == mod.__name__ and cls.__dict__.get("LANE"):
            cls.tick = fake_tick

daemon.build = lambda: daemon.Daemon(
    run_dir=run, broker=Client(), runner_cmd=[], customer_slug="example", cgroup_root=run / "cg"
)
sys.exit(daemon.main([]))
"""

#: Every queued lane's queue env (the note lane has none, the chronology reads
#: ``<run_dir>/queue``), set as the entrypoint sets them for an authored lane.
QUEUE_ENVS = (
    "SMD_DEMAND_QUEUE_DIR",
    "SMD_DRAFTING_QUEUE_DIR",
    "SMD_LITIGATION_QUEUE_DIR",
    "SMD_NEGOTIATION_QUEUE_DIR",
    "SMD_CASEFILE_QUEUE_DIR",
    "SMD_RECORDS_QUEUE_DIR",
)


def _lane_names() -> list[str]:
    import inspect

    out = []
    for name in lanes.LANES:
        mod = lanes._module(name)
        out += [
            c.__dict__["LANE"]
            for _n, c in inspect.getmembers(mod, inspect.isclass)
            if c.__module__ == mod.__name__ and c.__dict__.get("LANE")
        ]
    return out


def _wait_for(pred, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def test_sigterm_drains_every_lane_and_exits_stopped(tmp_path: Path) -> None:
    names = _lane_names()
    assert len(names) == len(lanes.LANES) == 8 and "medchron" in names
    run = tmp_path / "run"
    run.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in (lanes.AUTHORED_ENV,)}
    env.update({q: str(tmp_path / q.lower()) for q in QUEUE_ENVS})
    env.update(SMD_DRAIN_SECONDS=str(DRAIN_SECONDS), SMD_JOBS_POLL_SECONDS="0.05", SMD_JOBS_LOG_LEVEL="WARNING")
    for q in QUEUE_ENVS:
        Path(env[q]).mkdir()
    proc = subprocess.Popen(  # noqa: S603 - argv is this interpreter and a literal script; no untrusted input
        [sys.executable, "-c", _CHILD, str(run), str(CHECKPOINT_SECONDS)],
        cwd=tempfile.gettempdir(),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        up = _wait_for(lambda: all((run / f"started-{n}").exists() for n in names), 60)
        assert up, f"lanes not all started: {sorted(p.name for p in run.iterdir())}"
        proc.send_signal(signal.SIGTERM)
        t0 = time.monotonic()
        _out, err = proc.communicate(timeout=DRAIN_SECONDS + 30)
        took = time.monotonic() - t0
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == daemon.STOPPED_EXIT == 130, err[-2000:]
    # Every lane's checkpoint landed before the process exited, inside the budget.
    for n in names:
        drained = run / f"drained-{n}"
        assert drained.is_file(), f"{n} was cut off mid-checkpoint"
        assert json.loads(drained.read_text())["after_stop"] < DRAIN_SECONDS
    assert CHECKPOINT_SECONDS <= took < DRAIN_SECONDS


def test_a_live_quiesce_stops_every_lane_claiming(tmp_path: Path, monkeypatch) -> None:
    """The quiesce file (``quiesce.py``) the supervisor writes on a Machine stop:
    every lane, each built the way ``start_lanes`` builds it, claims nothing
    while it is live, and the envelope stays queued."""
    for q in QUEUE_ENVS:
        (tmp_path / q.lower()).mkdir()
        monkeypatch.setenv(q, str(tmp_path / q.lower()))
    base = daemon.Daemon(run_dir=tmp_path, broker=object(), runner_cmd=[], customer_slug="example")
    (tmp_path / quiesce.NAME).write_text(json.dumps({"until": time.time() + 600, "by": "machine-stop", "reason": "t"}))
    built = []
    for name in lanes.LANES:
        lane = lanes._module(name).build_lane(base)
        assert lane is not None, name
        lane.queue.mkdir(parents=True, exist_ok=True)
        env = lane.queue / "01JQUIESCED0000000000000000.json"
        env.write_text("{}")
        assert quiesce.active(lane) and lane.claim_next() is None, lane.LANE
        assert env.is_file(), f"{lane.LANE} moved a queued envelope while quiesced"
        built.append(lane.LANE)
    assert len(set(built)) == len(lanes.LANES)
