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
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest
from host_cross_version_worker import REPLY
from host_matrix_cells import CELLS, STEPS, Run
from host_matrix_kit import (
    GOLDENS,
    SLUG,
    build_broker,
    freeze_broker_clock,
    load_allowed,
    seed_random,
    serve,
)
from host_matrix_lanes import remove

OLD_ENV = "JOBS_CROSS_VERSION_OLD"
#: The lanes checked (comma-separated); unset, every lane in ``LANES``.
LANES_ENV = "JOBS_CROSS_VERSION_LANES"
TESTS = Path(__file__).resolve().parent
NEW = TESTS.parents[2]
WORKER = TESTS / "host_cross_version_worker.py"
#: The lanes whose host this check holds (the families moved onto the one host).
LANES = ("medchron", "demand", "note", "drafting", "library_setup", "litigation", "casefile", "negotiation", "records")

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
    "queued_behind_same_lane": 3,
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
SAMPLE = {
    "medchron/delivered": 1,
    "demand/delivered": 1,
    "note/wake_paused": 1,
    "drafting/delivered": 1,
    "records/delivered": 1,
}


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


def _key(path: str) -> str:
    """A difference's key path: its list indexes blanked, its step (the first
    segment) kept. ``1.wire.3.request.fields.run`` and ``1.wire.2.request.fields.run``
    share one; ``1.wire.3.request.fields.text`` and the bare ``1.wire`` do not."""
    step, _, rest = path.partition(".")
    return ".".join([step, *("#" if seg.isdigit() else seg for seg in rest.split("."))]) if rest else step


def unexcused(found: list[str], allowed: Iterable[str], inherited: Iterable[str] = ()) -> list[str]:
    """The differences no reviewed entry excuses. An entry excuses a difference
    with its own key path in its own step and section, wherever a restart moved
    it in a list, and nothing wider: a whole list (``2.wire``) only when the
    entry names exactly that list. An entry the merge-base tree already carried
    (``inherited``: another change's leftover, cleared after its merge) excuses
    nothing here."""
    keys = {_key(p) for p in set(allowed) - set(inherited)}
    return [d for d in found if _key(d) not in keys]


#: Exact differences a restart onto the other version shows that no
#: same-version golden can (a fresh host's first-tick read of a row the other
#: version recorded), keyed ``<cell file>@<k>@<direction>``. Reviewed with the
#: PR that adds them and cleared after it merges, like an allowed list; each
#: entry must explain a difference in its own case or the case fails.
CROSS_ALLOWED = TESTS / "host_cross_version_allowed.json"


def _reviewed(cell: str, k: int, direction: str) -> tuple[list[str], list[str], list[str]]:
    """(this PR's host-matrix allowed entries for ``cell``, the merge-base
    tree's, this PR's exact cross-version entries for the case less the base's).
    With no old tree both sides are one version and nothing is excused."""
    if not os.environ.get(OLD_ENV):
        return [], [], []
    name = cell.replace("/", "__")
    base = _old() / "runners/jobs/tests/goldens/host_matrix"
    key = f"{name}@{k}@{direction}"
    mine = _cross(CROSS_ALLOWED).get(key, [])
    theirs = _cross(_old() / "runners/jobs/tests" / CROSS_ALLOWED.name).get(key, [])
    return (
        load_allowed(GOLDENS).get(name, []),
        load_allowed(base).get(name, []),
        sorted(set(mine) - set(theirs)),
    )


def _cross(path: Path) -> dict[str, list[str]]:
    if not path.is_file():
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    return {k: list(v) for k, v in doc.items() if not k.startswith("_")}


def _judge(found: list[str], allowed: list[str], inherited: list[str], exact: list[str]) -> list[str]:
    """What fails one case: an unexcused difference, or an exact entry that
    explains none (stale)."""
    left = [d for d in unexcused(found, allowed, inherited) if d not in set(exact)]
    return left + [f"stale cross-version entry: {e}" for e in exact if e not in set(found)]


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
        allowed, inherited, exact = _reviewed(cell, k, name)
        found = _judge(_diffs(got["same"], got[name]), allowed, inherited, exact)
        assert found == [], f"{name}: {cell} restarted after tick {k}"


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


# -- the reviewed list excuses a change, never its neighbours (planted) --------------------------


def _section_rule(found: list[str], allowed: list[str]) -> list[str]:
    """The rule this file used before 2026-11-17 review, kept only so each
    planted test below proves it would have let its defect through."""
    sections = {".".join(p.split(".")[:2]) for p in allowed}
    return [d for d in found if ".".join(d.split(".")[:2]) not in sections]


def _mutant(tmp_path: Path, edit: tuple[str, str, str]) -> Path:
    """This tree's runner with one host file edited (``(file, old, new)``)."""
    import shutil

    tree = tmp_path / "mutant" / "operator"
    shutil.copytree(NEW / "runners/jobs/jobs", tree / "runners/jobs/jobs", ignore=shutil.ignore_patterns("__pycache__"))
    host = tree / "runners/jobs/jobs" / edit[0]
    text = host.read_text(encoding="utf-8")
    assert text.count(edit[1]) == 1, edit
    host.write_text(text.replace(edit[1], edit[2]), encoding="utf-8")
    return tree


