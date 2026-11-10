"""The jobs runner's seat-path defaults resolve to the values they always had.

The runner reads its jobs-family paths from the generated ``jobs.seat_layout``
(operator/contracts/seat-layout.yaml). Each row below is a default that was a
hand-written literal before the migration, with that literal: a regenerated
module or a wrong constant name changes a resolved value and fails here.
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from jobs import arrivals, daemon, lane_proof_cli
from jobs.casefile import state as casefile_state
from jobs.casefile_lane import CasefileLane
from jobs.demand_lane import DemandLane
from jobs.drafting_lane import DraftingLane
from jobs.litigation import manifest as litigation_manifest, textcache
from jobs.litigation_lane import LitigationLane
from jobs.negotiation_lane import NegotiationLane
from jobs.records import parse as records_parse
from jobs.records_lane import RecordsLane

# (resolved value, the literal it replaced), captured before the migration.
CONSTANTS = {
    "arrivals.DEFAULT_DATA_DIR": (arrivals.DEFAULT_DATA_DIR, "/opt/data/jobs"),
    "casefile.state.DEFAULT_DATA_DIR": (casefile_state.DEFAULT_DATA_DIR, "/opt/data/jobs"),
    "litigation.manifest.DEFAULT_DATA_DIR": (litigation_manifest.DEFAULT_DATA_DIR, "/opt/data/jobs"),
    "litigation.textcache.DEFAULT_DATA_DIR": (textcache.DEFAULT_DATA_DIR, "/opt/data/jobs"),
    "daemon.DEFAULT_RUN_DIR": (daemon.DEFAULT_RUN_DIR, "/run/smd-jobs"),
    "daemon.RUNNER_BIN": (daemon.RUNNER_BIN, "/opt/jobs/.venv/bin/jobs"),
    "lane_proof_cli.DEFAULT_RUN_DIR": (lane_proof_cli.DEFAULT_RUN_DIR, "/run/smd-jobs"),
    "records.parse.DEFAULT_STATE_DIR": (records_parse.DEFAULT_STATE_DIR, "/opt/data/jobs/records-state"),
}

# (lane class, the queue_dir default it replaced).
QUEUES: dict[str, tuple[Any, str]] = {
    "DemandLane": (DemandLane, "/run/smd-jobs/demand-queue"),
    "DraftingLane": (DraftingLane, "/run/smd-jobs/drafting-queue"),
    "LitigationLane": (LitigationLane, "/run/smd-jobs/litigation-queue"),
    "NegotiationLane": (NegotiationLane, "/run/smd-jobs/negotiation-queue"),
    "CasefileLane": (CasefileLane, "/run/smd-jobs/casefile-queue"),
    "RecordsLane": (RecordsLane, "/run/smd-jobs/records-queue"),
}


@pytest.mark.parametrize("name", sorted(CONSTANTS))
def test_a_constant_resolves_to_its_old_literal(name: str) -> None:
    value, literal = CONSTANTS[name]
    assert value == literal
    assert type(value) is str


@pytest.mark.parametrize("name", sorted(QUEUES))
def test_a_lane_queue_default_resolves_to_its_old_literal(name: str) -> None:
    lane, literal = QUEUES[name]
    (queue,) = [f for f in fields(lane) if f.name == "queue_dir"]
    assert queue.default_factory() == Path(literal)
