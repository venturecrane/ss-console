"""The ``casefile_*`` broker verbs: the request edge of the Case File Manager
(``case-file-manager``), registered the way the litigation lane's are:
``verbs.py`` declares each verb's peer classes and checks them first, and
asserts at import that its casefile rows and ``VERBS`` below agree.

    casefile_job_submit    gateway PID (the agent's tool call; the overlay sets
                           requester, message_ref and request_text from the
                           turn's verified inbound) OR uid 0
    casefile_job_status    gateway PID, agent uid, or uid 0 (root: the full
                           row, and with no job id the Pacific month's spend)
    casefile_job_record    uid 0 only (the runner daemon)
    casefile_job_resume    uid 0 only
    casefile_job_close     uid 0 only (SMD ends a held job it finished by hand)
    casefile_apply_submit  gateway PID (the casework reply path, from the
                           verified reply; never the model) OR uid 0

THE SUBMIT CHECKS, in order, none of which spends anything: the envelope's
shape; the skill LISTED, ENABLED and its initiation allowing a request; the
requester a Named Administrator (``scope.admins``); the filing target authored
(``file_to_matter_id`` and ``folder_name`` in the skill's settings, and the
firm's library matter number); the firm's case file budget authored and not
spent this Pacific month; no unfinished job for the same caseload, and this
email not already queued. Only ``full`` is offered (``recheck`` is refused
until it ships).

THE APPLY (``casefile_apply_submit``). The broker, never the model, builds the
line set: every line a delivered case file job RAISED on the reply's thread
(``casefile_lines``), whose casework line on that thread and number is
``approved`` with ``decided_by`` naming a Named Administrator the firm
authored, and which no apply holds or finished. Those lines move to
``applying`` under one apply job in the same write transaction that queues it.
The runner then re-reads each record and refuses anything changed since the
proposal (``casefile/apply.py``).

THE CASEWORK LEDGER. A delivered job's lines are raised there by the broker
itself at the bound reply (``raise_lines``, from ``reply_binding``), on the
thread that reply went out on; an apply job's outcomes close them there
(``completed`` for a write the runner read back, ``write_failed`` otherwise),
witnessed by the job's own record.

Audit rows carry ids, counts and states, never the request text or a matter fact.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from . import casework_ledger, hold_class
from .broker_context import BrokerContext
from .casefile_ledger import (
    APPLIED_OUTCOMES,
    CLOSE_REF,
    CLOSED_REASON,
    COUNT_FIELDS,
    MAX_REQUEST_TEXT,
    SKILL_NAME,
    STATES,
    EnvelopeError,
    SubmitRefused,
    validate_envelope,
)
from .casefile_store import CasefileLedger
from .casework_verbs import casework_ledger_path
from .litigation_intake import initiation_allows
from .litigation_verbs import month_window
from .medchron_ledger import admins_from_customer_yaml
from .recipient_policy import sender_key

logger = logging.getLogger(__name__)

AUDIT_TYPES = {
    "queued": "CASEFILE_JOB_SUBMITTED",
    "running": "CASEFILE_JOB_RUNNING",
    "held": "CASEFILE_JOB_HELD",
    "delivered": "CASEFILE_JOB_DELIVERED",
    "failed": "CASEFILE_JOB_FAILED",
}
RESUME_AUDIT_TYPE = "CASEFILE_JOB_RESUME_REQUESTED"
APPLY_AUDIT_TYPE = "CASEFILE_APPLY_SUBMITTED"
FIRM_CONFIG_ENV = "SMD_CASEFILE_FIRM_CONFIG"
DEFAULT_FIRM_CONFIG = "/var/lib/smd-config/casefile-broker-firm.yaml"
FILE_TO_KEY = "file_to_matter_id"
FOLDER_KEY = "folder_name"
VERBS = (
    "casefile_job_submit",
    "casefile_job_status",
    "casefile_job_record",
    "casefile_job_resume",
    "casefile_job_close",
    "casefile_apply_submit",
)
_REQUEST_KEYS = {"trigger", "requester", "message_ref", "request_text", "scope"}


class _Refused(ValueError):
    """A submit check that failed; the message is the sentence to relay."""


def _refusal(reason: str, **extra: Any) -> dict[str, Any]:
    return {"ok": True, "accepted": False, "reason": reason, **extra}


def _load(path: str | Path) -> dict[str, Any]:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return doc if isinstance(doc, dict) else {}


def skill_entry(path: str | Path) -> dict[str, Any] | None:
    """The skill's entry, or None unless the seat LISTS it and does not
    disable it. Omission is refusal (ADR 0035)."""
    try:
        doc = _load(path)
    except Exception:  # noqa: BLE001 - an unreadable seat config enables nothing
        return None
    for persona in doc.get("personas") or []:
        for skill in (persona or {}).get("skills") or []:
            if isinstance(skill, dict) and skill.get("name") == SKILL_NAME:
                return None if skill.get("enabled") is False else skill
    return None


def _library_number(path: str | Path) -> str | None:
    try:
        number = _load(path)["self_initiation"]["document_library"]["operator_matter"]["number"]
    except Exception:  # noqa: BLE001 - unauthored or unreadable authorizes nothing
        return None
    return number.strip() if isinstance(number, str) and number.strip() else None


def firm_budget_cents(path: str | None = None) -> int | None:
    try:
        firm = _load(path or os.environ.get(FIRM_CONFIG_ENV) or DEFAULT_FIRM_CONFIG)
    except Exception:  # noqa: BLE001 - absent config enables nothing
        return None
    value = firm.get("monthly_budget_usd")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return int(round(value * 100))


def _scope_in(raw: Any) -> dict[str, Any]:
    """A turn's scope: ``{"assisting": [names]}`` or ``{"matters": [numbers]}``."""
    if isinstance(raw, dict) and set(raw) == {"assisting"}:
        return {"assisting": {"names": raw["assisting"]}}
    if isinstance(raw, dict) and set(raw) == {"matters"}:
        return {"matters": {"numbers": raw["matters"]}}
    if isinstance(raw, dict) and set(raw) == {"assisting_staff_ids"}:
        return raw
    raise EnvelopeError("scope names the people whose caseload to read (assisting) or matter numbers (matters)")


