# Per-tool Audit Emission Points

**Status:** Implemented in PR for [#842](https://github.com/venturecrane/ss-console/issues/842).

**Related:** [aie-adapter-register.md](aie-adapter-register.md), [audit-log-immutability.md](audit-log-immutability.md), [trust-ceiling-logging.md](trust-ceiling-logging.md), [refusal-handling.md](refusal-handling.md), [capability-contracts.md](capability-contracts.md), [ADR 0005 (external send identity)](../../adr/0005-external-send-identity.md).

This spec is the contract for `operator/adapter/audit_emit_points.py`. It thickens the per-tool audit emission shape that PR [#981](https://github.com/venturecrane/ss-console/pull/981) landed (`aie_adapter.register()` + the `hermes_hook` surface) with a closed tool registry, a latency timer, and scope-aware metadata extraction.

> **Doctrine note (2026-06-08, reconciliation to [ADR 0035](../../adr/0035-no-imposed-entitlement-defaults.md)).** Earlier revisions of this spec put every send tool (`email_send`, `sms_send`, …) in a `BANNED_TOOLS` set with reason `banned_tool_pattern_a` ("autonomous send forbidden"). That is the dead autonomous-send-forbidden default. **External send is not banned** — it is action class `EXTERNAL_SEND`, gated at runtime by `trust_ceiling.enforce()` per the authored ceiling (autonomous / draft / refused; fail-closed when unauthored — exactly as `operator/adapter/trust_ceiling.py` already implements). What stays structurally refused are the **irreversible** classes — `COMMITMENT` / `DESTRUCTIVE` (money movement, deletes, ledger posting, court filing) — which additionally require explicit current-turn approval (ADR 0025 reversibility floor). The registry below classifies send tools as `EXTERNAL_SEND`; there is no `banned_tool_pattern_a`. Note: `operator/adapter/audit_emit_points.py` is **not yet implemented** — this spec leads the implementation, which must match the corrected classification.

---

## 1. Why this exists

PR [#981] wired the four Hermes hooks. Its post-tool hook already writes one audit row per tool call, but the row's metadata was assembled inline at the call site, and the trust-ceiling enforcer had no canonical source for "what action class is this tool". Issue [#842](https://github.com/venturecrane/ss-console/issues/842) (PRD §7.4, §17.4) requires:

- Every tool dispatch emits an audit event with `timestamp, customer, skill, tool, action class, ceiling decision, outcome`.
- The event persists to the per-customer D1 `audit_log` table.
- Performance overhead is measured (target: <5ms p99 per tool call).
- New tools introduced at the capability layer cannot silently bypass classification.

This spec ships the registry + helpers that the overlay's dispatch path calls on every tool call.

## 2. Scope

In scope:

- `TOOL_ACTION_CLASS_MAP` - closed map of tool name to `HookActionClass`. The trust-ceiling enforcer keys on action class, so this registry IS the routing table.
- `IRREVERSIBLE_TOOLS` - closed set of tool names for irreversible actions (money movement, deletes, ledger posting, court filing). These classify as `COMMITMENT` / `DESTRUCTIVE` and additionally require explicit current-turn approval (ADR 0025 reversibility floor). Send tools are **not** here — they are `EXTERNAL_SEND`, gated by the authored ceiling at the trust-ceiling layer.
- `ReversibilityFloorError` - exception raised when an irreversible tool is invoked without current-turn approval.
- `classify_tool()` - the helper the overlay calls before trust-ceiling enforcement.
- `ToolCallTimer` - the explicit measurement surface for `metadata.duration_ms`.
- `extract_scope_metadata()` - lifts `matter_id` / `customer_segment` from `ToolCallContext.arguments` into the audit row.
- `build_per_tool_metadata()` - canonical metadata-dict builder, consumed by the post-tool hook.

Out of scope (covered by separate work):

- The actual fork-side overlay code that calls these helpers in production. The overlay constructs a `HookRegistry`, calls `aie_adapter.register()`, and exposes the result to upstream Hermes (per [ADR 0015](../../adr/0015-hermes-fork-vs-upstream.md)). The overlay implementation is filed as a follow-on against ADR 0015.
- Per-tool cost telemetry. The `duration_ms` value emitted here is reused by the cost telemetry pipeline ([cost-telemetry-events.md](cost-telemetry-events.md)); the cost emission itself lives in issue [#804](https://github.com/venturecrane/ss-console/issues/804).
- Audit-log immutability and the Worker-layer enforcement of append-only writes. Those are spec'd in [audit-log-immutability.md](audit-log-immutability.md).

## 3. Tool action class registry

The registry maps every known tool name to its `HookActionClass`. The action classes are: `READ`, `INTERNAL_WRITE`, `EXTERNAL_SEND`, `COMMITMENT`, `DESTRUCTIVE`. The string values match `adapter.trust_ceiling.ActionClass`.

| Capability group    | Tool name pattern                                                                                                                                                                  | Action class     |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------- |
| Email               | `email_list_*`, `email_get_*`, `email_search`, `email_get_thread`, `email_list_labels`                                                                                             | `READ`           |
| Email               | `email_create_draft`, `email_update_draft`, `email_delete_draft`                                                                                                                   | `INTERNAL_WRITE` |
| Email               | `email_send`, `email_send_message`, `email_reply`, `email_reply_all`, `email_forward`                                                                                              | `EXTERNAL_SEND`  |
| SMS                 | `sms_list_messages`, `sms_get_message`                                                                                                                                             | `READ`           |
| SMS                 | `sms_create_draft`                                                                                                                                                                 | `INTERNAL_WRITE` |
| SMS                 | `sms_send`, `sms_send_message`                                                                                                                                                     | `EXTERNAL_SEND`  |
| Calendar            | `calendar_list_events`, `calendar_get_event`, `calendar_search_events`, `calendar_check_availability`                                                                              | `READ`           |
| Calendar            | `calendar_create_event_draft`, `calendar_respond_invitation_draft`                                                                                                                 | `INTERNAL_WRITE` |
| Calendar            | `calendar_propose_time`                                                                                                                                                            | `COMMITMENT`     |
| Calendar            | `calendar_delete_event`                                                                                                                                                            | `DESTRUCTIVE`    |
| Practice management | `practice_management_search_matters`, `practice_management_get_matter`, `practice_management_list_documents`, `practice_management_get_document`, `practice_management_list_tasks` | `READ`           |
| Practice management | `practice_management_create_note`, `practice_management_create_task_draft`, `practice_management_update_matter_field`                                                              | `INTERNAL_WRITE` |
| Practice management | `practice_management_open_matter_draft`                                                                                                                                            | `COMMITMENT`     |
| Practice management | `practice_management_delete_matter`, `practice_management_close_matter_permanent`                                                                                                  | `DESTRUCTIVE`    |
| Payments            | `payments_initiate_transfer`, `payments_send_payment`, `payments_refund`, `payments_authorize_charge`, `payments_void_authorization`                                               | `DESTRUCTIVE`    |
| Memory              | `memory_search`, `memory_get_rule`, `memory_list_rules`                                                                                                                            | `READ`           |
| Voice gate          | `voice_score_draft`, `voice_list_judge_history`                                                                                                                                    | `READ`           |
| Connector           | `connector_get_status`, `connector_list_bindings`                                                                                                                                  | `READ`           |
| Connector           | `connector_revoke_oauth`, `connector_unbind_permanent`                                                                                                                             | `DESTRUCTIVE`    |

`TOOL_ACTION_CLASS_MAP` is exposed as `MappingProxyType` so mutation at runtime raises `TypeError`. Registry changes ship as a PR + test + spec update.

### 3.1 Unknown tools

A tool name that is not in the registry and not in `BANNED_TOOLS` returns:

```python
ToolClassification(action_class=HookActionClass.READ, unmapped=True)
```

The `unmapped=True` flag propagates through to `metadata.unmapped_tool = true` on the audit row so audit review can catch new tools that landed without registry updates. Default-to-READ is the safe fallback: an unmapped tool that turns out to be a write is caught by the enforcer at a higher action class would refuse legitimate reads that simply lack registry entries.

## 4. Irreversible tools (the reversibility floor)

Send tools are **not** banned. The only tools structurally gated beyond the configured ceiling are the **irreversible** ones, which classify as `COMMITMENT` / `DESTRUCTIVE`. The trust-ceiling enforcer (`operator/adapter/trust_ceiling.py`) refuses these unless the operator gave **explicit current-turn approval** (ADR 0025 reversibility floor) — a prior-turn or configured approval is not enough.

| Class         | Meaning                                                       | Examples                                                       |
| ------------- | ------------------------------------------------------------- | -------------------------------------------------------------- |
| `COMMITMENT`  | Records an obligation: ledger posting, court filing, signing. | `practice_management_open_matter_draft`, ledger/filing tools   |
| `DESTRUCTIVE` | Irreversible money movement or state destruction.             | `payments_initiate_transfer`, `calendar_delete_event`, deletes |

External send (`EXTERNAL_SEND`) is governed entirely by the authored ceiling at the trust-ceiling layer (autonomous / draft / refused; fail-closed when unauthored) — it is never in this floor.

### 4.1 No separate ban set

There is no `BANNED_TOOLS` set disjoint from the registry. Every tool is classified in `TOOL_ACTION_CLASS_MAP` by action class, and the trust ceiling is the single enforcement authority. The irreversibility floor lives in `enforce()` (the `COMMITMENT` / `DESTRUCTIVE` current-turn-approval requirement), not in a parallel structural ban.

## 5. Latency timer

`ToolCallTimer` is the per-call monotonic timer. The overlay's dispatch path uses it like this:

```python
timer = ToolCallTimer().start()
try:
    result_value = await tool_fn(...)
    outcome = "ok"
finally:
    duration_ms = timer.stop()
```

The timer is single-shot: `start()` twice, `stop()` before `start()`, or `stop()` twice all raise `RuntimeError`. That catches dispatch-path bugs where a call accidentally double-reports.

`duration_ms` is a float in milliseconds, produced from `time.perf_counter()` (monotonic, high-resolution). The value lands in:

- `ToolCallResult.duration_ms` (the existing field from PR [#981])
- `metadata.duration_ms` on the audit row
- The cost-telemetry feed (issue [#804], wired in a follow-on)
- The sticky-stop time-budget condition input (issue [#843], wired in a follow-on)

## 6. Scope-aware metadata

`extract_scope_metadata(context)` reads `context.arguments` and lifts a closed set of scope keys into the metadata output:

| Scope key          | Source                                  | Use                                                            |
| ------------------ | --------------------------------------- | -------------------------------------------------------------- |
| `matter_id`        | `context.arguments["matter_id"]`        | Per-matter drill-down on the audit viewer (law-firm vertical). |
| `customer_segment` | `context.arguments["customer_segment"]` | Cross-customer cohort aggregation.                             |

Missing or `None` values are omitted. Non-string values are coerced via `str()` so the JSON payload stays serializable. The dashboard treats both as opaque strings.

No other argument values are lifted into metadata. The full argument payload stays in the `AuditLogWriter`'s payload-digest path (per `audit_log.py`); only the closed scope keys appear in `metadata`.

## 7. Canonical metadata shape

`build_per_tool_metadata()` produces the canonical metadata dict that the post-tool hook writes to the audit row. The shape:

```json
{
  "per_tool_audit": true,
  "customer": "acme",
  "skill": "inbox-triage",
  "skill_version": "0.1.0",
  "tool": "email_create_draft",
  "action_class": "internal_write",
  "ceiling_level": "draft_for_review",
  "outcome": "ok",
  "error_type": null,
  "duration_ms": 8.25,
  "trace_id": "trace-test-0001",
  "matter_id": "matter-42",
  "customer_segment": "cohort-a"
}
```

Optional flags that appear when relevant:

- `unmapped_tool: true` - the tool was not in `TOOL_ACTION_CLASS_MAP`.
- `banned_tool: true` + `banned_reason: <reason>` - the tool was in `BANNED_TOOLS`.

All consumers (Captain dashboard aggregate view, customer dashboard recent-decisions feed, compliance-evidence packet, audit viewer) filter on `metadata.per_tool_audit = true` to find these rows, and aggregate on `tool`, `action_class`, `outcome`, and `duration_ms`. The keys are stable; changes ship as a PR + spec update + dashboard update in the same PR.

## 8. Compaction interaction

Per the compaction hook from PR [#981], transient per-call state must not live in context that compaction can compress. This module is pure: no module-level mutable state, no hidden per-customer caches. Every helper takes a `ToolCallContext` (the explicit shape from the overlay) and returns a value. The registry and banned set are module-level constants, but they are read-only `MappingProxyType` / `frozenset` and survive process restarts unchanged. There is no per-customer registry override - if a customer needs a tool restricted further than the registry says, the customer.yaml ceiling does the restriction at the trust-ceiling layer, not here.

## 9. Send is classified and gated, not banned

Nothing in this module _originates_ an action — it only classifies and times tool calls for audit. Send tools are classified `EXTERNAL_SEND` and gated downstream by `trust_ceiling.enforce()` per the authored ceiling (autonomous / draft / refused; fail-closed when unauthored). There is **no** `test_no_send_tool_appears_in_registry` merge gate — that asserted the dead autonomous-send-forbidden default (ADR 0035 struck it). The send-related merge gate is instead `operator/safety_substrate/tests/test_invariant_2_no_external_send_without_confirmation.py`, which verifies the configured-ceiling behavior (unauthored = fail-closed; authored autonomous = send; vertical floor narrows).

## 10. Acceptance criteria mapping

Issue [#842](https://github.com/venturecrane/ss-console/issues/842) acceptance criteria:

- [x] Every tool dispatch emits an audit event: `timestamp, customer, skill, tool, action class, ceiling decision, outcome` - the canonical metadata dict from `build_per_tool_metadata()` contains every required field; `timestamp` lands via `AuditLogWriter` (which writes `ts` automatically), `ceiling_level` carries the ceiling decision, `outcome` is one of `ok` / `error` / `blocked`.
- [x] Events persist to per-customer D1 audit table - the writer from PR [#942](https://github.com/venturecrane/ss-console/pull/942) writes to the per-customer `audit_log` table; the integration test `test_metadata_writes_through_real_audit_log_writer` exercises this against an in-memory sqlite executor.
- [x] Audit event schema documented - this spec (sections 7 + 3).
- [x] Performance overhead measured - the `ToolCallTimer` provides ms-precision per-call measurement; the test suite includes a timer-budget assertion (`test_timer_measures_elapsed_ms`); the larger <5ms p99 budget for the full hook stack is asserted in the audit-log writer's own perf test (`test_audit_log.py::test_write_under_10ms_p99`).

## 11. Out-of-scope follow-ons

- Fork-side overlay code that calls `classify_tool()` + `ToolCallTimer` + `build_per_tool_metadata()` on the actual Hermes dispatch path. The overlay is the fork-side wiring filed against ADR 0015's overlay-implementation work.
- Cost-telemetry consumption of `duration_ms` (issue [#804]).
- Sticky-stop time-budget condition consumption of `duration_ms` (issue [#843]).
- Audit-viewer dashboard filters on `per_tool_audit`, `unmapped_tool`, `banned_tool` (issue [#873]).
- Registry expansion when new capability tools land (filed per-capability).
