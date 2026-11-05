"""The conflict fence (hermes-smd-trust/conflict_fence.py, shared/conflict_facts.py).

An email turn from a sender off the roster, on a seat that authors Smokeball,
writes and sends nothing until ``mcp_smokeball_conflict_check`` has returned
``clear`` for that sender this turn, and nothing at all after ``hit``. A webhook
turn and a rostered colleague's daily paths are never fenced.

All of that is what ``logic_in_code.conflict_fence: enforce`` does, so every test
down to the observe section runs on a seat that authors it. Absent, the switch is
``observe``: the same calls go through, each leaving one audit row.

The seat configs are the repo's own non-client seats: pilot-smokeball authors
the Smokeball connector, smd-staging does not.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from shared import conflict_facts
from shared.audit_contract import CREATE_TABLE_SQL, sender_key
from shared.inbound import (
    SESSION_INBOUND_ORIGIN,
    UNTRUSTED_EMAIL_DELIMITER,
    InboundOrigin,
    bind_origin_from_prompt,
)
from tests.conftest import load_plugin

CUSTOMERS = Path(__file__).resolve().parents[2] / "customers"
SMOKEBALL_SEAT = CUSTOMERS / "pilot-smokeball" / "customer.yaml"
NO_SMOKEBALL_SEAT = CUSTOMERS / "smd-staging" / "customer.yaml"

SESSION = "sess-conflict"
OUTSIDER = "vhale@example.com"
COLLEAGUE = "scott@smd.services"  # on both seats' inbound_allow_from
SUBJECT = "Re: settlement"

WRITE = "mcp_smokeball_create_task"
DRAFT = "email_create_draft"
SEND = "smd_send_message"
READ = "mcp_smokeball_get_matter"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    conflict_facts._reset_for_tests()
    seat = tmp_path / "enforcing-seat.yaml"
    seat.write_text(
        SMOKEBALL_SEAT.read_text(encoding="utf-8")
        + "\nlogic_in_code:\n  conflict_fence: enforce\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(seat))


@pytest.fixture
def trust():
    return load_plugin("hermes-smd-trust")


@pytest.fixture
def fence(trust):
    return trust.conflict_fence


def _email_turn(sender: str, message_id: str = "msg-1", subject: str = SUBJECT) -> None:
    SESSION_INBOUND_ORIGIN.record(
        "", InboundOrigin(sender_address=sender, message_id=message_id, subject=subject)
    )
    prompt = f"from: {sender}\nmessage_id: {message_id}\n{UNTRUSTED_EMAIL_DELIMITER}\nbody text"
    bind_origin_from_prompt(SESSION, prompt)


def _webhook_turn() -> None:
    bind_origin_from_prompt(SESSION, "A Smokeball webhook: matter.updated for one matter.")


def _result(verdict: str, task: str | None = None) -> str:
    return json.dumps(
        {
            "result": json.dumps(
                {"verdict": verdict, "matters": [], "clearance_task_id": task, "searched": ["x"]}
            )
        }
    )


def _ran_check(
    fence, verdict: str, *, sender: str = OUTSIDER, subject: str = SUBJECT, task: str | None = None
):
    fence.record(
        conflict_facts.TOOL,
        SESSION,
        {"sender_name": "Victor Hale", "sender_email": sender, "subject_line": subject},
        _result(verdict, task),
    )


# ---- an outsider's email ----------------------------------------------------


@pytest.mark.parametrize("tool", [WRITE, DRAFT, SEND])
def test_a_write_a_draft_and_a_send_are_refused_with_no_check_this_turn(fence, tool: str) -> None:
    _email_turn(OUTSIDER)
    block = fence.check(tool, SESSION)
    assert block == {"action": "block", "message": fence.RUN_FIRST}


def test_reads_and_the_check_itself_are_never_fenced(fence) -> None:
    _email_turn(OUTSIDER)
    assert fence.check(READ, SESSION) is None
    assert fence.check(conflict_facts.TOOL, SESSION) is None


def test_a_clear_for_this_sender_opens_the_turn(fence) -> None:
    _email_turn(OUTSIDER)
    _ran_check(fence, "clear")
    assert fence.check(WRITE, SESSION) is None
    assert fence.check(DRAFT, SESSION) is None


@pytest.mark.parametrize("tool", [WRITE, DRAFT, SEND])
def test_a_hit_holds_everything_for_the_rest_of_the_turn(fence, tool: str) -> None:
    _email_turn(OUTSIDER)
    _ran_check(fence, "hit", task="task-1")
    _ran_check(fence, "clear")  # asking again does not clear a conflict
    assert fence.check(tool, SESSION) == {"action": "block", "message": fence.HELD}


@pytest.mark.parametrize("verdict", ["search_failed", "something-else"])
def test_a_failed_or_unknown_result_is_not_clear(fence, verdict: str) -> None:
    _email_turn(OUTSIDER)
    _ran_check(fence, verdict)
    assert fence.check(WRITE, SESSION)["message"] == fence.RUN_FIRST


def test_a_clear_for_a_different_sender_or_subject_opens_nothing(fence) -> None:
    _email_turn(OUTSIDER)
    _ran_check(fence, "clear", sender="someone-else@example.com")
    assert fence.check(WRITE, SESSION)["message"] == fence.RUN_FIRST
    _ran_check(fence, "clear", subject="A different message")
    assert fence.check(WRITE, SESSION)["message"] == fence.RUN_FIRST


def test_a_clear_for_one_email_does_not_open_the_next(fence) -> None:
    _email_turn(OUTSIDER, "msg-1")
    _ran_check(fence, "clear")
    _email_turn(OUTSIDER, "msg-2")
    assert fence.check(WRITE, SESSION)["message"] == fence.RUN_FIRST


def test_an_email_turn_whose_sender_is_unknown_is_fenced(fence) -> None:
    bind_origin_from_prompt(
        SESSION, f"message_id: never-recorded\n{UNTRUSTED_EMAIL_DELIMITER}\nbody"
    )
    assert fence.check(WRITE, SESSION)["message"] == fence.RUN_FIRST


# ---- never fenced -------------------------------------------------------------


def test_a_webhook_turn_is_never_fenced(fence) -> None:
    _webhook_turn()
    assert fence.check(WRITE, SESSION) is None
    assert fence.check(DRAFT, SESSION) is None


@pytest.mark.parametrize(
    "tool",
    [
        # the roster-forwarded daily paths: the post bundle, a vendor invoice, served papers
        "mcp_smokeball_file_attachment_pages_to_matter",
        "mcp_smokeball_record_post_batch",
        "mcp_smokeball_stage_vendor_invoice",
        "mcp_smokeball_add_medicals_row",
        WRITE,
        DRAFT,
    ],
)
def test_a_colleague_on_the_roster_is_never_fenced_on_the_daily_paths(fence, tool: str) -> None:
    _email_turn(COLLEAGUE)
    assert fence.check(tool, SESSION) is None


def test_a_seat_without_smokeball_is_never_fenced(fence, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(NO_SMOKEBALL_SEAT))
    _email_turn(OUTSIDER)
    assert fence.check(WRITE, SESSION) is None


def test_an_unreadable_seat_config_fences(
    fence, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    broken = tmp_path / "broken.yaml"
    broken.write_text("connectors: [", encoding="utf-8")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(broken))
    _email_turn(OUTSIDER)
    assert fence.check(WRITE, SESSION)["message"] == fence.RUN_FIRST


def test_a_fault_in_the_fence_refuses_when_enforcing(
    trust, fence, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(trust, "_paused_hard", lambda: False)
    monkeypatch.setattr("shared.gateway_identity.wall_block", lambda: None)
    _email_turn(OUTSIDER)

    def boom(_session_id: str) -> None:
        raise RuntimeError("the fence broke")

    monkeypatch.setattr(fence, "fenced_turn", boom)
    blocked = trust.on_pre_tool_call(
        tool_name=WRITE, args={}, session_id=SESSION, customer_slug="t"
    )
    assert blocked is not None and blocked["action"] == "block"
    assert "RuntimeError" in blocked["message"]


# ---- through the hook ---------------------------------------------------------


def test_the_trust_hook_refuses_and_then_records_and_opens(
    trust, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(trust, "_paused_hard", lambda: False)
    monkeypatch.setattr("shared.gateway_identity.wall_block", lambda: None)
    _email_turn(OUTSIDER)
    blocked = trust.on_pre_tool_call(
        tool_name=WRITE, args={"subject": "x"}, session_id=SESSION, customer_slug="t"
    )
    assert blocked == {"action": "block", "message": trust.conflict_fence.RUN_FIRST}
    trust.on_post_tool_call(
        tool_name=conflict_facts.TOOL,
        args={"sender_name": "Victor Hale", "sender_email": OUTSIDER, "subject_line": SUBJECT},
        result=_result("hit", "task-9"),
        session_id=SESSION,
    )
    held = trust.on_pre_tool_call(
        tool_name=DRAFT, args={"text": "x"}, session_id=SESSION, customer_slug="t"
    )
    assert held == {"action": "block", "message": trust.conflict_fence.HELD}
    assert conflict_facts.verdict_for(SESSION, "msg-1").clearance_task_id == "task-9"


# ---- observe: the switch absent ------------------------------------------------


class _Ledger:
    """The audit client shape over the real audit_log table."""

    def __init__(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.execute(CREATE_TABLE_SQL)

    def execute(self, sql: str, *params: object) -> None:
        self.db.execute(sql, params)

    def rows(self) -> list[tuple[str | None, dict]]:
        # The query the PR body tells the lead to run on a seat.
        found = self.db.execute(
            "SELECT ts, matter_ref, metadata FROM audit_log "
            "WHERE action_type = 'CONFLICT_FENCE_WOULD_REFUSE' ORDER BY ts"
        ).fetchall()
        return [(matter, json.loads(metadata)) for _ts, matter, metadata in found]


@pytest.fixture
def ledger(fence, monkeypatch: pytest.MonkeyPatch) -> _Ledger:
    held = _Ledger()
    monkeypatch.setattr(fence.outbound, "_audit_client", lambda: (held, "t"))
    return held


@pytest.fixture
def observing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A seat that authors no logic_in_code block, as every seat does at merge."""
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(SMOKEBALL_SEAT))


