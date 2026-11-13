"""A records job's reply goes out once, across a restart (2026-11-13).

THE DEFECT. Records and medchron had no reply-once mark; a reply could go out
twice. A records job's reply is not a bound reply: it is the SMD review row the
records lane writes (``jobs/records_lane.py``), which the gateway's review
sweeper releases as an ordinary ``msgraph_reply`` once it is approved (overlay
``hermes-smd-reply._review_one``). Nothing on that path consulted the job's
ledger, so a second row for the same job (a lane re-entry that lost its saved
row, or a resume after a send whose outcome was unknown) would have sent the
records a second time.

THE CHANNEL. The release already reaches the broker, and already names the job
a hand release answers (``for_job``, ``hand_reply.py``). A lane row's job is the
one its ``session_id`` names (``records:<job_id>``), so the sweeper names it as
``{"kind": "records_job", "job_id": ...}`` and this module takes the ledger's
mark (``RecordsLedger.mark_replied``, :data:`REPLY_KEY`) AT the first POST of
that release, exactly where ``reply_binding._claiming_ops`` takes it for every
other lane: every refusal and GET before it spends nothing.

ONLY THE LEDGER MARK, NOT THE BOUND-REPLY CLAIM. A records reply is usually
several replies in one thread (its files split into parts,
``shared.matter_file_reply``), and the ordinary reply verb refuses any reply to
an email that holds a bound-reply claim, so a claim would refuse parts two and
on. The part-two replies carry no ``for_job``; the first one is the reply.

RELEASED only when the send provably did not happen: Graph refused the POST
(4xx) and Sent Items shows nothing in the thread since the mark. Every other
failure keeps the mark (a second email to a client is the worse error); the
lane's review watch pages SMD when the row ends ``failed``.
"""

from __future__ import annotations

import copy
import logging
import re
from datetime import datetime, timezone
from typing import Any

from . import bound_replies, msgraph_lookup
from .broker_context import BrokerContext
from .msgraph_ops import MsGraphRefused, MsGraphTransportError
from .records_store import RecordsLedger
from .reply_binding import BindingRefused, _audit
from .transmit_verbs import dispatch_transmit

logger = logging.getLogger(__name__)

KIND = "records_job"
#: One reply per records job, ever: the lane answers one email once
#: (``records_ledger``: "one email is answered by at most one job"), so the
#: mark's key does not move with the attempt the way a bound reply's does.
REPLY_KEY = "reply"
_JOB_ID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def names_records_job(for_job: Any) -> bool:
    return isinstance(for_job, dict) and for_job.get("kind") == KIND


def _job(broker: BrokerContext, raw: Any, message_id: str) -> tuple[RecordsLedger, str]:
    """The ledger and the job id, or refused: the job exists, SMD has not
    closed it, and the email this replies to is the job's own request."""
    if not isinstance(raw, dict) or set(raw) != {"kind", "job_id"}:
        raise BindingRefused("for_job names a records job: {kind, job_id}")
    job_id = str(raw.get("job_id") or "").strip()
    if not _JOB_ID.fullmatch(job_id):
        raise BindingRefused("for_job names a records job: {kind, job_id}")
    ledger = RecordsLedger(str(broker.audit_db_path))
    row = ledger.read(job_id)
    if row is None:
        raise BindingRefused(f"there is no records job {job_id}; nothing was sent")
    if row.get("close_ref"):
        raise BindingRefused(f"records job {job_id} was closed by SMD; nothing was sent")
    if str(row.get("message_ref") or "") != message_id:
        raise BindingRefused(f"that email is not records job {job_id}'s request; nothing was sent")
    if row.get("reply_sent_at"):
        raise BindingRefused(
            f"records job {job_id}'s reply is already recorded as sent at {row['reply_sent_at']}; nothing was sent"
        )
    return ledger, job_id


def records_reply(broker: BrokerContext, action: str, request: dict[str, Any]) -> dict[str, Any]:
    """The gateway's release of a records job's review row: an ordinary
    ``msgraph_reply`` whose first POST takes the job's reply-once mark."""
    if action != "msgraph_reply":
        raise BindingRefused("only a reply in the request's thread can be a records job's reply")
    ops = broker.msgraph
    db_path = str(broker.audit_db_path or "")
    if ops is None or not db_path:
        raise ValueError("a records job's reply needs the msgraph transmit and the audit ledger")
    payload = request.get("payload")
    if not isinstance(payload, dict):
        raise ValueError(f"{action} requires a 'payload' object")
    raw = request.get("for_job")
    message_id = str(payload.get("message_id") or "").strip()
    try:
        ledger, job_id = _job(broker, raw, message_id)
    except BindingRefused as exc:
        _audit(broker, "records_refused", raw, reason=str(exc))
        raise
    state: dict[str, Any] = {"marked": False, "at": ""}
    original = ops._request

    def marking_request(path: str, method: str, body: Any, **kw: Any) -> dict[str, Any]:
        # The mark rides the FIRST POST and nothing earlier.
        if method == "POST" and not state["marked"]:
            if not ledger.mark_replied(job_id, REPLY_KEY):
                raise BindingRefused(f"records job {job_id}'s record shows its reply was already sent")
            state["marked"] = True
            state["at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return original(path, method, body, **kw)

    # A per-call copy, so the hook never touches the shared ops object.
    bound_ops = copy.copy(ops)
    bound_ops._request = marking_request  # type: ignore[method-assign]

    def reply(body: dict[str, Any]) -> dict[str, Any]:
        # The ordinary reply verb's own rule: an email that had its bound
        # reply is answered.
        if bound_replies.claimed_for_graph_id(db_path, str(body.get("message_id") or "")):
            raise MsGraphRefused("that email has already had its bound reply; this reply would answer it twice")
        return bound_ops.reply(body)

    try:
        return dispatch_transmit(
            broker,
            action,
            {k: v for k, v in request.items() if k != "for_job"},
            send=ops.send,
            reply=reply,
            refused=MsGraphRefused,
            transport=MsGraphTransportError,
            attempted_for_send=lambda _payload: [],
            identity_key="mailbox",
            channel="msgraph",
        )
    except Exception as exc:
        if state["marked"]:
            _settle_failed_post(broker, ledger, job_id, raw, message_id, exc, str(state["at"]))
        raise


def _settle_failed_post(
    broker: BrokerContext, ledger: RecordsLedger, job_id: str, raw: Any, message_id: str, exc: Exception, at: str
) -> None:
    """A 4xx is Graph refusing the request, which sends nothing: if Sent Items
    also shows nothing in the thread since the mark, the mark is RELEASED.
    Anything else keeps it."""
    status = getattr(exc, "status", None)
    if not (isinstance(status, int) and 400 <= status < 500 and at and broker.msgraph is not None):
        return
    ops = broker.msgraph
    try:
        found = msgraph_lookup.received_by_graph_id(ops, message_id)
        delivered = found is None or msgraph_lookup.sent_in_conversation_since(ops, found["conversation_id"], at)
    except (MsGraphTransportError, MsGraphRefused):
        delivered = True
    if delivered:
        return
    ledger.unmark_replied(job_id, REPLY_KEY)
    _audit(broker, "records_released", raw, reason=f"Graph refused the reply (HTTP {status}); nothing was sent")


__all__ = ["KIND", "REPLY_KEY", "names_records_job", "records_reply"]
