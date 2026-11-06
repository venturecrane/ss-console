"""A stopped chronology pages SMD once and its requester is told nothing
(Captain, 2026-11-06: "we should know. the firm should not."; the 2026-10-24
rule that the firm hears about a chronology only on delivery stands).

THE RULES, each with the mutation that breaks it:

1. A failed chronology that ``settle`` did not page raises ONE ``job_stopped``
   alert, gets no DELIVER wake, and the daemon asks the broker to send nothing.
   Mutation: drop the ``job_notes.tell`` call in ``MedchronLane._report`` and
   nothing pages; drop the ``failed`` branch of ``watch.wake_owed`` and a wake
   is written.
2. A failure ``settle`` already paged (a delivery without its proof) is not
   paged twice. Mutation: drop the ``paged_before`` comparison.
3. A held chronology pages under its hold's own code only.
4. A chronology waiting on its lane's proof run pages ``job_stopped`` once.
   Mutation: drop ``medchron`` from ``job_notes.LANES``.
5. A lost wake on a delivered chronology sends nothing in code (a chronology
   is not code-sent) and says nobody was told. Mutation: drop the
   ``DELIVERED_IN_CODE`` check and ``job_note_send`` is asked for.
6. A resumed chronology that fails again pages again: its ledger rows carry no
   attempt, so the page keys on the daemon's count of starts.
"""

from __future__ import annotations

import json

import test_proof_daemon as pd
import test_upload_daemon as ud
from jobs import job_notes, lane_proof
from jobs.proof import watch

JOB = "01A"


def _alerts(broker, code: str | None = None) -> list[dict]:
    return [
        r
        for r in broker.proof_requests
        if r["action"] == "delivery_proof_alert" and (code is None or r["code"] == code)
    ]


def _failed_unit() -> dict:
    return pd._unit(outcome="failed", reason="billing_extract: a pipeline stage stopped", stage="billing_extract")


def test_a_failed_chronology_pages_smd_once_with_no_wake_and_nothing_sent(tmp_path):
    """Rule 1. FALSIFIER: on main nothing pages and a wake is written, so the
    failure reached SMD only if a model turn read it."""
    d, broker, state = pd._run(tmp_path, _failed_unit())
    assert state == "failed"
    (alert,) = _alerts(broker)
    assert alert["code"] == "job_stopped" and alert["lane"] == "medchron" and alert["job_id"] == JOB
    assert alert["detail"].startswith("failed (failed): billing_extract")
    assert alert["detail"].endswith(job_notes.NOT_TOLD)
    assert "wake" not in d._daemon_state(JOB)
    assert not [r for r in broker.proof_requests if r["action"] != "delivery_proof_alert"]


def test_a_failure_settle_paged_is_not_paged_twice(tmp_path):
    """Rule 2: a delivered claim with no proof fails at settle under its code."""
    d, broker, state = pd._run(tmp_path, pd._unit())
    assert state == "failed"
    assert pd._codes(broker) == ["proof_missing"]
    assert "wake" not in d._daemon_state(JOB)


def test_a_held_chronology_pages_its_hold_code_only(tmp_path):
    """Rule 3."""
    d, broker, state = pd._run(tmp_path, pd._unit(outcome="held", reason="decision: 2 unplaced files", stage="decide"))
    assert state == "held" and pd._codes(broker) == ["gate_held"]
    assert "wake" not in d._daemon_state(JOB)


def test_a_chronology_waiting_on_its_proof_run_pages_once(tmp_path):
    """Rule 4. FALSIFIER: on main ``tell`` answers not_owed and pages nothing."""
    d, broker = ud._daemon(tmp_path)
    (d.jobs / JOB).mkdir(parents=True, exist_ok=True)
    broker.rows[JOB] = {"state": "submitted", "reason": f"{lane_proof.HOLD_PREFIX}medical-chronology"}
    assert job_notes.tell(d, JOB, why="waiting on the medical-chronology proof run") == job_notes.NOT_OWED
    assert job_notes.tell(d, JOB, why="waiting on the medical-chronology proof run") == job_notes.NOT_OWED
    (alert,) = _alerts(broker)
    assert alert["code"] == "job_stopped" and alert["detail"].startswith("submitted (waiting): lane_unproven")


def test_a_lost_wake_on_a_delivered_chronology_sends_nothing(tmp_path):
    """Rule 5. The fake broker refuses any verb but the proof ones, so a
    ``job_note_send`` fails the test."""
    d, broker = ud._daemon(tmp_path)
    (d.jobs / JOB).mkdir(parents=True, exist_ok=True)
    broker.rows[JOB] = {"state": "delivered"}
    watch.wake_lost(d, JOB, "timeout")
    assert [a["code"] for a in _alerts(broker)] == ["wake_lost"]
    assert _alerts(broker)[0]["detail"].endswith("nobody was told")


def test_a_resumed_chronology_that_fails_again_pages_again(tmp_path):
    """Rule 6."""
    d, broker = ud._daemon(tmp_path)
    (d.jobs / JOB).mkdir(parents=True, exist_ok=True)
    broker.rows[JOB] = {"state": "failed", "reason": "exit 1"}
    d._write_state(JOB, attempts=1)
    job_notes.tell(d, JOB, why="t")
    job_notes.tell(d, JOB, why="t")
    d._write_state(JOB, attempts=2)
    job_notes.tell(d, JOB, why="t")
    assert len(_alerts(broker, "job_stopped")) == 2


def test_the_envelope_is_not_read_for_the_page(tmp_path):
    """The page names the job, its state and its reason, never the envelope's
    requester or matter (they ride the ledger row SMD reads)."""
    d, broker, _ = pd._run(tmp_path, _failed_unit(), envelope={**ud._envelope(JOB), "requested_by": "a@example.test"})
    assert "a@example.test" not in json.dumps(_alerts(broker))
