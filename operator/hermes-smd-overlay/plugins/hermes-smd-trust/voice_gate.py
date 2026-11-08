"""Voice live-gate — ADR 0028 §2 (#855), repointed per-class per ss#2086 step 1.

Closes the "primed, not gated" gap. An autonomous OUTSIDE send could ship in
generic model prose, not the firm's authored voice, with no human to catch it.
This gate is that enforcement.

Binding — TWO regimes, resolved per (seat × output class), ADDITIVELY
---------------------------------------------------------------------
The output class for a send derives from the recipient class the trust decision
already resolved (``shared.spec_gate.resolve_output_class``). For that ONE
class:

* **SPEC regime** — the seat's live ``customer.yaml`` declares
  ``output_classes.<class>.voice_spec: expected``. The authored-spec binding
  governs (ss ADR 0083 / ADR 0085): pass = the class's voice spec is installed,
  its bytes hash to what the ROOT-OWNED manifest recorded, and the agent read it
  THIS turn (``shared.spec_status`` — a mark is only ever set after
  ``shared.spec_manifest`` verified the file, so the mark IS all three
  conditions). Reasons: ``no_spec`` | ``spec_hash_mismatch`` | ``spec_not_read``.

* **UNDECLARED** (since Mechanism B's removal, ss ADR 0083 decision 6,
  2026-11-07) — the class is NOT declared ``expected`` (undeclared class,
  ``output_classes`` absent entirely, ``voice_spec: none``, or a config this
  process cannot read). Nothing certifies the send's voice, so it FAILS CLOSED:
  downgraded to draft-for-review with reason ``no_voice_spec_declared``. This
  replaced the sample-transform fallback, which bound every seat (all seven
  authored a sample library) and passed only on a turn the transform reshaped;
  with no transform the old fallback downgraded every such send, so the new rule
  is never weaker on any (seat x class). ``tests/test_voice_gate_binding_coverage.py``
  asserts that over a checked-in snapshot of every real seat's config.

The gate is never unbound for an autonomous outside send: ADR 0028, a seat must
not send autonomously in a voice nothing certifies.

A config whose ``output_classes`` is unreadable or malformed reads as undeclared
and downgrades: never silently unbound.

Firing site (``enforce.evaluate_tool_call``)
--------------------------------------------
Fires on the SAME path as the content floor: only when the resolved decision is
an ALLOWED **outside** ``EXTERNAL_SEND`` (outside / client / vendor classes)
whose effective ceiling is ``autonomous``. Draft-for-review / confirm paths have
a human in the loop; ``external_send_internal`` is ops traffic to rostered
staff, not client-voice impersonation. Those are deliberately out of scope (the
authored-spec gate covers ``staff`` separately — ``shared.spec_gate``).

Fail behavior (fail-closed, ADR 0028 §4) — BOTH regimes
-------------------------------------------------------
Downgrade the send to draft-for-review — using the EXACT same block-directive
plumbing the content floor uses (a ``{"action": "block", ...}`` return that the
agent turns into a draft) — and write a ``VOICE_GATE_TRIGGERED`` audit row. The
action_type and row shape are UNCHANGED by the repoint; the new reason strings
ride the existing ``reason`` field (``no_spec`` | ``spec_not_read`` |
``spec_hash_mismatch`` alongside ``no_voice_spec_declared`` | ``gate_error``). ANY internal error in a bound gate
downgrades. The gate never silently passes.

Overlay-only (like the content floor, ADR 0031 §5). NOT mirrored into
ss-console's ``trust_ceiling.py`` adapter.
"""

import logging
from typing import Any

from shared import spec_manifest
from shared.audit_contract import CANONICAL_TOOL_CALL_KEY, agent_event_params
from shared.audit_contract import INSERT_SQL as _INSERT_SQL
from shared.audit_status import NoAuditWarner
from shared.customer_config import CustomerConfig
from shared.spec_gate import resolve_output_class, spec_read_instruction
from shared.spec_status import SPEC_STATUS

logger = logging.getLogger(__name__)


