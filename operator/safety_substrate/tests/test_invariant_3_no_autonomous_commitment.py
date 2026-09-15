"""Invariant 3: no contract or commitment execution autonomously.

Signing a document, accepting terms, agreeing to scope/dates/pricing,
clicking an "I agree" button — all gated. Even autonomous skills cannot
originate a commitment without current-turn approval. Draft skills don't
originate commitments at all — they produce drafts for human review.

Failure mode this protects against: a hallucinated context (skill thinks
the operator already approved a deal) or a prompt-injected tool result
causing the agent to sign or commit to something it shouldn't.
"""

from __future__ import annotations

import sys


from adapter.trust_ceiling import ActionClass, Ceiling, enforce


def run() -> tuple[bool, str]:
    # Scenario A: autonomous skill, commitment action, no approval — refused
    decision = enforce(
        ceiling=Ceiling.AUTONOMOUS,
        action=ActionClass.COMMITMENT,
        skill_name="contract-counter-signer",
        tool_name="docusign.sign",
        current_turn_approval=False,
    )
    if decision.allowed:
        return (
            False,
            f"FAIL: autonomous commitment WITHOUT approval was allowed (audit={decision.audit_action})",
        )

    # Scenario B: autonomous skill, commitment, WITH approval — allowed
    decision = enforce(
        ceiling=Ceiling.AUTONOMOUS,
        action=ActionClass.COMMITMENT,
        skill_name="contract-counter-signer",
        tool_name="docusign.sign",
        current_turn_approval=True,
    )
    if not decision.allowed:
        return (
            False,
            f"FAIL: autonomous commitment WITH approval should be allowed ({decision.reason})",
        )

    # Scenario C: draft_for_review skill, commitment action — never originates, routes to draft
    # (a draft skill that "agrees" to something is wrong by design — it should produce text
    # that says "Recommend accepting these terms" for human review)
    decision = enforce(
        ceiling=Ceiling.DRAFT_FOR_REVIEW,
        action=ActionClass.COMMITMENT,
        skill_name="contract-redliner",
        tool_name="docusign.sign",
        current_turn_approval=True,  # even with approval, draft doesn't commit
    )
    if decision.allowed:
        return (
            False,
            f"FAIL: draft_for_review skill should never originate commitment, even with approval ({decision.reason})",
        )

    return (True, "PASS: invariant 3 holds — commitment gated by approval; draft skills never commit")


if __name__ == "__main__":
    ok, msg = run()
    print(msg)
    sys.exit(0 if ok else 1)
