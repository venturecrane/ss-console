---
title: The Client Portal
section: system
order: 5
summary: The portal.smd.services surface where a client sees what they own - engagement lifecycle, product consoles, and one billing surface - plus the auth model and the integrations behind them
sources:
  - label: src/pages/portal/ (portal surfaces)
    href: https://github.com/venturecrane/ss-console/tree/main/src/pages/portal
  - label: src/middleware.ts (subdomain rewrite + portal auth)
    href: https://github.com/venturecrane/ss-console/blob/main/src/middleware.ts
  - label: src/lib/portal/session.ts (getPortalClient)
    href: https://github.com/venturecrane/ss-console/blob/main/src/lib/portal/session.ts
  - label: migration 0038 - portal Clerk subscriptions substrate
    href: https://github.com/venturecrane/ss-console/blob/main/migrations/0038_portal_clerk_subscriptions_substrate.sql
---

## What the portal is

The client portal is the surface a paying client sees. It is served at `portal.smd.services` and lives in source under `src/pages/portal/`. One Astro app, one Worker; the `portal.` subdomain is a front door, not a separate deployment (the rewrite mechanics live in `/admin/playbook/the-website`). Pages are marked `noindex, nofollow` - this is private client space, never a marketing surface, and it ships effectively no client JavaScript: mutations are HTML forms validated server-side.

The portal serves clients who arrive three ways: through a consulting engagement, as an Operator subscriber, or as a Hosted Agent subscriber. A client can hold any combination on one entity, and the portal composes to exactly what they own (ADR 0068: offerings as destinations). This page documents the shared shell, the engagement and billing destinations, and the auth model. The client-facing Operator product console - a large surface in its own right - is documented separately at `/admin/playbook/operator-console`. How a business travels from lead to portal account is `/admin/playbook/customer-lifecycle`.

## The destinations

Navigation is computed from data in exactly one place: `resolvePortalOfferings` (src/lib/portal/offerings.ts) answers "what does this entity own," `buildPortalNav` (src/lib/portal/nav.ts) turns that into the destination list, and every page renders through the single chrome owner `src/layouts/PortalShell.astro`. Pages carry no nav-shaping props, so the tab set cannot drift page to page. The destinations, in fixed order: Home (always), Engagement (when any engagement or proposal exists), Operator and Agent (per subscription), and Billing (once any invoice or subscription exists). An entity that owns nothing yet gets a warm holding page on Home.

Every surface scopes to the signed-in client's own entity and renders authored data only. The surfaces, from `src/pages/portal/`:

### Home (`index.astro`)

The status dashboard: one card per owned offering (src/lib/portal/home-cards.ts) showing its live status and, when something genuinely needs the client, a needs-you door (sign the proposal, pay the invoice, finish agent setup, review operator drafts). Below the cards, the recent-activity timeline assembled from real events - a proposal sent or signed, an invoice sent or paid, a milestone completed - in concrete past tense, never synthesized copy.

### Engagement (`engagement/index.astro` and children)

The one lifecycle destination. An open proposal renders as a spotlight above the active workspace - both can be true at once, because a follow-on proposal to an active client is a real state. The active engagement shows the overview (scope summary, start, estimated end) and the milestone list with status and evidence, never a synthesized percent-complete. Completed engagements live in a past-work list linking to read-only detail pages (`engagement/[id].astro`).

Proposal review and signing lives at `engagement/proposals/[id].astro`: deliverables and schedule rendered only from authored quote fields, terms (total price and payment split), and the SignWell review-and-sign block. SignWell calls back to `src/pages/api/webhooks/signwell.ts`; the signed SOW downloads via `/api/portal/quotes/[id]/sow`. The client never sees an hourly rate.

The document library lives at `engagement/documents/index.astro`: files stored in R2 across ALL of the client's engagements plus the signed SOW, served through the org-scoped, traversal-checked `/api/portal/documents/[...key]` endpoint.

### Billing (`billing/index.astro`, `billing/invoices/[id].astro`)