# Fail reasons (audit + the operator-facing draft message).
# Undeclared regime (the sample-transform fallback's replacement):
_REASON_NO_VOICE_SPEC_DECLARED = "no_voice_spec_declared"
# Spec regime (ss#2086 step 1):
_REASON_NO_SPEC = "no_spec"
_REASON_SPEC_NOT_READ = "spec_not_read"
_REASON_SPEC_HASH_MISMATCH = "spec_hash_mismatch"
#: This process cannot read the manifest, so an empty entry list proves nothing
#: (ss-console #2234). Distinct from `no_spec`: that one is the firm's authoring
#: gap, this one is our own fault, and only one of them is the firm's to fix.
_REASON_SPEC_UNPROVABLE = "spec_unprovable"
# Either regime:
_REASON_GATE_ERROR = "gate_error"


# ---------------------------------------------------------------------------
# Binding-regime resolution — the additive per-class decision
# ---------------------------------------------------------------------------

#: The class's declared spec governs (ss ADR 0083 binding).
REGIME_SPEC = "spec"
#: Nothing certifies the class's voice: the send downgrades (fail closed).
REGIME_UNDECLARED = "undeclared"

#: The one declaration value that selects the spec regime. ``none`` is the
#: other legal authored value and falls to the undeclared regime.
_EXPECTED = "expected"


def resolve_binding_regime(*, class_declaration: Any) -> str:
    """Which regime binds this (seat x resolved-output-class) send. PURE.

    * declaration is a mapping with ``voice_spec: expected`` -> ``REGIME_SPEC``;
    * any other state (absent class, absent or malformed ``output_classes``,
      ``voice_spec: none``) -> ``REGIME_UNDECLARED``, which downgrades.

    There is no unbound answer: every autonomous outside send is governed.
    """
    if isinstance(class_declaration, dict):
        if str(class_declaration.get("voice_spec", "")).strip().lower() == _EXPECTED:
            return REGIME_SPEC
    return REGIME_UNDECLARED


def _class_declaration(output_class: str | None) -> Any:
    """The seat's ``output_classes.<class>`` declaration, or ``None``.

    ``None`` means "no declaration I can positively confirm" (unresolved class,
    absent block, unreadable config) and ``resolve_binding_regime`` reads it as
    undeclared: the send downgrades, never a silently unbound gate.
    """
    if not output_class:
        return None
    try:
        return CustomerConfig.from_volume().output_classes.get(output_class)
    except Exception:  # noqa: BLE001 — unconfirmed ⇒ undeclared ⇒ downgrade
        logger.debug(
            "voice gate: output_classes unresolved; reading the class as undeclared",
            exc_info=True,
        )
        return None


# ---------------------------------------------------------------------------
# Audit emission — VOICE_GATE_TRIGGERED
#
# Mirrors the FABRICATION_FILTER_TRIGGERED writer in ``outbound.py``: one
# audit_log row via the shared D1Client + the canonical audit_log INSERT
# contract, preserving the trust/audit loose coupling (no cross-import of the
# audit plugin's hook surface). Best-effort RELATIVE TO THE DOWNGRADE — a write
# failure logs a warning but the draft downgrade still stands.
# ---------------------------------------------------------------------------


_AUDIT_CLIENT: Any = None
_AUDIT_CUSTOMER_SLUG: str | None = None
_AUDIT_WIRED: bool = False

_NO_AUDIT_WARNER = NoAuditWarner()


