"""Trust-ceiling decision logging (issue #864).

The platform PRD §11 ("Trust Ceiling Model") and §11.4 ("Audit log") say
every action at every ceiling is logged. The `enforce()` call point in
`operator/adapter/trust_ceiling.py` makes the allow / draft_for_review /
refuse call, but nothing emits those decisions as audit rows today. Without
emission, the trust ceiling is invisible to operators and customers: the
Captain dashboard has no aggregate view, the customer dashboard has no
recent-decisions feed, and the compliance-evidence packet (#802) cannot
show enforcement history.

This module is the emission layer. It wraps a trust-ceiling decision with
a synchronous audit_log write. The dispatch path on the Hermes side calls
`log_decision()` once per `enforce()` invocation, on the same code path,
before the action proceeds (for `allow`) or after the substrate has decided
to draft / refuse.

Design rules
------------

* **Closed enum vocabulary.** `Decision` (allow / draft_for_review / refuse)
  and `DecisionReason` are closed enums. The dashboard aggregates on these
  values; free-text reasons would make the aggregate view degrade as new
  variants appear in production. The enum members below are drawn from the
  decision tree in `adapter/trust_ceiling.py::enforce()`. Adding a new
  reason means: extend the enum here, extend the wrapper's reason mapping,
  extend the spec doc, ship in one PR.

* **No PII in audit metadata.** The reason is a closed-enum value. The
  audit row also carries `customer`, `skill`, `action_class`, and
  `ceiling_level`. All opaque identifiers / closed-vocabulary values.
  Substantive payload (the email body the agent wanted to send, etc.)
  is never logged here; that lives in R2 via the audit_log writer's
  payload-digest contract.

* **Synchronous emission.** `log_decision()` returns only after the
  audit_log INSERT lands. Same invariant as the writer itself
  (`audit_log.AuditLogWriter.write`): a state the substrate cannot
  enforce or evidence is a state the agent does not run. If the audit
  write fails, the wrapper propagates `AuditWriteError`; the caller
  (dispatch path) MUST treat that as a fatal: the unloggable decision
  must not produce an action.

* **Reuse closed-set action_type vocabulary.** Per the sticky-stop
  precedent (PR #948) and the audit_log writer's documented protocol,
  this module reuses `ACCEPTED_ACTION_TYPES` rather than extending it.
  Mapping is per-decision:

    - `allow`            -> `DRAFT_CREATED` if action_class is internal_write
                            with a draft routing, otherwise no audit row
                            emitted here (the downstream action's own audit
                            row covers the audit trail).
    - `draft_for_review` -> `DRAFT_CREATED`
    - `refuse`           -> `INVARIANT_VIOLATION`

  The trust-ceiling-decision detail lives in metadata so the dashboard,
  audit viewer (#873), and compliance-evidence packet (#802) can aggregate
  via stable JSON keys.

  NOTE: This is a deliberate scope-respecting choice. The audit_log
  writer's ACCEPTED_ACTION_TYPES constant is on main from PR #942 and
  this PR's strict file scope (issue #864 brief) forbids touching it.
  The sticky-stop module (PR #948) made the same call. The downside:
  the action_type column does not by itself identify a trust-ceiling
  row; readers must filter on `metadata.trust_ceiling_decision == true`.
  The compliance-evidence packet spec already uses that pattern for
  sticky-stop rows; it works the same way here.

* **`allow` decisions and the audit-floor question.** The AC says "every
  `enforce()` invocation emits an event." In practice, an `allow` decision
  means the underlying action proceeds normally and produces its OWN
  downstream audit row (DRAFT_CREATED on draft creation, etc.); that row
  already records the actor, skill, and ceiling at
  action time via the writer's `trust_ceiling` column. To meet the AC
  without duplicating rows, this module records `allow` decisions via the
  same `DRAFT_CREATED` shape with `metadata.decision == "allow"` so the
  dashboard can include allow decisions in its aggregate. The compliance
  view filters on the decision field, not the action_type.

  Wiring detail for the integration point: `log_decision()` is called on
  every `enforce()` invocation including allows; the audit row is emitted
  synchronously; downstream code is free to emit its own action-specific
  audit row in addition. The two rows are linked by `metadata.trace_id`.

Module shape
------------

::

    from operator_safety_substrate.trust_ceiling_log import (
        Decision,
        DecisionReason,
        log_decision,
    )

    # On the Hermes dispatch path, after `enforce()` returns:
    audit_id = await log_decision(
        writer=audit_writer,
        customer="acme",
        skill="inbox-triage",
        action_class="external_send",
        ceiling_level="draft_for_review",
        decision=Decision.DRAFT,
        reason=DecisionReason.DRAFT_ROUTE_SEND,
        skill_version="2.1.0",
        trace_id="trace-01HXY...",
    )

Aggregation query shape (for the dashboard implementer, separate issue)
------------------------------------------------------------------------

The Captain dashboard's "aggregate decisions over time" surface and the
customer dashboard's "recent decisions for your account" surface both
filter on `metadata.trust_ceiling_decision = true` and aggregate by
`metadata.decision`, `metadata.reason`, and `ts` buckets. The full query
shape is in `docs/specs/operator/trust-ceiling-logging.md` §Aggregation.
"""