class CasefileVerbs:
    def __init__(
        self,
        ledger: CasefileLedger | None,
        *,
        customer_yaml: str,
        audit_append: Any,
        broker: BrokerContext | None = None,
    ) -> None:
        self.ledger = ledger
        self.customer_yaml = customer_yaml
        self._audit_append = audit_append
        self.broker = broker
        self._now = lambda: datetime.now(timezone.utc)

    @classmethod
    def build(cls, broker: BrokerContext, *, audit_db_path: str | None, queue_dir: str | None) -> CasefileVerbs:
        writer = broker.ledger
        ledger = (
            CasefileLedger(audit_db_path, queue_dir) if audit_db_path and queue_dir and writer is not None else None
        )

        def audit_append(row: dict[str, Any]) -> Any:
            if writer is None:
                raise ValueError("casefile verbs have no audit ledger on this broker")
            return writer.append(row)

        return cls(ledger, customer_yaml=str(broker.customer_path), audit_append=audit_append, broker=broker)

    @property
    def _db(self) -> CasefileLedger:
        if self.ledger is None:
            raise ValueError("casefile ledger not configured on this broker")
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

    def handle(self, action: str, request: dict[str, Any], peer_uid: int | None, *, from_gateway: bool = False) -> dict:
        if self.ledger is None:
            raise ValueError("casefile ledger not configured on this broker")
        if action == "casefile_job_status":
            return self._status(request, full=peer_uid == 0)
        if action == "casefile_job_submit":
            return self._submit(request, peer_uid=peer_uid, from_gateway=from_gateway)
        if action == "casefile_apply_submit":
            return self._apply(request, peer_uid=peer_uid, from_gateway=from_gateway)
        if action == "casefile_job_record":
            return self._record(request)
        if action == "casefile_job_resume":
            return self._resume(request)
        if action == "casefile_job_close":
            return self._close(request)
        raise ValueError(f"unsupported casefile action: {action}")

    # ---- status ----------------------------------------------------------------------
    def month_cents(self, exclude: str = "") -> tuple[str, int]:
        month, start, end = month_window(self._now())
        return month, self._db.spend_between(start, end, exclude)

    def _status(self, request: dict[str, Any], *, full: bool) -> dict[str, Any]:
        job_id = str(request.get("job_id") or "")
        if job_id:
            row = self._db.read(job_id)
            if row is None:
                return {"ok": True, "job": None}
            job = {**CasefileLedger.project(row), **({"row": row} if full else {})}
            if job.get("state") == "held":  # the firm-safe token; the reason stays SMD's
                job["hold_class"] = hold_class.classify("casefile", job.get("reason"))[0]
            return {"ok": True, "job": job}
        out: dict[str, Any] = {"ok": True, "jobs": [CasefileLedger.project(r) for r in self._db.recent(20)]}
        if full:
            month, cents = self.month_cents(str(request.get("exclude_job_id") or ""))
            out.update({"month": month, "month_cents": cents, "monthly_budget_cents": firm_budget_cents()})
        return out

    # ---- submit -------------------------------------------------------------------------
    def _gates(self, peer_uid: int | None, from_gateway: bool) -> tuple[dict[str, Any], tuple[str, ...]]:
        if not from_gateway and peer_uid != 0:
            raise PermissionError("a case file job is submitted from an email turn or by root")
        entry = skill_entry(self.customer_yaml)
        if entry is None:
            raise _Refused("the Case File Manager is not enabled on this seat; nothing was queued")
        if not initiation_allows(entry, "request"):
            raise _Refused("the Case File Manager is not switched on by request for this seat; nothing was queued")
        admins = admins_from_customer_yaml(self.customer_yaml)
        if not admins:
            raise _Refused("the seat's administrator list cannot be read, so who may ask cannot be established")
        raw = entry.get("settings")
        settings: dict[str, Any] = raw if isinstance(raw, dict) else {}
        return settings, admins

    def _submit(self, request: dict[str, Any], *, peer_uid: int | None, from_gateway: bool) -> dict[str, Any]:
        if not from_gateway and peer_uid != 0:
            raise PermissionError("a case file job is submitted from an email turn or by root")
        try:
            raw = request.get("envelope")
            if not isinstance(raw, dict) or set(raw) != _REQUEST_KEYS or raw.get("trigger") != "request":
                raise EnvelopeError(f"a request envelope carries exactly {sorted(_REQUEST_KEYS)}, trigger request")
            if "assisting_staff_ids" in (raw.get("scope") or {}) and peer_uid != 0:
                raise PermissionError("staff ids are named by root only; a turn names people")
            settings, admins = self._gates(peer_uid, from_gateway)
            requester = str(raw["requester"] or "").strip().lower()
            if requester not in admins:
                raise _Refused("a case file review may only be requested by one of the firm's Named Administrators")
            library, file_to, folder = self._filing(settings)
            month, cents = self._budget()
            env = {
                "trigger": "request",
                "mode": "full",
                "requester": requester,
                "message_ref": raw["message_ref"],
                "request_text": str(raw["request_text"])[:MAX_REQUEST_TEXT],
                "scope": _scope_in(raw["scope"]),
                "file_to_matter_id": file_to,
                "file_to_matter_number": library,
                "folder_name": folder,
            }
            valid = validate_envelope(env)
            job_id = self._db.submit(valid)
        except (EnvelopeError, _Refused) as exc:
            return _refusal(str(exc))
        except SubmitRefused as exc:
            return _refusal(f"{exc}; nothing new was queued", **({"job_id": exc.job_id} if exc.job_id else {}))
        self._audit(
            AUDIT_TYPES["queued"],
            {"job_id": job_id, "mode": "full", "scope": next(iter(valid["scope"])), "requested_by": requester,
             "request_ref": valid["message_ref"], "month": month, "month_cents_used": cents},
        )  # fmt: skip
        return {"ok": True, "accepted": True, "job_id": job_id, "state": "queued", "mode": "full"}

    def _filing(self, settings: dict[str, Any]) -> tuple[str, str, str]:
        library = _library_number(self.customer_yaml)
        file_to = str(settings.get(FILE_TO_KEY) or "").strip()
        folder = str(settings.get(FOLDER_KEY) or "").strip()
        if not (library and file_to and folder):
            raise _Refused("where the caseload list is filed is not authored on this seat; nothing was queued")
        return library, file_to, folder

    def _budget(self) -> tuple[str, int]:
        budget = firm_budget_cents()
        if budget is None:
            raise _Refused("the firm's Case File Manager budget is not authored on this seat; nothing was queued")
        month, cents = self.month_cents()
        if cents >= budget:
            raise _Refused(f"this month's Case File Manager budget is spent ({month}); nothing was queued")
        return month, cents

    # ---- apply ------------------------------------------------------------------------------
    def approved_lines(self, thread_ref: str) -> tuple[str, list[dict[str, Any]]]:
        """(source job id, the lines open to apply) on this thread: raised
        there, approved there under a Named Administrator's key, not taken."""
        rows = [r for r in self._db.lines_on_thread(thread_ref) if r["state"] == "raised"]
        if not rows:
            return "", []
        source = rows[-1]["job_id"]
        admins = {sender_key(a) for a in admins_from_customer_yaml(self.customer_yaml) or ()} - {None}
        path = casework_ledger_path(self.broker) if self.broker is not None else casework_ledger.ledger_path()
        states = casework_ledger.derive_state(casework_ledger.read_ledger(path))
        out = []
        for r in rows:
            if r["job_id"] != source:
                continue
            key = casework_ledger.item_key(matter_id=r["matter_id"], kind=r["kind"], source_id=r["source_id"])
            st = states.get(key)
            d = st.decisions.get((thread_ref, int(r["n"]))) if st is not None else None
            by = (d.decided_by or {}).get("key") if d is not None else None
            if d is not None and d.verdict == "approved" and by in admins:
                out.append({k: r[k] for k in ("n", "kind", "matter_id", "source_id", "sha256")})
        return source, out

    def _apply(self, request: dict[str, Any], *, peer_uid: int | None, from_gateway: bool) -> dict[str, Any]:
        if not from_gateway and peer_uid != 0:
            raise PermissionError("an apply is submitted from the approver's verified reply or by root")
        thread = str(request.get("thread_ref") or "")
        approver = str(request.get("approver") or "").strip().lower()
        if approver not in (admins_from_customer_yaml(self.customer_yaml) or ()):
            return _refusal("only a Named Administrator's approval is applied; nothing was queued")
        source, lines = self.approved_lines(thread)
        if not lines:
            return _refusal("nothing_approved: no approved change on that thread is open to apply")
        env = {
            "trigger": "request",
            "mode": "apply",
            "requester": approver,
            "message_ref": request.get("message_ref"),
            "request_text": str(request.get("request_text") or "approval")[:MAX_REQUEST_TEXT],
            "apply": {"source_job_id": source, "approver": approver, "thread_ref": thread, "lines": lines},
        }
        try:
            job_id = self._db.submit(validate_envelope(env))
        except (EnvelopeError, SubmitRefused) as exc:
            return _refusal(str(exc))
        self._audit(APPLY_AUDIT_TYPE, {"job_id": job_id, "source_job_id": source, "lines": [ln["n"] for ln in lines]})
        return {"ok": True, "accepted": True, "job_id": job_id, "lines": [ln["n"] for ln in lines]}

    # ---- the runner's record, resume and close ---------------------------------------------------
    def _record(self, request: dict[str, Any]) -> dict[str, Any]:
        job_id, state, fields = str(request.get("job_id") or ""), str(request.get("state") or ""), request.get("fields")
        if not job_id or state not in STATES or not isinstance(fields, dict):
            raise ValueError("casefile_job_record requires job_id, a known state, and a fields object")
        row = self._db.record(job_id, state, dict(fields))
        projected = CasefileLedger.project(row)
        if row.get("prev_state") == state:
            return {"ok": True, "job": projected}
        meta: dict[str, Any] = {
            "job_id": job_id,
            "state": state,
            "mode": row["mode"],
            "cents": row["cents"],
            "stage": row["stage"],
            "reason": row["reason"],
            **{k: row[k] for k in COUNT_FIELDS},
        }
        if "writes" in fields:
            meta["writes"] = fields["writes"][:500]
        self._audit(AUDIT_TYPES[state], meta)
        if state == "delivered" and row["mode"] == "apply":
            self._close_casework(row)
        return {"ok": True, "job": projected}

    def _close_casework(self, row: dict[str, Any]) -> None:
        """Each applied line's casework authorization, consumed: ``completed``
        for a write the runner read back, ``write_failed`` for everything else."""
        if self.broker is None:
            return
        path = casework_ledger_path(self.broker)
        if not path:
            return
        outcomes = {int(o["n"]): o["outcome"] for o in json.loads(row.get("outcomes_json") or "[]")}
        with self.broker._escalation_lock:
            existing = casework_ledger.read_ledger(path)
            for ln in self._db.lines(row["source_job_id"]):
                if ln.get("apply_job_id") != row["id"] or ln["n"] not in outcomes:
                    continue
                ev = casework_event(ln, "completed" if outcomes[ln["n"]] in APPLIED_OUTCOMES else "write_failed")
                if ev["event"] == "completed":
                    ev["tool_call_id"] = f"casefile:{row['id']}:{ln['n']}"
                else:
                    ev["error"] = f"case file apply: {outcomes[ln['n']]}"
                try:
                    casework_ledger.validate_append(
                        existing, ev, send_witness=lambda _e: True, audit_witness=lambda _e: True
                    )
                except ValueError:
                    continue  # no open authorization (already consumed): nothing to close
                stamped = casework_ledger.stamp_event(ev)
                casework_ledger.append_line(path, stamped)
                existing.append(stamped)

    def _resume(self, request: dict[str, Any]) -> dict[str, Any]:
        job_id, reason = str(request.get("job_id") or ""), str(request.get("reason") or "").strip()
        if not job_id or not reason:
            raise ValueError("casefile_job_resume requires job_id and a reason")
        row = self._db.read(job_id)
        if row is None or row["state"] != "failed" or row.get("close_ref"):
            raise ValueError(f"job {job_id} is not a failed, unclosed job; only that resumes")
        self._db.queue_dir.mkdir(parents=True, exist_ok=True)
        (self._db.queue_dir / f".resume-{job_id}.json").write_text(
            json.dumps({"job_id": job_id, "reason": reason[:500]}, sort_keys=True), encoding="utf-8"
        )
        self._audit(RESUME_AUDIT_TYPE, {"job_id": job_id, "reason": reason[:500]})
        return {"ok": True, "job_id": job_id, "queued": True}

    def _close(self, request: dict[str, Any]) -> dict[str, Any]:
        job_id, reason = str(request.get("job_id") or ""), str(request.get("reason") or "").strip()
        source_ref = str(request.get("source_ref") or "").strip()
        if not job_id or not reason or not CLOSE_REF.match(source_ref):
            raise ValueError("casefile_job_close requires job_id, a reason and a source_ref (vfy_<ULID> or a ULID)")
        meta = {
            "job_id": job_id,
            "state": "failed",
            "prev_state": "held",
            "reason": CLOSED_REASON,
            "source_ref": source_ref,
        }
        if request.get("dry_run") is True:
            return {"ok": True, "dry_run": True, "job_id": job_id, "audit": meta}
        after = self._db.close(job_id, reason, source_ref)
        self._audit(AUDIT_TYPES["failed"], meta)
        return {"ok": True, "dry_run": False, "job": CasefileLedger.project(after), "audit": meta}


