---
title: Repository Map
section: system
order: 2
summary: The "where is the code for X" index - the top-level directory layout, the key src/lib modules and what each does, and how src/pages splits across marketing, admin, portal, and api
sources:
  - label: CLAUDE.md - Build Commands, Tech Stack
    href: https://github.com/venturecrane/ss-console/blob/main/CLAUDE.md
  - label: package.json
    href: https://github.com/venturecrane/ss-console/blob/main/package.json
  - label: src/middleware.ts
    href: https://github.com/venturecrane/ss-console/blob/main/src/middleware.ts
---

## Top-level directories

The repo is `venturecrane/ss-console`. From the root:

| Path | What lives here |
|---|---|
| `src/` | The Astro SSR application - marketing, admin, portal, API, and all the business logic in `src/lib/`. The bulk of the console plane. |
| `operator/` | The Operator plane's authored content and tooling: vertical skill bodies, the safety substrate, capability adapters, connectors, per-customer `customer.yaml` files, the workspace broker, provisioning scripts. Python and YAML, not the Worker. |
| `workers/` | Two sibling Cloudflare Workers run outside the request path: `cost-telemetry` and `cost-anomaly` (Operator cost ingest + anomaly detection). The lead-gen pipelines that used to live here were retired 2026-07-01 (PRs #1610/#1616). |
| `migrations/` | D1 schema migrations for the console database `ss-console-db`, numbered `0001_*` upward (107 forward migrations plus a `rollbacks/` directory). Applied with `wrangler d1 migrations apply`. See `/admin/playbook/data-model`. |
| `tests/` | Vitest suites, including the policy-enforcing tests cited in CLAUDE.md (`forbidden-strings.test.ts`, `intake-questionnaire.test.ts`). |
| `docs/` | All venture documentation: `adr/` (decision records), `handbook/` (this manual), plus `design/`, `runbooks/`, `specs/`, `security/`, and more. The full map is at `/admin/playbook/docs-map`. |
| `.github/` | CI workflows, including the fabrication and scope merge gates (`scope-deferred-todo.yml`, `unmet-ac-on-close.yml`). See `/admin/playbook/deployment-release`. |
| `scripts/` | One-off and operational TypeScript / shell scripts (data backfills, customer-config projection, migration verification). |
| `bin/` | Small operator-facing shell entry points, e.g. `reauth-connector.sh`. |
| `public/` | Static assets served as-is: `favicon.svg`, `robots.txt`, the OG image, the consultant photo. |

Build, test, and deploy commands for these are in CLAUDE.md (Build Commands) and at `/admin/playbook/building-the-platform`.

## The src/ layout

```
src/
  middleware.ts     subdomain routing + Clerk/legacy auth + Sentry (the front door)
  env.d.ts          Cloudflare.Env binding types
  layouts/          AdminLayout.astro, Base.astro
  components/        shared Astro components
  styles/           global.css (Tailwind v4)
  pages/            all routes (see below)
  lib/              all business logic (see below)
  portal/           assessment logic shared with the public assessment flow
  scripts/          in-app scripts
```

## src/pages - the route split

`src/pages/` holds every route. Which host serves which is decided by `src/middleware.ts` (subdomain rewrite), not by directory separation - the admin and portal trees live here and the subdomain prepends their prefix.

- **Marketing** (`smd.services`) - top-level `.astro` files: `index.astro`, `operator.astro`, `about.astro`, `industries.astro`, `patterns.astro`, `contact.astro`, `book.astro`, `assessment.astro`, `get-started.astro` (live only as the `?booked=1` post-booking questionnaire; otherwise it 301s home), `privacy.astro`, `terms.astro`, and `404.astro`, plus `packs/` (the per-vertical pack pages). The `consulting`, `why`, and `/scan` / `/scorecard` lead-magnet routes were removed and now 301 to home (the marketing consolidation + Outside View retirement); the redirect rules live in `src/lib/routing/legacy-redirects.ts`. Treat those paths as legacy, not live surfaces.
- **Admin** (`admin.smd.services` to `/admin/*`) - `src/pages/admin/`: `entities` (Leads), `clients`, `services`, `billing`, `operator`, `analytics`, `settings`, plus `assessments`, `engagements`, `follow-ups`. (The `generators` surface was removed with the lead-gen retirement.) Full surface map at `/admin/playbook/admin-console`.
- **Portal** (`portal.smd.services` to `/portal/*`) - `src/pages/portal/`: `quotes`, `engagement`, `documents`, `invoices`, `products`. See `/admin/playbook/client-portal`.
- **API** (`src/pages/api/`) - JSON and webhook endpoints: `admin/` (admin mutations and fleet reads), `operator/[customer]/`, `portal/`, `auth/`, `oauth/`, `booking/`, `assessment/`, `intake/`, `webhooks/` (`stripe`, `signwell`, `resend`, `sentry`, `healthchecks`), plus `contact.ts`, `events.ts`, `health.ts`, `mcp.ts`.

## src/lib - the "where is the code for X" index

`src/lib/` is where the business logic lives. The modules:

| Module | Responsibility |
|---|---|
| `admin/` | View-model builders for admin pages: fleet roster, client hub, billing view, config view, connectors view, cost query, authority writes. |
| `api/` | Shared API response helpers. |
| `assessment/` | The public live-assessment prompts and per-session turn/cost ceiling (`session.ts`). |
| `auth/` | Identity: Clerk bridge, the admin-session shim, legacy magic-link sessions, machine and API keys, health read key. |
| `booking/` | The Calendly-replacement booking system: availability, holds, Google Calendar sync, ICS generation, intake questionnaire, encryption, rate limiting. |
| `category.ts` | The single Operator category constant ("Managed Operator"). |
| `claude/` | LLM-backed business flows: assessment extraction and the live assessment. (The assessment-to-quote generator was removed 2026-09-10; no route called it.) |
| `config/` | Canonical app URLs (`app-url.ts`), brand and firm-contact constants. |
| `db/` | D1 data access, one file per domain (entities, engagements, contacts, assessments, analytics, quotes, invoices, milestones, and more). The query layer over `ss-console-db`. |
| `email/` | Resend transactional email, templates, booking and follow-up emails. |
| `entities/` | Entity domain logic: slug, recompute, meeting substate, list sort. |
| `follow-ups/` | The follow-up cadence scheduler. |
| `llm/` | Model id constants (`models.ts`). |
| `oauth/` | OAuth provider plumbing for the console's own integrations: state, store, audit, providers. |
| `observability/` | Sentry wiring (`sentry.ts`), no-op when `SENTRY_DSN` is unset. |
| `operator/` | The console side of the Operator plane: `output-class-specs.ts` (write the customer's authored output-class specs to `vaults/<slug>/output-classes.json` in R2 - the console's only writer into that bucket, and never `customer.yaml`, which CI publishes from git), `runtime-read.ts` (the read seam), `customer-yaml/` (authoring and validation, one file per `customer.yaml` section), `capabilities/types.ts` (the closed capability-name vocabulary; the typed adapter layer that once sat beside it was superseded by the connector platform, ADR 0053, and removed 2026-09-10), `mcp/` (the MCP route and audit), `authority.ts`, credential custody and secret transport, the Fly app registry. |
| `operator-packs/` | Shared vertical-pack helpers (`shared.ts`). |
| `pdf/` | PDF rendering and the SOW template (`sow-template.tsx`). |
| `portal/` | Client-portal logic: states, ledger, formatters, product access, the operator-access and customer-config projections. |
| `signwell/` | SignWell e-signature client and types. |
| `sow/` | SOW service, finalize, and store. |
| `storage/` | R2 access (`r2.ts`). |
| `stripe/` | Stripe invoicing client and types. |
| `ui/` | Server-rendered UI helpers: status badge, pipeline badge, admin action button. |
| `webhooks/` | Inbound webhook handlers (Stripe, SignWell, Resend) with their tests. |

## operator/ at a glance

The `operator/` tree is the Operator plane's content, distinct from the `src/lib/operator/` console glue above. Key subdirectories: `skills/` (vertical skill bodies), `verticals/` (per-vertical packs - law-firm, accounting, dental, insurance, and others), `safety-substrate/` (invariants, citation and identifier filters, sticky-stop), `adapter/` (audit log, cost ingest, inbound envelope), `connectors/`, `capabilities`, `workspace_broker/` (the managed-mailbox authorization boundary), `customers/` (per-customer `customer.yaml`), `contracts/` (the block and hook surface contracts), `bin/` and `templates/` (provisioning and bootstrap). Its architecture is covered at `/admin/playbook/operator-platform` and its connectors at `/admin/playbook/connectors-channels`.
