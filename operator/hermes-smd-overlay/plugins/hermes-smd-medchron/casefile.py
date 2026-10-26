"""Agent-facing tools for the Case File Manager (a caseload's files read, a
current File Status filed in each, the ranked list filed to the firm's library,
and numbered changes for a person to approve; ss-console casefile_verbs.py).

The litigation status job's shape (litigation.py). WHO ASKED IS NEVER THE
MODEL'S TO SAY: ``requester``, ``message_ref`` and ``request_text`` are filled
HERE from the turn's verified inbound origin; the tool schema has no field for
any of them. A turn no email opened submits nothing.

The model chooses only the scope: the people whose caseload to read (each the
Person Assisting on the matters, by name as the email gives it; the runner
resolves the name against the firm's staff list and holds the job when it
cannot), or matter numbers. The broker checks the rest (the skill enabled, the
requester on scope.admins, the filing target, the monthly budget, no
unfinished job for that caseload) and answers with a job id or a reason to
relay as it comes back.

There is no apply tool: a reply approving numbered changes is handled by the
casework reply path, which asks the broker to apply exactly the lines that
reply approved.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from shared.medchron_client import MedchronBrokerClient, MedchronBrokerError

from .demand import STRING, _origin, _refuse, _schema

logger = logging.getLogger(__name__)

MAX_NAMES = 10
MAX_NUMBERS = 200
MAX_CHARS = 100
_STATUS_KEYS = (
    "job_id",
    "state",
    "stage",
    "mode",
    "matters_total",
    "matters_read",
    "matters_current",
    "matters_held",
    "lines_proposed",
)


def _list(key: str, raw: Any, cap: int) -> list[str] | str:
    if not isinstance(raw, list) or not all(isinstance(n, str) for n in raw):
        return f"{key} must be a list; nothing was queued"
    cleaned = list(dict.fromkeys(" ".join(n.split()) for n in raw))
    if not cleaned or len(cleaned) > cap or any(not n or len(n) > MAX_CHARS for n in cleaned):
        return f"{key} must be 1 to {cap} entries of at most {MAX_CHARS} characters; nothing was queued"
    return cleaned


def _scope(args: dict[str, Any]) -> dict[str, Any] | str:
    people, numbers = args.get("assisting"), args.get("matters")
    if people and numbers:
        return "name the people whose caseload to read, or the matter numbers, not both; nothing was queued"
    if people:
        got = _list("assisting", people, MAX_NAMES)
        return got if isinstance(got, str) else {"assisting": got}
    if numbers:
        got = _list("matters", numbers, MAX_NUMBERS)
        return got if isinstance(got, str) else {"matters": got}
    return "name whose caseload to read (the person assisting on the files) or the matter numbers; nothing was queued"


def casefile_job_submit(args: dict[str, Any], session_id: str = "", **_: Any) -> str:
    origin = _origin(session_id)
    if origin is None or not origin.sender_address:
        return _refuse(
            "a case file review is submitted only from the turn of the email that asked for it; this "
            "turn was not opened by one, so nothing was queued"
        )
    if not origin.internet_message_id:
        return _refuse(
            "the request email's message id is not available on this turn; nothing was queued"
        )
    if not (origin.reply_text or "").strip():
        return _refuse(
            "the request email's own words could not be read on this turn; nothing was queued"
        )
    scope = _scope(args if isinstance(args, dict) else {})
    if isinstance(scope, str):
        return _refuse(scope)
    envelope = {
        "trigger": "request",
        "requester": origin.sender_address,
        "message_ref": origin.internet_message_id,
        "request_text": origin.reply_text,
        "scope": scope,
    }
    try:
        resp = MedchronBrokerClient().casefile_submit(envelope)
    except MedchronBrokerError as exc:
        return _refuse(f"the request could not be queued: {exc}")
    if not resp.get("accepted"):
        out = {"accepted": False, "reason": resp.get("reason")}
        if resp.get("job_id"):
            out["job_id"] = resp["job_id"]
        return json.dumps(out, ensure_ascii=False)
    logger.info("casefile_job_submit: queued %s", resp.get("job_id"))
    return json.dumps(
        {
            "accepted": True,
            "job_id": resp.get("job_id"),
            "state": resp.get("state"),
            # A factual ticket, no timing (the no-fabricated-content rule).
            "message": f"Case file job {resp.get('job_id')} is queued on this Machine.",
        },
        ensure_ascii=False,
    )


def casefile_job_status(args: dict[str, Any], **_: Any) -> str:
    job_id = str(args.get("job_id") or "").strip()
    if not job_id:
        return json.dumps({"job": None, "reason": "job_id is required"}, ensure_ascii=False)
    resp = MedchronBrokerClient().casefile_status(job_id)
    raw = resp.get("job") if "job" in resp else resp
    job = {k: raw.get(k) for k in _STATUS_KEYS if k in raw} if isinstance(raw, dict) else None
    out: dict[str, Any] = {"job": job}
    if isinstance(raw, dict) and raw.get("hold_class"):
        out["job"]["hold_class"] = raw["hold_class"]
    if job is not None and job.get("state") == "failed":
        # A failed case file job is SMD's to resolve, never the client's: the
        # audit plugin records this shape as a shortfall, which alerts SMD.
        out.update(
            {
                "status": "refused",
                "reason": "casefile_job_failed",
                "message": "This case file job failed on SMD's side and SMD has been alerted. Send the client nothing.",
            }
        )
    return json.dumps(out, ensure_ascii=False)


TOOLS: dict[str, tuple[str, dict[str, Any], Any]] = {
    "casefile_job_submit": (
        "Queue a Case File Manager review for the Named Administrator whose email opened this turn. Never "
        "review files in the turn: this queues a job that reads each file on the Machine's runner, files a "
        "current File Status in each, and files the ranked list. Name the people whose caseload to read "
        "(assisting: the person assisting on the files, as the email names them), or the matter numbers "
        "(matters). Who asked, the email's id and its words are taken from the email itself, never from you. "
        "It answers with a job id, or a reason to relay as it comes back.",
        _schema(
            {
                "assisting": {
                    "type": "array",
                    "items": STRING,
                    "maxItems": MAX_NAMES,
                    "description": "The people whose caseload to read, by name as the email gives them.",
                },
                "matters": {
                    "type": "array",
                    "items": STRING,
                    "maxItems": MAX_NUMBERS,
                    "description": "Matter numbers, exactly as the email gives them.",
                },
            }
        ),
        casefile_job_submit,
    ),
    "casefile_job_status": (
        "A case file job by id: its state, stage and counts (matters, read this run, already current, not "
        "reviewed, changes proposed). Never matter facts.",
        _schema({"job_id": STRING}, ["job_id"]),
        casefile_job_status,
    ),
}

__all__ = ["TOOLS", "casefile_job_status", "casefile_job_submit"]
