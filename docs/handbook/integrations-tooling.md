---
title: Integrations & Tooling
section: system
order: 7
summary: The external-service inventory - every third party the platform depends on and what each one is for, derived from package.json dependencies, wrangler.toml bindings, and CLAUDE.md
sources:
  - label: package.json (dependencies)
    href: https://github.com/venturecrane/ss-console/blob/main/package.json
  - label: wrangler.toml (bindings + secret list)
    href: https://github.com/venturecrane/ss-console/blob/main/wrangler.toml
  - label: CLAUDE.md - Tech Stack, Deployment, Three-Subdomain Architecture
    href: https://github.com/venturecrane/ss-console/blob/main/CLAUDE.md
---

## How to read this page

This is the inventory of external services the platform touches and what each is for. It does not list any credential values, and it is not the rotation runbook. For *where* a given secret lives and *how* to rotate it, go to `/admin/playbook/secrets-access`. The grounding here is `package.json` dependencies, `wrangler.toml` bindings and its documented secret list, and CLAUDE.md.

## Infrastructure

| Service | Used for | Grounded in |
|---|---|---|
| **Cloudflare Workers** | The runtime. One Worker, `ss-web`, serves all three subdomains via Astro SSR (`@astrojs/cloudflare`). | `wrangler.toml` (`name = "ss-web"`), `astro.config.mjs`. |
| **Cloudflare D1** | The structured database `ss-console-db` - all business data. Bound as `DB`. | `wrangler.toml`, `[[d1_databases]]`. |
| **Cloudflare R2** | Object storage in three buckets: `STORAGE` (documents, transcripts, SOWs), `CONSULTANT_PHOTOS` (public consultant images), `CUSTOMER_CONFIG` (`smd-customer-config`, the authoritative live Operator config and voice vaults). | `wrangler.toml`, `[[r2_buckets]]`. |
| **Cloudflare KV** | Session storage (`SESSIONS`) and booking rate-limit plus the public-assessment turn/cost ceiling (`BOOKING_CACHE`). | `wrangler.toml`, `[[kv_namespaces]]`. |
| **Fly.io** | The Operator plane. One Machine per customer (`hermes-{slug}`) running Hermes plus the overlay, each with its own D1, R2, and OAuth volume. | CLAUDE.md (Operator Architecture); ADR 0007. See `/admin/playbook/operator-platform`. |

## Identity and auth

| Service | Used for | Grounded in |
|---|---|---|
| **Clerk** | Identity for both the admin console and the client portal (users, orgs, memberships, invitations, sessions). The application is "SMD Services"; production binds to the `smd.services` apex with auth subdomains. Coexists with the legacy magic-link path during transition. | `@clerk/astro` in `package.json`; `astro.config.mjs`; `src/middleware.ts`. Needs `PUBLIC_CLERK_PUBLISHABLE_KEY` (build) and `CLERK_SECRET_KEY` (runtime). |

## LLM and voice

| Service | Used for | Grounded in |
|---|---|---|
| **Anthropic (Claude)** | The LLM behind the live assessment and assessment extraction (`src/lib/claude/*`). The Operator plane also runs on Claude via the per-Machine Anthropic key. | `ANTHROPIC_API_KEY` in `wrangler.toml` secrets; `src/lib/claude/*`, `src/lib/llm/models.ts`. Default to the latest, most capable Claude models. |
| **ElevenLabs** | Voice. Used by the public assessment voice flow (`@elevenlabs/client`) and in the Operator explainer video pipeline. | `@elevenlabs/client` in `package.json`; `src/lib/claude/assessment-llm.ts`, `src/scripts/assessment-voice.ts`. |

## Billing, documents, and email

