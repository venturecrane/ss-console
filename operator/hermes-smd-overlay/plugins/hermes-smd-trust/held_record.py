"""The record of a call the trust wall held AFTER its ceiling decision.

``enforce._evaluate_tool_call`` records the ceiling's decision (step 5), then
runs the gates the ceiling cannot see past: the matter gate, the multi-matter
fence, the structure floor, the content floor, the voice gate and the spec gate.
When one of them blocks a call the ceiling allowed, the register still held the
ceiling's allow for that ``tool_call_id``, and the wall returned without
recording again (the decision corpus's ``blocked-after-allow-decision``).

That reached the ledger. Hermes answers a plugin block by firing
``post_tool_call`` with ``status="blocked"`` (``model_tools.handle_function_call``
at the sha ``operator/templates/Dockerfile`` pins), the audit plugin writes a
``TOOL_CALL_COMPLETED`` row for it, and the row took the register's allow:
``trust_decision=allow``, ``trust_allowed=true``, reason "permitted", on a send
the wall refused (``tests/test_blocked_after_allow_row.py``).

So the held call is recorded again, through the same ``_record_decision``, as
refused for the gate's own words, with the ceiling fields kept (#2122: what the
seat authorized is still a fact about the call). A read-volume report recorded
earlier for the call rides onto it as it rides onto every later record.

A module of its own, not a step in ``evaluate_steps``, for two measured reasons:
that module sits at the logical-line ceiling, and the decision corpus's census
(``tests/decisions/census.py``) reads a helper the spine reaches twice as
recursion, so this cannot reuse ``evaluate_steps.record_ceiling``.
``_record_decision`` is read through ``_wall`` at call time, as in
``evaluate_steps``, so a patch on ``enforce`` reaches it.

The pre-hook (``on_pre_tool_call``) holds calls of its own after the wall
allowed them: the outbound draft scan, the outbound send scan, the rendered-body
gate, and its fail-closed catch-all. It has no ``EnforcementDecision`` in hand,
so ``refused_after_wall`` supersedes the register's record for the call with a
refusal that keeps its ceiling fields and session resolution, and
``catch_all_block`` does the same with a reason that names the catch-all.
Either way the register keeps the `` | ``-joined notes the superseded record
carried (``TrustDecisionRegister.record``), such as the matter gate's report-mode
``MULTI_MATTER_SESSION`` note.
A fault before the wall decided anything leaves no record to supersede; the
catch-all then records a fresh refusal, so the row says refuse, not none
(``tests/test_pre_hook_blocks_recorded.py``).
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from shared.action_classes import classify_tool
from shared.trust_decision import TRUST_DECISIONS, TrustDecision

from . import enforce as _wall

logger = logging.getLogger(__name__)

#: The reason prefix of a refusal the pre-hook's fail-closed catch-all recorded.
CATCH_ALL = "CATCH_ALL: "

if TYPE_CHECKING:
    from .enforce import EnforcementDecision


def record_refused(
    tool_name: str,
    tool_call_id: str,
    session_id: str,
    session_match: str,
    persona_slug: str,
    decision: EnforcementDecision,
    held: dict,
) -> dict:
    """Record ``held`` as the wall's refusal when the ceiling had allowed the
    call, and return it. A call the ceiling already withheld keeps its own
    record: its row says draft or await_approval, which is what was decided."""
    if decision.allowed:
        _wall._record_decision(
            tool_call_id,
            tool_name,
            persona_slug,
            action_class=decision.action_class.value,
            audit_action="refuse",
            allowed=False,
            reason=held["message"],
            authored_ceiling=decision.authored_ceiling,
            vertical_floor=decision.vertical_floor,
            effective_ceiling=decision.effective_ceiling,
            session_id=session_id,
            session_match=session_match,
        )
    return held


def refused_after_wall(hook_kwargs: dict, held: dict) -> dict:
    """Record ``held``, a pre-hook block on a call the wall allowed, as the
    call's refusal, and return it."""
    _supersede(hook_kwargs, held["message"])
    return held


def catch_all_block(hook_kwargs: dict, exc: BaseException) -> dict:
    """The pre-hook catch-all's fail-closed block for ``exc``, recorded as the
    call's refusal under a reason that names the catch-all.

    NAME THE ACTUAL FAULT. This is the catch-all around the WHOLE hook, roughly
    a hundred lines spanning session resolution, the ceiling resolver, the
    content floor, the outbound scans, the workspace broker and the audit
    transport. It said "trust-ceiling evaluation failed" for every one of them,
    and the real exception went only to the log.

    That wording had a measured cost. On 2026-07-31 it sent an agent to a
    production-severity bug report against a WORKING security control: the
    broker had correctly refused a caller that was not the gateway process, and
    the message named a subsystem that was never involved (ss#2103,
    vfy_01KYX1SHS2ZDCNNB6KR3PNSYQY).

    The exception TYPE and its origin are safe to surface: they name machinery,
    not payload. The exception's message is deliberately excluded, since it can
    carry recipient or content fragments from whatever raised.
    """
    origin = "unknown"
    tb = exc.__traceback__
    while tb is not None:  # innermost frame, where it actually raised
        origin = f"{Path(tb.tb_frame.f_code.co_filename).name}:{tb.tb_lineno}"
        tb = tb.tb_next
    held = {
        "action": "block",
        "message": (
            "Refused: the trust hook raised and failed closed — "
            f"{type(exc).__name__} at {origin}. This is an indeterminate "
            "decision blocked for safety, NOT necessarily a ceiling or "
            "entitlement problem; see the seat log for the traceback."
        ),
    }
    _supersede(hook_kwargs, f"{CATCH_ALL}{held['message']}")
    return held


def _supersede(hook_kwargs: dict, reason: str) -> None:
    """Record a refusal over whatever the register holds for this call.

    Best-effort and never raises, like ``_record_decision``: the block stands
    whatever the ledger handoff does."""
    try:
        tool_name = str(hook_kwargs.get("tool_name") or "")
        tool_call_id = str(hook_kwargs.get("tool_call_id") or "")
        prior = TRUST_DECISIONS.peek(tool_call_id, tool_name)
        if prior is None:
            prior = TrustDecision(
                action_class=_action_class(tool_name),
                audit_action="refuse",
                allowed=False,
                persona=_wall._resolve_active_persona(),
                session_resolved=str(hook_kwargs.get("session_id") or ""),
            )
        refused = replace(prior, audit_action="refuse", allowed=False, reason=reason)
        TRUST_DECISIONS.record(tool_call_id, tool_name, refused)
    except Exception:  # noqa: BLE001 — the ledger handoff must never break the path
        logger.debug("trust: pre-hook refusal handoff to the audit row failed", exc_info=True)


def _action_class(tool_name: str) -> str:
    """The tool's coarse class for a refusal the wall never classified; ``""``
    when the name cannot be classified (empty, or banned by name)."""
    try:
        return classify_tool(tool_name).action_class.value
    except Exception:  # noqa: BLE001 — a refusal must still be recorded
        return ""
