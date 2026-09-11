"""Golden enforcement matrix for external_send_client / external_send_vendor.

ADR 0075 gives a firm's own rostered CLIENT and RECORDS VENDOR their OWN authored,
fail-closed ceilings — so "chase our client" and "chase the records vendor" can be
graduated to autonomous with a one-line config change, independently of the outside
class and of each other. This is the ss mirror of the overlay's behavioral golden
tests (the recipient axis is resolved upstream; here the class is already definite):

  * client / vendor sends are governed by their OWN class, independent of each
    other AND of external_send / external_send_internal;
  * unauthored client / vendor send is fail-closed (refused) — NOT a silent draft,
    NOT a silent send (ADR 0035);
  * a rostered client / vendor send on a TAINTED turn is still refused;
  * `confirm` (ADR 0071) is a legal ceiling on both classes.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from adapter.trust_ceiling import ActionClass, Ceiling, enforce

CLIENT = ActionClass.EXTERNAL_SEND_CLIENT
VENDOR = ActionClass.EXTERNAL_SEND_VENDOR
OUTSIDE = ActionClass.EXTERNAL_SEND
INTERNAL = ActionClass.EXTERNAL_SEND_INTERNAL


def _enforce(action, action_ceilings=None, *, tainted=False, approval=False):
    return enforce(
        ceiling=Ceiling.AUTONOMOUS,  # skill scalar; does not grant sends
        action=action,
        skill_name="client-verification-tracker",
        tool_name="agentmail.send_draft",
        action_ceilings=action_ceilings,
        current_turn_approval=approval,
        inbound_trust_class="internal" if not tainted else "unknown_external",
    )


def test_client_authored_autonomous_sends():
    d = _enforce(CLIENT, {CLIENT: Ceiling.AUTONOMOUS})
    assert d.allowed and d.audit_action == "allow"


def test_vendor_authored_autonomous_sends():
    d = _enforce(VENDOR, {VENDOR: Ceiling.AUTONOMOUS})
    assert d.allowed and d.audit_action == "allow"


def test_client_unauthored_is_fail_closed_refused_not_draft():
    d = _enforce(CLIENT, {})  # nothing authored for the client class
    assert not d.allowed and d.audit_action == "refuse"


def test_vendor_unauthored_is_fail_closed_refused_not_draft():
    d = _enforce(VENDOR, {})
    assert not d.allowed and d.audit_action == "refuse"


def test_client_authored_draft_routes_to_draft():
    d = _enforce(CLIENT, {CLIENT: Ceiling.DRAFT_FOR_REVIEW})
    assert not d.allowed and d.audit_action == "draft"


def test_client_authored_confirm_withholds_pending_approval():
    d = _enforce(CLIENT, {CLIENT: Ceiling.CONFIRM})
    assert not d.allowed and d.audit_action == "await_approval"
    # With an explicit current-turn approval the confirm send goes.
    d2 = _enforce(CLIENT, {CLIENT: Ceiling.CONFIRM}, approval=True)
    assert d2.allowed and d2.audit_action == "allow"


def test_vendor_authored_confirm_is_legal():
    d = _enforce(VENDOR, {VENDOR: Ceiling.CONFIRM})
    assert not d.allowed and d.audit_action == "await_approval"


def test_client_and_vendor_are_independent_classes():
    # Authoring the vendor class autonomous does NOT grant the client class, and
    # neither does authoring the outside or internal class — each is fail-closed on
    # its own key.
    only_vendor = {VENDOR: Ceiling.AUTONOMOUS}
    assert _enforce(CLIENT, only_vendor).audit_action == "refuse"
    only_client = {CLIENT: Ceiling.AUTONOMOUS}
    assert _enforce(VENDOR, only_client).audit_action == "refuse"
    only_outside = {OUTSIDE: Ceiling.AUTONOMOUS}
    assert _enforce(CLIENT, only_outside).audit_action == "refuse"
    only_internal = {INTERNAL: Ceiling.AUTONOMOUS}
    assert _enforce(VENDOR, only_internal).audit_action == "refuse"


def test_client_autonomous_but_tainted_turn_is_refused():
    d = _enforce(CLIENT, {CLIENT: Ceiling.AUTONOMOUS}, tainted=True)
    assert not d.allowed and d.audit_action == "refuse"
    assert "tainted" in d.reason.lower() or "untrusted" in d.reason.lower()


def test_vendor_autonomous_but_tainted_turn_is_refused():
    d = _enforce(VENDOR, {VENDOR: Ceiling.AUTONOMOUS}, tainted=True)
    assert not d.allowed and d.audit_action == "refuse"
