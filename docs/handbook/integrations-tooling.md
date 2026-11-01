---
title: Integrations & Tooling
section: engineering
order: 12
owner: engineering
review_every: 90
last_reviewed: "2026-10-21"
summary: The external-service inventory - every third party the platform depends on and what each one is for, derived from package.json dependencies, wrangler.toml bindings, and CLAUDE.md
sources:
  - label: package.json (dependencies)
    href: https://github.com/venturecrane/ss-console/blob/main/package.json
  - label: wrangler.toml (bindings + secret list)
    href: https://github.com/venturecrane/ss-console/blob/main/wrangler.toml
  - label: CLAUDE.md - Three-Subdomain Architecture
    href: https://github.com/venturecrane/ss-console/blob/main/CLAUDE.md
  - label: src/env.d.ts (the Worker's declared secrets and vars)
    href: src/env.d.ts
  - label: Operator env and secret custody contract
    href: operator/contracts/env-consumption.yaml
---

This page lists every outside service the platform depends on and what each one is for.
An administrator needs it when an agent's report sends you here, or when a vendor's email,
invoice or outage notice arrives and you need to know what that vendor does for SMD.

## How to read this page

This is the inventory of external services the platform touches and what each is for. It does not list any credential values, and it is not the rotation runbook. For *where* a given secret lives and *how* to rotate it, go to `/admin/playbook/secrets-access`. The grounding here is `package.json` dependencies, `wrangler.toml` bindings and its documented secret list, the Worker's typed environment in `src/env.d.ts` (which lists secrets the `wrangler.toml` comment block does not), the Operator's env contract in `operator/contracts/env-consumption.yaml`, and CLAUDE.md.

## Infrastructure

| Service | Used for | Grounded in |
|---|---|---|
| **Cloudflare Workers** | The runtime. One Worker, `ss-web`, serves all three subdomains via Astro SSR (`@astrojs/cloudflare`). | `wrangler.toml` (`name = "ss-web"`), `astro.config.mjs`. |
| **Cloudflare D1** | The structured database `ss-console-db` - all business data. Bound as `DB`. | `wrangler.toml`, `[[d1_databases]]`. |
| **Cloudflare R2** | Object storage in three buckets: `STORAGE` (`ss-console-storage`: documents, transcripts, SOWs), `CONSULTANT_PHOTOS` (`smd-consultant-photos`, public consultant images), `CUSTOMER_CONFIG` (`smd-customer-config`: each seat's `customer.yaml`, written by CI on merge, and the client-authored output-class specs, which are the console's only write into that bucket). | `wrangler.toml`, `[[r2_buckets]]`. |
| **Cloudflare KV** | Session storage (`SESSIONS`) and booking rate-limit plus the public-assessment turn/cost ceiling (`BOOKING_CACHE`). | `wrangler.toml`, `[[kv_namespaces]]`. |
| **Cloudflare staging** | A separate staging Worker, `ss-web-staging`, with its own `-staging` D1, R2 and KV, so a staging deploy never touches production data. | `wrangler.toml`, `[env.staging]`. |
| **Fly.io** | The Operator plane. One Fly app and Machine per seat (`hermes-{slug}`) running Hermes plus the overlay. A seat's own storage is SQLite on its `/opt/data` Fly volume (which also holds its OAuth tokens) plus its R2 skills bucket (`ss-operator-{slug}-skills`); the per-customer D1 and Vectorize bindings ADR 0007 first described were never built. The console also calls the Fly API (`FLY_API_TOKEN`) to set a seat's Google token secret and restart its Machine on connect or re-consent. | ADR 0007 (storage-model note); `src/env.d.ts`. See `/admin/playbook/operator-platform`. |
| **Fly registry app `hermes-fleet`** | Holds the one built image per fleet release; it runs no Machines. `fleet build` pushes there and `fleet release` rolls the image out to the seats. | `operator/bin/lib/fleet/build.py`. See `/admin/playbook/deployment-release`. |
| **healthchecks.io** | Per-customer liveness checks; a check whose grace period expires posts to the console's webhook, authenticated by a shared bearer token (healthchecks.io does not sign webhooks). The decommission pipeline has a step to cancel a seat's check, but its healthchecks.io client is not wired yet: the step is a stub, the pipeline lists it as unwired, and a live decommission refuses to run while it is. | `src/pages/api/webhooks/healthchecks.ts`; `operator/bin/lib/decommission.py`; ADR 0023. |

## Identity and auth

| Service | Used for | Grounded in |
|---|---|---|
| **Clerk** | Identity for both the admin console and the client portal (users, orgs, memberships, invitations, sessions). The production instance binds to the `smd.services` apex with auth subdomains (`clerk.`, `accounts.`, `clkmail.smd.services`). Coexists with the legacy magic-link path during transition. | `@clerk/astro` in `package.json`; `astro.config.mjs`; `src/middleware.ts`. Needs `PUBLIC_CLERK_PUBLISHABLE_KEY` (build) and `CLERK_SECRET_KEY` (runtime). |

## LLM and voice

| Service | Used for | Grounded in |
|---|---|---|
| **Anthropic (Claude)** | The LLM behind the live assessment and assessment extraction (`src/lib/claude/*`). The Operator plane also runs on Claude via the per-Machine Anthropic key. | `ANTHROPIC_API_KEY` in `wrangler.toml` secrets; `src/lib/claude/*`, `src/lib/llm/models.ts`. Default to the latest, most capable Claude models. |
| **Groq** | Speech-to-text for voice notes on Operator seats, through Hermes' Groq Whisper provider; one shared SMD key across the seats that use it. | `GROQ_API_KEY` in `operator/bin/lib/secret_custody.py`. |
| **Brave Search** | Web search for a seat that authors a `WebSearch` connector, as the native Hermes provider `native:brave-free` on Brave's free tier; the self-serve preset enables it. | ADR 0070; `docs/specs/operator/customer-yaml-schema.md`. |
| **ElevenLabs** | The voice agent for the public live assessment (ADR 0039). The browser talks to ElevenLabs (`@elevenlabs/client`, with a token from `/api/assessment/voice-token`), and ElevenLabs calls back to `/api/assessment/llm`, so Claude, not ElevenLabs, writes the replies. Secrets: `ELEVENLABS_API_KEY`, `ELEVENLABS_ASSESSMENT_AGENT_ID`, `ELEVENLABS_LLM_SECRET`. | `@elevenlabs/client` in `package.json`; `src/lib/claude/assessment-llm.ts`, `src/scripts/assessment-voice.ts`, `src/env.d.ts`. |

## Billing, documents, and email

| Service | Used for | Grounded in |
|---|---|---|
| **Stripe** | Invoicing and the recurring-billing money path. Webhook-verified inbound events. | `STRIPE_API_KEY` / `STRIPE_WEBHOOK_SECRET` in `wrangler.toml`; `src/lib/stripe/*`, `src/pages/api/webhooks/stripe.ts`. |
| **SignWell** | E-signature for SOWs and agreements, with webhook callbacks on signature events. | `SIGNWELL_API_KEY` / `SIGNWELL_WEBHOOK_SECRET`; `src/lib/signwell/*`, `src/pages/api/webhooks/signwell.ts`. |
| **Resend** | Transactional email (booking confirmations, follow-ups, invitations) with delivery-event webhooks, verified with `RESEND_WEBHOOK_SECRET`. | `RESEND_API_KEY`; `src/lib/email/resend.ts`, `src/pages/api/webhooks/resend.ts`. |
| **PDF rendering** | SOW PDFs are generated in-app with `@formepdf/core` / `@formepdf/react` (no external service). | `src/lib/pdf/sow-template.tsx`, `src/lib/pdf/render.ts`. |

## Google Workspace

| Service | Used for | Grounded in |
|---|---|---|
| **Google Calendar OAuth** | The booking system (the Calendly replacement): availability and event creation. | `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `BOOKING_ENCRYPTION_KEY` in `wrangler.toml`; `src/lib/booking/google-calendar.ts`. |
| **Google Workspace (managed mailbox)** | On the Operator plane, the operator manages a principal's mailbox via per-operation domain-wide-delegation, with the workspace broker as the authorization boundary. OAuth tokens live on the customer's Machine volume, not the console. | `operator/workspace_broker/`; ADR 0010. See `/admin/playbook/connectors-channels`. |

## Client systems the Operator connects to

| Service | Used for | Grounded in |
|---|---|---|
| **Smokeball** | Practice-management connector for law-firm seats (matters and their documents), through SMD's own connector with a Machine-hosted OAuth callback (ADR 0054). | `operator/connectors/smokeball/`. See `/admin/playbook/connectors-channels`. |
| **Microsoft Graph** | The Microsoft 365 email channel for a client on Exchange, registered as two apps in the client's own tenant: read-only for the agent, send-capable for the broker (Decision #54, ADR 0078). Separately, the console holds an SMD Graph app (`MICROSOFT_GRAPH_CLIENT_ID` and its secret) for the portal's connector-consent OAuth callback. | `operator/connectors/msgraph-mail/`; `src/env.d.ts`. See `/admin/playbook/connectors-channels`. |

## Marketing measurement

| Service | Used for | Grounded in |
|---|---|---|
| **Meta Conversions API** | Server-side `Lead` (intake) and `Schedule` (booking) events for the paid-acquisition round, deduplicated with the browser pixel; email is hashed before it leaves the Worker. Unset credentials make it report `unconfigured`, and it never breaks a booking. | ADR 0066; `src/lib/marketing/meta-capi.ts`; `META_CAPI_ACCESS_TOKEN` in `src/env.d.ts`. |

## Lead-generation data providers (retired)

The automated lead-gen pipelines that called external data sources (Google Places, Outscraper, SerpAPI, Proxycurl) were retired root-and-branch on 2026-07-01 (PRs #1610/#1616): the Workers, the `NEW_BUSINESS_WORKER_URL`-style env URLs, the `LEAD_INGEST_API_KEY` ingest path, and the admin Generators surface are all gone. Some provider API keys (`GOOGLE_PLACES_API_KEY`, `OUTSCRAPER_API_KEY`, `SERPAPI_API_KEY`) may still linger in the Infisical vault and on the Worker; they are now unused. Current lead generation is hand-personalized outreach from the Captain's mailbox (ADR 0059), not an ingestion pipeline.

## Operator-plane and enterprise tooling

| Service | Used for | Grounded in |
|---|---|---|
| **AgentMail** | The Operator's own email inbox (allow-list gated). A seat carries a read-and-draft key (`AGENTMAIL_API_KEY`, held by the agent and unable to send), a send key (`AGENTMAIL_SEND_API_KEY`, read only by the workspace broker), and a webhook secret (`WEBHOOK_SECRET_AGENTMAIL`). | `operator/contracts/env-consumption.yaml`; `operator/bin/lib/secret_custody.py`. See `/admin/playbook/connectors-channels`. |
| **Telegram** | A chat channel on seats that author it: the Hermes Telegram platform, with a per-seat bot token held as a Fly secret and an allow-list of user ids. A seat refuses to boot with a token and no allow-list. | `operator/contracts/env-consumption.yaml` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS`); `operator/templates/ensure-telegram-allowlist.py`. |
| **Infisical** | The secrets vault of record (`/ss` path). Values are pushed from it into Workers secrets and per-Machine Fly secrets by the rotation procedure. The console also writes to it directly, write-only, for a self-serve client's own model key (ADR 0067). | `wrangler.toml` (bulk-load comment); `src/lib/operator/infisical-secret-transport.ts`. See `/admin/playbook/secrets-access`. |
| **crane (venturecrane toolkit)** | The enterprise context and control layer used by agents building and operating this repo (session context, docs, verify, memory). Not part of the web request path, but more than session tooling: the required merge gate `runtime-ac-proof.yml` resolves every cited `vfy_` id against crane-context's verify ledger, and the ops machine clones crane-console and runs the crane MCP. | CLAUDE.md (Session Start); `.github/workflows/runtime-ac-proof.yml`; `operator/ops-machine/bin/ops-bootstrap`. |
| **GitHub** | Source of record (`venturecrane/ss-console`) and CI. All work ships through PRs; never push to main. | CLAUDE.md (Enterprise Rules); `.github/`. See `/admin/playbook/deployment-release`. |
| **Sentry** | Error monitoring. Worker-side through `@sentry/cloudflare` (a no-op when `SENTRY_DSN` is unset), and on the Operator Machines through the `smd-operator` project, whose alert rules call back to `/api/webhooks/sentry` (ADR 0023). | `@sentry/cloudflare` in `package.json`; `src/lib/observability/sentry.ts`; `SENTRY_DSN` in `wrangler.toml`; `SENTRY_WEBHOOK_SECRET` in `src/env.d.ts`. |

## Webhook inbound surface

Several of these services call back into the console. The handlers live at `src/pages/api/webhooks/`: `stripe.ts`, `signwell.ts`, `resend.ts`, `sentry.ts`, and `healthchecks.ts`. Each verifies its sender (signature or shared secret) before acting. The Stripe, SignWell and Resend handler bodies are in `src/lib/webhooks/`; the Sentry and healthchecks.io handlers are not.

## Not a service: the design tokens package

`@venturecrane/tokens` is in `package.json` but is not a running service. It is an npm package published to GitHub Packages (`.npmrc` points the `@venturecrane` scope at `npm.pkg.github.com`), installed at build time, and its `ss.css` is imported by `src/styles/global.css`. Nothing calls it at runtime, so there is no hosted token service to document; the only dependency is that installs need a GitHub Packages token (`NODE_AUTH_TOKEN`).
