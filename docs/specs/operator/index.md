# Operator — Technical Specs

Formal specs for the operator platform. Each spec is the implementation contract for one P0/P1 issue from the [PRD critique batch](https://github.com/venturecrane/ss-console/pull/813).

Build agents consuming these specs should treat each spec as the **implementation contract** for its area.

## P0 — Phase 1 blockers (build cannot start without these)

| Spec                                               | Issue                                                         | Scope                                                                                                 |
| -------------------------------------------------- | ------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| [customer-yaml-schema.md](customer-yaml-schema.md) | [#790](https://github.com/venturecrane/ss-console/issues/790) | Formal schema, secret-exclusion enforcement, pre-commit validation hook                               |
| [capability-contracts.md](capability-contracts.md) | [#791](https://github.com/venturecrane/ss-console/issues/791) | TypeScript signatures for all 11 capability interfaces; send as a configurable entitlement (ADR 0035) |
| [oauth-lifecycle.md](oauth-lifecycle.md)           | [#789](https://github.com/venturecrane/ss-console/issues/789) | Token storage, refresh, failure handling, re-authorization, per-connector scopes                      |
| [dashboard-roles.md](dashboard-roles.md)           | [#788](https://github.com/venturecrane/ss-console/issues/788) | Principal + Operator + Compliance role schema; permission matrix                                      |

## P1 — Beta-1 dependencies

| Spec                                                           | Issue                                                                                                                        | Scope                                                                                                                       |
| -------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| [d1-schema.md](d1-schema.md)                                   | [#800](https://github.com/venturecrane/ss-console/issues/800)                                                                | 11 D1 tables; per-customer isolation via binding layer                                                                      |
| [r2-vectorize-naming.md](r2-vectorize-naming.md)               | [#801](https://github.com/venturecrane/ss-console/issues/801)                                                                | Per-customer R2 + Vectorize naming; invariant #7 boot-check                                                                 |
| [voice-gate-fallback.md](voice-gate-fallback.md)               | [#797](https://github.com/venturecrane/ss-console/issues/797)                                                                | Pass / Near-pass / Fail states; internal-drafts-only mode                                                                   |
| [fabrication-filter.md](fabrication-filter.md)                 | [#798](https://github.com/venturecrane/ss-console/issues/798)                                                                | Invariant #8 as runtime pre-output filter; `client_facing_fields` skill anatomy                                             |
| [compliance-evidence-packet.md](compliance-evidence-packet.md) | [#802](https://github.com/venturecrane/ss-console/issues/802)                                                                | Susan-readable compliance packet contents                                                                                   |
| [cost-telemetry-events.md](cost-telemetry-events.md)           | [#804](https://github.com/venturecrane/ss-console/issues/804)                                                                | Per-customer cost emission for all 9+ drivers                                                                               |
| [cost-attribution-rollup.md](cost-attribution-rollup.md)       | [#884](https://github.com/venturecrane/ss-console/issues/884)                                                                | Per-customer monthly rollup over cost_telemetry; nine category buckets; §17.1 COGS/MRR ratio computation                    |
| [decommission-customer.md](decommission-customer.md)           | [#820](https://github.com/venturecrane/ss-console/issues/820), [#805](https://github.com/venturecrane/ss-console/issues/805) | Full per-customer off-boarding pipeline; 9 idempotent steps; includes the 60s in-flight drain gate                          |
| [sticky-stop.md](sticky-stop.md)                               | [#843](https://github.com/venturecrane/ss-console/issues/843)                                                                | System-initiated circuit breaker for runaway agent loops (WARN/SOFT/HARD)                                                   |
| [safety-invariants.md](safety-invariants.md)                   | [#865](https://github.com/venturecrane/ss-console/issues/865)                                                                | Invariants #6 (citation enforcement on fact-bearing fields) and #7 (cross-Machine query prohibition)                        |
| [refusal-handling.md](refusal-handling.md)                     | [#866](https://github.com/venturecrane/ss-console/issues/866)                                                                | Runtime semantics when `trust_ceiling.enforce()` returns `refuse` (abort + notification + cascade alert)                    |
| [calibration-session.md](calibration-session.md)               | [#867](https://github.com/venturecrane/ss-console/issues/867)                                                                | Four 90-minute calibration sessions over two weeks; portal surface + integration seams                                      |
| [audit-log-immutability.md](audit-log-immutability.md)         | [#892](https://github.com/venturecrane/ss-console/issues/892)                                                                | Worker-layer enforcement, Logpush mirror protocol, integrity check, Captain exception process                               |
| [aie-adapter-register.md](aie-adapter-register.md)             | [#841](https://github.com/venturecrane/ss-console/issues/841)                                                                | Adapter-side hook surface for the SMD overlay on Hermes; pre/post/refusal/compaction hooks                                  |
| [audit-emit-points.md](audit-emit-points.md)                   | [#842](https://github.com/venturecrane/ss-console/issues/842)                                                                | Per-tool action-class registry, latency timer, scope-aware metadata for the post-tool audit emission                        |
| [no-pm-system-mode.md](no-pm-system-mode.md)                   | [#853](https://github.com/venturecrane/ss-console/issues/853)                                                                | Customer.yaml + capability bindings for customers without an external practice-management vendor                            |
| [connector-smoke-tests.md](connector-smoke-tests.md)           | [#852](https://github.com/venturecrane/ss-console/issues/852)                                                                | Per-connector read-only smoke probes at provisioning + periodic; read-only allowlist; pass/partial/fail rollup              |
| [audit-retention.md](audit-retention.md)                       | [#893](https://github.com/venturecrane/ss-console/issues/893)                                                                | Per-vertical audit-log retention defaults; customer.yaml override-up-only; decommission carve-out preserves audit log       |
| [email-channel-seam.md](email-channel-seam.md)                 | [#1978](https://github.com/venturecrane/ss-console/issues/1978)                                                              | ADR 0078 build spec: provider-neutral email seam, delta-poll inbound, msgraph-mail connector, normalized InboundMessage DTO |

## Routines designed, build gated on client acceptance

| Spec                                                     | Status                                                     | Scope                                                                                                                                 |
| -------------------------------------------------------- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| [demand-drafting-routine.md](demand-drafting-routine.md) | Design; first firm's terms decided 09-24, not yet accepted | On-request time-limited demand as a queued seat job: premise preflight, streamed drafting, filing with read-back; the low-limit watch |

## Open ambiguities requiring Captain decision

These were flagged as `[AMBIGUITY: ...]` markers in the specs. Listed here for triage:

1. ~~**OAuth token storage convergence**~~ — **Resolved 2026-05-23 ([ADR 0010](../adr/0010-per-customer-oauth-token-storage.md)):** Fly-volume only at `/opt/data/oauth/{connector}.json`, never Infisical. Spec [`oauth-lifecycle.md`](oauth-lifecycle.md) updated to match (consent state moved to `audit_log` table).
2. ~~**TypeScript vs Python adapter layer**~~ — **Resolved 2026-05-23:** TypeScript signatures at `operator/capabilities/<name>.ts` remain the doctrinal contract; concrete adapters stay in Python and re-declare interfaces via `typing.Protocol`. No TS-adapter migration. See [`capability-contracts.md`](capability-contracts.md) §Resolved decisions.
3. ~~**Calendar RSVP draft pattern**~~ — **Resolved 2026-05-23; amended 2026-06-08 (ADR 0035):** `DraftRef` shape is correct for the **authored** draft-for-review posture (adapter returns a draft; partner taps Accept/Decline; dashboard fires the API call). Under an authored autonomous `EXTERNAL_SEND` ceiling the agent responds directly — the modality is configured per engagement, not fixed by Pattern A. See [`capability-contracts.md`](capability-contracts.md) §"Send is a configurable entitlement".
4. ~~**Re-consent callback URL**~~ — **Resolved 2026-05-23:** Portal subdomain (`portal.smd.services/operator/oauth/{connector}/callback`). Customer-facing flows belong on portal; admin stays role-gated. See [`oauth-lifecycle.md`](oauth-lifecycle.md) §Resolved decisions.
5. **D1 audit-log immutability** (d1-schema.md) — Cloudflare D1 lacks per-role permissions; immutability enforced at Worker layer. **Resolved 2026-05-21 (#892):** Worker-layer enforcement (`D1Executor` wrapper in `operator/adapter/audit_log_immutability.py`) + Logpush mirror protocol + periodic integrity check; Captain-supervised redaction is the only legitimate mutation path. See [audit-log-immutability.md](audit-log-immutability.md).
6. **Vectorize index quota** (r2-vectorize-naming.md) — Wrangler/CF bulk-delete throttling may stretch heavy-customer decommissioning past the 60s drain window. Validate with synthetic fixture before launch.
7. ~~**Voice-gate judge pool size**~~ — **Resolved 2026-05-23:** Captain proxies for missing judges; audit log records `judge_panel: {N}_customer_chosen + {3-N}_captain_proxy`. Relaxed-threshold fallback only when Captain is unavailable. See [`voice-gate-fallback.md`](voice-gate-fallback.md) §Resolved decision.
8. **Internal-drafts-only retainer math** (voice-gate-fallback.md) — Pricing strategy doc doesn't yet specify; spec assumes 50-60%. Resolve before customer #1 signs.
9. **Fabrication filter block-rate ceiling** (fabrication-filter.md) — 5% heuristic; tune against real data.
10. **Compliance packet narrative review cadence** (compliance-evidence-packet.md) — Auto-generate with Captain review-and-amend, or fully manual? Decision: auto-render then Captain edits before delivery.
11. ~~**D1 metering access pattern**~~ — **Resolved 2026-05-23:** Plan around Cloudflare GraphQL Analytics; validation spike is first step of #824 work. Fallback: defer D1 cost-driver instrumentation to phase 2 if validation fails (Anthropic API tokens dominate COGS; D1 not kill-criterion-driving in v1). See [`cost-telemetry-events.md`](cost-telemetry-events.md) §Resolved decisions.
12. **Atomic-wipe decommissioning** (decommission-customer.md) — True atomicity impossible across independent APIs; spec settles for ordered-best-effort. Confirm satisfies §13.3.
13. **Decommission-archive cleanup verification** (decommission-customer.md) — Captain-signed deletion proof at 30 days needed.

## Cross-spec references

The specs link `[[name]]` style across each other:

```
customer-yaml-schema ─► oauth-lifecycle       (token_ref pattern)
customer-yaml-schema ─► dashboard-roles       (users[] block)
customer-yaml-schema ─► r2-vectorize-naming   (memory.* fields)

capability-contracts ─► oauth-lifecycle       (auth_expired error path)
capability-contracts ─► fabrication-filter    (empty-state on forbidden)

d1-schema            ─► all telemetry/audit specs
r2-vectorize-naming  ─► decommission-customer    (prefix enumeration)
r2-vectorize-naming  ─► compliance-evidence-packet (audit-exports path)


fabrication-filter   ─► compliance-evidence-packet (no PII in markers)


cost-telemetry       ─► d1-schema             (cost_telemetry table)
cost-telemetry       ─► decommission-customer    (final rollup before D1 delete)

cost-attribution-rollup ─► cost-telemetry-events (row source the rollup reads)
cost-attribution-rollup ─► d1-schema             (cost_telemetry + captain_time_events)
cost-attribution-rollup ─► decommission-customer    (final cost export before D1 delete)

decommission-customer   ─► compliance-evidence-packet (final export contents)
decommission-customer   ─► r2-vectorize-naming   (retention bucket)
```
