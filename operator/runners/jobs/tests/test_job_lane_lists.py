"""Every job-lane list the jobs runner holds equals operator/contracts/job-lanes.yaml.

The declaration is generated into ``jobs/job_lanes.py``. Each list below was a
hand copy; this compares it with the generated constant (by the column that
says how it differs), so a lane added to the yaml and missed here, or a hand
list edited alone, fails.
"""

from __future__ import annotations

from jobs import job_lanes as jl
from jobs import lane_proof, lane_proof_cli, lanes, stop
from jobs.proof import watch


def test_lane_proof_covers_every_queued_lane_by_its_skill() -> None:
    assert lane_proof.SKILLS == {lane: jl.LANE_SKILL[lane] for lane in jl.QUEUED_LANES}


def test_each_queued_lanes_queue_and_job_dirs() -> None:
    assert lane_proof_cli.QUEUES == jl.LANE_QUEUE_DIR
    assert lane_proof_cli.JOBS == jl.LANE_JOBS_DIR


def test_the_stop_tool_knows_each_queued_lanes_dirs_and_pid() -> None:
    assert stop.LANES == {lane: (jl.LANE_JOBS_DIR[lane], jl.LANE_CHILD_PID[lane]) for lane in jl.QUEUED_LANES}


def test_the_watch_answers_each_lane_under_its_job_kind() -> None:
    assert watch.REPLY_KIND == jl.LANE_JOB_KIND


def test_the_daemon_starts_a_module_for_every_declared_lane() -> None:
    named = {
        cls.__dict__["LANE"]
        for name in lanes.LANES
        for cls in vars(lanes._module(name)).values()
        if isinstance(cls, type) and cls.__module__ == lanes._module(name).__name__ and "LANE" in cls.__dict__
    }
    assert named == set(jl.JOB_LANES)
