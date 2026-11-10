"""Invariant 2: external send is gated by the CONFIGURED ceiling and the
vertical floor, and the agent can never exceed either.

Reshaped per ADR 0025, then ADR 0035 and ADR 0056. The old invariant asserted
"no external send without current-turn confirmation", a hardcoded refusal of
every autonomous send. ADR 0025 makes exposure a configurable ceiling per action
class; ADR 0035 removes the imposed default. The invariant is now:

  - Every send class (``external_send``, ``external_send_internal``,
    ``external_send_client``, ``external_send_vendor``) is fail-closed when
    UNAUTHORED: no exposure entry means refused, no send AND no draft (no
    imposed default posture).
  - An AUTHORED ``external_send = draft_for_review`` routes to draft.
  - An authored autonomous exposure permits the send; an authored refused
    blocks it.
  - A vertical-pack floor can only narrow: a floor of draft_for_review beats an
    authored autonomous, so a regulated vertical (e.g. law) keeps
    draft_for_review even if the customer tries to raise it.
  - ``confirm`` (ADR 0071) withholds without a current-turn approval, sends with
    one, and is narrowed by a draft_for_review floor.

The retired per-skill ``trust_ceiling`` scalar (ADR 0056) is not an input to the
gateway at all, so there is nothing for it to grant.

Failure mode this still protects against: a misconfigured prompt or
compromised tool result causing a skill to fire an outbound message it was
not configured to send autonomously. Authorization comes from authored config
(customer.yaml + vertical floor), never from model claims or the in-turn
approval flag (that flag governs the reversibility classes; see invariant 5).

Exercised against the gateway's own decision: ``hermes-smd-trust``'s
``enforce`` (``safety_substrate/invariants/trust_plugin.py``).
"""

from __future__ import annotations

import sys

from safety_substrate.invariants import trust_plugin


def run() -> tuple[bool, str]:
    t = trust_plugin.enforce_module()
    A, C = t.ActionClass, t.Ceiling
    send = A.EXTERNAL_SEND

    def decide(exposure, *, approved=False, floors=None, action=send):
        return t.enforce(
            action=action,
            exposure=exposure,
            tool_name="gmail.send",
            current_turn_approval=approved,
            vertical_floors=floors,
        )

    # Scenario A: every UNAUTHORED send class is fail-closed: refused, no draft,
    # even with an approval.
    for action in (A.EXTERNAL_SEND, A.EXTERNAL_SEND_INTERNAL, A.EXTERNAL_SEND_CLIENT, A.EXTERNAL_SEND_VENDOR):
        decision = decide(None, approved=True, action=action)
        if decision.allowed or decision.audit_action != "refuse":
            return (
                False,
                f"FAIL: unauthored {action.value} must be fail-closed (refused), got "
                f"allowed={decision.allowed} audit={decision.audit_action}",
            )

    # Scenario B: an AUTHORED external_send=draft_for_review routes to draft.
    decision = decide({send: C.DRAFT_FOR_REVIEW})
    if decision.allowed or decision.audit_action != "draft":
        return (
            False,
            f"FAIL: authored external_send=draft_for_review should draft, got "
            f"allowed={decision.allowed} audit={decision.audit_action}",
        )

    # Scenario C: an authored autonomous exposure permits the send.
    decision = decide({send: C.AUTONOMOUS})
    if not decision.allowed or decision.audit_action != "allow":
        return (
            False,
            f"FAIL: authored external_send=autonomous should send, got allowed={decision.allowed} "
            f"audit={decision.audit_action} ({decision.reason})",
        )

    # Scenario D: an authored external_send=refused blocks the send outright.
    decision = decide({send: C.REFUSED}, approved=True)
    if decision.allowed or decision.audit_action != "refuse":
        return (
            False,
            f"FAIL: external_send=refused should refuse, got allowed={decision.allowed} audit={decision.audit_action}",
        )

    # Scenario E: a vertical floor can only NARROW. A floor of draft_for_review
    # beats an authored autonomous: the regulated-vertical guarantee.
    decision = decide({send: C.AUTONOMOUS}, floors={send: C.DRAFT_FOR_REVIEW})
    if decision.allowed or decision.audit_action != "draft":
        return (
            False,
            f"FAIL: vertical floor draft_for_review must beat authored autonomous; "
            f"expected draft, got allowed={decision.allowed} audit={decision.audit_action}",
        )

    # Scenario F (ADR 0071): authored confirm WITHOUT a current-turn approval is
    # WITHHELD pending approval: not sent, not drafted, not refused.
    decision = decide({send: C.CONFIRM})
    if decision.allowed or decision.audit_action != "await_approval":
        return (
            False,
            f"FAIL: external_send=confirm without approval must withhold pending approval, got "
            f"allowed={decision.allowed} audit={decision.audit_action}",
        )

    # Scenario G (ADR 0071): authored confirm WITH a current-turn approval sends.
    decision = decide({send: C.CONFIRM}, approved=True)
    if not decision.allowed or decision.audit_action != "allow":
        return (
            False,
            f"FAIL: external_send=confirm with current-turn approval should send, got "
            f"allowed={decision.allowed} audit={decision.audit_action} ({decision.reason})",
        )

    # Scenario H (ADR 0071 ordering): a draft_for_review floor NARROWS an authored
    # confirm; even with approval it drafts, never sends.
    decision = decide({send: C.CONFIRM}, approved=True, floors={send: C.DRAFT_FOR_REVIEW})
    if decision.allowed or decision.audit_action != "draft":
        return (
            False,
            f"FAIL: vertical floor draft_for_review must narrow authored confirm to draft; "
            f"expected draft, got allowed={decision.allowed} audit={decision.audit_action}",
        )

    return (
        True,
        "PASS: invariant 2 holds: every unauthored send class is fail-closed; authored draft "
        "drafts, autonomous sends, refused refuses; a vertical floor narrows; confirm withholds "
        f"without approval, sends with it, and is narrowed by a draft_for_review floor ({trust_plugin.source()})",
    )


if __name__ == "__main__":
    ok, msg = run()
    print(msg)
    sys.exit(0 if ok else 1)
