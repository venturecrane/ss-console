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

NO AUDIT ROW OF ITS OWN (3a). The record is the job's ledger row
(``library_setup_jobs``: ids, codes, counts and times, in the audit database
file), and a live reply is audited by the reply binding as every reply is. The
drafting job's audit types would put "Started drafting a document for a matter"
in the firm's portal activity for a job that drafts nothing, a dark one
included, which must reach no one; types of its own each carry a portal activity
sentence, firm-facing words that belong with the Captain's read of
``library_setup_wording`` before any seat is switched on.
"""

from __future__ import annotations

from typing import Any

from . import library_setup_wording as wording
from .broker_context import BrokerContext
from .drafting_intake import operator_library_number
from .library_setup_ledger import STATES, EnvelopeError, SetupLedger, Underway, of_drafting
from .logic_in_code import LIBRARY_SETUP, load_switch
from .medchron_ledger import admins_from_customer_yaml

VERBS = ("library_setup_submit",)
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
    return {"ok": True, "job": SetupLedger.project(db.record(job_id, state, dict(fields)))}


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
    """``library_setup_submit``: the gateway's tool call, or root."""
    if action != "library_setup_submit":
        raise ValueError(f"unsupported library setup action: {action}")
    verbs = getattr(broker, "drafting", None)
    if verbs is None:
        return _refused("refused_seat")
    return submit(verbs, request)


__all__ = ["SKILL_NAME", "VERBS", "dispatch", "handle", "live", "owns", "submit"]
