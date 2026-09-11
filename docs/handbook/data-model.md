---
title: Data Model
section: system
order: 3
summary: Where the venture's data lives - a Cloudflare D1 relational store whose source of truth is the migrations directory, with R2 for objects and KV for sessions
sources:
  - label: migrations/ (D1 schema source of truth; the whole directory, so a new migration flags this page in handbook:drift)
    href: https://github.com/venturecrane/ss-console/tree/main/migrations
  - label: migrations/0001_create_tables.sql (the original schema and its JSON column contracts)
    href: https://github.com/venturecrane/ss-console/blob/main/migrations/0001_create_tables.sql
  - label: src/lib/db/ (data access layer)
    href: https://github.com/venturecrane/ss-console/tree/main/src/lib/db
  - label: Operator per-customer D1 schema spec
    href: https://github.com/venturecrane/ss-console/blob/main/docs/specs/operator/d1-schema.md
  - label: ADR 0012 - customer.yaml storage
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0012-customer-yaml-storage.md
---

## Where the data lives

The console's data lives in three Cloudflare stores, each bound to the single `ss-web` Worker:

- **D1** (SQLite at the edge) is the relational store. It holds the pipeline: leads, businesses, assessments, quotes, engagements, invoices, and the Operator control-plane tables. This is the primary store and the one most code touches.
- **R2** (object storage) holds large or binary artifacts that do not belong in a relational row: assessment transcripts (`assessments.transcript_path` is an R2 key), generated SOW PDFs, and the canonical `customer.yaml` upload per Operator customer (the Hermes read replica, per ADR 0012).
- **KV** (`SESSIONS` binding) holds session state for the legacy magic-link auth path. See `/admin/playbook/client-portal` for the auth model and `/admin/playbook/secrets-access` for how the bindings are wired.

The **`migrations/` directory is the schema source of truth.** Every table and column exists because a numbered migration created it. Migrations are forward-only and additive by convention - there is no down-migration path in normal operation (`migrations/rollbacks/` holds explicit exceptions). To learn the real shape of any table, read its migration, not a model class. `src/lib/db/` is the typed data-access layer over D1; each module owns the queries for one table group (`entities.ts`, `assessments.ts`, `quotes.ts`, `engagements.ts`, `invoices.ts`, and so on).

## Two distinct D1 databases

Do not conflate them:

1. **The console D1** - the venture's own operational database, defined by `migrations/` in this repo, bound to the `ss-web` Worker. Everything below describes this database.
2. **The per-Operator-customer D1** - one isolated database per Operator Machine (`hermes-{slug}-d1`), defined by `operator/migrations/` and specced in `docs/specs/operator/d1-schema.md`. It holds that one customer's runtime state (audit log, memory rules, draft queue, voice samples) and never touches the console D1. Isolation is enforced at the binding layer (one D1 binding per Machine), not by a customer_id column. See `/admin/playbook/operator-platform`.

## The console schema, by group

### Pipeline and CRM

The original portal schema (migration `0001_create_tables.sql`) modeled a business as a `clients` row. The **entity-context architecture** (migration `0008_create_entities_context.sql`) superseded that: a business is now an `entities` row that accumulates context across its whole lifecycle, and migration `0010_rename_client_id_to_entity_id.sql` dropped the legacy `client_id` columns in favor of `entity_id`. Code and newer queries key on `entities`; some older portal code still names the in-memory object `client` even though it carries an entity id.

| Table | Purpose |
|---|---|
| `organizations` | Top-level tenant. Every row in almost every table carries `org_id`. Holds branding and the `settings` JSON (default rate, deposit pct, payment terms, milestone threshold). |
| `entities` | One row per business, with a lifecycle `stage` state machine (`signal` -> `prospect` -> `meetings` -> `proposing` -> `engaged` -> `delivered` -> `ongoing` / `lost`, per `EntityStage` in `src/lib/db/entities.ts`) and classification columns (`vertical`, `area`, `source_pipeline`). The machine-scoring columns (pain score, tier, employee count, revenue range) were dropped by migration 0081 with the lead-gen retirement (ADR 0060). The CRM spine. |
| `context` | Append-only log of everything learned about an entity (migration 0008). Feeds the cached attributes on `entities`. |
| `clients` | Legacy business record from the original schema. Superseded by `entities` for new work; see the rename above. |
| `contacts` | People at a business (name, email, phone, role). |
| `users` | Auth principals. `role` is `admin` or `client`; `client` users link to their business so the portal can scope their view. |

