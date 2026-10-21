---
title: The Client Portal
section: product
order: 2
owner: captain
review_every: 90
last_reviewed: "2026-10-21"
summary: The portal.smd.services surface where a client sees what they own - engagement lifecycle, product consoles, and one billing surface - plus the auth model and the integrations behind them
sources:
  - label: src/pages/portal/ (portal surfaces)
    href: https://github.com/venturecrane/ss-console/tree/main/src/pages/portal
  - label: src/lib/portal/offerings.ts and nav.ts (what the client sees)
    href: src/lib/portal/offerings.ts
  - label: src/lib/portal/nav.ts
    href: src/lib/portal/nav.ts
  - label: src/lib/portal/billing.ts (the start-subscription door)
    href: src/lib/portal/billing.ts
  - label: src/lib/webhooks/operator-checkout-handler.ts
    href: src/lib/webhooks/operator-checkout-handler.ts
  - label: src/lib/webhooks/stripe-subscription-handler.ts (payments)
    href: src/lib/webhooks/stripe-subscription-handler.ts
  - label: src/lib/webhooks/stripe-subscription-lifecycle.ts (cancellations)
    href: src/lib/webhooks/stripe-subscription-lifecycle.ts
  - label: src/lib/pricing/card-fee.ts (card fee rule)
    href: src/lib/pricing/card-fee.ts
  - label: src/middleware.ts (subdomain rewrite + portal auth)
    href: https://github.com/venturecrane/ss-console/blob/main/src/middleware.ts
  - label: src/lib/portal/session.ts (getPortalClient)
    href: https://github.com/venturecrane/ss-console/blob/main/src/lib/portal/session.ts
  - label: migration 0038 - portal Clerk subscriptions substrate
    href: https://github.com/venturecrane/ss-console/blob/main/migrations/0038_portal_clerk_subscriptions_substrate.sql
---

The client portal at `portal.smd.services` is where a paying client sees what they have with SMD: their consulting engagement and proposals, their Operator or Hosted Agent, and their invoices and subscription, each appearing only once it exists. A client starts their own Operator subscription here and pays invoices here; nothing in the portal is invented, so a section with no authored data is simply absent.

## What the portal is

The portal is served at `portal.smd.services` from `src/pages/portal/`. It is one Astro app and one Worker; the `portal.` subdomain is a front door, not a separate deployment (the mechanics are on [The Website](/admin/playbook/the-website)). Pages are marked `noindex, nofollow`, and changes are HTML forms checked on the server.

A client can arrive through a consulting engagement, as an Operator subscriber, or as a Hosted Agent subscriber, in any combination, and the portal shows exactly what they own (ADR 0068). This page covers the shared shell, the engagement and billing pages, and sign-in. The Operator console is its own page, [The Operator Console](/admin/playbook/operator-console). How a business becomes a portal client is [Customer Lifecycle](/admin/playbook/customer-lifecycle).

## The tabs

The tabs are worked out in one place: `resolvePortalOfferings` (`src/lib/portal/offerings.ts`) answers what the client owns, `buildPortalNav` (`src/lib/portal/nav.ts`) turns that into tabs, and every page renders through `src/layouts/PortalShell.astro`, so the tabs cannot differ page to page. In fixed order:

| Tab | Shown when |
|---|---|
| Home | Always, except for a client whose only product is an Operator not yet live; they land on the Operator itself |
| Engagement | Any engagement or proposal exists |
| Operator | The client has any Operator (one tab, however many) |
| Agent | The client has a Hosted Agent subscription |
| Billing | A billing relationship exists: an invoice sent, paid or overdue, or a subscription past set-up |

Every page is limited to the signed-in client's own records and shows authored data only.

### Home (`index.astro`)

One card per thing the client owns (`src/lib/portal/home-cards.ts`), with its status and, when something needs the client, one action: sign the proposal, pay the invoice, start the subscription, finish agent setup, or review Operator drafts. Below the cards, a timeline of real events (a proposal sent or signed, an invoice sent or paid, a milestone done), never made-up copy. A client with nothing yet sees a holding page.

### Engagement (`engagement/`)

