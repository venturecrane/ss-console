"""A call ``on_pre_tool_call`` blocks AFTER the wall allowed it is recorded as
refused.

The wall (``enforce.evaluate_tool_call``) records its decision, and its own
later gates now record their refusals (``tests/test_blocked_after_allow_row.py``).
But the pre-hook runs four more blocks of its own once the wall allowed the
call: the outbound draft scan, the outbound send scan, the rendered-body gate,
and the hook's fail-closed catch-all. Each returned a block and recorded
nothing, so the register still held the wall's allow, Hermes fired
``post_tool_call`` with ``status="blocked"``, and the audit row said
``trust_decision=allow`` on a call that never ran.

These drive the real trust ``on_pre_tool_call`` and then the real audit
``on_post_tool_call`` with the envelope Hermes passes for a plugin block, and
read the row, reusing the fixture and dispatcher of the wall's own row test.
"""

from __future__ import annotations

import pytest

from shared import matter_binding
from tests.test_blocked_after_allow_row import (
    _BLOCK,
    M_A,
    M_B,
    MEMOS,
    SID,
    _assert_refused,
    _dispatch,
    _row,
    audit,
    seat,
    trust,
)

__all__ = ["seat"]  # the fixture, imported for pytest to find

_BODY = "Following up as discussed."


@pytest.fixture(autouse=True)
def _no_ladder(seat, monkeypatch):
    """Each refused row here is a refusal the audit plugin feeds the sticky-stop
    ladder, whose breaker is built lazily and cached on the module for the
    process. The ``seat`` fixture disarms it on the audit module ``_dispatch``
    calls (and points the state file at the test's own tmp dir); this checks,
    on that same module, that no breaker is cached before or after a test, and
    clears the trust plugin's pause cache so no HARD_STOP read leaks."""
    assert audit._COST_BREAKER is None, "a breaker was cached before this test"
    monkeypatch.setitem(trust._pause_cache, "at", 0.0)
    monkeypatch.setitem(trust._pause_cache, "hard", False)
    yield
    assert audit._COST_BREAKER is None, "this test armed the sticky-stop breaker"


@pytest.mark.parametrize(
    "target",
    [
        (trust.outbound, "check_outbound_draft"),
        (trust.outbound, "check_outbound_send"),
        (trust.rendered_body_gate, "check_body"),
    ],
    ids=["draft_scan", "send_scan", "rendered_body"],
)
def test_each_pre_hook_block_after_allow_writes_a_refused_row(seat, monkeypatch, target) -> None:
    """FAILS before the fix: the row said trust_decision=allow, trust_allowed=true."""
    owner, name = target
    monkeypatch.setattr(owner, name, lambda *a, **k: dict(_BLOCK))
    verdict = _dispatch(_BODY, f"tc-{name}")
    assert verdict == _BLOCK
    md = _row(seat)
    _assert_refused(md)
    assert md["trust_reason"] == _BLOCK["message"]


def _boom(*_a, **_k):
    raise RuntimeError("injected")


def test_the_catch_all_after_allow_writes_a_fail_closed_refused_row(seat, monkeypatch) -> None:
    """A fault after the wall allowed the call: the row keeps the wall's ceiling
    trail and says the catch-all refused it."""
    monkeypatch.setattr(trust.outbound, "check_outbound_send", _boom)
    verdict = _dispatch(_BODY, "tc-raise-late")
    assert verdict is not None and verdict["action"] == "block"
    md = _row(seat)
    _assert_refused(md)
    assert md["trust_reason"].startswith("CATCH_ALL: ")
    assert "failed closed" in md["trust_reason"] and "RuntimeError" in md["trust_reason"]


def test_the_catch_all_before_the_wall_writes_a_fail_closed_refused_row(seat, monkeypatch) -> None:
    """A fault before the wall decided anything: no decision existed for the
    call, and the row now carries the catch-all's refusal instead of none."""
    monkeypatch.setattr(trust.spec_read, "observe_read", _boom)
    verdict = _dispatch(_BODY, "tc-raise-early")
    assert verdict is not None and verdict["action"] == "block"
    md = _row(seat)
    assert md["trust_decision_match"] == "tool_call_id"
    assert md["trust_decision"] == "refuse" and md["trust_allowed"] is False
    assert md["trust_reason"].startswith("CATCH_ALL: ")
    assert md["resolved_action_class"] == "external_send"


@pytest.mark.parametrize(
    "target",
    [
        (trust.enforce._steps.spec_gate, "check_structure_floor"),
        (trust.outbound, "check_outbound_send"),
    ],
    ids=["wall_gate", "pre_hook_block"],
)
def test_a_refusal_keeps_the_matter_gates_report_mode_note(seat, monkeypatch, target) -> None:
    """In report mode the matter gate notes a multi-matter session on the
    ceiling's record. FAILS before the fix: the refusal replaced the record and
    the note was gone from the row."""
    monkeypatch.setenv("SMD_MULTI_MATTER_MODE", "report")
    for matter in (M_A, M_B):
        matter_binding.record_from_read(SID, "{}", tool_name=MEMOS, args={"matter_id": matter})
    owner, name = target
    monkeypatch.setattr(owner, name, lambda *a, **k: dict(_BLOCK))
    verdict = _dispatch(_BODY, f"tc-note-{name}")
    assert verdict == _BLOCK
    md = _row(seat)
    _assert_refused(md)
    assert md["trust_reason"].startswith(_BLOCK["message"] + " | ")
    assert "MULTI_MATTER_SESSION: session read content from 2 matters" in md["trust_reason"]
    assert "permitted" not in md["trust_reason"], "the ceiling's allow words are gone"
