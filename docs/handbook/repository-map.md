---
title: Repository Map
section: engineering
order: 6
owner: engineering
review_every: 90
last_reviewed: "2026-10-21"
summary: The "where is the code for X" index - the top-level directory layout, the key src/lib modules and what each does, and how src/pages splits across marketing, admin, portal, and api
sources:
  - label: CLAUDE.md - Venture Repositories, Enterprise Rules
    href: https://github.com/venturecrane/ss-console/blob/main/CLAUDE.md
  - label: package.json
    href: https://github.com/venturecrane/ss-console/blob/main/package.json
  - label: src/middleware.ts
    href: https://github.com/venturecrane/ss-console/blob/main/src/middleware.ts
---

This page answers "where is the code for X": which repository, which directory, which module. An administrator needs it when an agent's report names a file path or a module and you want to know what part of the business it belongs to.

## The two repositories

The venture spans two repositories, and moving between them is ordinary work inside one venture (CLAUDE.md, Venture Repositories):

| Repository | Visibility | What lives there |
|---|---|---|
| `venturecrane/ss-console` | Public | This repo: the marketing site, admin console, client portal, Operator skills, contracts and config, each seat's `customer.yaml`, and the Operator runtime overlay at `operator/hermes-smd-overlay/` (the trust plugin, gates, the Python side of the connectors, bootstrap). |
| `venturecrane/engagements` | Private | Client material: dossiers, correspondence, proposals, agreements, under the same `operator/customers/<slug>/` prefix used here (ADR 0081). |

Until 2026-10-22 the overlay was a third repository, `venturecrane/hermes-smd-overlay`, pinned into the seat image by `OVERLAY_REF`, with contract fixtures copied across by hand. ADR 0095 folded it into this repo: the image is built from the local tree, a runtime change is one pull request, and the version a seat reports is the ss-console commit its image was built from. The standalone repo is locked, and is archived once every seat runs a folded image; its history is kept here as the branch `overlay-history`.

## Top-level directories

The repo is `venturecrane/ss-console`. From the root:

