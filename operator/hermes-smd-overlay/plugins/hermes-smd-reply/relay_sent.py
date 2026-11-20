"""What the relay does once the reply is out: step 9's send record.

``_relay_transmit`` (the plugin's ``__init__``) hands the reply to the
transport; once it returns the sent message's id, the reply is in the firm's
inbox, and ``sent`` commits it, notes the bytes, writes the REPLY_SENT row
(``audit_sent``) and clears the working drafts. A fault in any of that is
never silent and never sends the reply twice (known defect
``fault-after-transmit-leaves-no-trace``, fixed in wave 4): ``trace_incomplete``
counts the reply as carried, writes its REPLY_SENT row if none was written yet
(marked ``trace_incomplete``), tells the turn it was sent and must not be sent
again, and pages SMD.

Plugin names are read through ``_plugin`` at call time, as ``relay_checks``
reads them (its docstring says why).
"""

from __future__ import annotations

import logging
import sys
from types import ModuleType
from typing import Any

from shared import inbound, matter_file_reply, reply_could_not, reply_sent, request_index

from . import notice, relay

_PACKAGE = __name__.rpartition(".")[0]
#: The plugin module this copy serves (see ``relay_checks._plugin``).
_plugin: ModuleType = sys.modules[_PACKAGE]
logger = logging.getLogger(_PACKAGE)


def sent(
    session_id: str,
    origin: Any,
    reply_key: str,
    reply_to: Any,
    scan_text: str,
    wire: tuple[str, str],
    matter: tuple[Any, Any],
    sent_id: Any,
    adapter: str,
    recipient_class: Any,
    internal: bool,
) -> None:
    """The reply is on the wire: commit it, audit it, send its continuations.

    A fault in any of it is a reply that went out with its trace unfinished:
    the handler hands it to ``trace_incomplete`` (the plugin's
    ``_sent_trace_incomplete``, with whether the REPLY_SENT row was already
    written) and re-raises, so the fault still reaches the catch-all, which
    logs it."""
    cited_matter_ref, prepared = matter
    _plugin._CURRENT_CALL.transmitted = True
    row_written = False
    try:
        if prepared is not None:
            # The files are on their way: the staging is spent either way.
            matter_file_reply.discard(session_id)

        # (f) The reply for this inbound is now committed — record it before the
        # audit emission so a retry cannot race between the send and the mark.
        _plugin._commit_reply(reply_key, origin)
        # The only evidence the end-of-turn act delivery trusts: bytes the
        # transport was actually handed (act_delivery module docstring).
        _plugin._ACT_BODIES.note(session_id, *wire)

        # (g) Audit the send — digest + recipient + message ids, never the body.
        _plugin._emit_reply_event(
            action_type="REPLY_SENT",
            metadata=sent_metadata(
                origin,
                reply_to,
                recipient_class,
                internal,
                adapter,
                sent_id,
                scan_text,
                wire,
                matter,
            ),
            session_id=session_id,
            matter_ref=cited_matter_ref,
        )
        row_written = True
        if prepared is not None and len(prepared.batches) > 1:
            _plugin._send_file_continuations(
                prepared, origin, session_id=session_id, matter_ref=cited_matter_ref
            )
        # Request cards: the opening of the first reply, seat-local only (the
        # audit row above carries digests, never words). Never raises.
        request_index.record_reply_opening(origin.message_id, wire[0], sent_message_id=sent_id)
        # (h) The reply is out: its working drafts (this one, and any held ones
        # it superseded in this turn) leave the Drafts folder. Never raises.
        _plugin._discard_drafts(_plugin._WORKING_DRAFTS.take(session_id, reply_key))
    except:  # noqa: E722 - re-raised; a bare except adds no branch no input can take
        _plugin._sent_trace_incomplete(
            reply_key,
            row_written,
            session_id,
            origin,
            reply_to,
            recipient_class,
            internal,
            adapter,
            sent_id,
            scan_text,
            wire,
            matter,
        )
        raise


