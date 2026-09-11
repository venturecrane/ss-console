# Refusal handling

**Spec for issue #866.** Runtime semantics for the case when
`trust_ceiling.enforce()` returns `refuse`. PR #953
(`trust_ceiling_log.py`) makes the refusal visible as an audit row. This
spec defines what the agent does next: what state it enters, what the
customer sees, and how a cascade of refusals reaches the Captain.

Before this issue the path was: `enforce()` returns `refuse`,
`log_decision()` writes one audit row, and the runtime had nothing
specified beyond that. No customer-facing notification, no Captain
alert on cascade, no documented signal back to the dispatch path that
the skill MUST abort.

## Source

- platform-prd.md §11 (trust ceiling model)
  - §11.4: every action at every ceiling is logged
- platform-prd.md §7.5: safety substrate invariants
  - invariant #5 (ceiling enforced in code, not in prompt)
- `operator/adapter/trust_ceiling.py` (`enforce()` decision tree)
- `operator/safety-substrate/trust_ceiling_log.py` (sibling module
  from PR #953; canonical audit row writer this module delegates to)
- `operator/safety-substrate/sticky_stop.py` (sibling module from
  PR #948; provides the per-state-machine refusal counter that the
  dispatch path may wire in for sticky-stop transition decisions)
- `operator/adapter/audit_log.py` (PR #942): writer +
  `ACCEPTED_ACTION_TYPES`
- ADR 0005: refusal handling never originates an
  outbound customer-bound message; rows are written for the in-app
  notification surface to poll
- compliance-evidence-packet.md (#802): downstream consumer

## Module + integration point

The module is `operator/safety-substrate/refusal.py`. The public
surface:

```python
class RefusalHandler:
    def __init__(
        self,
        *,
        audit_writer: AuditLogWriter,
        counter: Optional[RefusalCounter] = None,
        cascade_threshold: int = 5,
        cascade_window_seconds: int = 3600,
        clock: Optional[Callable[[], datetime]] = None,
    ): ...

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
    ) -> RefusalOutcome: ...
```

The dispatch path (Hermes side, separate PR) calls `enforce()`, and
when the result is `EnforcementDecision.audit_action == "refuse"` it
calls `handler.handle(...)` on the same code path with the matching
`DecisionReason`. The mapping from `EnforcementDecision` branches to
`DecisionReason` members is the integration contract documented in
`trust-ceiling-logging.md` §"Where `log_decision()` plugs into the
dispatch path".

## What `handle()` does

1. **Canonical audit row.** Delegates to `log_decision()`
   (`trust_ceiling_log.py`) to write exactly one audit row tagged
   `metadata.trust_ceiling_decision = true`,
   `metadata.decision = "refuse"`. The row carries the closed-enum
   `DecisionReason` for dashboard aggregation. This module never
   duplicates the row.
2. **Customer-facing notification row.** Writes one additional audit
   row tagged `metadata.refusal_notification = true` and
   `metadata.notification_eligible = true`. The in-app notification
   surface (#876 / #964) polls for rows whose
   `metadata.notification_eligible == true` and renders them in the
   "what your Operator tried and could not do" feed.
3. **Captain alert (pattern-based, not always).** If the configured
   `RefusalCounter` reports a count >= `cascade_threshold` within the
   `cascade_window_seconds` for the (customer, skill) pair, writes one
   `ESCALATION_FIRED` row tagged
   `metadata.refusal_cascade_alert = true`. The Captain alert UI /
   paging is downstream of `ESCALATION_FIRED` consumers; this module
   only emits the row.
4. **Returns `RefusalOutcome`.** `aborted` is always True. The
   dispatch path MUST consult this and stop the skill before any tool
   actually runs. The outcome also carries the chosen
   `CustomerMessage` and the three audit-row IDs for cross-row
   correlation.

The skill MUST NOT execute the refused action. Refusal is per-decision
and final; there is no retry path in this module.

## Customer-facing message vocabulary (closed enum)

Internal `DecisionReason` values are substrate vocabulary. The
notification surface must render plain language the customer can act
on, without learning substrate-internal terms. This module maps each
refuse-side `DecisionReason` to one of a small closed
`CustomerMessage` enum:

| `DecisionReason`             | `CustomerMessage`                      | Rendered text (verbatim)                                                                                                                       |
| ---------------------------- | -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `CEILING_DISABLED`           | `SKILL_DISABLED`                       | Your Operator tried to use a skill that is currently off. Re-enable the skill in Settings if you want it to run.                               |
| `EXTERNAL_SEND_NO_APPROVAL`  | `APPROVAL_REQUIRED_SEND`               | Your Operator wanted to send a message but it needs your approval first. Open the draft to review and send.                                    |
| `COMMITMENT_NO_APPROVAL`     | `APPROVAL_REQUIRED_COMMITMENT`         | Your Operator paused before agreeing to something on your behalf. Open the request to review and approve.                                      |
| `DESTRUCTIVE_NO_APPROVAL`    | `APPROVAL_REQUIRED_DESTRUCTIVE`        | Your Operator paused before doing something it cannot undo. Open the request to review and approve.                                            |
| `DESTRUCTIVE_DRAFT_CEILING`  | `DESTRUCTIVE_BLOCKED_AT_DRAFT_CEILING` | Your Operator tried to delete or remove something, but its current ceiling does not allow that. Raise the ceiling or do it yourself if needed. |
| `UNKNOWN_ACTION_CLASS`       | `UNKNOWN_ACTION`                       | Your Operator tried something the substrate did not recognize. We have logged it for review.                                                   |
| _any unmapped future reason_ | `GENERIC_REFUSED` (safe fallback)      | Your Operator tried an action that was not allowed under your current settings. We have logged it for review.                                  |

A new refuse-side `DecisionReason` added to `trust_ceiling_log.py`
must extend `_REASON_TO_MESSAGE` in the same PR. The fallback
(`GENERIC_REFUSED`) prevents leakage of substrate vocabulary even when
the mapping has not been updated; a `WARNING` log records the missing
mapping.

## Audit row shapes

### 1. Canonical trust-ceiling-decision row

Written by `log_decision()` per the contract in
`trust-ceiling-logging.md`. Shape unchanged. For a refusal:

| Column        | Value                                              |
| ------------- | -------------------------------------------------- |
| `action_type` | `INVARIANT_VIOLATION`                              |
| `actor`       | `agent`                                            |
| `actor_role`  | `agent`                                            |
| `metadata`    | `trust_ceiling_decision=true`, `decision="refuse"` |

### 2. Customer-facing notification row

| Column          | Value            |
| --------------- | ---------------- |
| `action_type`   | `DRAFT_REJECTED` |
| `actor`         | `agent`          |
| `actor_role`    | `agent`          |
| `skill_name`    | from `skill` arg |
| `trust_ceiling` | from arg         |
| `metadata`      | see below        |

Metadata:

```json
{
  "refusal_notification": true,
  "notification_eligible": true,
  "customer": "acme",
  "skill": "settlement-negotiation",
  "action_class": "commitment",
  "ceiling_level": "autonomous",
  "reason": "commitment_no_approval",
  "customer_message": "Your Operator paused before agreeing to something on your behalf. Open the request to review and approve.",
  "decision_audit_id": "01HXY...",
  "trace_id": "trace-01HXY...",
  "skill_version": "2.1.0"
}
```

`action_type` reuses the closed-set vocabulary
(`ACCEPTED_ACTION_TYPES`) per the substrate convention. The
notification surface MUST identify these rows by
`metadata.refusal_notification = true` AND
`metadata.notification_eligible = true`, not by `action_type` alone.
The `decision_audit_id` lets the dashboard render the notification
side-by-side with the underlying trust-ceiling-decision row in the
audit viewer.

### 3. Captain alert row (only on cascade)

Written when `recent_refusal_count >= cascade_threshold` after the
current event lands.

| Column        | Value              |
| ------------- | ------------------ |
| `action_type` | `ESCALATION_FIRED` |
| `actor`       | `agent`            |
| `actor_role`  | `agent`            |
| `metadata`    | see below          |

Metadata:

```json
{
  "refusal_cascade_alert": true,
  "customer": "acme",
  "skill": "settlement-negotiation",
  "action_class": "commitment",
  "ceiling_level": "autonomous",
  "reason": "commitment_no_approval",
  "recent_refusal_count": 5,
  "cascade_threshold": 5,
  "window_seconds": 3600,
  "decision_audit_id": "01HXY...",
  "notification_audit_id": "01HXZ...",
  "trace_id": "trace-01HXY..."
}
```

The Captain alert surface MUST identify these rows by
`metadata.refusal_cascade_alert = true` and aggregate by
`(customer, skill)`. The cross-row IDs let the alert UI link back to
the decision and notification rows that triggered the cascade.

## Cascade detection

Captain alerts fire on a refusal pattern, not on every refusal. The
default pattern: five or more refusals on a single (customer, skill)
pair within one hour. Configurable per construction via
`cascade_threshold` and `cascade_window_seconds`.

`RefusalCounter` is a Protocol:

```python
class RefusalCounter(Protocol):
    def record_and_count(
        self,
        *,
        customer: str,
        skill: str,
        now: datetime,
    ) -> int: ...
```

The module ships `InMemoryRefusalCounter` for tests and single-process
runtimes. Production wiring may back the counter with the sticky-stop
store (which already tracks per-customer refusal counts per PR #948)
or with a dedicated D1 table so the count survives Hermes restarts.

Partitioning: counts are kept per (customer, skill). Refusals on
`skill-A` and `skill-B` do not aggregate; a cascade in one skill is
not evidence of a cascade in another.

## Refusal vs sticky-stop

These two mechanisms are deliberately separate:

- **Refusal** is per-decision. One `enforce()` call, one decision,
  one set of audit rows. Always aborts the pending action.
- **Sticky-stop** is per-state-machine. Tracks an envelope of
  observations (consecutive tool failures, refusal cascades, time
  budget, cost) and transitions the dispatch path through
  OK -> HARD_STOP at the threshold (two states since 2026-09-02). Only
  Captain `clear()` decreases the level.

The refusal handler does NOT directly transition sticky-stop states.
The dispatch path is free to call sticky-stop's `record_refusal()`
in addition to invoking the refusal handler; the sticky-stop machine
decides on its own ladder whether the count warrants a transition.
Keeping the two paths separate means the refusal handler stays a
pure emitter and the sticky-stop policy stays in one place.

## Refusals emit internally only

This module never originates an outbound customer-bound message.
Every consequence of a refusal is mediated through an audit row that
a downstream surface (in-app notification, Captain alert UI) polls.
This is orthogonal to send entitlements: whether the operator may send
externally is governed by the trust ceiling (ADR 0025/0035), not by this
module. The notification feed is internal to the dashboard.

## Failure modes

- **Audit write fails.** Any of the three audit writes
  (decision row, notification row, Captain alert row) failing raises
  `AuditWriteError`. The dispatch path MUST treat this as fatal and
  abort the pending action. Same invariant as the audit log writer
  (PR #942) and the sticky-stop machine (PR #948): a state the
  substrate cannot evidence is a state the agent does not run.
- **Free-text reason supplied.** `ValueError` raised before any audit
  write executes. The dispatch path MUST map to a `DecisionReason`
  member; the integration table in `trust-ceiling-logging.md` is the
  contract.
- **Invalid `ceiling_level` / `action_class` string.** Validation
  happens inside `log_decision()`; `ValueError` raised before any
  audit write executes.
- **Cascade threshold / window misconfiguration.** Construction-time
  `ValueError` on non-positive `cascade_threshold` or
  `cascade_window_seconds`.

## Verification

`operator/safety-substrate/tests/test_refusal.py` exercises:

- Happy path: one refusal emits exactly one decision row + one
  notification row, aborted=True, customer-facing message present
- No duplicate audit rows for one refusal
- Each refuse-side `DecisionReason` maps to the expected
  `CustomerMessage` (parametrized)
- Customer message does NOT leak internal `DecisionReason` vocabulary
- Free-text reason raises `ValueError`
- Single refusal does not emit a Captain alert
- Refusal cascade fires exactly one `ESCALATION_FIRED` row when the
  threshold is met
- Cascade window drops events outside the window
- Cascade threshold is per (customer, skill); counts do not bleed
  across skills
- Construction-time threshold / window validation
- The refusal handler does NOT write sticky-stop transition rows
- `aborted == True` for every refuse-side reason
- Audit write failure propagates as `AuditWriteError`
- String-valued `action_class` / `ceiling_level` accepted
  (dispatch-path natural shape)
- `trace_id` propagates from decision row to notification row to
  Captain alert row for cross-row correlation
- `InMemoryRefusalCounter` partitions by (customer, skill) and drops
  events outside its window

Run locally:

```
cd operator && uv run --with pytest python -m pytest \
  safety-substrate/tests/test_refusal.py -v
```

## Out of scope (filed elsewhere)

- **Dashboard notification surface.** The in-app notification feed
  that polls `metadata.notification_eligible = true` is the work in
  #876 / #964. This spec is the emission contract.
- **Captain alert UI / paging.** Downstream of `ESCALATION_FIRED`
  consumers. The metadata contract here gives the UI enough to render
  and route.
- **Dispatch-path integration.** Wiring `RefusalHandler.handle()` to
  the dispatch path after `enforce()` returns `refuse` is the adapter
  team's work, tracked separately. The integration-point mapping
  table in `trust-ceiling-logging.md` is the contract.
- **D1-backed `RefusalCounter`.** Production wiring backs the counter
  with persistent storage (sticky-stop store or a dedicated table)
  so the count survives restarts. This spec keeps the counter as a
  Protocol so the test path can use the in-memory implementation.

## Cross-references

- trust-ceiling-logging.md (#864): canonical audit row writer this
  module delegates to
- sticky-stop.md (#843): independent state-machine for runaway-loop
  detection; refusal counter is a related but separate concern
- safety-invariants.md (#865): invariant #5 (ceiling enforced in
  code) is the source of the refusal contract
- compliance-evidence-packet.md (#802): downstream consumer that
  groups rows by `metadata.trust_ceiling_decision`,
  `metadata.refusal_notification`, and `metadata.refusal_cascade_alert`
- ADR 0005: refusal handling never originates an
  outbound message
- PR #942 (audit log persistence): `AuditLogWriter`
- PR #948 (sticky-stop mechanism): convention source +
  `record_refusal()` counter the dispatch path may also tick
- PR #953 (trust ceiling decision logging): `log_decision()`
