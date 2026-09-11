"""The audit-ledger appenders: one pinned ``action_type`` per verb.

The broker's whole discipline on the append-only ledger is that a WRITING
verb pins exactly one ``action_type``, so a caller holding one verb cannot
forge another verb's row. Each function here is one such door. The peer gate
(who may call it) is declared on the verb table in ``verbs.py`` and enforced
there BEFORE any of these run; nothing below re-checks the caller.

Row provenance, per verb:

``suppressed_wake_append`` (ADR 0021 Stream B)
    The ONE verb reachable by cron pre_run children (agent uid, non-gateway
    PID) for the suppressed half of a gated wake. Locked to SUPPRESSED_WAKE.
``emitted_wake_append`` (ss-console #2253)
    The WAKE half of the same gate. The four gated cron skills wrote a row when
    they suppressed and nothing when they woke, so the one tick that mattered
    was the one tick with no row; on 2026-08-10 a fabricated escalation email
    was discoverable only by reading the mailbox. A SEPARATE verb rather than
    a widened suppressed_wake_append, so each still pins one action_type. The
    caller swallows this verb's failures (a wake is never gated on its own
    audit row), which is exactly why validation lives here and not there.
``webhook_suppressed_append`` (ss-console #1791)
    The webhook gate (overlay hermes-smd-webhook-gate) records
    WEBHOOK_SUPPRESSED for an excluded delivery, from the agent uid on a
    non-gateway PID, which the gateway-gated ``audit_append`` refuses.
``correction_propose`` (ss-console #2091, ADR 0083 §4)
    The Operator CAPTURES a correction a customer stated and never applies
    one. THE ROW IS REBUILT, NOT FORWARDED: ``build_correction_row`` reads a
    bounded field set off the request, so a field the caller invents is
    dropped rather than stored, and ``status`` is a broker-side constant.
    Nothing here reaches a spec; promotion is portal-side by a Named
    Administrator.
``audit_append`` (OP-P1-4)
    The generic append, gateway-PID-gated: only the gateway may write;
    execute_code/terminal children get a different peer PID. The broker
    stamps id/ts; there is no update/delete/drop verb in this IPC surface,
    and that absence is the append-only guarantee.

Every append flows through the hash-chained ``LedgerWriter`` (broker stamps
id/ts; chain intact).
"""

from __future__ import annotations

from typing import Any

from .corrections import PROPOSED_STATUS, build_correction_row


def _ledger(broker: Any) -> Any:
    if broker.ledger is None:
        raise ValueError("audit ledger not configured on this broker")
    return broker.ledger


def _pinned_row(action: str, request: dict[str, Any], action_type: str) -> dict[str, Any]:
    row = request.get("row")
    if not isinstance(row, dict):
        raise ValueError(f"{action} requires a 'row' object")
    if row.get("action_type") != action_type:
        raise ValueError(f"{action} only accepts action_type={action_type}")
    return row


def _append_pinned(broker: Any, action: str, request: dict[str, Any], action_type: str) -> dict[str, Any]:
    ledger = _ledger(broker)
    row = _pinned_row(action, request, action_type)
    return {"ok": True, "id": ledger.append(row)}


def suppressed_wake_append(broker: Any, action: str, request: dict[str, Any], _pid: int, _uid: int | None) -> dict[str, Any]:
    return _append_pinned(broker, action, request, "SUPPRESSED_WAKE")


def emitted_wake_append(broker: Any, action: str, request: dict[str, Any], _pid: int, _uid: int | None) -> dict[str, Any]:
    return _append_pinned(broker, action, request, "EMITTED_WAKE")


def webhook_suppressed_append(broker: Any, action: str, request: dict[str, Any], _pid: int, _uid: int | None) -> dict[str, Any]:
    return _append_pinned(broker, action, request, "WEBHOOK_SUPPRESSED")


def correction_propose(broker: Any, _action: str, request: dict[str, Any], _pid: int, _uid: int | None) -> dict[str, Any]:
    ledger = _ledger(broker)
    row = build_correction_row(request.get("proposal"))
    return {"ok": True, "id": ledger.append(row), "status": PROPOSED_STATUS}


def audit_append(broker: Any, _action: str, request: dict[str, Any], _pid: int, _uid: int | None) -> dict[str, Any]:
    ledger = _ledger(broker)
    row = request.get("row")
    if not isinstance(row, dict):
        raise ValueError("audit_append requires a 'row' object")
    return {"ok": True, "id": ledger.append(row)}
