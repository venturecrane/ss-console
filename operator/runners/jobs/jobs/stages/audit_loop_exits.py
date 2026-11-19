"""The per-claim audit loop's exits (``stages/audit_loop.py``), as the update
lane's ``audit`` stage reads them (``medchron/dag.AUDIT_LOOP``). Since
2026-11-18 the full chronology's ``audit`` is the entry read
(``stages/entry_audit.py``); these are unchanged from the row both lanes
shared before. Exit 1's prefix is the one the broker resumes a hold by
(workspace_broker ``AUDIT_GATE_HOLD_PREFIX``)."""

from __future__ import annotations

EXITS = {
    1: ("held", "audit coverage: a live claim is not finally SUPPORTED"),
    2: ("failed", "audit INVALID: a control was wrongly SUPPORTED, no exhibit bytes, or a repair did not reconcile"),
    3: ("failed", "audit refused at the double-sweep guard: prior rows for this body carry keys no longer produced"),
}
