"""The steps of the trust wall, ``enforce._evaluate_tool_call``.

That function is the ordered spine; each block it used to hold is a function
here with its inputs and its result stated, so every step can be read and
tested on its own. The decisions, the records and their order are the ones the
unsplit function made (``tests/decisions`` pins them call by call).

Every name the wall resolves in ``enforce``'s own module (its private helpers,
``classify_tool``, ``enforce``, ``Ceiling``) is read through ``_wall`` at call
time, never imported: a test that patches ``enforce._record_decision`` and the
decision recorder (which wraps those names in ``enforce``'s globals) must reach
the object the wall calls. Shared modules and singletons are the same objects
from any importer, so they are imported directly.
"""

from __future__ import annotations

import copy
import dataclasses
import logging
from typing import TYPE_CHECKING, Any, TypedDict

from shared import (
    act_broker,
    matter_gate,
    read_volume,
    search_egress,
    send_anchor,
    spec_gate,
    turn_principal,
)
from shared.action_classes import ActionClass, BannedToolError, ToolClassification
from shared.inbound import SESSION_TAINT, TRUST_CLASS_INTERNAL, TRUST_CLASS_WEB_DERIVED
from shared.pending_acts import PENDING_ACTS
from shared.pending_send import PENDING_SEND
from shared.trust_decision import ACTION_CLASS_BANNED

from . import enforce as _wall
from . import send_as, voice_gate

if TYPE_CHECKING:
    from .enforce import EnforcementDecision

#: The wall's log lines stay on ``enforce``'s logger.
logger = logging.getLogger(__name__.rpartition(".")[0] + ".enforce")

#: Every send class that can resolve at the confirm ceiling: all of them capture
#: and replay identically (ADR 0071 #1806, ADR 0075).
_SEND_CLASSES = (
    ActionClass.EXTERNAL_SEND,
    ActionClass.EXTERNAL_SEND_INTERNAL,
    ActionClass.EXTERNAL_SEND_CLIENT,
    ActionClass.EXTERNAL_SEND_VENDOR,
)

#: The send classes that LEAVE the firm. EXTERNAL_SEND_INTERNAL is absent by
#: design: an internal alert carries matter context to a colleague on purpose
#: (ADR 0072), and is never content-floored for the same reason.
_LEAVING_CLASSES = (
    ActionClass.EXTERNAL_SEND,
    ActionClass.EXTERNAL_SEND_CLIENT,
    ActionClass.EXTERNAL_SEND_VENDOR,
)


class Call(TypedDict):
    """What the fail-closed zone resolved about this call, before ``enforce()``."""

    exposure: Any
    vertical_floors: Any
    session_taint: str
    web_admin_send: bool
    is_send_as: bool
    effective_action: ActionClass
    gate_trust_class: str
    is_send: bool
    send_recips: set[str]
    approved_replay: bool
    is_commitment: bool
    confirmed_act: Any


# ---------------------------------------------------------------------------
# 1. Banned tools, and the fences no authored ceiling can open
# ---------------------------------------------------------------------------


def classify_or_refuse_banned(
    tool_name: str, tool_call_id: str, session_id: str, session_match: str
) -> ToolClassification | dict:
    """The tool's classification, or the block for a banned tool (recorded)."""
    try:
        return _wall.classify_tool(tool_name)
    except BannedToolError as err:
        # The table is enforce's; imported here, at call time, because
        # enforce imports this module while it is still loading.
        from .enforce import _BANNED_REFUSAL_MESSAGE

        message = _BANNED_REFUSAL_MESSAGE.get(err.tool_name, err.reason)
        banned_persona = _wall._resolve_active_persona()
        logger.info(
            "trust-decision tool=%s persona=%s action_class=banned decision=refuse reason=%s",
            tool_name,
            banned_persona or "(none)",
            message,
        )
        # The audit plugin's own banned path at post_tool_call should carry the
        # refusal that was actually made here.
        _wall._record_decision(
            tool_call_id,
            tool_name,
            banned_persona,
            action_class=ACTION_CLASS_BANNED,
            audit_action="refuse",
            allowed=False,
            reason=message,
            effective_ceiling=_wall.Ceiling.REFUSED,
            session_id=session_id,
            session_match=session_match,
        )
        return {"action": "block", "message": f"Refused: {message}"}