The one money surface, composed as a ledger (2026-09-09): two lists of the same ticket row, subscriptions above invoices, each row stamped with its state (NOT STARTED, ACTIVE, CANCELS; DUE, PAID) and linking to the page where its act happens. The amount due sits in the page meta, once. An Operator row opens the subscription page (`/portal/billing/subscriptions/<instance>`, src/pages/portal/billing/subscriptions/[instance].astro), which states what the client is starting (the authored monthly fee, monthly in advance, bank account or card chosen at checkout, cancellation at the end of the paid month) and carries the one primary action, **Start monthly subscription**, for a principal on a startable row; once started, the same page holds the Manage Billing door that opens a Stripe Billing Portal session via `POST /api/portal/billing/manage` (principal-gated per product; the Stripe customer id comes from the subscription row, never user input). The earlier composition, a subscription block with its own inline button, a Paid-to-date / Balance-due pair inherited from the consulting-era invoices page, then the invoice list, was retired the day the Captain read it as confusing: it put two primaries on one screen, restated the one invoice's amount beside it, and labelled a startable Operator "Being set up". Cancelling lives behind the Manage Billing door: the Stripe portal configuration cancels at period end with no proration, so a client who cancels keeps the month they paid for and is never charged again. Stripe signals that as `cancel_at_period_end` on an otherwise healthy `active` subscription, which is why the webhook mirrors the end date to `subscriptions.settings_json.cancel_at` (`setSubscriptionCancelSchedule`, src/lib/db/subscriptions.ts) and both surfaces read it back through `parseCancelAt` - the client sees "Cancels Sep 8", the client hub sees the same date. Without that mirror a cancellation is invisible on both sides until the subscription actually ends weeks later. Invoices list below; the detail page shows authored line items and a Pay button when a Stripe hosted-invoice URL exists. The invoice's title is its type (`INVOICE_TYPES` in src/lib/db/invoices.ts): the consulting types, the Operator's monthly `retainer` (mirrored from Stripe cycle invoices), and the Operator's one-time `implementation` stand-up fee. Old `/portal/quotes`, `/portal/invoices`, and `/portal/documents` URLs 301 permanently to their new homes (src/lib/routing/legacy-redirects.ts, `portal-ia-redirects`).

