"""Agent-facing tool for "set up the document library" (or "refresh" it), and for
"set up our discovery responses" (any one kind the drafting job makes): the
drafting lane's second kind of job (ss-console library_setup_verbs.py).

WHO ASKED IS NEVER THE MODEL'S TO SAY. ``requested_by``, ``request_ref`` and
``request_text`` are filled HERE from the turn's verified inbound origin, as the
drafting tool fills them. The model gives only which kind and, for an attorney's
own file, the attorney's name as the request gives it: values code acts on.

THE ANSWER. ``live`` false: the seat's switch is off; the job only counts, and
the turn answers the request exactly as it does today. ``live`` true: ``say``
is the sentence to relay, as given; the job's own reply comes later, in code. A
refusal's ``reason`` is the broker's sentence, relayed as given. Every sentence
the firm reads comes from the broker (``library_setup_wording``), never this file.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from shared.jobs_client import JobsBrokerClient, JobsBrokerError

from .demand import STRING, _origin, _refuse, _schema

logger = logging.getLogger(__name__)

#: ss-console library_setup_ledger.KINDS, in its order.
KINDS = ("discovery_set", "discovery_response", "mediation_brief", "memo", "depo_outline")
#: ss-console library_setup_ledger.LIBRARY: every kind at once.
LIBRARY = "library"


def library_setup_submit(args: dict[str, Any], session_id: str = "", **_: Any) -> str:
    origin = _origin(session_id)
    if origin is None or not origin.sender_address or not origin.internet_message_id:
        return _refuse(
            "a setup is queued only from the turn of the email that asked for it; nothing was queued"
        )
    if not (origin.reply_text or "").strip():
        return _refuse(
            "the request email's own words could not be read on this turn; nothing was queued"
        )
    kind = str(args.get("kind") or "").strip()
    attorney = " ".join(str(args.get("attorney") or "").split()) or None
    envelope = {
        "kind": kind,
        "attorney": attorney,
        "requested_by": origin.sender_address,
        "request_ref": origin.internet_message_id,
        "request_text": origin.reply_text,
    }
    # What the request points at; the broker refuses words the email does not hold.
    for key in ("matter", "document"):
        value = " ".join(str(args.get(key) or "").split())
        if value:
            envelope[key] = value
    try:
        resp = JobsBrokerClient().library_setup_submit(envelope)
    except JobsBrokerError as exc:
        return _refuse(f"the request could not be queued: {exc}")
    if not resp.get("accepted"):
        out = {"accepted": False, "reason": resp.get("reason")}
        if resp.get("job_id"):
            out["job_id"] = resp["job_id"]
        return json.dumps(out, ensure_ascii=False)
    logger.info(
        "library_setup_submit: queued %s (live=%s)", resp.get("job_id"), bool(resp.get("live"))
    )
    out = {"accepted": True, "job_id": resp.get("job_id"), "live": bool(resp.get("live"))}
    if resp.get("live") and resp.get("say"):
        out["say"] = resp["say"]
    return json.dumps(out, ensure_ascii=False)


def library_setup_choose(args: dict[str, Any], session_id: str = "", **_: Any) -> str:
    """The administrator's reply naming one of the listed documents. Only the
    verified email travels: its sender, its message id and the reader's own
    words above the quoted history; code reads the choice from those words."""
    origin = _origin(session_id)
    if origin is None or not origin.sender_address or not origin.internet_message_id:
        return _refuse(
            "a choice is read only from the turn of the email that made it; nothing was made"
        )
    envelope = {
        "requested_by": origin.sender_address,
        "request_ref": origin.internet_message_id,
        "reply_text": origin.reply_text or "",
    }
    kind = str(args.get("kind") or "").strip()
    if kind:
        envelope["kind"] = kind
    try:
        resp = JobsBrokerClient().library_setup_choose(envelope)
    except JobsBrokerError as exc:
        return _refuse(f"the choice could not be read: {exc}")
    if not resp.get("accepted"):
        out: dict[str, Any] = {"accepted": False, "live": resp.get("live", True)}
        if resp.get("reason"):
            out["reason"] = resp["reason"]
        return json.dumps(out, ensure_ascii=False)
    logger.info(
        "library_setup_choose: queued %s (live=%s)", resp.get("job_id"), bool(resp.get("live"))
    )
    out = {"accepted": True, "job_id": resp.get("job_id"), "live": bool(resp.get("live"))}
    if resp.get("live") and resp.get("say"):
        out["say"] = resp["say"]
    return json.dumps(out, ensure_ascii=False)


TOOLS: dict[str, tuple[str, dict[str, Any], Any]] = {
    "library_setup_submit": (
        "A Named Administrator asked to set up or refresh the firm's document library (kind `library`, "
        'every kind at once; also part of "initialize yourself and set up for our firm"), or one kind '
        "of it (discovery sets, discovery responses, mediation briefs, memos, deposition outlines), for "
        "the firm or one attorney. This queues a job that reads the firm's own files across its matters "
        "and lists the best by file for each kind; it never runs in the turn. Who asked and the "
        "request's words are taken from the email itself, never from you. When `live` is false, answer "
        "the request exactly as you would have without this tool. When `live` is true, relay `say` as "
        "given and nothing else about the setup. A refusal's `reason` is relayed as given.",
        _schema(
            {
                "kind": {
                    "type": "string",
                    "enum": [*KINDS, LIBRARY],
                    "description": "Which kind of document the request asks to set up; `library` for the "
                    'whole document library ("set up the document library", "refresh the document '
                    'library").',
                },
                "attorney": {
                    **STRING,
                    "description": "Only when the request names one attorney's own documents: that "
                    "attorney's name exactly as the request gives it. Omit for the firm's own file.",
                },
                "matter": {
                    **STRING,
                    "description": "Only when the request points at the document on one matter for "
                    "one kind: that matter's number exactly as the request gives it.",
                },
                "document": {
                    **STRING,
                    "description": "Only when the request points at one document by its name for one "
                    "kind: the words of the name exactly as the request gives them.",
                },
            },
            ["kind"],
        ),
        library_setup_submit,
    ),
    "library_setup_choose": (
        "The email that opened this turn answers a list (or the document library's lists, one per kind) of "
        "the firm's own documents the Operator sent for setting up library files: it names a document per "
        "kind, says to keep the current one, or points at another document by matter or name. Pass at "
        "most the list's kind: the sender, the thread and the reader's own words are taken from the email "
        "itself, and code reads every choice. When `live` is false and there is no `reason`, answer the email exactly as you would "
        "have without this tool. Otherwise relay `say` or `reason` as given and nothing else.",
        _schema(
            {
                "kind": {
                    "type": "string",
                    "enum": list(KINDS),
                    "description": "Which kind of document the list was about, when you can tell; the "
                    "broker refuses a choice whose list is of another kind.",
                }
            }
        ),
        library_setup_choose,
    ),
}

__all__ = ["KINDS", "LIBRARY", "TOOLS", "library_setup_choose", "library_setup_submit"]