The one consulting destination. An open proposal shows above the active engagement; both can be true at once. The active engagement shows its scope summary, start, estimated end, and the milestone list with status and evidence, never a made-up percent complete. Finished engagements are listed with read-only detail pages (`engagement/[id].astro`).

A proposal is reviewed and signed at `engagement/proposals/[id].astro`: deliverables and schedule only from what was authored on the quote, the total price and payment split, and the SignWell signing block. SignWell reports back to `src/pages/api/webhooks/signwell.ts`; the signed SOW downloads from `/api/portal/quotes/[id]/sow`. The client never sees an hourly rate.

Documents are at `engagement/documents/index.astro`: files stored in R2 across all of the client's engagements plus the signed SOW, served only through `/api/portal/documents/[...key]`, which checks the file belongs to the client.

### Billing (`billing/`)

The one money page, laid out as a ledger: subscriptions above invoices, each row stamped with its state (NOT STARTED, ACTIVE, CANCELS; DUE, PAID) and linking to where its action happens. The amount due appears once, at the top.

- **The subscription page** (`billing/subscriptions/[instance].astro`) states what the client is starting: the authored monthly fee, billed monthly in advance, cancellable at the end of the paid month. For a principal it carries one button, **Start monthly subscription**. Once started, the same page holds **Manage Billing**, which opens Stripe's billing portal (`POST /api/portal/billing/manage`, principal only; the Stripe customer comes from the subscription record, never from what the user sends).
- **An invoice page** (`billing/invoices/[id].astro`) shows the dates, the authored line items with a total, and one **Pay invoice** link when Stripe has a payment page for it, or "Payment link pending" otherwise. The invoice's title is its type (`INVOICE_TYPES`, `src/lib/db/invoices.ts`): the consulting types, the Operator's monthly `retainer`, and its one-time `implementation` fee.

The two pages share three building blocks (`PortalDetailTable`, `PortalDetailRow`, `PortalActionLink`). Old `/portal/quotes`, `/portal/invoices` and `/portal/documents` links redirect permanently to their new homes (`src/lib/routing/legacy-redirects.ts`).

**Cancelling.** A client cancels through Manage Billing. Stripe is set to cancel at the end of the paid period with no proration, so they keep the month they paid for and are not charged again. Stripe reports this as a healthy subscription with an end date, so the webhook copies the date to the subscription record (`setSubscriptionCancelSchedule`, `src/lib/db/subscriptions.ts`) and both the portal and the admin client hub show it ("Cancels Sep 8"). Without that, a cancellation would be invisible until the subscription ended.

## How an Operator client starts paying

Until a billing relationship exists, an Operator client lands straight on their Operator page with no Home or Billing tab: the review-and-set-up window. Two acts end it, and neither sends the client anything they did not ask for.

**The client starts the subscription.** Once the Captain has authored a monthly price on the client hub, the start door opens everywhere the client looks, decided by one rule (`canStartOperatorSubscription`, `src/lib/portal/billing.ts`): Home's Operator card reads "Ready to start" with the price, the Operator page links to it, and Billing shows the row as NOT STARTED. All three lead to the subscription page. A principal clicks **Start monthly subscription** and pays the first month on Stripe Checkout, using the payment method authored for that client next to the price (`services.payment_method`, migration 0113):

- **Bank account (ACH)** by default, with no fee.
- **Card**, when the client asked for one. The checkout and every monthly invoice then carry a second line, "Card processing fee (3%)", and the subscription page shows the fee and the total before the client clicks (`src/lib/pricing/card-fee.ts`, which cites the Operator Service Agreement section 3.8). One checkout cannot offer both and add the fee only to cards, which is why the method is set per client rather than chosen at checkout.

Stripe charges the same account or card each month after that.

When the checkout completes, `src/lib/webhooks/operator-checkout-handler.ts` ties the Stripe subscription to the client's record and makes it active if the payment has gone through. A bank payment settles days later, so the record stays in set-up until the money lands; then any of three Stripe events makes it active, whichever arrives first (the checkout's settled event, or the first invoice being paid, handled in `src/lib/webhooks/stripe-subscription-handler.ts`). Nothing else makes an Operator subscription active. If the bank payment bounces, the Stripe subscription is cancelled, the record returns to set-up, team@ is told, and the start door opens again. SMD never starts a retainer on a client's behalf.

