"""A drafting job that held on our side, failed, or waited on its lane's proof
run gets ONE note in the broker's words, and the broker can send it itself
(2026-11-06, the answer key).

THE GAP. A held drafting job whose hold was ours read "Nothing is needed from
you." with no word that anyone was looking; a failed one (``audit_unrepaired``,
``no_verdict``, a runner killed past its retries) was refused its reply, so the
attorney heard nothing; a job waiting on its lane's proof run had no turn at all.

THE RULES, each with the mutation that breaks it:

1. Each stopped state renders its one note from the row
   (``fixed_wording.STOPPED`` + ``STOP_CLASS``). Mutation: drop the drafting
   classifier and every one of these renders the old words, or nothing.
2. The bind lets that state bind, and the send refuses any other words.
   Mutation: make ``stop_note.owed`` return False in ``reply_binding._verify_job`` and
   a failed or waiting job is refused its bind again.
3. Once per ``attempt:state``: a second send for the same key is
   ``already_sent`` and nothing goes twice. Mutation: skip the claim check in
   ``_verify_job``'s stopped branch and the second call sends again.
4. ``job_note_send`` is root only and answers the state, the class, the key and
   the reason the daemon pages SMD with.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_reply_binding import AGENT_UID, GATEWAY_PID, FakeMailbox, _ack_in_thread, _broker, _call, _drafting_job
from workspace_broker import fixed_wording, reply_text
from workspace_broker.drafting_ledger import DraftingLedger
from workspace_broker.reply_binding import BindingRefused

ROOT_PID = 4242

HELD = (
    "The document for matter 900201 is not ready yet. We are checking it before it is filed. Nothing is needed "
    "from you."
)
FAILED = (
    "The document for matter 900201 is not ready yet. The drafting stopped on our side, and we are looking into "
    "it. Nothing is needed from you."
)
WAITING = (
    "We have your request for the document for matter 900201. We are checking it before the drafting starts. "
    "Nothing is needed from you."
)


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


CASES = [
    ("held_ours", [("running", ""), ("held", "destination_mismatch: the matter id does not carry it")], "held", HELD),
    ("review_held", [("running", ""), ("failed", "review_held: 2 MUST-FIX findings")], "held", HELD),
    ("audit_unrepaired", [("running", ""), ("failed", "audit_unrepaired: 3 claims")], "failed", FAILED),
    (
        "no_verdict",
        [("running", ""), ("failed", "no_verdict: the runner exited -9 without a verdict")],
        "failed",
        FAILED,
    ),
    (
        "killed",
        [("running", ""), ("failed", "retried after runner killed by a signal at draft 4 time(s), failed again")],
        "failed",
        FAILED,
    ),
    ("lane_unproven", [("submitted", "lane_unproven: document-drafter.discovery_set")], "waiting", WAITING),
]


@pytest.mark.parametrize(("name", "path", "cls", "text"), CASES, ids=[c[0] for c in CASES])
def test_each_stopped_state_renders_its_one_note(tmp_path: Path, name: str, path, cls: str, text: str) -> None:
    """Rule 1. FALSIFIER: on origin/main a failed row renders [] and an ours-hold
    renders "... not drafted yet. Nothing is needed from you." with no follow-up."""
    job = _job(tmp_path, path)
    row = DraftingLedger(str(tmp_path / "audit.db"), tmp_path / "dq").read(job)
    assert fixed_wording.stopped_class("drafting_job", row) == cls
    assert fixed_wording.render("drafting_job", row) == [text]


def test_a_note_without_a_matter_number_still_says_nothing_it_cannot_fill() -> None:
    assert fixed_wording.render("drafting_job", {"state": "failed"}) == [fixed_wording.STOPPED["drafting"]["failed"][1]]
    assert "{" not in fixed_wording.STOPPED["drafting"]["failed"][1]


def test_a_firm_cause_hold_keeps_its_own_words() -> None:
    """A hold the requester can act on is not "we are checking it"."""
    row = {
        "state": "held",
        "matter_number": "900201",
        "reason": "request_incomplete: the request is missing what the draft needs: the deponent's name",
    }
    assert fixed_wording.stopped_class("drafting_job", row) is None
    assert "missing what the draft needs" in fixed_wording.render("drafting_job", row)[0]
    revise = {"state": "held", "matter_number": "900201", "revise_of": "01J00000000000000000000000", "reason": "x"}
    assert fixed_wording.stopped_class("drafting_job", revise) is None


def test_other_lanes_have_no_stopped_note_yet() -> None:
    """Shaped for the other lanes, built for drafting only: one entry each joins."""
    assert set(fixed_wording.STOPPED) == set(fixed_wording.STOP_CLASS) == {"drafting"}
    for kind in ("demand_job", "litigation_job", "medchron_job", "casefile_job"):
        assert fixed_wording.stopped_class(kind, {"state": "failed", "reason": "no_verdict: x"}) is None


def test_the_notes_are_plain_we_voice_with_no_date_reason_or_tool() -> None:
    for pair in fixed_wording.STOPPED["drafting"].values():
        for text in pair:
            low = text.lower()
            assert "\N{EM DASH}" not in text and "\N{EN DASH}" not in text, text
            for word in (" job", "pipeline", "model", "audit", "verdict", "runner", "broker", " i ", "lane", "proof"):
                assert word not in f" {low} ", (word, text)
            assert "we are" in low or "we have" in low, text
            assert not any(ch.isdigit() for ch in text.replace("{number}", "")), text


#: What a stopped note must never say (CLAUDE.md "No fabricated client-facing
#: content", Pattern A): a promised follow-up or next message, a time, or a name
#: other than "we" (the firm hears from us, never from a vendor name).
PROMISES = ("follow up", "follow-up", "get back", "reach out", "will ", "we'll", "next message", "shortly", "soon")
TIMES = ("today", "tomorrow", "tonight", "hour", "minute", "day", "week", "business", "monday", "friday", "by ")
NAMES = ("smd", "operator", "claude", "anthropic", "smokeball", "microsoft", "outlook", "scott")


def test_no_stopped_note_promises_a_follow_up_a_time_or_a_vendor_name() -> None:
    """FALSIFIER: put "and SMD will follow up with you" back and this fails three ways."""
    for pair in fixed_wording.STOPPED["drafting"].values():
        for text in pair:
            low = f" {text.lower()} "
            for phrase in (*PROMISES, *TIMES, *NAMES):
                assert phrase not in low, (phrase, text)


@pytest.mark.parametrize(("name", "path", "cls", "text"), CASES, ids=[c[0] for c in CASES])
def test_the_broker_sends_the_note_once_and_refuses_other_words(tmp_path: Path, name, path, cls, text) -> None:
    """Rules 2 and 3. FALSIFIER: on origin/main ``job_note_send`` does not exist,
    and a failed or waiting drafting job is refused its bind."""
    job = _job(tmp_path, path)
    box = _box()
    with pytest.raises(BindingRefused, match="fixed_wording"):
        _call(
            _broker(tmp_path, box),
            "msgraph_reply_bound",
            {"kind": "drafting_job", "job_id": job},
            payload={"comment": "Our audit stage failed; SMD will rerun it tomorrow."},
            session_id="s",
        )
    out = _note(_broker(tmp_path, box), job)
    assert out["outcome"] == "sent" and out["stop_class"] == cls
    assert out["state"] == path[-1][0] and out["job_reason"] == path[-1][1]
    assert out["key"].startswith(f"drafting_job:{job}:1:{path[-1][0]}")
    assert len(box.replies()) == 1
    assert box.bodies[0]["comment"] == text
    again = _note(_broker(tmp_path, box), job)
    assert again["outcome"] == "already_sent" and again["key"] == out["key"]
    assert len(box.replies()) == 1
    # The gateway's DELIVER turn then finds it answered, too.
    bind = _call(_broker(tmp_path, box), "msgraph_reply_bind", {"kind": "drafting_job", "job_id": job})
    assert bind["bound"] is False and bind["code"] == "already_replied"


def test_a_resumed_job_that_ends_again_is_owed_its_new_reply(tmp_path: Path) -> None:
    """One note per attempt:state, not one per job: a resumed job delivered
    later gets its delivery reply."""
    job = _job(tmp_path, [("running", ""), ("failed", "no_verdict: x")])
    box = _box()
    assert _note(_broker(tmp_path, box), job)["outcome"] == "sent"
    # The note went before the resume. The fake stamps every send at its fixed
    # NOW, which can fall after the ledger's real clock; put it where it was, or
    # the thread_answered guard (#3813) reads it as a reply after the finish.
    for sent in box.sent:
        sent["sentDateTime"] = "2000-01-01T00:00:00Z"
    ledger = DraftingLedger(str(tmp_path / "audit.db"), tmp_path / "dq")
    ledger.record(job, "running", {})
    ledger.record(job, "delivered", {})
    out = _note(_broker(tmp_path, box), job)
    assert out["outcome"] == "sent", out.get("why")
    assert out["stop_class"] == "" and len(box.replies()) == 2


def test_a_running_job_is_owed_nothing(tmp_path: Path) -> None:
    job = _job(tmp_path, [("running", "")])
    out = _note(_broker(tmp_path, _box()), job)
    assert out["outcome"] == "not_owed" and out["stop_class"] == ""


def test_job_note_send_is_root_only_and_drafting_only(tmp_path: Path) -> None:
    job = _job(tmp_path, [("running", ""), ("failed", "no_verdict: x")])
    box = _box()
    with pytest.raises(PermissionError, match="job_note_send is not permitted"):
        _note(_broker(tmp_path, box), job, uid=AGENT_UID, pid=GATEWAY_PID)
    assert box.replies() == []
    with pytest.raises(Exception, match="sends only"):
        from workspace_broker import job_notes

        job_notes.send_verb(_broker(tmp_path, box), "job_note_send", {"kind": "demand_job", "job_id": job}, 1, 0)
    assert "drafting_job" in reply_text.CODE_SENT_KINDS
