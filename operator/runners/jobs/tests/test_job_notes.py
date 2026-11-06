"""The daemon has the broker send a drafting job's note in code, and pages SMD
once per job state (2026-11-06, jobs/job_notes.py).

THE RULES, each with the mutation that breaks it:

1. A held or failed drafting job asks the broker for its note BEFORE the wake
   is composed. Mutation: drop the ``job_notes.tell`` call in
   ``DraftingLane._report`` and no note is requested.
2. A delivered job is not noted here: its wake turn sends it. Mutation: call
   ``tell`` for every state and a delivered job is noted.
3. One ``job_stopped`` alert per job state, carrying the job id, the state and
   the code's reason. Mutation: drop ``unit=`` in ``tell`` and a second state
   in the same attempt is never paged, or a repeat is.
4. A lost wake sends the reply in code, and its page says so. Mutation: drop
   the ``tell`` in ``watch.wake_lost`` and the page says nobody was told.
5. A job waiting on its lane's proof run is noted once. Mutation: drop the
   ``tell`` in ``lane_proof.admits``.
6. Only code-sent lanes; a broker that cannot be reached still pages.
"""

from __future__ import annotations

import pytest
import test_drafting_lane as dl
from jobs import job_notes, lane_proof
from jobs.broker_client import BrokerError
from jobs.proof import watch

JOB = "01DRAFTJ0B000000000000000A"


class NoteClient(dl.FakeClient):
    """The drafting lane's fake broker, answering the note verb like the real
    one: the row's state, its class, its reply key, its reason."""

    def __init__(self, outcome: str = "sent") -> None:
        super().__init__()
        self.outcome = outcome
        self.down = False

    def _request(self, req: dict) -> dict:
        if req["action"] != "job_note_send":
            return super()._request(req)
        self.requests.append(req)
        if self.down:
            raise BrokerError("broker unreachable")
        row = self.rows.get(req["job_id"]) or {}
        state = str(row.get("state") or "")
        stop = {"failed": "failed", "held": "held", "submitted": "waiting"}.get(state, "")
        return {
            "ok": True,
            "outcome": self.outcome,
            "state": state,
            "stop_class": stop,
            "job_reason": str(row.get("reason") or ""),
            "key": f"drafting_job:{req['job_id']}:1:{state}",
        }


def _lane(tmp_path, script=dl.FAIL_RUNNER, outcome="sent"):
    lane, _client = dl._lane(tmp_path, script=script)
    client = NoteClient(outcome)
    lane.broker.client = client
    return lane, client


def _actions(client) -> list[str]:
    return [r["action"] for r in client.requests]


def _alerts(client, code="job_stopped") -> list[dict]:
    return [r for r in client.requests if r["action"] == "delivery_proof_alert" and r["code"] == code]


def test_a_failed_job_is_noted_before_its_wake_and_pages_smd_once(tmp_path):
    """Rules 1 and 3. FALSIFIER: on origin/main no job_note_send is ever asked."""
    lane, client = _lane(tmp_path)
    dl._submit(lane, client, JOB)
    assert lane.tick() == "failed"
    acts = _actions(client)
    assert acts.count("job_note_send") == 1
    assert acts.index("job_note_send") > max(i for i, a in enumerate(acts) if a == "drafting_job_record")
    (alert,) = _alerts(client)
    assert alert["job_id"] == JOB and alert["lane"] == "drafting"
    assert alert["detail"].startswith("failed (failed): ") and "the requester was sent its note" in alert["detail"]
    assert lane._daemon_state(JOB)["wake"]["pending"] is True


def test_a_delivered_job_is_not_noted_by_the_daemon(tmp_path):
    """Rule 2."""
    lane, client = _lane(tmp_path, script=dl.RUNNER)
    dl._submit(lane, client, JOB)
    assert lane.tick() == "delivered"
    assert "job_note_send" not in _actions(client) and _alerts(client) == []


def test_one_alert_per_job_state_and_a_new_state_pages_again(tmp_path):
    """Rule 3: the alert is keyed on the broker's reply key, not the attempt."""
    lane, client = _lane(tmp_path)
    dl._submit(lane, client, JOB)
    (lane.jobs / JOB).mkdir(parents=True, exist_ok=True)
    client.rows[JOB] = {"state": "held", "reason": "destination_mismatch: x"}
    job_notes.tell(lane, JOB, why="t")
    job_notes.tell(lane, JOB, why="t")
    assert len(_alerts(client)) == 1
    client.rows[JOB] = {"state": "failed", "reason": "no_verdict: the runner exited -9 without a verdict"}
    job_notes.tell(lane, JOB, why="t")
    assert len(_alerts(client)) == 2 and "no_verdict" in _alerts(client)[-1]["detail"]


def test_a_note_that_did_not_go_still_pages(tmp_path):
    """Rule 6: a refused note and an unreachable broker are both SMD's to see."""
    lane, client = _lane(tmp_path, outcome="refused")
    client.rows[JOB] = {"state": "failed", "reason": "audit_unrepaired: 3"}
    assert job_notes.tell(lane, JOB, why="t") == "refused"
    assert "the note was NOT sent (refused" in _alerts(client)[-1]["detail"]
    client.down = True
    assert job_notes.tell(lane, JOB, why="the job ended failed") == "failed"
    assert "could not be sent" in _alerts(client)[-1]["detail"]


def test_a_lost_wake_sends_the_reply_in_code_and_says_so(tmp_path):
    """Rule 4. FALSIFIER: on origin/main the page reads "nobody was told"."""
    lane, client = _lane(tmp_path, script=dl.RUNNER)
    dl._submit(lane, client, JOB)
    lane.tick()
    client.rows[JOB]["state"] = "delivered"
    watch.wake_lost(lane, JOB, "status 500")
    assert _actions(client).count("job_note_send") == 1
    (lost,) = _alerts(client, "wake_lost")
    assert "the requester was sent the reply in code" in lost["detail"]
    assert _alerts(client) == []  # a delivered reply is not a stopped job


def test_other_lanes_are_not_noted(tmp_path):
    """Rule 6: only the code-sent lanes."""
    lane, client = _lane(tmp_path)
    lane.LANE = "demand"
    assert job_notes.tell(lane, JOB, why="t") == job_notes.NOT_OWED
    assert "job_note_send" not in _actions(client)


@pytest.mark.usefixtures("real_lane_gate")
def test_a_job_waiting_on_its_lanes_proof_is_noted_once(tmp_path):
    """Rule 5. FALSIFIER: on origin/main the waiting job is noted for SMD's
    request card only, and the attorney hears nothing."""
    lane, client = _lane(tmp_path)
    (lane.run_dir / lane_proof.AUTHORED_NAME).write_text("schema: 1\nlanes: {}\nrevoked: []\n")
    dl._submit(lane, client, JOB)
    for _ in range(3):
        assert lane.claim_next() is None
    assert _actions(client).count("job_note_send") == 1
    note = next(r for r in client.records if r[1] == "submitted")
    assert note[2]["reason"].startswith("lane_unproven: document-drafter.")
    assert lane_proof.holds(lane.run_dir)[0]["job_id"] == JOB
