"""Invariant 3: no contract or commitment execution autonomously.

Signing a document, accepting terms, agreeing to scope/dates/pricing,
clicking an "I agree" button: all gated. Even an autonomous exposure cannot
originate a commitment without current-turn approval. A draft posture doesn't
originate commitments at all; it produces drafts for human review.

Failure mode this protects against: a hallucinated context (the agent thinks
the operator already approved a deal) or a prompt-injected tool result
causing the agent to sign or commit to something it shouldn't.

Exercised against the gateway's own decision: ``hermes-smd-trust``'s
``enforce`` (``safety_substrate/invariants/trust_plugin.py``).
"""

from __future__ import annotations

import sys

from safety_substrate.invariants import trust_plugin


def run() -> tuple[bool, str]:
    t = trust_plugin.enforce_module()
    A, C = t.ActionClass, t.Ceiling

    def decide(exposure, approved):
        return t.enforce(
            action=A.COMMITMENT,
            exposure=exposure,
            tool_name="docusign.sign",
            current_turn_approval=approved,
        )

    # Scenario A: autonomous exposure, commitment action, no approval: refused
    decision = decide({A.COMMITMENT: C.AUTONOMOUS}, False)
    if decision.allowed:
        return (
            False,
            f"FAIL: autonomous commitment WITHOUT approval was allowed (audit={decision.audit_action})",
        )

    # Scenario B: autonomous exposure, commitment, WITH approval: allowed
    decision = decide({A.COMMITMENT: C.AUTONOMOUS}, True)
    if not decision.allowed:
        return (
            False,
            f"FAIL: autonomous commitment WITH approval should be allowed ({decision.reason})",
        )

    # Scenario C: draft_for_review exposure: never originates, even with approval
    decision = decide({A.COMMITMENT: C.DRAFT_FOR_REVIEW}, True)
    if decision.allowed:
        return (
            False,
            f"FAIL: draft_for_review should never originate a commitment, even with approval ({decision.reason})",
        )

    # Scenario D: unauthored commitment, with approval: fail-closed (ADR 0056)
    decision = decide(None, True)
    if decision.allowed or decision.audit_action != "refuse":
        return (
            False,
            f"FAIL: unauthored commitment with approval must refuse, got "
            f"allowed={decision.allowed} audit={decision.audit_action}",
        )

    # Scenario E: authored confirm without approval is withheld, not executed
    decision = decide({A.COMMITMENT: C.CONFIRM}, False)
    if decision.allowed or decision.audit_action != "await_approval":
        return (
            False,
            f"FAIL: commitment at confirm without approval must be withheld, got "
            f"allowed={decision.allowed} audit={decision.audit_action}",
        )

    return (
        True,
        "PASS: invariant 3 holds: commitment gated by approval; draft and unauthored never commit; "
        f"confirm withholds ({trust_plugin.source()})",
    )


if __name__ == "__main__":
    ok, msg = run()
    print(msg)
    sys.exit(0 if ok else 1)
