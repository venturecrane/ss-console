"""The ``records_*`` broker verbs: the request edge of the matter records lane
(``matter-records``), registered the way the case file lane's are:
``verbs.py`` declares each verb's peer classes and checks them first, and
asserts at import that its records rows and ``VERBS`` below agree.

    records_job_submit    gateway PID (the agent's tool call; the overlay sets
                          requester, message_ref and request_text from the
                          turn's verified inbound) OR uid 0 (SMD from the seat,
                          which alone may say ``ack: "none"``)
    records_job_status    gateway PID, agent uid, or uid 0 (root: the full
                          row, and with no job id the Pacific month's spend)
    records_job_record    uid 0 only (the runner daemon)
    records_job_resume    uid 0 only
    records_job_close     uid 0 only (SMD ends a held or in-review job)

THE SUBMIT CHECKS, in order, none of which spends anything: the envelope's
exact keys; the skill LISTED, ENABLED and its initiation allowing a request;
THE RECIPIENTS RULE (not the Named Administrator list): the requester, lower-
cased, is an exact address or at a ``@domain`` in
``matter_file_delivery.recipients``, read live from the seat's customer.yaml,
the same list a files reply may go to; the firm's records budget authored and
not spent this Pacific month; this email not already answered by a job, ever.

THE ACKNOWLEDGEMENT is composed here, in code, never by the model: the turn
relays the returned sentence verbatim, or sends nothing when it is ``none``.

Audit rows carry ids, counts and states, never the request text or a matter fact.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


from . import hold_class, seat_layout
from .broker_context import BrokerContext
from .litigation_intake import initiation_allows
from .litigation_verbs import month_window
from .records_ledger import (
    CLOSE_REF,
    CLOSED_REASON,
    MAX_REQUEST_TEXT,
    SKILL_NAME,
    _UUID,
    EnvelopeError,
    SubmitRefused,
    validate_envelope,
)
from .records_store import RecordsLedger
from . import seat_config

logger = logging.getLogger(__name__)

AUDIT_TYPES = {
    "queued": "RECORDS_JOB_SUBMITTED",
    "running": "RECORDS_JOB_RUNNING",
    "held": "RECORDS_JOB_HELD",
    "review": "RECORDS_JOB_REVIEW",
    "delivered": "RECORDS_JOB_DELIVERED",
    "failed": "RECORDS_JOB_FAILED",
}
RESUME_AUDIT_TYPE = "RECORDS_JOB_RESUME_REQUESTED"
FIRM_CONFIG_ENV = "SMD_RECORDS_FIRM_CONFIG"
DEFAULT_FIRM_CONFIG = seat_layout.RECORDS_BROKER_FIRM_YAML
#: Where root writes review decisions (records_contract.review_row; same env and default).
DECISIONS_DIR_ENV = "SMD_MATTER_FILE_DECISIONS_DIR"
DEFAULT_DECISIONS_DIR = "/var/lib/smd-review"
CONFIG_BLOCK = "matter_file_delivery"
VERBS = (
    "records_job_submit",
    "records_job_status",
    "records_job_record",
    "records_job_resume",
    "records_job_close",
)
NOT_STAFF = "records requests come from the firm's staff addresses; nothing was queued"
BUDGET_SPENT = "the firm's records budget for this month is spent; nothing was queued"


def acknowledgement(matter: str | None) -> str:
    """The one sentence the requester is told, word for word. No time, no mechanics.
    The matter is named only as staff wrote it (a number); a Smokeball uuid the
    turn resolved means nothing to the requester and is left out."""
    on = f" on matter {matter}" if matter and not _UUID.match(matter) else ""
    return (
        f"Received. Your request for records{on} is queued; the files and an answer "
        "for each item will come back in this thread."
    )


def refusal(reason: str, **extra: Any) -> dict[str, Any]:
    return {"ok": True, "accepted": False, "reason": reason, **extra}


class Refused(ValueError):
    """A submit check that failed; the message is the sentence to relay."""


def _load(path: str | Path) -> dict[str, Any]:
    doc = seat_config.load(Path(path), encoding="utf-8", on_missing="raise")
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


def staff_list(doc: Any) -> tuple[frozenset[str], frozenset[str]]:
    """``matter_file_delivery.recipients`` as (exact addresses, domains).
    Copied from the overlay's ``shared.matter_file_reply.staff_list`` (the broker
    cannot import the overlay); ``test_records_verbs`` pins the two read alike."""
    block = doc.get(CONFIG_BLOCK) if isinstance(doc, dict) else None
    raw = block.get("recipients") if isinstance(block, dict) else None
    exact: set[str] = set()
    domains: set[str] = set()
    for entry in raw if isinstance(raw, list) else []:
        text = str(entry or "").strip().lower()
        if text.startswith("@") and "." in text:
            domains.add(text[1:])
        elif "@" in text:
            exact.add(text)
    return frozenset(exact), frozenset(domains)


def is_staff(path: str | Path, address: str) -> bool:
    try:
        exact, domains = staff_list(_load(path))
    except Exception:  # noqa: BLE001 - unreadable authorizes nobody
        return False
    addr = str(address or "").strip().lower()
    return addr in exact or (addr.count("@") == 1 and addr.rsplit("@", 1)[1] in domains)


def firm_budget_cents(path: str | None = None) -> int | None:
    try:
        firm = _load(path or os.environ.get(FIRM_CONFIG_ENV) or DEFAULT_FIRM_CONFIG)
    except Exception:  # noqa: BLE001 - absent config enables nothing
        return None
    value = firm.get("monthly_budget_usd")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return int(round(value * 100))


class RecordsVerbs:
    def __init__(self, ledger: RecordsLedger | None, *, customer_yaml: str, audit_append: Any) -> None:
        self.ledger = ledger
        self.customer_yaml = customer_yaml
        self._audit_append = audit_append
        self._now = lambda: datetime.now(timezone.utc)
        self._decision_uid = 0  # a decision counts only when root wrote it

    @classmethod
    def build(cls, broker: BrokerContext, *, audit_db_path: str | None, queue_dir: str | None) -> RecordsVerbs:
        writer = broker.ledger
        ledger = RecordsLedger(audit_db_path, queue_dir) if audit_db_path and queue_dir and writer is not None else None

        def audit_append(row: dict[str, Any]) -> Any:
            if writer is None:
                raise ValueError("records verbs have no audit ledger on this broker")
            return writer.append(row)

        return cls(ledger, customer_yaml=str(broker.customer_path), audit_append=audit_append)

    @property
    def _db(self) -> RecordsLedger:
        if self.ledger is None:
            raise ValueError("records ledger not configured on this broker")
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
            raise ValueError("records ledger not configured on this broker")
        if action == "records_job_status":
            return self._status(request, full=peer_uid == 0)
        if action == "records_job_submit":
            return self._submit(request, peer_uid=peer_uid, from_gateway=from_gateway)
        if action == "records_job_record":
            return self._record(request)
        if action == "records_job_resume":
            return self._resume(request)
        if action == "records_job_close":
            return self._close(request)
        raise ValueError(f"unsupported records action: {action}")

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
            job = {**RecordsLedger.project(row), **({"row": row, "items": self._db.lines(job_id)} if full else {})}
            if job.get("state") == "held":  # the firm-safe token; the reason stays SMD's
                job["hold_class"] = hold_class.classify("records", job.get("reason"))[0]
            if not full:
                job.pop("reason", None)
            return {"ok": True, "job": job}
        jobs = [RecordsLedger.project(r) for r in self._db.recent(20)]
        if not full:
            for j in jobs:
                j.pop("reason", None)
        out: dict[str, Any] = {"ok": True, "jobs": jobs}
        if full:
            month, cents = self.month_cents(str(request.get("exclude_job_id") or ""))
            out.update({"month": month, "month_cents": cents, "monthly_budget_cents": firm_budget_cents()})
        return out

    # ---- submit -------------------------------------------------------------------------
    def _submit(self, request: dict[str, Any], *, peer_uid: int | None, from_gateway: bool) -> dict[str, Any]:
        if not from_gateway and peer_uid != 0:
            raise PermissionError("a records job is submitted from an email turn or by root")
        try:
            raw = request.get("envelope")
            env = validate_envelope(raw)
            if env["ack"] == "none" and peer_uid != 0:
                raise EnvelopeError('only root submits with ack "none"; an email turn acknowledges')
            entry = skill_entry(self.customer_yaml)
            if entry is None:
                raise Refused("matter records requests are not enabled on this seat; nothing was queued")
            if not initiation_allows(entry, "request"):
                raise Refused(
                    "matter records requests are not switched on by request for this seat; nothing was queued"
                )
            if not is_staff(self.customer_yaml, env["requester"]):
                raise Refused(NOT_STAFF)
            month, cents = self._budget()
            env["request_text"] = env["request_text"][:MAX_REQUEST_TEXT]
            job_id = self._db.submit(env)
        except (EnvelopeError, Refused) as exc:
            return refusal(str(exc))
        except SubmitRefused as exc:
            return refusal(f"{exc}; nothing new was queued", job_id=exc.job_id, duplicate=exc.duplicate)
        self._audit(
            AUDIT_TYPES["queued"],
            {"job_id": job_id, "requested_by": env["requester"], "request_ref": env["request_ref"],
             "ack": env["ack"], "by_root": peer_uid == 0 and not from_gateway, "month": month,
             "month_cents_used": cents},
        )  # fmt: skip
        ack = "none" if env["ack"] == "none" else acknowledgement(env["matter_id"])
        return {"ok": True, "accepted": True, "job_id": job_id, "state": "queued", "ack": ack}

    def _budget(self) -> tuple[str, int]:
        budget = firm_budget_cents()
        if budget is None:
            raise Refused("the firm's records budget is not authored on this seat; nothing was queued")
        month, cents = self.month_cents()
        if cents >= budget:
            raise Refused(BUDGET_SPENT)
        return month, cents

    # ---- the runner's record, resume and close ---------------------------------------------------
    def _record(self, request: dict[str, Any]) -> dict[str, Any]:
        job_id = str(request.get("job_id") or "")
        if not job_id:
            raise ValueError("records_job_record requires job_id")
        report = {k: v for k, v in request.items() if k not in ("action", "job_id")}
        row = self._db.record(job_id, report)
        projected = RecordsLedger.project(row)
        if row.get("prev_state") == row["state"]:
            return {"ok": True, "job": projected}
        self._audit(
            AUDIT_TYPES[row["state"]],
            {"job_id": job_id, "state": row["state"], "prev_state": row["prev_state"], "cents": row["cents"],
             "stage": row["stage"], "reason": row["reason"], "review_id": row["review_id"]},
        )  # fmt: skip
        return {"ok": True, "job": projected}

    def _resume(self, request: dict[str, Any]) -> dict[str, Any]:
        job_id, reason = str(request.get("job_id") or ""), str(request.get("reason") or "").strip()
        if not job_id or not reason:
            raise ValueError("records_job_resume requires job_id and a reason")
        row = self._db.read(job_id)
        if row is None or row["state"] != "failed" or row.get("close_ref"):
            raise ValueError(f"job {job_id} is not a failed, unclosed job; only that resumes")
        self._db.queue_dir.mkdir(parents=True, exist_ok=True)
        (self._db.queue_dir / f".resume-{job_id}.json").write_text(
            json.dumps({"job_id": job_id, "reason": reason[:500]}, sort_keys=True), encoding="utf-8"
        )
        self._audit(RESUME_AUDIT_TYPE, {"job_id": job_id, "reason": reason[:500]})
        return {"ok": True, "job_id": job_id, "queued": True}

    def _decision(self, review_id: str, dig: str) -> str | None:
        """``approve`` / ``reject`` when uid 0 decided this exact row, as the
        gateway sweeper reads it (``records_contract.review_row.decision_for``,
        approve first). The broker cannot import that package or write the
        decisions dir (it is not root), so it only reads."""
        base = Path(os.environ.get(DECISIONS_DIR_ENV, "").strip() or DEFAULT_DECISIONS_DIR)
        for word in ("approve", "reject"):
            try:
                st = os.lstat(base / f"{review_id}.{dig}.{word}")
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) and st.st_uid == self._decision_uid:
                return word
        return None

    def _row_cannot_send(self, job_id: str) -> None:
        """A job in review is closed only once its reply can no longer go out:
        root has REJECTED its row. An approve decision makes the gateway send
        the row whatever this ledger says, and a row with no decision can still
        be approved, so both refuse the close (PR #3717 review, finding 4)."""
        row = self._db.read(job_id)
        if row is None or row["state"] != "review":
            return  # the store's own check answers a missing or non-review job
        delivery = json.loads(row["delivery_json"]) if row.get("delivery_json") else {}
        review_id, dig = str(delivery.get("review_id") or ""), str(delivery.get("digest") or "")
        decided = self._decision(review_id, dig) if review_id and dig else None
        if decided == "approve":
            raise ValueError(
                f"job {job_id}'s reply is approved and the gateway will send it; a close cannot stop that, "
                "so nothing was closed"
            )
        if decided != "reject":
            raise ValueError(
                f"job {job_id}'s reply still waits for review; reject its row first "
                f"(review id {review_id or '?'}), then close"
            )

    def _close(self, request: dict[str, Any]) -> dict[str, Any]:
        job_id, reason = str(request.get("job_id") or ""), str(request.get("reason") or "").strip()
        source_ref = str(request.get("source_ref") or "").strip()
        if not job_id or not reason or not CLOSE_REF.match(source_ref):
            raise ValueError("records_job_close requires job_id, a reason and a source_ref (vfy_<ULID> or a ULID)")
        meta = {"job_id": job_id, "state": "failed", "reason": CLOSED_REASON, "source_ref": source_ref}
        self._row_cannot_send(job_id)
        if request.get("dry_run") is True:
            return {"ok": True, "dry_run": True, "job_id": job_id, "audit": meta}
        after = self._db.close(job_id, reason, source_ref)
        self._audit(AUDIT_TYPES["failed"], {**meta, "prev_state": after["prev_state"]})
        return {"ok": True, "dry_run": False, "job": RecordsLedger.project(after), "audit": meta}


def records_dispatch(
    broker: BrokerContext, action: str, request: dict[str, Any], pid: int, peer_uid: int | None
) -> dict[str, Any]:
    verbs = getattr(broker, "records", None)
    if verbs is None:
        raise ValueError("records verbs not configured on this broker")
    return verbs.handle(action, request, peer_uid, from_gateway=pid == broker.gateway_pid)


__all__ = ["AUDIT_TYPES", "VERBS", "RecordsVerbs", "acknowledgement", "records_dispatch", "staff_list"]
