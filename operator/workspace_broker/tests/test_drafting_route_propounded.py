"""A reply on filed PROPOUNDED sets revises them in place (2026-11-26).

On 11-24 an attorney sent three replies in the thread of the propounded sets
filed on his matter: define a term in each set, add follow-up questions, and
cut the special interrogatories "to the most important 120125" (then "Sorry, I
meant 120-125"). Each was held for SMD (``prior_several_drafts``) because the
revise runner edited one filed draft. The propounded runner now revises every
filed set (``revise/propounded_run.py``); these tests pin that the broker sends
each reply there with the delivered job and every filed set by file id. Names,
matters, threads and ids are invented.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_drafting_filed_by_smd import _failed, _sets, _seat
from test_drafting_verbs import call

ASKED = "<asked-sets@mail.firm.example>"
DEFINE = "<define-term@mail.firm.example>"
FOLLOW = "<follow-ups@mail.firm.example>"
CUT = "<cut-sets@mail.firm.example>"
SORRY = "<cut-sorry@mail.firm.example>"
OTHER = "<other-thread@mail.firm.example>"
THREADS = {r: "conv-sets" for r in (ASKED, DEFINE, FOLLOW, CUT, SORRY)} | {OTHER: "conv-other"}
NAMES = (
    "Special Interrogatories to Example Corp, Set One.docx",
    "Requests for Admission to Example Corp, Set One.docx",
    "Requests for Production to Example Corp, Set One.docx",
)
#: The attorney's three replies, the parties and terms invented.
REPLIES = (
    (DEFINE, "Please define ACCOMODATION(S) in each set."),
    (FOLLOW, "The interrogatories need follow up questions so the answers are useful."),
    (CUT, "Cut the special interrogatories to the most important 120125."),
)


def _record(broker, writer, jid: str, names=NAMES, ids=None) -> None:
    """Job ``jid`` delivered with ``names`` filed, proven by id (the lane's own order: proof, then record)."""
    ids = ids or [f"file-set-{i:04d}" for i in range(len(names))]
    files = [{"name": n, "size": 3000 + i, "role": "draft"} for i, n in enumerate(names)]
    meta = {
        "lane": "drafting",
        "job_id": jid,
        "files": [{"file_id": fid, "size": 3000 + i, "sha256": "e" * 64} for i, fid in enumerate(ids)],
    }
    writer.append(
        {
            "action_type": "DELIVERY_PROVEN",
            "actor": "workspace-broker",
            "actor_role": "broker",
            "metadata": json.dumps(meta),
        }
    )
    fields = {"cents": 300, "folder_id": "folder-sets-0001", "delivery": {"files": files, "cents": 300}}
    call(broker, "drafting_job_record", pid=1, uid=0, job_id=jid, state="delivered", fields=fields)


def _delivered(broker, writer, ref=ASKED, names=NAMES) -> str:
    jid = call(broker, "drafting_job_submit", envelope=_sets(ref))["job_id"]
    call(broker, "drafting_job_record", pid=1, uid=0, job_id=jid, state="running", fields={})
    _record(broker, writer, jid, names)
    return jid


def _queued(queue: Path, job_id: str) -> dict:
    return json.loads((queue / f"{job_id}.json").read_text())


@pytest.mark.parametrize("ref,text", REPLIES)
def test_each_reply_on_the_filed_sets_reaches_the_propounded_runner_with_every_set(tmp_path, ref, text) -> None:
    """FALSIFIER: keep ``discovery_set`` out of ``SET_CLASSES`` and each reply is
    held for SMD (``prior_several_drafts``), as all three were on 11-24."""
    broker, _db, queue, writer = _seat(tmp_path, THREADS.__getitem__)
    prior = _delivered(broker, writer)
    out = call(broker, "drafting_job_submit", envelope=_sets(ref, text))
    assert out["accepted"] and out["mode"] == "revise" and out["revise_of"] == prior
    job = _queued(queue, out["job_id"])
    assert job["document_class"] == "discovery_set"
    revise = job["revise"]
    assert revise["prior_job_id"] == prior and revise["folder_id"] == "folder-sets-0001"
    assert [u["file_id"] for u in revise["units"]] == ["file-set-0000", "file-set-0001", "file-set-0002"]
    assert [u["set_type"] for u in revise["units"]] == [
        "special_interrogatories",
        "requests_for_admission",
        "requests_for_production",
    ]
    assert all(u["prior_job_id"] == prior and u["sha256"] == "e" * 64 for u in revise["units"])


def test_the_second_email_while_the_cut_is_being_made_holds(tmp_path) -> None:
    """An unfinished revision in the thread holds the next email ("Sorry, I meant
    120-125"): it is carried to the running job, never a second revision of the
    same files. FALSIFIER: drop the in-flight check and two runs write one file."""
    broker, _db, _queue, writer = _seat(tmp_path, THREADS.__getitem__)
    _delivered(broker, writer)
    first = call(broker, "drafting_job_submit", envelope=_sets(CUT, REPLIES[2][1]))
    assert first["mode"] == "revise"
    again = call(broker, "drafting_job_submit", envelope=_sets(SORRY, "Sorry, I meant 120-125."))
    assert again["accepted"] is False and again["code"] == "underway"


def test_the_next_reply_revises_the_newest_filing_of_each_set(tmp_path) -> None:
    """Reply 1 delivered as new versions of the same files; reply 2 revises them
    as the newest job filed them. FALSIFIER: read only the first delivery, and
    reply 2 works from bytes the files no longer hold."""
    broker, _db, queue, writer = _seat(tmp_path, THREADS.__getitem__)
    _delivered(broker, writer)
    first = call(broker, "drafting_job_submit", envelope=_sets(*REPLIES[0]))["job_id"]
    call(broker, "drafting_job_record", pid=1, uid=0, job_id=first, state="running", fields={})
    _record(broker, writer, first)
    out = call(broker, "drafting_job_submit", envelope=_sets(*REPLIES[1]))
    revise = _queued(queue, out["job_id"])["revise"]
    assert out["mode"] == "revise" and revise["prior_job_id"] == first
    assert {u["prior_job_id"] for u in revise["units"]} == {first} and len(revise["units"]) == 3


def test_sets_smd_recorded_as_delivered_by_hand_are_revised_in_place(tmp_path) -> None:
    """The 11-24 sets were SMD's hand filing of a failed job. Once recorded
    (``jobs.drafting.deliver_by_smd``: its proof, then the row delivered), a reply
    revises them. FALSIFIER: route only the Operator's own deliveries, and the
    hand-filed sets stay SMD's forever."""
    broker, _db, queue, writer = _seat(tmp_path, THREADS.__getitem__)
    failed = _failed(broker, ASKED)
    held = call(broker, "drafting_job_submit", envelope=_sets(*REPLIES[1]))
    assert held["code"] == "prior_filed_by_smd"  # not recorded yet: SMD's
    call(broker, "drafting_job_record", pid=1, uid=0, job_id=failed, state="running", fields={})
    _record(broker, writer, failed)
    out = call(broker, "drafting_job_submit", envelope=_sets(FOLLOW, "Add the follow-ups, please."))
    assert out["mode"] == "revise" and out["revise_of"] == failed
    assert len(_queued(queue, out["job_id"])["revise"]["units"]) == 3


def test_more_sets_than_one_revision_takes_stay_smds(tmp_path) -> None:
    """FALSIFIER: drop the unit ceiling and the runner refuses the job after it is
    queued (its ``job.MAX_UNITS``), with the attorney told nothing true."""
    broker, _db, _queue, writer = _seat(tmp_path, THREADS.__getitem__)
    _delivered(broker, writer, names=tuple(f"Special Interrogatories, Set {i}.docx" for i in range(21)))
    out = call(broker, "drafting_job_submit", envelope=_sets(*REPLIES[0]))
    assert out["accepted"] is False and out["code"] == "prior_several_drafts" and out["smd_told"] is True


def test_a_request_in_another_thread_still_drafts(tmp_path) -> None:
    broker, _db, _queue, writer = _seat(tmp_path, THREADS.__getitem__)
    _delivered(broker, writer)
    out = call(broker, "drafting_job_submit", envelope=_sets(OTHER, "Draft special interrogatories to Example LLC."))
    assert out["accepted"] and out["mode"] == "draft"


def test_a_cut_to_a_number_is_a_change_never_a_new_party() -> None:
    """FALSIFIER: read "to the most important 120125" as a party, and the cut
    drafts every set afresh from the file."""
    from workspace_broker.drafting_new_ask import asks_new

    prior = [{"request_words": "Draft special interrogatories to Example Corp.", "delivery_json": None}]
    assert asks_new(REPLIES[2][1], prior) is False
    assert asks_new("Please also prepare interrogatories to Example LLC.", prior) is True
