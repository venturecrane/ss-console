"""Refusal handling runtime (issue #866).

When `enforce()` in `operator/adapter/trust_ceiling.py` returns
`refuse`, PR #953 (`trust_ceiling_log.py`) records the decision as an
audit row tagged `metadata.trust_ceiling_decision = true` with
`metadata.decision = "refuse"`. That row makes the refusal visible to
operators, the audit viewer (#873), and the compliance-evidence packet
(#802). It does NOT, by itself, specify what the runtime does next:

* whether the skill is supposed to abort or continue,
* what (if anything) the customer sees in the in-app notification
  surface (#876 / #964),
* whether the substrate should escalate the pattern to a Captain alert
  when refusals start cascading on a single skill.

This module is the runtime semantics layer on top of `log_decision()`.
It does NOT replace the audit row that `log_decision()` writes; it
delegates to that module for the canonical audit emission and then adds
two refusal-specific follow-ups, each as one additional audit row:

  1. A customer-facing notification row (the in-app notification
     surface from #876 / #964 polls for rows whose
     `metadata.notification_eligible == true` and surfaces them in the
     "what your Operator tried and could not do" feed).
  2. A Captain-side alert row (only when the refusal pattern indicates
     an operator-side issue, e.g. five or more refusals on the same
     skill within one hour).

Design rules
------------

* **Refusal != sticky-stop.** A refusal is per-decision; sticky-stop is
  per-state-machine. The refusal handler MAY call sticky-stop's
  `record_refusal()` to tick the refusal-cascade counter (PR #948
  already exposes that API); it does NOT directly transition states.
  The sticky-stop machine decides on its own ladder whether the count
  warrants WARN / SOFT_STOP / HARD_STOP.

* **No duplicate audit rows.** One refusal = one trust-ceiling-decision
  audit row written by `log_decision()`. This module never duplicates
  that row. The two follow-up emissions above are SEPARATE rows that
  carry their own purpose (notification eligibility, Captain alert) and
  are linked back via `metadata.trace_id` and
  `metadata.refusal_audit_id`.

* **Customer-facing message vocabulary is closed.** Internal
  `DecisionReason` values would expose substrate vocabulary the
  customer should not have to learn ("commitment_no_approval",
  "destructive_draft_ceiling"). This module maps every refuse-side
  `DecisionReason` to one of a small closed enum of
  `CustomerMessage` values that describe the situation in
  customer-appropriate terms. The dashboard / notification surface
  renders the message verbatim. Adding a new `DecisionReason` requires
  extending the mapping or accepting the safe fallback.

* **Synchronous emission.** Like `log_decision()` and the audit log
  writer itself, `handle()` returns only after all audit rows have
  landed. Any of the three writes failing (the decision row, the
  notification row, the optional Captain alert) raises
  `AuditWriteError`, and the caller (dispatch path) MUST treat the
  refusal as "could not be evidenced" and abort the pending action,
  same invariant as elsewhere in the substrate.

* **NO autonomous send.** This module never originates an outbound
  customer-bound message. It writes audit rows that the in-app
  notification surface and the Captain alert surface poll for. ADR
  0005 is not relaxed here.

* **The skill MUST NOT execute the refused action.** The handler
  returns a `RefusalOutcome` whose `aborted == True`. The dispatch
  path checks this and stops the skill before any tool actually runs.

* **Captain alert is opt-in by pattern, not always.** A single refusal
  is normal substrate behavior: the operator chose to keep their
  ceiling tight; the skill asked anyway; the substrate said no. That
  loop should not page anyone. The Captain alert only fires when the
  refusal count on a given (customer, skill) tuple within the configured
  alert window exceeds the threshold. The cascade detection is delegated
  to the caller-supplied `RefusalCounter` so this module stays a pure
  emitter; the production wiring backs the counter with the
  sticky-stop store (which already tracks refusal counts per customer)
  or with a simpler skill-scoped sqlite/in-memory table.

* **Module shape mirrors the rest of the substrate.** Pure-Python,
  no module-level state, injectable clock for deterministic tests,
  closed-set enums for everything that lands in audit metadata.

Module shape
------------

::

    from operator_safety_substrate.refusal import (
        CustomerMessage,
        RefusalHandler,
        RefusalOutcome,
        InMemoryRefusalCounter,
    )

    handler = RefusalHandler(
        audit_writer=audit_writer,
        counter=InMemoryRefusalCounter(),
    )

    # On the dispatch path, after `enforce()` returns `refuse`:
    outcome = await handler.handle(
        customer="acme",
        skill="settlement-negotiation",
        action_class=ActionClassName.COMMITMENT,
        ceiling_level=CeilingLevel.AUTONOMOUS,
        reason=DecisionReason.COMMITMENT_NO_APPROVAL,
        skill_version="2.1.0",
        trace_id="trace-01HXY...",
        matter_ref="matter-abc-123",
    )

    assert outcome.aborted is True
    # The skill is NOT invoked; the in-app notification surface will
    # pick up the notification row on its next poll; if the cascade
    # threshold was exceeded, the Captain alert row was also written.

Out of scope (filed elsewhere)
------------------------------

- The dashboard surface that consumes `notification_eligible` audit
  rows is the in-app notification work in #876 / #964; this module is
  the emission side.
- The Captain alert UI / paging is downstream of `ESCALATION_FIRED`
  consumers; this module emits the row using the existing closed-set
  action_type.
- Override / clear paths for an active refusal pattern live in the
  Captain control plane (sticky-stop `clear()` already covers the
  related sticky-stop path).
"""

