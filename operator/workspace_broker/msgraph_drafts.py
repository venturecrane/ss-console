"""``msgraph_discard_draft``: delete the agent's working draft once its reply is out.

WHY. The overlay's reply relay never sends the draft the agent's
``create_draft`` made; it transmits the reply as a new message through
``msgraph_reply`` / ``msgraph_reply_bound``, the recipient-locked and audited
path. The draft was only the agent's working copy and nothing removed it: on
ashton-price the operator mailbox's Drafts folder held 50+ unsent drafts to
firm staff by 2026-10-12, every one a reply that had in fact gone out. The relay
(overlay ``plugins/hermes-smd-reply/working_drafts.py``) decides WHICH drafts
are spent; this verb only deletes one.

WHAT IT WILL DELETE. One message in the seat's own pinned mailbox that Graph
still reports as ``isDraft``. A sent or received message is refused, so a
wrong id can never remove mail anyone sent or received. A message already gone
answers ``absent``. Graph's DELETE moves the message to Deleted Items, so even
a correct delete stays recoverable by the firm.

ON THE READ CREDENTIAL. The read app holds ``Mail.ReadWrite`` (the same
registration the agent's own mail tools use to create the draft); the send app
holds ``Mail.Send`` only (overlay#280) and could do neither the GET nor the
DELETE. Nothing widens: the agent already holds this exact credential.
"""

from __future__ import annotations

from typing import Any

from .broker_context import BrokerContext
from .msgraph_ops import MsGraphOps, MsGraphRefused, MsGraphTransportError

VERB = "msgraph_discard_draft"
DISCARDED = "discarded"
ABSENT = "absent"


def discard_draft(ops: MsGraphOps, draft_id: str) -> str:
    """Delete ``draft_id`` if it is still a draft; ``discarded`` or ``absent``."""
    draft_id = str(draft_id or "").strip()
    if not draft_id:
        raise MsGraphRefused("no draft id; nothing to discard")
    if ops._read_credential_path is None:
        raise MsGraphTransportError("no msgraph read credential; cannot discard a draft")
    read = {"credential_path": ops._read_credential_path, "role": "read"}
    path = ops._mail_path("messages", draft_id)
    try:
        found = ops._request(path + "?$select=id,isDraft", "GET", None, **read)
    except MsGraphTransportError as exc:
        if getattr(exc, "status", None) == 404:
            return ABSENT
        raise
    if found.get("isDraft") is not True:
        raise MsGraphRefused("that message is not a draft; only a draft is ever discarded")
    try:
        ops._request(path, "DELETE", None, **read)
    except MsGraphTransportError as exc:
        if getattr(exc, "status", None) == 404:
            return ABSENT
        raise
    return DISCARDED


def discard_draft_verb(
    broker: BrokerContext, _action: str, request: dict[str, Any], _pid: int, _uid: int | None
) -> dict[str, Any]:
    if broker.msgraph is None:
        raise ValueError("msgraph is not configured on this broker (needs SMD_MSGRAPH_CREDENTIAL_PATH)")
    payload = request.get("payload")
    draft_id = payload.get("draft_id") if isinstance(payload, dict) else None
    if not isinstance(draft_id, str):
        raise MsGraphRefused("payload.draft_id must be a string")
    return {"ok": True, "outcome": discard_draft(broker.msgraph, draft_id)}


__all__ = ["ABSENT", "DISCARDED", "VERB", "discard_draft", "discard_draft_verb"]
