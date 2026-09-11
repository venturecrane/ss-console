"""Invariant 2: external send is gated by the CONFIGURED ceiling and the
vertical floor — and the agent can never exceed either.

Reshaped per ADR 0025, then ADR 0035. The old invariant asserted "no external
send without current-turn confirmation" — a hardcoded refusal of every
autonomous send. ADR 0025 makes exposure a configurable ceiling per action
class; ADR 0035 removes the imposed default. The invariant is now:

  - `external_send` is fail-closed when UNAUTHORED: no `action_ceilings` entry
    means refused — no send, AND no draft (ADR 0035 — no imposed default
    posture). An autonomous *skill scalar* does NOT grant external send, and a
    draft_for_review skill scalar does NOT silently author a draft route; the
    entitlement must be authored explicitly in `action_ceilings`.
  - An AUTHORED `external_send = draft_for_review` routes to draft
    (an authored draft_for_review value, never a default).
  - An explicit autonomous override permits the send.
  - A vertical-pack floor can only narrow: a floor of draft_for_review beats a
    customer override of autonomous, so a regulated vertical (e.g. law) keeps
    draft_for_review even if the customer tries to raise it.
  - A skill whose scalar ceiling is `refused` runs nothing at all.

Failure mode this still protects against: a misconfigured prompt or
compromised tool result causing a skill to fire an outbound message it was
not configured to send autonomously. Authorization comes from authored config
(customer.yaml + vertical floor), never from model claims or the in-turn
approval flag (that flag governs the reversibility classes; see invariant 5).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from adapter.trust_ceiling import ActionClass, Ceiling, enforce


def run() -> tuple[bool, str]:
    # Scenario A: UNAUTHORED external_send (no action_ceilings) is fail-closed —
    # refused, no draft (ADR 0035: no imposed default posture).
    decision = enforce(
        ceiling=Ceiling.DRAFT_FOR_REVIEW,
        action=ActionClass.EXTERNAL_SEND,
        skill_name="inbox-triage",
        tool_name="gmail.send",
        current_turn_approval=False,
    )
    if decision.allowed or decision.audit_action != "refuse":
        return (
            False,
            f"FAIL: unauthored external_send must be fail-closed (refused), got "
            f"allowed={decision.allowed} audit={decision.audit_action}",
        )

    # Scenario B: an autonomous SKILL scalar does NOT auto-grant external send.
    # With no action_ceilings entry, external_send is unauthored → refused.
    decision = enforce(
        ceiling=Ceiling.AUTONOMOUS,
        action=ActionClass.EXTERNAL_SEND,
        skill_name="ar-chaser",
        tool_name="gmail.send",
        current_turn_approval=False,
    )
    if decision.allowed or decision.audit_action != "refuse":
        return (
            False,
            f"FAIL: autonomous skill scalar must NOT silently grant external send; "
            f"unauthored external_send is fail-closed, got allowed={decision.allowed} "
            f"audit={decision.audit_action}",
        )

    # Scenario B2: an AUTHORED external_send=draft_for_review routes to draft —
    # draft_for_review is a value you author, distinct from unauthored=refused.
    decision = enforce(
        ceiling=Ceiling.AUTONOMOUS,
        action=ActionClass.EXTERNAL_SEND,
        skill_name="ar-chaser",
        tool_name="gmail.send",
        current_turn_approval=False,
        action_ceilings={ActionClass.EXTERNAL_SEND: Ceiling.DRAFT_FOR_REVIEW},
    )
    if decision.allowed or decision.audit_action != "draft":
        return (
            False,
            f"FAIL: authored external_send=draft_for_review should draft, got "
            f"allowed={decision.allowed} audit={decision.audit_action}",
        )

    # Scenario C: explicit action_ceilings override permits autonomous send.
    decision = enforce(
        ceiling=Ceiling.DRAFT_FOR_REVIEW,
        action=ActionClass.EXTERNAL_SEND,
        skill_name="ar-chaser",
        tool_name="gmail.send",
        current_turn_approval=False,
        action_ceilings={ActionClass.EXTERNAL_SEND: Ceiling.AUTONOMOUS},
    )
    if not decision.allowed or decision.audit_action != "allow":
        return (
            False,
            f"FAIL: explicit external_send=autonomous should send, got allowed={decision.allowed} "
            f"audit={decision.audit_action} ({decision.reason})",
        )

    # Scenario D: explicit external_send=refused blocks the send outright.
    decision = enforce(
        ceiling=Ceiling.AUTONOMOUS,
        action=ActionClass.EXTERNAL_SEND,
        skill_name="ar-chaser",
        tool_name="gmail.send",
        current_turn_approval=False,
        action_ceilings={ActionClass.EXTERNAL_SEND: Ceiling.REFUSED},
    )
    if decision.allowed or decision.audit_action != "refuse":
        return (
            False,
            f"FAIL: external_send=refused should refuse, got allowed={decision.allowed} audit={decision.audit_action}",
        )

    # Scenario E: a vertical floor can only NARROW. A floor of draft_for_review
    # beats a customer override of autonomous — the regulated-vertical guarantee.
    decision = enforce(
        ceiling=Ceiling.AUTONOMOUS,
        action=ActionClass.EXTERNAL_SEND,
        skill_name="ar-chaser",
        tool_name="gmail.send",
        current_turn_approval=False,
        action_ceilings={ActionClass.EXTERNAL_SEND: Ceiling.AUTONOMOUS},
        vertical_floors={ActionClass.EXTERNAL_SEND: Ceiling.DRAFT_FOR_REVIEW},
    )
    if decision.allowed or decision.audit_action != "draft":
        return (
            False,
            f"FAIL: vertical floor draft_for_review must beat customer override autonomous; "
            f"expected draft, got allowed={decision.allowed} audit={decision.audit_action}",
        )

    # Scenario F: a skill whose scalar ceiling is refused runs nothing — even an
    # external_send=autonomous override cannot revive a disabled skill.
    decision = enforce(
        ceiling=Ceiling.REFUSED,
        action=ActionClass.EXTERNAL_SEND,
        skill_name="never-runs",
        tool_name="gmail.send",
        current_turn_approval=True,
        action_ceilings={ActionClass.EXTERNAL_SEND: Ceiling.AUTONOMOUS},
    )
    if decision.allowed:
        return (
            False,
            f"FAIL: refused-scalar skill should never execute anything, got allowed ({decision.reason})",
        )

    # Scenario G (ADR 0071): authored external_send=confirm WITHOUT a current-turn
    # approval is WITHHELD pending approval — not sent, not drafted, not refused.
    decision = enforce(
        ceiling=Ceiling.DRAFT_FOR_REVIEW,
        action=ActionClass.EXTERNAL_SEND,
        skill_name="ar-chaser",
        tool_name="gmail.send",
        current_turn_approval=False,
        action_ceilings={ActionClass.EXTERNAL_SEND: Ceiling.CONFIRM},
    )
    if decision.allowed or decision.audit_action != "await_approval":
        return (
            False,
            f"FAIL: external_send=confirm without approval must withhold pending approval, got "
            f"allowed={decision.allowed} audit={decision.audit_action}",
        )

    # Scenario H (ADR 0071): authored external_send=confirm WITH a current-turn
    # approval sends — the one exposure value that consults the approval flag.
    decision = enforce(
        ceiling=Ceiling.DRAFT_FOR_REVIEW,
        action=ActionClass.EXTERNAL_SEND,
        skill_name="ar-chaser",
        tool_name="gmail.send",
        current_turn_approval=True,
        action_ceilings={ActionClass.EXTERNAL_SEND: Ceiling.CONFIRM},
    )
    if not decision.allowed or decision.audit_action != "allow":
        return (
            False,
            f"FAIL: external_send=confirm with current-turn approval should send, got "
            f"allowed={decision.allowed} audit={decision.audit_action} ({decision.reason})",
        )

    # Scenario I (ADR 0071 ordering): a vertical floor of draft_for_review NARROWS an
    # authored confirm (confirm < draft_for_review) — even with approval it drafts,
    # never sends. The regulated-vertical guarantee holds against confirm too.
    decision = enforce(
        ceiling=Ceiling.DRAFT_FOR_REVIEW,
        action=ActionClass.EXTERNAL_SEND,
        skill_name="ar-chaser",
        tool_name="gmail.send",
        current_turn_approval=True,
        action_ceilings={ActionClass.EXTERNAL_SEND: Ceiling.CONFIRM},
        vertical_floors={ActionClass.EXTERNAL_SEND: Ceiling.DRAFT_FOR_REVIEW},
    )
    if decision.allowed or decision.audit_action != "draft":
        return (
            False,
            f"FAIL: vertical floor draft_for_review must narrow authored confirm to draft; "
            f"expected draft, got allowed={decision.allowed} audit={decision.audit_action}",
        )

    return (
        True,
        "PASS: invariant 2 holds — external send gated by configured ceiling; "
        "autonomous skill scalar does not auto-grant send; explicit override sends; "
        "vertical floor narrows; refused scalar blocks all; confirm withholds without "
        "approval, sends with it, and is narrowed by a draft_for_review floor (ADR 0071)",
    )


if __name__ == "__main__":
    ok, msg = run()
    print(msg)
    sys.exit(0 if ok else 1)