@pytest.mark.parametrize("tool", [WRITE, DRAFT, SEND])
def test_observing_lets_the_call_through_and_records_what_it_would_refuse(
    fence, ledger: _Ledger, observing: None, tool: str
) -> None:
    _email_turn(OUTSIDER)
    assert fence.check(tool, SESSION, {"matter_id": "m-1"}, "call-1") is None
    [(matter, row)] = ledger.rows()
    assert matter == "m-1"
    assert row == {
        "tool": tool,
        "reason": "no_check_this_turn",
        "basis": "sender_off_roster",
        "vendor_message_id": "msg-1",
        "sender_key": sender_key(OUTSIDER),
        "session_id": SESSION,
        "tool_call_id": "call-1",
    }


def test_observing_records_a_hit_and_keeps_the_verdict(
    fence, ledger: _Ledger, observing: None
) -> None:
    _email_turn(OUTSIDER)
    _ran_check(fence, "hit", task="task-1")
    _ran_check(fence, "clear")  # still sticky: the verdict recording is the same in both modes
    assert fence.check(WRITE, SESSION) is None
    assert conflict_facts.verdict_for(SESSION, "msg-1").verdict == conflict_facts.HIT
    [(_, row)] = ledger.rows()
    assert (row["reason"], row["clearance_task_id"]) == ("hit", "task-1")