from __future__ import annotations

import enum
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))  # operator/ on sys.path

from adapter.audit_log import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    ActorRole,
    AuditEvent,
    AuditLogWriter,
)


# ---------------------------------------------------------------------------
# Decision + reason enums
#
# Source of truth: platform-prd.md §11 (trust ceiling model) and the
# decision tree in adapter/trust_ceiling.py::enforce(). The enum members
# below cover every branch in that decision tree. When the dispatch
# integration lands, the mapping from enforce()'s EnforcementDecision
# (allowed, reason_text, audit_action) to the (Decision, DecisionReason)
# pair lives at the call site, NOT here. This module is a pure emitter;
# the caller decides what decision was made and why.
# ---------------------------------------------------------------------------


class Decision(str, enum.Enum):
    """Closed set of trust-ceiling decisions.

    Mirrors `EnforcementDecision.audit_action` in
    `adapter/trust_ceiling.py`. Names match the platform PRD §11
    vocabulary verbatim; the underlying string values match the audit_log
    column convention.
    """

    ALLOW = "allow"
    DRAFT = "draft_for_review"
    REFUSE = "refuse"


class CeilingLevel(str, enum.Enum):
    """The trust ceiling configured for the skill at decision time.

    Values match platform PRD §11.1's three ceilings and the
    `Ceiling` enum in `adapter/trust_ceiling.py`. The PRD names the
    third value `disabled`; the adapter uses `refused` for the same
    semantics (skill does not run). Both spellings are accepted here so
    the dispatch path can pass through whichever name its source uses
    without translation. Aggregations should treat the two as equivalent.
    """

    AUTONOMOUS = "autonomous"
    DRAFT_FOR_REVIEW = "draft_for_review"
    DISABLED = "disabled"
    REFUSED = "refused"  # synonym for DISABLED per adapter convention


class ActionClassName(str, enum.Enum):
    """Action class as declared on the tool the skill is invoking.

    Mirrors `ActionClass` in `adapter/trust_ceiling.py`. Held as a closed
    set here so the dashboard's "decisions by action class" facet stays
    well-defined and so new categories can't enter via free-text drift.
    """

    READ = "read"
    INTERNAL_WRITE = "internal_write"
    EXTERNAL_SEND = "external_send"
    COMMITMENT = "commitment"
    DESTRUCTIVE = "destructive"


class DecisionReason(str, enum.Enum):
    """Closed set of reasons the trust ceiling reached a given decision.

    Each member maps to exactly one branch in the
    `adapter/trust_ceiling.py::enforce()` decision tree. Adding a branch
    means adding a member here; the spec doc and the wrapper test must be
    updated in the same PR.

    Naming convention: ``<SCOPE>_<OUTCOME>``. Scopes correspond to the
    action class or ceiling state that drove the decision; outcomes are
    one of ALLOW / DRAFT / REFUSE matching the `Decision` value.
    """

    # ALLOW reasons --------------------------------------------------------
    READ_ALLOWED = "read_allowed"
    INTERNAL_WRITE_AUTONOMOUS = "internal_write_autonomous"
    EXTERNAL_SEND_AUTONOMOUS_WITH_APPROVAL = "external_send_autonomous_with_approval"
    COMMITMENT_WITH_APPROVAL = "commitment_with_approval"
    DESTRUCTIVE_WITH_APPROVAL = "destructive_with_approval"

    # DRAFT_FOR_REVIEW reasons --------------------------------------------
    INTERNAL_WRITE_DRAFT_ROUTE = "internal_write_draft_route"
    EXTERNAL_SEND_DRAFT_ROUTE = "external_send_draft_route"
    COMMITMENT_REQUIRES_AUTONOMOUS = "commitment_requires_autonomous"

    # REFUSE reasons ------------------------------------------------------
    CEILING_DISABLED = "ceiling_disabled"
    EXTERNAL_SEND_NO_APPROVAL = "external_send_no_approval"
    COMMITMENT_NO_APPROVAL = "commitment_no_approval"
    DESTRUCTIVE_NO_APPROVAL = "destructive_no_approval"
    DESTRUCTIVE_DRAFT_CEILING = "destructive_draft_ceiling"
    UNKNOWN_ACTION_CLASS = "unknown_action_class"


