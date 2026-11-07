"""Agent-facing tool for "set up our mediation briefs" (or memos): the drafting
lane's second kind of job (ss-console library_setup_verbs.py).

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
KINDS = ("mediation_brief", "memo")


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


TOOLS: dict[str, tuple[str, dict[str, Any], Any]] = {
    "library_setup_submit": (
        "A Named Administrator asked to set up the firm's mediation briefs or memos (or one attorney's) "
        "from the firm's own documents. This queues a job that reads the firm's own files of that kind "
        "across its matters and names the best by file; it never runs in the turn. Who asked and the "
        "request's words are taken from the email itself, never from you. When `live` is false, answer "
        "the request exactly as you would have without this tool. When `live` is true, relay `say` as "
        "given and nothing else about the setup. A refusal's `reason` is relayed as given.",
        _schema(
            {
                "kind": {
                    "type": "string",
                    "enum": list(KINDS),
                    "description": "Which kind of document the request asks to set up.",
                },
                "attorney": {
                    **STRING,
                    "description": "Only when the request names one attorney's own documents: that "
                    "attorney's name exactly as the request gives it. Omit for the firm's own file.",
                },
            },
            ["kind"],
        ),
        library_setup_submit,
    ),
}

__all__ = ["KINDS", "TOOLS", "library_setup_submit"]