@pytest.mark.parametrize(
    ("verdict", "reason"), [("search_failed", "search_failed"), ("not-a-verdict", "unreadable")]
)
def test_observing_records_a_verdict_that_was_not_clear(
    fence, ledger: _Ledger, observing: None, verdict: str, reason: str
) -> None:
    _email_turn(OUTSIDER)
    _ran_check(fence, verdict)
    assert fence.check(WRITE, SESSION) is None
    assert ledger.rows()[0][1]["reason"] == reason


def test_observing_records_a_clear_for_someone_else(
    fence, ledger: _Ledger, observing: None
) -> None:
    _email_turn(OUTSIDER)
    _ran_check(fence, "clear", sender="someone-else@example.com")
    assert fence.check(WRITE, SESSION) is None
    assert ledger.rows()[0][1]["reason"] == "mismatch"


def test_observing_a_clear_writes_no_row(fence, ledger: _Ledger, observing: None) -> None:
    _email_turn(OUTSIDER)
    _ran_check(fence, "clear")
    assert fence.check(WRITE, SESSION) is None
    assert ledger.rows() == []


@pytest.mark.parametrize("tool", [READ, conflict_facts.TOOL])
def test_observing_a_read_writes_no_row(fence, ledger: _Ledger, observing: None, tool: str) -> None:
    _email_turn(OUTSIDER)
    assert fence.check(tool, SESSION) is None
    assert ledger.rows() == []


