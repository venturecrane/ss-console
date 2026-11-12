"""Every job-lane list the broker holds equals operator/contracts/job-lanes.yaml.

The declaration is generated into ``workspace_broker/job_lanes.py``. Each list
below was a hand copy; this compares it with the generated constant (by the
column that says how it differs), so a lane added to the yaml and missed here,
or a hand list edited alone, fails.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workspace_broker import delivery_proof, fixed_wording, help_signals, reply_binding
from workspace_broker import job_lanes as jl

#: The lanes whose completion wakes a turn (the lanes a wake word names).
_WAKE_LANES = {kind.removesuffix("_job") for kind in jl.WAKE_WORD_JOB_KIND.values()}


def test_every_lane_writes_a_delivery_proof_row() -> None:
    assert delivery_proof.LANES == frozenset(jl.JOB_LANES)


def test_the_reply_wording_lanes_are_the_lanes_that_wake_a_turn() -> None:
    assert fixed_wording.KIND_LANE == {jl.LANE_JOB_KIND[lane]: lane for lane in _WAKE_LANES}


def test_reply_binding_names_every_waking_kind() -> None:
    assert set(reply_binding._NOUN) == set(jl.WAKE_WORD_JOB_KIND.values())
    # The chronology's ledger carries no reply mark; every other waking kind does.
    assert reply_binding._LEDGER_MARKED_KINDS == frozenset(jl.WAKE_WORD_JOB_KIND.values()) - {"medchron_job"}


def test_the_help_review_reads_every_queued_lane_and_library_setup() -> None:
    assert set(help_signals.LANES) == set(jl.QUEUED_LANES) | {"library_setup"}