def mixing_fence(
    tool_name: str, args: dict, tool_call_id: str, session_id: str, session_match: str
) -> dict | None:
    """The matter-mixing fence (ss#2167): two clients' facts never meet in one
    composition. Ahead of exposure because no ceiling a firm authors may permit
    it, and at READ time because by send time the mixed draft already exists."""
    mixing_refusal = matter_gate.content_read_refusal(session_id, tool_name, args or {})
    if mixing_refusal is not None:
        _wall._record_decision(
            tool_call_id,
            tool_name,
            _wall._resolve_active_persona(),
            action_class=ActionClass.READ.value,
            audit_action="refuse",
            allowed=False,
            reason=f"MATTER_MIXING_FENCE: {mixing_refusal}",
            effective_ceiling=_wall.Ceiling.REFUSED,
            session_id=session_id,
            session_match=session_match,
        )
        return {"action": "block", "message": f"Refused: {mixing_refusal}"}
    return None


def search_egress_fence(
    tool_name: str, args: dict, tool_call_id: str, session_id: str, session_match: str
) -> dict | None:
    """Search egress (ADR 0099): a web query leaves the seat when it runs. The
    reason names kinds, never values; the row gets the length and verdict only."""
    if tool_name in search_egress.SEARCH_TOOLS:
        egress = search_egress.evaluate(session_id, tool_name, args or {})
        search_egress.record_verdict(tool_call_id, egress)
        if egress.refused:
            _wall._record_decision(
                tool_call_id,
                tool_name,
                _wall._resolve_active_persona(),
                action_class=ActionClass.READ.value,
                audit_action="refuse",
                allowed=False,
                reason=f"SEARCH_EGRESS ({egress.verdict}): {egress.reason}",
                effective_ceiling=_wall.Ceiling.REFUSED,
                session_id=session_id,
                session_match=session_match,
            )
            return {"action": "block", "message": f"Refused: {egress.reason}"}
    return None


def read_volume_fence(
    tool_name: str, tool_call_id: str, session_id: str, session_match: str
) -> dict | None:
    """The read-volume gate (agreement §2.8): the authored threshold is a
    commercial term no ceiling overrides. Report mode records the crossing once
    as an allowed row and never blocks (the register carries that report onto
    this call's later records, ``shared.trust_decision``)."""
    volume_verdict = read_volume.evaluate_read(session_id, tool_name)
    if volume_verdict.report is not None:
        _wall._record_decision(
            tool_call_id,
            tool_name,
            _wall._resolve_active_persona(),
            action_class=ActionClass.READ.value,
            audit_action="report",
            allowed=True,
            reason=f"READ_VOLUME_GATE: {volume_verdict.report}",
            session_id=session_id,
            session_match=session_match,
        )
    elif volume_verdict.refusal is not None:
        _wall._record_decision(
            tool_call_id,
            tool_name,
            _wall._resolve_active_persona(),
            action_class=ActionClass.READ.value,
            audit_action="refuse",
            allowed=False,
            reason=f"READ_VOLUME_GATE: {volume_verdict.refusal}",
            effective_ceiling=_wall.Ceiling.REFUSED,
            session_id=session_id,
            session_match=session_match,
        )
        return {"action": "block", "message": f"Refused: {volume_verdict.refusal}"}
    return None


# ---------------------------------------------------------------------------
# 2. Persona and exposure (inside the fail-closed zone)
# ---------------------------------------------------------------------------


def _turn_taint(tool_name: str, args: dict, session_id: str) -> tuple[str, bool]:
    """The turn's trust class, and whether this is an admin-only send on a
    web_derived turn (ADR 0099: its recipients are bounded by trusted config)."""
    session_taint = SESSION_TAINT.trust_class(session_id)
    web_admin_send = session_taint == TRUST_CLASS_WEB_DERIVED and _wall._web_derived_admin_send(
        tool_name, args or {}, session_id
    )
    return session_taint, web_admin_send


