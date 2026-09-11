"""The five ``medchron_*`` broker verbs (routine 11, ss#2614).

Registered on the verb table in ``verbs.py``, which declares each verb's
peer classes (the same five rows as the list below) and checks them before
handing the request here; ``_gate`` below re-checks with this module's own
message, as defence in depth.

Peer gating, per verb:

    medchron_job_submit    gateway PID (an agent tool call) OR uid 0 (root on
                           the box: the rehearsal, the Captain-side skill)
    medchron_job_status    gateway PID, agent uid, or uid 0
    medchron_allowance     gateway PID, agent uid, or uid 0
    medchron_job_list      agent uid (the runtime-read gate process) or uid 0
    medchron_job_record    uid 0 only (the runner daemon)

Every writing verb pins the audit type its transition maps to (``AUDIT_TYPE``),
so none can forge another row. Audit rows carry counts, digests and ids —
never the envelope, never a name.
"""

from __future__ import annotations

import json
from typing import Any

from .cycle_window import AnchorInvalid
from .medchron_ledger import (
    ALLOWANCE_KEY,
    AUDIT_TYPE,
    STATES,
    EnvelopeError,
    MedchronLedger,
    allowance_from_customer_yaml,
    cycle_from_customer_yaml,
    validate_envelope,
)

VERBS = ("medchron_job_submit", "medchron_job_status", "medchron_allowance", "medchron_job_list", "medchron_job_record")


def medchron_dispatch(
    verbs: MedchronVerbs | None, action: str, request: dict[str, Any], peer_pid: int, peer_uid: int | None
) -> dict[str, Any]:
    """The server's one-line door. A broker built without the verbs (tests via
    ``__new__``, or an image whose entrypoint predates the queue) refuses."""
    if verbs is None:
        raise ValueError("medchron verbs not configured on this broker")
    return verbs.handle(action, request, peer_pid, peer_uid)


