"""The ``litigation_*`` broker verbs: the request edge of the litigation status
job (one fresh status workbook of the firm's open litigation matters, filed to
the firm's own library matter), registered the way the drafting lane's are:
``verbs.py`` declares each verb's peer classes and checks them first, and
asserts at import that its litigation rows and ``VERBS`` below agree.

Peer gating, per verb:

    litigation_job_submit   two forms, told apart by the envelope's trigger:
                            ``request``   gateway PID (the agent's tool call;
                                          the overlay sets requester,
                                          message_ref and request_text from the
                                          turn's verified inbound) OR uid 0;
                            ``scheduled`` the weekday cron's pre_run (agent
                                          uid, NOT the gateway) OR uid 0. The
                                          envelope names no requester: the
                                          broker fills it from the authored
                                          ``scheduled_recipients``. From the
                                          agent uid it also requires the cron
                                          row to be LIVE (uncommented), so an
                                          execute_code turn cannot start a
                                          scheduled run the firm never enabled.
    litigation_job_status   gateway PID, agent uid, or uid 0. Root gets the
                            full row, and with no job id the Pacific month's
                            spend (``month_cents``), which the runner's lane
                            meters against the firm's budget.
    litigation_job_record   uid 0 only (the runner daemon)
    litigation_job_resume   uid 0 only, with NO agent tool.

THE SUBMIT CHECKS, in order, none of which spends anything:
1. the envelope is one of the two input shapes;
2. the skill ``litigation-status`` is LISTED and ENABLED, and its
   ``initiation`` permits the trigger;
3. (scheduled, agent uid) the cron row for the skill is live;
4. the requester is a Named Administrator (``scope.admins``); for a scheduled
   run, the first authored ``scheduled_recipients`` entry must be one;
5. the filing target is authored: ``file_to_matter_id`` and ``folder_name``
   in the skill's settings and the firm's library matter number;
6. the firm's litigation config is staged and its monthly budget authored and
   not yet spent this Pacific month;
7. any attorneys named resolve, one each, against the firm's authored roster;
8. no unfinished job for the same scope, and this email (or this Pacific
   date's scheduled run) has not already queued one (re-checked in the
   ledger's write transaction).

Audit rows carry ids, counts and states, never the request text and never a
matter fact.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .broker_context import BrokerContext
from .litigation_intake import (
    FILE_TO_KEY,
    FOLDER_KEY,
    attorney_roster,
    cron_row_live,
    firm_config,
    initiation_allows,
    monthly_budget_cents,
    operator_library_number,
    resolve_attorneys,
    scheduled_recipients,
    settings_of,
    skill_entry,
)
from .litigation_ledger import (
    AUDIT_TYPE,
    COUNT_FIELDS,
    MAX_REQUEST_TEXT,
    SKILL_NAME,
    STATES,
    EnvelopeError,
    LitigationLedger,
    SubmitRefused,
    scope_key,
    validate_envelope,
)
from .medchron_ledger import admins_from_customer_yaml

#: The audit types, spelled as literals here so a reader (and the producer
#: manifest's token check) finds them in this file; pinned to the ledger's.
AUDIT_TYPES = {
    "queued": "LITIGATION_JOB_SUBMITTED",
    "running": "LITIGATION_JOB_RUNNING",
    "held": "LITIGATION_JOB_HELD",
    "delivered": "LITIGATION_JOB_DELIVERED",
    "failed": "LITIGATION_JOB_FAILED",
}
#: A person asked the runner to re-run a failed job. NOT a transition.
RESUME_AUDIT_TYPE = "LITIGATION_JOB_RESUME_REQUESTED"
if AUDIT_TYPES != AUDIT_TYPE:
    raise RuntimeError("litigation_verbs.AUDIT_TYPES and litigation_ledger.AUDIT_TYPE disagree")

_PACIFIC = ZoneInfo("America/Los_Angeles")
_UUID = re.compile(r"^[0-9a-fA-F-]{32,40}$")
#: What a scheduled run's request_text says (it has no email).
SCHEDULED_TEXT = "scheduled weekday litigation status run"


class _Refused(ValueError):
    """A submit check that failed; the message is the sentence to relay."""


def _refusal(reason: str, **extra: Any) -> dict[str, Any]:
    return {"ok": True, "accepted": False, "reason": reason, **extra}


VERBS = (
    "litigation_job_submit",
    "litigation_job_status",
    "litigation_job_record",
    "litigation_job_resume",
)

_REQUEST_KEYS = {"trigger", "requester", "message_ref", "request_text", "scope"}


def pacific_today(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(_PACIFIC).date()


def month_window(now: datetime | None = None) -> tuple[str, str, str]:
    """("YYYY-MM", start, end) of the current Pacific calendar month, as UTC
    ISO strings in the ledger's format."""
    local = (now or datetime.now(timezone.utc)).astimezone(_PACIFIC)
    start = datetime(local.year, local.month, 1, tzinfo=_PACIFIC)
    nxt = (start + timedelta(days=32)).replace(day=1)

    def utc(dt: datetime) -> str:
        return (
            datetime(dt.year, dt.month, 1, tzinfo=_PACIFIC).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        )

    return start.strftime("%Y-%m"), utc(start), utc(nxt)


