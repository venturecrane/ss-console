"""``drafting_job_add_to_request``: the attorney's later email in a running
job's thread reaches the job, or he is told it did not (2026-11-05, A&P). The
broker cannot read a job dir, so a root-side daemon answers it: this drives the
real ``request_additions.take`` on a thread, as the seat does."""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "runners" / "jobs"))

from jobs import request_additions as ra
from workspace_broker import delivered_matter, drafting_additions, verbs
from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.drafting_ledger import DraftingLedger
from workspace_broker.drafting_verbs import DraftingVerbs
from workspace_broker.server import Broker

GATEWAY_PID = 4242
AGENT_UID = 10000
ADMIN = "admin@firm.example"
ORIGINAL = "<asked-1@mail.firm.example>"
LATER = "<later-1@mail.firm.example>"
WORDS = "All appropriate topics, including those listed."
YAML = """
scope:
  admins:
    - admin@firm.example
personas:
  - slug: operator
    skills:
      - name: document-drafter
        enabled: true
        settings:
          drafting_allowance_per_cycle: 25
          enabled_classes: 'discovery_set'
"""


class _Daemon:
    """The lane surface ``take`` reads: a queue, job dirs, each job's state."""

    def __init__(self, queue: Path, root: Path) -> None:
        self.queue, self.root = queue, root
        self.states: dict[str, dict] = {}

    def job_dir(self, job_id: str) -> Path:
        return self.root / job_id

    def _daemon_state(self, job_id: str) -> dict:
        return self.states.get(job_id, {})


@pytest.fixture
def seat(tmp_path):
    db = str(tmp_path / "audit.db")
    writer = LedgerWriter(db)
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(YAML)
    queue = tmp_path / "drafting-queue"
    broker = Broker.__new__(Broker)
    broker.gateway_pid = GATEWAY_PID
    broker.agent_uid = AGENT_UID
    broker.ledger = writer
    broker.drafting = DraftingVerbs(DraftingLedger(db, queue), customer_yaml=str(yaml_path), audit_append=writer.append)
    broker.drafting.add_wait_seconds = 10.0
    return broker, db, queue


@pytest.fixture
def daemon(seat, tmp_path):
    _broker, _db, queue = seat
    d = _Daemon(queue, tmp_path / "jobs")
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            if queue.is_dir():
                ra.take(d)
            stop.wait(0.05)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    yield d
    stop.set()
    t.join(5)


def call(broker, action, *, pid=GATEWAY_PID, uid=AGENT_UID, **req):
    return broker.handle({"action": action, **req}, peer_pid=pid, peer_uid=uid)


def _submit(broker, ref=ORIGINAL):
    return call(
        broker,
        "drafting_job_submit",
        envelope={
            "matter": {"id": "b041dd06-30a4-4c1f-912b-27724bd77a64", "number": "900201"},
            "file_to": None,
            "requested_by": ADMIN,
            "request_ref": ref,
            "request_text": "Please draft the special interrogatories.",
            "document_class": "discovery_set",
        },
    )


def _job(broker) -> str:
    answer = _submit(broker)
    assert answer["accepted"], answer
    return answer["job_id"]


def _add(broker, job_id, **over):
    req = {
        "job_id": job_id,
        "request_ref": LATER,
        "requested_by": ADMIN,
        "thread_refs": [ORIGINAL, LATER],
        "words": WORDS,
        **over,
    }
    return call(broker, "drafting_job_add_to_request", **req)


def _audit_types(db: str) -> list[str]:
    import sqlite3

    conn = sqlite3.connect(db)
    try:
        return [r[0] for r in conn.execute("SELECT action_type FROM audit_log")]
    finally:
        conn.close()


def _past_compose(broker, daemon, queue, job_id) -> Path:
    jd = daemon.job_dir(job_id)
    jd.mkdir(parents=True)
    (queue / f"{job_id}.json").replace(jd / "envelope.json")
    ra.close_door(jd)
    return jd


def test_a_widening_before_compose_lands_and_is_audited(seat, daemon) -> None:
    broker, db, queue = seat
    job_id = _job(broker)
    was = broker.drafting.ledger.read(job_id)["envelope_digest"]
    answer = _add(broker, job_id)
    assert answer["result"] == "applied"
    assert answer["matter_number"] == "900201" and answer["document_class"] == "discovery_set"
    doc = json.loads((queue / f"{job_id}.json").read_text())
    assert doc["request_text"].endswith(WORDS)
    assert broker.drafting.ledger.read(job_id)["envelope_digest"] != was
    assert "DRAFTING_JOB_REQUEST_ADDED" in _audit_types(db)


def test_the_audit_row_carries_no_words(seat, daemon) -> None:
    import sqlite3

    broker, db, _queue = seat
    _add(broker, _job(broker))
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT metadata FROM audit_log WHERE action_type='DRAFTING_JOB_REQUEST_ADDED'").fetchall()
    finally:
        conn.close()
    assert rows and all(WORDS not in r[0] for r in rows)


def test_after_compose_the_words_are_kept_and_the_delivery_reply_names_them(seat, daemon) -> None:
    broker, _db, queue = seat
    job_id = _job(broker)
    jd = _past_compose(broker, daemon, queue, job_id)
    assert _add(broker, job_id)["result"] == "compose_started"
    assert WORDS not in (jd / "envelope.json").read_text()
    seen = call(broker, "drafting_job_status", job_id=job_id)["job"]
    assert seen["additions_not_covered"][0]["words"] == WORDS
    call(broker, "drafting_job_record", uid=0, job_id=job_id, state="running", fields={})
    call(
        broker,
        "drafting_job_record",
        uid=0,
        job_id=job_id,
        state="delivered",
        fields={"delivery": {"files": [{"name": "Set.docx", "role": "draft"}]}, "folder_id": "f"},
    )
    reply = delivered_matter.drafting(broker.drafting.ledger.read(job_id))
    assert WORDS in reply and "not in this document" in reply


