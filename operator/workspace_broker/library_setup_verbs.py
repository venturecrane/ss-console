"""The request edge of a "set up our ..." job (``library_setup_ledger``): the
``library_setup_submit`` verb, and the drafting verbs' status and record for a
setup job's id (``drafting_verbs.drafting_dispatch`` hands those here).

THE SUBMIT CHECKS, in order, none of which spends anything:

1. the kind is one a firm can set up this way (a mediation brief or a memo);
2. the drafting lane runs on this seat (its ledger and queue exist) and the
   ``document-library-establishment`` skill is listed and enabled;
3. the requester is one of the firm's Named Administrators (``scope.admins``);
4. the firm's own library matter is authored
   (``self_initiation.document_library.operator_matter.number``), the job's
   filing target;
5. the envelope is exactly ``library_setup_ledger.validate_envelope``'s shape
   (the overlay fills who asked, the email and its words from the turn's
   verified inbound; the model gives only the kind and any attorney's name),
   and no setup of the same kind for the same attorney (or the firm) is
   unfinished.

No allowance is counted: the job runs no model and files nothing.

THE SWITCH is read here too (``logic_in_code.library_setup``): ``live`` on the
answer and on the queue file is true only when it enforces and reads cleanly.
Live, the answer carries ``say`` (the acknowledgement) for the turn to relay;
dark, the turn answers the request as it does today, and the job only counts.

Every sentence a firm can read here comes from ``library_setup_wording``.

AUDIT ROWS OF ITS OWN, AND ONLY ON A SWITCHED-ON SEAT. ``LIBRARY_SETUP_*``
(submitted, delivered, chosen, accepted, held, failed), each with its portal
activity line in ``library_setup_wording.ACTIVITY``. A dark job writes none:
the firm's portal activity must not show a job that reaches no one. Its record
is its ledger row. Never the drafting job's types: a setup job drafts nothing.

THE CHOICE AND THE HAND-OFF live in ``library_setup_choose.py``.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from . import library_setup_choose as choose_mod, library_setup_wording as wording
from .broker_context import BrokerContext
from .drafting_intake import operator_library_number
from .library_setup_ledger import STATES, EnvelopeError, SetupLedger, Underway, of_drafting
from .logic_in_code import LIBRARY_SETUP, load_switch
from .medchron_ledger import admins_from_customer_yaml


def _obj(value: Any) -> dict[str, Any]:
    """``value`` when it is an object, else an empty one."""
    return value if isinstance(value, dict) else {}


VERBS = ("library_setup_submit", "library_setup_choose", "library_setup_accept_submit", "library_setup_accept_status")
SKILL_NAME = "document-library-establishment"


def skill_enabled(path: str) -> bool:
    """The establishment skill is listed on a persona and not disabled."""
    try:
        import yaml

        with open(path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001 - an unreadable seat config enables nothing
        return False
    for persona in doc.get("personas") or []:
        for skill in (persona or {}).get("skills") or []:
            if isinstance(skill, dict) and skill.get("name") == SKILL_NAME:
                return skill.get("enabled") is not False
    return False


def live(path: str) -> bool:
    """The switch enforces and reads cleanly, now."""
    sw = load_switch(LIBRARY_SETUP, path)
    return sw.enforcing and sw.error is None


def ledger_of(drafting_verbs: Any) -> SetupLedger | None:
    db = getattr(drafting_verbs, "ledger", None)
    return of_drafting(db) if db is not None else None


def owns(drafting_verbs: Any, request: dict[str, Any]) -> bool:
    """A status or record for one job whose id is a setup job's."""
    job_id = str(request.get("job_id") or "")
    db = ledger_of(drafting_verbs) if job_id else None
    return db is not None and db.owns(job_id)


def _refused(key: str, **values: Any) -> dict[str, Any]:
    return {"ok": True, "accepted": False, "reason": wording.say(key, **values), "reason_key": key}


def _checks(verbs: Any, raw: dict[str, Any]) -> dict[str, Any] | str:
    """The library matter number, or the refusal's wording key."""
    path = str(verbs.customer_yaml)
    if str(raw.get("kind") or "") not in wording.KIND_NAMES:
        return "refused_kind"
    if ledger_of(verbs) is None or not skill_enabled(path):
        return "refused_seat"
    admins = admins_from_customer_yaml(path)
    if not admins or str(raw.get("requested_by") or "").strip().lower() not in admins:
        return "refused_admin"
    library = operator_library_number(path)
    return {"library": library} if library else "refused_seat"


