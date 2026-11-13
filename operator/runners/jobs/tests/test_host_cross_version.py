"""Cross-version job folders: a job one version of the job host started, the
other version carries on, in both directions, over the real broker socket,
each host in its own interpreter.

A release restarts the jobs daemon between two ticks of any job: an old
host's job folder (its ``daemon.json``, envelope, job file, pending wake,
queue markers, ledger row) is resumed by the new host, and on a roll back a
new host's folder is read by the old one. Each case runs one host matrix cell
(``host_matrix_cells.STEPS``, the same seat, broker, gateway and children) with
the lane ticked by a worker interpreter (``host_cross_version_worker.py``),
restarted after tick ``k``:

* ``same``: the old version on both sides of the restart (what the fleet did
  before this release, a Machine restart at that point);
* ``old_new``: the old version, then the new one (the release lands);
* ``new_old``: the new version, then the old one (a roll back).

Both mixed runs must record exactly what ``same`` records at every step: the
wire, the audit rows, every ``daemon.json``, the job files, the child
launches, the wakes, the log lines, the run dir's files and what the busy
probe and the lane counts read of it.

The old version is the operator tree ``$JOBS_CROSS_VERSION_OLD`` names
(``bin/goldens/cross-version-hosts.py --base <sha>`` extracts the merge-base's
and runs this file); unset, both sides are this tree, and the file checks the
instrument itself (a restart at any tick changes nothing a cell records).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from host_cross_version_worker import REPLY
from host_matrix_cells import CELLS, STEPS, Run
from host_matrix_kit import SLUG, build_broker, freeze_broker_clock, seed_random, serve
from host_matrix_lanes import remove

OLD_ENV = "JOBS_CROSS_VERSION_OLD"
#: The lanes checked (comma-separated); unset, every lane in ``LANES``.
LANES_ENV = "JOBS_CROSS_VERSION_LANES"
TESTS = Path(__file__).resolve().parent
NEW = TESTS.parents[2]
WORKER = TESTS / "host_cross_version_worker.py"
#: The lanes whose host this check holds (the families moved onto the one host).
LANES = ("medchron", "demand", "note")

#: Ticks per cell's steps (``host_matrix_cells.STEPS``); a run that ticks a
#: different number of times fails, so a changed step list cannot go unseen.
TICKS = {
    "delivered": 2,
    "held": 2,
    "failed": 2,
    "no_verdict_usage": 2,
    "stopped_by_smd_running": 2,
    "stopped_by_smd_queued": 2,
    "machine_stop": 3,
    "settings_missing": 3,
    "broker_unreachable": 3,
    "seat_paused": 4,
    "busy": 3,
    "resume_failed": 3,
    "lane_unproven": 3,
    "wake_paused": 3,
    "wake_lost": 7,
    "broker_down_mid_run": 3,
    "orphan_envelope": 1,
}
#: The note lane's cells have no run tick (``host_matrix_cells._note_job``).
NOTE_TICKS = {"delivered": 1, "wake_paused": 2, "wake_lost": 6}


def _old() -> Path:
    return Path(os.environ.get(OLD_ENV) or NEW)


def _lanes() -> tuple[str, ...]:
    raw = os.environ.get(LANES_ENV, "")
    return tuple(x for x in raw.replace(",", " ").split() if x) or LANES


def _ticks(cell: str) -> int:
    lane, ending = cell.split("/")
    return NOTE_TICKS[ending] if lane == "note" else TICKS[ending]


#: Without an old tree both sides are this one: the suite then runs one
#: restart per checked lane, enough to keep the instrument honest at a fraction
#: of the cost (the full set runs against the merge-base in the goldens job).
SAMPLE = {"medchron/delivered": 1, "demand/delivered": 1, "note/wake_paused": 1}


def _cases() -> list[tuple[str, int]]:
    every = [(c, k) for c in CELLS if c.split("/")[0] in _lanes() for k in range(1, _ticks(c))]
    if os.environ.get(OLD_ENV):
        return every
    return [(c, k) for c, k in every if SAMPLE.get(c) == k]


def _pythonpath(tree: Path) -> str:
    """One version's runner ahead of everything, then this tree's test kit."""
    roots = [tree / "runners/jobs", *(tree / "connectors" / c for c in ("_sdk", "_contract", "smokeball")), TESTS]
    return os.pathsep.join(str(r) for r in roots)


class CrossRun(Run):
    """A host matrix cell whose lane ticks in a worker interpreter, one worker
    per entry of ``plan`` (a fresh worker is a restart)."""

    def __init__(self, tmp_path: Path, mp: Any, cell: str, plan: list[tuple[str, Path]]) -> None:
        lane, ending = cell.split("/")
        super().__init__(tmp_path, mp, lane, ending)
        self.plan = plan
        self.n = 0
        self.proc: subprocess.Popen[str] | None = None
        self.worker: tuple[str, Path] | None = None

    def _start(self, worker: tuple[str, Path]) -> None:
        env = {**os.environ, "PYTHONPATH": _pythonpath(worker[1]), "PYTHONDONTWRITEBYTECODE": "1"}
        self.proc = subprocess.Popen(  # noqa: S603 - this tree's own worker, over a fixed argv
            [sys.executable, str(WORKER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env
        )
        init = {"root": str(self.seat.root), "sock_dir": str(self.seat.sock_dir), "lane": self.lane_name,
                "slug": SLUG, "now": self.seat.clock()}  # fmt: skip
        self._send(init)
        self.worker = worker

    def stop(self) -> None:
        if self.proc is not None:
            assert self.proc.stdin is not None
            self.proc.stdin.close()
            assert self.proc.wait(timeout=120) == 0, "the worker failed"
            self.proc = None

    def _send(self, doc: dict[str, Any]) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(doc) + "\n")
        self.proc.stdin.flush()

    def _reply(self) -> dict[str, Any]:
        assert self.proc is not None and self.proc.stdout is not None
        for line in self.proc.stdout:
            if line.startswith(REPLY):
                return json.loads(line[len(REPLY) :])
        raise AssertionError(f"the worker ended without answering (exit {self.proc.wait()})")

    def tick(self, label: str = "tick") -> None:
        worker = self.plan[min(self.n, len(self.plan) - 1)]
        self.n += 1
        if worker != self.worker:
            self.stop()
            self._start(worker)
        # The Machine-stop cell sets ``stopping`` to its child-under-way test
        # (a function of the run dir), then back to a constant False.
        names = getattr(getattr(self.lane.stopping, "__code__", None), "co_names", ())
        self._send({"now": self.seat.clock(), "ending": self.launches.ending, "stopping": "glob" in names})
        reply = self._reply()
        self.launches.rows.extend(reply["launches"])
        self.handler.lines.extend(reply["log"])
        self.snap(label, reply["result"])


def _run(tmp_path: Path, mp: Any, cell: str, plan: list[tuple[str, Path]]) -> list[dict[str, Any]]:
    """``host_matrix_cells.run_cell`` with the lane in workers."""
    import logging

    r = CrossRun(tmp_path, mp, cell, plan)
    r.setup()
    r.broker = build_broker(r.seat)
    freeze_broker_clock(mp, r.seat)
    seed_random(mp)
    try:
        with serve(r.seat, r.broker) as port:
            r.build(port)
            STEPS[r.ending](r)
            r.stop()
    finally:
        if r.proc is not None:
            r.proc.kill()
        logging.getLogger("jobs").removeHandler(r.handler)
        remove(r.sock_dir)
    assert r.n == _ticks(cell), f"{cell} ticked {r.n} times; TICKS says {_ticks(cell)}"
    return json.loads(json.dumps(r.steps, sort_keys=True))


def _diffs(a: Any, b: Any, path: str = "") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[str] = []
        for k in sorted(set(a) | set(b)):
            sub = f"{path}.{k}" if path else str(k)
            out += [sub] if (k in a) != (k in b) else _diffs(a[k], b[k], sub)
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in _diffs(x, y, f"{path}.{i}" if path else str(i))]
    return [] if a == b else [path or "<root>"]


def test_every_cell_of_the_checked_lanes_has_its_cases():
    """MUTATION: a cell of a checked lane with no restart point between its ticks is not checked."""
    cells = [c for c in CELLS if c.split("/")[0] in _lanes()]
    assert cells and all(_ticks(c) >= 1 for c in cells)
    assert set(TICKS) == set(STEPS)
    if os.environ.get(OLD_ENV):
        assert {c for c, _k in _cases()} == {c for c in cells if _ticks(c) > 1}
    else:
        assert set(_cases()) == {(c, k) for c, k in SAMPLE.items() if c.split("/")[0] in _lanes()}


@pytest.mark.parametrize(("cell", "k"), _cases())
def test_a_job_folder_carries_across_versions(tmp_path, monkeypatch, real_lane_gate, cell, k):
    """MUTATION: a host that reads an older or newer job folder differently
    (a state word, a wake, a deferral count, a pending marker) fails here."""
    old, new = ("old", _old()), ("new", NEW)
    plans = {
        "same": [old] * k + [("old again", _old())],
        "old_new": [old] * k + [new],
        "new_old": [new] * k + [("old after", _old())],
    }
    got = {}
    for name, plan in plans.items():
        with monkeypatch.context() as mp:  # each run patches the broker's clock and ids afresh
            got[name] = _run(tmp_path, mp, cell, plan)
    for name in ("old_new", "new_old"):
        assert _diffs(got["same"], got[name]) == [], f"{name}: {cell} restarted after tick {k}"


def test_a_host_that_reads_a_folder_differently_fails(tmp_path, monkeypatch, real_lane_gate):
    """The check can fail: a "new" host that reads the pending wake the old
    host left in ``daemon.json`` one character differently must differ from
    the old host's own restart."""
    import shutil

    mutant = tmp_path / "mutant" / "operator"
    shutil.copytree(
        NEW / "runners/jobs/jobs", mutant / "runners/jobs/jobs", ignore=shutil.ignore_patterns("__pycache__")
    )
    host = mutant / "runners/jobs/jobs/daemon.py"
    text = host.read_text(encoding="utf-8")
    assert text.count('"task": wake["task"]}') == 1
    host.write_text(text.replace('"task": wake["task"]}', '"task": wake["task"] + " "}'), encoding="utf-8")
    runs = {}
    for name, plan in (("same", [("old", NEW), ("old again", NEW)]), ("old_new", [("old", NEW), ("new", mutant)])):
        with monkeypatch.context() as mp:
            runs[name] = _run(tmp_path, mp, "demand/delivered", plan)
    found = _diffs(runs["same"], runs["old_new"])
    assert any(".wake_posts." in d for d in found), found
