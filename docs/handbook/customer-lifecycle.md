---
title: Customer Lifecycle
section: business
order: 7
owner: captain
review_every: 180
last_reviewed: "2026-10-21"
summary: How a prospect becomes a live client and, eventually, leaves - one track for an Operator client, one for a consulting client, a short one for the Hosted Agent - with who acts at each step and what the system does on its own.
sources:
  - label: Operator implementation playbook
    href: docs/runbooks/operator/implementation-playbook.md
  - label: Operator subscriptions (client-started checkout)
    href: src/lib/stripe/subscriptions.ts
  - label: Operator contract templates
    href: docs/templates/operator/README.md
  - label: ADR 0065 - Operator offboarding and dunning
    href: docs/adr/0065-operator-offboarding-and-dunning.md
  - label: Decommission tooling
    href: operator/bin/decommission-customer.sh
  - label: ADR 0067 - Hosted Agent self-serve SKU
    href: docs/adr/0067-hosted-agent-self-serve-sku.md
  - label: ADR 0090 - Hosted-agent roles
    href: docs/adr/0090-hosted-agent-roles.md
  - label: Entity stages and transitions
    href: src/lib/db/entities.ts
  - label: Send booking link
    href: src/pages/api/admin/entities/[id]/send-booking-link.ts
  - label: Draft a quote from a meeting
    href: src/pages/api/admin/entities/[id]/meetings/[meetingId]/draft-quote.ts
  - label: Quote sign - SOW to SignWell
    href: src/pages/api/admin/quotes/[id]/sign.ts
  - label: SOW finalize - stage to engaged
    href: src/lib/sow/service-finalize.ts
  - label: Jobs run at signing (deposit invoice, emails)
    href: src/lib/sow/outbox-jobs.ts
  - label: Engagement handoff and the support window
    href: src/lib/db/engagements.ts
  - label: Retire the automated lead-gen machine (ADR 0060)
    href: docs/adr/0060-retire-automated-lead-gen-machine.md
---

A prospect becomes a client by one of two main paths: an Operator client is won through letters and a proposal, signs an agreement drafted for them, and then starts paying in the portal once their Operator is stood up; a consulting client goes through an assessment, a quote and an e-signed SOW in the admin console. This page walks both, plus the short Hosted Agent path, and ends with how an Operator client leaves.

## Which track you are on

| | Operator | Consulting | Hosted Agent |
|---|---|---|---|
| Has it happened? | Yes: SMD's one paying client (seat `ashton-price`) | Built and documented; see [The Consulting Engagement](/admin/playbook/consulting-engagement) | SMD's own seats only so far (ADR 0090) |
| Where the paper lives | The private `venturecrane/engagements` repository | The admin console (quote, SOW PDF, SignWell) | Stripe checkout and the product terms |
| How payment starts | The client starts the subscription in the portal | A deposit invoice is emailed automatically at signing | Self-serve checkout |

Who may do what at each step is set in [Who Does What](/admin/playbook/who-does-what): pricing, contract terms, new commitments and taking on a client are the Captain's; routine client emails and invoices at agreed prices are yours.

## Track 1: an Operator client

This is the path SMD's one sale took. Everything specific to that client (letters, proposal, agreement, dossier) is in `venturecrane/engagements` under `operator/customers/<slug>/`, the same folder name as the seat ([Handoff Companion](/admin/playbook/handoff-companion)).

**1. Conversation and letters.** The sale is founder-run (ADR 0060): the Captain leads the conversations and the letters. Each letter is archived in the client's `correspondence/` folder. Whenever a letter promises something, the session that read it records the promise in the [obligation register](/admin/playbook/obligation-register) before it closes.

**2. Proposal.** A written proposal for that client, kept in the client's folder. The price in it comes from the private pricing record ([Pricing & Economics](/admin/playbook/pricing-economics)).

**3. Agreement.** A service agreement and a data processing addendum (DPA), plus a confidentiality addendum for a law firm, drafted for that client in the client's `agreements/` folder. The starting point is the template set in `docs/templates/operator/` (service contract, DPA, confidentiality addendum, design-partner addendum). Contract terms are the Captain's; you do not negotiate or change them.