def _planted(tmp_path: Path, mp: Any, cell: str, k: int, direction: str, edit: tuple[str, str, str]) -> list[str]:
    """The differences between this tree's own restart and a restart onto (or
    from) the mutant, at restart point ``k``."""
    mutant = _mutant(tmp_path, edit)
    same = [("old", NEW)] * k + [("old again", NEW)]
    mixed = [("old", NEW)] * k + [("new", mutant)] if direction == "old_new" else [("new", mutant)] * k + [("old", NEW)]
    runs = {}
    for name, plan in (("same", same), (direction, mixed)):
        with mp.context() as m:
            runs[name] = _run(tmp_path, m, cell, plan)
    return _diffs(runs["same"], runs[direction])


LANE_RECORD = "            self.broker.record(job_id, state, fields)\n"


def test_a_reviewed_run_entry_never_excuses_another_field_in_its_section(tmp_path, monkeypatch, real_lane_gate):
    """(a) ``medchron/delivered``'s reviewed entries name ``...fields.run``; a host
    that sends a DIFFERENT field of the same request must still fail."""
    allowed = load_allowed(GOLDENS)["medchron__delivered"]
    # A field the ledger ignores, so the change shows on this request and nowhere after it.
    edit = ("medchron/lane.py", LANE_RECORD, LANE_RECORD.replace("fields)", '{**fields, "stray": 1})'))
    found = _planted(tmp_path, monkeypatch, "medchron/delivered", 1, "new_old", edit)
    assert any(d.endswith(".fields.stray") for d in found), found
    assert unexcused(found, allowed) != []
    assert _section_rule(found, allowed) == [], "the old rule let this through; the test proves nothing"


def test_a_host_that_records_its_ending_twice_fails(tmp_path, monkeypatch, real_lane_gate):
    """(b) The deliver record sent twice: a longer wire and audit in a section
    the reviewed list names, never excused."""
    allowed = load_allowed(GOLDENS)["medchron__delivered"]
    edit = ("medchron/lane.py", LANE_RECORD, LANE_RECORD + LANE_RECORD)
    found = _planted(tmp_path, monkeypatch, "medchron/delivered", 1, "new_old", edit)
    assert "1.wire" in found and "1.audit" in found, found
    assert {"1.wire", "1.audit"} <= set(unexcused(found, allowed))
    assert _section_rule(found, allowed) == [], "the old rule let this through; the test proves nothing"


def test_a_wrong_state_word_after_the_restart_fails(tmp_path, monkeypatch, real_lane_gate):
    """(c) After the restart the new host writes ``failed`` where the old one's
    own restart leaves ``delivered``: a state word in a daemon section the
    reviewed list names (``run_pending``), never excused."""
    allowed = load_allowed(GOLDENS)["medchron__resume_failed"]
    # Written as the tick ends (its heartbeat), so nothing the tick does changes:
    # only the state word the folder is left with.
    hook = "    def poll_requests(self) -> None:\n"
    flip = (
        "    def heartbeat(self, *, running):\n"
        "        super().heartbeat(running=running)\n"
        "        for jd in sorted(self.jobs.iterdir()) if self.jobs.is_dir() else []:\n"
        '            if self._daemon_state(jd.name).get("state") == "delivered":\n'
        '                self._write_state(jd.name, state="failed")\n\n' + hook
    )
    found = _planted(tmp_path, monkeypatch, "medchron/resume_failed", 2, "old_new", ("medchron/lane.py", hook, flip))
    assert any(d.endswith(".state") and ".daemon." in d for d in found), found
    assert unexcused(found, allowed) != []
    assert _section_rule(found, allowed) == [], "the old rule let this through; the test proves nothing"


def test_an_entry_the_merge_base_carried_excuses_nothing():
    """(d) Another change's leftover entry (present in the base tree) excuses
    nothing here, and an entry only the base carries excuses nothing either."""
    found = ["1.wire.2.request.fields.run"]
    assert unexcused(found, ["1.wire.2.request.fields.run"], inherited=["1.wire.2.request.fields.run"]) == found
    assert unexcused(found, [], inherited=["1.wire.2.request.fields.run"]) == found
    assert unexcused(found, ["1.wire.4.request.fields.run"]) == []  # a restart moved it two requests on
    assert unexcused(["1.wire.4.request.fields.text"], ["1.wire.4.request.fields.run"]) != []
    assert unexcused(["1.wire"], ["1.wire.4.request.fields.run"]) == ["1.wire"]
    assert unexcused(["2.wire.0.request"], ["1.wire.0.request"]) == ["2.wire.0.request"]


def test_a_stale_cross_version_entry_fails_its_case():
    assert _judge([], [], [], ["3.wire.0.reply.jobs.0.state"]) == [
        "stale cross-version entry: 3.wire.0.reply.jobs.0.state"
    ]
    assert _judge(["3.wire.0.reply.jobs.0.state"], [], [], ["3.wire.0.reply.jobs.0.state"]) == []