from __future__ import annotations

import enum
import logging
import sys
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Deque, Optional, Protocol

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))  # operator/

from adapter.audit_log import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    ActorRole,
    AuditEvent,
    AuditLogWriter,
)

# Sibling module from PR #953. Read-only; this module delegates the
# canonical audit row write to log_decision() and never duplicates it.
from trust_ceiling_log import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    ActionClassName,
    CeilingLevel,
    Decision,
    DecisionReason,
    log_decision,
)

log = logging.getLogger("aie.refusal")


# ---------------------------------------------------------------------------
# Customer-facing message vocabulary
#
# Closed enum, NOT a free-text bag. The in-app notification surface
# (issues #876 / #964) renders the value verbatim. We pick wording the
# customer can act on without needing substrate-internal vocabulary.
#
# Mapping from `DecisionReason` to `CustomerMessage` is defined below in
# `_REASON_TO_MESSAGE`. Adding a new refuse-side `DecisionReason` to
# trust_ceiling_log.py requires extending the mapping here in the same
# PR; otherwise the wrapper falls back to `GENERIC_REFUSED` and a
# `WARNING` log records the missing-mapping for follow-up.
# ---------------------------------------------------------------------------


class CustomerMessage(str, enum.Enum):
    """Closed set of customer-facing refusal messages.

    Strings are intentionally plain-language and avoid substrate-internal
    vocabulary. The notification surface renders them verbatim.
    """

    APPROVAL_REQUIRED_SEND = (
        "Your Operator wanted to send a message but it needs your approval first. Open the draft to review and send."
    )
    APPROVAL_REQUIRED_COMMITMENT = (
        "Your Operator paused before agreeing to something on your behalf. Open the request to review and approve."
    )
    APPROVAL_REQUIRED_DESTRUCTIVE = (
        "Your Operator paused before doing something it cannot undo. Open the request to review and approve."
    )
    DESTRUCTIVE_BLOCKED_AT_DRAFT_CEILING = (
        "Your Operator tried to delete or remove something, but its "
        "current ceiling does not allow that. Raise the ceiling or do "
        "it yourself if needed."
    )
    SKILL_DISABLED = (
        "Your Operator tried to use a skill that is currently off. "
        "Re-enable the skill in Settings if you want it to run."
    )
    UNKNOWN_ACTION = "Your Operator tried something the substrate did not recognize. We have logged it for review."
    GENERIC_REFUSED = (
        "Your Operator tried an action that was not allowed under your current settings. We have logged it for review."
    )


_REASON_TO_MESSAGE: dict[DecisionReason, CustomerMessage] = {
    DecisionReason.CEILING_DISABLED: CustomerMessage.SKILL_DISABLED,
    DecisionReason.EXTERNAL_SEND_NO_APPROVAL: CustomerMessage.APPROVAL_REQUIRED_SEND,
    DecisionReason.COMMITMENT_NO_APPROVAL: CustomerMessage.APPROVAL_REQUIRED_COMMITMENT,
    DecisionReason.DESTRUCTIVE_NO_APPROVAL: CustomerMessage.APPROVAL_REQUIRED_DESTRUCTIVE,
    DecisionReason.DESTRUCTIVE_DRAFT_CEILING: CustomerMessage.DESTRUCTIVE_BLOCKED_AT_DRAFT_CEILING,
    DecisionReason.UNKNOWN_ACTION_CLASS: CustomerMessage.UNKNOWN_ACTION,
}


