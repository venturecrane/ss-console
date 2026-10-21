---
title: Architecture Map
section: engineering
order: 5
owner: engineering
review_every: 90
summary: The whole stack end to end as a words-diagram - how a request flows from the browser through one Cloudflare Worker into D1, R2, and KV, and how the separate Operator plane runs one Fly.io Machine per customer
sources:
  - label: CLAUDE.md - Tech Stack, Three-Subdomain Architecture, Deployment
    href: https://github.com/venturecrane/ss-console/blob/main/CLAUDE.md
  - label: src/middleware.ts
    href: https://github.com/venturecrane/ss-console/blob/main/src/middleware.ts
  - label: wrangler.toml
    href: https://github.com/venturecrane/ss-console/blob/main/wrangler.toml
  - label: astro.config.mjs
    href: https://github.com/venturecrane/ss-console/blob/main/astro.config.mjs
  - label: src/lib/operator/runtime-read.ts
    href: https://github.com/venturecrane/ss-console/blob/main/src/lib/operator/runtime-read.ts
---

## Two planes

The system has two distinct planes that share a vocabulary but not a runtime.

1. **The console plane** - one Astro SSR application running as a single Cloudflare Worker named `ss-web`. It serves the marketing site, the admin console, and the client portal, and it owns all structured business data (leads, clients, engagements, invoices, Operator config). This is the code in this repo.
2. **The Operator plane** - one Fly.io Machine per customer, each running the NousResearch Hermes Agent runtime plus the SMD plugin overlay. This is where a live Operator actually works. Its code lives in a separate repo, `venturecrane/hermes-smd-overlay`, and on the Machine images. See `/admin/playbook/operator-platform`.

The console plane never reaches into a Machine's database directly. The only path between the planes is a narrow, read-only, audited HTTPS seam described at the end of this page. Keep the two planes separate in your head and the rest of the architecture follows.

## The console plane: request flow

Every request to `smd.services`, `admin.smd.services`, or `portal.smd.services` hits the same Worker. There are no separate deployments per subdomain. The flow:

1. **Browser to Cloudflare.** All three custom domains are bound to the one Worker `ss-web` (`wrangler.toml`, `[assets]` and the domain notes). Cloudflare's edge terminates TLS and routes the request to the Worker.
2. **Worker runs first.** `run_worker_first = true` in the `[assets]` block (`wrangler.toml`) forces every request through the Worker before the static-asset binding can answer. This is load-bearing: without it, a request for `/` could be short-circuited by a prerendered asset and never reach the middleware that does subdomain routing and auth.
3. **Astro SSR middleware.** `src/middleware.ts` runs as a `sequence()` of `clerkMiddleware` then the SS middleware. It does three things in order:
   - **Subdomain routing.** It inspects `hostname`. On `admin.smd.services` it prepends `/admin` to the path (unless the path already starts with `/admin`, `/api/admin`, `/auth`, or `/api/auth`); on `portal.smd.services` it prepends `/portal` unless the path already starts with `/portal`, `/api/portal`, `/auth`, or `/api/auth`. The admin and portal source files live under `src/pages/admin/*` and `src/pages/portal/*` - the subdomain is a front door, not a separate build (`src/middleware.ts`, `handleSubdomainRewrite`). `smd.services/admin/*` and `smd.services/portal/*` 301-redirect to the admin and portal hosts, and legacy auth paths such as `/auth/login` redirect to `/auth/sign-in` (`src/lib/routing/legacy-redirects.ts`).
   - **Auth.** Clerk owns identity for both admin and portal. On admin paths, `resolveAdminSessionFromClerk` maps the Clerk user id to the local `users` row and gates on `role='admin'`, synthesizing the legacy session shape into `locals.session` so existing call sites keep working (`src/lib/auth/admin-session-shim.ts`). On portal paths, Clerk is primary with a legacy magic-link session accepted as a fallback during the transition (`src/middleware.ts`, `resolveLegacyPortalSession`).
   - **Observability.** The handler is wrapped by `withSentryRequestHandler` (`src/lib/observability/sentry.ts`); when `SENTRY_DSN` is unset the wrapper is a no-op.
4. **Page or API handler.** The rewritten path resolves to an Astro page (`*.astro`) or an API route (`src/pages/api/*`). Env is read directly via `import { env } from 'cloudflare:workers'` - the v13 adapter removed `Astro.locals.runtime` (CLAUDE.md, Deployment).
5. **Storage.** Handlers read and write the Worker's bound resources:
   - **D1** (binding `DB`, database `ss-console-db`) - all structured business data. See `/admin/playbook/data-model`.
   - **R2** - three buckets: `STORAGE` (documents, transcripts, SOWs), `CONSULTANT_PHOTOS` (public consultant images), and `CUSTOMER_CONFIG` (the authoritative live Operator config and voice vaults, `smd-customer-config`).
   - **KV** - `SESSIONS` (session storage) and `BOOKING_CACHE` (booking rate-limit plus the public-assessment turn/cost ceiling).

