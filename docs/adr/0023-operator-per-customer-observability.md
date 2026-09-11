---
title: Operator Per-Customer Observability — Compose Existing Specs + Add Sentry, Heartbeat, Fleet View, Alert Routing
date: 2026-05-26
status: accepted
captain: Scott Durgan
supersedes: none
renamed-by: 0034-operator-product-naming.md
related-issue: TBD
---

# ADR 0023 — Operator Per-Customer Observability

**Status:** Accepted, **renamed 2026-06-01 by [ADR 0034](./0034-operator-product-naming.md)** (product now named **Operator**; observability decisions unchanged). Where this ADR says "Operator," read "Operator." The external Sentry project `smd-operator` keeps its name (slug-independent infra resource, per ADR 0034 §4).

**Status (original):** Accepted (Captain decision 2026-05-26).

> **Forward-note (2026-07-13).** The **cost-telemetry** portions of this ADR are now owned by [ADR 0062](./0062-operator-cost-plane.md) (Operator cost plane, 2026-07-03), the current source of truth for cost — it moves `cost_telemetry` + `captain_time_events` to the central console D1 with a `customer_slug` column and defines the sticky-stop breaker. The observability composition here (Sentry, heartbeat, fleet view, alert routing) stands; read 0062 for the cost plane.

**Source:** Per-customer Fly Machine isolation (ADR 0007) is the runtime architecture, but no ADR ties together what observability looks like across an engagement. Several detailed specs in `docs/specs/operator/` cover individual observability primitives — audit log, retention, cost telemetry, cost rollup, dashboard roles — but they were authored independently and there is no single document naming what composes the stack, what is genuinely new, and which cross-cutting calls are locked.

---

## Context

The Operator runs as one Fly.io Machine per customer per [ADR 0007](./0007-per-customer-machine-isolation.md). Observability for that Machine is a product surface: SMD operates the fleet; customers see their own activity through the dashboard; compliance needs immutable audit; the COGS/MRR ratio is the kill-criterion gate. Without a coherent ADR, each new piece (Sentry, fleet view, alert routing) gets designed in a vacuum and risks contradicting or duplicating the existing spec corpus.

The substrate has five existing specs:

