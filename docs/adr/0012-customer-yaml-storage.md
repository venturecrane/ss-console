---
title: customer.yaml Storage — Git as Source of Truth, D1/R2 as Materialized Replicas
date: 2026-05-21
status: superseded
superseded-date: 2026-10-22
superseded-by: 0094-customer-configuration-as-built.md
captain: Scott Durgan
supersedes: none
related-spec: docs/specs/operator/customer-yaml-schema.md
related-issue: https://github.com/venturecrane/ss-console/issues/790, https://github.com/venturecrane/ss-console/issues/924
---

# ADR 0012: customer.yaml Storage (SUPERSEDED)

> **Superseded 2026-10-22 by [ADR 0094](./0094-customer-configuration-as-built.md).** Do not load this record as a description of the running system. Its git-first principle survives in 0094; almost everything else here was never built as written. The config lives at `operator/customers/<slug>/customer.yaml`, not `customer-configs/<slug>.yaml`; the R2 key is `vaults/<slug>/customer.yaml`, not `customers/<slug>/customer.yaml`; a merge never provisions a Machine (provisioning is `operator/bin/reprovision.sh`, by hand); drift is reconciled by the `customer-config-reconcile.yml` GitHub Action, which opens issues, not by a Cloudflare cron paging Sentry; retirement is `operator/bin/decommission-customer.sh`, which tombstones the directory, not a PR deletion CI acts on; there is no `smd-config sync` CLI; and `customer_configs` is keyed on `customer_slug` since migration 0090, not `entity_id`. The text below is kept unchanged as history.

**Status (original):** Accepted. The storage architecture below is the durable answer to "where does `customer.yaml` live?" — a question deferred when [ADR 0011](./0011-multi-persona-per-customer.md) §4 declared the file authoritative without pinning its location. This ADR pins it.