def test_observing_never_records_a_colleague_or_a_webhook(
    fence, ledger: _Ledger, observing: None
) -> None:
    _email_turn(COLLEAGUE)
    assert fence.check(WRITE, SESSION) is None
    _webhook_turn()
    assert fence.check(WRITE, SESSION) is None
    assert ledger.rows() == []


def test_observing_the_fail_closed_sender_only_records(
    fence, ledger: _Ledger, observing: None
) -> None:
    bind_origin_from_prompt(
        SESSION, f"message_id: never-recorded\n{UNTRUSTED_EMAIL_DELIMITER}\nbody"
    )
    assert fence.check(WRITE, SESSION) is None
    [(_, row)] = ledger.rows()
    assert row["basis"] == "sender_not_in_verified_index"
    assert "sender_key" not in row


def test_a_seat_whose_config_has_gone_only_records(
    fence, ledger: _Ledger, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A file that is not there reads as observe (shared/logic_in_code.py), so even a
    seat that authored enforce records rather than refuses while the file is gone."""
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(tmp_path / "missing.yaml"))
    _email_turn(OUTSIDER)
    assert fence.check(WRITE, SESSION) is None
    assert ledger.rows()[0][1]["basis"] == "roster_unreadable"


def test_observing_the_fail_closed_roster_only_records(
    fence, ledger: _Ledger, observing: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fence, "_on_roster", lambda _address: None)
    _email_turn(OUTSIDER)
    assert fence.check(WRITE, SESSION) is None
    assert ledger.rows()[0][1]["basis"] == "roster_unreadable"


def test_observing_a_row_never_carries_an_arguments_text(
    fence, ledger: _Ledger, observing: None
) -> None:
    _email_turn(OUTSIDER)
    fence.check(WRITE, SESSION, {"matter_id": "m-1", "subject": "PRIVILEGED WORDS"})
    assert "PRIVILEGED WORDS" not in json.dumps(ledger.rows())
    assert OUTSIDER not in json.dumps(ledger.rows())


def test_observing_a_row_that_cannot_be_written_never_stops_the_call(
    fence, observing: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Down:
        def execute(self, *_args: object) -> None:
            raise OSError("broker down")

    monkeypatch.setattr(fence.outbound, "_audit_client", lambda: (Down(), "t"))
    _email_turn(OUTSIDER)
    assert fence.check(WRITE, SESSION) is None


def test_observing_a_fault_in_the_fence_records_and_never_stops_the_call(
    fence, ledger: _Ledger, observing: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_session_id: str) -> None:
        raise RuntimeError("the fence broke")

    monkeypatch.setattr(fence, "fenced_turn", boom)
    _email_turn(OUTSIDER)
    assert fence.check(WRITE, SESSION) is None
    assert ledger.rows()[0][1]["reason"] == "fence_fault"


def test_the_switch_is_read_fresh_so_an_edit_takes_the_next_call(
    fence, ledger: _Ledger, observing: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _email_turn(OUTSIDER)
    assert fence.check(WRITE, SESSION) is None
    seat = tmp_path / "now-enforcing.yaml"
    seat.write_text(
        SMOKEBALL_SEAT.read_text(encoding="utf-8")
        + "\nlogic_in_code:\n  conflict_fence: enforce\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(seat))
    assert fence.check(WRITE, SESSION)["message"] == fence.RUN_FIRST
    assert len(ledger.rows()) == 1


def test_the_trust_hook_passes_an_observed_call_on_and_threads_the_call_id(
    trust, ledger: _Ledger, observing: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(trust, "_paused_hard", lambda: False)
    monkeypatch.setattr("shared.gateway_identity.wall_block", lambda: None)
    monkeypatch.setattr(trust.enforce, "evaluate_tool_call", lambda *_a, **_k: None)
    _email_turn(OUTSIDER)
    blocked = trust.on_pre_tool_call(
        tool_name=WRITE,
        args={"matter_id": "m-1"},
        session_id=SESSION,
        tool_call_id="call-7",
        customer_slug="t",
    )
    assert blocked is None
    assert ledger.rows()[0][1]["tool_call_id"] == "call-7"
