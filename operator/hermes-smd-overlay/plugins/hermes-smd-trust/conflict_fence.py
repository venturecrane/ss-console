"""The conflict fence: an outside sender's email moves nothing until the
conflict check has cleared it.

WHY (2026-11-04, the model-as-logic audit, the conflict halt). The router's
conflict step was prose: the model chose whether to check, what to search and
what counted, and a miss went on to a draft, a handoff or a Smokeball write.
The check is now a connector read with a written rule table
(``mcp_smokeball_conflict_check``), and this fence is what makes its answer
binding: the gateway cannot search Smokeball itself (``shared/matter_binding.py``
has no client, by design), so the model calls the tool, ``post_tool_call``
records the connector's own result (``shared/conflict_facts.py``), and every
other tool call in the turn is checked here against that record.

THE RULE. In a turn opened by an EMAIL (the router's verified envelope named
its message id) on a seat that authors the Smokeball connector, from a sender
NOT on the firm's roster:

* any call that is not a read (a write, a draft, a send, code) is refused
  until ``conflict_check`` has returned ``clear`` for that sender and subject
  this turn: "Run conflict_check first; nothing is written or sent until it
  returns clear";
* once it has returned ``hit``, every such call is refused for the rest of
  the turn: the halt. The clearance task is the tool's own write, already made.

WHAT IS NEVER FENCED, and why:

* a webhook or handoff turn (a Smokeball event, a job's completion): no email
  opened it, so there is no sender to be in conflict with;
* a sender on the roster. The firm's own people forward the day's post, the
  vendor invoices and the served papers; those daily paths must not wait on a
  check about the firm's own staff. A non-roster sender's reply is already
  held before send (``hermes-smd-reply``, ``sender_not_on_roster``); this fence
  adds the WRITES, which nothing held before;
* a seat with no Smokeball connector: there is no conflict_check to call, and
  a fence the model cannot satisfy is a fence that stops all work.

WHAT DOES NOT REACH IT. The router's classification (new inquiry, new client)
is the model's, in the turn, and no code path carries it to the overlay
(grep: no consumer of an inbound class under ``shared/`` or ``plugins/``). So
the fence keys on what code knows, the verified sender and the roster, and a
rostered colleague's new-client inquiry is the router's own conflict step.

FAIL CLOSED. An email turn whose sender cannot be found in the verified index,
or a customer.yaml that cannot be read, is fenced: "cannot tell" is never
"permitted". A tool that cannot be classified is left to the ceiling, which
refuses it one layer up and names the right fault.

THE SWITCH (``logic_in_code.conflict_fence``, ``shared/logic_in_code.py``, ADR
0104, read fresh on every call). A merge reaches a client seat before anything
proves it there, so this ships recording only:

* ``enforce`` is everything above;
* ``observe``, the default, refuses nothing. Every call the fence would have
  refused, for every reason above including the fail-closed ones, writes one
  ``CONFLICT_FENCE_WOULD_REFUSE`` row to the seat's audit ledger and goes through.
  A row that cannot be written is logged, and the call still goes through. A fault
  in the fence itself is recorded the same way in ``observe`` and refuses in
  ``enforce`` (it re-raises to the trust hook, which fails closed).

The verdict recording below runs in both modes, and ``conflict_check`` itself does
not know the switch exists. The row carries the tool, the reason and the join keys,
never a tool argument's text; the sender is the audit contract's hash.

    SELECT ts, matter_ref, metadata FROM audit_log
    WHERE action_type = 'CONFLICT_FENCE_WOULD_REFUSE' ORDER BY ts
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from shared import capabilities, conflict_facts, logic_in_code, seat_doc
from shared.audit_contract import (
    CANONICAL_TOOL_CALL_KEY,
    INSERT_SQL,
    agent_event_params,
    sender_key,
)
from shared.customer_config import CustomerConfig, CustomerConfigError
from shared.inbound import SESSION_INBOUND_ORIGIN

from . import enforce, outbound

logger = logging.getLogger(__name__)

WOULD_REFUSE_ACTION = "CONFLICT_FENCE_WOULD_REFUSE"

#: Why this turn is fenced at all. The last three are the fail-closed cases.
BASIS_OFF_ROSTER = "sender_off_roster"
BASIS_SENDER_UNKNOWN = "sender_not_in_verified_index"
BASIS_ROSTER_UNREADABLE = "roster_unreadable"
BASIS_SEAT_UNREADABLE = "seat_config_unreadable"

#: Why this call would have been refused, beyond the conflict_facts verdicts.
REASON_NO_CHECK = "no_check_this_turn"
REASON_FENCE_FAULT = "fence_fault"

RUN_FIRST = (
    "Run conflict_check first; nothing is written or sent until it returns clear. "
    "Pass the sender's name and address and the subject line exactly as this email's "
    "headers give them, and every party the message names."
)
HELD = (
    "conflict_check returned hit for this message: it is held for conflict clearance. "
    "Nothing is written, sent, drafted or routed this turn. The clearance task is "
    "already in Smokeball; end the turn without replying."
)


def _is_read(tool_name: str) -> bool | None:
    """True for a read, False for anything else, None when unclassifiable."""
    try:
        return enforce.classify_tool(tool_name).action_class is enforce.ActionClass.READ
    except Exception:  # noqa: BLE001 - the ceiling refuses an unclassifiable tool and names it
        return None


@dataclass(frozen=True)
class FencedTurn:
    message_id: str
    origin: Any
    basis: str


def _on_roster(address: str) -> bool | None:
    """Is this address on the roster? None when the roster cannot be read."""
    try:
        return CustomerConfig.from_volume().sender_on_roster(address)
    except (CustomerConfigError, OSError):
        logger.warning("hermes-smd-trust: conflict fence cannot read the roster; fencing")
        return None


def _basis(origin: Any, seat_doc_read: bool) -> str | None:
    """Why this email turn is fenced, or None when it is not."""
    if origin is None:
        return BASIS_SENDER_UNKNOWN if seat_doc_read else BASIS_SEAT_UNREADABLE
    roster = _on_roster(origin.sender_address)
    if roster is True:
        return None
    if roster is None:
        return BASIS_ROSTER_UNREADABLE
    return BASIS_OFF_ROSTER if seat_doc_read else BASIS_SEAT_UNREADABLE


def fenced_turn(session_id: str) -> FencedTurn | None:
    """The turn, when it is fenced, else None."""
    emailed, origin = SESSION_INBOUND_ORIGIN.turn_origin(session_id)
    if not emailed:
        return None
    doc = seat_doc.live_doc()
    if doc is not None and not capabilities.authors_connector(doc, "smokeball"):
        return None
    basis = _basis(origin, doc is not None)
    if basis is None:
        return None
    return FencedTurn(SESSION_INBOUND_ORIGIN.turn_prompt_id(session_id), origin, basis)


def _would_refuse_metadata(
    *,
    tool_name: str,
    reason: str,
    basis: str,
    switch_error: str | None,
    origin: Any,
    message_id: str,
    clearance_task_id: str,
    tool_call_id: str,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {"tool": tool_name, "reason": reason, "basis": basis}
    optional = {
        "vendor_message_id": message_id,
        "sender_key": sender_key(origin.sender_address) if origin is not None else None,
        "clearance_task_id": clearance_task_id,
        "switch_error": switch_error,
        CANONICAL_TOOL_CALL_KEY: tool_call_id,
    }
    metadata.update({key: value for key, value in optional.items() if value})
    return metadata


def _write_would_refuse(metadata: dict[str, Any], session_id: str, args: Any) -> None:
    """One audit row. Never raises: observing must not stop the call it observes."""
    try:
        client, _slug = outbound._audit_client()
        if client is None:
            logger.warning(
                "hermes-smd-trust: %s for %s not recorded (no audit client)",
                WOULD_REFUSE_ACTION,
                metadata.get("tool"),
            )
            return
        matter = args.get("matter_id") if isinstance(args, dict) else None
        params = agent_event_params(
            action_type=WOULD_REFUSE_ACTION,
            metadata=metadata,
            session_id=session_id,
            matter_ref=matter if isinstance(matter, str) else None,
        )
        client.execute(INSERT_SQL, *params)
    except Exception:  # noqa: BLE001 - a lost row is logged; the observed call proceeds
        logger.warning(
            "hermes-smd-trust: %s for %s not recorded",
            WOULD_REFUSE_ACTION,
            metadata.get("tool"),
            exc_info=True,
        )


def _decide(tool_name: str, session_id: str) -> tuple[dict[str, Any], str, FencedTurn, str] | None:
    """``(block, reason, turn, clearance task)`` for a call the fence stops, else None."""
    if _is_read(tool_name) is not False:
        return None
    turn = fenced_turn(session_id)
    if turn is None:
        return None
    held = conflict_facts.verdict_for(session_id, turn.message_id)
    if held is None:
        return {"action": "block", "message": RUN_FIRST}, REASON_NO_CHECK, turn, ""
    if held.verdict == conflict_facts.CLEAR:
        return None
    if held.verdict == conflict_facts.HIT:
        return {"action": "block", "message": HELD}, held.verdict, turn, held.clearance_task_id
    return {"action": "block", "message": RUN_FIRST}, held.verdict, turn, ""


def check(
    tool_name: str, session_id: str, args: Any = None, tool_call_id: str = ""
) -> dict[str, Any] | None:
    """The block directive for this call, or None to let it through."""
    switch = logic_in_code.load_switch(logic_in_code.CONFLICT_FENCE)
    try:
        decided = _decide(tool_name, session_id)
    except Exception:
        if switch.enforcing:
            raise  # the trust hook fails closed and names the fault
        logger.warning("hermes-smd-trust: conflict fence raised while observing", exc_info=True)
        metadata = {"tool": tool_name, "reason": REASON_FENCE_FAULT, "basis": "unknown"}
        _write_would_refuse(metadata, session_id, args)
        return None
    if decided is None:
        return None
    block, reason, turn, task = decided
    if switch.enforcing:
        logger.info("hermes-smd-trust: %s refused by the conflict fence (%s)", tool_name, reason)
        return block
    metadata = _would_refuse_metadata(
        tool_name=tool_name,
        reason=reason,
        basis=turn.basis,
        switch_error=switch.error,
        origin=turn.origin,
        message_id=turn.message_id,
        clearance_task_id=task,
        tool_call_id=tool_call_id,
    )
    _write_would_refuse(metadata, session_id, args)
    return None


def record(tool_name: str, session_id: str, args: Any, result: Any) -> None:
    """Keep a ``conflict_check`` result for the turn it answers. Never raises."""
    if tool_name != conflict_facts.TOOL:
        return
    try:
        emailed, origin = SESSION_INBOUND_ORIGIN.turn_origin(session_id)
        if not emailed:
            return
        verdict, task = conflict_facts.classify(
            args,
            result,
            sender_address=origin.sender_address if origin is not None else "",
            subject=origin.subject if origin is not None else "",
        )
        conflict_facts.record(
            session_id, SESSION_INBOUND_ORIGIN.turn_prompt_id(session_id), verdict, task
        )
    except Exception:  # noqa: BLE001 - a lost record leaves the turn fenced, never open
        logger.warning("hermes-smd-trust: recording a conflict_check result failed", exc_info=True)


__all__ = ["HELD", "RUN_FIRST", "WOULD_REFUSE_ACTION", "check", "fenced_turn", "record"]
