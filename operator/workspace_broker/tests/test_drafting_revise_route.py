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


def test_verbs_built_before_the_mailbox_still_route_a_revision(tmp_path, monkeypatch) -> None:
    """The broker builds DraftingVerbs (server.py) BEFORE it sets ``msgraph``.
    A lookup captured at build time was None for the broker's whole life: no
    request on the A&P seat was routed as a revision from 11-05 to 11-21.
    FALSIFIER: capture ``graph_thread_of(broker.msgraph)`` in ``build`` again
    and this reply queues a fresh draft."""
    from workspace_broker import msgraph_lookup

    broker, db, queue, writer = _seat(tmp_path, None)
    broker.customer_path = tmp_path / "customer.yaml"
    broker.msgraph = None  # the server's order: verbs first, mailbox after
    broker.drafting = DraftingVerbs.build(broker, audit_db_path=db, queue_dir=str(queue))
    broker.msgraph = object()  # the mailbox connects after the verbs exist
    monkeypatch.setattr(msgraph_lookup, "find_received", lambda _ops, ref: {"conversation_id": THREADS[ref]})
    prior = _deliver(broker, writer)
    out = call(broker, "drafting_job_submit", envelope=_envelope(request_ref=REPLY, request_text="Apply the rules."))
    assert out["accepted"] and out["mode"] == "revise" and out["revise_of"] == prior


SETS = ("Form Interrogatories", "Special Interrogatories", "Requests for Production")
KINDS = ("form_interrogatories", "special_interrogatories", "requests_for_production")
RESPONSE = "discovery_response"


def _deliver_set(broker, writer, ref, ids, *, sha="0", klass=RESPONSE, proven=True, served=True) -> str:
    """A delivered response job filing one document per id (``ids`` index ``SETS``)."""
    out = call(broker, "drafting_job_submit", envelope=_envelope(request_ref=ref, document_class=klass))
    jid = out["job_id"]
    call(broker, "drafting_job_record", pid=1, uid=0, job_id=jid, state="running", fields={})
    names = [f"Responses to {SETS[i]}, Set One.docx" for i in ids]
    files = [{"name": n, "size": 1000 + i, "role": "draft"} for n, i in zip(names, ids)]
    rows = [{"set": f"Responses to {SETS[i]}, Set One", "status": "filed", "document": n} for n, i in zip(names, ids)]
    delivery = {"files": files, "cents": 300, **({"served_sets": rows} if served else {})}
    fields = {"cents": 300, "folder_id": "folder-0001", "delivery": delivery}
    call(broker, "drafting_job_record", pid=1, uid=0, job_id=jid, state="delivered", fields=fields)
    if proven:
        meta = {
            "lane": "drafting",
            "job_id": jid,
            "files": [{"file_id": f"d-{i}", "size": 1000 + i, "sha256": sha * 64} for i in ids],
        }
        row = {"action_type": "DELIVERY_PROVEN", "actor": "workspace-broker", "actor_role": "broker"}
        writer.append({**row, "metadata": json.dumps(meta)})
    return jid


def test_a_delivery_of_several_drafts_is_revised_in_every_document(tmp_path) -> None:
    """2026-11-21: a reply asking to change a three-document discovery delivery.
    FALSIFIER: keep the one-draft guard for responses and it queues a fresh
    draft; take one job's first file and two documents go unrevised."""
    broker, _db, queue, writer = _seat(tmp_path, THREADS.__getitem__)
    prior = _deliver_set(broker, writer, FIRST, [0, 1, 2])
    reply = _envelope(request_ref=REPLY, document_class=RESPONSE, request_text="Apply all new rules to them.")
    out = call(broker, "drafting_job_submit", envelope=reply)
    assert out["accepted"] and out["mode"] == "revise" and out["revise_of"] == prior
    rev = _queued(queue, out["job_id"])["revise"]
    assert set(rev) == {"prior_job_id", "folder_id", "units"} and rev["prior_job_id"] == prior
    assert [u["file_id"] for u in rev["units"]] == ["d-0", "d-1", "d-2"]
    assert [u["set_type"] for u in rev["units"]] == list(KINDS)
    assert all(u["prior_job_id"] == prior and u["folder_id"] == "folder-0001" for u in rev["units"])