### Assessment -> quote -> engagement

This is the core funnel; the tables chain by foreign key.

| Table | Purpose |
|---|---|
| `assessments` | One assessment call. Holds the structured `extraction` JSON (the full `AssessmentExtraction` from `src/portal/assessments/extraction-schema.ts`), `problems`, and `disqualifiers`. Status flows `scheduled` -> `completed` -> `disqualified` / `converted`. |
| `quotes` | A proposal derived from an assessment. Self-references via `parent_quote_id` for versioning. Carries `line_items` JSON, `total_price` (the project price the client sees), and the authored client-facing content (migration 0021). |
| `engagements` | A signed, in-flight project. Status flows `scheduled` -> `active` -> `handoff` -> `safety_net` -> `completed`. Carries consultant attribution and next-touchpoint fields (migration 0019). |
| `milestones` | Per-engagement milestones; a `payment_trigger` flag marks the ones that release a billing milestone. |
| `engagement_contacts` | Maps contacts to an engagement with a role (`owner`, `decision_maker`, `champion`). |
| `parking_lot` | Out-of-scope requests captured during an engagement, dispositioned `fold_in` / `follow_on` / `dropped`. The scope-discipline ledger. |
| `invoices` | Deposit, completion, milestone, assessment, retainer, or implementation charges. Carries the Stripe invoice id and hosted URL. The Operator stand-up fee is its own `implementation` type (migration 0110) so the portal never titles it as a milestone; the type CHECK was retired with that change, so the vocabulary lives in `src/lib/db/invoices.ts`. |
| `services` | The commercial spine (migration 0068, ADR 0046): one row per thing a client bought, whether a consulting engagement or an Operator subscription; `engagements` and `subscriptions` point up at it by `service_id`. For the Operator it carries `recurring_price` and, since migration 0113, `payment_method` (`ach`, the default with no fee, or `card`, which adds the 3% processing line the service agreement prices), authored by the Captain on the client hub. |
| `subscriptions` | One row per Operator or hosted-agent instance a client owns (migration 0089 made this multi-instance per entity and product), linked to its `customer_configs` row by `customer_slug`; carries the Stripe subscription id once the client starts the retainer. |
| `time_entries` | Hours logged against an engagement (internal; feeds margin, not the client price). |
| `follow_ups` | Scheduled touchpoints (proposal day-2/5/7 nudges, review requests, safety-net check-ins). |

The lifecycle these tables encode is documented narratively in `/admin/playbook/customer-lifecycle`.

### Operator control plane

The `operator_*` tables (and the projection/audit tables around them) are the console-side control plane for the Operator product. They are distinct from the per-customer runtime D1 above: these live in the console database and record what SMD authored and did, not what a customer's Operator is doing minute to minute.