def casework_event(line: dict[str, Any], event: str, **extra: Any) -> dict[str, Any]:
    """One casework row for a case file line (the broker stamps ts and id)."""
    return {
        "event": event,
        "skill": SKILL_NAME,
        "kind": line["kind"],
        "matter_id": line["matter_id"],
        "source_id": line["source_id"],
        "item_key": casework_ledger.item_key(
            matter_id=line["matter_id"], kind=line["kind"], source_id=line["source_id"]
        ),
        **extra,
    }


def raise_lines(broker: BrokerContext, job_id: str, thread_ref: str, session_id: str) -> int:
    """The bound reply for a delivered case file job just went out on
    ``thread_ref``: every proposed line becomes a numbered ``proposed`` row on
    the casework ledger, on that thread, so a reply there can approve it by
    number. Returns the count raised. Never raises: the reply already went."""
    verbs = getattr(broker, "casefile", None)
    path = casework_ledger_path(broker)
    if verbs is None or verbs.ledger is None or not path or not thread_ref:
        return 0
    dispatch = secrets.token_hex(16)
    lines = verbs.ledger.mark_raised(job_id, thread_ref, dispatch)
    raised = 0
    with broker._escalation_lock:
        existing = casework_ledger.read_ledger(path)
        for ln in lines:
            action = "close" if ln["kind"] == "task" else "add_row"
            ev = casework_event(
                ln, "proposed", n=int(ln["n"]), dispatch_ref=dispatch, thread_ref=thread_ref,
                payload={"action": action, "class": "done" if action == "close" else "open",
                         "reason": f"case file job {job_id} change {ln['n']}",
                         "evidence": [f"job:{job_id}", f"sha:{ln['sha256'][:16]}"]},
                **({"session_id": session_id} if session_id else {}),
            )  # fmt: skip
            try:
                casework_ledger.validate_append(
                    existing, ev, send_witness=lambda _e: True, audit_witness=lambda _e: True
                )
            except ValueError:
                continue
            stamped = casework_ledger.stamp_event(ev)
            casework_ledger.append_line(path, stamped)
            existing.append(stamped)
            raised += 1
    return raised