def _effective_action(
    tool_name: str,
    args: dict,
    session_id: str,
    classification: ToolClassification,
    tainted: bool,
) -> tuple[bool, ActionClass]:
    """Whether the call is a staff send-as, and the class the ceiling decides.

    A send carrying ``from`` is its own class (ss ADR 0089), decided before the
    recipient axis: a proposal to a named staff member, never a send class, so
    never taint-gated, captured or replayed. Otherwise the recipient axis
    reclassifies a send to a rostered internal recipient."""
    if send_as.requested_from(tool_name, args) is not None:
        return True, ActionClass.EXTERNAL_SEND_AS_STAFF
    return False, _wall._reclassify_send(
        tool_name, args or {}, classification.action_class, session_id, tainted=tainted
    )


def _replay_approved_send(
    tool_name: str, args: dict, session_id: str, effective_action: ActionClass
) -> tuple[bool, set[str], bool]:
    """The confirm round trip (ADR 0071 #1806): a current-turn approval of a
    held send replays the STORED payload over the live args before enforce and
    every scan, so what ships is what was reviewed. The MATCHED record, never
    the newest (one pending per requester binding). Consumed once it clears."""
    is_send = effective_action in _SEND_CLASSES
    send_recips = _wall._resolve_send_recipients(tool_name, args, session_id) if is_send else set()
    approved_replay = False
    stored = PENDING_SEND.approved_match(tool_name, send_recips) if send_recips else None
    if stored is not None:
        if isinstance(args, dict):
            args.clear()
            args.update(copy.deepcopy(stored.args))
        approved_replay = True
    return is_send, send_recips, approved_replay


def _replay_confirmed_act(
    tool_name: str,
    args: dict,
    session_id: str,
    effective_action: ActionClass,
    approved_replay: bool,
) -> tuple[bool, Any, bool]:
    """The commitment half of the same round trip: the payload the administrator
    READ executes. "Commitment" is every COMMITMENT plus a DESTRUCTIVE tool with
    a call-payload act shape. The two authored names are stripped for the tool
    and kept on the record."""
    is_commitment = effective_action is ActionClass.COMMITMENT or (
        effective_action is ActionClass.DESTRUCTIVE and act_broker.is_call_payload_act(tool_name)
    )
    confirmed_act = PENDING_ACTS.peek_confirmed(session_id, tool_name) if is_commitment else None
    if confirmed_act is not None and isinstance(args, dict):
        args.clear()
        args.update(copy.deepcopy(act_broker.tool_arguments(tool_name, confirmed_act.payload)))
        approved_replay = True
    return is_commitment, confirmed_act, approved_replay


def resolve_call(
    tool_name: str,
    args: dict,
    session_id: str,
    persona_slug: str,
    classification: ToolClassification,
    code_fixed_recipients: bool,
) -> Call:
    """Exposure, floors, taint, the effective class and both replays. May raise:
    the caller's fail-closed zone decides what a raise means."""
    exposure = _wall._resolve_persona_exposure(persona_slug)
    vertical_floors = _wall._resolve_vertical_floors()
    session_taint, web_admin_send = _turn_taint(tool_name, args, session_id)
    is_send_as, effective_action = _effective_action(
        tool_name,
        args,
        session_id,
        classification,
        # A code-fixed recipient did not come from tainted content, so its
        # provenance is not tainted even when the turn is.
        session_taint != TRUST_CLASS_INTERNAL and not code_fixed_recipients and not web_admin_send,
    )
    gate_trust_class = _wall._taint_gate_input(
        session_taint, effective_action, code_fixed_recipients
    )
    is_send, send_recips, approved_replay = _replay_approved_send(
        tool_name, args, session_id, effective_action
    )
    is_commitment, confirmed_act, approved_replay = _replay_confirmed_act(
        tool_name, args, session_id, effective_action, approved_replay
    )
    return {
        "exposure": exposure,
        "vertical_floors": vertical_floors,
        "session_taint": session_taint,
        "web_admin_send": web_admin_send,
        "is_send_as": is_send_as,
        "effective_action": effective_action,
        "gate_trust_class": gate_trust_class,
        "is_send": is_send,
        "send_recips": send_recips,
        "approved_replay": approved_replay,
        "is_commitment": is_commitment,
        "confirmed_act": confirmed_act,
    }