| Service | Used for | Grounded in |
|---|---|---|
| **Stripe** | Invoicing and the recurring-billing money path. Webhook-verified inbound events. | `STRIPE_API_KEY` / `STRIPE_WEBHOOK_SECRET` in `wrangler.toml`; `src/lib/stripe/*`, `src/pages/api/webhooks/stripe.ts`. |
| **SignWell** | E-signature for SOWs and agreements, with webhook callbacks on signature events. | `SIGNWELL_API_KEY` / `SIGNWELL_WEBHOOK_SECRET`; `src/lib/signwell/*`, `src/pages/api/webhooks/signwell.ts`. |
| **Resend** | Transactional email (booking confirmations, follow-ups, invitations) with delivery-event webhooks. | `RESEND_API_KEY`; `src/lib/email/resend.ts`, `src/pages/api/webhooks/resend.ts`. |
| **PDF rendering** | SOW PDFs are generated in-app with `@formepdf/core` / `@formepdf/react` (no external service). | `src/lib/pdf/sow-template.tsx`, `src/lib/pdf/render.ts`. |

## Google Workspace

| Service | Used for | Grounded in |
|---|---|---|
| **Google Calendar OAuth** | The booking system (the Calendly replacement): availability and event creation. | `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `BOOKING_ENCRYPTION_KEY` in `wrangler.toml`; `src/lib/booking/google-calendar.ts`. |
| **Google Workspace (managed mailbox)** | On the Operator plane, the operator manages a principal's mailbox via per-operation domain-wide-delegation, with the workspace broker as the authorization boundary. OAuth tokens live on the customer's Machine volume, not the console. | `operator/workspace_broker/`; ADR 0010. See `/admin/playbook/connectors-channels`. |

## Lead-generation data providers (retired)

The automated lead-gen pipelines that called external data sources (Google Places, Outscraper, SerpAPI, Proxycurl) were retired root-and-branch on 2026-07-01 (PRs #1610/#1616): the Workers, the `NEW_BUSINESS_WORKER_URL`-style env URLs, the `LEAD_INGEST_API_KEY` ingest path, and the admin Generators surface are all gone. Some provider API keys (`GOOGLE_PLACES_API_KEY`, `OUTSCRAPER_API_KEY`, `SERPAPI_API_KEY`) may still linger in the Infisical vault and on the Worker; they are now unused. Current lead generation is hand-personalized outreach from the Captain's mailbox (ADR 0059), not an ingestion pipeline.

## Operator-plane and enterprise tooling

| Service | Used for | Grounded in |
|---|---|---|
| **AgentMail** | The Operator's own inbound email channel account (allow-list gated). SMD holds one AgentMail account. | `AGENTMAIL_API_KEY` / `WEBHOOK_SECRET_AGENTMAIL`; `operator/` config. |
| **Infisical** | The secrets vault of record. Secrets are exported from Infisical (`/ss` path) and bulk-loaded into Workers secrets and per-Machine Fly secrets. | CLAUDE.md (Deployment, Secrets); `wrangler.toml` secret-load note. See `/admin/playbook/secrets-access`. |
| **crane MCP** | The enterprise context and control layer used by agents building and operating this repo (session context, docs, verify, memory). Not part of the web request path. | CLAUDE.md (Session Start). |
| **GitHub** | Source of record (`venturecrane/ss-console`) and CI. All work ships through PRs; never push to main. | CLAUDE.md (Enterprise Rules); `.github/`. See `/admin/playbook/deployment-release`. |
| **Sentry** | Error monitoring, wired Worker-side. The integration is a no-op when `SENTRY_DSN` is unset. | `@sentry/cloudflare` in `package.json`; `src/lib/observability/sentry.ts`; `SENTRY_DSN` in `wrangler.toml`. |

## Webhook inbound surface

Several of these services call back into the console. The handlers live at `src/pages/api/webhooks/`: `stripe.ts`, `signwell.ts`, `resend.ts`, `sentry.ts`, and `healthchecks.ts`. Each verifies its sender (signature or shared secret) before acting; the handler bodies are in `src/lib/webhooks/`.

> TODO(why): The design tokens dependency `@venturecrane/tokens` is an enterprise package, not an external runtime service, so it is listed in `package.json` but intentionally omitted from this service inventory. Confirm there is no separate hosted token service to document.