def casefile_ledger_of(broker: BrokerContext) -> CasefileLedger:
    """The ledger the reply binding reads (a read: the queue dir is never written)."""
    queue = os.environ.get("SMD_CASEFILE_QUEUE_DIR") or "/run/smd-medchron/casefile-queue"
    return CasefileLedger(str(broker.audit_db_path), queue)


def after_bound_reply(broker: BrokerContext, v: Any, session_id: str) -> None:
    """A delivered case file job's bound reply just went out: its numbered
    changes become casework lines on that reply's thread (``raise_lines``), so
    the requester can approve them by number there. The broker raises them
    itself; the model never names a line. Never raises: the reply went."""
    row = getattr(v, "row", None) or {}
    if getattr(v, "kind", "") != "casefile_job" or row.get("state") != "delivered" or row.get("mode") != "full":
        return
    try:
        raised = raise_lines(broker, v.job_id, v.conversation_id, session_id)
    except Exception:
        logger.exception("could not raise the case file lines of %s", v.job_id)
        return
    if raised and broker.ledger is not None:
        broker.ledger.append(
            {
                "action_type": "CASEFILE_LINES_PROPOSED",
                "actor": "workspace-broker",
                "actor_role": "broker",
                "skill_name": SKILL_NAME,
                "metadata": json.dumps({"job_id": v.job_id, "lines": raised}, sort_keys=True, separators=(",", ":")),
            }
        )


def casefile_dispatch(
    broker: BrokerContext, action: str, request: dict[str, Any], pid: int, peer_uid: int | None
) -> dict[str, Any]:
    verbs = getattr(broker, "casefile", None)
    if verbs is None:
        raise ValueError("casefile verbs not configured on this broker")
    return verbs.handle(action, request, peer_uid, from_gateway=pid == broker.gateway_pid)


__all__ = [
    "AUDIT_TYPES",
    "VERBS",
    "CasefileVerbs",
    "after_bound_reply",
    "casefile_dispatch",
    "casefile_ledger_of",
    "raise_lines",
]
