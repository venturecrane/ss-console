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
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import enforce as _wall

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