| Table | Purpose |
|---|---|
| `customer_configs` | The portal's read-only projection of each customer's `customer.yaml` (migration 0039). CI projects the git source of truth into this table on merge; portal pages read it on the hot path and never write it (ADR 0012). |
| `customer_config_history` / `config_change_audit` | History and audit of config changes (migrations 0045-0047). |
| `operator_provisioning_intent` | Records each admin-console attempt to author a new Operator (migration 0067). It keys on a proposed `customer_id`, not an `entity_id`, because the customer does not exist yet - a Worker cannot itself stand up a Machine (ADR 0012 puts that out of scope). |
| `operator_authority_posture` / `operator_authority_audit` | Per-customer authority posture and its audit trail (migrations 0049, 0066). |
| `operator_credential_custody` / `connector_secret_audit` | Credential-custody and connector-secret bookkeeping (migrations 0050, 0051). |
| `operator_runtime_summary` / `operator_runtime_read_audit` | The runtime-read seam projection and its access audit (migrations 0052, 0053). |
| `machine_credentials` | One row per seat: the HMAC-SHA256 hash (with per-row salt) of the key the seat's Machine presents on `POST /api/internal/heartbeat`, `/runtime-summary` and `/sentry-probe`, plus the previous hash until `prev_expires_at` so a rotation never 401s a seat mid-redeploy (migration 0114, ADR 0023 #10). Written by `operator/bin/lib/machine_credential.py` during provisioning and rotation; read by `src/lib/auth/machine-key.ts`. The plaintext lives only on the seat. A slug with no row falls back to the Worker's shared `MACHINE_HEARTBEAT_KEY` while that secret is set; unsetting it retires the shared key. |
| `operator_mcp_clerk_bindings` / `operator_mcp_auth_contract` | Clerk-identity bindings and auth contract for the Operator's MCP channel (migrations 0071-0073). |
| `cost_telemetry` / `captain_time_events` | Operator cost rows, keyed by `customer_slug` (migration 0083, ADR 0062). Written nightly by the `ss-cost-telemetry` worker from the Anthropic usage report; per-seat attribution maps `customer_configs.anthropic_workspace_id` to a seat, with reserved slugs `_org` (reconciliation) and `_unmapped` (unclaimed workspace usage). ADR 0062 superseded the per-customer-D1 placement, which was never provisioned. |
| `cost_anomaly_alerts` | Nightly spike detections over `cost_telemetry` plus Captain snooze/ack state (migration 0041), written by the `ss-cost-anomaly` worker. |
| `fleet_status` | One row per seat, keyed by `customer_slug` (migration 0093 re-keyed it from `entity_id` because several seats can share an entity). Written only by `POST /api/internal/heartbeat`, read by the admin roster and the `ss-fleet-alerts` pager. It is everything the seat reports about itself on each tick: liveness, and the alert-driving fields added as each blind spot was found. Migration 0105 added `cron_containment` (crons deliberately off is visible, not mistaken for broken); 0106 `webhook_surface_ok` and `webhook_surface_json` (the expected-tool surface the seat had been reporting and the console had been dropping); 0107 `audit_write_failures`, `audit_head`, `audit_rows` (the ledger reports on itself) and `gateway_loop_ok`, `gateway_loop_age_seconds`, `gateway_supervisor_state`, `gateway_restarts_last_hour` (a wedged event loop no longer reads green); 0112 `sticky_stop_reason` and `sticky_stop_condition` (a stopped seat says why, not just that it stopped). NULL in any of these means the seat could not answer, never zero. |
| `fleet_alert_state` | The pager's ledger: one open or resolved row per seat and condition, so a condition pages once when it opens and once when it clears rather than every minute. Rebuilt by migration 0107 (the gateway-loop conditions) and 0109 (refused or unsent escalations: the 2026-08-19 incident where a seat's deadline escalator was refused by its own gates for four days and nobody was paged). |
| `audit_head_history` | The seat's audit-chain head pinned off the Machine per heartbeat (migration 0108). The on-seat verifier can prove a row in the middle of the chain was altered but cannot see rows removed from the end; this table can, because the console keeps every head it was ever told. |
| `operator_agreement_documents` | The firm's own executed Operator paper (the service agreement and signature copies), readable on its portal Compliance surface (migration 0111). `executed_on` is NOT NULL, so nothing unsigned can be filed here; A&P holds no `engagements` rows, which is why the engagement document library could not carry these. |

Secrets never enter any of these tables; non-secret references (token pointers) are denormalized where the portal needs them, and live values stay in Infisical (see `/admin/playbook/secrets-access`).

## Conventions

- **Primary keys are ULIDs** (TEXT, lexicographically sortable by creation time).
- **Multi-tenancy by `org_id`.** Nearly every table carries it; queries filter on it. `organizations` and `magic_links` are the exceptions.
- **JSON columns carry contracts**, documented in the head comment of `migrations/0001_create_tables.sql` (the `branding`, `settings`, `extraction`, `problems`, `disqualifiers`, and `line_items` shapes). Parse these on read; never cast (per `coding-standards.md`).
- **Status columns are CHECK-constrained enums.** The allowed values are in the migration; treat the migration as authoritative when a status string looks unfamiliar.

> TODO(why): The original schema was specced against "PRD Section 7 (Data Model)" (cited in `migrations/0001_create_tables.sql` head comment), but that PRD is not in the repo, so the original rationale for some early modeling choices (for example why `clients` and `entities` briefly coexisted rather than a single rename) is reconstructed from the migration comments only.