| Path | What lives here |
|---|---|
| `src/` | The Astro SSR application - marketing, admin, portal, API, and all the business logic in `src/lib/`. The bulk of the console plane. |
| `operator/` | The Operator plane's authored content and tooling: vertical skill bodies, the safety substrate, capability adapters, connectors, per-customer `customer.yaml` files, the workspace broker, provisioning scripts, and the seat runtime overlay (`operator/hermes-smd-overlay/`, the Hermes plugins and bootstrap baked into every seat image; ADR 0095). Python and YAML, not the Worker. |
| `workers/` | Three sibling Cloudflare Workers run outside the request path: `cost-telemetry` (`ss-cost-telemetry`, the nightly Operator cost ingest) and `cost-anomaly` (`ss-cost-anomaly`, nightly spike detection), and `fleet-alerts` (`ss-fleet-alerts`, the pager: it runs every 2 minutes and mails `team@smd.services` when a seat condition opens or clears, plus a Monday digest; `workers/fleet-alerts/wrangler.toml`). What each one watches is at `/admin/playbook/architecture-map`. The lead-gen pipelines that used to live here were retired 2026-07-01 (PRs #1610/#1616). Any other directory found under `workers/` in a local checkout holds no tracked files; it is a leftover, not live code. |
| `migrations/` | D1 schema migrations for the console database `ss-console-db`, numbered `0001_*` upward (forward migrations, plus a `rollbacks/` directory; the directory is the count). Applied with `wrangler d1 migrations apply`. See `/admin/playbook/data-model`. |
| `tests/` | Vitest suites, including the policy-enforcing tests cited in CLAUDE.md (`forbidden-strings.test.ts`, `intake-questionnaire.test.ts`) and the handbook gate (`handbook-integrity.test.ts`). Each worker carries its own tests beside its source. |
| `docs/` | All venture documentation: `adr/` (decision records), `handbook/` (this manual), plus `design/`, `runbooks/`, `specs/`, `security/`, and more. The full map is at `/admin/playbook/docs-map`. |
| `.github/` | CI workflows, including the fabrication and scope merge gates (`scope-deferred-todo.yml`, `unmet-ac-on-close.yml`). See `/admin/playbook/deployment-release`. |
| `scripts/` | Operational TypeScript and shell scripts: the CI publishers and reconcilers for `customer.yaml`, skills and the obligation register (`ci-publish-*`, `ci-reconcile-*`), customer-config projection, the handbook generators (`handbook-*.ts`), the API inventory, and one-off checks. |
| `bin/` | Two operator-facing shell entry points: `reauth-connector.sh` and `connect-smokeball.sh`. |
| `public/` | Static assets served as-is: `favicon.svg`, `robots.txt`, the OG image, the consultant photo, the analytics bootstrap scripts (`js/ga4-init.js`, `js/meta-pixel-init.js`), and the public evidence-packet signing key (`keys/`). |
| `.claude/`, `.design/`, `.stitch/` | Agent session machinery (hooks, skills, `bin/` helpers such as `register` and `mission`), design explorations, and UI-drift audit output. Not shipped. |

Build, test, and deploy commands are the `scripts` in `package.json` (`npm run verify` is the full local gate) and are explained at `/admin/playbook/building-the-platform` and `/admin/playbook/deployment-release`.

## The src/ layout

```
src/
  middleware.ts     subdomain routing + Clerk/legacy auth + Sentry (the front door)
  env.d.ts          Cloudflare.Env binding types
  content.config.ts the handbook content collection (docs/handbook/ rendered at /admin/playbook)
  layouts/          AdminLayout.astro, Base.astro, PortalShell.astro
  components/       shared Astro components
  styles/           global.css (Tailwind v4)
  pages/            all routes (see below)
  lib/              all business logic (see below)
  portal/           the assessment extraction schema (portal/assessments/)
  scripts/          client-side scripts for the booking, intake and live-assessment pages
```

## src/pages - the route split

`src/pages/` holds every route. Which host serves which is decided by `src/middleware.ts` (subdomain rewrite), not by directory separation - the admin and portal trees live here and the subdomain prepends their prefix.

- **Marketing** (`smd.services`) - top-level `.astro` files: `index.astro`, `operator.astro`, `about.astro`, `industries.astro`, `patterns.astro`, `contact.astro`, `book.astro`, `assessment.astro` (plus `assessment/report-preview.astro`), `get-started.astro` (live only as the `?booked=1` post-booking questionnaire; otherwise it 301s home), `privacy.astro`, `terms.astro`, `security.astro` (the public security overview), `trust.astro` (where a holder of a compliance evidence packet verifies its signature), `ai-disclosure.astro`, `agent.astro` plus `agent/` (the Hosted Agent storefront and its terms and thanks pages, ADR 0067), and `404.astro`, plus `packs/` (the per-vertical pack pages), `case-studies/`, and `book/manage/` (manage an existing booking). `auth/` holds the shared Clerk sign-in, sign-up and post-sign-in pages plus the legacy magic-link `verify` page, reached on every host. `design-preview/` and `dev/` hold fixture-driven component previews, not marketing pages. The `consulting`, `ai`, `/scan`, `/scorecard` and `/outside-view` routes were removed and now 301 to home, and `/why` 301s to `/operator#compare` (the marketing consolidation + Outside View retirement); the redirect rules live in `src/lib/routing/legacy-redirects.ts`. Treat those paths as legacy, not live surfaces.
- **Admin** (`admin.smd.services` to `/admin/*`) - `src/pages/admin/`: `entities` (Leads), `clients`, `services`, `billing`, `operator` (the fleet, per-seat pages, alerts, costs, requests and provisioning), `hosted-agent` (the Hosted Agent concierge queue, ADR 0067), `playbook` (this handbook), `settings`, plus `assessments`, `engagements`, `follow-ups`. (The `generators` surface was removed with the lead-gen retirement, and `analytics` was retired in #1930.) Full surface map at `/admin/playbook/admin-console`.
- **Portal** (`portal.smd.services` to `/portal/*`) - `src/pages/portal/`: `engagement/` (the engagement page, its `proposals/` and `documents/`), `billing/` (`invoices/` and `subscriptions/`), and `products/` (`operator/[instance]/` with its account, activity, calendar, compliance, configure, connections, onboarding, people, scope, settings, skills, team and work pages, plus the connector `oauth/` callback; and `hosted-agent/`). The older top-level `quotes`, `documents` and `invoices` routes were removed in #1790. See `/admin/playbook/client-portal`.
- **API** (`src/pages/api/`) - JSON and webhook endpoints: `admin/` (admin mutations and fleet reads), `operator/[customer]/`, `portal/`, `auth/`, `booking/`, `assessment/`, `intake/`, `checkout/` (the Hosted Agent buy entry), `internal/` (Machine-to-console seams: heartbeat, request card, runtime summary, Sentry probe), `webhooks/` (`stripe`, `signwell`, `resend`, `sentry`, `healthchecks`), plus `contact.ts`, `events.ts`, `health.ts`, `intake.ts` (standalone intake without a booking), `mcp.ts`. Connector OAuth callbacks are not under `api/`; they live at `src/pages/portal/products/operator/oauth/[connector]/`.

## src/lib - the "where is the code for X" index

`src/lib/` is where the business logic lives. The modules:

| Module | Responsibility |
|---|---|
| `admin/` | View-model builders for admin pages: fleet roster, client hub, billing view, config view, connectors view, cost query, authority writes. |
| `api/` | Shared API response helpers. |
| `assessment/` | The public live-assessment prompts and per-session turn/cost ceiling (`session.ts`). |
| `auth/` | Identity: Clerk bridge and appearance, the admin-session shim, legacy magic-link sessions (D1 `sessions` with a KV cache), sign-in redirects, portal login events, and `machine-key.ts` (verifies the per-seat key a Machine presents to `/api/internal/*`). |
| `booking/` | The Calendly-replacement booking system: availability, holds, Google Calendar sync, ICS generation, intake questionnaire, encryption, rate limiting. |
| `category.ts` | The single Operator category constant ("Managed Operator"). |
| `claude/` | LLM-backed business flows: assessment extraction and the live assessment. (The assessment-to-quote generator was removed 2026-09-10; no route called it.) |
| `config/` | Canonical app URLs (`app-url.ts`), brand and firm-contact constants. |
| `constants.ts` | Values shared by the web app and the workers, such as the SMD organization id. |
| `db/` | D1 data access, one file per domain (entities, engagements, contacts, assessments, meetings, quotes, invoices, milestones, services, subscriptions, obligations, and more). The query layer over `ss-console-db`. |
| `email/` | Resend transactional email, templates, booking and follow-up emails. |
| `entities/` | Entity domain logic: slug, recompute, meeting substate, list sort. |
| `follow-ups/` | The follow-up cadence scheduler. |
| `fonts.ts` | The web font pipeline (the two-family typography system). |
| `handbook/` | This handbook's section definitions (`sections.ts`) and the freshness and reading-order rules each page renders (`freshness.ts`). |
| `identity/` | The canonical email normalization used as the identity join key (`email.ts`, #2315). |
| `llm/` | Model id constants (`models.ts`). |
| `marketing/` | Paid-ad attribution capture (`attribution.ts`) and Meta Conversions API events (`meta-capi.ts`), ADR 0066. |
| `oauth/` | OAuth provider plumbing for the console's own integrations: state, store, audit, providers. |
| `observability/` | Sentry wiring (`sentry.ts`), no-op when `SENTRY_DSN` is unset. |
| `operator/` | The console side of the Operator plane: `output-class-specs.ts` (write the customer's authored output-class specs to `vaults/<slug>/output-classes.json` in R2 - the console's only writer into that bucket, and never `customer.yaml`, which CI publishes from git), `runtime-read.ts` (the read seam), `customer-yaml/` (authoring and validation, one file per `customer.yaml` section), `capabilities/types.ts` (the closed capability-name vocabulary; the typed adapter layer that once sat beside it was superseded by the connector platform, ADR 0053, and removed 2026-09-10), `mcp/` (the MCP route and audit), `authority.ts`, credential custody and secret transport, the Fly app registry. |
| `operator-packs/` | Shared vertical-pack helpers (`shared.ts`). |
| `pdf/` | PDF rendering and the SOW template (`sow-template.tsx`). |
| `pricing/` | The card-processing fee rule (`card-fee.ts`). |
| `portal/` | Client-portal logic: states, formatters, home cards, billing, product access, the operator-access and customer-config projections, and `portal/operator/` (the readers and gates behind the Operator console pages). |
| `routing/` | Legacy-path redirect rules (`legacy-redirects.ts`). |
| `security/` | Cross-site request check, encryption, security response headers, and the signed-token codec. |
| `seo/` | JSON-LD serialization and the sitemap filter. |
| `signwell/` | SignWell e-signature client and types. |
| `sow/` | SOW service, finalize, and store. |
| `storage/` | R2 access (`r2.ts`). |
| `stripe/` | Stripe invoicing client and types. |
| `trust/` | The evidence-packet signing key registry (`signing-keys.ts`, #2122). |
| `ui/` | Server-rendered UI helpers: status badge, pipeline badge, admin action button. |
| `webhooks/` | Inbound webhook handlers (Stripe, SignWell, Resend) with their tests. |

## operator/ at a glance

The `operator/` tree is the Operator plane's content, distinct from the `src/lib/operator/` console glue above. Key subdirectories: `skills/` (vertical skill bodies), `verticals/` (per-vertical packs - law-firm, accounting, dental, insurance, and others), `safety_substrate/` (invariants, citation and identifier filters, sticky-stop), `adapter/` (audit log, cost ingest, inbound envelope), `connectors/`, `workspace_broker/` (the managed-mailbox authorization boundary), `customers/` (per-customer `customer.yaml`, plus each seat's console-side `seat.yaml`; see "The seat inventory" below), `contracts/` (the block and hook surface contracts), `bin/` and `templates/` (provisioning and bootstrap), `fleet/` (the rollout ring definitions, `rings.yaml`), `ops-machine/` (the ops machine image, crontab and Fly config), `runners/` (code that runs on the seat outside the agent: the Machine's stop supervisor and the medical-chronology runner), `roles/` (authored role templates, today `couples`), the test rigs (`rehearsal/`, the adversarial shadow-firm suite run against a rig seat; `voice-gate/`, the voice blind-test harness; `assessment-eval/`, the interview-quality harness; `grading/`, per-skill ship readiness), and `observability/heartbeat-fields.json` (what a seat reports on each heartbeat). `operator/migrations/` is the never-built per-customer D1 schema, kept as history (its README). `bundles/` is an empty catalog since its one bundle was retired. Its architecture is covered at `/admin/playbook/operator-platform` and its connectors at `/admin/playbook/connectors-channels`.

### The seat inventory

Which Operator seats exist is defined in one place. A seat is a folder `operator/customers/<slug>/` that holds a `customer.yaml` and is neither a `_`-prefixed provisioning scaffold (`_template`, `_hosted-template`, `_couples-template`) nor a `<slug>.decommissioned.<date>` tombstone. Each seat also carries a `seat.yaml` naming its Fly app, its release ring, the day it was authored, and, when the seat is not its own client, the client its obligations roll up to. `seat.yaml` is read only by the console tooling, never published to a seat, so editing it cannot stop a seat from booting.

`scripts/lib/seat-inventory.ts` loads it, and `npm run gen:seat-registries` (`scripts/gen-seat-registries.ts`) writes the three files derived from it: the Fly app allow-list (`src/lib/operator/fly-app-registry.generated.ts`), the release rings (`operator/fleet/rings.yaml`), and the obligation register's seat-to-client rollup (`scripts/lib/seat-clients.generated.mjs`). Those files are committed and must not be edited by hand; `tests/seat-inventory.test.ts` fails while any is stale. The same folder rule exists in Python (`operator/bin/lib/seat_dirs.py`, used by the fleet tool and the reconcilers) and in bash (`scripts/lib/seat-dir.sh`, used by the CI projection scripts), and the same test runs all three over one set of example folders.
