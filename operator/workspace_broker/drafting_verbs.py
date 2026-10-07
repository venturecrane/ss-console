"""The ``drafting_*`` broker verbs: the request edge of the drafting job (a
mediation brief, propounded discovery, discovery responses, a memo or a
deposition outline a firm administrator asks the Operator to draft).

A parallel copy of ``demand_verbs`` minus the re-run, registered the same way:
``verbs.py`` declares each verb's peer classes and checks them before handing
the request here, and asserts at import that its drafting rows and ``VERBS``
below agree.

Peer gating, per verb (identical to demand):

    drafting_job_submit    gateway PID (the agent's tool call; the overlay sets
                           requested_by / request_ref from the turn's verified
                           inbound, never from the model) OR uid 0
    drafting_job_status    gateway PID, agent uid, or uid 0. Root gets the full
                           row; everyone else the projection.
    drafting_allowance     gateway PID, agent uid, or uid 0.
    drafting_job_record    uid 0 only (the runner daemon)
    drafting_job_resume    uid 0 only, with NO agent tool.

THE SUBMIT CHECKS, in order, none of which spends anything:
1. the envelope is exactly ``drafting_ledger.validate_envelope``'s shape;
2. the skill ``document-drafter`` is LISTED and ENABLED on this seat;
3. the document class is in the skill's ``enabled_classes`` (a class not
   listed is refused with a sentence the skill relays to the requester);
4. a per-cycle allowance is authored (``drafting_allowance_per_cycle``);
5. the requester is a Named Administrator (``scope.admins``);
6. the filing target is the matter itself or the authored rehearsal matter;
7. the cycle's allowance has a draft left;
8. no unfinished draft of the same class on the matter (re-checked in the
   ledger's write transaction, with the request-email duplicate).

Audit rows carry ids, counts, classes and states, never the request text.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .broker_context import BrokerContext
from .cycle_window import AnchorInvalid
from .demand_verbs import _resolve_request_graph_id, operator_library_number
from .drafting_ledger import (
    ALLOWANCE_KEY,
    AUDIT_TYPE,
    CLASS_LABEL,
    CLASSES_KEY,
    DOCUMENT_CLASSES,
    REPORT_LISTS,
    SKILL_NAME,
    STATES,
    DraftingLedger,
    EnvelopeError,
    SubmitRefused,
    merged_delivery,
    validate_envelope,
)
from .medchron_ledger import admins_from_customer_yaml, cycle_from_customer_yaml

#: The audit types, spelled as literals here so a reader (and the producer
#: manifest's token check) finds them in this file; pinned to the ledger's.
AUDIT_TYPES = {
    "submitted": "DRAFTING_JOB_SUBMITTED",
    "running": "DRAFTING_JOB_RUNNING",
    "held": "DRAFTING_JOB_HELD",
    "delivered": "DRAFTING_JOB_DELIVERED",
    "failed": "DRAFTING_JOB_FAILED",
}
#: A person asked the runner to re-run a failed job. NOT a transition.
RESUME_AUDIT_TYPE = "DRAFTING_JOB_RESUME_REQUESTED"
if AUDIT_TYPES != AUDIT_TYPE:
    raise RuntimeError("drafting_verbs.AUDIT_TYPES and drafting_ledger.AUDIT_TYPE disagree")

#: The skill setting naming the library matter's id (the same key and value
#: the demand lane authors), the one matter a rehearsal may file on.
REHEARSAL_MATTER_KEY = "rehearsal_matter_id"

_PACIFIC = ZoneInfo("America/Los_Angeles")

VERBS = (
    "drafting_job_submit",
    "drafting_job_status",
    "drafting_allowance",
    "drafting_job_record",
    "drafting_job_resume",
)


def drafting_skill_settings(path: str | Path) -> dict[str, Any] | None:
    """The drafting skill's authored settings, or None unless the seat LISTS
    the skill and does not disable it. Omission is refusal (ADR 0035)."""
    try:
        import yaml

        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - an unreadable seat config enables nothing
        return None
    for persona in doc.get("personas") or []:
        for skill in (persona or {}).get("skills") or []:
            if not isinstance(skill, dict) or skill.get("name") != SKILL_NAME:
                continue
            if skill.get("enabled") is False:
                return None
            settings = skill.get("settings")
            return settings if isinstance(settings, dict) else {}
    return None


def allowance_of(settings: dict[str, Any] | None) -> int | None:
    value = (settings or {}).get(ALLOWANCE_KEY)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def enabled_classes_of(settings: dict[str, Any] | None) -> frozenset[str]:
    """The classes the seat switched on. Authored as a comma-separated string
    (skill settings are scalars, ADR 0075); a YAML list is read the same way.
    Unauthored is none; an unknown name switches nothing on."""
    value = (settings or {}).get(CLASSES_KEY)
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return frozenset()
    return frozenset(c.strip() for c in value if isinstance(c, str) and c.strip() in DOCUMENT_CLASSES)


class DraftingVerbs:
    def __init__(self, ledger: DraftingLedger | None, *, customer_yaml: str, audit_append: Any) -> None:
        self.ledger = ledger
        self.customer_yaml = customer_yaml
        self._audit_append = audit_append

    @classmethod
    def build(cls, broker: BrokerContext, *, audit_db_path: str | None, queue_dir: str | None) -> DraftingVerbs:
        """Enabled only when the audit ledger is and the entrypoint exported
        the drafting queue dir (SMD_DRAFTING_QUEUE_DIR)."""
        writer = broker.ledger
        ledger = (
            DraftingLedger(audit_db_path, queue_dir) if audit_db_path and queue_dir and writer is not None else None
        )

        def audit_append(row: dict[str, Any]) -> Any:
            if writer is None:
                raise ValueError("drafting verbs have no audit ledger on this broker")
            return writer.append(row)

        return cls(ledger, customer_yaml=str(broker.customer_path), audit_append=audit_append)

    @property
    def _db(self) -> DraftingLedger:
        if self.ledger is None:
            raise ValueError("drafting ledger not configured on this broker")
        return self.ledger

    def _audit(self, action_type: str, metadata: dict[str, Any], matter_ref: str | None) -> None:
        self._audit_append(
            {
                "action_type": action_type,
                "actor": "workspace-broker",
                "actor_role": "broker",
                "skill_name": SKILL_NAME,
                "matter_ref": matter_ref,
                "metadata": json.dumps(metadata, sort_keys=True, separators=(",", ":")),
            }
        )

    # -- dispatch ------------------------------------------------------------
    def handle(self, action: str, request: dict[str, Any], peer_uid: int | None) -> dict[str, Any]:
        if self.ledger is None:
            raise ValueError("drafting ledger not configured on this broker")
        if action == "drafting_allowance":
            return self._allowance(request)
        if action == "drafting_job_status":
            return self._status(request, full=peer_uid == 0)
        if action == "drafting_job_submit":
            return self._submit(request)
        if action == "drafting_job_record":
            return self._record(request)
        if action == "drafting_job_resume":
            return self._resume(request)
        raise ValueError(f"unsupported drafting action: {action}")

    # -- allowance ------------------------------------------------------------
    def _allowance(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            anchor, effective_from = cycle_from_customer_yaml(self.customer_yaml)
        except AnchorInvalid as exc:
            return {"ok": True, "authored": False, "invalid": True, "reason": str(exc)}
        exclude = str(request.get("exclude_job_id") or "")
        settings = drafting_skill_settings(self.customer_yaml)
        state = self._db.allowance(
            allowance_of(settings), anchor_day=anchor, effective_from=effective_from, exclude=exclude
        )
        month, cents = self._cents_this_month(exclude)
        return {
            "ok": True,
            **state,
            "enabled_classes": sorted(enabled_classes_of(settings)),
            "cents_used": cents,
            "cents_month": month,
        }

    def _cents_this_month(self, exclude: str, now: datetime | None = None) -> tuple[str, int]:
        """("YYYY-MM", cents) written in the current Pacific calendar month,
        without ``exclude`` (the runner's monthly spend budget's window)."""
        local = (now or datetime.now(timezone.utc)).astimezone(_PACIFIC)
        start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        nxt = (start + timedelta(days=32)).replace(day=1)

        def utc(dt: datetime) -> str:
            wall = datetime(dt.year, dt.month, 1, tzinfo=_PACIFIC)
            return wall.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        return start.strftime("%Y-%m"), self._db.spend_between(utc(start), utc(nxt), exclude)

    # -- status ---------------------------------------------------------------
    def _status(self, request: dict[str, Any], *, full: bool) -> dict[str, Any]:
        job_id = str(request.get("job_id") or "")
        if job_id:
            row = self._db.read(job_id)
            if row is None:
                return {"ok": True, "job": None}
            return {"ok": True, "job": row if full else DraftingLedger.project(row)}
        return {"ok": True, "jobs": [DraftingLedger.project(r) for r in self._db.recent(20)]}

    # -- submit ---------------------------------------------------------------
    def _filing_target_ok(self, envelope: dict[str, Any], settings: dict[str, Any]) -> bool:
        """The job files on the matter it reads, or on the authored rehearsal
        matter (its id in the skill's settings AND the firm's library number)."""
        file_to = envelope["file_to"]
        if file_to is None or file_to["id"] == envelope["matter"]["id"]:
            return True
        library = operator_library_number(self.customer_yaml)
        library_id = str(settings.get(REHEARSAL_MATTER_KEY) or "").strip().lower()
        return bool(
            library and library_id and file_to["number"] == library and file_to["id"].strip().lower() == library_id
        )

    def _submit(self, request: dict[str, Any]) -> dict[str, Any]:
        def refused(reason: str, **extra: Any) -> dict[str, Any]:
            return {"ok": True, "accepted": False, "reason": reason, **extra}

        try:
            envelope = validate_envelope(request.get("envelope") or {})
        except EnvelopeError as exc:
            return refused(str(exc))
        settings = drafting_skill_settings(self.customer_yaml)
        if settings is None:
            return refused(
                "the drafting lane is not enabled on this seat (document-drafter is not listed); nothing was queued"
            )
        klass = envelope["document_class"]
        if klass not in enabled_classes_of(settings):
            label = CLASS_LABEL[klass]
            return refused(f"{label[0].upper()}{label[1:]} isn't switched on for your firm; nothing was queued")
        try:
            anchor, effective_from = cycle_from_customer_yaml(self.customer_yaml)
        except AnchorInvalid as exc:
            return refused(str(exc))
        state = self._db.allowance(allowance_of(settings), anchor_day=anchor, effective_from=effective_from)
        if not state["authored"]:
            return refused(f"no drafting allowance is authored for this seat ({ALLOWANCE_KEY}); nothing was queued")
        admins = admins_from_customer_yaml(self.customer_yaml)
        if not admins:
            return refused(
                "the seat's administrator list (scope.admins) is empty or could not be read, so who may "
                "request a draft cannot be established; nothing was queued"
            )
        if envelope["requested_by"] not in admins:
            return refused(
                "a draft may only be requested by one of the firm's Named Administrators, and the "
                "requester on this submission is not one of them; nothing was queued"
            )
        if not self._filing_target_ok(envelope, settings):
            return refused(
                "a draft is filed on the matter it reads, or on the firm's own authored "
                "library matter for a rehearsal; that filing target is neither, so nothing was queued"
            )
        if state["remaining"] <= 0:
            return refused(
                f"this cycle's drafting allowance is spent ({state['used']} of {state['allowance']} in "
                f"{state['cycle']}); nothing was queued"
            )
        try:
            job_id = self._db.submit(
                envelope, allowance=state["allowance"], anchor_day=anchor, effective_from=effective_from
            )
        except SubmitRefused as exc:
            return refused(f"{exc}; nothing new was queued", **({"job_id": exc.job_id} if exc.job_id else {}))
        self._audit(
            AUDIT_TYPES["submitted"],
            {
                "job_id": job_id,
                "matter_number": envelope["matter"]["number"],
                "file_to_other_matter": envelope["file_to"] is not None,
                "document_class": klass,
                "allowance_remaining": state["remaining"],
                "requested_by": envelope["requested_by"],
                "request_ref": envelope["request_ref"],
            },
            envelope["matter"]["id"],
        )
        return {
            "ok": True,
            "accepted": True,
            "job_id": job_id,
            "state": "submitted",
            "document_class": klass,
            "allowance_remaining": state["remaining"],
        }

    # -- the runner's report and a person's resume ---------------------------------
    def _record(self, request: dict[str, Any]) -> dict[str, Any]:
        job_id = str(request.get("job_id") or "")
        state = str(request.get("state") or "")
        fields = request.get("fields")
        if not job_id or state not in STATES or not isinstance(fields, dict):
            raise ValueError("drafting_job_record requires job_id, a known state, and a fields object")
        row = self._db.record(job_id, state, dict(fields))
        if row.get("prev_state") == state:
            return {"ok": True, "job": DraftingLedger.project(row)}
        meta: dict[str, Any] = {
            "job_id": job_id,
            "state": state,
            "document_class": row["document_class"],
            "cents": row["cents"],
            "reason": row["reason"],
            "folder_id": row["folder_id"],
        }
        delivery = merged_delivery(dict(fields))
        if isinstance(delivery, dict):
            meta["files"] = [
                {"name": f.get("name"), "size": f.get("size"), "role": f.get("role")}
                for f in (delivery.get("files") or [])
                if isinstance(f, dict)
            ][:20]
            # Counts only: a marker or a caption line can carry a client fact.
            for key in REPORT_LISTS:
                if isinstance(delivery.get(key), list):
                    meta[f"{key}_count"] = len(delivery[key])
        self._audit(AUDIT_TYPES[state], meta, row["matter_id"])
        return {"ok": True, "job": DraftingLedger.project(row)}

    def _resume(self, request: dict[str, Any]) -> dict[str, Any]:
        """Write the resume marker the runner daemon reads; the row moves when
        the daemon actually starts (it records ``running``)."""
        job_id = str(request.get("job_id") or "")
        reason = str(request.get("reason") or "").strip()
        if not job_id:
            raise ValueError("drafting_job_resume requires job_id")
        if not reason:
            raise ValueError(
                "drafting_job_resume requires a reason: what was fixed, and why the done stages still hold"
            )
        row = self._db.read(job_id)
        if row is None:
            raise ValueError(f"no such job {job_id}")
        if row["state"] != "failed":
            raise ValueError(f"job {job_id} is {row['state']}, not failed; only a failed job resumes")
        twin = self._db.active_on_matter(row["matter_id"], row["document_class"])
        if twin is not None and twin != job_id:
            raise ValueError(f"job {twin} is already underway on this matter; resuming {job_id} would run it twice")
        self._db.queue_dir.mkdir(parents=True, exist_ok=True)
        marker = self._db.queue_dir / f".resume-{job_id}.json"
        marker.write_text(json.dumps({"job_id": job_id, "reason": reason[:500]}, sort_keys=True), encoding="utf-8")
        self._audit(RESUME_AUDIT_TYPE, {"job_id": job_id, "reason": reason[:500]}, row["matter_id"])
        return {"ok": True, "job_id": job_id, "queued": True}


def drafting_dispatch(
    broker: BrokerContext, action: str, request: dict[str, Any], _pid: int, peer_uid: int | None
) -> dict[str, Any]:
    verbs = broker.drafting
    if verbs is None:
        raise ValueError("drafting verbs not configured on this broker")
    envelope = request.get("envelope")
    if action == "drafting_job_submit" and isinstance(envelope, dict) and "request_graph_id" in envelope:
        # Root only, the same resolution the demand lane uses (demand_verbs).
        resolved = _resolve_request_graph_id(broker, envelope, peer_uid)
        if "refused" in resolved:
            return {"ok": True, "accepted": False, "reason": resolved["refused"]}
        request = {**request, "envelope": resolved["envelope"]}
    return verbs.handle(action, request, peer_uid)


__all__ = [
    "VERBS",
    "DraftingVerbs",
    "allowance_of",
    "drafting_dispatch",
    "drafting_skill_settings",
    "enabled_classes_of",
]
