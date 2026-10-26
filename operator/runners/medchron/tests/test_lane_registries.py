"""Every daemon lane is registered in every runner-side table that names lanes.

A lane added to ``medchron.lanes.LANES`` (or the two built-in slots, the
chronology daemon and the demand lane) must also appear in each registry the
runner keys by lane name, or the lane runs while one of its edges silently
falls through: its reply is watched under no kind, its proof key is
``unknown-lane``, ``lane-proof`` cannot find its queue, ``rewake`` refuses it,
an authored memory cap never reaches its cgroup, or its holds are classified
by an accident of the default branch.

Data-driven on purpose: the lane names come from each module's own ``LANE``
class attribute, so a new lane is covered the moment ``lanes.py`` names it.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from types import ModuleType

import pytest

from medchron import daemon, demand_lane, lane_memory, lane_proof, lane_proof_cli, lanes, rewake
from medchron.proof import holds, watch


def _lane_name(mod: ModuleType) -> str:
    """The ``LANE`` of the lane class this module defines (not one it imports)."""
    names = {
        cls.__dict__["LANE"]
        for _n, cls in inspect.getmembers(mod, inspect.isclass)
        if cls.__module__ == mod.__name__ and "LANE" in cls.__dict__
    }
    assert len(names) == 1, f"{mod.__name__} must define exactly one lane class with a LANE (found {names})"
    return names.pop()


def _short(mod: ModuleType) -> str:
    return mod.__name__.rsplit(".", 1)[-1]


#: ``lanes._module`` is the daemon's own fixed name -> module table.
MODULES = [daemon, demand_lane] + [lanes._module(name) for name in lanes.LANES]
#: Lanes that take jobs from a queue (a module with ``QUEUE_ENV``, plus the
#: chronology daemon's own queue). A watch-only lane (the SMD note) has none.
QUEUED = [m for m in MODULES if m is daemon or hasattr(m, "QUEUE_ENV")]
#: Lanes whose child joins the shared demand cgroup.
DEMAND_CGROUP = [m for m in MODULES if m is not daemon and hasattr(m, "MEMORY_ENV")]


def test_every_lane_module_names_its_lane() -> None:
    names = [_lane_name(m) for m in MODULES]
    assert len(names) == len(set(names)), f"two lane modules share a LANE: {names}"
    assert "medchron" in names and "demand" in names


@pytest.mark.parametrize("mod", MODULES, ids=_short)
def test_every_lane_has_a_reply_kind(mod: ModuleType) -> None:
    lane = _lane_name(mod)
    assert lane in watch.REPLY_KIND, f"proof.watch.REPLY_KIND has no entry for lane {lane!r}"
    assert re.fullmatch(r"[a-z_]+_job", watch.REPLY_KIND[lane])


@pytest.mark.parametrize("mod", QUEUED, ids=_short)
def test_every_queued_lane_has_a_proof_skill_and_key(mod: ModuleType) -> None:
    lane = _lane_name(mod)
    assert lane in lane_proof.SKILLS, f"lane_proof.SKILLS has no skill for lane {lane!r}"
    key = lane_proof.lane_key(lane, {})
    assert key.split(".", 1)[0] == lane_proof.SKILLS[lane]
    assert lane_proof.KEY_RE.match(key), key


@pytest.mark.parametrize("mod", QUEUED, ids=_short)
def test_every_queued_lane_has_its_queue_and_jobs_dirs(mod: ModuleType) -> None:
    lane = _lane_name(mod)
    assert lane in lane_proof_cli.QUEUES, f"lane_proof_cli.QUEUES has no queue for lane {lane!r}"
    assert lane in lane_proof_cli.JOBS, f"lane_proof_cli.JOBS has no jobs dir for lane {lane!r}"
    assert lane in rewake.JOBS, f"rewake.JOBS has no jobs dir for lane {lane!r}"
    if mod is not daemon:
        # The seat entrypoints export the lane's queue under the run dir by the
        # same names the CLI looks in (``<lane>-queue``, ``<lane>-jobs``).
        assert lane_proof_cli.QUEUES[lane] == f"{lane}-queue"
        assert lane_proof_cli.JOBS[lane] == f"{lane}-jobs"


def test_queue_and_jobs_dirs_are_distinct() -> None:
    assert len(set(lane_proof_cli.QUEUES.values())) == len(lane_proof_cli.QUEUES)
    assert len(set(lane_proof_cli.JOBS.values())) == len(lane_proof_cli.JOBS)


@pytest.mark.parametrize("mod", DEMAND_CGROUP, ids=_short)
def test_every_demand_cgroup_lane_reads_the_authored_demand_cap(mod: ModuleType) -> None:
    # Every lane other than the chronology runs its child in the shared demand
    # cgroup; an authored ``demand`` cap must reach each one's own env.
    assert mod.MEMORY_ENV in lane_memory.LANE_ENVS["demand"], (
        f"{mod.__name__}.MEMORY_ENV ({mod.MEMORY_ENV}) is missing from lane_memory.LANE_ENVS['demand']"
    )


def test_the_chronology_cap_is_its_own() -> None:
    assert lane_memory.LANE_ENVS["medchron"] == (daemon.MEMORY_MAX_ENV,)


@pytest.mark.parametrize("mod", MODULES, ids=_short)
def test_every_lane_is_classified_by_holds_or_named_as_using_the_default(mod: ModuleType) -> None:
    lane = _lane_name(mod)
    # A lane with its own table pages an unknown reason (an unrecognised hold
    # never goes unseen); a lane on the default branch never pages from here
    # and must be named where holds.py says so, so the choice is deliberate.
    cls, page = holds.classify(lane, "zz_never_a_code: registry probe")
    assert cls in holds.CLASSES
    if page is not None:
        return
    src = Path(holds.__file__).read_text(encoding="utf-8")
    default = re.search(r"# ([A-Za-z ,]+) holds are not classified here yet", src)
    assert default, "holds.classify no longer names the lanes that use its default branch"
    named = {w.lower() for w in re.findall(r"[A-Za-z]+", default.group(1))} - {"and"}
    assert lane in named, f"lane {lane!r} has no hold table and is not named as using the default in holds.py"
