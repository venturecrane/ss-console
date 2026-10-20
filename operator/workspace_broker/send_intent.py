"""Record-before-send for every broker transmit (2026-10-20).

THE DEFECT THIS CLOSES. Until today the transmit verbs sent FIRST and recorded
AFTER: ``send(payload)``, then ``append_send_row(... CONFIRM_SEND_DISPATCHED)``.
If that append failed after a successful vendor call (sqlite busy past the 5 s
timeout, an IO error, or the broker killed between the two lines) the email was
out, no audit row existed, and the caller was told the send had FAILED, which is
exactly the answer that invites a retry and a second copy to a client.

THE SHAPE NOW, three rows' worth of promises:

1. An INTENT row (``CONFIRM_SEND_INTENT``) is appended before the vendor is
   called. It carries what the failure row always carried (the recipients
   attempted where a send names them, the input digest, the verb, the session
   and matter joins, the caller's closed-list stamps, the fence metadata) and
   never the body.
2. FAIL-CLOSED. If the intent cannot be written, nothing is sent, and the
   caller gets the channel's own REFUSAL saying so in words ("not sent"), never
   a transport error, whose meaning is "it may have gone".
3. The existing CONFIRM_SEND_DISPATCHED / CONFIRM_SEND_FAILED row stays the
   confirm row and names its intent (``intent_row``), so the two join. If the
   confirm cannot be written after a send that succeeded, the caller is told
   the send SUCCEEDED, flagged ``confirm_row_written: False``: the message is
   out, and "failed" would be a lie that buys a duplicate.

AN INTENT WITH NO CONFIRM IS VISIBLE. The console's send reconciler grades it
``send_unconfirmed`` (operator/bin/lib/send_invariants.py ``intent_invariant``),
and on a seat mailbox the message itself is also unaccounted, because no
dispatch row carries its id. Either way it pages; neither way is it silent.

The row writer is passed in rather than imported: ``transmit_verbs`` owns
``append_send_row`` and imports this module, so importing it back would be a
cycle.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .broker_context import BrokerContext

logger = logging.getLogger(__name__)

INTENT_ACTION_TYPE = "CONFIRM_SEND_INTENT"

#: The metadata key on a confirm row that names the intent row it settles.
INTENT_KEY = "intent_row"

#: The response flag set when a send went out and its confirm row did not land.
CONFIRM_FLAG = "confirm_row_written"

#: What the caller is told when the intent could not be written. Leads with the
#: outcome, because an agent reading it must never wonder whether to resend.
NOT_SENT = "not sent: the audit ledger could not record this send before it was made, so it was not attempted"

#: ``append_send_row``'s shape: (broker, action_type, verb, metadata, *, session_id, matter_ref) -> row id.
RowWriter = Callable[..., str]


def record_intent(
    append: RowWriter,
    broker: BrokerContext,
    verb: str,
    metadata: dict[str, Any],
    *,
    session_id: str = "",
    matter_ref: str | None = None,
    refused: type[Exception],
) -> str:
    """Append the intent row and return its id, or raise ``refused(NOT_SENT)``.

    Any failure refuses, including a ledger that is not configured (the writer
    returns ``""``): a send with no row is the thing this exists to prevent, so
    "could not look" never reads as "permitted". The metadata is copied first
    because the writer pops ``skill_name`` off the dict it is handed.
    """
    try:
        intent_id = append(
            broker, INTENT_ACTION_TYPE, verb, dict(metadata), session_id=session_id, matter_ref=matter_ref
        )
    except Exception as exc:  # every failure to record means do not send
        logger.exception("%s: intent row not written; the send was not attempted", verb)
        raise refused(NOT_SENT) from exc
    if not intent_id:
        logger.error("%s: no audit ledger for the intent row; the send was not attempted", verb)
        raise refused(NOT_SENT)
    return intent_id


def record_confirm(
    append: RowWriter,
    broker: BrokerContext,
    action_type: str,
    verb: str,
    metadata: dict[str, Any],
    intent_id: str,
    *,
    session_id: str = "",
    matter_ref: str | None = None,
) -> bool:
    """Append the confirm row naming its intent. True when it landed.

    Never raises. The vendor call has already happened by the time this runs,
    so the confirm's own failure must not replace the send's real outcome: on a
    success the caller is still told it succeeded (with the flag), and on a
    vendor failure the vendor's exception is what propagates. The orphan intent
    is what makes the gap visible afterwards.
    """
    try:
        append(
            broker,
            action_type,
            verb,
            {**metadata, INTENT_KEY: intent_id},
            session_id=session_id,
            matter_ref=matter_ref,
        )
    except Exception:  # the send has happened; its outcome stands whatever the ledger did
        logger.exception("%s: %s row not written after the vendor call (intent %s)", verb, action_type, intent_id)
        return False
    return True


__all__ = ["CONFIRM_FLAG", "INTENT_ACTION_TYPE", "INTENT_KEY", "NOT_SENT", "record_confirm", "record_intent"]
