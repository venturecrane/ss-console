"""Safety-substrate runtime invariants - modular enforcement points.

Each module in this package implements one runtime invariant from platform
PRD §7.5. The base 5 invariants (no destructive without approval, no external
send, no autonomous commitment, sticky stop, code-enforced trust ceilings) are
exercised by the per-fixture tests under `safety_substrate/tests/` and routed
through the trust-ceiling adapter and the sticky-stop machine. This package
holds the remaining two:

- ``invariant_6``: citation enforcement for client-facing fact-bearing fields.
  Every fact in a skill's output that is rendered into a client-facing field
  must be cited to a specific source (matter document, memory rule, system
  of record, or a verbatim quote from the source). Output containing an
  unsourced fact in such a field blocks emission with a structured error.

  This is distinct from ``safety_substrate/citation_filter.py``. That module
  detects FABRICATED legal citations (case names, reporter cites, statutory
  references) in any law-vertical output and refuses on detection - it is
  the law-firm-vertical implementation of invariant #6's refusal semantics.
  The module here is the platform-universal complement: it enforces source
  attribution on fields the skill declares as fact-bearing. Both layers
  ship together; either alone is one-layer enforcement.

- ``invariant_7``: cross-Machine query prohibition. At Hermes Machine boot,
  the runtime verifies that every storage binding (D1, R2, Vectorize, KV)
  resolves to a name scoped by the Machine's own customer slug. If any
  binding resolves to a name that does not start with the expected
  ``hermes-{slug}-`` prefix, the runtime refuses to start. This is the
  architectural enforcement of the customer-isolation promise.

Both modules emit to the audit log via :class:`adapter.audit_log.AuditLogWriter`.
Action types are drawn from the existing closed set
:data:`adapter.audit_log.ACCEPTED_ACTION_TYPES`. See
``docs/specs/operator/safety-invariants.md`` for the spec.
"""

from .invariant_6 import (
    Citation,
    CitationViolation,
    CitationViolations,
    SourceKind,
    SourceRegistry,
    enforce_citations,
)
from .invariant_7 import (
    BindingMismatch,
    BindingSnapshot,
    Invariant7Violation,
    verify_storage_bindings,
)

__all__ = [
    "BindingMismatch",
    "BindingSnapshot",
    "Citation",
    "CitationViolation",
    "CitationViolations",
    "Invariant7Violation",
    "SourceKind",
    "SourceRegistry",
    "enforce_citations",
    "verify_storage_bindings",
]
