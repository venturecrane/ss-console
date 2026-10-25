"""Cross-language capability-name contract (Python mirror).

The canonical CapabilityName union lives in
``src/lib/operator/capabilities/types.ts``; this frozenset is its Python
mirror, kept in lockstep by the google connector conformance test. When one
side changes, the other MUST change in the same PR.

History: this constant originally lived in ``connectors/filevine/errors.py``.
The filevine / no_pm / lawpay adapter packages were deleted on Captain's
2026-06-12 code-review decision (ADR 0020 went MCP-first; the build:/
synthetic: adapters had zero runtime wiring — nothing materializes them into
tools — and their smoke harness referenced a script that never existed). The
contract constant is the one piece the live google suite still needed.
"""

from __future__ import annotations

CAPABILITY_NAMES: frozenset[str] = frozenset(
    {
        "PracticeManagement",
        "Email",
        "Calendar",
        "DocumentStorage",
        "ESign",
        "CourtAccess",
        "Payments",
        "Accounting",
        "IntakeCRM",
        "CallTracking",
        "InternalComms",
        "WebSearch",
        "Website",
        "VideoChannel",
        "Analytics",
    }
)

__all__ = ["CAPABILITY_NAMES"]