**When Billing appears.** The destination is revealed by a billing relationship, not by a flag: an invoice in `sent`/`paid`/`overdue`, or a subscription whose status has moved past `provisioning` (`hasBillingRelationship`, src/lib/portal/offerings.ts). Until then an Operator client lands straight on their operator page with no Home and no Billing tab, which is the review-and-configure window during stand-up. Two acts end that window, and neither sends the client anything they did not ask for. The first is the client's own: once a monthly price is authored on the client hub, the start door opens on every surface the client lands on, decided by one predicate (`canStartOperatorSubscription`, src/lib/portal/billing.ts): Home's Operator card reads "Ready to start" with the price and a **Start monthly subscription** action, the Operator page's hero carries the same link, and Billing's Operator row is stamped NOT STARTED; all three land on the subscription page, where the one primary button lives. It was a button on Billing alone until 2026-09-09, when the firm's partner said it was time to start paying and nobody signed in as a client could find the door. A principal clicks it, pays the first month on Stripe Checkout by bank account (ACH, verified instantly or by micro-deposits) or card, and Stripe charges that same method every month from then on. The `checkout.session.completed` webhook (src/lib/webhooks/operator-checkout-handler.ts) binds the Stripe subscription and customer to the row and promotes it to `active` when the session's `payment_status` is not `unpaid`, which a card payment never is. An ACH payment settles days later, so the row stays `provisioning` (bound, not live) until the money lands, and three webhook paths can then promote it, whichever Stripe delivers first: that same handler on `checkout.session.async_payment_succeeded`; the retainer `invoice.paid` handler (src/lib/webhooks/stripe-subscription-handler.ts) when the first invoice is paid on a bound row; and the same `invoice.paid` handler when the invoice arrives before the checkout event at all, in which case it binds the row from the subscription metadata the checkout stamped and promotes it. Nothing else promotes an operator row. If the ACH payment bounces (`checkout.session.async_payment_failed`), the subscription is cancelled at Stripe, unbound from the row, and team@ is told; the row is back to `provisioning` with no subscription, so the door opens again on all three surfaces. No SMD-side act creates a retainer on a client's behalf (the admin-started, Stripe-emailed retainer was removed 2026-08-29). The second is the Captain's: issuing an invoice from the client hub. An invoice can be **presented** (finalized in Stripe with automatic collection off, so it is payable in the portal and nobody is emailed) or **sent** (Stripe's hosted-invoice email plus our notification). Both land the invoice in Billing identically; present is for the client who already knows the amount and will pay when directed. Invoices are payable by ACH with no fee; when a client asks to pay by card, the issue form's card option adds a "Card processing fee (3%)" line (`CARD_FEE_LINE_DESCRIPTION`, src/lib/db/invoices.ts) and the Stripe invoice then offers card instead of ACH, so the fee is on the invoice before payment as the Operator Service Agreement §3.8 promises. Retainer cycle invoices are ACH only.

**What reaches team@smd.services.** Money and billing intent are never left to be discovered in the Stripe dashboard. `src/lib/webhooks/stripe-subscription-handler.ts` alerts the operational address on four events: a retainer payment received, a payment failed, a cancellation scheduled (naming the date service runs through), and a subscription actually ended. The cancellation alerts are edge-triggered off the stored `cancel_at`, because Stripe re-sends the whole subscription object on every update and an unguarded alert would re-fire on unrelated changes. When Stripe reports a scheduled cancellation but names no end date, the alert says exactly that rather than putting a guessed date in front of a client. No alert takes an action: pausing, decommissioning, and offboarding stay human decisions under the offboarding doctrine (#1684).

### Product consoles

The Operator console (`products/operator/`) and Hosted Agent console (`products/hosted-agent/`) keep their own subtrees (paths frozen: email deep links and externally registered OAuth callbacks point at them). Client-facing operator activity renders curated language only: the allowlist in src/lib/portal/operator/activity-language.ts maps raw audit actions to authored copy, unmapped actions render nothing, and a guard test bans the raw formatter from client surfaces.

## The auth model

Portal auth is enforced in `src/middleware.ts`. Two paths are accepted, with Clerk primary:

1. **Clerk (primary).** `clerkMiddleware` runs first in the composed pipeline and populates `locals.auth()`. `enforcePortalAuth` lets a request through if a Clerk session is present; if not, it redirects to `/auth/sign-in` (or returns 401 on `/api/portal/*`). The bridge from a Clerk identity to the local user and business runs lazily per route via `getPortalClient()` (`src/lib/portal/session.ts`), which maps the Clerk user to the local `users` row and its `entity_id` and `org_id`. Every query is scoped to that entity, so a client sees only their own data.
2. **Legacy magic-link (fallback).** Before Clerk, the portal used emailed magic links that created a KV/D1 session. `resolveLegacyPortalSession` still accepts those cookies, but only for a session whose `role === 'client'`, and renews them on a sliding window. This path exists solely to keep in-flight invitation emails working through the Clerk transition; treat it as temporary, not a second permanent auth system.

### The role=client gate

The portal is for clients, the admin console is for staff, and the boundary is the `users.role` column (`admin` or `client`). The gate is asymmetric:

- The **admin** console requires `role === 'admin'`; a signed-in client who hits an admin path is redirected to `/portal` (`enforceAdminAuth`).
- The **portal** accepts any Clerk-authenticated user at the middleware layer, then narrows per route. The Operator surfaces narrow hardest: a request needs a Clerk session, a local user linked to an entity, an active `subscriptions` row on `(entity, 'operator')`, and at least one granted role on `(user, entity, 'operator')` in `product_roles` - the vocabulary is `principal | staff | compliance` (migration 0038). Anything less degrades to the appropriate empty state, never to fabricated content.

### Cookie boundaries

Session cookies are per-host (no `Domain` attribute), so a portal cookie lives only on `portal.smd.services` and an admin cookie only on `admin.smd.services`. The subdomain and cookie-isolation mechanics are owned by `/admin/playbook/the-website`.

## The integrations behind the portal

The consulting portal is a read-and-act surface over three external systems, all documented in `/admin/playbook/integrations-tooling`:

- **SignWell** signs proposals. The admin creates the signature request from the quote; the portal presents it; SignWell's webhook records the signed state and the executed PDF.
- **Stripe** collects payment. The admin attaches a hosted-checkout URL to the invoice; the portal links to it; Stripe's webhook marks the invoice paid.
- **R2** stores documents and the signed SOW, served only through the org-scoped, traversal-checked download endpoint.

## What the portal never does

The portal renders authored data only. Timelines, next-touchpoint copy, consultant outreach sentences, deliverables, schedules, and invoice line items come from columns a human authored, or they render nothing. This is the no-fabricated-client-facing-content policy applied at the surface that faces a real customer; the policy, its Pattern A and Pattern B failure modes, and the tests that enforce it are in `/admin/playbook/security-trust`.
