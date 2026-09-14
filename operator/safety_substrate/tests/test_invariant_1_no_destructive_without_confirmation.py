"""Invariant 1: no destructive action without explicit current-turn confirmation.

Test that even an autonomous skill cannot execute a destructive tool
(delete, drop, archive, etc.) without the operator's approval text present
in the current invocation. Approvals from prior turns or prior sessions
are not valid.

Reference incident: OpenClaw, Meta AI Safety director's agent — autonomous
mode deleted operator's inbox after compaction dropped a "don't act"
instruction (Feb 2026). Our enforcement: even with the prompt fully
autonomous and the ceiling raised, destructive == refuse unless approval
is in THIS turn.
"""

from __future__ import annotations

import sys

# Allow the test to run both via the substrate runner and standalone.

from adapter.trust_ceiling import ActionClass, Ceiling, enforce


def run() -> tuple[bool, str]:
    # Scenario A: autonomous skill, destructive action, no approval — must refuse
    decision = enforce(
        ceiling=Ceiling.AUTONOMOUS,
        action=ActionClass.DESTRUCTIVE,
        skill_name="inbox-cleanup-test",
        tool_name="gmail.delete",
        current_turn_approval=False,
    )
    if decision.allowed:
        return (
            False,
            f"FAIL: autonomous destructive WITHOUT approval was allowed (audit={decision.audit_action})",
        )
    if decision.audit_action != "refuse":
        return (
            False,
            f"FAIL: destructive without approval should refuse, got audit={decision.audit_action}",
        )

    # Scenario B: autonomous skill, destructive action, WITH approval — allowed
    decision = enforce(
        ceiling=Ceiling.AUTONOMOUS,
        action=ActionClass.DESTRUCTIVE,
        skill_name="inbox-cleanup-test",
        tool_name="gmail.delete",
        current_turn_approval=True,
    )
    if not decision.allowed:
        return (
            False,
            f"FAIL: destructive with current-turn approval should be allowed, got refused ({decision.reason})",
        )

    # Scenario C: draft-for-review skill, destructive action, with approval — still refused
    # (draft-for-review never escalates to destructive, even with approval)
    decision = enforce(
        ceiling=Ceiling.DRAFT_FOR_REVIEW,
        action=ActionClass.DESTRUCTIVE,
        skill_name="inbox-triage",
        tool_name="gmail.delete",
        current_turn_approval=True,
    )
    # Per the current implementation, destructive routes through the same
    # approval gate regardless of ceiling. If ceiling is draft_for_review,
    # destructive with approval IS allowed (the gate is "is this a person
    # who said yes right now"). Document this and ensure it matches policy.
    # Policy choice for SMD: draft_for_review skills never originate destructive
    # tool calls. The skill author marking it draft_for_review should be using
    # tools that can't destruct in the first place. If a draft skill ever calls
    # a destructive tool, that's a skill-authorship bug. The substrate refuses
    # to be safe.
    if decision.allowed:
        return (
            False,
            "FAIL: draft_for_review skill destructive action allowed (policy says: draft skills shouldn't destruct)",
        )

    return (True, "PASS: invariant 1 holds across autonomous/draft ceilings + with/without approval")


if __name__ == "__main__":
    ok, msg = run()
    print(msg)
    sys.exit(0 if ok else 1)