def test_a_held_reply_names_them_without_a_filed_document_to_change(seat, daemon) -> None:
    broker, _db, queue = seat
    job_id = _job(broker)
    _past_compose(broker, daemon, queue, job_id)
    assert _add(broker, job_id)["result"] == "compose_started"
    call(broker, "drafting_job_record", uid=0, job_id=job_id, state="held", fields={"reason": "gate_refused: x"})
    reply = delivered_matter.drafting(broker.drafting.ledger.read(job_id))
    assert WORDS in reply and "filed document" not in reply


def test_a_repeat_after_compose_is_kept_once(seat, daemon) -> None:
    broker, _db, queue = seat
    job_id = _job(broker)
    _past_compose(broker, daemon, queue, job_id)
    assert _add(broker, job_id)["result"] == "compose_started"
    assert _add(broker, job_id)["result"] == "compose_started"
    assert len(call(broker, "drafting_job_status", job_id=job_id)["job"]["additions_not_covered"]) == 1


def test_a_different_sender_is_refused_and_nothing_is_written(seat, daemon) -> None:
    broker, _db, queue = seat
    job_id = _job(broker)
    assert _add(broker, job_id, requested_by="other@firm.example")["result"] == "not_requester"
    assert WORDS not in (queue / f"{job_id}.json").read_text()


def test_a_different_thread_is_refused_and_nothing_is_written(seat, daemon) -> None:
    broker, _db, queue = seat
    job_id = _job(broker)
    answer = _add(broker, job_id, thread_refs=[LATER, "<elsewhere@mail.firm.example>"])
    assert answer["result"] == "other_thread"
    assert WORDS not in (queue / f"{job_id}.json").read_text()


def test_the_asking_email_itself_adds_nothing(seat, daemon) -> None:
    broker, _db, queue = seat
    job_id = _job(broker)
    assert _add(broker, job_id, request_ref=ORIGINAL)["result"] == "same_email"
    assert WORDS not in (queue / f"{job_id}.json").read_text()


def test_a_repeat_is_idempotent(seat, daemon) -> None:
    broker, _db, queue = seat
    job_id = _job(broker)
    assert _add(broker, job_id)["result"] == "applied"
    assert _add(broker, job_id)["result"] == "applied"
    assert json.loads((queue / f"{job_id}.json").read_text())["request_text"].count(WORDS) == 1


def test_a_delivered_job_is_ended_with_no_marker_written(seat) -> None:
    broker, _db, queue = seat
    job_id = _job(broker)
    call(broker, "drafting_job_record", uid=0, job_id=job_id, state="running", fields={})
    call(broker, "drafting_job_record", uid=0, job_id=job_id, state="delivered", fields={"folder_id": "f"})
    answer = _add(broker, job_id)
    assert answer["result"] == "job_ended" and answer["state"] == "delivered"
    assert not list(queue.glob(".add*"))


def test_a_runner_that_does_not_answer_takes_nothing_and_says_so(seat) -> None:
    """FALSIFIER: leave the marker and the words land after the attorney was told they did not."""
    broker, _db, queue = seat
    broker.drafting.add_wait_seconds = 0.1
    job_id = _job(broker)
    assert _add(broker, job_id)["result"] == "not_confirmed"
    assert not list(queue.glob(f"{ra.MARKER_PREFIX}*"))


def test_too_many_words_are_refused_at_the_broker(seat) -> None:
    broker, _db, _queue = seat
    job_id = _job(broker)
    assert _add(broker, job_id, words="x" * (drafting_additions.MAX_WORDS + 1))["result"] == "too_long"


def test_an_unknown_job_refuses(seat) -> None:
    broker, _db, _queue = seat
    assert _add(broker, "01DRAFTJ0B000000000000000Z")["result"] == "no_such_job"


def test_the_verb_has_the_gate_submit_has() -> None:
    gates = {v.name: v.auth for v in verbs.VERBS}
    assert gates["drafting_job_add_to_request"] == gates["drafting_job_submit"]


def test_an_agent_uid_child_is_refused(seat) -> None:
    broker, _db, _queue = seat
    with pytest.raises(PermissionError):
        call(broker, "drafting_job_add_to_request", pid=GATEWAY_PID + 1, job_id="x")


def test_a_second_submit_says_it_collided_with_a_running_draft(seat) -> None:
    broker, _db, _queue = seat
    job_id = _job(broker)
    answer = _submit(broker, ref=LATER)
    assert answer["accepted"] is False and answer["job_id"] == job_id and answer["underway"] is True


def test_the_same_email_again_is_not_a_collision_with_a_running_draft(seat) -> None:
    broker, _db, _queue = seat
    _job(broker)
    answer = _submit(broker)
    assert answer["accepted"] is False and not answer.get("underway")


def test_a_revision_carries_the_words_the_first_draft_did_not_cover(seat, daemon) -> None:
    broker, _db, queue = seat
    job_id = _job(broker)
    _past_compose(broker, daemon, queue, job_id)
    assert _add(broker, job_id)["result"] == "compose_started"
    row = broker.drafting.ledger.read(job_id)
    out = drafting_additions.with_carried_words({"request_text": "Please add them."}, row)
    assert out["request_text"].startswith("Please add them.") and out["request_text"].endswith(WORDS)


def test_a_revision_carries_nothing_when_nothing_was_left_out(seat) -> None:
    broker, _db, _queue = seat
    row = broker.drafting.ledger.read(_job(broker))
    env = {"request_text": "Please add them."}
    assert drafting_additions.with_carried_words(env, row) == env