def decide(tool_name: str, args: dict, persona_slug: str, call: Call) -> EnforcementDecision:
    """``enforce()`` on the resolved call, marked when the taint gate read a
    code-fixed exemption rather than the turn's class (Captain, 2026-09-28)."""
    decision = _wall.enforce(
        action=call["effective_action"],
        exposure=call["exposure"],
        tool_name=tool_name,
        persona_slug=persona_slug,
        current_turn_approval=call["approved_replay"] or _wall._resolve_current_turn_approval(args),
        vertical_floors=call["vertical_floors"],
        inbound_trust_class=call["gate_trust_class"],
        admin_recipients=call["web_admin_send"],
    )
    if call["gate_trust_class"] != call["session_taint"]:
        decision = dataclasses.replace(
            decision,
            reason=(
                f"{decision.reason} | TAINT_EXEMPT: {_wall.TAINT_EXEMPT_CODE_FIXED} "
                f"(turn trust_class={call['session_taint']}; recipients fixed by the pre_run "
                "envelope, Captain decision 2026-09-28)"
            ),
        )
    return decision


def fail_closed(
    tool_name: str,
    tool_call_id: str,
    session_id: str,
    session_match: str,
    persona_slug: str,
    classification: ToolClassification,
) -> dict | None:
    """What a raise in the zone means (issue #12): a READ is allowed (no blast
    radius, and a config error must not brick read tooling), recorded with no
    ceiling because none was decided; every other class refuses. Called from
    inside the ``except``, so the log lines carry the exception."""
    if classification.action_class == ActionClass.READ:
        logger.warning(
            "trust: exposure resolution failed for READ tool %r; allowing "
            "(read is low-risk and always permitted)",
            tool_name,
            exc_info=True,
        )
        _wall._record_decision(
            tool_call_id,
            tool_name,
            persona_slug,
            action_class=ActionClass.READ.value,
            audit_action="allow",
            allowed=True,
            reason="read allowed despite an indeterminate exposure resolution",
            session_id=session_id,
            session_match=session_match,
        )
        return None
    logger.exception(
        "trust: exposure resolution failed for sensitive tool %r (action=%s); "
        "FAILING CLOSED — refusing the call",
        tool_name,
        classification.action_class,
    )
    _wall._record_decision(
        tool_call_id,
        tool_name,
        persona_slug,
        action_class=classification.action_class.value,
        audit_action="refuse",
        allowed=False,
        reason="trust decision unavailable; failing closed",
        session_id=session_id,
        session_match=session_match,
    )
    return {
        "action": "block",
        "message": (
            f"Refused: trust decision unavailable for this "
            f"{classification.action_class} action; failing closed"
        ),
    }


# ---------------------------------------------------------------------------
# 3. The record, then the dispositions
# ---------------------------------------------------------------------------


def record_ceiling(
    tool_name: str,
    tool_call_id: str,
    session_id: str,
    session_match: str,
    persona_slug: str,
    decision: EnforcementDecision,
) -> None:
    """The log line and the row (#2122). ``decision.action_class`` is the
    RESOLVED class, so the typed send classes reach the ledger."""
    _wall._audit_decision(tool_name, persona_slug, decision)
    _wall._record_decision(
        tool_call_id,
        tool_name,
        persona_slug,
        action_class=decision.action_class.value,
        audit_action=decision.audit_action,
        allowed=decision.allowed,
        reason=decision.reason,
        authored_ceiling=decision.authored_ceiling,
        vertical_floor=decision.vertical_floor,
        effective_ceiling=decision.effective_ceiling,
        session_id=session_id,
        session_match=session_match,
    )


