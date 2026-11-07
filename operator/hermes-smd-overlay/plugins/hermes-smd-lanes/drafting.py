"""Agent-facing tools for the drafting job (a litigation document a firm
administrator asks the Operator for, drafted in the firm's authored house
style; ss-console drafting_verbs.py).

The demand job's shape exactly (demand.py), for five document classes.

WHO ASKED IS NEVER THE MODEL'S TO SAY. ``requested_by``, ``request_ref`` and
``request_text`` are filled HERE from the turn's verified inbound origin, the
same way demand.py fills them; the tool schema has no field for any of them.
A turn no email opened submits nothing: a draft is always person-initiated.

The broker then checks the rest (the lane enabled on the seat, the class
switched on for the firm, the allowance, the requester on scope.admins, the
filing target, no unfinished draft of that class on the matter) and answers
with a job id or a reason to relay as it comes back.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from typing import Any

from shared import attachment_spool, email_adapter, msgraph_attachments, request_index
from shared.jobs_client import JobsBrokerClient, JobsBrokerError

from .demand import STRING, _matter, _origin, _refuse, _schema

logger = logging.getLogger(__name__)

#: ss-console drafting_ledger.DOCUMENT_CLASSES, in its order.
DOCUMENT_CLASSES = (
    "mediation_brief",
    "discovery_set",
    "discovery_response",
    "memo",
    "depo_outline",
)


#: The class whose request may carry the attorney's OWN documents: sets of
#: discovery he drafted himself, for the job to place in the court's caption on
#: the firm's paper (ss-console drafting/place.py).
ATTACHMENT_CLASSES = ("discovery_set",)
#: ss-console workspace_broker/drafting_attachments.py: the broker's socket
#: takes one request line of at most 1 MiB, and base64 grows bytes by a third.
MAX_ATTACHMENTS = 6
MAX_ATTACHMENT_BYTES = 700_000


def _request_attachments(origin: Any) -> tuple[list[dict[str, str]], str]:
    """The request email's own Word documents, as the broker's envelope
    carries them, or ``([], reason)`` when they exist and cannot be carried.

    Read by the message id of the turn's VERIFIED origin, never an id or a
    file the model names: the documents are the request's, exactly as the
    words are. A Microsoft 365 seat only (A&P's transport); another seat
    carries none and the job drafts as before."""
    if email_adapter.email_adapter() != email_adapter.ADAPTER_MSGRAPH:
        return [], ""
    try:
        found = msgraph_attachments.list_attachments(origin.message_id)
    except msgraph_attachments.MsGraphAttachmentError as exc:
        return [], f"the request email's attachments could not be read ({exc}); nothing was queued"
    docs = [
        a
        for a in found
        if not a.get("refused")
        and not a.get("is_inline")
        and str(a.get("filename") or "").lower().endswith(".docx")
    ]
    if len(docs) > MAX_ATTACHMENTS:
        return [], (
            f"the request attaches {len(docs)} Word documents and one request carries at most "
            f"{MAX_ATTACHMENTS}; nothing was queued"
        )
    out, total = [], 0
    for a in docs:
        try:
            receipt = msgraph_attachments.spool_attachment(origin.message_id, a["attachment_id"])
            blob = attachment_spool.read(receipt["spool_token"], expected_sha256=receipt["sha256"])
        except (msgraph_attachments.MsGraphAttachmentError, attachment_spool.SpoolError) as exc:
            return [], f"an attached Word document could not be read ({exc}); nothing was queued"
        total += len(blob)
        if total > MAX_ATTACHMENT_BYTES:
            return [], (
                "the attached Word documents are too large together for one request "
                f"(over {MAX_ATTACHMENT_BYTES:,} bytes); nothing was queued"
            )
        out.append(
            {
                "filename": receipt["filename"],
                "sha256": hashlib.sha256(blob).hexdigest(),
                "b64": base64.b64encode(blob).decode("ascii"),
            }
        )
    return out, ""


#: How a reply names a class to the firm (ss-console drafting_ledger.CLASS_LABEL).
_DOCUMENT = {
    "mediation_brief": "mediation brief",
    "discovery_set": "propounded discovery",
    "discovery_response": "discovery responses",
    "memo": "memo",
    "depo_outline": "deposition outline",
}
#: The broker waits up to 25 s for the runner's daemon; this outlasts that.
ADD_TIMEOUT_SECONDS = 40.0
_NOT_ADDED = (
    "Received. What you wrote was not added to the draft in progress. Please reply again in this thread "
    "and we will look at it."
)


def _say(result: str, resp: dict[str, Any]) -> str:
    """The one thing the reply states, from the result: the whole of the wording
    here so that no turn can say the direction was taken unless it was."""
    number = str(resp.get("matter_number") or "")
    doc = f"{_DOCUMENT.get(str(resp.get('document_class')), 'document')} for matter {number}"
    state = str(resp.get("state") or "")
    if result == "applied":
        return (
            f"Received. We added what you wrote to the instructions for the {doc}, and it will be drafted "
            "with them. We'll reply in this thread when it's filed."
        )
    if result == "compose_started":
        return (
            f"Received. The {doc} was already being written when your email arrived, so what you wrote is not "
            "in this draft. We'll say so again when we reply with the filed document, and you can ask for the "
            "change then."
        )
    if result == "job_ended" and state == "held":
        return (
            f"Received. What you wrote was not added to the {doc}, because that draft is on hold, as our earlier "
            "reply in this thread explains. To go ahead with it, please send the full request again."
        )
    if result == "job_ended" and state == "failed":
        return (
            f"Received. What you wrote was not added to the {doc}, because that draft has stopped on our side. "
            "We are looking at it and will reply in this thread."
        )
    if result == "not_requester":
        return (
            f"Received. What you wrote was not added to the {doc}, because changes to a draft in progress come "
            "from the person who asked for it."
        )
    if result == "other_thread":
        return (
            f"Received. What you wrote was not added to the {doc}, which is already underway from another email "
            "thread. Please send it as a reply in that thread."
        )
    return _NOT_ADDED


def _added_result(result: str, resp: dict[str, Any]) -> str:
    out: dict[str, Any] = {"result": result, "applied": result == "applied"}
    if result == "job_ended" and resp.get("state") == "delivered":
        # The filed draft: the broker's revise route decides this one, on submit.
        out["next"] = "drafting_job_submit"
        return json.dumps(out, ensure_ascii=False)
    out["say"] = _say(result, resp)
    if result == "job_ended" and resp.get("state") == "failed":
        # The shape the audit plugin records as a shortfall, as the status read's is.
        out.update({"status": "refused", "reason": "drafting_job_failed"})
    return json.dumps(out, ensure_ascii=False)


def _add_to_request(origin: Any, job_id: str) -> str:
    """The verified email's own words, by code, to the job they are about. The
    words and the thread come from the seat's request index row for this email;
    the model names at most which job, and the broker checks that."""
    try:
        row = request_index.read_request(origin.message_id or "", origin.internet_message_id)
        thread = request_index.read_thread_message_ids(str((row or {}).get("thread_ref") or ""))
    except Exception:  # noqa: BLE001 - could not look is never "nothing to add"
        logger.exception("drafting_job_add_to_request: the request index could not be read")
        return _added_result("could_not_read", {})
    words = (row or {}).get("request_text")
    if not words or not thread:
        return _added_result("could_not_read", {})
    if request_index.bare_sender(row.get("sender")) != request_index.bare_sender(
        origin.sender_address
    ):
        return _added_result("not_requester", {})
    if len(origin.reply_text or "") > request_index.TEXT_CHARS:
        return _added_result("too_long", {})  # the index cut it; never say the whole of it landed
    try:
        resp = JobsBrokerClient(timeout=ADD_TIMEOUT_SECONDS).drafting_add_to_request(
            job_id=job_id,
            request_ref=origin.internet_message_id,
            requested_by=origin.sender_address,
            thread_refs=thread,
            words=words,
        )
    except JobsBrokerError:
        logger.exception("drafting_job_add_to_request: the broker did not answer")
        return _added_result("not_confirmed", {})
    return _added_result(str(resp.get("result") or "not_confirmed"), resp)


def drafting_job_add_to_request(args: dict[str, Any], session_id: str = "", **_: Any) -> str:
    origin = _origin(session_id)
    if origin is None or not origin.sender_address or not origin.internet_message_id:
        return _added_result("no_email_this_turn", {})
    job_id = str(args.get("job_id") or "").strip()
    return _add_to_request(origin, job_id) if job_id else _added_result("no_such_job", {})


def drafting_job_submit(args: dict[str, Any], session_id: str = "", **_: Any) -> str:
    origin = _origin(session_id)
    if origin is None or not origin.sender_address:
        return _refuse(
            "a draft is submitted only from the turn of the email that asked for it; "
            "this turn was not opened by one, so nothing was queued"
        )
    if not origin.internet_message_id:
        return _refuse(
            "the request email's message id is not available on this turn, so the completion "
            "reply could not find its thread; nothing was queued"
        )
    if not (origin.reply_text or "").strip():
        return _refuse(
            "the request email's own words could not be read on this turn, and they are the "
            "drafting instruction; nothing was queued"
        )
    klass = str(args.get("document_class") or "").strip()
    if klass not in DOCUMENT_CLASSES:
        return _refuse(
            f"document_class must be one of {list(DOCUMENT_CLASSES)}; nothing was queued"
        )
    envelope = {
        "matter": _matter(args, "matter_id", "matter_number"),
        "file_to": _matter(args, "file_to_matter_id", "file_to_matter_number"),
        "requested_by": origin.sender_address,
        "request_ref": origin.internet_message_id,
        "request_text": origin.reply_text,
        "document_class": klass,
    }
    if klass in ATTACHMENT_CLASSES:
        attached, why = _request_attachments(origin)
        if why:
            return _refuse(why)
        if attached:
            envelope["attachments"] = attached
    try:
        resp = JobsBrokerClient().drafting_submit(envelope)
    except JobsBrokerError as exc:
        return _refuse(f"the request could not be queued: {exc}")
    if not resp.get("accepted"):
        if resp.get("underway") and resp.get("job_id"):
            # This email collided with a draft still running: it is about that
            # draft, and the reply must say whether its words got there.
            return json.dumps(
                {
                    "accepted": False,
                    "job_id": resp["job_id"],
                    **json.loads(_add_to_request(origin, resp["job_id"])),
                },
                ensure_ascii=False,
            )
        out = {"accepted": False, "reason": resp.get("reason")}
        if resp.get("job_id"):
            out["job_id"] = resp["job_id"]
        return json.dumps(out, ensure_ascii=False)
    logger.info(
        "drafting_job_submit: queued %s (%s)", resp.get("job_id"), resp.get("mode") or "draft"
    )
    revising = resp.get("mode") == "revise"
    out = {
        "accepted": True,
        "job_id": resp.get("job_id"),
        "state": resp.get("state"),
        "document_class": resp.get("document_class") or klass,
        "mode": "revise" if revising else "draft",
        # A factual ticket, no timing (the no-fabricated-content rule).
        "message": (
            f"Drafting job {resp.get('job_id')} is queued on this Machine. It makes the requested changes "
            "to the draft already filed in the matter and files them as new versions of the same files; "
            "the reply goes to this thread."
            if revising
            else f"Drafting job {resp.get('job_id')} is queued on this Machine. The draft is filed "
            "in the matter when it finishes, and the reply goes to this thread."
        ),
    }
    if revising and resp.get("revise_of"):
        out["revise_of"] = resp["revise_of"]
    return json.dumps(out, ensure_ascii=False)


def drafting_job_status(args: dict[str, Any], **_: Any) -> str:
    job_id = str(args.get("job_id") or "").strip() or None
    resp = JobsBrokerClient().drafting_status(job_id)
    if not job_id:
        return json.dumps({"jobs": resp.get("jobs") or []}, ensure_ascii=False)
    job = resp.get("job")
    out: dict[str, Any] = {"job": job}
    if isinstance(job, dict) and job.get("state") == "failed":
        # A failed drafting job is SMD's to resolve, never the client's. This
        # shape is what the audit plugin records as a shortfall (code
        # drafting_job_failed), which raises SMD's shortfall alert; the client
        # is told nothing (the broker refuses the reply for a failed job).
        out.update(
            {
                "status": "refused",
                "reason": "drafting_job_failed",
                "message": (
                    "This drafting job failed on SMD's side and SMD has been alerted. Send the "
                    "client nothing and end the turn."
                ),
            }
        )
    return json.dumps(out, ensure_ascii=False)


def drafting_allowance(args: dict[str, Any], **_: Any) -> str:
    client = JobsBrokerClient()
    resp = client.drafting_allowance()
    keys = ("unit", "cycle", "allowance", "used", "remaining", "authored", "enabled_classes")
    out = {k: resp.get(k) for k in keys}
    if args.get("include_recent_jobs"):
        out["recent_jobs"] = client.drafting_status().get("jobs") or []
    return json.dumps(out, ensure_ascii=False)


TOOLS: dict[str, tuple[str, dict[str, Any], Any]] = {
    "drafting_job_submit": (
        "Queue a litigation draft on a matter (a mediation brief, a discovery set, discovery "
        "responses, a memo, or a deposition outline) for the Named Administrator whose email opened "
        "this turn. Never draft in the turn: this queues a job that drafts on the Machine's runner "
        "and files the document in the matter. Resolve the matter first. Who asked, the email's id "
        "and the request's words are taken from the email itself, never from you. A reply asking "
        "for changes to a draft already filed is submitted the SAME way, with the same class: the "
        "broker sees the filed draft in that email thread and revises it in place (mode revise), "
        "never a fresh draft. It answers with a job id, or a reason to relay as it comes back.",
        _schema(
            {
                "matter_id": {
                    "type": "string",
                    "description": "The practice-management matter id, resolved this turn.",
                },
                "matter_number": {"type": "string", "description": "The firm's matter number."},
                "document_class": {
                    "type": "string",
                    "enum": list(DOCUMENT_CLASSES),
                    "description": "Which document was asked for. One job per document.",
                },
                "file_to_matter_id": {
                    "type": "string",
                    "description": "Only for a rehearsal: the matter the document is filed on instead.",
                },
                "file_to_matter_number": STRING,
            },
            ["matter_id", "matter_number", "document_class"],
        ),
        drafting_job_submit,
    ),
    "drafting_job_add_to_request": (
        "The email that opened this turn adds to or changes what a drafting job still running was asked "
        "to do: carry its words to that job. Pass only the job's id; the words, the sender and the thread "
        "are taken from the email itself, never from you. It answers with `result`, and `say` is what the "
        "reply states, as given. `applied` is the only result that means the words reached the job.",
        _schema(
            {"job_id": {"type": "string", "description": "The running drafting job."}}, ["job_id"]
        ),
        drafting_job_add_to_request,
    ),
    "drafting_job_status": (
        "A drafting job by id (state, document class, delivery folder, filed file roles), or the last twenty.",
        _schema({"job_id": STRING}),
        drafting_job_status,
    ),
    "drafting_allowance": (
        "The firm's drafting allowance for the current billing cycle: authored, used, remaining "
        "(a count of drafts), and which document classes are switched on.",
        _schema(
            {
                "include_recent_jobs": {
                    "type": "boolean",
                    "description": "Also list the last twenty drafting jobs with their states.",
                }
            }
        ),
        drafting_allowance,
    ),
}

__all__ = [
    "DOCUMENT_CLASSES",
    "TOOLS",
    "drafting_allowance",
    "drafting_job_add_to_request",
    "drafting_job_status",
    "drafting_job_submit",
]
