"""The B1 durable-job control-plane verbs.

Gateway-PID-gated on the verb table (``verbs.py``): only the gateway, which
hosts the worker thread, reaches these; execute_code/terminal children get a
different peer PID and are refused, so the agent cannot claim leases, raise
budgets, or flip job status directly.

Time is stamped server-side (``now_and_lease_cutoff``): the broker is the
single clock of record for lease timing, so callers never pass time over the
wire. The verbs past ``job_claim`` are epoch-fenced: a stale worker's write is
a no-op (the ledger checks ``lease_epoch`` in the WHERE clause).
"""

from __future__ import annotations

from typing import Any

from .job_ledger import LEASE_TTL_SECONDS, now_and_lease_cutoff

VERBS: tuple[str, ...] = (
    "job_create",
    "job_list_claimable",
    "job_list",
    "job_read",
    "job_cancel",
    "job_claim",
    "job_heartbeat",
    "job_record",
    "job_idem_begin",
    "job_idem_complete",
)


def _ledger(broker: Any) -> Any:
    if broker.job_ledger is None:
        raise ValueError("job ledger not configured on this broker")
    return broker.job_ledger


def _job_id(action: str, request: dict[str, Any]) -> str:
    job_id = str(request.get("job_id") or "")
    if not job_id:
        raise ValueError(f"{action} requires job_id")
    return job_id


def _epoch(action: str, request: dict[str, Any]) -> int:
    epoch = request.get("lease_epoch")
    if not isinstance(epoch, int):
        raise ValueError(f"{action} requires an integer lease_epoch")
    return epoch


def _step_key(action: str, request: dict[str, Any]) -> str:
    step_key = str(request.get("step_key") or "")
    if not step_key:
        raise ValueError(f"{action} requires step_key")
    return step_key


def job_create(broker: Any, _action: str, request: dict[str, Any], _pid: int, _uid: int | None) -> dict[str, Any]:
    row = request.get("row")
    if not isinstance(row, dict):
        raise ValueError("job_create requires a 'row' object")
    return {"ok": True, "id": _ledger(broker).create(row)}


def job_list_claimable(
    broker: Any, _action: str, _request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    now, cutoff = now_and_lease_cutoff(LEASE_TTL_SECONDS)
    return {"ok": True, "jobs": _ledger(broker).list_claimable(now, cutoff)}


def job_list(broker: Any, _action: str, _request: dict[str, Any], _pid: int, _uid: int | None) -> dict[str, Any]:
    # Observability read: every job row (terminal + live), newest first. Powers
    # the console's ``jobs`` runtime-read kind so the worker is verifiable end
    # to end over HTTPS. Read-only; no lease filter.
    return {"ok": True, "jobs": _ledger(broker).list_all()}


def job_read(broker: Any, action: str, request: dict[str, Any], _pid: int, _uid: int | None) -> dict[str, Any]:
    return {"ok": True, "job": _ledger(broker).read(_job_id(action, request))}


def job_cancel(broker: Any, action: str, request: dict[str, Any], _pid: int, _uid: int | None) -> dict[str, Any]:
    # ``ok`` == request processed; ``result`` == the verb's boolean outcome.
    # Keeping them separate lets a legitimately-false outcome (a fenced-out
    # record) return False instead of reading as a transport refusal.
    return {"ok": True, "result": _ledger(broker).request_cancel(_job_id(action, request))}


def job_claim(broker: Any, action: str, request: dict[str, Any], _pid: int, _uid: int | None) -> dict[str, Any]:
    ledger = _ledger(broker)
    job_id = _job_id(action, request)
    worker_id = str(request.get("worker_id") or "")
    if not worker_id:
        raise ValueError("job_claim requires worker_id")
    now, cutoff = now_and_lease_cutoff(LEASE_TTL_SECONDS)
    return {"ok": True, "lease_epoch": ledger.claim(job_id, worker_id, now, cutoff)}


def job_heartbeat(broker: Any, action: str, request: dict[str, Any], _pid: int, _uid: int | None) -> dict[str, Any]:
    ledger = _ledger(broker)
    job_id = _job_id(action, request)
    epoch = _epoch(action, request)
    now, _ = now_and_lease_cutoff(LEASE_TTL_SECONDS)
    return {"ok": True, "result": ledger.heartbeat(job_id, epoch, now)}


def job_record(broker: Any, action: str, request: dict[str, Any], _pid: int, _uid: int | None) -> dict[str, Any]:
    ledger = _ledger(broker)
    job_id = _job_id(action, request)
    epoch = _epoch(action, request)
    fields = request.get("fields")
    if not isinstance(fields, dict):
        raise ValueError("job_record requires a 'fields' object")
    return {"ok": True, "result": ledger.record(job_id, epoch, fields)}


def job_idem_begin(broker: Any, action: str, request: dict[str, Any], _pid: int, _uid: int | None) -> dict[str, Any]:
    ledger = _ledger(broker)
    job_id = _job_id(action, request)
    epoch = _epoch(action, request)
    step_key = _step_key(action, request)
    return {"ok": True, "decision": ledger.idempotency_begin(job_id, step_key, epoch)}


def job_idem_complete(broker: Any, action: str, request: dict[str, Any], _pid: int, _uid: int | None) -> dict[str, Any]:
    ledger = _ledger(broker)
    job_id = _job_id(action, request)
    epoch = _epoch(action, request)
    step_key = _step_key(action, request)
    return {"ok": True, "result": ledger.idempotency_complete(job_id, step_key, epoch)}


def unknown_job_action(
    broker: Any, action: str, _request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    """A ``job_*`` name the table does not carry: the old prefix dispatcher's
    vocabulary, kept so a misspelt job verb still names itself in the reply."""
    _ledger(broker)
    raise ValueError(f"unsupported job action: {action}")
