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

import json
from typing import Any

from .broker_context import BrokerContext
from .corrections import PROPOSED_STATUS, build_correction_row


def _ledger(broker: BrokerContext) -> Any:
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


def _append_pinned(broker: BrokerContext, action: str, request: dict[str, Any], action_type: str) -> dict[str, Any]:
    ledger = _ledger(broker)
    row = _pinned_row(action, request, action_type)
    return {"ok": True, "id": ledger.append(row)}


def suppressed_wake_append(
    broker: BrokerContext, action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    return _append_pinned(broker, action, request, "SUPPRESSED_WAKE")


def emitted_wake_append(
    broker: BrokerContext, action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    return _append_pinned(broker, action, request, "EMITTED_WAKE")


def webhook_suppressed_append(
    broker: BrokerContext, action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    return _append_pinned(broker, action, request, "WEBHOOK_SUPPRESSED")


def correction_propose(
    broker: BrokerContext, _action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    ledger = _ledger(broker)
    row = build_correction_row(request.get("proposal"))
    return {"ok": True, "id": ledger.append(row), "status": PROPOSED_STATUS}


def audit_append(
    broker: BrokerContext, _action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    ledger = _ledger(broker)
    row = request.get("row")
    if not isinstance(row, dict):
        raise ValueError("audit_append requires a 'row' object")
    return {"ok": True, "id": ledger.append(row)}


# ``smokeball_write_append`` (A&P, 2026-09-26)
#     Every write to a firm's Smokeball is recorded here BEFORE it is sent, by the
#     connector client itself (operator/connectors/smokeball/smokeball_connector/
#     write_record.py), whichever process holds it: the Operator's MCP server, a
#     script run over seat-probe, the boot/connect webhook reconciler. On
#     2026-09-25 SMD filed 52 documents on 31 of the firm's matters by hand with
#     no row at all; this verb is how that becomes impossible, because the client
#     refuses a write whose intent row did not land. REBUILT, NOT FORWARDED, like
#     correction_propose: only the bounded fields below are read off the request,
#     and the peer's uid and pid are stamped by the broker, so the row says which
#     process wrote even when the caller's own account of itself is wrong.
_SMOKEBALL_WRITE_PHASES = frozenset({"intent", "result"})
_SMOKEBALL_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _bounded(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def build_smokeball_write_row(row: Any, peer_pid: int, peer_uid: int | None) -> dict[str, Any]:
    if not isinstance(row, dict) or row.get("action_type") != "SMOKEBALL_WRITE":
        raise ValueError("smokeball_write_append only accepts action_type=SMOKEBALL_WRITE")
    phase = row.get("phase")
    if phase not in _SMOKEBALL_WRITE_PHASES:
        raise ValueError("smokeball_write_append requires phase 'intent' or 'result'")
    method = str(row.get("method") or "").upper()
    if method not in _SMOKEBALL_WRITE_METHODS:
        raise ValueError("smokeball_write_append records writes only (POST, PUT, PATCH, DELETE)")
    actor = _bounded(row.get("actor"), 200)
    path = _bounded(row.get("path"), 300)
    write_id = _bounded(row.get("write_id"), 64)
    if not actor or not path or not write_id:
        raise ValueError("smokeball_write_append requires actor, path and write_id")
    status = row.get("status")
    metadata = {
        "write_id": write_id,
        "phase": phase,
        "method": method,
        "path": path,
        "status": status if isinstance(status, int) else None,
        "returned_id": _bounded(row.get("returned_id"), 80),
        "error": _bounded(row.get("error"), 300),
        "peer_pid": peer_pid,
        "peer_uid": peer_uid,
    }
    return {
        "action_type": "SMOKEBALL_WRITE",
        "actor": actor,
        # The portal's role vocabulary (src/lib/portal/operator/audit.ts): the
        # Operator is the agent; anyone else writing directly is SMD's side.
        "actor_role": "agent" if actor == "operator" else "captain",
        "matter_ref": _bounded(row.get("matter_id"), 80),
        "input_digest": _bounded(row.get("body_sha256"), 64),
        "metadata": json.dumps({k: v for k, v in metadata.items() if v is not None}, sort_keys=True),
    }


def smokeball_write_append(
    broker: BrokerContext, _action: str, request: dict[str, Any], pid: int, uid: int | None
) -> dict[str, Any]:
    ledger = _ledger(broker)
    return {"ok": True, "id": ledger.append(build_smokeball_write_row(request.get("row"), pid, uid))}