def _audit_client() -> tuple[Any, str | None]:
    """Lazily resolve ``(D1Client, customer_slug)``. Cached across calls.

    Returns ``(None, None)`` when the audit env is not configured — the gate
    still downgrades; the row is skipped (rate-limited WARNING per skip, #64).
    Tests reset the cache by restoring the module globals."""
    global _AUDIT_CLIENT, _AUDIT_CUSTOMER_SLUG, _AUDIT_WIRED
    if _AUDIT_WIRED:
        return _AUDIT_CLIENT, _AUDIT_CUSTOMER_SLUG
    _AUDIT_WIRED = True
    try:
        from shared.audit_client import audit_client_from_env
        from shared.secrets import require

        secrets_map = require("SMD_CUSTOMER_SLUG", "SMD_D1_AUDIT_BINDING")
        slug = secrets_map["SMD_CUSTOMER_SLUG"]
        _AUDIT_CLIENT = audit_client_from_env(customer_slug=slug)
        _AUDIT_CUSTOMER_SLUG = slug
    except Exception as exc:  # noqa: BLE001 — audit is best-effort vs the downgrade
        logger.debug("voice gate: audit client unconfigured (%s); downgrades won't emit a row", exc)
        _AUDIT_CLIENT = None
        _AUDIT_CUSTOMER_SLUG = None
    return _AUDIT_CLIENT, _AUDIT_CUSTOMER_SLUG


def _emit_voice_gate_audit(
    *,
    tool_name: str,
    reason: str,
    session_id: str,
    tool_call_id: str,
) -> None:
    """Write one ``VOICE_GATE_TRIGGERED`` row. Best-effort, never raises.

    Provenance only — never the draft body. Carries the fail ``reason`` so the
    audit review can tell a no-samples seat from a transform-skipped turn."""
    client, slug = _audit_client()
    if client is None or slug is None:
        _NO_AUDIT_WARNER.warn(
            logger, f"VOICE_GATE_TRIGGERED on tool={tool_name} (reason={reason}) not recorded"
        )
        return
    try:
        metadata: dict = {
            "voice_gate": True,
            "customer": slug,
            "tool": tool_name,
            "reason": reason,
        }
        if session_id:
            metadata["session_id"] = session_id
        if tool_call_id:
            metadata[CANONICAL_TOOL_CALL_KEY] = tool_call_id
        params = agent_event_params(action_type="VOICE_GATE_TRIGGERED", metadata=metadata)
        client.execute(_INSERT_SQL, *params)
    except Exception as exc:  # noqa: BLE001 — audit row is best-effort vs downgrade
        logger.warning(
            "voice gate: VOICE_GATE_TRIGGERED emission failed (tool=%s reason=%s err=%s); "
            "the draft downgrade still stands",
            tool_name,
            reason,
            exc,
        )


# ---------------------------------------------------------------------------
# The gate entry point — called from enforce.evaluate_tool_call
# ---------------------------------------------------------------------------


def _draft_message(reason: str) -> str:
    return (
        "Refused: this autonomous send is not certified to be in the principal's "
        f"authored voice (voice live-gate reason={reason}); routing to draft for "
        "human review instead of autonomous send (ADR 0028 §2). Create a draft instead."
    )


def _spec_draft_message(output_class: str, reason: str) -> str:
    if reason == _REASON_SPEC_NOT_READ:
        return (
            f"Refused: this seat declares an authored voice spec for the '{output_class}' "
            f"output class, and this turn did not read it. {spec_read_instruction(output_class)}, "
            "compose against it, then send — or create a draft for review. "
            "(voice live-gate, ss ADR 0083)"
        )
    return (
        f"Refused: the voice live-gate could not certify this '{output_class}' send "
        f"against its authored voice spec (reason={reason}); routing to draft for human "
        "review instead of autonomous send. Create a draft instead."
    )


def _spec_fail_reason(output_class: str) -> str:
    """Distinguish WHY the spec pass condition failed. Off the happy path only.

    ``was_read`` being False is the refusal; this probe only names it.

    Manifest unreadable ⇒ ``spec_unprovable``, and that check comes FIRST.
    Without it a lost ``SMD_SPEC_DIR`` reports as ``no_spec`` — the seat would
    claim the firm never installed a spec when the truth is the seat could not
    look, and the two want opposite responses (ss-console #2234).

    Then: no manifest entry for (class, voice) ⇒ ``no_spec``, a broken control.
    Entries exist but none verifies ⇒ ``spec_hash_mismatch`` (disk no longer
    matches what root recorded — reads as tamper). A verified spec existed and
    simply was not read ⇒ ``spec_not_read``.

    All four downgrade an OUTBOUND send to draft-for-review. This gate never
    fires on ``staff``, whose broken-control disposition changed on 2026-08-10
    and lives in ``shared.spec_gate``.
    """
    if spec_manifest.manifest_state() == spec_manifest.STATE_UNREADABLE:
        return _REASON_SPEC_UNPROVABLE
    entries = [e for e in spec_manifest.entries_for_class(output_class) if e.prop == "voice"]
    if not entries:
        return _REASON_NO_SPEC
    if not any(spec_manifest.verify(e) for e in entries):
        return _REASON_SPEC_HASH_MISMATCH
    return _REASON_SPEC_NOT_READ


