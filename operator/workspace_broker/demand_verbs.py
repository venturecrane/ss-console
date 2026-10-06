"""The ``demand_*`` broker verbs: the request edge of the demand job (the gap
audit and draft demand a firm administrator asks the Operator for).

Modeled on ``medchron_verbs`` and registered the same way: ``verbs.py``
declares each verb's peer classes and checks them before handing the request
here, and asserts at import that its demand rows and ``VERBS`` below agree.

Peer gating, per verb:

    demand_job_submit    gateway PID (the agent's tool call; the overlay sets
                         requested_by / request_ref from the turn's verified
                         inbound, never from the model) OR uid 0 (root on the
                         box: a rehearsal, the Captain-side skill)
    demand_job_status    gateway PID, agent uid, or uid 0. Root gets the full
                         row (the runner daemon); everyone else the projection,
                         which carries no request text and no message id.
    demand_allowance     gateway PID, agent uid, or uid 0. The COUNT is per
                         billing cycle; cents_used is the Pacific calendar
                         month's spend, for the runner's monthly budget.
    demand_job_record    uid 0 only (the runner daemon)
    demand_job_resume    uid 0 only, with NO agent tool: a resume spends money
                         and writes to the firm's matter, and "the defect is
                         fixed" is a judgment only a person makes.

THE SUBMIT CHECKS, in order, none of which spends anything:
1. the envelope is exactly ``demand_ledger.validate_envelope``'s shape;
2. the skill is ENABLED on this seat (the lane is fail-closed by omission:
   a seat that does not list ``demand-letter-drafter`` queues nothing);
3. a per-cycle allowance is authored (``demand_allowance_per_cycle`` in the
   skill's settings), on the chronology cycle's anchor;
4. the requester is a Named Administrator (``scope.admins``);
5. no unfinished demand job on the same matter;
6. the cycle's allowance has a demand left.

Audit rows carry ids, counts and states, never the request text.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

from .broker_context import BrokerContext
from .cycle_window import AnchorInvalid
from .demand_ledger import (
    ALLOWANCE_KEY,
    AUDIT_TYPE,
    SKILL_NAME,
    STATES,
    DemandLedger,
    EnvelopeError,
    SubmitRefused,
    validate_envelope,
)
from .medchron_ledger import admins_from_customer_yaml, cycle_from_customer_yaml

#: The audit types, spelled as literals here so a reader (and the producer
#: manifest's token check) finds them in this file; pinned to the ledger's.
AUDIT_TYPES = {
    "submitted": "DEMAND_JOB_SUBMITTED",
    "running": "DEMAND_JOB_RUNNING",
    "held": "DEMAND_JOB_HELD",
    "delivered": "DEMAND_JOB_DELIVERED",
    "failed": "DEMAND_JOB_FAILED",
}
#: A person asked the runner to re-run a failed job. NOT a transition (the
#: daemon records ``running`` when it actually starts), so not RUNNING.
RESUME_AUDIT_TYPE = "DEMAND_JOB_RESUME_REQUESTED"
if AUDIT_TYPES != AUDIT_TYPE:
    raise RuntimeError("demand_verbs.AUDIT_TYPES and demand_ledger.AUDIT_TYPE disagree")

#: The demand skill setting naming the library matter's id, the one matter a
#: rehearsal may file on (its number is the firm's authored library number).
REHEARSAL_MATTER_KEY = "rehearsal_matter_id"

#: The spend budget's clock (calendar month, Pacific).
_PACIFIC = ZoneInfo("America/Los_Angeles")

VERBS = (
    "demand_job_submit",
    "demand_job_status",
    "demand_allowance",
    "demand_job_record",
    "demand_job_resume",
)


def demand_skill_settings(path: str | Path) -> dict[str, Any] | None:
    """The demand skill's authored settings, or None unless the seat LISTS the
    skill and does not disable it. Omission is refusal (ADR 0035)."""
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


def operator_library_number(path: str | Path) -> str | None:
    """The firm's own authored library matter number (``self_initiation.
    document_library.operator_matter.number``), the one matter a rehearsal may
    file on instead of the matter it reads. None when unauthored."""
    try:
        import yaml

        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        number = doc["self_initiation"]["document_library"]["operator_matter"]["number"]
    except Exception:  # noqa: BLE001 - unauthored or unreadable authorizes nothing
        return None
    return number.strip() if isinstance(number, str) and number.strip() else None


def allowance_of(settings: dict[str, Any] | None) -> int | None:
    value = (settings or {}).get(ALLOWANCE_KEY)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class DemandVerbs:
    def __init__(self, ledger: DemandLedger | None, *, customer_yaml: str, audit_append: Any) -> None:
        self.ledger = ledger
        self.customer_yaml = customer_yaml
        self._audit_append = audit_append

    @classmethod
    def build(cls, broker: BrokerContext, *, audit_db_path: str | None, queue_dir: str | None) -> DemandVerbs:
        """Enabled only when the audit ledger is (a job that cannot be recorded
        must not be queued) and the entrypoint exported the demand queue dir."""
        writer = broker.ledger
        ledger = DemandLedger(audit_db_path, queue_dir) if audit_db_path and queue_dir and writer is not None else None

        def audit_append(row: dict[str, Any]) -> Any:
            if writer is None:
                raise ValueError("demand verbs have no audit ledger on this broker")
            return writer.append(row)

        return cls(ledger, customer_yaml=str(broker.customer_path), audit_append=audit_append)

    @property
    def _db(self) -> DemandLedger:
        if self.ledger is None:
            raise ValueError("demand ledger not configured on this broker")
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
            raise ValueError("demand ledger not configured on this broker")
        if action == "demand_allowance":
            return self._allowance(request)
        if action == "demand_job_status":
            return self._status(request, full=peer_uid == 0)
        if action == "demand_job_submit":
            return self._submit(request)
        if action == "demand_job_record":
            return self._record(request)
        if action == "demand_job_resume":
            return self._resume(request)
        raise ValueError(f"unsupported demand action: {action}")

    # -- allowance ------------------------------------------------------------
    def _allowance(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            anchor, effective_from = cycle_from_customer_yaml(self.customer_yaml)
        except AnchorInvalid as exc:
            return {"ok": True, "authored": False, "invalid": True, "reason": str(exc)}
        exclude = str(request.get("exclude_job_id") or "")
        # The job asking (the runner, before a paid stage) is excluded from the
        # count as well as from the spend: it must not take its own slot.
        state = self._db.allowance(
            allowance_of(demand_skill_settings(self.customer_yaml)),
            anchor_day=anchor,
            effective_from=effective_from,
            exclude=exclude,
        )
        month, cents = self._cents_this_month(exclude)
        return {"ok": True, **state, "cents_used": cents, "cents_month": month}

    def _cents_this_month(self, exclude: str, now: datetime | None = None) -> tuple[str, int]:
        """("YYYY-MM", SUM(cents)) of the demand jobs created in the current
        CALENDAR month in America/Los_Angeles, without ``exclude`` (the job about
        to run, so a resume does not count its own earlier spend twice).

        The spend budget's window (the Captain, 2026-10-06: a monthly demand
        budget per calendar month, Pacific). Deliberately NOT the billing
        cycle the COUNT allowance uses: the count is a contract term, the
        spend budget is ours. Cents are dated when the runner WROTE them
        (``demand_spend``), not when the job began, and stored in UTC, so the
        Pacific month's two midnights are converted to UTC bounds."""
        local = (now or datetime.now(timezone.utc)).astimezone(_PACIFIC)
        start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        nxt = (start + timedelta(days=32)).replace(day=1)

        def utc(dt: datetime) -> str:
            # Re-anchor the wall-clock midnight in its own month's offset (DST).
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
            return {"ok": True, "job": row if full else DemandLedger.project(row)}
        conn = self._db._connect()
        try:
            rows = [dict(r) for r in conn.execute("SELECT * FROM demand_jobs ORDER BY created_at DESC LIMIT 20")]
        finally:
            conn.close()
        return {"ok": True, "jobs": [DemandLedger.project(r) for r in rows]}

    # -- submit ---------------------------------------------------------------
    def _submit(self, request: dict[str, Any]) -> dict[str, Any]:
        def refused(reason: str, **extra: Any) -> dict[str, Any]:
            return {"ok": True, "accepted": False, "reason": reason, **extra}

        try:
            envelope = validate_envelope(request.get("envelope") or {})
        except EnvelopeError as exc:
            return refused(str(exc))
        settings = demand_skill_settings(self.customer_yaml)
        if settings is None:
            return refused(
                "the demand lane is not enabled on this seat (demand-letter-drafter is not listed); nothing was queued"
            )
        try:
            anchor, effective_from = cycle_from_customer_yaml(self.customer_yaml)
        except AnchorInvalid as exc:
            return refused(str(exc))
        state = self._db.allowance(allowance_of(settings), anchor_day=anchor, effective_from=effective_from)
        if not state["authored"]:
            return refused(f"no demand allowance is authored for this seat ({ALLOWANCE_KEY}); nothing was queued")
        admins = admins_from_customer_yaml(self.customer_yaml)
        if not admins:
            return refused(
                "the seat's administrator list (scope.admins) is empty or could not be read, so who may "
                "request a demand cannot be established; nothing was queued"
            )
        if envelope["requested_by"] not in admins:
            return refused(
                "a demand may only be requested by one of the firm's Named Administrators, and the "
                "requester on this submission is not one of them; nothing was queued"
            )
        file_to = envelope["file_to"]
        if file_to is not None and file_to["id"] != envelope["matter"]["id"]:
            # The rehearsal target is AUTHORED by id and by number: the id in the
            # demand skill's settings (rehearsal_matter_id), the number as the
            # firm's own library matter. A right number with a wrong id is refused.
            library = operator_library_number(self.customer_yaml)
            library_id = str(settings.get(REHEARSAL_MATTER_KEY) or "").strip().lower()
            if (
                not library
                or not library_id
                or file_to["number"] != library
                or file_to["id"].strip().lower() != library_id
            ):
                return refused(
                    "a demand is filed on the matter it reads, or on the firm's own authored "
                    "library matter for a rehearsal; that filing target is neither, so nothing was queued"
                )
        if state["remaining"] <= 0:
            return refused(
                f"this cycle's demand allowance is spent ({state['used']} of {state['allowance']} in "
                f"{state['cycle']}); nothing was queued"
            )
        try:
            # The duplicate, the matter and the cycle are re-checked INSIDE the
            # ledger's write transaction: the checks above are for a readable
            # answer, these are the ones two concurrent submits cannot both pass.
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
                "deliverables": envelope["deliverables"],
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
            "allowance_remaining": state["remaining"],
        }

    # -- the runner's report and a person's resume ---------------------------------
    def _record(self, request: dict[str, Any]) -> dict[str, Any]:
        job_id = str(request.get("job_id") or "")
        state = str(request.get("state") or "")
        fields = request.get("fields")
        if not job_id or state not in STATES or not isinstance(fields, dict):
            raise ValueError("demand_job_record requires job_id, a known state, and a fields object")
        row = self._db.record(job_id, state, dict(fields))
        if row.get("prev_state") == state:
            # A note on an unchanged state (the runner's lost-wake note): the
            # row is updated, but the audit log records transitions only.
            return {"ok": True, "job": DemandLedger.project(row)}
        meta: dict[str, Any] = {
            "job_id": job_id,
            "state": state,
            "cents": row["cents"],
            "reason": row["reason"],
            "folder_id": row["folder_id"],
        }
        delivery = fields.get("delivery")
        if isinstance(delivery, dict):
            meta["files"] = [
                {"name": f.get("name"), "size": f.get("size")}
                for f in (delivery.get("files") or [])
                if isinstance(f, dict)
            ][:20]
            meta["coverage_report"] = delivery.get("coverage_report") is True
        self._audit(AUDIT_TYPES[state], meta, row["matter_id"])
        return {"ok": True, "job": DemandLedger.project(row)}

    def _resume(self, request: dict[str, Any]) -> dict[str, Any]:
        """Write the resume marker the runner daemon reads; the row moves when
        the daemon actually starts (it records ``running``)."""
        job_id = str(request.get("job_id") or "")
        reason = str(request.get("reason") or "").strip()
        if not job_id:
            raise ValueError("demand_job_resume requires job_id")
        if not reason:
            raise ValueError("demand_job_resume requires a reason: what was fixed, and why the done stages still hold")
        row = self._db.read(job_id)
        if row is None:
            raise ValueError(f"no such job {job_id}")
        if row["state"] != "failed":
            raise ValueError(f"job {job_id} is {row['state']}, not failed; only a failed job resumes")
        twin = self._db.active_on_matter(row["matter_id"])
        if twin is not None and twin != job_id:
            raise ValueError(f"job {twin} is already underway on this matter; resuming {job_id} would run it twice")
        self._db.queue_dir.mkdir(parents=True, exist_ok=True)
        marker = self._db.queue_dir / f".resume-{job_id}.json"
        marker.write_text(json.dumps({"job_id": job_id, "reason": reason[:500]}, sort_keys=True), encoding="utf-8")
        self._audit(
            RESUME_AUDIT_TYPE,
            {"job_id": job_id, "reason": reason[:500]},
            row["matter_id"],
        )
        return {"ok": True, "job_id": job_id, "queued": True}


def demand_dispatch(
    broker: BrokerContext, action: str, request: dict[str, Any], _pid: int, peer_uid: int | None
) -> dict[str, Any]:
    verbs = broker.demand
    if verbs is None:
        raise ValueError("demand verbs not configured on this broker")
    return verbs.handle(action, request, peer_uid)


__all__ = ["VERBS", "DemandVerbs", "allowance_of", "demand_dispatch", "demand_skill_settings"]
