"""One job host in its own interpreter, for the cross-version check
(``test_host_cross_version.py``).

``python host_cross_version_worker.py`` runs with ONE version of the jobs
runner first on ``PYTHONPATH`` (the merge-base tree's or this tree's). It
builds the cell's lane exactly as ``host_matrix_cells.Run.build`` does, over
the seat the test process set up (its run dir, its broker on the real unix
socket, its wake gateway, its environment, inherited), and then ticks the lane
once per line it reads on stdin:

    {"now": <seat clock>, "ending": <the child's ending>, "stopping": <bool>}

and answers one line per tick, after ``REPLY``:

    {"result": <the tick's word>, "launches": [<child launch rows>], "log": [<warning lines>]}

A worker that exits and a fresh one that takes over the same seat is a
Machine restart; a fresh one on the other version is a release landing (or
rolled back) between two ticks of a job.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any


#: Each answer's prefix: anything else on stdout is not an answer.
REPLY = "@@host-cross-version "


class _Lines(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(f"{record.levelname} {record.name} {record.getMessage()}")


def _build(init: dict[str, Any], now: dict[str, float]) -> tuple[Any, Any, _Lines]:
    import pytest
    from host_matrix_kit import Launches, Seat, install_child, seed_random
    from host_matrix_lanes import KITS

    from jobs.broker_client import BrokerClient
    from jobs.daemon import Daemon

    seat = Seat(root=Path(init["root"]), sock_dir=Path(init["sock_dir"]))
    launches = Launches()
    mp = pytest.MonkeyPatch()
    install_child(mp, launches, seat)
    # The records lane draws its review row's id in this process: the same
    # fixed sequence the matrix uses, restarted with each worker.
    seed_random(mp)
    handler = _Lines()
    logging.getLogger("jobs").addHandler(handler)
    d = Daemon(
        run_dir=seat.run_dir,
        broker=BrokerClient(str(seat.socket_path)),
        runner_cmd=[],
        customer_slug=init["slug"],
        sticky_db=str(seat.root / "sticky.db"),
        cgroup_root=seat.root / "cgroup",
        child_uid=None,
        clock=lambda: now["t"],
    )
    lane = KITS[init["lane"]].module().build_lane(d)
    lane.clock = lambda: now["t"]
    lane.child_uid = None
    lane.drain_seconds = 0.0
    lane.heartbeat_seconds = 3600.0
    if init["lane"] == "records":
        lane.chown = lambda *a: None
        lane.gateway_ids = lambda: (os.getuid(), os.getgid())
    return lane, launches, handler


def main() -> int:
    init = json.loads(sys.stdin.readline())
    now = {"t": float(init["now"])}
    lane, launches, handler = _build(init, now)

    def mark() -> bool:  # the Machine-stop cell's own test: the child is under way
        return any(lane.run_dir.glob("*jobs/*/.host-matrix-running"))

    for line in sys.stdin:
        cmd = json.loads(line)
        now["t"] = float(cmd["now"])
        launches.ending = cmd["ending"]
        lane.stopping = mark if cmd["stopping"] else (lambda: False)
        before_l, before_h = len(launches.rows), len(handler.lines)
        result = lane.tick()
        out = {"result": result, "launches": launches.rows[before_l:], "log": handler.lines[before_h:]}
        sys.stdout.write(REPLY + json.dumps(out) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