def _check_spec_regime(
    *,
    output_class: str,
    tool_name: str,
    session_id: str,
    tool_call_id: str,
) -> dict | None:
    """The spec regime's pass/fail evaluation. The seat is BOUND on entry.

    Pass = the class's voice spec is installed, hash-matches the root-owned
    manifest, and was read this turn. ``SPEC_STATUS.was_read`` is all three at
    once: ``spec_read.observe_read`` only ever marks after
    ``shared.spec_manifest`` resolved the path to a manifest entry AND verified
    the bytes against the recorded digest — reusing that machinery, not
    duplicating it. Fail-closed (ADR 0028 §4): any evaluation error downgrades.
    """
    try:
        if SPEC_STATUS.was_read(session_id, output_class, "voice"):
            return None
        reason = _spec_fail_reason(output_class)
    except Exception:  # noqa: BLE001 — a bound-seat evaluation fault fails closed
        logger.exception(
            "voice gate: spec-regime evaluation failed for %s (%s); failing toward draft "
            "(ADR 0028 §4)",
            tool_name,
            output_class,
        )
        reason = _REASON_GATE_ERROR

    _emit_voice_gate_audit(
        tool_name=tool_name,
        reason=reason,
        session_id=session_id,
        tool_call_id=tool_call_id,
    )
    return {"action": "block", "message": _spec_draft_message(output_class, reason)}


def check_voice_gate(
    *,
    tool_name: str,
    action_class_value: str = "",
    session_id: str = "",
    tool_call_id: str = "",
) -> dict | None:
    """Voice live-gate on an allowed autonomous OUTSIDE send.

    The caller (``enforce.evaluate_tool_call``) invokes this ONLY when the
    decision is an allowed ``EXTERNAL_SEND`` whose effective ceiling is
    ``autonomous`` — this function resolves WHICH binding regime governs the
    send's output class (see ``resolve_binding_regime``) and runs that regime's
    pass/fail evaluation. Returns a draft-routing block directive
    ``{"action": "block", "message": ...}`` to downgrade, or ``None`` to let the
    autonomous send proceed.

    ``action_class_value`` is the resolved ``ActionClass`` value (post recipient
    reclassification); its output class comes from the same map the spec gate
    uses. An empty / unresolvable value yields no output class, which reads as
    undeclared and downgrades.

    Fail-closed (ADR 0028 §4): a bound seat that cannot certify its regime's
    pass condition this turn downgrades; any internal error downgrades."""
    output_class = resolve_output_class(action_class_value) if action_class_value else None
    regime = resolve_binding_regime(class_declaration=_class_declaration(output_class))
    if regime == REGIME_SPEC and output_class is not None:
        return _check_spec_regime(
            output_class=output_class,
            tool_name=tool_name,
            session_id=session_id,
            tool_call_id=tool_call_id,
        )

    # Undeclared: nothing certifies this class's voice, so the send is a draft
    # (the sample-transform fallback's fail-closed replacement, ADR 0083 decision 6).
    reason = _REASON_NO_VOICE_SPEC_DECLARED
    _emit_voice_gate_audit(
        tool_name=tool_name,
        reason=reason,
        session_id=session_id,
        tool_call_id=tool_call_id,
    )
    return {"action": "block", "message": _draft_message(reason)}


__all__ = [
    "REGIME_SPEC",
    "REGIME_UNDECLARED",
    "check_voice_gate",
    "resolve_binding_regime",
]
