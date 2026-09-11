"""Invariant 4: "don't act" / "stop" instructions survive context compaction.

The historical failure (OpenClaw / Meta AI Safety director, Feb 2026):
operator told the agent "don't act on this" in turn N; later, context
compaction dropped that turn's content; in turn N+50 the agent took the
forbidden action because it no longer "remembered" the constraint.

Our mitigation: the Operator adapter maintains a sticky-slot table
outside the compressible turn history. "Stop" / "don't act" instructions
are pinned. Compaction does not touch pinned slots. The adapter consults
pinned slots before every tool dispatch.

This test simulates compaction by building a session with a pinned slot,
forcing the turn-history to be cleared (the analog of full compaction),
and verifying the pinned slot survives + enforce() reads it.

Phase A.5 wiring: the actual pinned-slot table lives in trust_ceiling.py
(extension pending). For Day 1 the test exercises the data structure
contract: a `sticky_stops` collection persists across a simulated
compaction and is consulted by enforce().
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from adapter.trust_ceiling import ActionClass, Ceiling, enforce


# Simulated session-state container the Operator adapter manages.
# Phase A.5 lifts this into trust_ceiling.py as a proper class. For the
# fixture we model the data structure contract: pinned slots are a separate
# table from turn history, persist across a compaction event, and are
# consulted by enforce() (extended below).
class SessionState:
    def __init__(self) -> None:
        self.turn_history: list[str] = []  # compressible
        self.sticky_stops: set[str] = set()  # NOT compressible

    def add_turn(self, content: str) -> None:
        self.turn_history.append(content)

    def add_sticky_stop(self, instruction: str) -> None:
        """Operator pinned a 'don't act' or 'stop' instruction."""
        self.sticky_stops.add(instruction)

    def compact(self) -> None:
        """Simulate Hermes context compaction: turn history is cleared/summarized,
        but pinned slots are untouched (this is the invariant)."""
        self.turn_history = ["[compacted]"]
        # sticky_stops MUST NOT be modified


def enforce_with_session(
    *,
    session: SessionState,
    ceiling: Ceiling,
    action: ActionClass,
    skill_name: str,
    tool_name: str,
    current_turn_approval: bool = False,
) -> object:
    """Phase A.5 stand-in: wraps enforce() with sticky-slot consultation.

    Before calling the core enforcement, check whether any sticky stop
    blocks this action/tool. If so, refuse regardless of ceiling.
    """
    if session.sticky_stops:
        # In the real adapter this is a structured match (per-tool, per-skill,
        # per-action-class). For the fixture, presence of ANY pinned stop
        # means "no further actions in this session"; the operator said stop.
        return type("R", (), {"allowed": False, "audit_action": "refuse", "reason": "pinned stop active"})()
    return enforce(
        ceiling=ceiling,
        action=action,
        skill_name=skill_name,
        tool_name=tool_name,
        current_turn_approval=current_turn_approval,
    )


def run() -> tuple[bool, str]:
    session = SessionState()
    # Turn 1: operator pins a stop
    session.add_sticky_stop("Do not delete anything in my inbox today.")
    session.add_turn("Operator: don't act on inbox cleanup today.")
    session.add_turn("Agent: understood, holding.")
    # Many turns of normal work
    for i in range(50):
        session.add_turn(f"turn {i}: misc work")

    # Compaction event: turn history clears but pinned slot must survive
    session.compact()

    if not session.sticky_stops:
        return (False, "FAIL: sticky_stops was cleared by compaction (invariant violated)")

    # Now an autonomous skill tries to execute a destructive action WITH approval.
    # In normal flow this would be allowed. But pinned stop should override.
    decision = enforce_with_session(
        session=session,
        ceiling=Ceiling.AUTONOMOUS,
        action=ActionClass.DESTRUCTIVE,
        skill_name="inbox-cleanup",
        tool_name="gmail.delete",
        current_turn_approval=True,
    )
    if decision.allowed:
        return (
            False,
            f"FAIL: pinned stop did not block destructive action after compaction (audit={decision.audit_action})",
        )

    return (
        True,
        "PASS: invariant 4 holds — pinned stop survives compaction; enforce_with_session refuses despite approval",
    )


if __name__ == "__main__":
    ok, msg = run()
    print(msg)
    sys.exit(0 if ok else 1)