# ---------------------------------------------------------------------------
# Action-type mapping
#
# Per the safety-substrate scope rules (issue brief), this module reuses
# the audit_log writer's closed-set `ACCEPTED_ACTION_TYPES` rather than
# extending it. Mapping is per-Decision:
#
#   - REFUSE -> INVARIANT_VIOLATION (the substrate enforced an invariant)
#   - DRAFT  -> DRAFT_CREATED       (the substrate routed to a draft)
#   - ALLOW  -> DRAFT_CREATED       (audit-floor; metadata.decision = allow)
#
# Readers identify trust-ceiling rows by `metadata.trust_ceiling_decision`
# = true (NOT by action_type alone). This mirrors the sticky-stop convention
# of carrying transition specifics in metadata while reusing existing
# action_types.
# ---------------------------------------------------------------------------


_DECISION_TO_ACTION_TYPE: dict[Decision, str] = {
    Decision.ALLOW: "DRAFT_CREATED",
    Decision.DRAFT: "DRAFT_CREATED",
    Decision.REFUSE: "INVARIANT_VIOLATION",
}


# ---------------------------------------------------------------------------
# Public emission API
# ---------------------------------------------------------------------------


async def log_decision(
    writer: AuditLogWriter,
    *,
    customer: str,
    skill: str,
    action_class: ActionClassName | str,
    ceiling_level: CeilingLevel | str,
    decision: Decision,
    reason: DecisionReason,
    skill_version: Optional[str] = None,
    trace_id: Optional[str] = None,
    matter_ref: Optional[str] = None,
    extra_metadata: Optional[dict] = None,
) -> str:
    """Emit one audit_log row for a trust-ceiling decision.

    Returns the inserted ULID (the audit_log row id). Synchronous: returns
    only after the underlying INSERT has been acknowledged. Caller
    responsibility on `AuditWriteError`: abort the pending action.

    Required:
        writer         - constructed `AuditLogWriter` (one per Machine)
        customer       - customer slug; matches the D1 binding's tenant
        skill          - skill name (from SKILL.md frontmatter)
        action_class   - one of `ActionClassName` (or its string value)
        ceiling_level  - the ceiling configured at decision time
        decision       - one of `Decision`
        reason         - one of `DecisionReason`; closed enum, NOT free text

    Optional:
        skill_version  - SKILL.md content hash or version pin
        trace_id       - opaque request/turn id for cross-row correlation
        matter_ref     - opaque per-vertical reference (matter id, lead id)
        extra_metadata - additional JSON-serializable fields; merged after
                         the canonical keys. Use sparingly: dashboard
                         aggregations key on the canonical fields, not the
                         extra bag. Caller may NOT supply keys that
                         collide with canonical keys.

    Raises:
        ValueError      - decision / reason / ceiling_level / action_class
                          are not enum members
        AuditWriteError - underlying audit_log INSERT failed; caller must
                          abort the pending action
    """
    if not isinstance(decision, Decision):
        raise ValueError(f"decision must be a Decision enum, got {type(decision)!r}")
    if not isinstance(reason, DecisionReason):
        raise ValueError(
            f"reason must be a DecisionReason enum, got {type(reason)!r}; "
            "free-text reasons are not allowed (dashboard aggregates on the enum)"
        )

    # Normalize ceiling_level + action_class. Accept enum or string-value,
    # validate string-values against the enum to fail fast on typos.
    ceiling_value = (
        ceiling_level.value if isinstance(ceiling_level, CeilingLevel) else CeilingLevel(ceiling_level).value
    )
    action_class_value = (
        action_class.value if isinstance(action_class, ActionClassName) else ActionClassName(action_class).value
    )

    if not customer:
        raise ValueError("customer is required")
    if not skill:
        raise ValueError("skill is required")

    action_type = _DECISION_TO_ACTION_TYPE[decision]

    metadata: dict = {
        "trust_ceiling_decision": True,
        "customer": customer,
        "skill": skill,
        "action_class": action_class_value,
        "ceiling_level": ceiling_value,
        "decision": decision.value,
        "reason": reason.value,
        "skill_version": skill_version,
        "trace_id": trace_id,
    }

    if extra_metadata:
        canonical_keys = set(metadata.keys())
        collisions = canonical_keys & extra_metadata.keys()
        if collisions:
            raise ValueError(f"extra_metadata may not override canonical keys: {sorted(collisions)}")
        metadata.update(extra_metadata)

    return await writer.write(
        AuditEvent(
            action_type=action_type,
            actor="agent",
            actor_role=ActorRole.AGENT,
            skill_name=skill,
            matter_ref=matter_ref,
            trust_ceiling=ceiling_value,
            metadata=metadata,
        )
    )


__all__ = [
    "ActionClassName",
    "CeilingLevel",
    "Decision",
    "DecisionReason",
    "log_decision",
]
