"""A stopped drafting job pages SMD once and its requester is told nothing
(Captain, 2026-11-06, jobs/job_notes.py).

THE RULES, each with the mutation that breaks it:

1. A held or failed drafting job raises ONE ``job_stopped`` alert, naming the
   job, its state, its class and its reason, and saying the requester was not
   told; the daemon never asks the broker to send (no ``job_note_send``).
   Mutation: route stopped jobs through ``_send_delivered`` and the send is
   asked for.
2. A delivered job is not paged: its wake turn sends it.
3. One ``job_stopped`` alert per job state (the broker's reply key). Mutation:
   drop ``unit=`` in ``_page_stopped`` and a second state in one attempt is
   never paged.
4. A lost wake on a delivered job has the broker send its reply in code and
   says so; on a stopped job it sends nothing and says nobody was told.
5. A job waiting on its lane's proof run is paged once, nothing sent.
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
    """The drafting lane's fake broker; ``job_note_send`` answers like the real
    one for a delivered job (it is never asked for a stopped one)."""

    def __init__(self, outcome: str = "sent") -> None:
        super().__init__()
        self.outcome = outcome
        self.down = False

    def _request(self, req: dict) -> dict:
        if self.down and req["action"] in ("job_note_send", "drafting_job_status"):
            self.requests.append(req)
            raise BrokerError("broker unreachable")
        if req["action"] != "job_note_send":
            return super()._request(req)
        self.requests.append(req)
        row = self.rows.get(req["job_id"]) or {}
        return {"ok": True, "outcome": self.outcome, "state": row.get("state"), "stop_class": "", "key": "k"}


def _lane(tmp_path, script=dl.FAIL_RUNNER, outcome="sent"):
    lane, _client = dl._lane(tmp_path, script=script)
    client = NoteClient(outcome)
    lane.broker.client = client
    return lane, client


def _actions(client) -> list[str]:
    return [r["action"] for r in client.requests]


def _alerts(client, code="job_stopped") -> list[dict]:
    return [r for r in client.requests if r["action"] == "delivery_proof_alert" and r["code"] == code]


def test_a_failed_job_pages_smd_once_and_sends_nothing(tmp_path):
    """Rule 1. FALSIFIER: on the parent commit ``job_note_send`` is asked and
    the page says the requester was sent its note."""
    lane, client = _lane(tmp_path)
    dl._submit(lane, client, JOB)
    assert lane.tick() == "failed"
    acts = _actions(client)
    assert "job_note_send" not in acts
    (alert,) = _alerts(client)
    assert alert["job_id"] == JOB and alert["lane"] == "drafting"
    assert alert["detail"].startswith("failed (failed): the gate refused")
    assert alert["detail"].endswith(job_notes.NOT_TOLD)
    assert lane._daemon_state(JOB)["wake"]["pending"] is True


def test_a_held_job_pages_smd_once_and_sends_nothing(tmp_path):
    lane, client = _lane(tmp_path)
    dl._submit(lane, client, JOB)
    (lane.jobs / JOB).mkdir(parents=True, exist_ok=True)
    client.rows[JOB] = {"state": "held", "reason": "request_incomplete: a name", "attempt": 1}
    assert job_notes.tell(lane, JOB, why="t") == job_notes.NOT_OWED
    assert "job_note_send" not in _actions(client)
    (alert,) = _alerts(client)
    assert alert["detail"] == f"held (held): request_incomplete: a name; {job_notes.NOT_TOLD}"


def test_a_delivered_job_is_not_paged_by_the_daemon(tmp_path):
    """Rule 2."""
    lane, client = _lane(tmp_path, script=dl.RUNNER)
    dl._submit(lane, client, JOB)
    assert lane.tick() == "delivered"
    assert "job_note_send" not in _actions(client) and _alerts(client) == []


def test_one_alert_per_job_state_and_a_new_state_pages_again(tmp_path):
    """Rule 3: the alert is keyed on the reply key, not the attempt."""
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
    assert "job_note_send" not in _actions(client)


def test_an_unreachable_broker_still_pages(tmp_path):
    """Rule 6."""
    lane, client = _lane(tmp_path)
    client.down = True
    assert job_notes.tell(lane, JOB, why="the job ended failed") == "failed"
    assert "could not be read; nothing was sent" in _alerts(client)[-1]["detail"]


def test_a_lost_wake_sends_a_delivered_reply_in_code_and_says_so(tmp_path):
    """Rule 4, delivered: unchanged from the parent commit."""
    lane, client = _lane(tmp_path, script=dl.RUNNER)
    dl._submit(lane, client, JOB)
    lane.tick()
    client.rows[JOB]["state"] = "delivered"
    watch.wake_lost(lane, JOB, "status 500")
    assert _actions(client).count("job_note_send") == 1
    (lost,) = _alerts(client, "wake_lost")
    assert "the requester was sent the reply in code" in lost["detail"]
    assert _alerts(client) == []


def test_a_lost_wake_on_a_stopped_job_sends_nothing(tmp_path):
    """Rule 4, stopped. FALSIFIER: on the parent commit the broker is asked to
    send and the page says the requester was sent the reply."""
    lane, client = _lane(tmp_path)
    dl._submit(lane, client, JOB)
    lane.tick()
    client.requests.clear()
    watch.wake_lost(lane, JOB, "status 500")
    assert "job_note_send" not in _actions(client)
    (lost,) = _alerts(client, "wake_lost")
    assert lost["detail"].endswith("nobody was told")


def test_other_lanes_are_not_paged(tmp_path):
    """Rule 6: only the code-sent lanes."""
    lane, client = _lane(tmp_path)
    lane.LANE = "demand"
    assert job_notes.tell(lane, JOB, why="t") == job_notes.NOT_OWED
    assert "job_note_send" not in _actions(client) and _alerts(client) == []


@pytest.mark.parametrize(
    ("row", "cls"),
    [
        ({"state": "failed", "reason": "review_held: 2"}, "review_held"),
        ({"state": "failed", "reason": "audit_unrepaired: 3"}, "failed"),
        ({"state": "submitted", "reason": "lane_unproven: x"}, "waiting"),
        ({"state": "held", "revise_of": "01J", "reason": "x"}, "held_revision"),
        ({"state": "held", "reason": "x"}, "held"),
    ],
)
def test_the_page_names_the_stop_class(row, cls):
    assert job_notes.stop_class(row) == cls


@pytest.mark.usefixtures("real_lane_gate")
def test_a_job_waiting_on_its_lanes_proof_is_paged_once_and_told_nothing(tmp_path):
    """Rule 5. FALSIFIER: on the parent commit the waiting job's requester is
    sent a "we are checking it" note (``job_note_send``)."""
    lane, client = _lane(tmp_path)
    (lane.run_dir / lane_proof.AUTHORED_NAME).write_text("schema: 1\nlanes: {}\nrevoked: []\n")
    dl._submit(lane, client, JOB)
    for _ in range(3):
        assert lane.claim_next() is None
    assert "job_note_send" not in _actions(client)
    (alert,) = _alerts(client)
    assert alert["detail"].startswith("submitted (waiting): lane_unproven: document-drafter.")
    note = next(r for r in client.records if r[1] == "submitted")
    assert note[2]["reason"].startswith("lane_unproven: document-drafter.")