def audit_sent(
    session_id: str,
    origin: Any,
    reply_to: Any,
    recipient_class: Any,
    internal: bool,
    adapter: str,
    sent_id: Any,
    scan_text: str,
    wire: tuple[str, str],
    matter: tuple[Any, Any],
) -> None:
    """(g) Audit the send, then send any file continuations and card the reply's
    opening: what ``sent`` does after the commit, for a caller that wants only
    that."""
    cited_matter_ref, prepared = matter
    _plugin._emit_reply_event(
        action_type="REPLY_SENT",
        metadata=sent_metadata(
            origin,
            reply_to,
            recipient_class,
            internal,
            adapter,
            sent_id,
            scan_text,
            wire,
            matter,
        ),
        session_id=session_id,
        matter_ref=cited_matter_ref,
    )
    if prepared is not None and len(prepared.batches) > 1:
        _plugin._send_file_continuations(
            prepared, origin, session_id=session_id, matter_ref=cited_matter_ref
        )
    request_index.record_reply_opening(origin.message_id, wire[0], sent_message_id=sent_id)
    # The reply is out: what keys off a SENT reply runs now (shared/reply_sent.py):
    # the read-back binding and the help line's offered mark.
    reply_sent.notify(origin, sent_id, wire[0])


def sent_metadata(
    origin: Any,
    reply_to: Any,
    recipient_class: Any,
    internal: bool,
    adapter: str,
    sent_id: Any,
    scan_text: str,
    wire: tuple[str, str],
    matter: tuple[Any, Any],
) -> dict[str, Any]:
    """A REPLY_SENT row's metadata: digest + recipient + message ids, never the body."""
    wire_text, wire_html = wire
    prepared = matter[1]

    inbound_sender_key = _plugin.sender_key(origin.sender_address)
    sender_key_metadata = {"sender_key": inbound_sender_key} if inbound_sender_key else {}
    return {
        "recipient": reply_to,
        # For a device redirect: which device wrote in. Absent otherwise.
        **_plugin._reply_recipient(origin)[1],
        "recipient_class": recipient_class.value if recipient_class else "unclassified",
        "content_floor_applied": not internal,
        "adapter": adapter,
        "in_reply_to": origin.message_id,
        "inbox_id": origin.inbox_id,
        "sent_message_id": sent_id,
        "body_digest": inbound.content_digest(scan_text),
        # The digests an OUTSIDER can recompute (ss-console#2501). The
        # line above stays what it has always been: sha256 of the string
        # the floors scanned, which folds in a subject the wire never
        # carries and is therefore only checkable inside this system.
        # These two cover exactly the bytes handed to the transport, so
        # counsel holding the firm's own Sent Items copy can verify the
        # body without taking SMD's word for it. Recipe, per transport,
        # is in ``relay.authored_digests``.
        **relay.authored_digests(text=wire_text, html=wire_html),
        # Which PERSON this answered, as a key rather than an address
        # (ss-console#2497). ``recipient`` above is the raw address and
        # predates this issue; ``sender_key`` is what joins this row to
        # the INBOUND_RECEIVED row that opened the turn, without a second
        # address leaving the Machine in an export.
        **sender_key_metadata,
        # 2026-11-01: a reply that told the person the Operator could
        # not do what they asked, as a closed code (never the words).
        # The heartbeat pages it as a shortfall and the request card
        # marks the request NOT FULFILLED (shared/reply_could_not.py).
        **reply_could_not.metadata(wire_text),
        # ADR 0102: the files this reply carried (ids and hashes only).
        **(
            {
                **matter_file_reply.audit_fields(prepared.batches[0]),
                "file_reply_part": f"1/{len(prepared.batches)}",
            }
            if prepared is not None
            else {}
        ),
    }


def trace_incomplete(
    reply_key: str,
    row_written: bool,
    session_id: str,
    origin: Any,
    reply_to: Any,
    recipient_class: Any,
    internal: bool,
    adapter: str,
    sent_id: Any,
    scan_text: str,
    wire: tuple[str, str],
    matter: tuple[Any, Any],
) -> None:
    """A fault after the transport returned the sent id: the reply is in their
    inbox and its trace is unfinished. Count it as carried, exactly as a lost
    answer is (``RelayOutcomeUnknown`` in ``_relay_transmit``), so a redraft in
    this turn relays nothing and an act line it carried is not owed again. Then
    write its REPLY_SENT row if none was written yet, marked ``trace_incomplete``
    with the fault's type (never a second one, never a REPLY_FAILED), card its
    opening, tell the turn it was sent and must not be sent again, and page SMD.

    Each step stands alone, so a fault that recurs on every call (the same
    raise in ``_commit_reply``, in the row's metadata, in the emit) stops only
    that step: the commit falls back to the register itself, the row to its
    minimal fields, and the telling and the page always go. Called from the
    handler, while the fault is the one being handled. Never raises."""
    fault = type(sys.exception()).__name__
    _mark_carried(reply_key, origin, session_id, wire)
    if not row_written:
        _trace_row(
            fault,
            session_id,
            origin,
            reply_to,
            recipient_class,
            internal,
            adapter,
            sent_id,
            scan_text,
            wire,
            matter,
        )
    _tell_and_page(origin, adapter)