def test_the_threads_attorney_notes_ride_with_the_revision_and_another_threads_never(tmp_path) -> None:
    """The runner files a new version of the delivery's notes (2026-11-23).
    FALSIFIER: drop notes_from and the revision's notes start from nothing."""
    broker, _db, queue, writer = _seat(tmp_path, THREADS.__getitem__)
    out = call(broker, "drafting_job_submit", envelope=_envelope(request_ref=FIRST, document_class=RESPONSE))
    jid = out["job_id"]
    call(broker, "drafting_job_record", pid=1, uid=0, job_id=jid, state="running", fields={})
    files = [
        {"name": "Responses to Form Interrogatories, Set One.docx", "size": 1000, "role": "draft"},
        {"name": "Attorney Notes.docx", "size": 500, "role": "attorney_notes"},
    ]
    fields = {"cents": 300, "folder_id": "folder-0001", "delivery": {"files": files, "cents": 300}}
    call(broker, "drafting_job_record", pid=1, uid=0, job_id=jid, state="delivered", fields=fields)
    meta = {"lane": "drafting", "job_id": jid, "files": [{"file_id": "d-0", "size": 1000, "sha256": "0" * 64},
                                                         {"file_id": "n-0", "size": 500, "sha256": "0" * 64}]}  # fmt: skip
    writer.append({"action_type": "DELIVERY_PROVEN", "actor": "workspace-broker", "actor_role": "broker",
                   "metadata": json.dumps(meta)})  # fmt: skip
    reply = _envelope(request_ref=REPLY, document_class=RESPONSE)
    rev = _queued(queue, call(broker, "drafting_job_submit", envelope=reply)["job_id"])["revise"]
    assert rev["notes"]["file_id"] == "n-0" and [u["file_id"] for u in rev["units"]] == ["d-0"]


def test_a_file_refiled_by_a_later_job_is_one_unit_from_the_newest_proof(tmp_path) -> None:
    """On the seat 7 jobs map to 8 file ids: a later job re-filed into the same
    ids. FALSIFIER: keep the oldest proof (or both) and the stale bytes win, or
    one file is revised twice."""
    broker, _db, queue, writer = _seat(tmp_path, THREADS.__getitem__)
    first = _deliver_set(broker, writer, FIRST, [0, 1], sha="1")
    again = _deliver_set(broker, writer, REPLY, [1, 2], sha="2")
    reply = _envelope(request_ref="<third@mail.firm.example>", document_class=RESPONSE)
    broker.drafting.thread_of = {**THREADS, "<third@mail.firm.example>": "conv-1"}.__getitem__
    rev = _queued(queue, call(broker, "drafting_job_submit", envelope=reply)["job_id"])["revise"]
    got = {u["file_id"]: (u["prior_job_id"], u["sha256"][0]) for u in rev["units"]}
    assert got == {"d-1": (again, "2"), "d-2": (again, "2"), "d-0": (first, "1")}
    assert rev["prior_job_id"] == again


def test_a_job_in_another_thread_is_not_part_of_the_set(tmp_path) -> None:
    broker, _db, queue, writer = _seat(tmp_path, THREADS.__getitem__)
    _deliver_set(broker, writer, FIRST, [0])
    _deliver_set(broker, writer, ELSEWHERE, [2])
    out = call(broker, "drafting_job_submit", envelope=_envelope(request_ref=REPLY, document_class=RESPONSE))
    assert [u["file_id"] for u in _queued(queue, out["job_id"])["revise"]["units"]] == ["d-0"]


def test_one_unidentifiable_job_in_the_thread_refuses_the_whole_set(tmp_path) -> None:
    """FALSIFIER: skip the job with no proof and its document goes unrevised, silently."""
    broker, _db, _queue, writer = _seat(tmp_path, THREADS.__getitem__)
    broker.drafting.thread_of = {**THREADS, FIRST.replace("first", "second"): "conv-1"}.__getitem__
    _deliver_set(broker, writer, FIRST, [0])
    _deliver_set(broker, writer, FIRST.replace("first", "second"), [1], proven=False)
    out = call(broker, "drafting_job_submit", envelope=_envelope(request_ref=REPLY, document_class=RESPONSE))
    assert out["accepted"] is False and out["code"] == "prior_unidentified"


def test_the_set_type_comes_from_the_served_row_then_the_name() -> None:
    from workspace_broker.drafting_revise_units import set_type_of

    rows = [{"set": "Responses to Special Interrogatories, Set One", "document": "Responses A"}]
    assert set_type_of("Responses A.docx", rows) == "special_interrogatories"
    assert set_type_of("Responses to Requests for Admission.docx", []) == "requests_for_admission"
    assert (
        set_type_of("Responses to Interrogatories Set One.docx", []) == ""
    )  # neither says: the file's headings decide


def test_a_brief_delivered_in_several_drafts_still_drafts_afresh(tmp_path) -> None:
    """Only a discovery response is revised whole; another class keeps the one-draft guard."""
    broker, _db, queue, writer = _seat(tmp_path, THREADS.__getitem__)
    _deliver_set(broker, writer, FIRST, [0, 1, 2], klass="mediation_brief")
    out = call(broker, "drafting_job_submit", envelope=_envelope(request_ref=REPLY))
    assert out["accepted"] and out["mode"] == "draft" and "revise" not in _queued(queue, out["job_id"])


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