class LitigationVerbs:
    def __init__(
        self,
        ledger: LitigationLedger | None,
        *,
        customer_yaml: str,
        audit_append: Any,
        firm_config_path: str | None = None,
        now: Any = None,
    ) -> None:
        self.ledger = ledger
        self.customer_yaml = customer_yaml
        self.firm_config_path = firm_config_path
        self._audit_append = audit_append
        self._now = now or (lambda: datetime.now(timezone.utc))

    @classmethod
    def build(cls, broker: BrokerContext, *, audit_db_path: str | None, queue_dir: str | None) -> LitigationVerbs:
        """Enabled only when the audit ledger is and the entrypoint exported
        the litigation queue dir (SMD_LITIGATION_QUEUE_DIR)."""
        writer = broker.ledger
        ledger = (
            LitigationLedger(audit_db_path, queue_dir) if audit_db_path and queue_dir and writer is not None else None
        )

        def audit_append(row: dict[str, Any]) -> Any:
            if writer is None:
                raise ValueError("litigation verbs have no audit ledger on this broker")
            return writer.append(row)

        return cls(ledger, customer_yaml=str(broker.customer_path), audit_append=audit_append)

    @property
    def _db(self) -> LitigationLedger:
        if self.ledger is None:
            raise ValueError("litigation ledger not configured on this broker")
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

    # -- dispatch ------------------------------------------------------------
    def handle(
        self, action: str, request: dict[str, Any], peer_uid: int | None, *, from_gateway: bool = False
    ) -> dict[str, Any]:
        if self.ledger is None:
            raise ValueError("litigation ledger not configured on this broker")
        if action == "litigation_job_status":
            return self._status(request, full=peer_uid == 0)
        if action == "litigation_job_submit":
            return self._submit(request, peer_uid=peer_uid, from_gateway=from_gateway)
        if action == "litigation_job_record":
            return self._record(request)
        if action == "litigation_job_resume":
            return self._resume(request)
        raise ValueError(f"unsupported litigation action: {action}")

    # -- status ---------------------------------------------------------------
    def month_cents(self, exclude: str = "") -> tuple[str, int]:
        month, start, end = month_window(self._now())
        return month, self._db.spend_between(start, end, exclude)

    def _status(self, request: dict[str, Any], *, full: bool) -> dict[str, Any]:
        job_id = str(request.get("job_id") or "")
        if job_id:
            row = self._db.read(job_id)
            if row is None:
                return {"ok": True, "job": None}
            job = {**LitigationLedger.project(row), **({"row": row} if full else {})}
            return {"ok": True, "job": job}
        out: dict[str, Any] = {"ok": True, "jobs": [LitigationLedger.project(r) for r in self._db.recent(20)]}
        if full:
            month, cents = self.month_cents(str(request.get("exclude_job_id") or ""))
            budget = monthly_budget_cents(firm_config(self.firm_config_path))
            out.update({"month": month, "month_cents": cents, "monthly_budget_cents": budget})
        return out

    # -- submit ---------------------------------------------------------------
    def _input(self, raw: Any, *, peer_uid: int | None, from_gateway: bool) -> dict[str, Any]:
        """The caller's envelope, checked for shape and peer. Returns the
        trigger and the fields the caller may set."""
        if not isinstance(raw, dict):
            raise EnvelopeError("litigation_job_submit requires an envelope object")
        trigger = raw.get("trigger")
        if trigger == "scheduled":
            if from_gateway:
                raise PermissionError(
                    "a scheduled litigation run is submitted by its cron's pre_run or root, never a turn"
                )
            if set(raw) != {"trigger"}:
                raise EnvelopeError('a scheduled envelope carries only {"trigger": "scheduled"}')
            return {"trigger": "scheduled"}
        if trigger != "request":
            raise EnvelopeError('trigger must be "request" or "scheduled"')
        if not from_gateway and peer_uid != 0:
            raise PermissionError("a requested litigation run is submitted from an email turn or by root")
        if set(raw) != _REQUEST_KEYS:
            raise EnvelopeError(f"a request envelope carries exactly {sorted(_REQUEST_KEYS)}")
        scope = raw["scope"]
        if isinstance(scope, dict) and "attorney_staff_ids" in scope and peer_uid != 0:
            raise PermissionError("staff ids are named by root only; a turn names attorneys")
        text = raw["request_text"]
        if isinstance(text, str) and len(text) > MAX_REQUEST_TEXT:
            raise EnvelopeError(f"request_text must be at most {MAX_REQUEST_TEXT} characters")
        return dict(raw)

    def _submit(self, request: dict[str, Any], *, peer_uid: int | None, from_gateway: bool) -> dict[str, Any]:
        """The checks in the module docstring's order. Each helper raises
        ``_Refused`` with the sentence the skill relays; none spends anything."""
        try:
            given = self._input(request.get("envelope"), peer_uid=peer_uid, from_gateway=from_gateway)
            settings, admins = self._seat_gates(given["trigger"], peer_uid)
            requester, message_ref, request_text, scope_in = self._who(given, settings, admins)
            library, file_to, folder = self._filing_target(settings)
            firm, cents = self._budget()
            envelope = {
                "trigger": given["trigger"],
                "requester": requester,
                "message_ref": message_ref,
                "request_text": request_text,
                "scope": self._resolve_scope(scope_in, firm),
                "file_to_matter_id": file_to,
                "file_to_matter_number": library,
                "folder_name": folder,
            }
            valid = validate_envelope(envelope)
            job_id = self._db.submit(valid)
        except EnvelopeError as exc:
            return _refusal(str(exc))
        except _Refused as exc:
            return _refusal(str(exc))
        except SubmitRefused as exc:
            return _refusal(f"{exc}; nothing new was queued", **({"job_id": exc.job_id} if exc.job_id else {}))
        self._audit_submitted(job_id, valid, cents)
        return {"ok": True, "accepted": True, "job_id": job_id, "state": "queued", "trigger": valid["trigger"]}

    def _seat_gates(self, trigger: str, peer_uid: int | None) -> tuple[dict[str, Any], tuple[str, ...]]:
        """Checks 2 to 4's seat half: the skill listed and enabled for this
        trigger, a scheduled run's cron row live, the admin list readable."""
        entry = skill_entry(self.customer_yaml)
        if entry is None:
            raise _Refused(
                "the litigation status lane is not enabled on this seat (litigation-status is not listed); "
                "nothing was queued"
            )
        if not initiation_allows(entry, trigger):
            word = "on request" if trigger == "request" else "on a schedule"
            raise _Refused(f"the litigation status list is not switched on {word} for this seat; nothing was queued")
        if trigger == "scheduled" and peer_uid != 0 and not cron_row_live(self.customer_yaml):
            raise _Refused("the weekday litigation status run is not enabled on this seat; nothing was queued")
        admins = admins_from_customer_yaml(self.customer_yaml)
        if not admins:
            raise _Refused(
                "the seat's administrator list (scope.admins) is empty or could not be read, so who may "
                "request a status list cannot be established; nothing was queued"
            )
        return settings_of(entry), admins

    def _who(
        self, given: dict[str, Any], settings: dict[str, Any], admins: tuple[str, ...]
    ) -> tuple[str, str, str, Any]:
        """(requester, message_ref, request_text, scope) for either trigger,
        the requester a Named Administrator in both."""
        if given["trigger"] == "scheduled":
            recipients = scheduled_recipients(settings)
            if not recipients or recipients[0] not in admins:
                raise _Refused(
                    "a scheduled run goes to the first authored scheduled_recipients entry, which must be "
                    "one of the firm's Named Administrators; none is, so nothing was queued"
                )
            ref = f"scheduled:{pacific_today(self._now()).isoformat()}"
            return recipients[0], ref, SCHEDULED_TEXT, {"all": True}
        requester = str(given["requester"] or "").strip().lower()
        if requester not in admins:
            raise _Refused(
                "a status list may only be requested by one of the firm's Named Administrators, and the "
                "requester on this submission is not one of them; nothing was queued"
            )
        return requester, given["message_ref"], given["request_text"], given["scope"]

    def _filing_target(self, settings: dict[str, Any]) -> tuple[str, str, str]:
        library = operator_library_number(self.customer_yaml)
        file_to = str(settings.get(FILE_TO_KEY) or "").strip()
        folder = str(settings.get(FOLDER_KEY) or "").strip()
        if not (library and _UUID.match(file_to) and folder):
            raise _Refused(
                "where the status list is filed is not authored on this seat (the library matter and its "
                "folder); nothing was queued"
            )
        return library, file_to, folder

    def _budget(self) -> tuple[dict[str, Any] | None, int]:
        """(the firm config, cents spent this Pacific month), or refused."""
        firm = firm_config(self.firm_config_path)
        budget = monthly_budget_cents(firm)
        if budget is None:
            raise _Refused("the firm's litigation status budget is not authored on this seat; nothing was queued")
        month, cents = self.month_cents()
        if cents >= budget:
            raise _Refused(f"this month's litigation status budget is spent ({month}); nothing was queued")
        return firm, cents

    @staticmethod
    def _resolve_scope(scope_in: Any, firm: dict[str, Any] | None) -> Any:
        """Named attorneys become staff ids from the firm's roster; any other
        scope passes through for the envelope check."""
        if not (isinstance(scope_in, dict) and set(scope_in) == {"attorneys"}):
            return scope_in
        names = scope_in["attorneys"]
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise _Refused("scope.attorneys must be a list of names; nothing was queued")
        ids, why = resolve_attorneys(names, attorney_roster(firm))
        if why:
            raise _Refused(why)
        return {"attorney_staff_ids": ids}

    def _audit_submitted(self, job_id: str, valid: dict[str, Any], cents: int) -> None:
        scope = valid["scope"]
        self._audit(
            AUDIT_TYPES["queued"],
            {
                "job_id": job_id,
                "trigger": valid["trigger"],
                "scope": "all" if scope.get("all") else "attorneys",
                "attorney_count": len(scope.get("attorney_staff_ids") or []),
                "requested_by": valid["requester"],
                "request_ref": valid["message_ref"],
                "month_cents_used": cents,
            },
        )

    # -- the runner's report and a person's resume ---------------------------------
    def _record(self, request: dict[str, Any]) -> dict[str, Any]:
        job_id = str(request.get("job_id") or "")
        state = str(request.get("state") or "")
        fields = request.get("fields")
        if not job_id or state not in STATES or not isinstance(fields, dict):
            raise ValueError("litigation_job_record requires job_id, a known state, and a fields object")
        row = self._db.record(job_id, state, dict(fields))
        if row.get("prev_state") == state:
            return {"ok": True, "job": LitigationLedger.project(row)}
        projected = LitigationLedger.project(row)
        meta: dict[str, Any] = {
            "job_id": job_id,
            "state": state,
            "trigger": row["trigger"],
            "cents": row["cents"],
            "stage": row["stage"],
            "reason": row["reason"],
            "folder_id": row["folder_id"],
            **{k: row[k] for k in COUNT_FIELDS},
        }
        if projected["file"]:
            meta["file"] = {"name": projected["file"]["name"], "size": projected["file"]["size"]}
        self._audit(AUDIT_TYPES[state], meta)
        return {"ok": True, "job": projected}

    def _resume(self, request: dict[str, Any]) -> dict[str, Any]:
        """Write the resume marker the runner daemon reads; the row moves when
        the daemon actually starts (it records ``running``)."""
        job_id = str(request.get("job_id") or "")
        reason = str(request.get("reason") or "").strip()
        if not job_id:
            raise ValueError("litigation_job_resume requires job_id")
        if not reason:
            raise ValueError(
                "litigation_job_resume requires a reason: what was fixed, and why the done stages still hold"
            )
        row = self._db.read(job_id)
        if row is None:
            raise ValueError(f"no such job {job_id}")
        if row["state"] != "failed":
            raise ValueError(f"job {job_id} is {row['state']}, not failed; only a failed job resumes")
        twin = self._db.unfinished_for_scope(row["scope_key"])
        if twin is not None and twin != job_id:
            raise ValueError(f"job {twin} is already underway for this scope; resuming {job_id} would run it twice")
        self._db.queue_dir.mkdir(parents=True, exist_ok=True)
        marker = self._db.queue_dir / f".resume-{job_id}.json"
        marker.write_text(json.dumps({"job_id": job_id, "reason": reason[:500]}, sort_keys=True), encoding="utf-8")
        self._audit(RESUME_AUDIT_TYPE, {"job_id": job_id, "reason": reason[:500]})
        return {"ok": True, "job_id": job_id, "queued": True}


def litigation_dispatch(
    broker: BrokerContext, action: str, request: dict[str, Any], pid: int, peer_uid: int | None
) -> dict[str, Any]:
    verbs = broker.litigation
    if verbs is None:
        raise ValueError("litigation verbs not configured on this broker")
    return verbs.handle(action, request, peer_uid, from_gateway=pid == broker.gateway_pid)


__all__ = [
    "AUDIT_TYPES",
    "RESUME_AUDIT_TYPE",
    "SCHEDULED_TEXT",
    "VERBS",
    "LitigationVerbs",
    "litigation_dispatch",
    "month_window",
    "pacific_today",
    "scope_key",
]
