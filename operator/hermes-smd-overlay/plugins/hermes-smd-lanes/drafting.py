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
with a job id, or a refusal carrying a closed ``code``.

WHAT THE FIRM IS TOLD IS CODE'S, NEVER THE MODEL'S (2026-11-19). The submit
answers with ``say``, one fixed sentence the reply is, word for word (the
records lane's shape, ``records.py``); the reply relay refuses any other body
(``hermes-smd-reply/relay_checks.turn_say``). Until then an accepted submit
answered ``message: "Drafting job <id> is queued on this Machine..."``, and the
model relayed mechanics, or promised what the job would do. The sentences:

* accepted, a fresh draft: :func:`acknowledgment` (the matter number is the one
  the broker validated and echoed);
* accepted, a revision of a filed draft: the revise sentence;
* refused for a FIRM cause (``FIRM_SAY``): one plain sentence each;
* the same email already started this kind of draft: the acknowledgment on the
  same matter, ``SECOND_MATTER_SAY`` on another (nothing is queued for it);
* refused on SMD's side (owner's ruling, 2026-11-19: our failure is not the
  attorney's problem, and he is not told of it): the NORMAL acknowledgment, but
  only when the broker answered ``smd_told: true``, i.e. it recorded the
  request as work SMD owes in the same step as the refusal
  (``workspace_broker/smd_owed.py``; the hourly triage lists it until SMD
  closes it). Otherwise, and whenever the broker did not answer or the refusal
  is the overlay's own, ``say: "none"`` with ``instruction`` and ``forbid``:
  nothing is said about this request, the rest of the email is answered, and
  the relay refuses a reply that mentions the request anyway. An email that
  asked only this gets no reply and is listed as unanswered.
* an addition (``drafting_job_add_to_request``) to a draft whose job has
  failed or was stopped (owner's ruling, 2026-11-20): the same rule. The
  revise sentence only when the broker answered ``smd_told: true`` (it
  recorded the addition as work SMD owes, ``drafting_addition``), else
  ``none``; never a word that anything broke.

* an email about documents ALREADY FILED on the matter (2026-11-24,
  ``drafting_filed.py``): revised, or held for SMD's hand with "Let me look
  into that. I'll reply in this thread.", and carried to any draft of the same
  class still running on the matter.

No job id reaches the model from these tools; the id stays in the logs and the
broker's rows.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from typing import Any

from shared import attachment_spool, email_adapter, msgraph_attachments, request_index
from shared.jobs_client import JobsBrokerClient, JobsBrokerError

from . import drafting_filed
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
    carries them, or ``([], code)`` when they exist and cannot be carried
    (``too_many_attachments`` and ``attachments_too_large`` are the firm's to
    fix, ``attachment_unreadable`` SMD's).

    Read by the message id of the turn's VERIFIED origin, never an id or a
    file the model names: the documents are the request's, exactly as the
    words are. A Microsoft 365 seat only (A&P's transport); another seat
    carries none and the job drafts as before."""
    if email_adapter.email_adapter() != email_adapter.ADAPTER_MSGRAPH:
        return [], ""
    try:
        found = msgraph_attachments.list_attachments(origin.message_id)
    except msgraph_attachments.MsGraphAttachmentError:
        logger.exception("drafting_job_submit: the request email's attachments could not be listed")
        return [], "attachment_unreadable"
    docs = [
        a
        for a in found
        if not a.get("refused")
        and not a.get("is_inline")
        and str(a.get("filename") or "").lower().endswith(".docx")
    ]
    if len(docs) > MAX_ATTACHMENTS:
        return [], "too_many_attachments"
    out, total = [], 0
    for a in docs:
        try:
            receipt = msgraph_attachments.spool_attachment(origin.message_id, a["attachment_id"])
            blob = attachment_spool.read(receipt["spool_token"], expected_sha256=receipt["sha256"])
        except (msgraph_attachments.MsGraphAttachmentError, attachment_spool.SpoolError):
            logger.exception("drafting_job_submit: an attached Word document could not be read")
            return [], "attachment_unreadable"
        total += len(blob)
        if total > MAX_ATTACHMENT_BYTES:
            return [], "attachments_too_large"
        out.append(
            {
                "filename": receipt["filename"],
                "sha256": hashlib.sha256(blob).hexdigest(),
                "b64": base64.b64encode(blob).decode("ascii"),
            }
        )
    return out, ""


#: How a reply names a class to the firm (ss-console drafting_ledger.CLASS_LABEL),
#: also the acknowledgment's and the refusals' words.
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


#: The firm's own causes for a refused submit (ss-console
#: ``drafting_verbs.REFUSAL_FIRM_CODES``, and the request's attachments), each
#: one plain sentence with no mechanics. ``{doc}`` is the document and matter.
FIRM_SAY = {
    "not_admin": (
        "Received. Drafts are requested by one of the firm's named administrators, so we have not started "
        "the {doc}."
    ),
    "class_not_enabled": (
        "Received. Drafting {a_what} isn't switched on for your firm, so we have not started the {doc}."
    ),
    "allowance_spent": (
        "Received. Your firm's drafts for this billing cycle are all used, so we have not started the {doc}."
    ),
    "underway": (
        "Received. The {doc} {is_} already being drafted from an earlier email, so this one did not start a "
        "second draft. We'll reply in that thread when it's filed."
    ),
    "too_many_attachments": (
        "Received. This email attaches more Word documents than we can take in one request (at most "
        f"{MAX_ATTACHMENTS}), so we have not started the {{doc}}. Please send the request again with at most "
        f"{MAX_ATTACHMENTS} Word documents attached."
    ),
    "attachments_too_large": (
        "Received. The attached Word documents are too large to take together in one request, so we have "
        "not started the {doc}. Please send the request again with fewer or smaller Word documents attached."
    ),
}
#: The same request email already queued a draft of this kind (ss-console
#: ``drafting_verbs.REFUSAL_DUPLICATE_CODE``, keyed on the email and the class,
#: never the matter). On the same matter it is the same request, answered with
#: the acknowledgment; on another matter it is a second request one email cannot
#: start, and nothing was queued for it.
DUPLICATE_CODE = "already_queued"
#: A drafting request that names no matter (review 2026-11-19): a required
#: block asking for the number in plain words.
MATTER_NUMBER_SAY = "Received. Which matter is this for? Please reply with the matter number."

SECOND_MATTER_SAY = (
    "Received. One email starts one draft of each kind, and this one already started the {queued}. "
    "Please send the request for matter {number} in its own email."
)
#: The relay's "no fixed sentence for this point" say (``shared.turn_says.SAY_NONE``).
SAY_NONE = "none"
#: What the model is told with a ``none``: no claim about this request at all.
NONE_INSTRUCTION = (
    "Say nothing about this request in your reply: do not acknowledge it, refuse it or explain it. "
    "Answer anything else the email asked."
)


#: The document phrases that read as plural ("the discovery responses ... are").
_PLURAL = frozenset({"discovery_response"})
#: The document phrases a sentence counts with "a" ("drafting a memo").
_COUNTED = frozenset({"mediation_brief", "memo", "depo_outline"})


def _doc(klass: str, number: str) -> str:
    return f"{_DOCUMENT.get(klass, 'document')} for matter {number}"


def _is(klass: str) -> str:
    return "are" if klass in _PLURAL else "is"


def acknowledgment(klass: str, number: str, *, revising: bool = False) -> str:
    """The sentence a received drafting request is answered with, true whoever
    does the work: no time, no mechanics, nothing about who drafts it."""
    if revising:
        return (
            f"Received. We'll make those changes to the {_doc(klass, number)} and reply in this thread "
            "when the new version is filed."
        )
    return (
        f"Received. The {_doc(klass, number)} {_is(klass)} with us, and you'll get a reply in this thread when "
        "the draft is filed in the matter."
    )


def duplicate_say(klass: str, number: str, mode: str, state: str) -> str:
    """The sentence for an email whose draft of this kind is already queued: the
    revise sentence for a revision, the acknowledgment while it is in hand, and
    for a draft already filed or on hold, no promise of a reply "when it's
    filed" (it was, or SMD has it)."""
    if state == "delivered":
        return (
            f"Received. The {_doc(klass, number)} from this request {_is(klass)} already filed in the matter, "
            "as our earlier reply in this thread says."
        )
    if state == "held":
        return (
            f"Received. The {_doc(klass, number)} from this request {_is(klass)} on hold, as our earlier reply "
            "in this thread explains."
        )
    return acknowledgment(klass, number, revising=mode == "revise")


def _a(klass: str) -> str:
    what = _DOCUMENT.get(klass, "document")
    return f"a {what}" if klass in _COUNTED else what


def refusal_say(code: str, klass: str, number: str, *, smd_told: bool = False) -> str:
    """A refused submit's say: a firm cause's own sentence; on SMD's side (every
    other code, an unknown one included) the normal acknowledgment when the
    broker recorded the request as owed, else ``none``."""
    hand = drafting_filed.hand_say(code, smd_told, SAY_NONE)
    if hand is not None:
        return hand  # SMD revises the filed documents: the Operator owns the follow-up
    template = FIRM_SAY.get(code)
    if template is None:
        return acknowledgment(klass, number) if smd_told else SAY_NONE
    return template.format(doc=_doc(klass, number), a_what=_a(klass), is_=_is(klass))


def _forbid(klass: str, number: str) -> dict[str, Any]:
    """What a reply may not claim on a "none" turn: this matter's document of
    this class, by its name and its last word ("mediation brief", "brief")."""
    name = _DOCUMENT.get(klass, "document")
    return {"matter": number, "words": sorted({name, name.split()[-1]})}


def _refused_say(code: str, klass: str, number: str, *, smd_told: bool = False) -> str:
    """A refused submit's answer: the closed code, and the say."""
    say = refusal_say(code, klass, number, smd_told=smd_told)
    out: dict[str, Any] = {"accepted": False, "code": code, "say": say}
    if say == SAY_NONE:
        # Nothing is said about this request; the relay refuses a reply that
        # mentions it anyway (``forbid``), and the rest of the email is answered.
        out.update(instruction=NONE_INSTRUCTION, forbid=_forbid(klass, number))
    return json.dumps(out, ensure_ascii=False)


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
        # Our failure is not the attorney's problem (owner's ruling, 2026-11-20):
        # the revise acknowledgment, true because the broker recorded the
        # addition as work SMD owes in the same step; else nothing is said.
        if resp.get("smd_told") is not True:
            return SAY_NONE
        return acknowledgment(str(resp.get("document_class")), number, revising=True)
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
    if out["say"] == SAY_NONE:
        # Nothing is said about this addition; the relay refuses a reply that
        # mentions the document anyway, and the rest of the email is answered.
        number = _number(resp.get("matter_number"))
        out.update(
            instruction=NONE_INSTRUCTION, forbid=_forbid(str(resp.get("document_class")), number)
        )
    if result == "job_ended" and resp.get("state") == "failed":
        # The shape the audit plugin records as a shortfall, as the status read's is.
        out.update({"status": "refused", "reason": "drafting_job_failed"})
    return json.dumps(out, ensure_ascii=False)


def _add_to_request(
    origin: Any, job_id: str, *, about_filed: bool = False, then: Any = None
) -> str:
    """The verified email's own words, by code, to the job they are about. The
    words and the thread come from the seat's request index row for this email;
    the model names at most which job, and the broker checks that.
    ``about_filed``: the email is about documents already filed on the matter
    (``drafting_filed``). ``then(added, resp)``: what the answer becomes after."""
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
            **({"about_filed": True} if about_filed else {}),
        )
    except JobsBrokerError:
        logger.exception("drafting_job_add_to_request: the broker did not answer")
        return _added_result("not_confirmed", {})
    added = _added_result(str(resp.get("result") or "not_confirmed"), resp)
    if then is None:
        return added
    return json.dumps(then(json.loads(added), resp), ensure_ascii=False)


def drafting_job_add_to_request(args: dict[str, Any], session_id: str = "", **_: Any) -> str:
    origin = _origin(session_id)
    if origin is None or not origin.sender_address or not origin.internet_message_id:
        return _added_result("no_email_this_turn", {})
    job_id = str(args.get("job_id") or "").strip()
    if not job_id:
        return _added_result("no_such_job", {})

    def filed_too(added: dict[str, Any], resp: dict[str, Any]) -> dict[str, Any]:
        # The thread also holds this class's filed documents: submit the email
        # too, so they are revised or held for SMD (``drafting_filed``).
        return drafting_filed.also_submit(added, resp, lambda a: _submit(origin, a, carry=False))

    return _add_to_request(origin, job_id, then=filed_too)


def _duplicate(resp: dict[str, Any], klass: str, number: str) -> str:
    """The same email already queued a draft of this kind. On the same matter it
    is the same request: the sentence true for that job (a revision, or a draft
    already filed or on hold). On another matter, a second request."""
    queued = _number(resp.get("queued_matter_number"))
    if queued != number:
        say = SECOND_MATTER_SAY.format(queued=_doc(klass, queued), number=number)
        return json.dumps(
            {"accepted": False, "code": "second_matter", "say": say}, ensure_ascii=False
        )
    mode, state = str(resp.get("queued_mode") or ""), str(resp.get("queued_state") or "")
    say = duplicate_say(klass, number, mode, state)
    return json.dumps(
        {"accepted": True, "duplicate": DUPLICATE_CODE, "say": say}, ensure_ascii=False
    )


def _number(value: Any) -> str:
    return " ".join(str(value or "").split())


def drafting_job_submit(args: dict[str, Any], session_id: str = "", **_: Any) -> str:
    origin = _origin(session_id)
    if origin is None or not origin.sender_address:
        # No email opened this turn, so there is no one to reply to.
        return _refuse(
            "a draft is submitted only from the turn of the email that asked for it; "
            "this turn was not opened by one, so nothing was queued"
        )
    return _submit(origin, args)


def _submit(origin: Any, args: dict[str, Any], *, carry: bool = True) -> str:
    """The submit from a verified origin. ``carry``: an email about filed
    documents is also carried to a draft of the class still running (False when
    the caller already carried it there)."""
    # The model's own arguments first: a call it can correct is told how, and
    # says nothing to the firm.
    klass = str(args.get("document_class") or "").strip()
    if klass not in DOCUMENT_CLASSES:
        return _refuse(
            f"document_class must be one of {list(DOCUMENT_CLASSES)}; nothing was queued"
        )
    number = _number(args.get("matter_number"))
    if not number:
        # The reply is a fixed sentence asking for the number (review 2026-11-19):
        # the old prose ("nothing was queued") was the model's to relay.
        return json.dumps(
            {"accepted": False, "code": "matter_number_missing", "say": MATTER_NUMBER_SAY},
            ensure_ascii=False,
        )
    if not origin.internet_message_id:
        return _refused_say("request_ref_unavailable", klass, number)
    if not (origin.reply_text or "").strip():
        return _refused_say("request_words_unreadable", klass, number)
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
            return _refused_say(why, klass, number)
        if attached:
            envelope["attachments"] = attached
    try:
        resp = JobsBrokerClient().drafting_submit(envelope)
    except JobsBrokerError:
        logger.exception("drafting_job_submit: the broker did not answer")
        return _refused_say("broker_unanswered", klass, number)
    if carry and resp.get("underway_job_id"):
        drafting_filed.reach_underway(resp, lambda j, **kw: _add_to_request(origin, j, **kw))
    if not resp.get("accepted"):
        if resp.get("underway") and resp.get("job_id") and carry:
            # This email collided with a draft still running: it is about that
            # draft, and the reply must say whether its words got there. The
            # job's id stays here; the model is never handed it.
            return json.dumps(
                {"accepted": False, **json.loads(_add_to_request(origin, resp["job_id"]))},
                ensure_ascii=False,
            )
        code = str(resp.get("code") or "")
        if resp.get("correct_call"):
            # The call itself was malformed (first time for this email): the
            # model corrects it; nothing is said and nothing is owed yet.
            return _refuse(
                f"the submission was not valid ({resp.get('reason')}); correct it and submit again"
            )
        if code == DUPLICATE_CODE:
            return _duplicate(resp, klass, number)
        # Every other code, and none at all (an older broker), is SMD's side:
        # the acknowledgment only if the broker recorded it as owed.
        return _refused_say(
            code or "not_accepted", klass, number, smd_told=resp.get("smd_told") is True
        )
    logger.info(
        "drafting_job_submit: queued %s (%s)", resp.get("job_id"), resp.get("mode") or "draft"
    )
    revising = resp.get("mode") == "revise"
    queued_class = str(resp.get("document_class") or klass)
    # The matter the broker validated the job on, never only the model's word.
    resolved = _number(resp.get("matter_number")) or number
    if resolved != number:
        logger.warning("drafting_job_submit: the broker echoed another matter number; saying its")
    out = {
        "accepted": True,
        "document_class": queued_class,
        "say": acknowledgment(queued_class, resolved, revising=revising),
    }
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
        "broker sees the filed draft in that email thread and revises it in place, never a fresh "
        'draft. It answers with "say": your reply must contain this sentence exactly, as its own '
        "paragraph; answer anything else the email asked in other paragraphs; never describe how the "
        'work is done. When it is "none", say nothing about that request; answer the rest of the '
        "email. Any other answer names an argument to correct and is not for the reply.",
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
        "A drafting job by id (state, document class, delivery folder, filed file roles), or the last twenty. "
        "A job's id is for your tool calls only: never write it in a reply (the seat refuses a reply "
        "that carries one).",
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
