"""An email about drafting documents ALREADY FILED on the matter (2026-11-24).

An attorney replied in the thread of propounded special interrogatories SMD
had filed by hand (the Operator's own job for them had failed): "the
interrogatories need follow-up questions". His words went only into a
different draft still running, and nothing revised the sets he was reading;
another reply in that thread earlier had started a fresh redraft that pulled
502 files. These tests pin what the BROKER decides. Each fails if the line it
defends is removed. Names, matters and ids are invented.
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
from workspace_broker.drafting_verbs import DraftingVerbs
from workspace_broker.server import Broker

from test_drafting_verbs import AGENT_UID, GATEWAY_PID, _envelope, _yaml, call

SETS = "discovery_set"
ASKED = "<asked-sets@mail.firm.example>"
COMMENT = "<follow-up@mail.firm.example>"
OTHER = "<other-thread@mail.firm.example>"
LATER = "<later-comment@mail.firm.example>"
THREADS = {ASKED: "conv-sets", COMMENT: "conv-sets", LATER: "conv-sets", OTHER: "conv-other"}


def _seat(tmp_path, thread_of=THREADS.__getitem__):
    db = str(tmp_path / "audit.db")
    writer = LedgerWriter(db)
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(_yaml())
    queue = tmp_path / "drafting-queue"
    broker = Broker.__new__(Broker)
    broker.gateway_pid, broker.agent_uid, broker.ledger = GATEWAY_PID, AGENT_UID, writer
    broker.drafting = DraftingVerbs(
        DraftingLedger(db, queue), customer_yaml=str(yaml_path), audit_append=writer.append, thread_of=thread_of
    )
    return broker, db, queue, writer


def _sets(ref: str, text: str = "Draft special interrogatories to each defendant.") -> dict:
    return _envelope(request_ref=ref, document_class=SETS, request_text=text)


def _failed(broker, ref=ASKED) -> str:
    jid = call(broker, "drafting_job_submit", envelope=_sets(ref))["job_id"]
    call(broker, "drafting_job_record", pid=1, uid=0, job_id=jid, state="running", fields={})
    call(broker, "drafting_job_record", pid=1, uid=0, job_id=jid, state="failed", fields={"cents": 0, "reason": "x"})
    return jid


def _delivered(broker, writer, ref=ASKED, drafts=5) -> str:
    jid = call(broker, "drafting_job_submit", envelope=_sets(ref))["job_id"]
    call(broker, "drafting_job_record", pid=1, uid=0, job_id=jid, state="running", fields={})
    files = [
        {"name": f"Special Interrogatories Set {i}.docx", "size": 2000 + i, "role": "draft"} for i in range(drafts)
    ]
    fields = {"cents": 300, "folder_id": "folder-sets", "delivery": {"files": files, "cents": 300}}
    call(broker, "drafting_job_record", pid=1, uid=0, job_id=jid, state="delivered", fields=fields)
    meta = {
        "lane": "drafting",
        "job_id": jid,
        "files": [{"file_id": f"s-{i}", "size": 2000 + i, "sha256": "c" * 64} for i in range(drafts)],
    }
    row = {"action_type": "DELIVERY_PROVEN", "actor": "workspace-broker", "actor_role": "broker"}
    writer.append({**row, "metadata": json.dumps(meta)})
    return jid


def _owed(db: str) -> list[tuple[str, str]]:
    conn = sqlite3.connect(db)
    try:
        return [(r[0], r[1]) for r in conn.execute("SELECT refusal_code, detail FROM smd_owed")]
    finally:
        conn.close()


def test_a_comment_in_the_thread_of_sets_smd_filed_by_hand_never_redrafts_them(tmp_path) -> None:
    """FALSIFIER: drop the failed-job match in drafting_route.route and this
    comment queues a fresh draft that pulls the whole file again (11:25, 502 files)."""
    broker, db, queue, _writer = _seat(tmp_path)
    failed = _failed(broker)
    out = call(broker, "drafting_job_submit", envelope=_sets(COMMENT, "The interrogatories need follow-ups."))
    assert out["accepted"] is False and out["code"] == "prior_filed_by_smd"
    assert out["smd_told"] is True  # recorded as work SMD owes, so the overlay says it owns it
    assert not [p for p in queue.glob("*.json") if p.name != f"{failed}.json"]
    ((code, detail),) = _owed(db)
    assert code == "prior_filed_by_smd" and failed in detail


def test_a_request_in_another_thread_still_drafts(tmp_path) -> None:
    broker, _db, _queue, _writer = _seat(tmp_path)
    _failed(broker)
    out = call(broker, "drafting_job_submit", envelope=_sets(OTHER))
    assert out["accepted"] and out["mode"] == "draft"


def test_the_failed_jobs_own_email_asked_again_still_drafts(tmp_path) -> None:
    """A failed job does not hold its email (the ledger's unique index): the
    same request read again is that request, not a comment on filed documents."""
    broker, _db, _queue, _writer = _seat(tmp_path)
    _failed(broker)
    out = call(broker, "drafting_job_submit", envelope=_sets(ASKED))
    assert out["accepted"] and out["mode"] == "draft"


def test_a_comment_on_several_delivered_sets_is_smds_never_a_redraft(tmp_path) -> None:
    """FALSIFIER: let a propounded delivery of several drafts fall through to
    ``return None`` and the comment redrafts all five sets from the file."""
    broker, _db, _queue, writer = _seat(tmp_path)
    _delivered(broker, writer, drafts=5)
    out = call(broker, "drafting_job_submit", envelope=_sets(COMMENT))
    assert out["accepted"] is False and out["code"] == "prior_several_drafts" and out["smd_told"] is True


def test_the_hold_names_the_draft_still_running_on_the_matter(tmp_path) -> None:
    """The overlay carries the comment to it (``underway_job_id``).
    FALSIFIER: drop ``also`` from the hand refusal and it reaches nothing."""
    broker, _db, _queue, _writer = _seat(tmp_path)
    _failed(broker)
    running = call(broker, "drafting_job_submit", envelope=_sets(OTHER))["job_id"]
    out = call(broker, "drafting_job_submit", envelope=_sets(COMMENT))
    assert out["code"] == "prior_filed_by_smd" and out["underway_job_id"] == running


def test_a_revision_is_queued_beside_a_fresh_draft_of_the_class_and_names_it(tmp_path) -> None:
    """FALSIFIER: keep the twin check blind to ``revise`` and the comment is
    refused ``underway``: it lands only in the running draft, and the filed
    document he was reading is never changed."""
    broker, _db, queue, writer = _seat(tmp_path)
    prior = _delivered(broker, writer, drafts=1)
    running = call(broker, "drafting_job_submit", envelope=_sets(OTHER))["job_id"]
    out = call(broker, "drafting_job_submit", envelope=_sets(COMMENT, "Add follow-up questions."))
    assert out["accepted"] and out["mode"] == "revise" and out["revise_of"] == prior
    assert out["underway_job_id"] == running
    assert json.loads((queue / f"{out['job_id']}.json").read_text())["revise"]["prior_job_id"] == prior


def test_a_second_revision_in_flight_still_holds_the_next(tmp_path) -> None:
    broker, _db, _queue, writer = _seat(tmp_path)
    _delivered(broker, writer, drafts=1)
    assert call(broker, "drafting_job_submit", envelope=_sets(COMMENT))["mode"] == "revise"
    again = call(broker, "drafting_job_submit", envelope=_sets(LATER))
    assert again["accepted"] is False and again["code"] == "underway"


def _add(broker, job_id, ref, thread, **over):
    req = {"job_id": job_id, "request_ref": ref, "requested_by": "admin@firm.example", "thread_refs": thread}
    return call(broker, "drafting_job_add_to_request", **{**req, "words": "Add follow-ups.", **over})


def test_about_filed_reaches_a_running_draft_from_another_thread_only_through_a_filed_job(tmp_path) -> None:
    """The overlay's carry after a hold: the email's thread holds the filed (failed,
    hand-filed) job; the running draft is from another thread. FALSIFIER: drop the
    ``about_filed`` branch and it is refused ``other_thread``; drop ``filed_job``
    and any thread reaches any running draft."""
    broker, _db, _queue, _writer = _seat(tmp_path)
    broker.drafting.add_wait_seconds = 0.2  # no daemon here: past the checks it is not_confirmed
    _failed(broker)
    running = call(broker, "drafting_job_submit", envelope=_sets(OTHER))["job_id"]
    got = _add(broker, running, COMMENT, [ASKED, COMMENT], about_filed=True)
    assert got["result"] == "not_confirmed"  # every check passed; it asked the runner
    assert _add(broker, running, COMMENT, [ASKED, COMMENT])["result"] == "other_thread"
    stray = _add(broker, running, "<stray@mail.firm.example>", ["<stray@mail.firm.example>"], about_filed=True)
    assert stray["result"] == "other_thread"
    someone = _add(broker, running, COMMENT, [ASKED, COMMENT], about_filed=True, requested_by="b@firm.example")
    assert someone["result"] == "other_thread"


def test_an_addition_in_a_thread_that_holds_filed_documents_says_so(tmp_path) -> None:
    """The model added the comment to a running draft of the thread (the 11:25
    redraft): the answer says filed documents of its class are in the thread
    too, so the overlay also submits it. FALSIFIER: drop the ``filed_in_thread``
    flag and the filed sets are never revised (12:05)."""
    broker, _db, _queue, _writer = _seat(tmp_path)
    broker.drafting.add_wait_seconds = 0.2
    _failed(broker)
    redraft = call(broker, "drafting_job_submit", envelope=_sets(OTHER))["job_id"]
    got = _add(broker, redraft, COMMENT, [ASKED, OTHER, COMMENT])
    assert got["filed_in_thread"] is True and got["matter_number"] == "900201" and got["matter_id"]
    alone = _add(broker, redraft, COMMENT, [OTHER, COMMENT])
    assert "filed_in_thread" not in alone
