"""A litigation job that held, failed, or waited on its lane's proof run tells
SMD, never the firm (Captain, 2026-11-06; ``workspace_broker/stop_note.py``).

THE CALL. "No, not now. We are still refining and monitoring. we should know.
the firm should not." Drafting went silent that morning (#3828); litigation
follows. Before this, a held list was replied on in its hold class's fixed
wording (``test_held_wording``, now the reopened path).

THE RULES, each with the mutation that breaks it:

1. The gateway's bind refuses every stopped state with a "send nothing" code
   (failed ``job_failed``, held ``job_held``, waiting ``not_ended``), so the
   DELIVER turn has no binding to send on. Mutation: take ``litigation_job``
   out of ``stop_note.SILENT_KINDS`` and a held list binds to its held words.
2. A hold whose class was the firm's answer (which staff, whose files, an
   empty scope) is labelled ``held_firm_cause``, so SMD's page says the firm
   would have been asked.
3. A delivered list is unchanged: it binds, and its reply is the DELIVER
   turn's, never code-sent (``reply_text.CODE_SENT_KINDS`` stays drafting only).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_reply_binding import LIT_YAML, FakeMailbox, _ack_in_thread, _broker, _call, _litigation_job
from workspace_broker import reply_text, stop_note
from workspace_broker.litigation_ledger import LitigationLedger
from workspace_broker.reply_binding import BindingRefused

#: (name, the last ledger step (state, reason), the class SMD's page names, the bind's refusal code)
CASES = [
    ("held_gates", ("held", "gate_refused: 1 case held at the status gate"), "held", "job_held"),
    ("held_allowance", ("held", "list_allowance: 2 of 2 included lists are delivered"), "held", "job_held"),
    ("held_staff", ("held", "staff_unresolved: from ambiguous"), "held_firm_cause", "job_held"),
    ("held_requester", ("held", "requester_unresolved: no staff record"), "held_firm_cause", "job_held"),
    ("held_scope", ("held", "scope_empty: no open litigation matters"), "held_firm_cause", "job_held"),
    ("no_verdict", ("failed", "no_verdict: the runner exited -9 without a verdict"), "failed", "job_failed"),
    ("closed", ("failed", "closed: SMD closed it by hand"), "failed", "job_failed"),
]
IDS = [c[0] for c in CASES]


def _job(tmp_path: Path, last: tuple[str, str]) -> str:
    job = _litigation_job(tmp_path, ["running"])
    LitigationLedger(str(tmp_path / "audit.db"), tmp_path / "lq").record(job, last[0], {"reason": last[1]})
    return job


@pytest.mark.parametrize(("name", "last", "cls", "code"), CASES, ids=IDS)
def test_the_gateway_bind_refuses_every_stopped_state(tmp_path: Path, name, last, cls, code) -> None:
    """Rules 1 and 2. FALSIFIER: on the parent commit every held case binds
    (``bound`` True, with its hold class's wording) and a reply goes out."""
    job = _job(tmp_path, last)
    box = FakeMailbox()
    _ack_in_thread(box)
    out = _call(_broker(tmp_path, box, LIT_YAML), "msgraph_reply_bind", {"kind": "litigation_job", "job_id": job})
    assert out["bound"] is False and out["code"] == code, out
    assert "Send nothing to anyone" in out["reason"]
    with pytest.raises(BindingRefused):
        _call(
            _broker(tmp_path, box, LIT_YAML),
            "msgraph_reply_bound",
            {"kind": "litigation_job", "job_id": job},
            payload={"comment": "Nothing is needed from you."},
            session_id="s",
        )
    assert box.replies() == []
    row = LitigationLedger(str(tmp_path / "audit.db"), tmp_path / "lq").read(job)
    assert stop_note.stop_class("litigation_job", row) == cls


def test_a_waiting_list_is_refused_and_named_waiting(tmp_path: Path) -> None:
    row = {"state": "submitted", "reason": "lane_unproven: litigation-status.status"}
    assert stop_note.stop_class("litigation_job", row) == "waiting"
    assert not stop_note.held_silent("litigation_job", row)


def test_a_delivered_list_still_binds_and_is_not_code_sent(tmp_path: Path) -> None:
    """Rule 3: the delivered reply stays the DELIVER turn's, worded as today."""
    assert "litigation_job" not in reply_text.CODE_SENT_KINDS
    job = _litigation_job(tmp_path, ["running", "delivered"])
    box = FakeMailbox()
    _ack_in_thread(box)
    out = _call(_broker(tmp_path, box, LIT_YAML), "msgraph_reply_bind", {"kind": "litigation_job", "job_id": job})
    assert out["bound"] is True, out
    row = LitigationLedger(str(tmp_path / "audit.db"), tmp_path / "lq").read(job)
    assert stop_note.stop_class("litigation_job", row) is None


def test_job_note_send_still_refuses_litigation() -> None:
    """The broker's code-sent verb is drafting only: nothing new can send a
    litigation reply in code."""
    assert reply_text.CODE_SENT_KINDS == {"drafting_job"}
