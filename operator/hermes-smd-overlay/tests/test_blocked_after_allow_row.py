"""A call the ceiling allows and a later wall gate blocks is recorded as
refused (known defect ``blocked-after-allow-decision``, wave 4 corpus).

The wall recorded the ceiling's allow for the call, then the matter gate, the
multi-matter fence, the structure floor, the content floor, the voice gate or
the spec gate returned a block, and nothing recorded again. The defect entry
said a refused call "never reaches post_tool_call"; the pinned Hermes says
otherwise. ``handle_function_call`` (model_tools.py, the sha in
``operator/templates/Dockerfile``) answers a plugin block with
``_emit(result, status="blocked", error_type="plugin_block", ...)``, which
fires ``post_tool_call``; the audit plugin's ``on_post_tool_call`` writes a
``TOOL_CALL_COMPLETED`` row for it and takes the register's decision onto it.
So the row of a refused send said ``trust_decision=allow``,
``trust_allowed=true``, ``trust_reason=external_send permitted``.

These drive the real hooks in the order the dispatcher fires them: the trust
plugin's ``on_pre_tool_call`` (which blocks), then the audit plugin's
``on_post_tool_call`` with the blocked envelope Hermes passes, and read the row.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from shared import heartbeat, matter_binding
from shared.trust_decision import TRUST_DECISIONS
from tests.conftest import load_plugin
from tests.test_audit_emit import FakeD1Client
from tests.test_audit_shortfall import _ledger_from
from tests.test_matter_gate_placement import AUTONOMOUS_YAML

trust = load_plugin("hermes-smd-trust")
audit = load_plugin("hermes-smd-audit")
enforce = trust.enforce

SID = "s-allowfix"
SEND = "mcp_agentmail_send_message"
M_A = "aaaaaaaa-1111-2222-3333-444444444444"
M_B = "bbbbbbbb-1111-2222-3333-444444444444"
CLIENT = "alvarez@example.com"
MEMOS = "mcp_smokeball_get_memos_on_matter"
_BLOCK = {"action": "block", "message": "Refused: the gate under test held it"}


@pytest.fixture()
def seat(tmp_path, monkeypatch, voice_certified):
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(AUTONOMOUS_YAML)
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(yaml_path))
    monkeypatch.setenv("SMD_EXPOSURE_OVERRIDE_DB_PATH", str(tmp_path / "ovr.db"))
    monkeypatch.setenv("SMD_CUSTOMER_SLUG", "testco")
    monkeypatch.setenv("HERMES_ACTIVE_PROFILE", "marcus")
    monkeypatch.setenv("SMD_MATTER_GATE_MODE", "block")
    monkeypatch.setattr(trust, "_paused_hard", lambda: False)
    client = FakeD1Client()
    monkeypatch.setattr(audit, "_WRITER", audit.AuditLogWriter(client))
    monkeypatch.setattr(audit, "_CUSTOMER_SLUG", "testco")
    # The audit hook feeds every plugin block to the sticky-stop ladder's
    # refusal arm, which persists to SMD_STICKY_STOP_DB_PATH (default
    # /opt/data/smd/sticky_stop.db, writable on a CI runner). Unisolated, these
    # blocks summed across the corpus replays to HARD_STOP and paused every
    # later test's pre_tool_call. The ladder is not under test here.
    monkeypatch.setenv("SMD_STICKY_STOP_DB_PATH", str(tmp_path / "sticky_stop.db"))
    monkeypatch.setattr(audit, "_cost_breaker", lambda: None)
    matter_binding._reset_for_tests()
    TRUST_DECISIONS.clear()
    yield client
    TRUST_DECISIONS.clear()
    matter_binding._reset_for_tests()


def _dispatch(body: str, call_id: str) -> dict | None:
    """pre_tool_call, then the post_tool_call Hermes fires for a plugin block."""
    args = {"to": CLIENT, "text": body}
    verdict = trust.on_pre_tool_call(
        tool_name=SEND, args=args, session_id=SID, tool_call_id=call_id, customer_slug="testco"
    )
    if verdict is not None:
        audit.on_post_tool_call(
            tool_name=SEND,
            args=args,
            result=json.dumps({"error": verdict["message"]}),
            task_id="t",
            session_id=SID,
            tool_call_id=call_id,
            duration_ms=0,
            status="blocked",
            error_type="plugin_block",
        )
    return verdict


def _row(client: FakeD1Client) -> dict:
    rows = [r for r in client.rows() if r["action_type"] == "TOOL_CALL_COMPLETED"]
    assert len(rows) == 1, "a blocked call writes exactly one audit row"
    return json.loads(rows[0]["metadata"])


def _assert_refused(md: dict) -> None:
    assert md["hook_status"] == "blocked" and md["plugin_block"] is True
    assert md["trust_decision_match"] == "tool_call_id"
    assert md["trust_decision"] == "refuse", md.get("trust_reason")
    assert md["trust_allowed"] is False
    # The ceiling trail stays: what the seat authorized is still a fact.
    assert md["ceiling_level"] == "autonomous"


def test_a_matter_mismatch_on_an_allowed_send_writes_a_refused_row(seat) -> None:
    """FAILS before the fix: the row said trust_decision=allow, trust_allowed=true."""
    matter_binding.membership_for(SID).add(M_A, ["someone-else@example.com"], complete=True)
    verdict = _dispatch(f"Regarding matter {M_A}, please see attached.", "tc-mm")
    assert verdict is not None and verdict["action"] == "block"
    md = _row(seat)
    _assert_refused(md)
    assert "matter identity" in md["trust_reason"]
    assert "permitted" not in md["trust_reason"], "the ceiling's allow reason is gone"


def test_a_multi_matter_session_on_an_allowed_send_writes_a_refused_row(seat) -> None:
    for matter in (M_A, M_B):
        matter_binding.record_from_read(SID, "{}", tool_name=MEMOS, args={"matter_id": matter})
    verdict = _dispatch("Following up as discussed.", "tc-multi")
    assert verdict is not None
    md = _row(seat)
    _assert_refused(md)
    assert "matter mixing" in md["trust_reason"]


@pytest.mark.parametrize(
    "target",
    [
        (trust.enforce._steps.spec_gate, "check_structure_floor"),
        (enforce, "_apply_content_floor"),
        (trust.enforce._steps.voice_gate, "check_voice_gate"),
        (trust.enforce._steps.spec_gate, "check_spec_gate"),
    ],
    ids=["structure", "content_floor", "voice", "spec"],
)
def test_each_later_gate_block_writes_a_refused_row(seat, monkeypatch, target) -> None:
    owner, name = target
    monkeypatch.setattr(owner, name, lambda *a, **k: dict(_BLOCK))
    verdict = _dispatch("Following up as discussed.", f"tc-{name}")
    assert verdict == _BLOCK
    md = _row(seat)
    _assert_refused(md)
    assert md["trust_reason"] == _BLOCK["message"]


def test_an_allowed_send_still_writes_an_allowed_row(seat, monkeypatch) -> None:
    """The control: without a later block the ceiling's allow is what the row says."""
    args = {"to": CLIENT, "text": "Following up as discussed."}
    assert (
        trust.on_pre_tool_call(
            tool_name=SEND, args=args, session_id=SID, tool_call_id="tc-ok", customer_slug="t"
        )
        is None
    )
    audit.on_post_tool_call(
        tool_name=SEND,
        args=args,
        result=json.dumps({"ok": True}),
        task_id="t",
        session_id=SID,
        tool_call_id="tc-ok",
        duration_ms=3,
        status="ok",
    )
    md = _row(seat)
    assert md["trust_decision"] == "allow" and md["trust_allowed"] is True


def test_the_shortfall_ticker_files_the_refused_row_under_the_trust_gate(seat, tmp_path) -> None:
    """The one downstream reader of ``trust_decision`` (``heartbeat._classify_call``)
    filed the wrong row as ``plugin_block`` only because the allow fell through;
    the refused row is the trust gate's own refusal."""
    matter_binding.membership_for(SID).add(M_A, ["someone-else@example.com"], complete=True)
    _dispatch(f"Regarding matter {M_A}, please see attached.", "tc-hb")
    conn: sqlite3.Connection = _ledger_from(seat, tmp_path / "ledger.db")
    rows = [heartbeat._CallRow(*r) for r in conn.execute(heartbeat.CALL_ROW_SELECT)]
    assert heartbeat._classify_call(rows[0], rows, 0) == ("not_allowed", "trust_refuse")