def _message_for(reason: DecisionReason) -> CustomerMessage:
    """Look up the customer-facing message for a refuse-side reason.

    Falls back to GENERIC_REFUSED and logs a warning if a new
    DecisionReason has landed in trust_ceiling_log.py without a mapping
    entry here. The fallback is safe (does not leak internal text) but
    the missing mapping should be fixed.
    """
    mapped = _REASON_TO_MESSAGE.get(reason)
    if mapped is not None:
        return mapped
    log.warning(
        "refusal: no CustomerMessage mapping for DecisionReason=%s; "
        "falling back to GENERIC_REFUSED. Update _REASON_TO_MESSAGE in "
        "operator/safety-substrate/refusal.py.",
        reason.value,
    )
    return CustomerMessage.GENERIC_REFUSED


# ---------------------------------------------------------------------------
# Cascade detection
#
# The Captain alert fires only on a refusal pattern, not on every
# refusal. The pattern check is a sliding-window count per
# (customer, skill) pair. Production wiring may back this with the
# sticky-stop store (which already tracks per-customer refusal counts
# per PR #948) or with a dedicated table; the test path uses the
# in-memory implementation below.
# ---------------------------------------------------------------------------


class RefusalCounter(Protocol):
    """Tracks refusal counts per (customer, skill) for cascade detection.

    Implementations supply the per-(customer, skill) sliding-window
    bookkeeping and answer one question: how many refusals on this
    (customer, skill) pair landed within the configured alert window
    ending at `now`?
    """

    def record_and_count(
        self,
        *,
        customer: str,
        skill: str,
        now: datetime,
    ) -> int: ...


@dataclass
class InMemoryRefusalCounter:
    """In-memory sliding-window refusal counter.

    Suitable for tests and single-process runtimes. Production wiring
    should back this with the sticky-stop store (PR #948) so the count
    survives Hermes restarts. The window default matches the design rule
    ("5+ refusals on the same skill within 1 hour").
    """

    window_seconds: int = 3600
    _events: dict[tuple[str, str], Deque[datetime]] = field(default_factory=dict)

    def record_and_count(
        self,
        *,
        customer: str,
        skill: str,
        now: datetime,
    ) -> int:
        key = (customer, skill)
        bucket = self._events.setdefault(key, deque())
        cutoff = now - timedelta(seconds=self.window_seconds)
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        bucket.append(now)
        return len(bucket)


# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RefusalOutcome:
    """Return value from `RefusalHandler.handle()`.

    Fields:
        aborted               - always True (refusal means the skill MUST
                                NOT execute the action). The dispatch path
                                MUST check this and stop the skill before
                                any tool runs.
        message               - customer-facing message (from CustomerMessage).
        decision_audit_id     - ULID of the trust-ceiling-decision audit
                                row written by `log_decision()`.
        notification_audit_id - ULID of the notification-eligible audit
                                row this handler wrote.
        captain_alert_audit_id - ULID of the Captain alert row, or None
                                 if the cascade threshold was not met.
        recent_refusal_count  - count of refusals on this (customer, skill)
                                in the alert window after this event.
    """

    aborted: bool
    message: CustomerMessage
    decision_audit_id: str
    notification_audit_id: str
    captain_alert_audit_id: Optional[str]
    recent_refusal_count: int