**4. Stand-up.** The seat is built and proven by following the [Operator implementation playbook](https://github.com/venturecrane/ss-console/blob/main/docs/runbooks/operator/implementation-playbook.md): Prepare, Explore, Realize, Deploy, Run, each ending at a gate. It is written for an implementation specialist working with a build agent, not for an engineer. The Captain approves the first boot of a new seat ([Who Does What](/admin/playbook/who-does-what)).

**5. Billing starts.** In the agreement drafted for SMD's Operator client, no monthly fee accrues until SMD tells the client in writing that implementation testing is complete and the client then starts the service in the portal; that day is the billing start date. In the portal the client's principal clicks **Start subscription**, which opens Stripe Checkout and pays the first month; Stripe charges the same method monthly after that. Nobody at SMD can start a subscription for a client (`src/lib/stripe/subscriptions.ts`). The one-time stand-up fee goes out as a separate **implementation** invoice, which you may send at the agreed amount ([Money](/admin/playbook/money)). SMD's client did this on 2026-09-15 (`operator/customers/ashton-price/customer.yaml`, billing-cycle note).

**6. Running.** Day to day, the client changes their own Operator within the limits agreed; anything else comes to SMD as a change request ([Changing a Client's Operator](/admin/playbook/changing-a-clients-operator)). A request for a new kind of work follows the change-request process on [Pricing & Economics](/admin/playbook/pricing-economics), and every open item is a register row.

**7. Leaving.** See "Offboarding an Operator client" at the end of this page.

## Track 2: a consulting client

Every business is one row in the `entities` table, moving through eight stages (`src/lib/db/entities.ts`): `signal`, `prospect`, `meetings`, `proposing`, `engaged`, `delivered`, `ongoing`, with `lost` as the exit. Records before acceptance appear under **Leads** (`/admin/entities`); from `engaged` on they appear under **Clients** (`/admin/clients`). The row never moves; only the view changes. The allowed moves between stages are enforced in code, not left to convention.

### 1. Signal

A lead you or the Captain add by hand. The automated lead-generation machine that once filled this stage was retired on 2026-07-01, and the board is now a manual worklist (ADR 0060). Older rows may still carry a source filter (`review_mining`, `job_monitor` and others) from that machine.

- **Where:** `/admin/entities?stage=signal`.

A signal worth pursuing is promoted to `prospect`; one that is not is dismissed or marked `lost`.

### 2. Prospect and outreach

Outreach is written by a person; nothing drafts it automatically any more (ADR 0060). Once outreach text is saved on the record, the prospect row offers **Send outreach** (opens a mail draft with that text, addressed to the first contact with an email) and **Log reply** (`src/pages/admin/entities/index.astro`).

When the prospect agrees to talk, **Send booking link** creates the meeting, moves the record to `meetings` at once, and emails the prospect a signed booking link (valid 14 days by default) through SMD's own email service, so opens and replies are tracked. With no contact email it gives you a template to send yourself (`send-booking-link.ts`).

- **Where:** `/admin/entities?stage=prospect` and the record's page.

### 3. Assessment (stage `meetings`)

The assessment is a meeting on the record's page (`/admin/entities/<id>/meetings/<meetingId>`). **Complete Meeting** records the outcome notes and duration and can move the record to a stage you pick. It does not create a quote. How the call itself runs is on [The Consulting Engagement](/admin/playbook/consulting-engagement).

### 4. Quote and SOW (stage `proposing`)

**Draft quote** on the meeting page creates an empty draft quote and moves the record to `proposing`. The quote carries line items, hours, rate, price, deposit terms, schedule and deliverables. Its states are draft, sent, accepted, with declined, expired and superseded as endings; a sent quote expires five days after it is sent, so the Proposing tab lists the oldest-sent first (`src/lib/db/quotes.ts`).

The quote is the internal pricing object; the SOW is the client-facing PDF made from it. The price on a quote is the Captain's decision.

- **Where:** `/admin/entities?stage=proposing`, the quote page, and the Quotes tab on `/admin/billing`.

### 5. Signing

Sending for signature takes the SOW PDF and creates a signature request in **SignWell**, the e-signature service for consulting SOWs (`src/pages/api/admin/quotes/[id]/sign.ts`). When the client signs, SignWell tells the system, and three things then happen on their own (`src/lib/sow/service-finalize.ts`, `src/lib/sow/outbox-jobs.ts`):

1. The record moves to `engaged` (with a note: "Stage: proposing -> engaged. SOW signed via SignWell.") and becomes a client.
2. A **deposit invoice is created in Stripe and emailed** to the client's first contact, due in three days. Signing is therefore also sending an invoice.
3. A "SOW signed" email and a portal invitation go to the client.

SignWell is used only for consulting SOWs. Operator agreements are not produced or signed through the admin console; the templates in `docs/templates/operator/` describe a DocuSign workflow, and how a given Operator client's agreement is executed is handled by the Captain and recorded in that client's private folder.

### 6. Delivery (stage `engaged`)

The signed SOW creates an **engagement**: scope, status, estimated and actual hours, milestones, contacts, files, and a parking lot for out-of-scope asks (`/admin/engagements/<id>`). Work in motion across clients is on `/admin/services`. Marking a payment milestone complete creates and emails that milestone's invoice at once ([Money](/admin/playbook/money)).

### 7. Billing

Payment follows the SOW: 50/50 by default, with 40/30/30 considered for 40 hours or more (Decision #14). One-time invoices show on `/admin/billing` as invoiced, paid and outstanding; overdue ones appear in the home page's "Needs you today" list. The recurring side of billing is Operator subscriptions, which a consulting client does not have.

### 8. The client portal

The client sees their quotes, invoices, engagement and documents at `portal.smd.services` ([The Client Portal](/admin/playbook/client-portal)). Sign-in is through Clerk, and a person sees a client's records only once they are bound to that client; an unbound sign-in gets no portal (`src/lib/portal/session.ts`). Draft and superseded quotes never reach the client.

### 9. Handoff, support, and after (stages `delivered`, `ongoing`)

Moving the engagement to **handoff** does three things on its own: it sets the handoff date and the end of the two-week support window (14 days later), schedules the follow-ups (referral ask, review request, support check-in, 30-day feedback), and moves the record to `delivered` (`src/lib/db/engagements.ts`).

**Mark as Ongoing** on the record's page moves a delivered client to `ongoing`. It is refused until the completion invoice is paid, unless someone gives a written override reason, which is recorded on the client's timeline (`src/lib/db/entities.ts`). Nothing moves a client to `ongoing` automatically when the support window ends. A client who wants more work gets a new scope or, if it fits, an Operator subscription (ADR 0004, lock 4).

A prospect that falls out at any stage before signing is marked `lost` with a reason, shown on the Lost tab.

## Track 3: a Hosted Agent

A buyer checks out on `/agent`. Checkout creates the client record, a subscription waiting to be set up, and an intake item, and emails team@. The seat is then set up by hand from a template and switched on; a payment alone never switches anything on (ADR 0067). A seat built on a role such as `couples` is written by hand from that role's template, merged and reprovisioned, and the agent then introduces itself to each person by email (ADR 0090, superseded decision 4; `docs/runbooks/hosted-agent/couples-setup.md`). Payment failures follow the same human-run steps as the Operator.

## Offboarding an Operator client

The exit path, set by the Captain on 2026-07-04 (ADR 0065). Every step that matters is a person's decision; the system only raises the alert.

**Termination is asymmetric** (ADR 0065, amended 2026-08-29). The client may cancel in the portal, or by letter with the same effect, with no notice period; it takes effect at the end of the month already paid for. SMD's own termination gives the client 30 days' notice. The signed agreement is the binding text.

**Pause** is the alternative to cancelling: collection stops (a paused seat is never charged), the portal shows the seat paused, and the client keeps access to its audit record. A pause past 60 days prompts a conversation about whether it is really an offboarding.

**When a payment fails** (the dunning ladder): day 0, team@ gets an alert and the Captain contacts the client personally the same business day; day 14 past due, the seat is paused; day 30, it is treated as a cancellation and offboarding begins. Stripe marking an invoice past due never reduces access by itself. Each step is the Captain's ([Money](/admin/playbook/money)).

**The offboarding sequence** (any termination):

1. **Final export.** The client receives its audit record and the Operator's working memory in exportable form within 14 days of the termination date.
2. **Access removed.** Claude-connector grants are revoked (the ADR 0057 kill switch), connector credentials revoked or returned, the Operator's mailbox closed.
3. **Destruction.** An agent runs `operator/bin/decommission-customer.sh`, which destroys the machine, its storage and the client's stores, keeping only legal, tax and accounting records. Before the client's folder is retired, the same run builds the signed compliance evidence packet and reports its path, file count and signature check. A live run will not start without the signing key in place.
4. **Attestation.** Destruction is confirmed in writing on request.

Return and destruction finish within 30 days of termination; that window and the 30-day notice of a new sub-processor are the DPA's standard terms (ADR 0065). ADR 0065 also calls for a dry run of the whole sequence on the staging seat before the first paid contract carries these terms; whether that dry run has been done is not recorded in this repository. Ending a client is the Captain's decision; once made, running the sequence is agent work, reported with proof that each layer is gone.
