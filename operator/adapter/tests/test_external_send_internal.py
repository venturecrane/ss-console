"""Golden enforcement matrix for external_send_internal (rostered/internal sends).

The recipient-aware split gives internal (human-rostered) sends their OWN authored,
fail-closed ceiling — so "a coordinator that needs a click to answer staff isn't
one" is authorable without opening outside sends. These are the behavioral
assertions the resolved-ceiling snapshot guard (WS4) is built on:

  * internal sends are governed by their OWN class, independent of external_send;
  * unauthored internal send is fail-closed (refused) — NOT a silent draft, NOT a
    silent send (ADR 0035);
  * the law-firm draft floor pins the OUTSIDE class only, never the internal class;
  * a rostered internal send on a TAINTED turn is still refused (an injected
    "notify staff" is still an injected action).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from adapter.trust_ceiling import ActionClass, Ceiling, enforce

INTERNAL = ActionClass.EXTERNAL_SEND_INTERNAL
OUTSIDE = ActionClass.EXTERNAL_SEND
# The law-firm vertical pins the OUTSIDE send class to draft — and ONLY that class.
LAW_FLOORS = {ActionClass.EXTERNAL_SEND: Ceiling.DRAFT_FOR_REVIEW}


def _enforce(action, action_ceilings=None, *, floors=None, tainted=False):
    return enforce(
        ceiling=Ceiling.AUTONOMOUS,  # skill scalar; does not grant sends
        action=action,
        skill_name="client-verification-tracker",
        tool_name="agentmail.send_draft",
        action_ceilings=action_ceilings,
        vertical_floors=floors,
        inbound_trust_class="internal" if not tainted else "unknown_external",
    )


def test_internal_authored_autonomous_sends():
    d = _enforce(INTERNAL, {INTERNAL: Ceiling.AUTONOMOUS})
    assert d.allowed and d.audit_action == "allow"


def test_internal_unauthored_is_fail_closed_refused_not_draft():
    # The keystone: no authored external_send_internal → refused, not a silent draft.
    d = _enforce(INTERNAL, {})  # nothing authored for the internal class
    assert not d.allowed and d.audit_action == "refuse"


def test_internal_authored_draft_routes_to_draft():
    d = _enforce(INTERNAL, {INTERNAL: Ceiling.DRAFT_FOR_REVIEW})
    assert not d.allowed and d.audit_action == "draft"


def test_internal_authored_refused_blocks():
    d = _enforce(INTERNAL, {INTERNAL: Ceiling.REFUSED})
    assert not d.allowed and d.audit_action == "refuse"


def test_law_outside_floor_does_NOT_pin_the_internal_class():
    # A law seat: internal autonomous, outside floored to draft. The internal send
    # stays autonomous — the floor is on external_send only. THIS is the fix.
    d = _enforce(INTERNAL, {INTERNAL: Ceiling.AUTONOMOUS}, floors=LAW_FLOORS)
    assert d.allowed and d.audit_action == "allow"


def test_law_outside_floor_still_pins_the_outside_class():
    # Same seat, an OUTSIDE send: even authored autonomous, the law floor draws it
    # to draft. Outside sends stay exactly as gated as before.
    d = _enforce(OUTSIDE, {OUTSIDE: Ceiling.AUTONOMOUS}, floors=LAW_FLOORS)
    assert not d.allowed and d.audit_action == "draft"


def test_internal_autonomous_but_tainted_turn_is_refused():
    # An injected "notify staff about X" must not fire autonomously on a tainted turn.
    d = _enforce(INTERNAL, {INTERNAL: Ceiling.AUTONOMOUS}, tainted=True)
    assert not d.allowed and d.audit_action == "refuse"
    assert "tainted" in d.reason.lower() or "untrusted" in d.reason.lower()


def test_internal_and_outside_are_independent_classes():
    # Authoring the outside class autonomous does NOT grant the internal class,
    # and vice-versa — each is fail-closed on its own key.
    only_outside = {OUTSIDE: Ceiling.AUTONOMOUS}
    d_internal = _enforce(INTERNAL, only_outside)
    assert not d_internal.allowed and d_internal.audit_action == "refuse"

    only_internal = {INTERNAL: Ceiling.AUTONOMOUS}
    d_outside = _enforce(OUTSIDE, only_internal)
    assert not d_outside.allowed and d_outside.audit_action == "refuse"
