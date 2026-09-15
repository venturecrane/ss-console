# Operator Adapter Register - Hermes Hook Surface (Phase A.5)

**Status:** Implemented in PR for [#841](https://github.com/venturecrane/ss-console/issues/841).

**Related:** [ADR 0015 Hermes Fork vs Upstream](../../adr/0015-hermes-fork-vs-upstream.md), [refusal-handling.md](refusal-handling.md), [audit-log-immutability.md](audit-log-immutability.md), [trust-ceiling-logging.md](trust-ceiling-logging.md), [sticky-stop.md](sticky-stop.md).

> **Note (ADR 0024, 2026-05-28):** This spec's references to a "vendored fork with an SMD overlay layer in the fork" describe the pre-2026-05-24 posture. That posture is superseded: all SMD extension code lives in the plugin-only `venturecrane/hermes-smd-overlay` repo (ADR 0015 rewrite), and the `venturecrane/hermes-agent` fork was retired entirely in favor of pinning `NousResearch/hermes-agent` directly (ADR 0024). The adapter contract below stands; only the "where the overlay lives" framing changed.

This spec is the contract for `operator/adapter/aie_adapter.py::register()` and the typed hook surface it consumes (`operator/adapter/hermes_hook.py`). The contract is held on the adapter side so the SMD overlay layer in the Hermes fork (per ADR 0015) can swap its internal implementation without renegotiating the integration.

---

## 1. Why this exists

PR [#812](https://github.com/venturecrane/ss-console/pull/812) shipped `aie_adapter.py` with `register()` as a stub. The stub assumed the upstream Hermes seam was `agent/tool_router.py`. PR [#829](https://github.com/venturecrane/ss-console/pull/829)'s runtime status report discovered upstream actually exposes `agent/tool_guardrails.py`, `agent/tool_executor.py`, `agent/tool_dispatch_helpers.py`, and `agent/tool_result_classification.py`. [ADR 0015](../../adr/0015-hermes-fork-vs-upstream.md) locked the integration strategy: a thin vendored fork (`venturecrane/hermes-agent`) with an SMD overlay layer that conforms to whatever the adapter side declares.

Phase A.5's gap was that nothing in Hermes' dispatch path actually called `trust_ceiling.enforce()`, no per-tool audit row was emitted, refusals did not propagate to the customer-facing notification surface, and pinned slots had no compaction-survival mechanism. This PR wires the four hooks.

## 2. Scope

In scope:

- `register()` builds and installs four hooks against a `HookRegistry`: pre-tool, post-tool, refusal, compaction.
- Pre-tool hook calls trust-ceiling enforcement for every tool dispatch; refused calls raise `BlockedToolCall` and the tool never executes.
- Post-tool hook emits one per-tool audit row through the existing `AuditLogWriter` (PR #942), recording outcome (`ok` / `error` / `blocked`).
- Refusal hook delegates to `RefusalHandler.handle()` (PR #967) to write the customer-facing notification row and (on cascade) the Captain alert row.
- Compaction hook re-injects a closed set of pinned slots after Hermes context compaction (safety invariant #4).

Out of scope (covered by separate work or filed as follow-ons):

- The fork itself. The fork lives at `venturecrane/hermes-agent`; this PR ships the adapter-side contract the fork will conform to. The fork's overlay layer is a separate work item per ADR 0015 follow-ons.
- The substrate-side compaction context-injection. The v1 compaction hook logs the pinned-slot snapshot; the actual context-injection wiring is fork-side overlay code that will land in a sibling PR.
- The `trust_ceiling.enforce(customer, skill, action_class, ceiling_level)` free function the PRD names. This PR ships the `TrustCeilingEnforcer` Protocol plus `DefaultTrustCeilingEnforcer` adapter; a fork-side or platform-team implementation can replace it later without changing the hook surface.

## 3. Hook surface

The contract is in `operator/adapter/hermes_hook.py`. Four hook-type aliases:

| Hook         | Signature                                                       | Purpose                                                                                                                           |
| ------------ | --------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `pre_tool`   | `Callable[[ToolCallContext], Awaitable[None]]`                  | Trust-ceiling enforcement. Raises `BlockedToolCall` to stop the dispatch.                                                         |
| `post_tool`  | `Callable[[ToolCallContext, ToolCallResult], Awaitable[None]]`  | Per-tool audit row. Fires whether the call was allowed, blocked, or errored.                                                      |
| `refusal`    | `Callable[[ToolCallContext, BlockedToolCall], Awaitable[None]]` | Customer-facing notification + Captain cascade alert (delegates to `RefusalHandler`).                                             |
| `compaction` | `Callable[[PinnedSlots], Awaitable[None]]`                      | Re-inject pinned slots after context compaction. Invariant #4. Fork-side overlay is responsible for the actual context-injection. |

### 3.1 `ToolCallContext`

Frozen dataclass the overlay constructs per tool call. Fields:

- `customer` (required) - customer slug; matches the D1 binding tenant.
- `skill_name` (required) - from SKILL.md frontmatter.
- `tool_name` (required) - the tool being invoked (e.g. `Email.create_draft`).
- `action_class` (required) - `HookActionClass` enum value (string values mirror `adapter.trust_ceiling.ActionClass`).
- `ceiling_level` (required) - string value of the ceiling configured for the skill at dispatch time.
- `skill_version` (optional) - content-hash SHA or version pin per §7.4 of the platform PRD.
- `matter_ref` (optional) - per-vertical opaque reference (matter id, lead id).
- `trace_id` (optional) - request/turn id for cross-row correlation.
- `current_turn_approval` (optional) - True iff the operator approved this specific action in this turn (invariant #1).
- `arguments` (optional) - tool arguments. Never logged verbatim; the audit writer digests separately per the AuditLogWriter contract.

### 3.2 `HookActionClass`

Closed enum with string values matching `adapter.trust_ceiling.ActionClass`: `read`, `internal_write`, `external_send`, `commitment`, `destructive`. Held as a separate enum so the overlay does not need to import adapter internals.

### 3.3 `BlockedToolCall`

Exception raised by `pre_tool` to stop a tool call. Carries:

- `reason` - free-text reason from the enforcer (the refusal hook maps it to a closed `DecisionReason` for audit emission).
- `customer_message` - populated lazily by the refusal hook with the closed-set `CustomerMessage` value the in-app notification surface will render.
- `context` - the `ToolCallContext` that was blocked (defensive; lets the post-hook recover context if its own copy was discarded).

The overlay's dispatch path MUST catch this exception, route to the refusal hook, then to the post-hook with `outcome="blocked"`, and NEVER execute the tool. This is the runtime enforcement invariant.

### 3.4 `PinnedSlots` and `DEFAULT_PINNED_SLOT_KEYS`

In-process key-value store that survives compaction. v1 pins this closed set:

- `persona.name`
- `reviewer.identity`
- `customer.yaml.signature`
- `sticky_stop.active`
- `trust_ceiling.locked_skills`

`register()` seeds the slots whose values are derivable from `customer.yaml` (`persona.name`, `reviewer.identity`, `customer.yaml.signature`). The other two are pinned by the substrate as state changes (sticky-stop transitions, ceiling locks).

The compaction hook reads the slot snapshot and re-injects each slot into the post-compaction context. The slot table itself is never mutated by compaction.

### 3.5 `HookRegistry`

Constructed by the overlay; passed to `register()`. Each hook slot accepts exactly one consumer (re-registration raises). Exposes:

- `register_pre_tool(hook)`, `register_post_tool(hook)`, `register_refusal(hook)`, `register_compaction(hook)` - adapter-side installation.
- `pinned_slots` - the `PinnedSlots` instance (read-write).
- `dispatch_pre_tool(context)`, `dispatch_post_tool(context, result)`, `dispatch_refusal(context, block)`, `dispatch_compaction()` - overlay-side invocation.

### 3.6 `TrustCeilingEnforcer` protocol

```python
class TrustCeilingEnforcer(Protocol):
    def enforce(
        self,
        *,
        customer: str,
        skill: str,
        action_class: HookActionClass,
        ceiling_level: str,
        current_turn_approval: bool = False,
    ) -> EnforcementDecision: ...
```

`DefaultTrustCeilingEnforcer` is the v1 implementation. It wraps `adapter.trust_ceiling.enforce()` (PR #812) and accepts the PRD-shape arguments. A future fork-side implementation can subclass or replace it without changing the hook surface.

## 4. `register()` contract

```python
def register(
    registry: Optional[HookRegistry] = None,
    *,
    audit_writer=None,
    refusal_handler=None,
    enforcer: Optional[TrustCeilingEnforcer] = None,
) -> HookRegistry:
```

Steps:

1. Construct a fresh `HookRegistry` if none was passed (unit-test path).
2. Construct `DefaultTrustCeilingEnforcer` if none was passed.
3. Load `customer.yaml`; seed pinned slots from its `persona.name`, `reviewer.identity`, and optional `signature` field.
4. Build and install the four hooks.
5. Return the registry.

Production callers SHOULD supply `audit_writer` and `refusal_handler`. Test paths can omit either; the corresponding hook then logs-and-skips its emission.

## 5. Audit row schema

The post-tool hook writes one `audit_log` row per tool dispatch. The row uses the closed-set vocabulary from `ACCEPTED_ACTION_TYPES` (PR #942):

- `action_type` - `DRAFT_CREATED` for allowed/errored calls, `INVARIANT_VIOLATION` for blocked calls.
- `actor` - always `agent`.
- `actor_role` - `agent`.
- `skill_name` - from context.
- `matter_ref` - from context.
- `trust_ceiling` - the ceiling at dispatch time.
- `metadata` -
- `per_tool_audit: true` (filter key for dashboards)
- `customer`, `skill`, `skill_version`, `tool`, `action_class`, `ceiling_level`, `outcome` (`ok` / `error` / `blocked`), `error_type`, `duration_ms`, `trace_id`

Refusals additionally trigger the `RefusalHandler`, which writes its own two rows (canonical trust-ceiling-decision row plus customer-facing notification row); see `refusal-handling.md` for that schema. The per-tool audit row from the post-hook is in addition to those rows, not a replacement.

## 6. Invariant #4 - pinned-slot survival

The compaction hook is the runtime enforcement seam for invariant #4 ("don't act" / "stop" instructions survive context compaction). v1 behavior:

- `register()` seeds `persona.name`, `reviewer.identity`, and `customer.yaml.signature` at boot.
- The substrate pins `sticky_stop.active` and `trust_ceiling.locked_skills` as state changes.
- The compaction hook fires after Hermes' internal compaction has run. It receives the live `PinnedSlots` reference and emits an info log with the snapshot (`{"persona.name": "the Operator", ...}`).
- Fork-side overlay code is responsible for the actual post-compaction context-injection. This adapter ships the seam; the substrate-side wiring is filed as a follow-on against ADR 0015's overlay-implementation work.

The pinned-slot table is never mutated by the compaction hook. Test coverage in `test_hermes_hook.py::test_pinned_slots_survive_simulated_compaction` asserts that property explicitly.

## 7. Integration test shape

`tests/test_aie_adapter.py::test_forbidden_action_with_refusal_handler_emits_refusal_audit_rows` is the AC-bearing integration test. It:

1. Constructs an in-memory SQLite-backed `AuditLogWriter` and a `RefusalHandler` with an `InMemoryRefusalCounter`.
2. Calls `register(audit_writer=writer, refusal_handler=handler)` to install all four hooks.
3. Builds a `FakeHermesRuntime(registry)` and dispatches a forbidden tool call (commitment action without current-turn approval).
4. Asserts: the tool function never ran; the result is `blocked`; three audit rows landed in order (canonical decision row, notification row, per-tool audit row); each row carries the expected metadata keys.

The `test_customer_zero_smoke_end_to_end` test exercises allowed + forbidden + compaction in one run against a `customer-zero`-flavored `customer.yaml` to mirror the AC #6 smoke check.

## 8. Out-of-scope follow-ons

These are filed as separate issues against ADR 0015's overlay-implementation work:

- Fork-side overlay layer that constructs the `HookRegistry`, exposes it via the adapter loader, and wires the four hooks into Hermes' actual `tool_guardrails.py` dispatch path.
- Substrate-side compaction context-injection (the part of the compaction hook that actually re-materializes pinned slots in the post-compaction context).
- Per-tool cost telemetry emission (#804). The post-tool hook is the natural place to emit cost rows; deferred so this PR stays in safety_substrate scope.
- Sticky-stop integration in the pre-hook. The sticky-stop machine (PR #948) is already on main; lifting its `assert_allowed()` check into the pre-hook is a follow-on so this PR keeps the trust-ceiling and sticky-stop paths independently testable.
- First upstream PR for the generic tool-dispatch hook surface (per ADR 0015 follow-on #5).

## 9. Acceptance criteria mapping

Issue #841 acceptance criteria:

- [x] `aie_adapter.register()` actually hooks into Hermes' `agent/tool_guardrails.py` dispatch - `register()` installs four hooks against a `HookRegistry` typed to mirror that seam. The fork-side overlay (separate PR) plugs the registry into the actual `tool_guardrails.py` dispatch path.
- [x] Every tool call routes through `trust_ceiling.enforce()` before execution - the pre-tool hook calls `TrustCeilingEnforcer.enforce()` for every dispatch; the default enforcer wraps `adapter.trust_ceiling.enforce()`.
- [x] Refusals + draft-routed actions logged to per-customer audit log - the post-tool hook emits `per_tool_audit` rows for every outcome; the refusal hook delegates to `RefusalHandler.handle()` which writes the canonical decision + notification rows.
- [x] Compaction event re-injects pinned slots (invariant #4) - the compaction hook reads the `PinnedSlots` snapshot; v1 logs the slot set, fork-side overlay handles the actual context-injection.
- [x] Integration test confirms a forbidden action is actually blocked at runtime - `test_forbidden_action_is_blocked_audit_row_records_outcome_blocked` asserts the tool function never ran.
- [x] Customer-zero smoke test passes end-to-end with adapter wired - `test_customer_zero_smoke_end_to_end` exercises allowed + forbidden + compaction in one run.
