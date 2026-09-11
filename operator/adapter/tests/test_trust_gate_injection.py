"""The trust gate refuses an injected send regardless of what the fence did (ADR 0027, ADR 0071).

These three assertions were the load-bearing half of ``test_inbound_envelope.py``
until 2026-09-11. That file tested ``adapter/inbound_envelope.py``, a copy of the
inbound envelope that nothing in this repo imported; the running boundary is the
overlay's ``shared/inbound.py`` and its ``hermes-smd-inbound`` plugin, tested
there. The copy is gone (same call as the in-tree YAML validator, #1045). What
stays is the part that tests THIS repo's code: the wall is ``enforce()``, the
fence is defense-in-depth, and an instruction smuggled in inbound text asking
the agent to send is refused here no matter what the fence did with it.

Deterministic, no live model.
"""

from __future__ import annotations

import sys
from pathlib import Path

# operator/ root, so `adapter.*` imports resolve.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from adapter.trust_ceiling import ActionClass, Ceiling, enforce


def test_injected_external_send_is_refused_by_the_gate():
    # The wall, not the fence: even if an injection convinces the agent to try
    # an external send, the trust gate refuses it at the default ceiling.
    decision = enforce(
        ceiling=Ceiling.DRAFT_FOR_REVIEW,
        action=ActionClass.EXTERNAL_SEND,
        skill_name="inbox-triage",
        tool_name="gmail.send",
        current_turn_approval=True,  # an injected "user approved this" is not honored
    )
    assert not decision.allowed
    assert decision.audit_action in {"draft", "refuse"}


def test_injected_send_refused_even_with_autonomous_skill_scalar():
    # An autonomous skill scalar does not grant external send (ADR 0025);
    # absent an explicit action_ceilings override, an injected send drafts.
    decision = enforce(
        ceiling=Ceiling.AUTONOMOUS,
        action=ActionClass.EXTERNAL_SEND,
        skill_name="ar-chaser",
        tool_name="gmail.send",
        current_turn_approval=False,
    )
    assert not decision.allowed


def test_confirm_send_refused_on_tainted_turn_even_with_approval():
    # ADR 0071: the `confirm` ceiling is the one exposure value that consults
    # current_turn_approval, but the taint-gate DOMINATES and sits before the
    # send branch. A turn that ingested untrusted inbound content cannot reach the
    # confirm allow-path, even with the approval flag set: an inbound/injected
    # "reply yes" must never turn into a send. This is the load-bearing invariant
    # for confirm-over-channel (the approval must come from a trusted path, not the
    # tainted inbound turn).
    decision = enforce(
        ceiling=Ceiling.DRAFT_FOR_REVIEW,
        action=ActionClass.EXTERNAL_SEND,
        skill_name="inbox-triage",
        tool_name="gmail.send",
        current_turn_approval=True,
        action_ceilings={ActionClass.EXTERNAL_SEND: Ceiling.CONFIRM},
        inbound_trust_class="external_untrusted",
    )
    assert not decision.allowed
    assert decision.audit_action == "refuse"
