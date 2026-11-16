"""The read-volume report reaches the audit row (known defect
``read-volume-report-overwritten``, fixed in wave 4 PR C).

Report mode records the crossing as an allowed ``report`` decision, then the
same call records its own ceiling decision under the same ``tool_call_id``. The
register replaced by key, so the row ``post_tool_call`` takes said only "read
permitted" and the one artifact report mode exists to leave was gone. The
register now carries a report's reason onto every later record for that call.
"""

from __future__ import annotations

import pytest

from shared import read_volume
from shared.trust_decision import (
    MATCH_KEYED,
    TRUST_DECISIONS,
    TrustDecision,
    TrustDecisionRegister,
)
from tests.conftest import load_plugin

trust = load_plugin("hermes-smd-trust")
enforce = trust.enforce
steps = trust.evaluate_steps

READ = "mcp_smokeball_read_document"
#: What the read-volume step records (``evaluate_steps.read_volume_fence``).
AUDIT_REPORT = "report"


def _d(audit: str, reason: str, **kw) -> TrustDecision:
    return TrustDecision(action_class="read", audit_action=audit, allowed=True, reason=reason, **kw)


@pytest.fixture(autouse=True)
def _clean():
    TRUST_DECISIONS.clear()
    yield
    TRUST_DECISIONS.clear()


def test_the_wall_steps_leave_the_report_on_the_row(monkeypatch) -> None:
    """FAILS before the fix: the row taken for the call held no READ_VOLUME_GATE."""
    monkeypatch.setattr(
        read_volume, "evaluate_read", lambda s, t: read_volume.Verdict(report="crossed 50 pages")
    )
    monkeypatch.setattr(enforce, "_resolve_active_persona", lambda: "marcus")
    assert steps.read_volume_fence(READ, "tc-rv", "s-rv", "keyed") is None
    decision = enforce.EnforcementDecision(
        allowed=True,
        reason="read permitted",
        audit_action="allow",
        action_class=enforce.ActionClass.READ,
    )
    steps.record_ceiling(READ, "tc-rv", "s-rv", "keyed", "marcus", decision)
    row, match = TRUST_DECISIONS.take("tc-rv", READ)
    assert match == MATCH_KEYED and row is not None
    assert row.audit_action == "allow" and row.allowed is True
    assert row.reason == "read permitted | READ_VOLUME_GATE: crossed 50 pages"


def test_a_report_rides_every_later_record_once() -> None:
    reg = TrustDecisionRegister()
    reg.record("tc", READ, _d(AUDIT_REPORT, "READ_VOLUME_GATE: x"))
    reg.record("tc", READ, _d("allow", "read permitted", effective_ceiling="autonomous"))
    reg.record("tc", READ, _d("allow", "read permitted | MATTER_MISMATCH: y"))
    row, _ = reg.take("tc", READ)
    assert row is not None
    assert row.reason == "read permitted | MATTER_MISMATCH: y | READ_VOLUME_GATE: x"


def test_the_ceiling_fields_of_the_later_record_are_kept() -> None:
    reg = TrustDecisionRegister()
    reg.record("tc", READ, _d(AUDIT_REPORT, "READ_VOLUME_GATE: x"))
    reg.record("tc", READ, _d("allow", "ok", effective_ceiling="autonomous"))
    row, _ = reg.take("tc", READ)
    assert row is not None and row.effective_ceiling == "autonomous"


def test_a_report_never_crosses_to_another_call_or_survives_take() -> None:
    reg = TrustDecisionRegister()
    reg.record("tc-a", READ, _d(AUDIT_REPORT, "READ_VOLUME_GATE: x"))
    reg.record("tc-b", READ, _d("allow", "other call"))
    assert reg.take("tc-b", READ)[0].reason == "other call"  # type: ignore[union-attr]
    reg.take("tc-a", READ)
    reg.record("tc-a", READ, _d("allow", "a reused id"))
    assert reg.take("tc-a", READ)[0].reason == "a reused id"  # type: ignore[union-attr]


def test_an_unkeyed_record_carries_nothing() -> None:
    reg = TrustDecisionRegister()
    reg.record("", READ, _d(AUDIT_REPORT, "READ_VOLUME_GATE: x"))
    reg.record("", READ, _d("allow", "read permitted"))
    assert reg.take("", READ)[0].reason == "read permitted"  # type: ignore[union-attr]


def test_eviction_drops_the_carried_report() -> None:
    reg = TrustDecisionRegister(max_calls=1)
    reg.record("tc-a", READ, _d(AUDIT_REPORT, "READ_VOLUME_GATE: x"))
    reg.record("tc-b", READ, _d("allow", "evicts a"))
    reg.record("tc-a", READ, _d("allow", "after eviction"))
    assert reg.take("tc-a", READ)[0].reason == "after eviction"  # type: ignore[union-attr]


def test_the_register_names_the_report_verdict_the_wall_records() -> None:
    from shared import trust_decision

    assert trust_decision.AUDIT_REPORT == AUDIT_REPORT
