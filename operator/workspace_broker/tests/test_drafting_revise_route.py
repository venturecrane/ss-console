"""Revise or draft, decided by the broker from its own job store (2026-11-04).

A reply asking for changes to a brief the Operator filed must reach the brief,
not a fresh draft that re-reads the matter: on 200045 a fresh job re-read 3,631
pages and spent $56.64 on a draft its own gate refused. The model only passes
the request; these tests pin what the BROKER decides from it. Each fails if the
line it defends is removed.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from workspace_broker.audit_ledger import LedgerWriter
from workspace_broker.drafting_ledger import DraftingLedger
from workspace_broker.drafting_verbs import DraftingVerbs, ThreadUnknown
from workspace_broker.server import Broker

from test_drafting_verbs import ADMIN, AGENT_UID, GATEWAY_PID, MATTER, _envelope, _yaml, call

FIRST = "<first@mail.firm.example>"
REPLY = "<reply@mail.firm.example>"
ELSEWHERE = "<other@mail.firm.example>"
THREADS = {FIRST: "conv-1", REPLY: "conv-1", ELSEWHERE: "conv-2"}


def _seat(tmp_path, thread_of, allowance=25):
    db = str(tmp_path / "audit.db")
    writer = LedgerWriter(db)
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(_yaml(allowance=allowance))
    queue = tmp_path / "drafting-queue"
    broker = Broker.__new__(Broker)
    broker.gateway_pid, broker.agent_uid, broker.ledger = GATEWAY_PID, AGENT_UID, writer
    broker.drafting = DraftingVerbs(
        DraftingLedger(db, queue), customer_yaml=str(yaml_path), audit_append=writer.append, thread_of=thread_of
    )
    return broker, db, queue, writer


def _deliver(broker, writer, ref=FIRST, proven=True) -> str:
    out = call(broker, "drafting_job_submit", envelope=_envelope(request_ref=ref))
    jid = out["job_id"]
    call(broker, "drafting_job_record", pid=1, uid=0, job_id=jid, state="running", fields={})
    files = [
        {"name": "Mediation Brief - 900201.docx", "size": 4100, "role": "draft"},
        {"name": "Mediation Brief - 900201 - attorney notes.docx", "size": 900, "role": "attorney_notes"},
    ]
    fields = {"cents": 300, "folder_id": "folder-0001", "delivery": {"files": files, "cents": 300}}
    call(broker, "drafting_job_record", pid=1, uid=0, job_id=jid, state="delivered", fields=fields)
    if proven:
        meta = {
            "lane": "drafting",
            "job_id": jid,
            "files": [
                {"file_id": "draft-0001", "size": 4100, "sha256": "a" * 64},
                {"file_id": "notes-0001", "size": 900, "sha256": "b" * 64},
            ],
        }
        writer.append(
            {
                "action_type": "DELIVERY_PROVEN",
                "actor": "workspace-broker",
                "actor_role": "broker",
                "metadata": json.dumps(meta),
            }
        )
    return jid


def _queued(queue: Path, jid: str) -> dict:
    return json.loads((queue / f"{jid}.json").read_text())


def test_a_reply_in_the_delivery_thread_revises_the_filed_brief_by_id(tmp_path) -> None:
    """FALSIFIER: drop _route and the reply queues a fresh draft of the whole file."""
    broker, _db, queue, writer = _seat(tmp_path, THREADS.__getitem__)
    prior = _deliver(broker, writer)
    out = call(broker, "drafting_job_submit", envelope=_envelope(request_ref=REPLY, request_text="Take out the cites."))
    assert out["accepted"] and out["mode"] == "revise" and out["revise_of"] == prior
    rev = _queued(queue, out["job_id"])["revise"]
    assert rev == {
        "prior_job_id": prior,
        "folder_id": "folder-0001",
        "files": [
            {
                "role": "draft",
                "file_id": "draft-0001",
                "name": "Mediation Brief - 900201.docx",
                "size": 4100,
                "sha256": "a" * 64,
            },
            {
                "role": "attorney_notes",
                "file_id": "notes-0001",
                "name": "Mediation Brief - 900201 - attorney notes.docx",
                "size": 900,
                "sha256": "b" * 64,
            },
        ],
    }


def test_a_request_in_another_thread_is_a_fresh_draft(tmp_path) -> None:
    broker, _db, queue, writer = _seat(tmp_path, THREADS.__getitem__)
    _deliver(broker, writer)
    out = call(broker, "drafting_job_submit", envelope=_envelope(request_ref=ELSEWHERE))
    assert out["accepted"] and out["mode"] == "draft" and "revise" not in _queued(queue, out["job_id"])


def test_a_thread_the_mailbox_cannot_read_queues_nothing(tmp_path) -> None:
    """FALSIFIER: treat 'could not look' as 'not a reply' and the $56 fresh draft runs."""

    def broken(ref: str) -> str:
        raise ThreadUnknown("graph timed out")

    broker, _db, _queue, writer = _seat(tmp_path, THREADS.__getitem__)
    _deliver(broker, writer)
    broker.drafting.thread_of = broken
    out = call(broker, "drafting_job_submit", envelope=_envelope(request_ref=REPLY))
    assert out["accepted"] is False and "could not be matched" in out["reason"]


def test_a_seat_with_no_mailbox_thread_drafts_as_before(tmp_path) -> None:
    broker, _db, _queue, writer = _seat(tmp_path, None)
    _deliver(broker, writer)
    assert call(broker, "drafting_job_submit", envelope=_envelope(request_ref=REPLY))["mode"] == "draft"


def test_a_delivery_without_proven_file_ids_is_never_revised_on_a_guess(tmp_path) -> None:
    broker, _db, _queue, writer = _seat(tmp_path, THREADS.__getitem__)
    _deliver(broker, writer, proven=False)
    out = call(broker, "drafting_job_submit", envelope=_envelope(request_ref=REPLY))
    assert out["accepted"] is False and "by file id" in out["reason"]


def test_a_spent_allowance_never_blocks_a_revision(tmp_path) -> None:
    broker, _db, _queue, writer = _seat(tmp_path, THREADS.__getitem__, allowance=1)
    _deliver(broker, writer)
    assert call(broker, "drafting_job_submit", envelope=_envelope(request_ref=REPLY))["mode"] == "revise"
    fresh = call(broker, "drafting_job_submit", envelope=_envelope(request_ref=ELSEWHERE))
    assert fresh["accepted"] is False and "allowance is spent" in fresh["reason"]


def test_revise_of_is_root_only_and_names_a_delivered_draft(tmp_path) -> None:
    broker, _db, _queue, writer = _seat(tmp_path, None)
    prior = _deliver(broker, writer)
    agent = call(broker, "drafting_job_submit", envelope=_envelope(request_ref=REPLY, revise_of=prior))
    assert agent["accepted"] is False and "root only" in agent["reason"]
    other = call(
        broker,
        "drafting_job_submit",
        pid=1,
        uid=0,
        envelope=_envelope(request_ref=REPLY, revise_of="01JZZZZZZZZZZZZZZZZZZZZZZZ"),
    )
    assert other["accepted"] is False and "not a delivered draft" in other["reason"]
    ok = call(broker, "drafting_job_submit", pid=1, uid=0, envelope=_envelope(request_ref=REPLY, revise_of=prior))
    assert ok["mode"] == "revise"


def test_an_old_ledger_gains_the_revise_column(tmp_path) -> None:
    db = str(tmp_path / "audit.db")
    LedgerWriter(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE drafting_jobs (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
        "state TEXT NOT NULL, matter_id TEXT NOT NULL, matter_number TEXT NOT NULL, file_to_matter_id TEXT NOT NULL, "
        "document_class TEXT NOT NULL, requester TEXT NOT NULL, request_ref TEXT NOT NULL, envelope_digest TEXT NOT NULL, "
        "cents INTEGER NOT NULL DEFAULT 0, reason TEXT, folder_id TEXT, delivery_json TEXT, reply_sent_at TEXT, "
        "attempt INTEGER NOT NULL DEFAULT 1, reply_key TEXT)"
    )
    conn.commit()
    conn.close()
    DraftingLedger(db, tmp_path / "q")
    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(drafting_jobs)")}
    conn.close()
    assert "revise_of" in cols
    assert ADMIN and MATTER  # the shared fixtures this file builds on