**Source:** Captain prompt 2026-05-21 — _"what is best for the long term durability of the product that does not cut corners or make assumptions?"_ — in the context of building the [#924](https://github.com/venturecrane/ss-console/issues/924) `getActivePersona()` portal resolver, which needs to read from `customer.yaml`.

Pairs with [ADR 0007](./0007-per-customer-machine-isolation.md) (per-customer Machine isolation), [ADR 0008](./0008-customer-owned-memory-artifact.md) (customer-owned memory artifact), [ADR 0009](./0009-cross-machine-query-prohibition.md) (cross-Machine query prohibition), and [ADR 0011](./0011-multi-persona-per-customer.md) (multi-persona per customer).

---

## Context

[ADR 0011](./0011-multi-persona-per-customer.md) §4 commits `customer.yaml` as the authoritative source for product configuration — personas, connectors, voice samples reference, scope envelope, escalation rules, business hours. The earlier proposal to read configuration from `subscriptions.settings_json` was explicitly rejected as a sync-trap.

But the ADR did not pin storage. Two real consumers need to read this file:

1. **The portal Worker** — on every authenticated portal request that needs persona context (the `getActivePersona()` helper [#924](https://github.com/venturecrane/ss-console/issues/924) being the immediate consumer; future surfaces follow). The portal runs on Cloudflare Workers, has hot-path latency budgets, and per [ADR 0009](./0009-cross-machine-query-prohibition.md) cannot bind to per-customer Hermes D1 databases.
2. **Each customer's Hermes Machine** — on boot and on reload, to resolve its persona configuration, skill assignments, signature blocks, channel bindings. Hermes runs on Fly Machines inside the per-customer isolation boundary established by [ADR 0007](./0007-per-customer-machine-isolation.md), with each Machine reading only its own state.

Six storage candidates were considered:

| Option                                                                        | Why it fails the long-term-durability test                                                                                                                                                                                                                                                                                                      |
| ----------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **D1 only** (a `customer_configs` table that's written via admin UI)          | No version control, no PR review, no diff history. Every change is a database mutation with no audit trail of intent. Schema evolution requires migration coordination across SMD and customer state.                                                                                                                                           |
| **R2 only** (per-customer prefix, portal reads R2 directly)                   | Portal hot-path latency: every authenticated portal request requiring persona context pays a ~50–100ms R2 round-trip. A cache layer would mitigate this — and that cache layer becomes another moving part to keep coherent.                                                                                                                    |
| **Per-customer Hermes D1 only**                                               | Portal cannot read it per [ADR 0009](./0009-cross-machine-query-prohibition.md). Forces an outbound portal-to-Machine call for every config lookup. Couples portal availability to every Machine being warm. Cold-start Machines (which [ADR 0007](./0007-per-customer-machine-isolation.md) explicitly allows) would fail portal page renders. |
| **Hermes-exposed API** (Machine serves its own config to portal)              | Same coupling as above. Also requires Machines to expose an authenticated endpoint to SMD's portal, which thickens the security surface for no gain.                                                                                                                                                                                            |
| **Git only** (portal reads from GitHub via API on every request)              | Portal Worker hitting GitHub on every request: latency, rate limits, GitHub credentials in runtime. Couples product runtime to a developer-facing tool. Equivalent failure mode to a third-party SaaS dependency.                                                                                                                               |
| **Hybrid — git as source of truth, materialized replicas at the read points** | The pattern Kubernetes manifests, Terraform state, Helm charts, and every serious GitOps system converged on. Reviewability, versioning, and audit trail come from git; read-path latency comes from local replicas.                                                                                                                            |

The hybrid pattern is the only one that does not cut a corner on either reviewability, latency, or isolation. It is the decision.

---

## Decision

### 1. Git is the source of truth

Each customer's configuration lives at one canonical path: `customer-configs/<customer-slug>.yaml`. The file is human-edited, PR-reviewed, and validated against the [`customer-yaml-schema.md`](../specs/operator/customer-yaml-schema.md) contract before merge.

**Repository location:** TBD — captured as a follow-on decision in the implementation issue. Two options:

- (a) A subdirectory under `ss-console/` (e.g. `ss-console/customer-configs/`). Lower operational overhead — one repo, one PR flow. Mixes operational config with application source.
- (b) A separate `smd-customer-configs` repo. Cleaner separation, but adds a second repo with its own access controls, CI, and review cadence.

Captain decides at the implementation issue. The storage architecture below is identical in either case; only the path prefix changes.

### 2. Replicas are projections, not competing sources

On every merge to the canonical branch, CI:

1. Validates the YAML against the schema spec (closes [#790](https://github.com/venturecrane/ss-console/issues/790) at the validator level).
2. Checks for secret leakage. Refuses to merge if any field matches a secret heuristic (gitleaks rules + a project-specific allowlist of public fields).
3. Parses the YAML into a normalized JSON projection.
4. Writes the projection to portal D1 (`customer_configs` table, keyed by `entity_id`).
5. Uploads the canonical YAML to per-customer R2 (`r2://customers/<slug>/customer.yaml`).
6. Stamps both replicas with the git SHA and a `synced_at` timestamp.

**The replicas are never edited directly.** No admin UI mutates them. No script writes to them outside CI. If a divergence is detected (see _Drift detection_ below), the resolution is always "re-sync from git" — never "patch the replica."

### 3. Portal reads from portal D1

The new `customer_configs` table in portal D1 (migration 0039):

```sql
CREATE TABLE customer_configs (
  entity_id           TEXT PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE,
  org_id              TEXT NOT NULL REFERENCES organizations(id),
  customer_slug       TEXT NOT NULL UNIQUE,
  schema_version      TEXT NOT NULL,
  personas_json       TEXT NOT NULL,        -- JSON array, length ≥1 (ADR 0011 §1)
  voice_library_json  TEXT,                  -- nullable until voice library exists
  escalation_json     TEXT,
  business_hours_json TEXT,
  connectors_json     TEXT,                  -- non-secret connector references only
  scope_json          TEXT,
  git_sha             TEXT NOT NULL,        -- commit SHA the projection was built from
  synced_at           TEXT NOT NULL,
  created_at          TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_customer_configs_org ON customer_configs(org_id);
CREATE INDEX idx_customer_configs_slug ON customer_configs(customer_slug);
```

**The shape is a projection of customer.yaml, not a mirror.** It stores only fields the portal needs to render. Secrets (API tokens, OAuth refresh tokens, anything sensitive) never enter this table — they live in Infisical with references denormalized into `connectors_json` if needed.

**Read path:** `src/lib/portal/customer-config.ts` exports `getCustomerConfig(db, entity_id)` and `getActivePersona(db, entity_id)`. `getActivePersona` returns `personas[0]` if present, else `null`. Phase 2 of [ADR 0011](./0011-multi-persona-per-customer.md) (when N > 1) extends the selector but the function signature is stable.

### 4. Hermes reads from per-customer R2

The canonical YAML is uploaded to the customer's R2 prefix on every CI sync. Hermes Machines read from their own prefix only — same isolation discipline as memory vault objects ([ADR 0008](./0008-customer-owned-memory-artifact.md)).

Hermes-side schema parsing remains in Hermes's existing pydantic validator. The portal projection is independent — both consumers parse the YAML against the same schema spec but maintain independent typed representations.

**Why not portal pushes config to Hermes via API?** Because pull-from-R2 is simpler, cheaper, and aligned with the Machine's existing R2 access pattern for the memory vault. Push-from-portal would require an inbound authenticated endpoint on every Machine, thickening the security surface.

### 5. Onboarding flow

1. Captain (or admin) creates a PR against the canonical repo: `customer-configs/<new-slug>.yaml`.
2. CI runs the schema + secret validators. Failures block the PR.
3. Captain reviews diff (since it's a PR, normal review applies).
4. On merge, CI's sync job:
   - Inserts the projection into portal D1 `customer_configs`.
   - Uploads the YAML to `r2://customers/<slug>/customer.yaml`.
   - Provisions the Hermes Machine if it doesn't exist (existing tooling — `provision-customer.sh`).
5. Captain inserts the `subscriptions` row to activate portal access (per [ADR 0011](./0011-multi-persona-per-customer.md) §4; Stripe automation deferred to [#917](https://github.com/venturecrane/ss-console/issues/917)).

**Hand-edits are a defect.** If anyone hand-edits the D1 row or the R2 object outside CI, drift detection (§6) will page on the next run.

### 6. Drift detection

A scheduled Cloudflare cron job runs daily:

1. Reads the canonical YAML for every customer from the git repo.
2. Compares its content hash against the `git_sha` + reconstructed projection in D1.
3. Compares against the R2 object hash.
4. On mismatch: emits a Sentry event tagged `customer_config_drift`, names the customer, names the divergent replica, and includes the diff.

The resolution path is always re-sync from git. The drift event names the canonical state; the operator does not have to investigate which side is "right."

### 7. Offboarding

Customer offboarding is a PR that deletes `customer-configs/<slug>.yaml`. CI on merge:

1. Removes the row from portal D1 `customer_configs`.
2. Removes (or archives — Captain decision per customer) the R2 object.
3. Deactivates the `subscriptions` row.
4. Decommissions the Hermes Machine per the existing decommission runbook.

Cleaner than D1-only or R2-only paths, where offboarding can leave orphan rows or objects that nobody finds.

### 8. Schema evolution

When the schema changes (new fields, new validations), the order of operations:

1. Update [`customer-yaml-schema.md`](../specs/operator/customer-yaml-schema.md) and the pydantic validator + portal projection mapping.
2. Bump `schema_version` in the spec.
3. Migrate existing customer YAML files via PR — each customer file gets the new field, defaults applied, validated.
4. CI on merge re-syncs all replicas with the new schema_version.

Old `schema_version` in D1 → CI sync warns and re-projects. No customer is silently running on a stale schema.

### 9. Escape hatches

Two scenarios where CI sync alone is not enough:

- **CI is broken on the day a customer needs to onboard.** An admin CLI (`smd-config sync --customer=<slug> --from-sha=<sha>`) re-runs the sync locally. Logs the deviation; Sentry event named `manual_config_sync`. Acceptable in emergencies, not as routine practice.
- **A drift event needs immediate triage outside the cron window.** Same CLI. Same logging.

The escape hatches do not write to git. They only re-project from a known-good git SHA. This preserves git's role as source of truth even when CI is down.

---

## Consequences

### Positive

- **Single source of truth, two read replicas.** Disputes resolve at git. No question about which side is authoritative.
- **Reviewable.** Every config change is a PR with diff. Captain can review before customer state changes.
- **Versioned.** Full history. `git log -- customer-configs/<slug>.yaml` shows every change with intent.
- **Auditable.** The `git_sha` column on the D1 row ties every portal read to a specific commit. Audit log entries can cite the commit a decision was made against.
- **Fast portal reads.** D1 lookup on the hot path. Same latency profile as `subscriptions` and `product_roles` reads (which the portal already does).
- **ADR-compliant Hermes reads.** Per-customer R2 prefix keeps the isolation boundary [ADR 0008](./0008-customer-owned-memory-artifact.md) and [ADR 0009](./0009-cross-machine-query-prohibition.md) established.
- **Onboarding and offboarding are PRs.** Same review, same audit, same revert path as code changes.
- **Secret-exclusion enforced at write time.** Bad-secret commits fail CI before they merge. Cannot enter the replicas.
- **Schema evolution is bounded.** All customers on the same schema version, enforced at sync.

### Negative / accepted

- **More moving parts than D1-only or R2-only.** A CI workflow, a sync job, a drift cron, an admin CLI. Each is a small piece; together they're more surface than a single store. Accepted because the alternative is corner-cutting.
- **CI flakiness can block onboarding.** Mitigated by the escape-hatch CLI. Not eliminated. Accepted because the alternative — letting anyone hand-edit a replica — is worse.
- **Two schema parsers.** Portal projects from YAML to JSON; Hermes parses YAML to its pydantic model. They must stay aligned against the same schema spec. Drift between them is a class of bug. Mitigated by both consumers building against the [`customer-yaml-schema.md`](../specs/operator/customer-yaml-schema.md) contract and a cross-consumer test suite (Phase 2). Accepted at v1 because divergence at one customer × one persona × one schema version is detectable in QA.
- **Storage cost of the canonical YAML.** R2 is cheap, but every Hermes Machine pulls its YAML at boot. Insignificant at any plausible customer count. Noted for completeness.

### Out of scope

- **Whether the configs repo lives in `ss-console/` or a separate `smd-customer-configs` repo.** Captured as a follow-on decision at the implementation issue.
- **Stripe Subscriptions ↔ subscriptions row automation.** [#917](https://github.com/venturecrane/ss-console/issues/917). Compatible with this ADR.
- **Cross-customer config policies** (e.g. enterprise-wide voice library updates that propagate to every customer). Out of scope; if it ever becomes relevant, the canonical repo's directory structure supports it.
- **Customer-self-serve config edits via the portal.** Out of scope at v1. If ever desired, the path is: portal write → PR-via-API → CI sync. Not a database mutation.

---

## Verification

### Schema readiness checks

1. The portal projection mapping (`src/lib/portal/customer-config.ts:projectFromYaml`) round-trips: a valid YAML parses to a projection that can be inserted into D1, and the projection's fields satisfy every read site in the portal.
2. The Hermes pydantic validator and the portal projection parse the same YAML without divergence — verified by a cross-consumer fixture test (Phase 2).
3. The drift cron detects a deliberately-injected divergence (test fixture).
4. CI rejects a YAML that contains a known-shape secret (test fixture).

### Onboarding rehearsal

End-to-end rehearsal for a new customer:

1. PR with `customer-configs/<new-slug>.yaml` opens.
2. CI validates → passes.
3. PR merges.
4. CI sync job runs.
5. Portal D1 has the new row.
6. R2 has the new object.
7. `getActivePersona(db, <entity_id>)` returns the persona configured in the YAML.
8. Hermes Machine, when provisioned, reads the YAML from R2 successfully.

If any step fails, the customer is not onboarded — the partial state must be cleaned up before the next attempt. The PR can be reverted to roll back.

### Adversarial review

Hand the ADR to a fresh agent and ask: _"How do I add a new persona to an existing v1 customer?"_

Acceptable answer:

1. Open a PR editing `customer-configs/<slug>.yaml` to add a second entry to `personas[]`.
2. Wait for CI to validate.
3. Merge the PR.
4. CI re-syncs both replicas; the portal D1 row now has two personas in `personas_json`.
5. Phase 2 runtime (per [ADR 0011](./0011-multi-persona-per-customer.md)) handles the second persona's Machine, AgentMail inbox, and routing.

No database mutation. No admin UI. No script run by hand. The PR is the change.

---

## Implementation

The Phase 1 sequence — recorded here, executed by the follow-on PRs:

1. **This ADR lands** (recording the architectural commitment).
2. **Portal D1 substrate PR** — adds the `customer_configs` migration, the `getCustomerConfig` / `getActivePersona` helpers, and unit tests. Does NOT depend on the git repo or CI sync existing yet; the helpers read whatever is in `customer_configs`. Captain can hand-seed rows for the alpha customer via an admin endpoint or direct SQL. The git/CI side lands separately.
3. **PRD + runbook vocabulary update PR** ([#921](https://github.com/venturecrane/ss-console/issues/921)) — independent of (2); the docs update can land in parallel.
4. **Canonical configs repo + CI sync PR** (separate, follow-on) — establishes the git repo (location decision deferred), the CI workflow, the secret validator, and the R2 upload.
5. **Drift detection cron + admin CLI** (separate, follow-on) — adds the daily comparison, the Sentry integration, and the escape-hatch CLI.
6. **Schema validator integration** ([#790](https://github.com/venturecrane/ss-console/issues/790)) — the YAML schema spec gets a runtime validator that CI invokes.

Phases 2–3 are the minimum coherent slice for this session. Phases 4–6 are durable architecture work for follow-on sessions and do not block [#924](https://github.com/venturecrane/ss-console/issues/924).

---

## References

- [ADR 0007](./0007-per-customer-machine-isolation.md) — per-customer Machine isolation
- [ADR 0008](./0008-customer-owned-memory-artifact.md) — customer-owned memory artifact
- [ADR 0009](./0009-cross-machine-query-prohibition.md) — cross-Machine query prohibition
- [ADR 0011](./0011-multi-persona-per-customer.md) — multi-persona per customer (§4 declares customer.yaml authoritative; this ADR pins its storage)
- Platform PRD §7.3 (customer.yaml example), §9 (persona model), §20 (Phase 1 deliverables)
- [`customer-yaml-schema.md`](../specs/operator/customer-yaml-schema.md) — formal schema contract
- [Issue #790](https://github.com/venturecrane/ss-console/issues/790) — customer.yaml formal schema with secret-exclusion enforcement
- [Issue #917](https://github.com/venturecrane/ss-console/issues/917) — Stripe Subscriptions wiring
- [Issue #924](https://github.com/venturecrane/ss-console/issues/924) — `getActivePersona()` portal resolver helper (immediate consumer of this ADR)