**The Captain issues an invoice** from the client hub. It can be **presented** (payable in the portal, nobody emailed) or **sent** (Stripe's invoice email plus our notification). Both appear in Billing the same way. Invoices are paid by bank transfer with no fee; when a client asks to pay by card, the issue form adds the card fee line and the invoice then takes card instead. Monthly retainer invoices are bank transfer only. Who may send or present an invoice is on [Who Does What](/admin/playbook/who-does-what) and [Money](/admin/playbook/money).

## What reaches team@smd.services

Money events are never left to be found in the Stripe dashboard. team@ is emailed when a retainer payment is received, when a payment fails (`src/lib/webhooks/stripe-subscription-handler.ts`), when a client schedules a cancellation (naming the last day of service), and when a subscription ends (`src/lib/webhooks/stripe-subscription-lifecycle.ts`), and when a first bank payment bounces (`src/lib/webhooks/operator-checkout-handler.ts`). The cancellation emails fire once per change, because Stripe resends the whole subscription on every update. If Stripe gives no end date, the email says so rather than guessing. The client also gets a "Payment received" thank-you email for each retainer payment.

No alert takes an action. Pausing, ending or offboarding a client stays a human decision ([Customer Lifecycle](/admin/playbook/customer-lifecycle)).

## Product consoles

The Operator console (`products/operator/`) and the Hosted Agent pages (`products/hosted-agent/`) keep their own paths, because email links and registered sign-in callbacks point at them. The Hosted Agent pages show where the client is: no subscription, set-up (complete the questionnaire, or answers received), paused, or live, with the channel details SMD authored; and a write-only page for the client's own AI key (ADR 0067). Operator activity shown to a client uses authored wording only: `src/lib/portal/operator/activity-language.ts` maps each kind of recorded action to a sentence, unmapped actions show nothing, and tests keep the raw labels off client pages.

## Sign-in

Portal sign-in is enforced in `src/middleware.ts`. Two paths are accepted, Clerk first:

1. **Clerk (primary).** If a Clerk session is present the request goes through; if not, a page redirects to sign-in and an API call gets 401. `getPortalClient()` (`src/lib/portal/session.ts`) then maps the Clerk user to the local user and its business, and every query is limited to that business.
2. **Old emailed links (fallback).** Before Clerk, the portal used emailed sign-in links. `resolveLegacyPortalSession` still accepts those sessions, only for client users, so invitations sent before the switch keep working. Treat it as temporary.

### Clients and staff are kept apart

The `users.role` column (`admin` or `client`) is the line:

- The **admin** console requires `admin`. A signed-in client who opens an admin page is sent to `/portal`; an admin API call without the role gets 401 if not signed in and 403 if signed in without it (`enforceAdminAuth`).
- The **portal** accepts any signed-in user at the first check, then each page narrows. Operator pages narrow most: a linked business, an active Operator subscription, and at least one role (`principal`, `staff` or `compliance`; migration 0038). Anything less shows the right empty state.

Session cookies are per host, so a portal cookie works only on `portal.smd.services` and an admin cookie only on `admin.smd.services` ([The Website](/admin/playbook/the-website)).

## The services behind the portal

Described in full on [Integrations & Tooling](/admin/playbook/integrations-tooling):

- **SignWell** signs proposals. The admin creates the signing request from the quote; the portal shows it; SignWell's webhook records the signature and the signed PDF.
- **Stripe** takes payment. Invoices are created in Stripe when the admin sends or presents them; subscriptions start from the client's own checkout. Stripe's webhooks mark invoices paid and keep subscriptions in step.
- **R2** stores documents and the signed SOW, served only through the checked download endpoint.

## What the portal never does

It shows authored data only. Timelines, next steps, consultant messages, deliverables, schedules and invoice lines come from fields a person filled in, or they show nothing. This is the no-fabricated-client-content policy applied where a real client looks; the policy and its tests are on [Security & Trust](/admin/playbook/security-trust).
