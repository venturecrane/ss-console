"""A1 (2026-11-23): a response delivery that names a set's draft an earlier Operator
job delivered (the runner's ``served.PRIOR`` row, with that job's id). A change
sent in reply to the delivery must reach that draft too, by file id, from the job
that filed it. Every name and number is invented.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from workspace_broker import delivered_matter

from test_drafting_revise_route import ELSEWHERE, FIRST, REPLY, RESPONSE, THREADS, _deliver_set, _seat, _queued
from test_drafting_verbs import _envelope, call

PRIOR_NAME = "Responses to Special Interrogatories, Set One.docx"


@pytest.mark.parametrize(
    "ask",
    [
        "Please amend the responses to remove the objection to No. 4.",
        "Can you supplement the responses with the treatment dates?",
        "Please add further answers to 3 and 5.",
    ],
)
def test_an_amend_or_supplement_ask_in_a_deliverys_thread_is_still_a_revision(tmp_path, ask) -> None:
    """Review 11-23: a change to the delivered responses, worded as amend or supplement, revises them."""
    broker, _db, queue, writer = _seat(tmp_path, THREADS.__getitem__)
    prior = _deliver_set(broker, writer, FIRST, [0, 1, 2])
    env = _envelope(request_ref=REPLY, document_class=RESPONSE, request_text=ask)
    out = call(broker, "drafting_job_submit", envelope=env)
    assert out["accepted"] and out["mode"] == "revise" and out["revise_of"] == prior
    assert _queued(queue, out["job_id"])["revise"]["units"]


def _deliver_naming_prior(broker, writer, ref: str, prior_job: str, prior_name: str = PRIOR_NAME) -> str:
    """A delivered response job that filed the production set (d-9) and named the special set's prior draft."""
    out = call(broker, "drafting_job_submit", envelope=_envelope(request_ref=ref, document_class=RESPONSE))
    jid = out["job_id"]
    call(broker, "drafting_job_record", pid=1, uid=0, job_id=jid, state="running", fields={})
    name = "Responses to Requests for Production, Set One.docx"
    rows = [
        {"set": "Responses to Requests for Production, Set One", "drafted_by": "operator", "status": "filed",
         "document": name},
        {"set": "Responses to Special Interrogatories, Set One", "drafted_by": "operator", "status": "filed",
         "document": prior_name, "prior_draft": True, "prior_job": prior_job, "told": True},
    ]  # fmt: skip
    delivery = {"files": [{"name": name, "size": 1002, "role": "draft"}], "cents": 300, "served_sets": rows}
    fields = {"cents": 300, "folder_id": "folder-0002", "delivery": delivery}
    call(broker, "drafting_job_record", pid=1, uid=0, job_id=jid, state="delivered", fields=fields)
    meta = {"lane": "drafting", "job_id": jid, "files": [{"file_id": "d-9", "size": 1002, "sha256": "9" * 64}]}
    writer.append({"action_type": "DELIVERY_PROVEN", "actor": "workspace-broker", "actor_role": "broker",
                   "metadata": json.dumps(meta)})  # fmt: skip
    return jid


def test_a_change_in_reply_reaches_the_named_prior_draft_filed_by_another_job(tmp_path) -> None:
    """FALSIFIER: drop _prior_holders and only d-9 is revised; the attorney's change to the
    special set, which the reply said would be made, is lost."""
    broker, _db, queue, writer = _seat(tmp_path, THREADS.__getitem__)
    older = _deliver_set(broker, writer, ELSEWHERE, [1, 2])  # filed the special set (d-1) and a production (d-2)
    named = _deliver_naming_prior(broker, writer, FIRST, older)
    out = call(broker, "drafting_job_submit", envelope=_envelope(request_ref=REPLY, document_class=RESPONSE))
    assert out["accepted"] and out["revise_of"] == named
    rev = _queued(queue, out["job_id"])["revise"]
    got = {u["file_id"]: u["prior_job_id"] for u in rev["units"]}
    assert got == {"d-9": named, "d-1": older}  # the older job's other file (d-2) is not this request's
    assert rev["prior_job_id"] == named and rev["folder_id"] == "folder-0002"


def test_a_named_prior_draft_its_job_did_not_file_refuses_for_smd(tmp_path) -> None:
    """Never revise silently around it: refused, SMD owes it."""
    broker, _db, _queue, writer = _seat(tmp_path, THREADS.__getitem__)
    older = _deliver_set(broker, writer, ELSEWHERE, [2])
    _deliver_naming_prior(broker, writer, FIRST, older)
    out = call(broker, "drafting_job_submit", envelope=_envelope(request_ref=REPLY, document_class=RESPONSE))
    assert out["accepted"] is False and out["code"] == "prior_unidentified"


def test_the_reply_promises_a_revision_only_for_an_operator_delivered_draft() -> None:
    row = {"set": "Responses to Special Interrogatories, Set One", "drafted_by": "operator", "status": "filed",
           "document": "X Draft", "prior_draft": True, "prior_job": "01JOB", "told": True}  # fmt: skip
    got = delivered_matter._served_sets({"served_sets": [row]})
    assert 'Set One, "X Draft": a draft of these responses is already on the matter' in got and "?" not in got
    bare = delivered_matter._served_sets({"served_sets": [{**row, "prior_job": None}]})
    assert "changes sent in reply" not in bare