All bindings are declared in `wrangler.toml`. A separate staging Worker, `ss-web-staging` (`[env.staging]` in `wrangler.toml`), runs on a single `workers.dev` host with its own `*-staging` D1, R2, and KV resources, so nothing in it touches production data; because it has no subdomains, admin and portal are reached by path there. The canonical outbound origins (`APP_BASE_URL`, `ADMIN_BASE_URL`, `PORTAL_BASE_URL`) are built from env vars, never from the inbound request host (`src/lib/config/app-url.ts`).

## Background Workers

Some work does not belong in the request path. Three sibling Workers live under `workers/`:

- **Cost** - `cost-telemetry` and `cost-anomaly` handle Operator cost ingest and anomaly detection.
- **Alerts** - `fleet-alerts` (Worker `ss-fleet-alerts`) is the pager. It reads `ss-console-db` on a 2-minute cron (plus a weekly Monday run) and mails `team@smd.services` when a seat's heartbeat goes stale, work runs overdue, a connector is down, a token nears expiry, or the public site fails its health poll (`workers/fleet-alerts/wrangler.toml`).

The automated lead-gen pipelines (`job-monitor`, `review-mining`, `enrichment-workflow`, `new-business`, `scan-workflow`, `social-listening`) that used to live here, along with the `ENRICHMENT_WORKFLOW_SERVICE` service binding and the `LEAD_INGEST_API_KEY` ingest path, were retired root-and-branch on 2026-07-01 (PRs #1610/#1616). The cost workers and the pager are all that remain; `.github/workflows/deploy.yml` deploys all three.

## The Operator plane

The Operator plane is described in full at `/admin/playbook/operator-platform`; what matters for the system map is its shape and its one connection to the console.

- **One Machine per customer.** Each customer gets a dedicated Fly.io Machine `hermes-{customer-slug}` running Hermes plus the overlay (per ADR 0007). There is no shared runtime. Cross-customer access is architecturally impossible, not merely denied in code.
- **Per-Machine storage.** Each Machine carries its own D1, R2, and OAuth token volume, namespaced to that one customer. The customer's OAuth tokens live on the Machine's Fly volume, never in the console plane (per ADR 0010).
- **Config flows to the Machine via R2, from two writers that never share a key.** `customer.yaml` is git-authoritative and published to `vaults/<slug>/customer.yaml` by CI on merge (`scripts/ci-publish-customer-configs.sh`); the console does not write it, because a portal write would be clobbered by the next unrelated merge to that slug. The customer's own authored voice and format specs go the other way, from the portal to `vaults/<slug>/output-classes.json` (`src/lib/operator/output-class-specs.ts`, ADR 0083) - prose a client edits, which no portal actor could put in git. Each writer is structurally barred from the other's key. The on-Machine root appliers pull both from R2 (`wrangler.toml`, `CUSTOMER_CONFIG` block). A third key belongs to the seat: every boot uploads the image's skill manifest to `vaults/<slug>/image-manifest.json` (`operator/templates/entrypoint-image-manifest.sh`), and the CI publisher refuses a `customer.yaml` naming a skill or pre-run script the running image lacks, or adding one when the manifest cannot be read (`scripts/check-config-against-image.mjs`); the seat rebuild publishes such a config with its image. A seat that still boots an image older than its config skips what it lacks and pages team@ rather than failing the boot (overlay `bootstrap/translate.py`, `_drop_unrunnable`).

## The seam between the planes

The admin console needs to show an Operator's live runtime state (audit log, drafts, activity), but that state lives on the customer's isolated Machine D1, which the console cannot query across the isolation boundary. The bridge is the **runtime read seam** (ADR 0043 path A), implemented in `src/lib/operator/runtime-read.ts`.

It encodes four invariants and never bends them:

1. **Single customer per call** - the function takes one `customerSlug`; there is no list form and no surface joins across customers.
2. **Read-only** - the transport exposes `read` and nothing else; no mutation path exists.
3. **Audited at the console** - every read records who looked at what, separate from the operator's own runtime audit log.
4. **Fail-closed** - a transport error resolves to an empty result with a reason, never a throw that breaks a portal render.

The per-customer read key is derived `HMAC-SHA256(master, slug)` from `OPERATOR_RUNTIME_READ_SECRET`, which lives only on `ss-web` and never on a Machine (`wrangler.toml`, secrets note). The Machine-side read endpoint lives in the overlay repo; this module is the console side of that seam. The console reads Operator runtime; it never writes it. Governance of what the Operator may do is covered at `/admin/playbook/autonomy-governance`.

## The enterprise control layer

Across all of SMD's ventures sits the **crane MCP** server - the enterprise context and control layer that loads session context, documentation, and handoff state, and provides the verify and memory machinery. It is invoked by agents working in this repo (CLAUDE.md, Session Start), not by the running web application. It is part of how the platform is *built and operated*, not part of the request path.

## External services at a glance

The console plane depends on Cloudflare (Workers, D1, R2, KV), Clerk (identity), Anthropic (LLM), and a billing and document stack (Stripe, SignWell, Resend). The Operator plane adds Fly.io (Machines) and Google Workspace (managed-mailbox OAuth). The full inventory, what each is for, and where its credentials live is at `/admin/playbook/integrations-tooling`. The repository layout that implements all of the above is at `/admin/playbook/repository-map`.
