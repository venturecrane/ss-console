---
title: Operator Cost Plane — Central Telemetry, Workspace Attribution, Machine-Local Breaker
date: 2026-07-03
status: accepted
captain: Scott Durgan
related-adr: 0009-cross-machine-query-prohibition.md, 0023-operator-per-customer-observability.md, 0024-hermes-consumption-and-update-cadence.md, 0035-no-imposed-entitlement-defaults.md, 0049-operator-model-selection.md
related-issues: 1659, 1660, 1661
supersedes: the per-customer-D1 storage clause of docs/specs/operator/cost-telemetry-events.md and d1-schema.md (cost tables only)
---

# ADR 0062 — Operator Cost Plane

**Status:** Accepted (Captain direction 2026-07-03: "take 1660 and 1661 as the next wave").

**Source:** The Review 5 unit-economics stress test (2026-07-03) found the cost pipeline dark at every link and the cost circuit breaker inert. Specifics, all verified live: no per-customer D1 database exists anywhere in the Cloudflare account, so the daily cost-telemetry ingest skips 100% of seats; the `/ss` runtime `ANTHROPIC_API_KEY` is rejected by the Anthropic usage-report API (Admin key required); the ingest design writes org-level totals into every seat (no per-seat attribution); `sticky_stop_cost_cap` ($50/day ladder) has zero callers, so per-Machine spend is unbounded; and `services.recurring_price` is NULL fleet-wide, so the locked COGS/MRR kill criterion (>40% for two consecutive months) can never fire. Issues [#1660](https://github.com/venturecrane/ss-console/issues/1660) and [#1661](https://github.com/venturecrane/ss-console/issues/1661); pricing authoring is [#1659](https://github.com/venturecrane/ss-console/issues/1659) (separate Captain decision).

## Context constraints discovered during design

1. **The per-customer-D1 storage premise is already retracted doctrine.** ADR 0009's wiring note states the per-customer Cloudflare D1/R2/Vectorize storage model "was never built"; the live data plane is Machine-local SQLite on the Fly volume (#1355: broker-owned audit ledger, `persona_observations` mirror, `agent_skills_inventory`). No provisioning code ever created a per-customer D1, no `run_migrations.py` exists, and no Machine receives D1 credentials. Cost telemetry was the last live _writer_ designed against that premise.
2. **A central control-plane table is explicitly permitted.** ADR 0009 §Out of scope carves out control-plane cross-customer visibility for "billing reconciliation" and "fleet health"; ADR 0023's `fleet_status` (central table in `ss-console-db`, written by Machines over authenticated HTTP) is the shipped precedent. Cost rows are SMD's own spend metadata, not customer content.
3. **The interactive turn seam has no token counts.** The Hermes `post_llm_call` hook passes `model` but no usage; only the durable-job path (`shared/job_worker_runtime.py` `hermes_segment_cost`) has exact `(tokens, model)` and already computes cents via Hermes-native `agent.usage_pricing`. Exposing usage on the interactive hook is an upstream Hermes change, consumed via the ADR 0024 SHA-pin cadence — not something the plugin overlay can reach around (plugins must not modify core).
4. **The dominant spend driver is interactive.** Live audit data (runtime-read seam, 2026-07-03) shows webhook-routed inbound driving `LLM_TURN_COMPLETED` on the gateway path, not durable jobs. A cost control that only guards the job path does not close the P1 runaway scenario (webhook storm / injected loop).

## Decisions

### 1. Cost telemetry is stored centrally

`cost_telemetry` and `captain_time_events` move to `ss-console`'s central D1 (`env.DB`) with a `customer_slug` tenant column, following the `fleet_status` pattern. The per-customer-D1 placement in `docs/specs/operator/cost-telemetry-events.md`, `d1-schema.md`, and the `operator/migrations/0001`/`0006` prose is superseded for these tables. Justification: ADR 0009 billing-reconciliation carve-out; ADR 0023 precedent; the alternative (provisioning a per-customer D1 fleet, its migration runner, credential plane, and decommission path) services a premise doctrine has already retracted. The per-customer migration set under `operator/migrations/` is historical; no new consumers may be designed against it.

### 2. Per-seat attribution comes from per-customer Anthropic workspaces

Each customer seat gets its own Anthropic **workspace**; the workspace-scoped API key is staged to that Machine as its `ANTHROPIC_API_KEY` at provision time. The daily ingest calls the usage-report API grouped by `workspace_id` and writes per-seat rows to the central table; the org-level total is retained as a reconciliation row (driver `anthropic.org_total`), not an attribution source. This covers **all** spend paths (interactive + jobs) with exact vendor-billed numbers and no seam code. Operational prerequisites (Captain, Anthropic Console): mint an `ANTHROPIC_ADMIN_KEY` (usage-report access) into `/ss`, create one workspace per live seat, rotate each Machine's key to its workspace key. The provisioning runbook gains a "create workspace + key" step.

### 3. The cost breaker persists Machine-local

`sticky_stop` state lives in Machine-local SQLite on the Fly volume (the existing `SqliteStickyStopStore`), consistent with the #1355 data-plane doctrine. The `HttpD1StickyStopStore` named in the module docstring is retired unbuilt — another artifact of the dead per-customer-D1 premise.

### 4. Enforcement is defense-in-depth across three seams (v1)

- **Job/segment path (exact cents):** `record_cost_cents` + `assert_allowed` wired where `segment_cost(agent)` already returns cents (`shared/job_segment.py`). On `StickyStopError` (HARD_STOP), the job dead-letters to `needs_review` — the existing budget-exhaustion pattern — and the `AGENT_STOPPED` audit row sticky_stop already emits is the control-plane signal. At `SOFT_STOP`, exposure pins to draft-for-review per the module's built semantics. **[Amended 2026-09-02: that last sentence was never true in running code — no enforcement arm ever read `SOFT_STOP` to pin a ceiling, and `assert_allowed()` passed straight through it. Rather than build the pin retroactively, the rung was removed along with `WARN`; the ladder is now `OK -> HARD_STOP`. Stop thresholds are unchanged. See `docs/specs/operator/sticky-stop.md`.]**
- **Interactive turns (estimated cents) — added 2026-07-04, #1701:** every interactive turn (webhook-routed inbound + console `/mcp/turn`) is metered at the `post_llm_call` hook (`overlay shared/interactive_cost_meter.py`) and fed the **same** `record_cost_cents` ladder as the job path. Because ADR 0015 forbids patching Hermes core to enrich the hook with exact tokens, the meter estimates each turn's cents locally from the hook's `(model, conversation_history, assistant_response)` — the correct architecture for a real-time safety cap (local, instant, no external call), with the nightly usage-report ingest as the exact reconciliation. It meters only the per-turn **delta** (new content) so a long idle context does not false-trip, and biases conservative so it trips a touch early. On a HARD_STOP transition the gate halts the next interactive driver (inbound parked, `/mcp/turn` + `/webhooks/handoff` 503). **Meter-fail posture (Captain decision):** unmeterable turns (unpriced model, unreadable content) keep going and raise a loud rate-limited `INVARIANT_VIOLATION` alarm — a meter glitch must not freeze the Operator, and never silently passes.
- **Webhook gate (bounds the wake rate):** the gate enforces an authored **inbound daily wake cap** — inbound beyond the cap is acknowledged (202), audited, and **not routed to the agent** (fail-closed park). This is a wake-storm / DoS limiter, **not** the spend control (the interactive cents meter above is); it also refuses to wake the agent while sticky_stop is at `HARD_STOP`.
- **Detection (next-day):** workspace-attributed telemetry + the cost-anomaly worker alert on spend the caps did not explain.

**Amendment (2026-07-04, #1701).** The prior version of this section deferred interactive per-turn cents to "upstream Hermes exposing usage in `post_llm_call`." The live trip-fire probe (ADR 0050 B3) proved that deferral left the _dominant_ spend path with no dollar ceiling while the cents cap guarded the durable-job path, which no live seat entitles. Since ADR 0015 forbids us from enriching the core hook, the doctrine-compliant fix is the local estimate above — now built, not deferred. Exact vendor tokens remain the nightly reconciliation's job by design.

### 5. Caps are authored in customer.yaml; the platform default stands

`safety.sticky_stop.cost_cap_daily_cents` (default 5000 = $50/day, the module's built ladder: warn 80%, soft-stop 100%, hard-stop 200%) and `safety.sticky_stop.inbound_daily_cap` (default 200 routed wakes/day) are authored per seat in `customer.yaml`, read at runtime via the existing `CustomerConfig` volume read — no new env vars, no consumption-contract churn. **ADR 0035 compliance:** these are integrity controls protecting SMD's own spend, in the same class as the taint gate — not client entitlements — so a platform default is doctrine-consistent; unauthored means the default applies, not fail-open.

### 6. The fleet view shows breaker state

The heartbeat payload gains an optional `sticky_stop_level` field (`ok | warn | soft_stop | hard_stop`); the fleet roster escalates the seat's dot on `soft_stop`/`hard_stop`. **[Amended 2026-09-02: a post-collapse seat emits only `ok | hard_stop`, and the payload now also carries `sticky_stop_reason` and `sticky_stop_condition` so the page names what stopped the seat instead of guessing at a cause it never measured. The console still accepts and renders the legacy words, because a seat keeps emitting them until it is reprovisioned.]** Captain reset remains `sticky_stop.clear()` (audited `AGENT_RESUMED`), surfaced through the existing admin runtime surface. Alert emission stays console-side (the Machines do not send email; the established boundary holds).

## Consequences

- Per-seat COGS becomes real, vendor-billed data; the COGS/MRR gate can arm once #1659 authors `recurring_price`.
- Runaway spend is bounded at ~2× the authored daily cap on the job path and by `inbound_daily_cap × worst-case turn cost` on the gateway path (both authored, both audited when hit), with next-day anomaly detection behind them.
- The cost-telemetry worker simplifies: central write, workspace grouping, no per-customer database enumeration. `src/lib/admin/cost-query.ts` drops the per-database HTTP fan-out.
- Spec debt to clear in the implementing PRs: amend `cost-telemetry-events.md`, `d1-schema.md`, `cost-attribution-rollup.md`; flip `operator/contracts/runtime-controls.yaml` `sticky_stop_cost_cap` from `inert` when the live probe exists; update the handbook cost/observability pages per the maintenance contract.
- `captain_time_events` (labor, $200/hr) gets a writable home; the `crane operator log-time` CLI remains unbuilt and is unblocked by this ADR.
