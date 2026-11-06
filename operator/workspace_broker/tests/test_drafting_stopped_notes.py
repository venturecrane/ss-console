"""A drafting job that held, failed, or waited on its lane's proof run tells
SMD, never the firm (Captain, 2026-11-06; ``workspace_broker/stop_note.py``).

THE CALL. #3816 sent the attorney a fixed "we are checking it" note for these
states. The same day the Captain reversed it: while the lane is still being
refined and watched, SMD should know and the firm should not. Only a delivered
drafting job is replied on.

THE RULES, each with the mutation that breaks it:

1. The gateway's bind refuses every stopped state with a "send nothing" code
   (failed ``job_failed``, held ``job_held``, waiting ``not_ended``), so the
   DELIVER turn has no binding to send on. Mutation: drop the
   ``stop_note.held_silent`` branch in ``reply_binding._verify_job`` and a held
   job binds to its old held words.
2. ``job_note_send`` sends nothing for a stopped job and answers ``not_owed``
   with the state, the class, the key and the reason SMD's page names.
   Mutation: drop its early ``stop_class`` return and an older broker path
   could reach the send.
3. A delivered job is unchanged: one reply, once, in the broker's words.
4. There is no stopped wording left to send (``fixed_wording`` renders none).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_reply_binding import AGENT_UID, GATEWAY_PID, FakeMailbox, _ack_in_thread, _broker, _call, _drafting_job
from workspace_broker import fixed_wording, reply_text, stop_note
from workspace_broker.drafting_ledger import DraftingLedger
from workspace_broker.reply_binding import BindingRefused

ROOT_PID = 4242


def _box() -> FakeMailbox:
    box = FakeMailbox()
    _ack_in_thread(box)
    return box


def _job(tmp_path: Path, path: list[tuple[str, str]]) -> str:
    """A drafting job moved through ``(state, reason)`` steps."""
    job = _drafting_job(tmp_path, [])
    ledger = DraftingLedger(str(tmp_path / "audit.db"), tmp_path / "dq")
    for state, reason in path:
        ledger.record(job, state, {"reason": reason} if reason else {})
    return job


def _note(broker, job: str, uid: int = 0, pid: int = ROOT_PID) -> dict:
    return broker.handle({"action": "job_note_send", "kind": "drafting_job", "job_id": job}, peer_pid=pid, peer_uid=uid)


#: (name, ledger path, the class SMD's page names, the bind's refusal code)
CASES = [
    (
        "held_ours",
        [("running", ""), ("held", "destination_mismatch: the matter id does not carry it")],
        "held",
        "job_held",
    ),
    (
        "held_firm_cause",
        [("running", ""), ("held", "request_incomplete: the request is missing what the draft needs: a name")],
        "held_firm_cause",
        "job_held",
    ),
    ("review_held", [("running", ""), ("failed", "review_held: 2 MUST-FIX findings")], "review_held", "job_failed"),
    ("audit_unrepaired", [("running", ""), ("failed", "audit_unrepaired: 3 claims")], "failed", "job_failed"),
    ("no_verdict", [("running", ""), ("failed", "no_verdict: the runner exited -9")], "failed", "job_failed"),
    (
        "killed",
        [("running", ""), ("failed", "retried after runner killed by a signal at draft 4 time(s), failed again")],
        "failed",
        "job_failed",
    ),
    ("lane_unproven", [("submitted", "lane_unproven: document-drafter.discovery_set")], "waiting", "not_ended"),
]
IDS = [c[0] for c in CASES]


@pytest.mark.parametrize(("name", "path", "cls", "code"), CASES, ids=IDS)
def test_the_gateway_bind_refuses_every_stopped_state(tmp_path: Path, name, path, cls, code) -> None:
    """Rule 1. FALSIFIER: on the parent commit every one of these binds
    (``bound`` True, with a "not ready yet" note or the old held words)."""
    job = _job(tmp_path, path)
    box = _box()
    out = _call(_broker(tmp_path, box), "msgraph_reply_bind", {"kind": "drafting_job", "job_id": job})
    assert out["bound"] is False and out["code"] == code, out
    if code != "not_ended":
        assert "Send nothing to anyone" in out["reason"]
    with pytest.raises(BindingRefused):
        _call(
            _broker(tmp_path, box),
            "msgraph_reply_bound",
            {"kind": "drafting_job", "job_id": job},
            payload={"comment": "We are checking it. Nothing is needed from you."},
            session_id="s",
        )
    assert box.replies() == []


@pytest.mark.parametrize(("name", "path", "cls", "code"), CASES, ids=IDS)
def test_job_note_send_sends_nothing_for_a_stopped_job(tmp_path: Path, name, path, cls, code) -> None:
    """Rule 2. FALSIFIER: on the parent commit the outcome is ``sent`` (or the
    old held reply goes out) and one reply reaches the requester."""
    job = _job(tmp_path, path)
    box = _box()
    for _ in range(2):
        out = _note(_broker(tmp_path, box), job)
        assert out["outcome"] == "not_owed" and out["stop_class"] == cls, out
        assert out["state"] == path[-1][0] and out["job_reason"] == path[-1][1]
        assert out["key"] == f"drafting_job:{job}:1:{path[-1][0]}"
    assert box.replies() == []


def test_a_revision_hold_is_silent_too(tmp_path: Path) -> None:
    row = {"state": "held", "revise_of": "01J00000000000000000000000", "reason": "x"}
    assert stop_note.stop_class("drafting_job", row) == "held_revision"
    assert stop_note.held_silent("drafting_job", row)


def test_a_delivered_job_still_gets_its_one_reply(tmp_path: Path) -> None:
    """Rule 3: a lost wake's delivered reply is sent by the broker, once."""
    job = _job(tmp_path, [("running", ""), ("delivered", "")])
    box = _box()
    out = _note(_broker(tmp_path, box), job)
    assert out["outcome"] == "sent" and out["stop_class"] == "", out.get("why")
    assert len(box.replies()) == 1
    assert _note(_broker(tmp_path, box), job)["outcome"] == "already_sent"
    assert len(box.replies()) == 1