- [`audit-log-immutability.md`](../specs/operator/audit-log-immutability.md) (issue #892) — D1 wrapper + Logpush mirror to R2 Object Lock + integrity check. **Pending rehome** to the overlay's `hermes-smd-audit` plugin per [ADR 0015](./0015-hermes-fork-vs-upstream.md).
- [`audit-retention.md`](../specs/operator/audit-retention.md) (issue #893) — per-vertical retention defaults, `memory.retention.audit_log_days` override-up-only rule, decommission audit-log carve-out.
- [`cost-telemetry-events.md`](../specs/operator/cost-telemetry-events.md) (issue #804) — 17 drivers, per-call emission, nightly Worker at `infra/workers/cost-telemetry/worker.ts`, COGS/MRR > 0.40 / two-consecutive-month threshold, Captain CLI `crane operator log-time`.
- [`cost-attribution-rollup.md`](../specs/operator/cost-attribution-rollup.md) (issue #884) — `compute_monthly_rollup` reader contract, nine driver categories, basis-point math.
- [`dashboard-roles.md`](../specs/operator/dashboard-roles.md) (issue #788) — principal / operator / compliance role schema, full permission matrix, `compliance_enabled` opt-in for the dedicated Compliance dashboard.

Two surfaces in `ss-console` are already implemented:

- `src/pages/admin/operator/costs/index.astro` — customer-list page with 30-day COGS, 7-day rolling average, COGS-vs-revenue indicator from `subscriptions.settings_json.monthly_price_cents`, open cost-anomaly alerts via `listOpenAlerts`.
- `src/pages/admin/operator/costs/[customer_slug].astro` — per-customer drill-down.

Plus an existing anomaly-detection helper: `src/lib/admin/cost-anomaly.ts` (`listOpenAlerts`) and `src/lib/admin/cost-query.ts` (`listCostCustomers`, `fetchCustomerCostRows`, `cogsRatio`).

What is missing is:

1. A coherent statement of which existing specs compose the per-customer observability stack.
2. Four genuinely-new pieces that no existing spec covers: Sentry on Machines, `/health` + push heartbeat, fleet roll-up extension of the existing customer-list page, alert routing that composes with the existing cost-anomaly system.
3. Locked Captain decisions on vendor ownership, cost-breach response, and substrate shape.

## Decision

The per-customer observability stack is composed from existing specs (cited, not redesigned) plus four new pieces (introduced here). Cross-cutting Captain decisions are locked below.

### Composed from existing specs

| Layer                                                                                               | Owner                                                           | Status                                                   |
| --------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- | -------------------------------------------------------- |
| Audit truth substrate                                                                               | `audit-log-immutability.md` (post-rehome to `hermes-smd-audit`) | spec done; implementation pending #892 + ADR 0015 rehome |
| Audit retention policy                                                                              | `audit-retention.md`                                            | spec done                                                |
| Cost telemetry emission                                                                             | `cost-telemetry-events.md`                                      | spec done; instrumentation tagged Phase 2                |
| Cost rollup + COGS/MRR computation                                                                  | `cost-attribution-rollup.md`                                    | spec done; v1 on-demand reader                           |
| Customer-facing dashboard surfaces (Today / Queue / Memory / Audit / Persona / Skills / Voice tabs) | `dashboard-roles.md`                                            | spec done; multi-role v1 ships beta-1                    |
| Per-customer cost drill-down at `/admin/operator/costs/{customer_slug}`                             | `cost-telemetry-events.md` §Per-customer rollup view            | implemented                                              |
| Customer-list / COGS overview at `/admin/operator/costs/`                                           | issue #885                                                      | implemented                                              |
| Cost-anomaly alert detection (`src/lib/admin/cost-anomaly.ts`, `listOpenAlerts`)                    | (predates this ADR)                                             | implemented                                              |

### Introduced by this ADR

**1. Sentry on Operator Machines.** One shared `smd-operator` Sentry project; DSN pushed as a Fly secret at provision time (same mechanism as Anthropic / Composio per [ADR 0010](./0010-per-customer-oauth-token-storage.md)). The Operator bootstrap reads `customer.yaml` (via the same path used by `hermes-smd bootstrap` per [ADR 0019](./0019-customer-yaml-to-profile-config-translation.md)) and initializes the Sentry SDK with a scope tag `tenant=<slug>`.

The marketing-site reference pattern is `src/lib/observability/sentry.ts` (a `withSentryRequestHandler` wrapper using `@sentry/cloudflare@^10.51.0`), invoked from `src/middleware.ts:255`. **That is a TypeScript-for-Workers pattern; the Operator Machines are Python, so the implementation uses the `sentry-sdk` Python package, not `@sentry/cloudflare`.** The marketing site is conceptual reference, not code reuse. The Python init lives in `venturecrane/hermes-smd-overlay` per [ADR 0015](./0015-hermes-fork-vs-upstream.md) — that repo is not in the `ss-console` workspace; implementation lands in a separate PR there.

SMD ops configures Sentry alert rules in Sentry UI per customer. No auto-provisioning from `customer.yaml` in v1.

**2. `/health` endpoint + push heartbeat contract.** Each Machine exposes an internal-only `/health` returning:

```json
{
  "last_audit_ts": "...",
  "last_skill_ts": "...",
  "heartbeat_ts": "...",
  "process_uptime_seconds": ...,
  "version": "..."
}
```

`heartbeat_ts` is written by a 30-second internal ticker, independent of any traffic. A background task on the Machine POSTs to its assigned healthchecks.io URL every 60 seconds. Healthchecks.io grace expiration fires a webhook to `/api/webhooks/healthchecks`, which writes a `cost_anomaly_alerts` row with `source='healthchecks'`.

`/health` is reachable only via Fly's private network — it is not exposed publicly. The grace and period are configurable per customer via `customer.yaml.observability.health`.

**3. Fleet roll-up view — extends the existing customer-list page.** `src/pages/admin/operator/costs/index.astro` already enumerates every Operator customer with COGS metrics. The fleet roll-up extends this page with three new columns:

- Heartbeat status (green / yellow / red, derived from the most recent `fleet_status` row for that customer).
- Sentry error count last hour (tenant-tagged, queried via Sentry API).
- Uptime-since-last-restart.

Per-customer drill-down to `costs/[customer_slug].astro` is unchanged.

Adds a new D1 table `fleet_status` in `ss-console`'s D1 (`env.DB`) — not per-customer — written by Machines via authenticated HTTP, read by this page. [ADR 0009](./0009-cross-machine-query-prohibition.md) §"Out of scope" explicitly permits this control-plane cross-customer aggregation for "fleet health."

No separate `/admin/operator/fleet` page.

**4. Alert routing — composes with the existing cost-anomaly system.** All sources (cost-anomaly, Sentry, healthchecks, audit-integrity in Wave 2) write rows to the existing `cost_anomaly_alerts` table tagged by `source`. The admin dashboard at `/admin/operator/costs/` surfaces them via the existing `listOpenAlerts()` reader (source-agnostic by design). The dashboard is the primary monitoring surface; push-channel escalation is deferred to a real-customer-demand follow-on per locked-decision #9.

The router has two distinct surfaces:

- **Push (webhook receiver — composes with `src/pages/api/webhooks/`, no new Worker).** Sentry's native alert rules POST to `/api/webhooks/sentry`; healthchecks.io POSTs to `/api/webhooks/healthchecks` on grace expiration. Each handler verifies HMAC against the source's signing secret and writes a `cost_anomaly_alerts` row with the appropriate `source` tag. No custom Sentry threshold is invented here — Sentry's native rate-of-change and frequency rules are the configuration surface, owned per-customer by SMD ops in Sentry UI. **Operational gotcha at scale**: per-customer alert rules in Sentry are configured manually; manageable at the first few customers, painful at ~20+. Auto-provisioning Sentry rules from `customer.yaml` is a follow-on triggered by customer-count scale.

- **Pull (cron consumer — extends `workers/cost-anomaly`, no parallel router).** The existing daily cron at `workers/cost-anomaly` already handles COGS/MRR threshold detection and persists alert rows via `listOpenAlerts` / `upsertAlert`. This ADR adds (a) a 24h Sentry error-count sync that writes `fleet_status.sentry_errors_last_24h`, and (b) the `source='cost'` tag on cost-anomaly writes for the dashboard's source-agnostic banner. Captain ops escalation continues via the existing Resend path on this Worker. The router does NOT reinvent the anomaly-detection logic and does NOT introduce a parallel alert-router Worker.

**5. Fly resource metrics (not new, named explicitly).** Per-Machine CPU, memory, disk, network, and restart count come from Fly's hosted Grafana + Prometheus endpoint. SMD ops bookmark the Fly Grafana view per Machine; nothing in SMD's stack duplicates these signals. Listed here as a layer of the observability stack so the ADR does not leave the resource-metrics gap silent.

### Cross-cutting calls locked

1. **SMD-managed vendor accounts** (Captain decision 2026-05-26). Single Sentry org, single healthchecks.io account, SMD-owned per-customer R2 buckets (Object Lock per `audit-log-immutability.md`). Customer-owned vendor accounts deferred to a follow-on ADR triggered by real demand (premium tier or compliance posture).

2. **Alert-only on COGS/MRR breach** (Captain decision 2026-05-26). The existing > 0.40 / two-consecutive-month threshold from `cost-telemetry-events.md` is the trigger. This ADR adds: alert rows are written to `cost_anomaly_alerts` with `source='cost'` and surface in the admin dashboard banner; the existing Resend path on `workers/cost-anomaly` continues to email Captain. Captain decides per-incident (re-scope, eat cost, talk to client). No automatic disabling of the Operator. Auto-disable / soft-cap / hard-cap mechanisms are explicitly out of scope.

3. **Shared Sentry project, tenant tag at SDK init** — not per-customer Sentry projects. Isolation comes from tag scoping; query filters on `tenant=<slug>` to surface per-customer error streams.

4. **Healthchecks.io for heartbeats**, not self-hosted. The outside-the-trust-boundary property matters more than minimizing vendor count.

5. **Retention defaults inherited from `audit-retention.md`** §"Per-vertical defaults" — `law-firm`, `real-estate`, `manufacturing`, `insurance`, and `mixed` default to 2555 days (7 years); `marketing-agency` defaults to 1095 days (3 years). Override-up-only enforcement is already specified. This ADR does not introduce new retention defaults.

6. **Audit-log substrate rehome is a prerequisite for the audit layer only**, not for the whole ADR. The `audit-log-immutability.md` rehome to `hermes-smd-audit` per [ADR 0015](./0015-hermes-fork-vs-upstream.md) gates the audit-immutability + Logpush-mirror pieces. Sentry, `/health`, healthchecks.io, fleet view, and alert routing are independent — they can ship before the rehome lands. This ADR's implementation proceeds in two waves:
   - **Wave 1 (pre-rehome).** Sentry on Machines, `/health` + heartbeat, fleet view extension, alert routing (with audit-integrity ingestion stubbed).
   - **Wave 2 (post-rehome).** Audit-log Logpush wiring per #892, against the rewritten spec; alert routing wires the audit-integrity report consumer.

7. **Fly's native observability is the resource-metrics substrate.** No SMD-hosted Prometheus stack; no duplicate metrics pipeline. Fly's free tier covers what this ADR needs.

8. **Healthchecks.io heartbeats are push-from-Machine, not pull.** The Machine emits an outbound POST to its assigned healthchecks.io URL every 60 seconds. Push direction means the Machine declares liveness; pull would require exposing `/health` publicly and trusting the responder.

9. **Admin dashboard is the primary monitoring surface; push channels are escalation overlay.** All alert sources (cost, Sentry, healthchecks, audit-integrity in Wave 2) write rows to the existing `cost_anomaly_alerts` table tagged by `source`. The existing `/admin/operator/costs/` page surfaces them via the existing `listOpenAlerts()` reader. Customer-configurable external destinations (`alert_webhook`) are a follow-on triggered by real customer demand — Wave 1 ships without the field because Telegram bot webhooks don't accept arbitrary JSON, Slack incoming webhooks accept only `{text}`, and shipping a "Telegram-compatible payload" that doesn't function against Telegram is worse than not shipping it. Captain ops escalation (fleet-wide critical alerts) reuses the existing Resend path on `workers/cost-anomaly`.

10. **Machine-to-control-plane heartbeat auth — single shared secret in Wave 1.** A single `MACHINE_HEARTBEAT_KEY` Worker secret authenticates `POST /api/internal/heartbeat`; `X-Tenant-Slug` header identifies the tenant. No per-customer key DB lookup → no timing-oracle path on slug enumeration. **Upgrade path (customer #2 onboarding):** per-tenant secrets in a `machine_credentials` table with HMAC-SHA256 + per-row salt, dual-key rotation (current + prev with TTL), always-DB-roundtrip lookup with sentinel hash on miss for timing uniformity. Documented and gated on customer #2; not built now. **Built 2026-09-10** (migration 0114, `src/lib/auth/machine-key.ts`, `operator/bin/lib/machine_credential.py`, minted at provisioning and by `operator/bin/rotate-machine-credential.sh`), after the 2026-09-10 code review found four seats on the shared key with the trigger long passed. The shared Worker secret remains a fallback for a slug with no credential row until it is unset.

11. **Sentry PII scrub is locked at SDK init.** `send_default_pii=False`; `before_send` + `before_breadcrumb` hooks strip: request bodies entirely; named headers (`cookie`, `authorization`, `x-api-key`, `x-machine-token`, `x-sentry-auth`, `x-tenant-slug`); email-shaped regex from messages and breadcrumbs; provider key shapes (`sk-[a-zA-Z0-9]{20,}`, `pk_(live|test)_[a-zA-Z0-9]{20,}`, AWS `AKIA[0-9A-Z]{16}`). The scrub list is a constant in the overlay's Sentry init module; a pytest regression suite gates every overlay PR so refactor-induced regressions surface in review, not after a leak.

### customer.yaml additions

The existing `logging:` block (`level`, `ship_to`) handles application stdout/stderr destinations — it is unchanged and orthogonal to this addition. `memory.retention.audit_log_days`, `compliance_enabled`, role-related fields, and connector OAuth scope declarations also stay unchanged. The new `observability:` block is parallel to `logging:` and covers vendor wiring only:

```yaml
observability:
  sentry:
    enabled: true # default; shared SMD project, tenant-tagged at SDK init
  health:
    period_seconds: 60 # push cadence to healthchecks.io
    grace_minutes: 5 # late before alert fires
```

Sentry error-spike thresholds are NOT in `customer.yaml` — they are owned by Sentry's native alert rules, configured per-customer in Sentry UI by SMD ops. Auto-provisioning from `customer.yaml` is a follow-on triggered by customer-count scale.

`alert_webhook` is deferred per locked-decision #9 — Wave 1 has no customer-configurable external destination. The Captain ops escalation path is the existing Resend channel on `workers/cost-anomaly`; customer-facing escalation is the admin dashboard. Reintroduction is a follow-on driven by real customer demand, with per-destination adapter shape (Telegram bot API / Slack incoming webhook / raw HTTPS) — not a single "Telegram-compatible" payload that doesn't actually work against Telegram.

No customer-owned vendor fields. Adding them is a follow-on ADR.

## Consequences

**Positive.**

- Per-customer observability is composable rather than reinvented: every layer points at an existing spec or a small new piece, not a parallel substrate.
- Provisioning (`bin/provision-customer.sh`) ends up as a single linear script: stage Sentry DSN, create healthchecks.io check, seed `fleet_status` row, render `fly.toml`, deploy. Decommission reverses cleanly with the existing nine-step pipeline.
- Wave-1 work ships independently of the audit-log rehome; the ADR does not block Sentry, `/health`, fleet view, or alert routing on out-of-tree work.
- The fleet view drops into the existing customer-list page, preserving the drill-down and avoiding a new admin surface that would have its own auth and IA decisions.
- Alert routing composes with the existing `listOpenAlerts` system rather than duplicating cost-anomaly detection logic.
- Admin dashboard is the single always-on monitoring surface across all customers; Captain's mobile / inbox escalation goes through the existing Resend path (no new channel for Wave 1). Customer-configurable external destinations are deferred until a real customer asks for one — at which point the design lands as per-destination adapters, not a single shape that doesn't fit any real webhook target.

**Negative / accepted.**

- SMD operates more vendor accounts (Sentry, healthchecks.io, per-customer R2 buckets). Vendor sprawl is real; the trade is centralized account hygiene vs. per-customer provisioning complexity. We accept.
- Sentry alert rules are configured manually per customer in Sentry UI. Manageable for the first few customers; at ~50 customers this becomes an operational burden, triggering an auto-provisioning follow-on.
- The fleet view depends on a Machine-to-control-plane HTTP write per heartbeat. A control-plane outage degrades the fleet view but does not affect Machine operation. We accept.
- `fleet_status` is a control-plane table that crosses customer boundaries by design. [ADR 0009](./0009-cross-machine-query-prohibition.md) explicitly permits this for fleet health; the choice is auditable but worth re-stating loudly here.
- Wave 1 ships without audit-log Logpush. The audit log still writes to per-customer D1 (the immutability invariant in `audit-log-immutability.md` Layer 1 holds via the Worker wrapper), but the tamper-resistant R2 mirror is deferred to Wave 2. We accept; the compliance posture between "D1-only" and "D1 + Logpush mirror" is acceptable for the pre-customer-zero window. The delta is real (the Worker wrapper defends against application-level mistakes but not against privileged D1 manipulation), so Wave 2 is on the critical path before the first paying regulated-vertical customer.

**Out of scope.**

- Cost telemetry schema, drivers, COGS/MRR computation — owned by `cost-telemetry-events.md` and `cost-attribution-rollup.md`.
- Audit log immutability or retention — owned by `audit-log-immutability.md` (post-rehome) and `audit-retention.md`.
- Dashboard role / permission matrix — owned by `dashboard-roles.md`.
- Audit-log-immutability rehome to `hermes-smd-audit` — prerequisite, separate spec rewrite + PR in the overlay repo.
- Logpush mirror implementation (#892) — prerequisite, lands with the rehome.
- Customer-owned vendor accounts (premium / compliance tier) — follow-on ADR triggered by real demand.
- Cost enforcement / auto-disable mechanisms — Captain decision is alert-only.
- Time-machine retrospect (`ADR 0022` design intent locked; implementation deferred).
- Pricing / packaging of observability costs — folded into Operator retainer math (`ADR 0004`), not here.
- Auto-provisioning Sentry alert rules from `customer.yaml` — follow-on triggered by customer-count scale.

## Implementation

### Wave 1 — pre-rehome (independent of audit-log rehome)

**Spec updates:**

- `docs/specs/operator/customer-yaml-schema.md` — add the `observability:` block above (parallel to existing `logging:`).

**Overlay repo (`venturecrane/hermes-smd-overlay`):**

- `sentry-sdk` (Python) init at boot with `tenant=<slug>` scope tag.
- Internal `/health` endpoint exposing `{ last_audit_ts, last_skill_ts, heartbeat_ts, process_uptime_seconds, version }`.
- 30-second internal heartbeat ticker.
- 60-second healthchecks.io push task.

**`ss-console` repo:**

- `operator/bin/provision-customer.sh` — stage shared Sentry DSN secret to Fly app secrets; create healthchecks.io check via API (auth: project API key from Infisical); seed a `fleet_status` row in `env.DB`.
- `operator/bin/decommission-customer.sh` + `operator/bin/lib/decommission.py` — unstub: cancel healthchecks.io check; remove `fleet_status` row.
- New: D1 migration adding `fleet_status` table (control-plane, in `env.DB`).
- Extend: `src/pages/admin/operator/costs/index.astro` — add three new columns (heartbeat status, Sentry errors last hour, uptime).
- New: Sentry + healthchecks.io webhook receivers in `src/pages/api/webhooks/` (no new Worker). Each receiver verifies HMAC and writes a `cost_anomaly_alerts` row with the appropriate `source` tag.
- Extend `workers/cost-anomaly` with a daily 24h Sentry error-count sync (writes `fleet_status.sentry_errors_last_24h` + `sentry_errors_synced_at`). Existing Resend escalation path unchanged. Cost-anomaly writes get `source='cost'` for the dashboard banner.

### Wave 2 — post-rehome (gates on audit-log-immutability rehome)

- `docs/specs/operator/audit-log-immutability.md` — rewrite against post-ADR-0015 architecture (overlay-side `hermes-smd-audit` plugin).
- Wire #892 (Logpush mirror) in the overlay.
- Alert-router pull surface adds `audit_log_integrity` report consumer.

### Existing utilities reused

- `src/lib/observability/sentry.ts` — marketing-site Sentry helper (conceptual reference; not code-reusable across the Python boundary).
- `src/lib/admin/cost-anomaly.ts` + `listOpenAlerts` — anomaly detection.
- `src/lib/admin/cost-query.ts` — `listCostCustomers`, `fetchCustomerCostRows`, `cogsRatio`, `thirtyDayCogsToMonthlyEstimateCents`.
- `subscriptions.settings_json.monthly_price_cents` — existing MRR source.
- `compute_monthly_rollup` from `operator/adapter/cost_rollup.py`.
- `audit_log_integrity` from `operator/adapter/audit_log_integrity.py` (post-rehome).
- `crane operator log-time` CLI.

## Verification

### Wave 1

1. **Provisioning** — `bin/provision-customer.sh --dry-run synthetic-test` shows the staged shared Sentry DSN secret, a created healthchecks.io check URL, and a `fleet_status` seed row.
2. **Liveness** — boot Machine; internal `/health` returns 200 with timestamps; 60s push tick lands at healthchecks.io; check flips green within 2 grace windows.
3. **Error tagging** — fire a deliberate unhandled exception; Sentry event arrives scope-tagged `tenant=synthetic-test`; filter by another tenant shows nothing.
4. **Resource metrics** — Fly Grafana view for the Machine renders CPU / memory / disk / network; SMD ops bookmark URL works.
5. **Cost emission** — drive 100 mocked Anthropic calls per `cost-telemetry-events.md` verification suite; `cost_telemetry` rows appear; `compute_monthly_rollup` reads them back; COGS/MRR computation matches expected.
6. **Alert routing — pull surface** — synthetic COGS/MRR breach (seeded cost rows + rollup) writes a `cost_anomaly_alerts` row with `source='cost'`; admin dashboard banner surfaces it; existing Resend Captain notification fires.
7. **Alert routing — push surface** — Sentry alert rule fires test webhook; healthchecks.io test-fail fires webhook; each handler verifies HMAC and writes a `cost_anomaly_alerts` row with `source='sentry'` and `source='healthchecks'` respectively; admin dashboard banner surfaces both.
8. **Fleet view** — extended `/admin/operator/costs/` page shows synthetic-test with new heartbeat / error / uptime columns; kill Machine; heartbeat column flips to red within grace window; drill-down to existing `/admin/operator/costs/{customer_slug}` works unchanged.
9. **Decommission** — `bin/decommission-customer.sh synthetic-test` completes all nine steps; healthchecks check cancelled; `fleet_status` row removed.

### Wave 2

10. **Audit immutability** — insert audit row via writer; R2 Object Lock bucket contains the mirror row within Logpush window; UPDATE / DELETE against `audit_log` raises `AuditLogImmutabilityError`; periodic integrity check reports clean.
11. **Audit-immutability alert routing** — seed an `IN_MIRROR_NOT_IN_D1` finding; writer emits a `cost_anomaly_alerts` row with `source='audit_integrity'`; admin dashboard banner surfaces it.
12. **Decommission audit carve-out** — `bin/decommission-customer.sh` preserves audit log per `audit-retention.md` carve-out; R2 audit bucket retained per retention policy.

## References

- [ADR 0007](./0007-per-customer-machine-isolation.md) — per-customer Machine isolation
- [ADR 0009](./0009-cross-machine-query-prohibition.md) — cross-Machine query prohibition; control-plane carve-out
- [ADR 0010](./0010-per-customer-oauth-token-storage.md) — per-customer OAuth token storage; SMD-secret push mechanism
- [ADR 0015](./0015-hermes-fork-vs-upstream.md) — Hermes fork posture; overlay-only code surface
- `docs/specs/operator/audit-log-immutability.md` — audit-log immutability (pending rehome)
- `docs/specs/operator/audit-retention.md` — retention defaults, override-up-only, decommission carve-out
- `docs/specs/operator/cost-telemetry-events.md` — cost telemetry emission, COGS/MRR threshold
- `docs/specs/operator/cost-attribution-rollup.md` — monthly rollup contract
- `docs/specs/operator/dashboard-roles.md` — principal / operator / compliance role schema
- `docs/specs/operator/customer-yaml-schema.md` — customer.yaml formal schema
