---
title: Money
section: running
order: 4
owner: captain
review_every: 30
last_reviewed: "2026-10-21"
summary: How clients start paying, how invoices go out, what you may approve, what happens when a payment fails, and where the costs are watched.
sources:
  - label: Operator subscriptions (client-started checkout)
    href: src/lib/stripe/subscriptions.ts
  - label: Card processing fee
    href: src/lib/pricing/card-fee.ts
  - label: Invoice actions (send, present, void)
    href: src/pages/api/admin/invoices/[id].ts
  - label: Milestone invoicing
    href: src/lib/stripe/milestone-invoicing.ts
  - label: Retainer payment webhook
    href: src/lib/webhooks/stripe-subscription-handler.ts
  - label: ADR 0065 offboarding and dunning
    href: docs/adr/0065-operator-offboarding-and-dunning.md
  - label: ADR 0062 cost plane
    href: docs/adr/0062-operator-cost-plane.md
  - label: Cost dashboard
    href: src/pages/admin/operator/costs/index.astro
---

Clients pay SMD in three ways: a monthly Operator subscription the client starts themselves, invoices SMD issues from the admin console, and self-serve Operator checkout. The amounts are set in each client's signed agreement and are never published; this page covers how money moves and who may move it.

## What you may approve

By the Captain's decision of 2026-10-21:

- **You may** send or present an invoice at a price that is already set (in a signed agreement, a signed SOW, or the seat's authored monthly price), and send routine client emails about billing.
- **The Captain decides** any new price, discount, credit or refund, any change to payment terms, anything that spends SMD money, and whether to pause, end or take on a client.

When an amount on an invoice does not match what was agreed, stop and take it to the Captain; do not fix it by editing the line.

## How an Operator client starts paying

1. The client's principal clicks **Start subscription** in their portal. That opens a Stripe Checkout page where the first month is paid; Stripe charges the same method every month after.
2. It only works when the seat is being set up, has no subscription yet, and has a monthly price authored. Nobody at SMD can start a subscription for a client; the old admin-started version was removed on 2026-08-29.
3. Each seat has one payment method, set by the Captain: **bank debit (ACH), the default**, or **card, which adds a 3% processing fee** as its own line on checkout and every monthly invoice. Bank debit has no fee.
4. The one-time stand-up fee is a separate implementation invoice.

## Invoices you issue

Use **Issue an invoice** on the client's page (`/admin/clients/<id>`). It creates a draft; an invoice cannot go out without at least one authored line. From the invoice you can:

| Action | What happens |
|---|---|
| **Present** | The invoice becomes payable in the client's portal. Nobody is emailed. |
| **Send** | Stripe emails the invoice and the system sends its own notice. |
| Reschedule, void, mark paid | As named. |

Invoices are bank-debit only unless they carry the card-fee line, in which case they are card only. Default terms are 30 days.

**Two automatic sends to know about,** both on consulting engagements:

- **Signing the SOW** creates the deposit invoice in Stripe and emails it to the client, due in 3 days (`src/lib/sow/outbox-jobs.ts`).
- **Marking a payment milestone complete** creates and emails that milestone's invoice at once (`src/lib/stripe/milestone-invoicing.ts`). Marking a milestone complete is therefore sending an invoice.

Invoice types: consulting deposit, milestone, completion and assessment; the Operator's monthly retainer (created by Stripe and copied into the console); and the Operator's implementation (stand-up) invoice. Consulting is paid 50% at signing and 50% at completion; for engagements of 40 hours or more, Decision #14 says to consider 40/30/30 with the mid-point named in the SOW; the deposit invoice is created and sent when the SOW is signed.

Billing lives at **`/admin/billing`** (Quotes, Invoices, Recurring, with real monthly recurring revenue and any seat missing a price flagged).

## When a payment fails or a client leaves

A failed monthly charge emails team@ ("Retainer payment failed") and marks the invoice overdue. **Nothing else happens automatically:** the client keeps access, because the steps after that are human decisions (ADR 0065):

| Day past due | Step | Who |
|---|---|---|
| 0 | Personal outreach the same business day | The Captain |
| 14 | Pause the seat (billing paused, the Operator's work suspended, audit access kept) | The Captain |
| 30 | Treat it as cancellation and begin offboarding | The Captain |

A client may cancel with no notice period; it takes effect at the end of the month they have paid for. SMD's own termination gives the client 30 days. There is no refund flow in the console; a refund is a Captain decision.

## What it costs to run

- **Per-seat cost** (mostly AI model usage) is on **`/admin/operator/costs`**: each Operator's last 30 days of cost against its monthly price, banded healthy (under 30%), watch (30 to 40%), and kill (40% or more). The kill line comes from ADR 0062 (cost above 40% of the price for two months running); the dashboard shows it, nothing acts on it automatically, and acting on it is the Captain's call.
- **A daily cost cap per seat** stops the seat when its spend for the day reaches twice the cap; there is no warning or slow-down level before that (ADR 0062, amended 2026-09-02). Clearing a cost stop lets the seat spend again, so it is the Captain's call; see the [sticky-stop runbook](https://github.com/venturecrane/ss-console/blob/main/docs/runbooks/operator/sticky-stop-clear.md).
- **A daily cost-anomaly digest** goes to the Captain's inbox.
- **Vendors SMD pays** include Anthropic, Fly.io, Cloudflare, Stripe, Clerk, Resend, SignWell, AgentMail, Groq, ElevenLabs, Brave Search, Sentry, healthchecks.io and Infisical (the names come from the system's own configuration). What each costs each month, the bank, the books and the tax calendar are in the private companion (`admin/vendor-billing.md`, `admin/entity.md`), where the amounts are still blank for the Captain to fill ([Handoff Companion](/admin/playbook/handoff-companion)).