def capture_held_send(
    tool_name: str, args: dict, session_id: str, call: Call, decision: EnforcementDecision
) -> None:
    """A send withheld at confirm is captured with the anchor and lane it was
    held under, bound to the person and chat that raised it (only they approve
    it), and to this session (the scans at approval judge what it read)."""
    if call["is_send"] and call["send_recips"] and decision.audit_action == "await_approval":
        held_anchor, held_lane = send_anchor.capture_context(session_id)
        PENDING_SEND.capture(
            tool_name,
            args,
            call["send_recips"],
            anchor=held_anchor,
            lane=held_lane,
            requester=turn_principal.requester(),
            session_id=session_id,
        )


def proposal_exit(
    tool_name: str,
    args: dict,
    session_id: str,
    tool_call_id: str,
    call: Call,
    decision: EnforcementDecision,
) -> tuple[bool, dict | None]:
    """``(True, verdict)`` when the call ends as a proposal, else ``(False, None)``.

    Staff send-as (ss ADR 0089) always ends here: refused, or proposed to the
    named staff member (the approval IS the human review, so no content floor).
    A commitment withheld at confirm, not yet confirmed, is put to an
    administrator, after its row is written."""
    if call["is_send_as"]:
        if decision.audit_action != "await_approval":
            return True, {"action": "block", "message": f"Refused: {decision.reason}"}
        return True, send_as.propose(tool_name, args, session_id, tool_call_id)
    if (
        call["is_commitment"]
        and call["confirmed_act"] is None
        and decision.audit_action == "await_approval"
    ):
        return True, _wall._propose_commitment_act(tool_name, session_id, args)
    return False, None


def _annotate_matter(
    tool_name: str,
    tool_call_id: str,
    session_id: str,
    session_match: str,
    persona_slug: str,
    decision: EnforcementDecision,
    fragments: list[str],
) -> None:
    """ONE re-record carrying every fragment: the register replaces by
    tool_call_id, so two records would erase the first. Re-recorded, never
    rebuilt, so the ceiling fields stay (#2122)."""
    if fragments:
        _wall._record_decision(
            tool_call_id,
            tool_name,
            persona_slug,
            action_class=decision.action_class.value,
            audit_action=decision.audit_action,
            allowed=decision.allowed,
            reason=" | ".join([decision.reason, *fragments]),
            authored_ceiling=decision.authored_ceiling,
            vertical_floor=decision.vertical_floor,
            effective_ceiling=decision.effective_ceiling,
            session_id=session_id,
            session_match=session_match,
        )


def matter_identity(
    tool_name: str,
    args: dict,
    session_id: str,
    tool_call_id: str,
    session_match: str,
    persona_slug: str,
    call: Call,
    decision: EnforcementDecision,
) -> dict | None:
    """Outbound matter identity and matter mixing (ss#2167), for sends that
    leave the firm. OUTSIDE the ``allowed`` guard: at draft_for_review every
    send is disallowed, so a check inside it would never run where it matters.
    Neither side is the model's word: the matters are the ones in the body, and
    membership comes from connector reads. An allowed send that might mix is
    routed to a human draft, never refused; a withheld one is annotated."""
    if not (call["is_send"] and call["effective_action"] in _LEAVING_CLASSES):
        return None
    matter_verdict = matter_gate.evaluate(
        session_id=session_id,
        body=matter_gate.body_from_args(args),
        recipients=call["send_recips"],
        # A records vendor is not a party to the matter it is written about.
        recipient_is_exempt=call["effective_action"] is ActionClass.EXTERNAL_SEND_VENDOR,
    )
    mismatch_withholds = matter_verdict.should_withhold and matter_gate.mode() == "block"
    if mismatch_withholds and decision.allowed:
        return {
            "action": "block",
            "message": (
                "Refused: this message cites "
                f"{', '.join(matter_verdict.matters) or 'a matter'} but "
                f"{matter_verdict.reason}; routing to draft for human review "
                "(ss#2167 matter identity)"
            ),
        }
    multi_read = matter_gate.multi_matter_session(session_id)
    if multi_read and decision.allowed and matter_gate.multi_matter_mode() == "block":
        return {
            "action": "block",
            "message": (
                "Refused: this session read content from "
                f"{len(multi_read)} matters ({', '.join(multi_read)}) before "
                "composing this send; routing to draft for human review "
                "(ss#2167 matter mixing)"
            ),
        }
    fragments: list[str] = []
    if mismatch_withholds:
        fragments.append(f"MATTER_MISMATCH: {matter_verdict.reason}")
    if multi_read:
        fragments.append(
            f"MULTI_MATTER_SESSION: session read content from {len(multi_read)} "
            f"matters ({', '.join(multi_read)})"
        )
    _annotate_matter(
        tool_name, tool_call_id, session_id, session_match, persona_slug, decision, fragments
    )
    return None