class RefusalHandler:
    """Runtime handler invoked when `enforce()` returns `refuse`.

    Construction takes an `AuditLogWriter`, a `RefusalCounter`, the
    cascade threshold, and an optional clock for deterministic tests.

    Public surface: `handle()`. The handler:

      1. Calls `log_decision()` (sibling PR #953) to write the canonical
         trust-ceiling-decision row (`metadata.decision == "refuse"`).
      2. Writes a customer-facing notification row tagged
         `metadata.notification_eligible == true`.
      3. Records the refusal in the counter; if the count exceeds the
         configured threshold within the window, writes an
         `ESCALATION_FIRED` Captain alert row.
      4. Returns a `RefusalOutcome` so the dispatch path can stop the
         skill and surface the message to the customer.
    """

    DEFAULT_CASCADE_THRESHOLD = 5
    DEFAULT_CASCADE_WINDOW_SECONDS = 3600

    def __init__(
        self,
        *,
        audit_writer: AuditLogWriter,
        counter: Optional[RefusalCounter] = None,
        cascade_threshold: int = DEFAULT_CASCADE_THRESHOLD,
        cascade_window_seconds: int = DEFAULT_CASCADE_WINDOW_SECONDS,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        if cascade_threshold < 1:
            raise ValueError("cascade_threshold must be >= 1")
        if cascade_window_seconds < 1:
            raise ValueError("cascade_window_seconds must be >= 1")
        self._audit = audit_writer
        self._counter = counter or InMemoryRefusalCounter(
            window_seconds=cascade_window_seconds,
        )
        self._cascade_threshold = cascade_threshold
        self._cascade_window_seconds = cascade_window_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def handle(
        self,
        *,
        customer: str,
        skill: str,
        action_class: ActionClassName | str,
        ceiling_level: CeilingLevel | str,
        reason: DecisionReason,
        skill_version: Optional[str] = None,
        trace_id: Optional[str] = None,
        matter_ref: Optional[str] = None,
    ) -> RefusalOutcome:
        """Emit the refusal-related audit rows and return the outcome.

        Raises:
            ValueError       - inputs fail validation in `log_decision()`
                               (e.g., bad action_class string).
            AuditWriteError  - any of the audit writes failed; the caller
                               MUST abort the pending action.
        """
        if not isinstance(reason, DecisionReason):
            raise ValueError(f"reason must be a DecisionReason enum, got {type(reason)!r}")

        # 1. Canonical trust-ceiling-decision row via the sibling module.
        #    log_decision() writes one row tagged `trust_ceiling_decision`
        #    true with `decision = "refuse"`. We do NOT duplicate it.
        decision_audit_id = await log_decision(
            self._audit,
            customer=customer,
            skill=skill,
            action_class=action_class,
            ceiling_level=ceiling_level,
            decision=Decision.REFUSE,
            reason=reason,
            skill_version=skill_version,
            trace_id=trace_id,
            matter_ref=matter_ref,
        )

        # Normalize ceiling_level / action_class for follow-up rows in
        # the same way log_decision did, so the values that land in
        # follow-up metadata stay aligned.
        ceiling_value = (
            ceiling_level.value if isinstance(ceiling_level, CeilingLevel) else CeilingLevel(ceiling_level).value
        )
        action_class_value = (
            action_class.value if isinstance(action_class, ActionClassName) else ActionClassName(action_class).value
        )

        message = _message_for(reason)

        # 2. Customer-facing notification row. Action_type reuses the
        #    closed-set vocabulary (DRAFT_REJECTED is the closest fit:
        #    the substrate rejected what would have been a draft or
        #    action). The dashboard / notification surface identifies
        #    these rows via metadata.notification_eligible == true and
        #    metadata.refusal_notification == true; not by action_type
        #    alone, mirroring the trust-ceiling-logging convention.
        notification_audit_id = await self._audit.write(
            AuditEvent(
                action_type="DRAFT_REJECTED",
                actor="agent",
                actor_role=ActorRole.AGENT,
                skill_name=skill,
                matter_ref=matter_ref,
                trust_ceiling=ceiling_value,
                metadata={
                    "refusal_notification": True,
                    "notification_eligible": True,
                    "customer": customer,
                    "skill": skill,
                    "action_class": action_class_value,
                    "ceiling_level": ceiling_value,
                    "reason": reason.value,
                    "customer_message": message.value,
                    "decision_audit_id": decision_audit_id,
                    "trace_id": trace_id,
                    "skill_version": skill_version,
                },
            )
        )

        # 3. Cascade detection. record_and_count returns the count in
        #    the configured window after recording this event. If the
        #    count crossed the threshold on this event, emit one
        #    Captain alert row.
        now = self._clock()
        recent_count = self._counter.record_and_count(
            customer=customer,
            skill=skill,
            now=now,
        )
        captain_alert_audit_id: Optional[str] = None
        if recent_count >= self._cascade_threshold:
            captain_alert_audit_id = await self._audit.write(
                AuditEvent(
                    action_type="ESCALATION_FIRED",
                    actor="agent",
                    actor_role=ActorRole.AGENT,
                    skill_name=skill,
                    matter_ref=matter_ref,
                    trust_ceiling=ceiling_value,
                    metadata={
                        "refusal_cascade_alert": True,
                        "customer": customer,
                        "skill": skill,
                        "action_class": action_class_value,
                        "ceiling_level": ceiling_value,
                        "reason": reason.value,
                        "recent_refusal_count": recent_count,
                        "cascade_threshold": self._cascade_threshold,
                        "window_seconds": self._cascade_window_seconds,
                        "decision_audit_id": decision_audit_id,
                        "notification_audit_id": notification_audit_id,
                        "trace_id": trace_id,
                    },
                )
            )

        return RefusalOutcome(
            aborted=True,
            message=message,
            decision_audit_id=decision_audit_id,
            notification_audit_id=notification_audit_id,
            captain_alert_audit_id=captain_alert_audit_id,
            recent_refusal_count=recent_count,
        )


__all__ = [
    "CustomerMessage",
    "InMemoryRefusalCounter",
    "RefusalCounter",
    "RefusalHandler",
    "RefusalOutcome",
]
