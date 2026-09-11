"""The reviewed-Workspace verbs: ``authorize`` mints a payload-bound grant,
``execute`` consumes it and journals a signed receipt.

Gateway-PID-gated on the verb table (``verbs.py``). Both verbs, and the
unknown-action fall-through, validate the request in the same order the
original dispatcher did: operation and payload shape first, support second,
so the reply vocabulary a caller sees is unchanged.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from .canon import canonical


def _operation_and_digest(broker: Any, request: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    operation = str(request.get("operation") or "")
    payload = request.get("payload")
    if not operation.startswith("workspace_") or not isinstance(payload, dict):
        raise ValueError("operation and object payload are required")
    if not broker.operations.supports(operation):
        raise ValueError(f"unsupported Workspace operation: {operation}")
    return operation, payload, hashlib.sha256(canonical(payload)).hexdigest()


def authorize(broker: Any, _action: str, request: dict[str, Any], _pid: int, _uid: int | None) -> dict[str, Any]:
    operation, _payload, digest = _operation_and_digest(broker, request)
    grant = broker.grants.mint(
        {
            "customer_slug": broker.customer_slug,
            "operation": operation,
            "payload_digest": digest,
            "session_id": str(request.get("session_id") or ""),
            "tool_call_id": str(request.get("tool_call_id") or ""),
        }
    )
    return {"ok": True, "grant": grant, "payload_digest": digest}


def execute(broker: Any, _action: str, request: dict[str, Any], _pid: int, _uid: int | None) -> dict[str, Any]:
    operation, payload, digest = _operation_and_digest(broker, request)
    claims = broker.grants.consume(
        str(request.get("grant") or ""),
        {
            "customer_slug": broker.customer_slug,
            "operation": operation,
            "payload_digest": digest,
        },
    )
    started = time.perf_counter()
    result = broker.operations.dispatch(operation, payload)
    receipt = {
        "customer_slug": broker.customer_slug,
        "operation": operation,
        "payload_digest": digest,
        "nonce": claims["nonce"],
        "executed_at": int(time.time()),
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
    }
    receipt["signature"] = broker.grants.sign_receipt(receipt)
    journal = broker.credential_path.parent / "execution-receipts.jsonl"
    with journal.open("ab") as handle:
        handle.write(canonical(receipt) + b"\n")
    journal.chmod(0o600)
    return {"ok": True, "result": result, "receipt": receipt}


def unknown_action(broker: Any, _action: str, request: dict[str, Any], _pid: int, _uid: int | None) -> dict[str, Any]:
    """An action the table does not carry. Validated like a Workspace verb
    first (the original dispatcher's fall-through order), then refused by
    name, so ``audit_update`` and friends answer exactly as they always have:
    there is no update or delete verb, and a request shaped like one gets the
    same "operation and object payload are required" a malformed Workspace
    call gets."""
    _operation_and_digest(broker, request)
    raise ValueError("unsupported broker action")