def structure_floor(
    tool_name: str,
    args: dict,
    session_id: str,
    tool_call_id: str,
    call: Call,
    decision: EnforcementDecision,
) -> dict | None:
    """The message structure floor (ss#2090), every send class including the
    internal one (the staff digest is what it exists for). The check runs and
    writes its row either way; only its disposition reads ``allowed``."""
    if call["is_send"]:
        return spec_gate.check_structure_floor(
            tool_name=tool_name,
            action_class_value=getattr(call["effective_action"], "value", ""),
            session_id=session_id,
            tool_call_id=tool_call_id,
            body=_wall._extract_send_body(args),
            allowed=decision.allowed,
        )
    return None


def content_floor_and_voice(
    tool_name: str, args: dict, session_id: str, call: Call, decision: EnforcementDecision
) -> dict | None:
    """An allowed send that leaves the firm: the content-sensitivity floor
    (ADR 0031), then, when autonomous, the voice live-gate (ADR 0028 §2)."""
    if decision.allowed and call["effective_action"] in _LEAVING_CLASSES:
        floor_block = _wall._apply_content_floor(tool_name, args)
        if floor_block is not None:
            return floor_block
        if decision.effective_ceiling == _wall.Ceiling.AUTONOMOUS:
            return voice_gate.check_voice_gate(
                tool_name=tool_name,
                action_class_value=getattr(call["effective_action"], "value", ""),
                session_id=session_id,
            )
    return None


def authored_spec(
    tool_name: str, args: dict, session_id: str, call: Call, decision: EnforcementDecision
) -> dict | None:
    """The authored-spec gate (ss ADR 0083, #2084) on an allowed autonomous
    call; it fires only where the seat declares a voice spec. The body passes
    UNCOERCED (None is indeterminate, ss-console #2234); ``templated`` is true
    only for a body the overlay templated (ss-console#2546)."""
    if decision.allowed and decision.effective_ceiling == _wall.Ceiling.AUTONOMOUS:
        return spec_gate.check_spec_gate(
            tool_name=tool_name,
            action_class_value=getattr(call["effective_action"], "value", ""),
            session_id=session_id,
            body=_wall._extract_send_body(args),
            templated=bool(args.get(spec_gate.TEMPLATED_BODY_ARG)),
        )
    return None


def release(
    tool_name: str, session_id: str, call: Call, decision: EnforcementDecision
) -> dict | None:
    """The last disposition: an allowed call proceeds, spending a replayed
    send's or a confirmed act's single-use approval now that it cleared every
    gate (the act's record survives for post_tool_call); anything else refuses."""
    if decision.allowed:
        if call["is_send"] and call["approved_replay"]:
            replayed = PENDING_SEND.take_for_send(tool_name, call["send_recips"])
            if replayed is not None:
                send_anchor.note_replay(replayed.anchor, replayed.lane, replayed.recipients)
        if call["is_commitment"] and call["confirmed_act"] is not None:
            PENDING_ACTS.take_in_flight(session_id, tool_name)
        return None
    return {"action": "block", "message": f"Refused: {decision.reason}"}
