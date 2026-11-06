"""Every reply SMD sends for a job is recorded as that job's reply, whichever
route sent it (2026-11-06).

THE DEFECT (A&P). A drafting job's completion reply went out as an SMD hand
release (overlay ``shared.matter_file_review``): a plain ``msgraph_reply``,
outside the bound-reply claim. The job's ``reply_sent_at`` stayed empty and
``bound_replies`` held no claim for its key, so a ``jobs rewake`` would have
sent the code-written completion a SECOND time.

Two rules, each with a falsifier:

* a plain reply that names ``for_job`` takes the job's claim and ledger mark
  AT the POST, exactly as the bound reply does;
* a job's bind refuses (``thread_answered``) when its thread already holds a
  reply this mailbox sent after the job finished, and ``delivery_reply_status``
  says so when the runner asks (``thread``).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_reply_binding import (
    AGENT_UID,
    GATEWAY_PID,
    GRAPH_ID,
    FakeMailbox,
    _ack_in_thread,
    _broker,
    _call,
    _drafting_job,
    _iso,
)
from workspace_broker import bound_replies, reply_text
from workspace_broker.reply_binding import BindingRefused

HAND_WORDS = "The brief is filed in the matter. We will follow up with anything else you need."
OTHER_ID = "AAMkOTHERTHREAD00002="


class TwoThreadBox(FakeMailbox):
    """The fake mailbox, plus one received email in ANOTHER conversation."""

    def __call__(self, request, timeout=None):
        if request.method == "GET" and f"/messages/{OTHER_ID}" in request.full_url:
            self.calls.append((request.method, request.full_url))
            other = {**self.message, "id": OTHER_ID, "conversationId": "CONV-2", "internetMessageId": "<o@x.example>"}
            from test_reply_binding import _Response

            return _Response(json.dumps(other))
        return super().__call__(request, timeout)


def _box(cls: type[FakeMailbox] = FakeMailbox) -> FakeMailbox:
    box = cls()
    _ack_in_thread(box)
    return box


def _hand(broker, job: str, message_id: str = GRAPH_ID) -> dict:
    return broker.handle(
        {
            "action": "msgraph_reply",
            "payload": {"message_id": message_id, "comment": HAND_WORDS},
            "anchor": {"kind": "graph_message", "graph_message_id": message_id},
            "for_job": {"kind": "drafting_job", "job_id": job},
            "session_id": "smd-review",
        },
        peer_pid=GATEWAY_PID,
        peer_uid=AGENT_UID,
    )


def _ledger_reply(tmp_path: Path, job: str) -> tuple[str | None, str | None]:
    conn = sqlite3.connect(tmp_path / "audit.db")
    try:
        row = conn.execute("SELECT reply_sent_at, reply_key FROM drafting_jobs WHERE id=?", (job,)).fetchone()
    finally:
        conn.close()
    return row[0], row[1]


def test_a_hand_reply_for_a_job_is_recorded_as_its_reply(tmp_path: Path) -> None:
    """FALSIFIER: on origin/main ``for_job`` is ignored, the hand reply goes
    out plain, no claim is taken, and the job still binds for a second reply."""
    job = _drafting_job(tmp_path, ["running", "delivered"])
    box = _box()
    out = _hand(_broker(tmp_path, box), job)
    assert out["ok"] is True and len(box.replies()) == 1
    key = f"drafting_job:{job}:1:delivered"
    assert bound_replies.outcome(str(tmp_path / "audit.db"), key) == "sent"
    sent_at, reply_key = _ledger_reply(tmp_path, job)
    assert sent_at and reply_key == "1:delivered"
    again = _call(_broker(tmp_path, box), "msgraph_reply_bind", {"kind": "drafting_job", "job_id": job})
    assert again["bound"] is False and again["code"] == "already_replied"
    (text,) = reply_text.texts_for(_broker(tmp_path, box), "drafting_job", job) or [""]
    with pytest.raises(BindingRefused):
        _call(
            _broker(tmp_path, box),
            "msgraph_reply_bound",
            {"kind": "drafting_job", "job_id": job},
            payload={"comment": text},
            session_id="s",
        )
    assert len(box.replies()) == 1


def test_a_second_hand_reply_for_the_same_job_is_refused(tmp_path: Path) -> None:
    job = _drafting_job(tmp_path, ["running", "delivered"])
    box = _box()
    _hand(_broker(tmp_path, box), job)
    with pytest.raises(BindingRefused):
        _hand(_broker(tmp_path, box), job)
    assert len(box.replies()) == 1


def test_a_hand_reply_outside_the_jobs_thread_cannot_claim_it(tmp_path: Path) -> None:
    job = _drafting_job(tmp_path, ["running", "delivered"])
    box = _box(TwoThreadBox)
    with pytest.raises(BindingRefused, match="not in drafting job"):
        _hand(_broker(tmp_path, box), job, OTHER_ID)
    assert box.replies() == []
    assert not bound_replies.claimed(str(tmp_path / "audit.db"), f"drafting_job:{job}:1:delivered")


def test_a_hand_reply_for_an_unfinished_job_is_refused(tmp_path: Path) -> None:
    job = _drafting_job(tmp_path, ["running"])
    box = _box()
    with pytest.raises(BindingRefused, match="has not ended"):
        _hand(_broker(tmp_path, box), job)
    assert box.replies() == []


def _unrecorded_reply_after_finish(box: FakeMailbox) -> None:
    """A reply that left this mailbox in the thread after the job finished,
    outside every claim (the pre-fix hand release)."""
    box.sent.append(
        {
            "id": "HAND",
            "conversationId": "CONV-1",
            "sentDateTime": _iso(datetime.now(timezone.utc) + timedelta(seconds=2)),
        }
    )


def test_a_job_whose_thread_was_answered_after_it_finished_does_not_bind(tmp_path: Path) -> None:
    """FALSIFIER: on origin/main the bind is ``bound`` and the code-written
    completion goes out as a second email."""
    job = _drafting_job(tmp_path, ["running", "delivered"])
    box = _box()
    _unrecorded_reply_after_finish(box)
    out = _call(_broker(tmp_path, box), "msgraph_reply_bind", {"kind": "drafting_job", "job_id": job})
    assert out["bound"] is False and out["code"] == "thread_answered"
    assert "after the job finished" in out["reason"]
    (text,) = reply_text.texts_for(_broker(tmp_path, box), "drafting_job", job) or [""]
    with pytest.raises(BindingRefused, match="after the job finished"):
        _call(
            _broker(tmp_path, box),
            "msgraph_reply_bound",
            {"kind": "drafting_job", "job_id": job},
            payload={"comment": text},
            session_id="s",
        )
    assert box.replies() == []


def test_a_reply_before_the_job_finished_does_not_block_its_completion(tmp_path: Path) -> None:
    """The "queued" acknowledgement is in the thread before the job ends; the
    completion still binds (the rule is AFTER the finish, not "any reply")."""
    job = _drafting_job(tmp_path, ["running", "delivered"])
    out = _call(_broker(tmp_path, _box()), "msgraph_reply_bind", {"kind": "drafting_job", "job_id": job})
    assert out["bound"] is True


def _status(broker, job: str) -> dict:
    return broker.handle(
        {"action": "delivery_reply_status", "kind": "drafting_job", "job_id": job, "thread": True},
        peer_pid=GATEWAY_PID,
        peer_uid=0,
    )


def test_reply_status_tells_the_runner_the_thread_is_answered(tmp_path: Path) -> None:
    job = _drafting_job(tmp_path, ["running", "delivered"])
    box = _box()
    assert _status(_broker(tmp_path, box), job)["thread"] == {"checked": True, "answered": ""}
    _unrecorded_reply_after_finish(box)
    thread = _status(_broker(tmp_path, box), job)["thread"]
    assert thread["checked"] is True and "after the job finished" in thread["answered"]


def test_reply_status_after_a_recorded_hand_reply_says_already_replied(tmp_path: Path) -> None:
    job = _drafting_job(tmp_path, ["running", "delivered"])
    box = _box()
    _hand(_broker(tmp_path, box), job)
    thread = _status(_broker(tmp_path, box), job)["thread"]
    assert thread["checked"] is True and "already had its reply" in thread["answered"]


def test_reply_status_without_thread_does_not_read_the_mailbox(tmp_path: Path) -> None:
    """The reply watch asks every tick; only an explicit ``thread`` reads Graph."""
    job = _drafting_job(tmp_path, ["running", "delivered"])
    box = _box()
    out = _broker(tmp_path, box).handle(
        {"action": "delivery_reply_status", "kind": "drafting_job", "job_id": job}, peer_pid=GATEWAY_PID, peer_uid=0
    )
    assert "thread" not in out
    assert [c for c in box.calls if "/token" not in c[1]] == []