def test_a_resumed_job_that_delivers_is_owed_its_reply(tmp_path: Path) -> None:
    job = _job(tmp_path, [("running", ""), ("failed", "no_verdict: x")])
    box = _box()
    assert _note(_broker(tmp_path, box), job)["outcome"] == "not_owed"
    ledger = DraftingLedger(str(tmp_path / "audit.db"), tmp_path / "dq")
    ledger.record(job, "running", {})
    ledger.record(job, "delivered", {})
    out = _note(_broker(tmp_path, box), job)
    assert out["outcome"] == "sent", out.get("why")
    assert len(box.replies()) == 1


def test_no_stopped_wording_is_left_to_send() -> None:
    """Rule 4: the broker renders nothing for a failed or waiting drafting job,
    and no "not ready yet" note survives anywhere in the wording tables."""
    assert fixed_wording.render("drafting_job", {"state": "failed", "matter_number": "900201"}) == []
    assert fixed_wording.render("drafting_job", {"state": "submitted", "reason": "lane_unproven: x"}) == []
    assert not hasattr(fixed_wording, "STOPPED") and not hasattr(fixed_wording, "stopped_class")
    assert not any("not ready yet" in t for _lane, _key, t in fixed_wording.delivered_templates())


def test_other_lanes_are_untouched() -> None:
    assert stop_note.SILENT_KINDS == {"drafting_job"} <= reply_text.CODE_SENT_KINDS
    for kind in ("demand_job", "litigation_job", "medchron_job", "casefile_job"):
        assert stop_note.stop_class(kind, {"state": "held", "reason": "x"}) is None


def test_job_note_send_is_root_only_and_drafting_only(tmp_path: Path) -> None:
    job = _job(tmp_path, [("running", ""), ("delivered", "")])
    box = _box()
    with pytest.raises(PermissionError, match="job_note_send is not permitted"):
        _note(_broker(tmp_path, box), job, uid=AGENT_UID, pid=GATEWAY_PID)
    assert box.replies() == []
    with pytest.raises(Exception, match="sends only"):
        from workspace_broker import job_notes

        job_notes.send_verb(_broker(tmp_path, box), "job_note_send", {"kind": "demand_job", "job_id": job}, 1, 0)
