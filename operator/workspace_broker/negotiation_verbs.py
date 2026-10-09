"""The ``negotiation_*`` broker verbs: the request edge of the negotiation watch
(new offers entered on the firm's Negotiation Details tabs, one email per new
offer), registered the way the drafting lane's are: ``verbs.py`` declares
each verb's peer classes and checks them first, and asserts at import that its
negotiation rows and ``VERBS`` below agree.

Peer gating, per verb:

    negotiation_job_submit   ONE form, ``{"trigger": "scheduled"}``: the weekday
                             cron's pre_run (agent uid, NOT the gateway) or uid
                             0. From the agent uid the cron row must be LIVE, so
                             an execute_code turn cannot start a run the firm
                             never enabled. No turn ever submits one.
    negotiation_job_status   gateway PID, agent uid, or uid 0: a job's counts,
                             or a NOTICE's message (what the completion turn
                             sends). Root also gets the Pacific month's spend.
    negotiation_job_record   uid 0 only (the runner daemon)
    negotiation_job_resume   uid 0 only, with NO agent tool.

THE SUBMIT CHECKS, in order, none of which spends anything: the envelope's
shape; the skill LISTED, ENABLED and ``initiation.scheduled``; (agent uid) the
cron row live; the first authored ``scheduled_recipients`` entry a Named
Administrator; the monthly budget authored and not spent this Pacific month;
no unfinished negotiation job, and this cron slot not already queued.

Audit rows carry ids, counts and states, never a matter fact.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .broker_context import BrokerContext
from .medchron_ledger import admins_from_customer_yaml
from .negotiation_intake import (
    cron_row_live,
    firm_words,
    matter_statuses,
    negotiation_design,
    scheduled_allowed,
    scheduled_recipients,
    settings_of,
    skill_entry,
    usd,
)
from .negotiation_ledger import (
    AUDIT_TYPE,
    COUNT_FIELDS,
    SKILL_NAME,
    STATES,
    EnvelopeError,
    NegotiationLedger,
    SubmitRefused,
    validate_envelope,
)

AUDIT_TYPES = {
    "queued": "NEGOTIATION_JOB_SUBMITTED",
    "running": "NEGOTIATION_JOB_RUNNING",
    "delivered": "NEGOTIATION_JOB_DELIVERED",
    "failed": "NEGOTIATION_JOB_FAILED",
}
RESUME_AUDIT_TYPE = "NEGOTIATION_JOB_RESUME_REQUESTED"
if AUDIT_TYPES != AUDIT_TYPE:
    raise RuntimeError("negotiation_verbs.AUDIT_TYPES and negotiation_ledger.AUDIT_TYPE disagree")

_PACIFIC = ZoneInfo("America/Los_Angeles")
SCHEDULED_TEXT = "scheduled weekday negotiation watch run"
#: The per-job cap when the firm authors only the monthly guard.
DEFAULT_PER_JOB_USD = 10.0

VERBS = (
    "negotiation_job_submit",
    "negotiation_job_status",
    "negotiation_job_record",
    "negotiation_job_resume",
)


class _Refused(ValueError):
    """A submit check that failed; the message is the reason recorded."""


def _refusal(reason: str, **extra: Any) -> dict[str, Any]:
    return {"ok": True, "accepted": False, "reason": reason, **extra}


def slot_ref(now: datetime) -> str:
    local = now.astimezone(_PACIFIC)
    return f"scheduled:{local.strftime('%Y-%m-%dT%H')}"


def month_window(now: datetime) -> tuple[str, str, str]:
    local = now.astimezone(_PACIFIC)
    start = datetime(local.year, local.month, 1, tzinfo=_PACIFIC)
    nxt = (start + timedelta(days=32)).replace(day=1)

    def utc(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    return start.strftime("%Y-%m"), utc(start), utc(nxt)


class NegotiationVerbs:
    def __init__(self, ledger: NegotiationLedger | None, *, customer_yaml: str, audit_append: Any, now: Any = None):
        self.ledger = ledger
        self.customer_yaml = customer_yaml
        self._audit_append = audit_append
        self._now = now or (lambda: datetime.now(timezone.utc))

    @classmethod
    def build(cls, broker: BrokerContext, *, audit_db_path: str | None, queue_dir: str | None) -> NegotiationVerbs:
        """Enabled only when the audit ledger is and the entrypoint exported the
        negotiation queue dir (SMD_NEGOTIATION_QUEUE_DIR)."""
        writer = broker.ledger
        ledger = (
            NegotiationLedger(audit_db_path, queue_dir) if audit_db_path and queue_dir and writer is not None else None
        )

        def audit_append(row: dict[str, Any]) -> Any:
            if writer is None:
                raise ValueError("negotiation verbs have no audit ledger on this broker")
            return writer.append(row)

        return cls(ledger, customer_yaml=str(broker.customer_path), audit_append=audit_append)

    @property
    def _db(self) -> NegotiationLedger:
        if self.ledger is None:
            raise ValueError("negotiation ledger not configured on this broker")
        return self.ledger

    def _audit(self, action_type: str, metadata: dict[str, Any]) -> None:
        self._audit_append(
            {
                "action_type": action_type,
                "actor": "workspace-broker",
                "actor_role": "broker",
                "skill_name": SKILL_NAME,
                "matter_ref": None,
                "metadata": json.dumps(metadata, sort_keys=True, separators=(",", ":")),
            }
        )

    def handle(self, action: str, request: dict[str, Any], peer_uid: int | None, *, from_gateway: bool = False):
        if self.ledger is None:
            raise ValueError("negotiation ledger not configured on this broker")
        if action == "negotiation_job_status":
            return self._status(request, full=peer_uid == 0)
        if action == "negotiation_job_submit":
            return self._submit(request, peer_uid=peer_uid, from_gateway=from_gateway)
        if action == "negotiation_job_record":
            return self._record(request)
        if action == "negotiation_job_resume":
            return self._resume(request)
        raise ValueError(f"unsupported negotiation action: {action}")

    # -- status ---------------------------------------------------------------
    def month_cents(self, exclude: str = "") -> tuple[str, int]:
        month, start, end = month_window(self._now())
        return month, self._db.spend_between(start, end, exclude)

    def _status(self, request: dict[str, Any], *, full: bool) -> dict[str, Any]:
        ident = str(request.get("job_id") or "")
        if ident:
            row = self._db.read(ident)
            if row is None:
                return {"ok": True, "job": None}
            return {"ok": True, "job": {**NegotiationLedger.project(row), **({"row": row} if full else {})}}
        out: dict[str, Any] = {"ok": True, "jobs": [NegotiationLedger.project(r) for r in self._db.recent(20)]}
        if full:
            month, cents = self.month_cents(str(request.get("exclude_job_id") or ""))
            out.update({"month": month, "month_cents": cents})
        return out

    # -- submit ---------------------------------------------------------------
    def _submit(self, request: dict[str, Any], *, peer_uid: int | None, from_gateway: bool) -> dict[str, Any]:
        raw = request.get("envelope")
        if from_gateway:
            raise PermissionError("a negotiation watch run is submitted by its cron's pre_run or root, never a turn")
        if raw != {"trigger": "scheduled"}:
            return _refusal('a negotiation envelope is exactly {"trigger": "scheduled"}; nothing was queued')
        try:
            settings = self._seat_gates(peer_uid)
            requester = self._recipient(settings)
            monthly, cents = self._budget(settings)
            envelope = {
                "trigger": "scheduled",
                "requester": requester,
                "message_ref": slot_ref(self._now()),
                "request_text": SCHEDULED_TEXT,
                "matter_statuses": matter_statuses(settings),
                "negotiation_design": negotiation_design(self.customer_yaml),
                "firm_words": firm_words(settings),
                "per_job_cap_usd": min(usd(settings, "per_job_cap_usd") or DEFAULT_PER_JOB_USD, monthly),
                "monthly_budget_usd": monthly,
            }
            valid = validate_envelope(envelope)
            job_id = self._db.submit(valid)
        except (EnvelopeError, _Refused) as exc:
            return _refusal(str(exc))
        except SubmitRefused as exc:
            return _refusal(f"{exc}; nothing new was queued", **({"job_id": exc.job_id} if exc.job_id else {}))
        self._audit(
            AUDIT_TYPES["queued"],
            {
                "job_id": job_id,
                "trigger": "scheduled",
                "requested_by": requester,
                "request_ref": valid["message_ref"],
                "month_cents_used": cents,
            },
        )
        return {"ok": True, "accepted": True, "job_id": job_id, "state": "queued", "trigger": "scheduled"}

    def _seat_gates(self, peer_uid: int | None) -> dict[str, Any]:
        entry = skill_entry(self.customer_yaml)
        if entry is None:
            raise _Refused(
                "the negotiation watch is not enabled on this seat (negotiation-watch is not listed); nothing was queued"
            )
        if not scheduled_allowed(entry):
            raise _Refused("the negotiation watch is not switched on for a schedule on this seat; nothing was queued")
        if peer_uid != 0 and not cron_row_live(self.customer_yaml):
            raise _Refused("the weekday negotiation watch is not enabled on this seat; nothing was queued")
        return settings_of(entry)

    def _recipient(self, settings: dict[str, Any]) -> str:
        admins = admins_from_customer_yaml(self.customer_yaml) or ()
        recipients = scheduled_recipients(settings)
        if not recipients or recipients[0] not in admins:
            raise _Refused(
                "the offer emails go to the first authored scheduled_recipients entry, which must be one of the "
                "firm's Named Administrators; none is, so nothing was queued"
            )
        return recipients[0]

    def _budget(self, settings: dict[str, Any]) -> tuple[float, int]:
        monthly = usd(settings, "monthly_budget_usd")
        if monthly is None:
            raise _Refused("the negotiation watch's monthly budget is not authored on this seat; nothing was queued")
        month, cents = self.month_cents()
        if cents >= int(round(monthly * 100)):
            raise _Refused(f"this month's negotiation watch budget is spent ({month}); nothing was queued")
        return monthly, cents

    # -- the runner's report and a person's resume ---------------------------------
    def _record(self, request: dict[str, Any]) -> dict[str, Any]:
        job_id = str(request.get("job_id") or "")
        state = str(request.get("state") or "")
        fields = request.get("fields")
        if not job_id or state not in STATES or not isinstance(fields, dict):
            raise ValueError("negotiation_job_record requires job_id, a known state, and a fields object")
        row = self._db.record(job_id, state, dict(fields))
        ids = list(row.get("notice_ids") or [])
        if row.get("prev_state") == state:
            return {"ok": True, "job": NegotiationLedger.project(row), "notice_ids": ids}
        meta: dict[str, Any] = {
            "job_id": job_id,
            "state": state,
            "cents": row.get("cents"),
            "stage": row.get("stage"),
            "reason": row.get("reason"),
            "notices": len(ids),
            **{k: row.get(k) for k in COUNT_FIELDS},
        }
        self._audit(AUDIT_TYPES[state], meta)
        return {"ok": True, "job": NegotiationLedger.project(row), "notice_ids": ids}

    def _resume(self, request: dict[str, Any]) -> dict[str, Any]:
        job_id = str(request.get("job_id") or "")
        reason = str(request.get("reason") or "").strip()
        if not job_id or not reason:
            raise ValueError("negotiation_job_resume requires job_id and a reason")
        row = self._db.read_job(job_id)
        if row is None:
            raise ValueError(f"no such job {job_id}")
        if row["state"] != "failed":
            raise ValueError(f"job {job_id} is {row['state']}, not failed; only a failed job resumes")
        twin = self._db.unfinished()
        if twin is not None and twin != job_id:
            raise ValueError(f"job {twin} is already underway; resuming {job_id} would run two at once")
        self._db.queue_dir.mkdir(parents=True, exist_ok=True)
        marker = self._db.queue_dir / f".resume-{job_id}.json"
        marker.write_text(json.dumps({"job_id": job_id, "reason": reason[:500]}, sort_keys=True), encoding="utf-8")
        self._audit(RESUME_AUDIT_TYPE, {"job_id": job_id, "reason": reason[:500]})
        return {"ok": True, "job_id": job_id, "queued": True}


def negotiation_dispatch(
    broker: BrokerContext, action: str, request: dict[str, Any], pid: int, peer_uid: int | None
) -> dict[str, Any]:
    verbs = broker.negotiation
    if verbs is None:
        raise ValueError("negotiation verbs not configured on this broker")
    return verbs.handle(action, request, peer_uid, from_gateway=pid == broker.gateway_pid)


__all__ = [
    "AUDIT_TYPES",
    "RESUME_AUDIT_TYPE",
    "VERBS",
    "NegotiationVerbs",
    "month_window",
    "negotiation_dispatch",
    "slot_ref",
]
