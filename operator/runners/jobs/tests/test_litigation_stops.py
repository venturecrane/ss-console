"""A stopped litigation job pages SMD once and the firm is told nothing
(Captain, 2026-11-06: "we should know. the firm should not."; jobs/job_notes.py).

THE RULES, each with the mutation that breaks it:

1. A held or failed list raises ONE ``job_stopped`` alert naming the job, its
   state, its class and its reason, and saying the requester was not told; the
   daemon never asks the broker to send (no ``job_note_send``). Mutation: drop
   the ``job_notes.tell`` call in ``LitigationLane._report`` and nothing pages.
2. A hold the firm was asked about before (which staff, whose files, an empty
   scope) is labelled ``held_firm_cause`` with its class, so SMD knows to ask
   the firm by hand.
3. A delivered list is not paged, and a lost wake on one sends nothing in
   code: litigation's delivered reply stays the DELIVER turn's. Mutation: add
   ``litigation`` to ``job_notes.CODE_SENT_LANES`` and ``job_note_send`` is asked.
"""

from __future__ import annotations

from pathlib import Path

import test_litigation_lane as ll
from litigation_testkit import JOB
from jobs import job_notes
from jobs.proof import watch

FAILS = """
import sys
sys.exit(3)
"""
STAFF_HELD = """
import json
print(json.dumps({"verdict": "held", "stage": "reassign", "cents": 0, "matters_total": 0, "matters_reread": 0,
                  "flags_new": 0, "files": [], "folder_id": "", "reason": "staff_unresolved: from ambiguous"}))
"""


class RowClient(ll.FakeClient):
    """The litigation lane's fake broker, with the ledger keeping each recorded
    state and reason (``job_notes.tell`` reads them back)."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[dict] = []

    def _request(self, req: dict) -> dict:
        self.requests.append(req)
        if req["action"] == "job_note_send":
            raise AssertionError("a litigation reply is never sent in code")
        if req["action"] == "litigation_job_record":
            row = self.rows.setdefault(req["job_id"], {})
            row["state"] = req["state"]
            if "reason" in (req["fields"] or {}):
                row["reason"] = req["fields"]["reason"]
        return super()._request(req)


def _lane(tmp_path: Path, script: str) -> tuple[ll.LitigationLane, RowClient]:
    lane, _ = ll._lane(tmp_path, script=script)
    client = RowClient()
    lane.broker.client = client
    return lane, client


def _alerts(client: RowClient, code: str = "job_stopped") -> list[dict]:
    return [r for r in client.requests if r["action"] == "delivery_proof_alert" and r["code"] == code]


def test_a_failed_list_pages_smd_once_and_sends_nothing(tmp_path):
    """Rule 1. FALSIFIER: on the parent commit no ``job_stopped`` alert."""
    lane, client = _lane(tmp_path, FAILS)
    ll._submit(lane, client)
    assert lane.tick() == "failed"
    (alert,) = _alerts(client)
    assert alert["job_id"] == JOB and alert["lane"] == "litigation"
    assert alert["detail"].startswith("failed (failed): no_verdict: the runner exited 3")
    assert alert["detail"].endswith(job_notes.NOT_TOLD)
    assert lane._daemon_state(JOB)["wake"]["pending"] is True


def test_a_held_list_pages_smd_once(tmp_path):
    lane, client = _lane(tmp_path, ll.HELD)
    ll._submit(lane, client)
    assert lane.tick() == "held"
    (alert,) = _alerts(client)
    assert alert["detail"].startswith("held (held): parity_hold: ")


def test_a_firm_question_hold_is_labelled_for_smd(tmp_path):
    """Rule 2."""
    lane, client = _lane(tmp_path, STAFF_HELD)
    ll._submit(lane, client)
    assert lane.tick() == "held"
    (alert,) = _alerts(client)
    assert alert["detail"].startswith("held (held_firm_cause, staff_unresolved): staff_unresolved: from ambiguous")


def test_a_delivered_list_is_not_paged_and_a_lost_wake_sends_nothing(tmp_path):
    """Rule 3."""
    assert "litigation" not in job_notes.CODE_SENT_LANES
    lane, client = _lane(tmp_path, ll.RUNNER)
    ll._submit(lane, client)
    assert lane.tick() == "delivered"
    assert _alerts(client) == []
    watch.wake_lost(lane, JOB, "spawn_failed")
    (lost,) = _alerts(client, "wake_lost")
    assert lost["detail"].endswith("nobody was told")
    assert _alerts(client) == []