class MedchronVerbs:
    def __init__(
        self,
        ledger: MedchronLedger | None,
        *,
        customer_yaml: str,
        customer_slug: str,
        audit_append: Any,
        gateway_pid: int,
        resolve_agent_uid: Any,
    ) -> None:
        self.ledger = ledger
        self.customer_yaml = customer_yaml
        self.customer_slug = customer_slug
        self._audit_append = audit_append
        self.gateway_pid = gateway_pid
        self._resolve_agent_uid = resolve_agent_uid

    @classmethod
    def build(cls, broker: Any, *, audit_db_path: str | None, queue_dir: str | None) -> MedchronVerbs:
        """From a live Broker. The ledger is enabled only when the audit ledger
        is (a job that cannot be recorded must not be queued) and the entrypoint
        exported the queue dir; otherwise the verbs answer fail-closed."""
        ledger = None
        if audit_db_path and queue_dir and broker.ledger is not None:
            ledger = MedchronLedger(audit_db_path, queue_dir)
        return cls(
            ledger,
            customer_yaml=str(broker.customer_path),
            customer_slug=broker.customer_slug,
            audit_append=lambda row: broker.ledger.append(row),
            gateway_pid=broker.gateway_pid,
            resolve_agent_uid=broker._resolve_agent_uid,
        )

    # -- gates ---------------------------------------------------------------
    def _is_agent(self, peer_uid: int | None) -> bool:
        agent_uid = self._resolve_agent_uid()
        return agent_uid is not None and peer_uid == agent_uid

    def _gate(self, action: str, peer_pid: int, peer_uid: int | None) -> None:
        is_root = peer_uid == 0
        is_gateway = peer_pid == self.gateway_pid
        ok = {
            "medchron_job_submit": is_gateway or is_root,
            "medchron_job_status": is_gateway or is_root or self._is_agent(peer_uid),
            "medchron_allowance": is_gateway or is_root or self._is_agent(peer_uid),
            "medchron_job_list": is_root or self._is_agent(peer_uid),
            "medchron_job_record": is_root,
        }.get(action, False)
        if not ok:
            raise PermissionError(f"{action} is not permitted for this caller")

    def _audit(self, action_type: str, metadata: dict[str, Any], matter_ref: str | None) -> None:
        self._audit_append(
            {
                "action_type": action_type,
                "actor": "workspace-broker",
                "actor_role": "broker",
                "skill_name": "medical-chronology-maintainer",
                "matter_ref": matter_ref,
                "metadata": json.dumps(metadata, sort_keys=True, separators=(",", ":")),
            }
        )

    # -- dispatch ------------------------------------------------------------
    def handle(self, action: str, request: dict[str, Any], peer_pid: int, peer_uid: int | None) -> dict[str, Any]:
        self._gate(action, peer_pid, peer_uid)
        if self.ledger is None:
            raise ValueError("medchron ledger not configured on this broker")
        if action == "medchron_allowance":
            exclude = str(request.get("exclude_job_id") or "") or None
            try:
                anchor, effective_from = cycle_from_customer_yaml(self.customer_yaml)
            except AnchorInvalid as exc:
                # Authored-but-unreadable is refused, never quietly demoted to
                # the calendar month: a firm metered on a window it did not
                # author is the harm, and the key it must fix is named.
                return {"ok": True, "authored": False, "invalid": True, "reason": str(exc)}
            return {
                "ok": True,
                **self.ledger.allowance(
                    allowance_from_customer_yaml(self.customer_yaml),
                    exclude_job_id=exclude,
                    anchor_day=anchor,
                    effective_from=effective_from,
                ),
            }
        if action == "medchron_job_status":
            job_id = str(request.get("job_id") or "")
            if job_id:
                row = self.ledger.read(job_id)
                return {"ok": True, "job": self.ledger.project(row) if row else None}
            return {"ok": True, "jobs": [self.ledger.project(r) for r in self.ledger.list_recent(20)]}
        if action == "medchron_job_list":
            return {"ok": True, "jobs": [self.ledger.project(r) for r in self.ledger.list_recent(200)]}
        if action == "medchron_job_submit":
            return self._submit(request)
        if action == "medchron_job_record":
            return self._record(request)
        raise ValueError(f"unsupported medchron action: {action}")

    def _submit(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            envelope = validate_envelope(request.get("envelope") or {})
        except EnvelopeError as exc:
            return {"ok": True, "accepted": False, "reason": str(exc)}
        try:
            anchor, effective_from = cycle_from_customer_yaml(self.customer_yaml)
        except AnchorInvalid as exc:
            return {"ok": True, "accepted": False, "reason": str(exc)}
        state = self.ledger.allowance(
            allowance_from_customer_yaml(self.customer_yaml),
            anchor_day=anchor,
            effective_from=effective_from,
        )
        if not state["authored"]:
            # The key is named because the seat that hits this is usually one
            # whose customer.yaml still carries only the pre-2026-09-09
            # document key: a firm cannot fix a key it is not told about.
            return {
                "ok": True,
                "accepted": False,
                "reason": "no page allowance is authored for this seat "
                f"({ALLOWANCE_KEY}); nothing can be submitted",
            }
        if state["remaining"] <= 0:
            return {
                "ok": True,
                "accepted": False,
                "reason": f"the page allowance is spent ({state['used']:,} of "
                f"{state['allowance']:,} pages in {state['month']}); the Operator stops here "
                "and surfaces the item",
            }
        job_id = self.ledger.submit(envelope, remaining=state["remaining"])
        self._audit(
            AUDIT_TYPE["submitted"],
            {
                "job_id": job_id,
                "matter_number": envelope["matter"]["number"],
                "units": len(envelope["units"]),
                "allowance_remaining_pages": state["remaining"],
                "allowance_remaining_documents": state["remaining"],
                "requested_by": envelope.get("requested_by"),
                "request_ref": envelope.get("request_ref"),
            },
            envelope["matter"]["id"],
        )
        return {
            "ok": True,
            "accepted": True,
            "job_id": job_id,
            "state": "submitted",
            "unit": state["unit"],
            "allowance_remaining_pages": state["remaining"],
            # Kept for one release: the overlay's pinned tool relays this
            # key by name (ADR 0087 amendment).
            "allowance_remaining_documents": state["remaining"],
        }

    def _record(self, request: dict[str, Any]) -> dict[str, Any]:
        job_id = str(request.get("job_id") or "")
        state = str(request.get("state") or "")
        fields = dict(request.get("fields") or {})
        if not job_id or state not in STATES or not isinstance(fields, dict):
            raise ValueError("medchron_job_record requires job_id, a known state, and a fields object")
        wake = fields.pop("wake", None)  # audit-only metadata (a lost deliver wake), never a column
        row = self.ledger.record(job_id, state, fields)
        meta = {
            "job_id": job_id,
            "state": state,
            "documents": row["documents"],
            "pages": row["pages"],
            "cents": row["cents"],
            "reason": row["reason"],
            "folder_id": row["folder_id"],
        }
        if isinstance(wake, dict):
            meta["wake"] = {k: wake.get(k) for k in ("wake_failed", "outcome")}
        if state == "delivered" and isinstance(fields.get("delivery"), dict):
            d = fields["delivery"]
            meta["files"] = [
                {"name": f.get("name"), "sha256": f.get("sha256"), "bytes": f.get("bytes")}
                for f in (d.get("files") or [])
                if isinstance(f, dict)
            ][:50]
        self._audit(AUDIT_TYPE[state], meta, row["matter_id"])
        return {"ok": True, "job": self.ledger.project(row)}