def submit(verbs: Any, request: dict[str, Any]) -> dict[str, Any]:
    raw = request.get("envelope")
    raw = raw if isinstance(raw, dict) else {}
    checked = _checks(verbs, raw)
    if isinstance(checked, str):
        return _refused(checked)
    db = ledger_of(verbs)
    assert db is not None  # _checks refused a seat without one
    kind = str(raw["kind"])
    on = live(str(verbs.customer_yaml))
    try:
        job_id = db.submit(raw, library_number=checked["library"], live=on)
    except Underway as exc:
        return {**_refused("refused_underway", **wording.names(kind)), "job_id": exc.job_id}
    except EnvelopeError as exc:
        if "attorney" in str(exc):
            return _refused("refused_attorney")
        return {"ok": True, "accepted": False, "reason": f"{exc}; nothing was queued"}
    out: dict[str, Any] = {"ok": True, "accepted": True, "job_id": job_id, "live": on, "state": "submitted"}
    if on:
        audit(verbs)("LIBRARY_SETUP_SUBMITTED", {"job_id": job_id, "document_class": kind})
        out["say"] = wording.ack(kind, " ".join(str(raw.get("attorney") or "").split()) or None)
    return out


def status(verbs: Any, request: dict[str, Any], *, full: bool) -> dict[str, Any]:
    db = ledger_of(verbs)
    row = db.read(str(request.get("job_id") or "")) if db is not None else None
    if row is None:
        return {"ok": True, "job": None}
    return {"ok": True, "job": row if full else SetupLedger.project(row)}


def record(verbs: Any, request: dict[str, Any]) -> dict[str, Any]:
    db = ledger_of(verbs)
    job_id, state, fields = str(request.get("job_id") or ""), str(request.get("state") or ""), request.get("fields")
    if db is None or not job_id or state not in STATES or not isinstance(fields, dict):
        raise ValueError("drafting_job_record requires job_id, a known state, and a fields object")
    row = db.record(job_id, state, dict(fields))
    kind = _audit_kind(row, state)
    if kind and row.get("prev_state") != state and row.get("live"):
        report = _obj(fields.get("report"))
        meta = {"job_id": job_id, "mode": row.get("mode"), "state": state, "setup_outcome": report.get("setup_outcome")}
        audit(verbs)(
            kind, {**meta, "candidates_count": len(report.get("candidates") or []), "hold": report.get("hold")}
        )
    return {"ok": True, "job": SetupLedger.project(row)}


def _audit_kind(row: dict[str, Any], state: str) -> str:
    """The audit type a live job's move writes; "" for a move that writes none."""
    if state == "delivered":
        outcome = _outcome(row)
        accepted = row.get("mode") == "accept" and outcome in ("accepted", "accepted_pending")
        return "LIBRARY_SETUP_ACCEPTED" if accepted else "LIBRARY_SETUP_DELIVERED"
    return {"held": "LIBRARY_SETUP_HELD", "failed": "LIBRARY_SETUP_FAILED"}.get(state, "")


def _outcome(row: dict[str, Any]) -> str:
    try:
        report = json.loads(row.get("report_json") or "{}")
    except ValueError:
        return ""
    return str(report.get("setup_outcome") or "") if isinstance(report, dict) else ""


def audit(verbs: Any) -> Callable[[str, dict[str, Any]], None]:
    """An audit appender for a switched-on seat's setup job: ids, codes and counts only."""

    def append(action_type: str, metadata: dict[str, Any]) -> None:
        verbs._audit_append(
            {
                "action_type": action_type,
                "actor": "workspace-broker",
                "actor_role": "broker",
                "skill_name": SKILL_NAME,
                "matter_ref": None,
                "metadata": json.dumps({k: v for k, v in metadata.items() if v is not None}, sort_keys=True),
            }
        )

    return append


def handle(verbs: Any, action: str, request: dict[str, Any], peer_uid: int | None) -> dict[str, Any]:
    """A setup job's status or record through the drafting verbs."""
    if action == "drafting_job_status":
        return status(verbs, request, full=peer_uid == 0)
    if action == "drafting_job_record":
        return record(verbs, request)
    raise ValueError(f"unsupported action for a library setup job: {action}")


def dispatch(
    broker: BrokerContext, action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    """``library_setup_submit`` and ``library_setup_choose``: the gateway's tool
    call, or root. ``library_setup_accept_submit`` / ``_status``: root only (the
    verb table's gate)."""
    if action not in VERBS:
        raise ValueError(f"unsupported library setup action: {action}")
    verbs = getattr(broker, "drafting", None)
    db = ledger_of(verbs) if verbs is not None else None
    if verbs is None or db is None:
        return _refused("refused_seat")
    if action == "library_setup_submit":
        return submit(verbs, request)
    if action == "library_setup_choose":
        return choose_mod.choose(verbs, db, request, audit(verbs))
    store = getattr(broker, "establishment", None)
    if action == "library_setup_accept_submit":
        return choose_mod.accept_submit(store, db, request)
    return choose_mod.accept_status(store, db, request)


__all__ = ["SKILL_NAME", "VERBS", "dispatch", "handle", "live", "owns", "submit"]
