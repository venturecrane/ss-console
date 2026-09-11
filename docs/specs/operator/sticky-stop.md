# Sticky-stop circuit breaker

**Spec for issue #843.** A forward-only state machine that pins the agent's
dispatch path when the substrate observes runaway-loop signals: consecutive
tool failures, refusal cascades, time-budget overruns, or daily cost-cap
breaches.

The mechanism is named in platform PRD §11.5 and §7.5 invariant #4 but
had no emission or enforcement points before this issue. Without it, a
runaway agent loop has no circuit breaker.

> **Amended 2026-09-02 — the ladder is two states, OK and HARD_STOP.**
> As originally specified below it had four. `WARN` and `SOFT_STOP` were
> removed because neither ever restricted anything: `SOFT_STOP`'s documented
> effect (pin every `trust_ceiling` to `draft_for_review`) was never
> implemented on any enforcement arm, and `assert_allowed()` passed straight
> through both. What they did do was name a cause — and a rung that names a
> cause without changing behaviour reads to an operator as a brake that is
> holding, which is worse than no rung at all. The cause is now carried
> explicitly on the state (`reason` / `condition`) and surfaced in the
> heartbeat, so nothing is lost by dropping the rungs. The stop thresholds
> are unchanged: every meter halts at exactly the count it always halted at.
> The one arm with no `HARD_STOP` threshold is the time budget — see
> [§3](#3-time-budget-exceeded).

## Source

- platform-prd.md §7.5 ("Safety substrate invariants") — invariant #4
  defines sticky-stop as architecturally pinned, surviving context
  compaction, restart, and skill reload
- platform-prd.md §11.5 ("Sticky stop") — the operator-pause surface
- d1-schema.md §1 — `audit_log` action_type vocabulary the transitions
  emit into

## Why two paths share one mechanism

There are two ways the sticky stop fires:

1. **Operator-initiated.** A human clicks "pause all skills" in the
   dashboard. Covered by platform-prd.md §11.5. The dispatch path
   consults the pinned slot before invoking any skill. Recovery is
   one-click by the same operator.
2. **System-initiated (this spec).** The substrate notices the agent
   is misbehaving — looping on a failing tool, refusing on every call,
   burning the cost cap, exceeding wall-clock time — and pins a stop
   on its behalf. Recovery requires Captain investigation, not a click.

Both paths produce the same dispatch-path effect (skill invocation
blocked or pinned to draft-for-review). The differences are:

- **Audit tagging.** Operator pauses write `AGENT_STOPPED` with
  metadata `actor_role=principal|operator`. System stops write
  `AGENT_STOPPED` (HARD_STOP) with `actor=agent`, `actor_role=agent`.
  `INVARIANT_VIOLATION` is still written, but now only for an observation
  that changes no level — see [Audit emission](#audit-emission).
- **Recovery actor.** Operator pauses clear via the dashboard pause
  control. System stops clear via Captain investigation through the
  control plane.
- **Conditions.** Operator pauses have no condition — the human chose
  to stop. System stops always carry a `condition_triggered` value.

The persistence layer (D1 table `sticky_stop_state`) is shared.

## State machine

Two states, forward-only by default:

```
  OK ----------------------------> HARD_STOP
   ^                                    |
   |                                    |
   +--- Captain clear() ----------------+
```

`clear()` is the only path backwards. There is no autonomous downgrade.
Tool successes reset the failure counter to zero but do NOT downgrade the
level; the runtime is permitted to keep observing without escalating the
sticky-stop tier, and the Captain decides when to clear after
investigation.

### State semantics

| State       | Effect on dispatch                                                            | UI                                                |
| ----------- | ----------------------------------------------------------------------------- | ------------------------------------------------- |
| `OK`        | Normal operation                                                              | No banner                                         |
| `HARD_STOP` | Every skill invocation refused. Dispatch entrypoint raises `StickyStopError`. | Red banner: "Agent paused. Captain investigating" |

A seat below its stop threshold is `OK` and says so plainly. Observations
that used to raise the level to `WARN` or `SOFT_STOP` still land as audit
rows and still populate the state's `reason`/`condition`; they simply do
not pretend to restrict anything they never restricted.

**Reading a legacy row.** Rows written before 2026-09-02 may still hold
`WARN` or `SOFT_STOP` in the `level` column. The module normalises them on
read (`LEGACY_LEVELS`): both map to `OK`, because that is what they meant
operationally. No migration rewrites stored history.

The dashboard surface for these banners is a separate issue. This spec
covers the state-machine module and its persistence; UI is downstream.

## Conditions and default thresholds

Configurable per customer via `customer.yaml.safety.sticky_stop` (see
[Customer.yaml shape](#customeryaml-shape) below). When unset, the
following defaults apply. They are conservative — easier to loosen than
to recover from a tighter-than-needed loop that ran for hours.

### 1. Consecutive tool failures

A tool call returns a failure within a rolling time window.

| Threshold                     | Default      | Transition        |
| ----------------------------- | ------------ | ----------------- |
| `tool_failure_hard_stop`      | 8            | `OK -> HARD_STOP` |
| `tool_failure_window_seconds` | 600 (10 min) | window for streak |

A successful tool call resets the streak. A failure outside the window
resets the streak to 1 and starts a new window.

### 2. Refusal cascade

A trust-ceiling refusal (the substrate refused to allow a skill action).
Counts within a rolling window. A skill spamming `refused` responses is
itself a runaway signal — either the prompt drifted in an unsafe direction
or the operator's ceiling is incompatible with the skill's request shape.

| Threshold                | Default       | Transition         |
| ------------------------ | ------------- | ------------------ |
| `refusal_hard_stop`      | 20            | `OK -> HARD_STOP`  |
| `refusal_window_seconds` | 1800 (30 min) | window for cascade |

### 3. Time budget exceeded

Wall-clock seconds for a single agent run.

**This arm records and stops nothing, deliberately.** `SOFT_STOP` was its
only outcome, and `SOFT_STOP` restricted nothing, so its real effect has
always been "write a row". Promoting it to `HARD_STOP` in the collapse
would have introduced a brake that can halt a client mid-run — a deliberate
call to make on its own evidence, not a side effect of deleting two unused
words. So the overrun is recorded (`observation=True`, `transitioned=False`)
and the level is left alone.

| Threshold             | Default       | Transition                       |
| --------------------- | ------------- | -------------------------------- |
| `time_budget_seconds` | 3600 (1 hour) | none — records an audit row only |

A single run therefore has no wall-clock brake; the daily cost cap is the
only backstop, and it asks a different question slowly. That exposure is
recorded against this control in `operator/contracts/runtime-controls.yaml`.

Callers feed `record_runtime_seconds()` per turn or per minute (poll).
The machine itself has no async wakeup.

### 4. Cost threshold

Daily $ cap on LLM costs in cents (so cost_telemetry's integer-cents
convention applies). Resets at UTC date rollover.

| Threshold            | Default        | Transition                    |
| -------------------- | -------------- | ----------------------------- |
| `cost_daily_cents`   | 5000 ($50/day) | per-day cap                   |
| `cost_hard_stop_pct` | 200            | `OK -> HARD_STOP` (at 2x cap) |

Note what this means in practice, because the removed rungs made it look
otherwise: the seat stops at **twice** the daily cap, not at the cap. Hitting
100% of `cost_daily_cents` never stopped anything — it moved the level to
`SOFT_STOP`, which restricted nothing. If the cap is meant to be a brake, the
lever is `cost_hard_stop_pct`, and lowering it to 100 is a real change in
behaviour that belongs to whoever authors the engagement.

Cost is fed in via `record_cost_cents()` as LLM-cost events fire. The
cost-telemetry pipeline (cost-telemetry-events.md) is the natural source.

## Captain recovery

`clear()` is the only path that decreases level. The interface:

```python
await machine.clear(
    customer="acme",
    persona="marcus",
    captain_id="captain-scott",  # caller-asserted identity
    reason="vendor tool flap confirmed; runaway-loop false positive",
)
```

Caller responsibility: the actor must be a Captain. Identity verification
lives in the control-plane RBAC layer; this module trusts the caller to
have established identity before invoking. The control plane MUST NOT
expose `clear()` to non-Captain roles.

`clear()` resets `level=OK`, zeroes the rolling counters
(`consecutive_tool_failures=0`, `refusal_count=0`), and preserves
`cost_cents_today` (the cost cap is a daily resource accounting; clearing
sticky-stop does not buy back budget).

## Audit emission

Every state transition writes exactly one row to `audit_log` via the
`AuditLogWriter` (`operator/adapter/audit_log.py`, on main from PR
#942). Action types reuse the existing closed-set vocabulary
(`ACCEPTED_ACTION_TYPES`) so this module does not need to extend the
audit-log contract:

| Transition kind                   | `action_type`         | `actor`      | `actor_role` |
| --------------------------------- | --------------------- | ------------ | ------------ |
| Entry to `HARD_STOP`              | `AGENT_STOPPED`       | `agent`      | `agent`      |
| Observation that changes no level | `INVARIANT_VIOLATION` | `agent`      | `agent`      |
| Captain `clear()`                 | `AGENT_RESUMED`       | `captain_id` | `captain`    |

The middle row is the time-budget overrun (§3). It carries
`sticky_stop_transition: false` and `level_unchanged_by_design: true`, so a
reader — human or query — can tell an observation from a stop without
inferring it from the level pair.

The transition-specific detail lives in the `metadata` column as JSON:

```json
{
  "sticky_stop_transition": true,
  "customer": "acme",
  "persona": "marcus",
  "from_state": "OK",
  "to_state": "HARD_STOP",
  "condition_triggered": "consecutive_tool_failures",
  "reason": "consecutive_tool_failures=8 (window=600s, skill=inbox-triage)",
  "consecutive_tool_failures": 8,
  "window_seconds": 600,
  "thresholds": { "hard_stop": 8 }
}
```

For Captain clears the metadata sets `sticky_stop_cleared: true` and
includes the prior state and the Captain's reason. The compliance-evidence
packet generator (`compliance-evidence-packet.md`) groups rows by these
metadata keys to render the sticky-stop section.

## Customer.yaml shape

```yaml
safety:
  sticky_stop:
    tool_failure:
      hard_stop: 8
      window_seconds: 600
    refusal:
      hard_stop: 20
      window_seconds: 1800
    time_budget_seconds: 3600
    cost:
      daily_cents: 5000
      hard_stop_pct: 200
```

All keys optional; module-level defaults apply when absent.

The `warn` / `soft_stop` / `warn_pct` / `soft_stop_pct` keys were removed with
their states on 2026-09-02. A customer.yaml still carrying one is not an error
and changes nothing: every reader pulls named keys out of the block with
`.get()` and ignores the rest (overlay `shared/cost_breaker.thresholds_from_config`).

**The live seat authors a different, narrower block.** What a real
`customer.yaml` carries today is `safety.sticky_stop.cost_cap_daily_cents`
(plus `inbound_daily_cap` and `web_search_daily_cap`, which are separate
controls), read by the overlay at runtime per ADR 0044. Only the daily cap is
customer-authorable; the 200% hard-stop percentage is platform semantics. The
richer block above is the substrate module's own construction shape — see
`operator/contracts/customer-yaml-blocks.yaml` for the authored contract.

The `customer-yaml-schema.md` schema does not yet have a `safety` top-level
section. When the schema picks one up (under a future ADR), the sticky-stop
block lives under it. Until then the block is read directly by the
sticky-stop module at construction time via a customer.yaml resolver
(provisioning-time wiring, not in scope of this module).

## D1 table

Per ADR 0008 + 0009 the state lives in the per-customer D1 database
(`hermes-{slug}-d1`). One row per `(customer, persona)` tuple. The
composite primary key enforces uniqueness.

Schema in `operator/migrations/0004_sticky_stop_state.sql`. Columns:

| Column                           | Type                       | Notes                                                                                    |
| -------------------------------- | -------------------------- | ---------------------------------------------------------------------------------------- |
| `customer`                       | TEXT NOT NULL              | customer_id slug; equals the D1 binding's tenant                                         |
| `persona`                        | TEXT NOT NULL              | `customer.yaml.personas[].slug`                                                          |
| `level`                          | TEXT NOT NULL DEFAULT 'OK' | `OK` or `HARD_STOP`; legacy rows may hold `WARN`/`SOFT_STOP`, normalised to `OK` on read |
| `updated_at`                     | TEXT NOT NULL              | ISO 8601 UTC                                                                             |
| `reason`                         | TEXT                       | snapshot at last transition                                                              |
| `condition`                      | TEXT                       | last condition that drove a transition                                                   |
| `consecutive_tool_failures`      | INTEGER NOT NULL DEFAULT 0 | rolling counter                                                                          |
| `tool_failure_window_started_at` | TEXT                       | ISO; NULL when no streak                                                                 |
| `refusal_count`                  | INTEGER NOT NULL DEFAULT 0 | rolling counter                                                                          |
| `refusal_window_started_at`      | TEXT                       | ISO                                                                                      |
| `cost_cents_today`               | INTEGER NOT NULL DEFAULT 0 | resets on UTC day rollover                                                               |
| `cost_date`                      | TEXT                       | YYYY-MM-DD                                                                               |

Index: `idx_sticky_stop_active` on `updated_at DESC WHERE level != 'OK'`,
for the dashboard's "is anything stuck?" indicator.

## Verification

`operator/safety-substrate/tests/test_sticky_stop.py` exercises:

- Initial state is `OK` and read alone does not persist
- Three of the four conditions drive `OK -> HARD_STOP` at their threshold,
  and stay `OK` at every count below it
- Tool success resets the failure counter
- Tool success does NOT downgrade level (forward-only invariant)
- Failures outside the rolling window reset the streak to 1
- Time-budget overrun leaves the level `OK`, leaves `condition` unset on a
  healthy seat, and still writes its audit row (`sticky_stop_transition:
false`, `level_unchanged_by_design: true`)
- Cost threshold stays `OK` at 80% and at 100% of the cap, stops at 200%,
  and the daily counter resets on UTC day rollover while the level does not
- Negative cost amounts raise `ValueError`
- State is forward-only — autonomous downgrade is impossible
- Dispatch guard `assert_allowed()` passes through one count below the stop
  threshold and raises `StickyStopError` at HARD_STOP
- Captain `clear()` resets level to OK, zeros counters, and emits an
  `AGENT_RESUMED` audit row carrying the prior state and clear reason
- `clear()` requires non-empty captain_id and reason
- Integration: each of the four conditions, run end-to-end, produces a
  visible audit-log row tagged with the right `condition_triggered`
- Restart resilience: opening a fresh `SqliteStickyStopStore` over the
  same connection returns the persisted state intact

Run locally:

```
cd operator && uv run --with pytest python -m pytest safety-substrate/tests/test_sticky_stop.py -v
```

## Failure modes

- **Audit write fails during a transition.** The `AuditWriteError`
  propagates out of the `record_*` method. The state row WAS persisted
  before the audit call. This means the runtime can end up with a state
  change that has no audit trail. The acceptable mitigation: the caller
  (Hermes dispatch) treats `AuditWriteError` as fatal — same rule as the
  audit log writer itself — and the boot-check picks up the stale state
  on restart. A future refinement may wrap the two writes in a
  best-effort transactional shape, but D1's HTTP API does not support
  multi-statement transactions across audit-log and sticky-stop-state
  tables today.
- **D1 unavailable.** `get_state()` fails with the underlying executor's
  exception. The dispatch guard then cannot determine state. The caller
  must treat this as "stop" (fail-closed): if you cannot read the state,
  you cannot dispatch.
- **Threshold misconfiguration.** Customer.yaml ships a `cost_daily_cents`
  of zero. The cost-threshold check short-circuits to OK (division-by-zero
  guard). This is a configuration error, not a runtime error; the customer
  has effectively disabled the cost dimension.
- **Cost rollover skew.** The day boundary uses UTC. Customers in non-UTC
  time zones see the daily reset at non-midnight local time. This is the
  same convention as `cost_telemetry.date` (per cost-telemetry-events.md)
  and is acceptable; the spec calls out the dimension explicitly.

## Implementation notes

- Module: `operator/safety-substrate/sticky_stop.py`
- Migration: `operator/migrations/0004_sticky_stop_state.sql`
- Tests: `operator/safety-substrate/tests/test_sticky_stop.py`
- The module follows the same pure-Python + injectable-store shape as
  `audit_log.py`. Production wiring uses a D1 HTTP executor (when the
  Hermes-side `HttpD1StickyStopStore` lands; not in this PR).
- The state machine is import-callable from any dispatch path. There is
  no global state, no module-level singleton — callers construct a
  `StickyStopMachine` from a store and an audit writer.
- Clock is injectable for deterministic tests via the `clock` constructor
  kwarg.
- The `consecutive_tool_failures` and `refusal_count` counters are
  persisted in the row (not in process memory) so the machine survives
  Hermes restarts without losing failure history per safety invariant #4.

## Cross-references

- platform-prd.md §7.5 (safety substrate invariants), §11.5 (sticky stop)
- d1-schema.md §1 (audit_log action types)
- customer-yaml-schema.md (future `safety.sticky_stop` block)
- compliance-evidence-packet.md (compliance packet renders the sticky-stop
  section from `metadata.sticky_stop_transition` and
  `metadata.sticky_stop_cleared` audit rows)
- cost-telemetry-events.md (cost source feeding `record_cost_cents()`)
- ADR 0008 (customer-owned memory artifact)
- ADR 0009 (cross-machine query prohibition)
- PR #942 (audit log persistence) — provides `AuditLogWriter`
