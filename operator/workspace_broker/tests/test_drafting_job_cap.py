"""SMD's per-job cost cap on a drafting job (``cap_cents``, 2026-11-24).

SMD needed a $35 cap on one A&P drafting job and could enforce it only by
watching the job and stopping it. The envelope now carries an optional
``cap_cents``: a whole number of cents, accepted from root only, carried to the
runner in the queue file. Absent, nothing changes. Each test fails if the line
it defends is removed."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from workspace_broker.drafting_ledger import EnvelopeError, queue_record, validate_envelope
from workspace_broker.drafting_verbs import REFUSAL_OUR_CODES

from test_drafting_verbs import _envelope, call, queued, seat, submit  # noqa: F401 - seat is the pytest fixture these tests take by name


def test_the_envelope_carries_a_cap_in_cents_and_the_queue_file_hands_it_to_the_runner() -> None:
    env = validate_envelope(_envelope(cap_cents=3500))
    assert env["cap_cents"] == 3500
    assert queue_record(env, "01JZZZZZZZZZZZZZZZZZZZZZZZ")["cap_cents"] == 3500


def test_no_cap_leaves_the_envelope_and_the_queue_file_as_they_were() -> None:
    env = validate_envelope(_envelope())
    assert "cap_cents" not in env
    assert "cap_cents" not in queue_record(env, "01JZZZZZZZZZZZZZZZZZZZZZZZ")


@pytest.mark.parametrize("bad", [0, -100, 35.0, "3500", True, None])
def test_a_cap_that_is_not_whole_positive_cents_is_refused(bad) -> None:
    with pytest.raises(EnvelopeError, match="cap_cents"):
        validate_envelope(_envelope(cap_cents=bad))


def test_the_agent_cannot_set_a_cap_and_nothing_is_queued(seat) -> None:
    broker, _db, _yaml, queue = seat
    out = submit(broker, cap_cents=3500)
    assert out["accepted"] is False and out["code"] == "cap_root_only" and "root only" in out["reason"]
    assert "cap_root_only" in REFUSAL_OUR_CODES
    assert queued(queue) == []


def test_root_sets_a_cap_and_the_runner_reads_it_in_the_queue_file(seat) -> None:
    broker, _db, _yaml, queue = seat
    out = call(broker, "drafting_job_submit", pid=1, uid=0, envelope=_envelope(cap_cents=3500))
    assert out["accepted"] is True, out
    job = json.loads((queue / f"{out['job_id']}.json").read_text())
    assert job["cap_cents"] == 3500
