"""The agent-facing tool for a matter-records request (ADR 0102 amendment 4).

A staff member emails the Operator for named records on a matter. The turn
does not find, read, cut or attach anything: it queues a ``records`` job and
relays the broker's acknowledgement. The runner parses the request once, finds
each record in each file once, cuts the pages, renders the facts from
``records_contract.facts`` and writes the reply as an SMD review row; the
gateway's review sweeper sends it once SMD releases it. No turn sends it.

WHO ASKED IS NEVER THE MODEL'S TO SAY (the casefile.py shape): ``requester``,
``message_ref`` and ``request_text`` are filled HERE from the turn's verified
inbound origin; the schema has no field for any of them. ``request_text`` is
``origin.reply_text``, the sender's OWN words: on a Graph seat the poller cuts
the body above its quoted history (``shared.reply_text.strip_quoted_reply``,
called in ``shared.msgraph_client``; not Graph ``uniqueBody``), so a request
quoted under someone's "please see below" submits only "please see below". A
forward (``Fwd:`` / ``FW:`` subject) submits nothing at all: the person sending
it did not write what is below it.

``message_ref`` is the Graph message id the review sweeper replies to
(``origin.message_id``), so the reply lands in the thread that asked. The broker
treats it as unique across every records job ever: a second submit for the same
email answers ``already_queued`` or ``already_done`` and the turn replies nothing.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from shared.medchron_client import MedchronBrokerClient, MedchronBrokerError

from .demand import STRING, _origin, _schema

logger = logging.getLogger(__name__)

MAX_MATTER_CHARS = 60
#: The broker's ``duplicate`` values: this email already has a job, so reply nothing.
SILENT_REASONS = frozenset({"already_queued", "already_done"})
#: A Smokeball matter id. The tool takes the matter NUMBER the colleague wrote,
#: because the acknowledgement names it back in their terms; the runner resolves it.
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
#: A subject that marks a forward (the person sending it did not write what is below).
_FORWARD = re.compile(r"^\s*(?:fwd?|fw)\s*:", re.IGNORECASE)


def _refuse(reason: str) -> str:
    return json.dumps({"accepted": False, "reason": reason}, ensure_ascii=False)


def _silent(reason: str) -> str:
    """The email already has a job: the turn sends no reply at all."""
    return json.dumps({"accepted": False, "reason": reason, "say": "none"}, ensure_ascii=False)


def records_job_submit(args: dict[str, Any], session_id: str = "", **_: Any) -> str:
    origin = _origin(session_id)
    if origin is None or not origin.sender_address:
        return _refuse(
            "a records request is queued only from the turn of the email that asked for it; this "
            "turn was not opened by one, so nothing was queued"
        )
    if not origin.message_id:
        return _refuse(
            "the request email's id is not available on this turn, so the reply could not find "
            "its thread; nothing was queued"
        )
    if _FORWARD.match(origin.subject or ""):
        return _refuse(
            "this email forwards someone else's request; a records request is queued only from "
            "the asker's own email, so nothing was queued"
        )
    if not (origin.reply_text or "").strip():
        return _refuse(
            "the request email's own words could not be read on this turn, and they are the "
            "request; nothing was queued"
        )
    args = args if isinstance(args, dict) else {}
    matter = args.get("matter_number")
    if matter is not None and (
        not isinstance(matter, str)
        or not matter.strip()
        or len(matter) > MAX_MATTER_CHARS
        or _UUID.match(matter.strip())
    ):
        return _refuse(
            "matter_number is the matter number exactly as the email writes it (never a "
            f"Smokeball id), at most {MAX_MATTER_CHARS} characters; nothing was queued"
        )
    envelope: dict[str, Any] = {
        "trigger": "request",
        "requester": origin.sender_address,
        "message_ref": origin.message_id,
        # The RFC id the request index and every lane ledger key a request on
        # (shared.request_cards joins request_ref on it); "" when the seam did not say.
        "internet_message_id": origin.internet_message_id or "",
        "request_text": origin.reply_text,
        "ack": "turn",
    }
    if matter:
        # The broker's key is matter_id; it takes a number as written and echoes it.
        envelope["matter_id"] = " ".join(matter.split())
    try:
        resp = MedchronBrokerClient().records_submit(envelope)
    except MedchronBrokerError as exc:
        return _refuse(f"the request could not be queued: {exc}")
    if not resp.get("accepted"):
        # A duplicate is named by ``duplicate``; ``reason`` is always a sentence.
        duplicate = str(resp.get("duplicate") or "")
        if duplicate in SILENT_REASONS:
            return _silent(duplicate)
        return _refuse(str(resp.get("reason") or "") or "the request was not queued")
    ack = str(resp.get("ack") or "").strip()
    if not ack:
        # The broker owns the words; a queued job with none to relay is SMD's
        # to answer, never the model's to write.
        logger.warning("records_job_submit: %s queued with no acknowledgement", resp.get("job_id"))
        return json.dumps(
            {"accepted": True, "job_id": resp.get("job_id"), "say": "none"}, ensure_ascii=False
        )
    logger.info("records_job_submit: queued %s", resp.get("job_id"))
    return json.dumps(
        {"accepted": True, "job_id": resp.get("job_id"), "say": ack}, ensure_ascii=False
    )


TOOLS: dict[str, tuple[str, dict[str, Any], Any]] = {
    "records_job_submit": (
        "Queue a matter-records request for the person whose email opened this turn. Never find, "
        "read, cut, attach or describe records in the turn: this queues a job that finds the "
        "records on the Machine and sends them in this thread once SMD has reviewed them. Pass the "
        "matter number exactly as the email writes it (matter_number) when it gives one, and "
        "nothing when it does not; never a Smokeball id, and look nothing up. Who asked, the "
        "email's id and its words are taken from the email itself, never from you. "
        'It answers with "say": relay that sentence exactly as the whole reply; when "say" is '
        '"none", send no reply at all. Any other answer is a reason to relay as it comes back.',
        _schema(
            {
                "matter_number": {
                    **STRING,
                    "description": "The matter number exactly as the email writes it.",
                },
            }
        ),
        records_job_submit,
    ),
}

__all__ = ["SILENT_REASONS", "TOOLS", "records_job_submit"]