def _mark_carried(reply_key: str, origin: Any, session_id: str, wire: tuple[str, str]) -> None:
    """Commit the reply (through the register itself if ``_commit_reply``
    faults again), then note its bytes. Never raises."""
    try:
        _plugin._commit_reply(reply_key, origin)
    except Exception as exc:  # noqa: BLE001 - the register itself, below
        logger.warning("hermes-smd-reply: commit faulted again (%s); committing directly", exc)
        try:
            _plugin._REPLIED.commit(reply_key)
            _plugin._REPLIED.commit(origin.message_id)
        except Exception as again:  # noqa: BLE001 - nothing further can commit it
            logger.warning("hermes-smd-reply: a sent reply not committed (%s)", again)
    try:
        _plugin._ACT_BODIES.note(session_id, *wire)
    except Exception as exc:  # noqa: BLE001 - the row and the page still go
        logger.warning("hermes-smd-reply: a sent reply's bytes not noted (%s)", exc)


def _trace_row(
    fault: str,
    session_id: str,
    origin: Any,
    reply_to: Any,
    recipient_class: Any,
    internal: bool,
    adapter: str,
    sent_id: Any,
    scan_text: str,
    wire: tuple[str, str],
    matter: tuple[Any, Any],
) -> None:
    """The REPLY_SENT row of a reply whose trace broke, and its card's opening.
    Its metadata is a clean send's; if building that faults again, the minimal
    fields that join it to the email and the sent message. Never raises."""
    try:
        metadata = sent_metadata(
            origin, reply_to, recipient_class, internal, adapter, sent_id, scan_text, wire, matter
        )
    except Exception as exc:  # noqa: BLE001 - the minimal row below
        logger.warning("hermes-smd-reply: a sent reply's metadata faulted again (%s)", exc)
        metadata = {
            "in_reply_to": origin.message_id,
            "sent_message_id": sent_id,
            "recipient": reply_to,
            "adapter": adapter,
        }
    try:
        _plugin._emit_reply_event(
            action_type="REPLY_SENT",
            metadata={**metadata, "trace_incomplete": True, "fault": fault},
            session_id=session_id,
            matter_ref=matter[0],
        )
    except Exception as exc:  # noqa: BLE001 - no row can be written; the page still goes
        logger.warning("hermes-smd-reply: a sent reply's row not written (%s)", exc)
    request_index.record_reply_opening(origin.message_id, wire[0], sent_message_id=sent_id)


def _tell_and_page(origin: Any, adapter: str) -> None:
    """Tell the turn the reply was sent and must not be sent again, without
    counting it as a hold of this email, and page SMD. Never raises."""
    try:
        recipient = _plugin._reply_recipient(origin)[0]
    except Exception:  # noqa: BLE001 - the verified sender, unredirected
        recipient = origin.sender_address
    try:
        _plugin._HOLD_NOTICES.record(
            tool_call_id=getattr(_plugin._CURRENT_CALL, "tool_call_id", "") or "",
            reason=notice.SENT_TRACE_INCOMPLETE,
            recipient=recipient,
            message_id=origin.message_id,
            counted=False,
        )
    except Exception as exc:  # noqa: BLE001 - the page below still goes
        logger.warning("hermes-smd-reply: a sent reply's notice not recorded (%s)", exc)
    try:
        _plugin._notify_hold(
            reason=notice.SENT_TRACE_INCOMPLETE,
            sender=recipient,
            sender_class="unclassified",
            adapter=adapter,
            message_id=origin.message_id,
            body_digest="",
            title="reply_sent_trace_incomplete",
        )
    except Exception as exc:  # noqa: BLE001 - telling must never break the hook
        logger.warning("hermes-smd-reply: a sent reply's fault not paged (%s)", exc)
