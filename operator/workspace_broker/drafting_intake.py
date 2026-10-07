"""Two intake helpers the drafting lane shares in BEHAVIOR with the demand
lane, held here as the drafting lane's own copies.

The demand lane is live and owned separately; importing its private helpers
would let a demand change move the drafting contract without anyone touching
a drafting file. These are copies of ``demand_verbs.operator_library_number``
and ``demand_verbs._resolve_request_graph_id`` as of 2026-10-07, and
``tests/test_drafting_intake.py`` drives both copies and demand's through the
same cases, so a divergence is a visible test failure, decided on purpose.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .broker_context import BrokerContext


def operator_library_number(path: str | Path) -> str | None:
    """The firm's own authored library matter number (``self_initiation.
    document_library.operator_matter.number``), the one matter a rehearsal may
    file on instead of the matter it reads. None when unauthored."""
    try:
        import yaml

        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        number = doc["self_initiation"]["document_library"]["operator_matter"]["number"]
    except Exception:  # noqa: BLE001 - unauthored or unreadable authorizes nothing
        return None
    return number.strip() if isinstance(number, str) and number.strip() else None


def resolve_request_graph_id(broker: BrokerContext, envelope: dict[str, Any], peer_uid: int | None) -> dict[str, Any]:
    """ROOT ONLY: queue a request whose email the seat knows only by its Graph id.
    The broker resolves the id itself, with the reply binding's lookup (Inbox
    only, never a draft, never this mailbox's own mail, no replyTo elsewhere),
    requires the sender to be one the seat may reply to AND to equal
    ``requested_by``, and sets ``request_ref`` to the email's resolved
    internetMessageId, so the job's completion reply threads to that email."""
    if peer_uid != 0:
        raise PermissionError("request_graph_id is accepted from root only")
    from . import msgraph_lookup
    from .msgraph_ops import MsGraphRefused
    from .recipient_policy import authored_policy, normalize_address

    if broker.msgraph is None:
        raise ValueError("request_graph_id needs the msgraph read credential on this broker")
    if "request_ref" in envelope:
        return {"refused": "pass request_graph_id or request_ref, not both"}
    gid = str(envelope.get("request_graph_id") or "").strip()
    if not gid or not all(c.isalnum() or c in "=_-" for c in gid):
        return {"refused": "request_graph_id must be the email's Graph id"}
    try:
        found = msgraph_lookup.received_by_graph_id(broker.msgraph, gid)
    except MsGraphRefused as exc:
        return {"refused": str(exc)}
    if found is None:
        return {"refused": "that email is not in this seat's Inbox as a received message"}
    sender = found["sender"]
    if not authored_policy(broker.customer_path).allows_reply_to(sender):
        return {"refused": "the sender of that email is not someone this seat may reply to"}
    if normalize_address(str(envelope.get("requested_by") or "")) != sender:
        return {"refused": "requested_by must be the sender of that email"}
    out = {k: v for k, v in envelope.items() if k != "request_graph_id"}
    out["request_ref"] = found["internet_message_id"]
    return {"envelope": out}


__all__ = ["operator_library_number", "resolve_request_graph_id"]
