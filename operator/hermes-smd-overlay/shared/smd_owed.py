"""Record work SMD owes a firm person, through the broker, before saying anything
that promises it (owner's ruling, 2026-11-19).

The help guide's hand-off ("Let me research that and get back to you in this
thread.") and the rule partial commit ("... is not recorded yet. I'm looking
into it and will reply in this thread.") are true only if SMD is certain to
learn of the request. The broker writes the record in its own database in the
same call (``workspace_broker/smd_owed.py``, verb ``smd_owed_record``), and the
hourly triage lists it until SMD closes it. :func:`record` is True only when the
broker said it was written; on False the caller says nothing about that point,
and the unanswered request is listed as no reply.

Never raises.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from shared import act_broker, broker_socket

logger = logging.getLogger(__name__)

_SOCKET_ENV = act_broker.SOCKET_ENV  # the overlay's one home for the name
_TIMEOUT_SECONDS = 10
VERB = "smd_owed_record"
KIND_HELP = "help_question"
KIND_RULE = "rule_not_recorded"


def _request(payload: dict[str, Any]) -> dict[str, Any]:
    socket_path = os.environ.get(_SOCKET_ENV, "")
    if not socket_path:
        return {"ok": True, "smd_told": False}  # no broker: nothing could be recorded
    raw = broker_socket.request(socket_path, payload, timeout=_TIMEOUT_SECONDS)
    parsed = json.loads(raw.decode("utf-8"))
    return parsed if isinstance(parsed, dict) else {}


def record(kind: str, request_ref: Any, detail: str = "", send: Any = None) -> bool:
    """True only when the broker recorded it. ``send`` replaces the call (tests)."""
    ref = str(request_ref or "").strip()
    if not ref:
        logger.warning("smd_owed: %s has no request email id, not recorded", kind)
        return False
    try:
        answer = (send or _request)(
            {"action": VERB, "kind": kind, "request_ref": ref, "detail": str(detail or "")[:300]}
        )
    except Exception:  # noqa: BLE001 - unrecorded means say nothing, never a crash
        logger.warning("smd_owed: %s for %s not recorded", kind, ref[-16:], exc_info=True)
        return False
    told = isinstance(answer, dict) and answer.get("ok") is True and answer.get("smd_told") is True
    if not told:
        logger.warning("smd_owed: the broker did not record %s for %s", kind, ref[-16:])
    return told


def open_page(send: Any = None) -> dict[str, Any]:
    """The seat's ``smd_owed`` runtime read: every open owed item and every
    delivered drafting job with a served set still owed, with no lookback
    (broker ``smd_owed_open``). ``unavailable`` when the broker could not be
    read, so the triage never takes "could not look" for "nothing owed"."""
    empty: dict[str, Any] = {"entries": [], "owed_jobs": [], "cursor": None}
    if send is None and not os.environ.get(_SOCKET_ENV, ""):
        return {**empty, "unavailable": True}
    try:
        answer = (send or _request)({"action": "smd_owed_open"})
    except Exception:  # noqa: BLE001 - an unread broker is reported, never empty
        logger.warning("smd_owed: the open read failed", exc_info=True)
        return {**empty, "unavailable": True}
    if not isinstance(answer, dict) or answer.get("ok") is not True:
        return {**empty, "unavailable": True}
    return {
        "entries": [r for r in answer.get("owed") or [] if isinstance(r, dict)],
        "owed_jobs": [r for r in answer.get("owed_jobs") or [] if isinstance(r, dict)],
        "cursor": None,
    }


__all__ = ["KIND_HELP", "KIND_RULE", "VERB", "open_page", "record"]
