---
title: The Admin Console
section: product
order: 3
owner: captain
review_every: 90
last_reviewed: "2026-10-21"
summary: The operator-facing console at admin.smd.services - every surface, the lead-to-cash working spine, the Operator fleet cockpit, and the cross-cutting patterns that hold across all of it
sources:
  - label: src/layouts/AdminLayout.astro (top nav + shell)
    href: https://github.com/venturecrane/ss-console/blob/main/src/layouts/AdminLayout.astro
  - label: src/pages/admin/ (route tree)
    href: https://github.com/venturecrane/ss-console/tree/main/src/pages/admin
  - label: src/pages/api/admin/ (action endpoints)
    href: https://github.com/venturecrane/ss-console/tree/main/src/pages/api/admin
  - label: src/pages/admin/operator/ (Operator fleet)
    href: https://github.com/venturecrane/ss-console/tree/main/src/pages/admin/operator
  - label: ADR 0077 - Admin portal mirrors the client portal
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0077-admin-portal-mirrors-client-portal.md
  - label: src/lib/admin/nav.ts (the five-destination spine)
    href: https://github.com/venturecrane/ss-console/blob/main/src/lib/admin/nav.ts
  - label: src/pages/admin/index.astro (home)
    href: src/pages/admin/index.astro
  - label: src/pages/api/admin/operator/ (fleet actions)
    href: src/pages/api/admin/operator
---

The admin console at `admin.smd.services` is where SMD runs the business: clients, proposals, engagements, invoices and subscriptions, and the fleet of client Operators with their alerts and change requests. For you, the pages that matter most each day are Home ("Needs you today"), a client's hub (prices, invoices, subscription), and the Fleet's Alerts and Requests pages; most Operator changes are not made here but by an agent through a pull request ([Changing a Client's Operator](/admin/playbook/changing-a-clients-operator)).

## Where to do the common things

| To... | Go to |
|---|---|
| See what needs attention today | Home (`/admin`) |
| Add a client | Clients, then **Add a client** (`/admin/clients/new`) |
| Price a seat, issue or present an invoice, pause or cancel a subscription | The client's hub (`/admin/clients/<id>`) |
| Record a client's signed agreement so it shows in their portal | The client's hub |
| Answer a client's change request | Fleet, then Requests (`/admin/operator/requests`) |
| See fleet alerts | Fleet, then Alerts (`/admin/operator/alerts`) |
| Clear a seat that stopped itself, after investigating | Fleet, the seat's Overview, **Clear stop** |
| Issue or revoke a person's Claude-connector access | Fleet, the seat's Connectors tab |
| Activate a self-serve Operator after it is built | Self-serve intake queue (`/admin/operator/intake`, inside Fleet) |

## What the admin console is

The admin console is the SMD-facing surface served at `admin.smd.services`. The subdomain rewrites to `/admin/*` and every route requires `role='admin'` in the middleware (`src/middleware.ts`), checked again at the top of each page. Admin access is per user (`users.role`); who holds it today is not recorded in the repository. The source lives under `src/pages/admin/`, the action endpoints under `src/pages/api/admin/`, and the shell is `src/layouts/AdminLayout.astro`.

The console renders server-side with no client charting library and minimal client JS. Most mutations are plain HTML `<form method="POST">` that hit an endpoint under `src/pages/api/admin/`, which validates, writes, and redirects back to the source page with a status param (`?saved=1`, `?error=...`) that the page turns into a flash banner. This POST-then-redirect shape is the dominant interaction pattern across the whole console.

## Top navigation

The navigation is a fixed five-destination spine (ADR 0077, 2026-07-14, which superseded the flow-ordered nav of ADR 0046): the admin console mirrors the client portal's object graph from the guide's side. The destinations are defined in `src/lib/admin/nav.ts` (`buildAdminNav`) and rendered by `src/components/admin/AdminNav.astro`:

| Section | Route | What it does |
|---|---|---|
| **Home** | `/admin` | The cockpit. Action-first: what needs you today, the two revenue shapes, then the three motion cards. |
| **Clients** | `/admin/clients` | The account directory; delivery, operators, and billing drill in per client. |
| **Fleet** | `/admin/operator` | Running products across all clients: the Operator fleet cockpit and per-customer drill-ins. |
| **Billing** | `/admin/billing` | The bi-modal money surface (ADR 0046): one-time invoices and the recurring Operator line. |
| **Playbook** | `/admin/playbook` | This handbook. |

**Settings** (`/admin/settings`) is an icon in the header, not a tab. **Leads** (`/admin/entities`), **Services** (`/admin/services`), engagement pages (`/admin/engagements/<id>`), **Follow-ups** (`/admin/follow-ups`) and the old `/admin/assessments/<id>` redirect also live under `src/pages/admin/` and are reached from inside the flow (a home card, a client, a lead's meeting) rather than from a tab.

## Home: the launchpad

`src/pages/admin/index.astro` is action-first. It opens with "Needs you today" - overdue invoices, at-risk services, and overdue follow-ups in one queue - then shows the two revenue shapes (one-time: invoiced / paid / outstanding; recurring: active operators and monthly recurring revenue, with any unpriced seat called out), then two motion cards that link into their surfaces:

- **Delivery** - services in motion, at risk, consulting (links to Services).
- **Fleet** - operators, healthy, alerting (links to the Operator fleet).

Every tile is a link; the home page itself takes no action. Its queries run in parallel so it stays fast as the data grows.

## The lead-to-cash spine

The core working flow is a single path: a business enters as a lead, moves through stages, gets a meeting, receives a quote, and (if it accepts) becomes an engagement that gets delivered and invoiced. The stages are `signal -> prospect -> meetings -> proposing -> engaged -> delivered -> ongoing`, with `lost` reachable from anywhere. How a business travels this path is the subject of `/admin/playbook/customer-lifecycle`; this section documents the surfaces that drive it.

### Leads list (`/admin/entities`)

The unified lead working view. It tabs by stage with a count per tab and a pipeline filter (Review Mining, Job Monitor - a filter over rows the retired lead-gen pipelines created). Each stage fills its rows differently, because what you need to see about a raw signal is not what you need to see about a lead in proposing:

- **signal** - the basic row with Promote and Dismiss. The machine-written signal evidence these rows once showed is no longer computed (`src/lib/admin/entity-row-view.ts`).
- **prospect** - whether an outreach draft exists, and the first contact email.
- **meetings** - all meetings and quotes for the entity; the row derives sub-state, next-meeting date, and whether a quote can be drafted.
- **proposing** - the top active quote per entity, sorted oldest-sent-first so an expiring quote surfaces.
- **engaged** - engagement progress (actual vs estimated hours) and an invoice rollup (outstanding count and amount, overdue flag).
- **lost** - a structured lost-reason rollup (code plus detail).

Bulk select is switched off on every stage today: the list renders each row with `bulkEnabled={false}` (`src/pages/admin/entities/index.astro`). Promote and Dismiss are per-row signal-stage actions. Every stage carries empty-state copy that names the next action rather than leaving a blank.

To create a business by hand, **Add a client** (`/admin/clients/new`) takes a business name and an optional primary contact and creates the record at the prospect stage.

### Lead detail (`/admin/entities/[id]`)

The decision surface for one business. It shows an identity strip (the signal source, the business name, an actor-role chip, the vertical when one is recorded, the stage, and how long the entity has been in it - see `EntityIdentityStrip.astro`), a contacts panel, and a deduplicated timeline of context entries (signals, notes, outreach, observations) with an inline add-note form, followed by collapsible rollups of the entity's meetings, engagements, quotes, and invoices.

The mutations each hit an endpoint under `src/pages/api/admin/entities/[id]/`:

- **Add note** (`context`) - appends a note to the timeline, from the detail page's inline form.
- **Log reply** (`reply-log`) - records an inbound reply as a context entry.
- **Send booking link** (`send-booking-link`) - on a prospect, creates a meeting, transitions prospect to meetings, and sends the booking email.
- **Promote** (`promote`) and **Dismiss** (`dismiss`) - signal-stage row actions on the leads list; promote moves signal to prospect and schedules the follow-up cadence.
- **Stage change** (`stage`) - transitions stage against a valid-transition table, optionally recording a lost reason and detail.
- **Merge** (`merge`) - folds a duplicate entity into this one.

The automated enrichment pipeline and its detail-page surfaces (the enrichment summary, the Re-enrich action, the pain-score and tier readouts, and the missing-data warnings built on them) were retired with the lead-gen machine on 2026-07-01 (PRs #1610/#1616, ADR 0060); migration 0081 dropped the scoring columns, and the detail page no longer renders an enrichment block.

### Meeting detail (`/admin/entities/[id]/meetings/[meetingId]`)

The assessment-call working surface. It shows the entity and meeting info, the schedule details when the meeting is booked (slot, timezone, guest, join link, calendar link), a live-notes textarea, and a complete-meeting form. Live notes auto-save after a short idle or on blur (`meetings/[meetingId]/live-notes`). Completing the meeting (`meetings/[meetingId]/complete`) records outcome notes, a duration, and an explicit next-stage choice - the admin picks the next stage rather than the system hardcoding "proposing," and completing a meeting never creates a quote (that is a separate, deliberate step). The legacy `/admin/assessments/[id]` route 301-redirects here; meetings were backfilled from the old assessments table preserving IDs.

### Quote detail (`/admin/entities/[id]/quotes/[quoteId]`)

Where a proposal is built and sent. A draft quote is editable: line items (problem, description, estimated hours) can be added, edited inline, and deleted, and the deposit percentage can be changed. Once sent, the quote is read-only. The page computes the payment structure from the totals - a deposit split for smaller engagements, a three-milestone breakdown for larger ones (the thresholds and terms are in `/admin/playbook/pricing-economics` and the Decision Stack). It carries a separate authored-client-content block (schedule, deliverables, engagement overview, milestone labels) that renders only what a human authored and shows a `TBD in SOW` marker otherwise - this is the no-fabrication policy applied at the quote.

From the quote, the admin generates the SOW PDF and sends it for signature. Sending requires a generated SOW and no already-open signature request (`/api/admin/quotes/[id]/sign`); the page warns if the quote was modified after the last PDF was generated. The client never sees an hourly rate or an hours breakdown. The full consulting motion this sits inside is documented at `/admin/playbook/consulting-engagement`.

### Engagement detail (`/admin/engagements/[id]`)

The delivery surface once a quote is accepted. It shows the engagement header (status, the key dates - start, estimated end, handoff, completed - and the quote summary), conditional status-transition buttons, and an editable details block (scope summary, dates, hours, and the originating signal for ROI attribution). Below that:

- **Milestones** - a list with per-row status transitions and a payment-trigger toggle; completing a milestone whose payment trigger is set auto-creates the corresponding invoice (`engagements/[id]/milestones`).
- **Parking lot** - scope-discovery items, each with a requester and a disposition (fold in, follow on, or dropped) that requires a rationale note at the time of disposition (`engagements/[id]/parking-lot`).
- **Contacts** - engagement-scoped roles (`engagements/[id]/contacts`).
- **Deliverables and consultant photo** - file uploads to R2 (`engagements/[id]/deliverables`, `engagements/[id]/consultant-photo`).
- **Time entries** - logged hours against the engagement (`/api/admin/time-entries`).

Invoices are sent and voided from the invoice endpoints (`/api/admin/invoices/[id]`): `send` creates the invoice in Stripe and emails the hosted link, `present` finalizes it in Stripe with no email so it is payable in the portal only, `void` voids it, and a mark-paid path records an offline payment. A `reschedule` action changes the due date of a presented or sent invoice: Stripe does not allow a due date to change once an invoice is finalized, so the action issues a replacement Stripe invoice with the same authored lines and payment method, points the row at it, then voids the original. No email goes out; the client sees the new date in the portal and on the Stripe payment page. The integration mechanics (Stripe, SignWell, R2, Google) are in `/admin/playbook/integrations-tooling`.

## Clients, Services, and Billing

These three surfaces watch the post-acceptance business.

- **Clients** (`/admin/clients`) is the account directory: entities past the acceptance line (engaged, delivered, ongoing), with a billing rollup and an Operator badge when one is provisioned. The client hub (`clients/[id]`) is the per-account view - identity, billing at a glance, the services on the account (consulting engagements and one card per Operator seat, each with its posture, monthly price, payment method and line name), recent activity, open invoices, and contacts. Each seat with a subscription record is priced on its own card, with a form that sets a price or clears it to unpriced (`/api/admin/clients/[id]/operator-price`); that card's pause, resume and cancel controls act on that seat's Stripe subscription only (`/api/admin/clients/[id]/subscription-billing`). The hub also records the client's executed agreements, with a title and execution date typed by a person, which then appear in the client's portal (`/api/admin/clients/[id]/operator-agreements`).
- **Services** (`/admin/services`) is the global delivery list - every in-flight service across all clients, risk-sorted, with a contextual risk column (at risk if overdue, next handoff, not yet priced). It runs a spine-drift check and surfaces any drift loudly (orphan engagements, childless services, unpriced operators, configs without a service) so the operator reconciles it manually rather than letting the money model and the delivery model silently diverge (ADR 0046).
- **Billing** (`/admin/billing`) is the bi-modal money surface. A two-revenue band shows one-time (invoiced / paid / outstanding, with overdue called out) beside recurring (active operators and MRR, with unpriced called out), and three tabs break out Quotes, Invoices, and Recurring. MRR is computed from the service spine, not from a subscriptions table.

## The Operator fleet cockpit

`/admin/operator` is the SMD-side cockpit for the Operator fleet, distinct from the client-facing console documented at `/admin/playbook/operator-console`. It is designed per `docs/design/operator/01-admin-portal.md` and splits into fleet-wide pages and per-customer drill-ins.

### Fleet-wide pages

- **Roster** (`operator/index.astro`) - the default landing, one row per operator, built for scanning a growing fleet: is anything on fire across all my operators. It composes three console-side projections only (customer identity and posture, the runtime-summary mirror, and heartbeat) and never reads a Machine's runtime D1 directly or joins two customers (ADR 0009). The heartbeat also carries the Machine's cost-breaker level (ADR 0062): a HARD_STOP turns the seat's dot red with a note naming what stopped it, and recovery is a Captain clear, never automatic. Since 2026-09-02 that ladder is two states, OK and HARD_STOP; the yellow SOFT_STOP dot is kept only so a seat still running a pre-collapse overlay renders correctly until it is reprovisioned.
- **Alerts** (`operator/alerts.astro`) - one feed of open alerts across every seat: cost anomalies, Sentry spikes, missed heartbeats, audit-record integrity, and obligations owed to a client, each linking to the seat it concerns, with snooze and acknowledge. Other alert kinds appear as their sources are connected; the page footnote says which.
- **Requests** (`operator/requests.astro`) - the change-request inbox: what clients asked for with **Request a change** in their console. Acknowledge to record receipt, resolve once the change is made through the normal configuration path, or decline with a note; the resolver's email is stamped on it. Who may approve which change is on [Changing a Client's Operator](/admin/playbook/changing-a-clients-operator).
- **Provision** (`operator/provision.astro`) - pick a vertical, author the `customer.yaml` essentials, and validate them (the shared validator plus the secret-exclusion scan). On a clean validation it emits the validated `customer.yaml` for the operator to drop into `operator/customers/<slug>/customer.yaml` before running `operator/bin/reprovision.sh <slug>`, and it logs each attempt to `operator_provisioning_intent`. It never commits to the repo or stands up a Machine: a Worker cannot write to the repo or call Fly (ADR 0012 §2).
- **Costs** (`operator/costs/index.astro` and `operator/costs/[customer_slug].astro`) - the SMD-only economics surface, fleet summary and per-customer drill-down. Cost is deliberately not a roster column.
- **Config history** (`operator/config-history/[customer_slug].astro`) - the per-customer `customer.yaml` materialization history, every applied version with its digest.

### Per-customer drill-in tabs

Each operator drills into `src/pages/admin/operator/[customer]/*`:

| Tab | File | What it shows |
|---|---|---|
| **Overview** | `index.astro` | The per-operator hub, including the **Clear stop** form for a seat that stopped itself (`/api/admin/operator/[customer]/clear-stop`). |
| **Authority** | `authority.astro` | The per-domain client authority switches (ADR 0041). A change here is recorded as intent; it takes effect when the client's `customer.yaml` is changed through a pull request. |
| **Config** | `config.astro` | The authored `customer.yaml` rendered for reading. |
| **Connectors** | `connectors.astro` | Which systems the operator is wired to, and their health (ADR 0042 / 0020). Also where SMD issues or revokes a person's Claude-connector access grant; a revoke takes effect on their next call (ADR 0057, `/api/admin/operator/[customer]/mcp-grants`). |
| **Governance** | `governance.astro` | The authored entitlement ceilings (ADR 0025 / 0035), checked against the industry floor. Like Authority, a change is recorded as intent and applies through the configuration file. See [Autonomy & Governance](/admin/playbook/autonomy-governance). |
| **People** | `people.astro` | Who at the client can reach and configure the operator. |
| **Memory** | `memory.astro` | The relationship surface (ADR 0048): what the operator has learned about working with the client, read-only and fail-closed. See `/admin/playbook/knowledge-memory`. |
| **Lifecycle** | `lifecycle.astro` | Provisioning, reprovision, and decommission state. |
| **Chronology** | `chronology.astro` | The seat's medical-chronology packages this month (routine 11): counts, states, pages, cents, and the delivery folder, read live over the runtime-read seam (kind `medchron_jobs`), audited, fail-closed, never a document or page of text. |
| **Runtime** | `runtime.astro` | Live detail (activity and drafts, audit log, matters) read from one seat at a time (ADR 0043 path A). Each read is audited; if the read path is not configured, the page says so and reads nothing. |

### Self-serve intake queue (`/admin/operator/intake`)

The concierge queue for self-serve Operator purchases (ADR 0067; one product, ADR 0100), reached from a link on the Fleet page. The old `/admin/hosted-agent` URL 301s here. One card per purchase, copy-paste-ready for authoring `customer.yaml` from the self-serve preset, `operator/customers/_template/presets/self-serve/customer.yaml`. Two actions live on each card: **assign slug** (names the seat and its Fly app and unlocks the customer's key form) and **activate** (marks the subscription active and the intake live and sends the authored go-live email). Activate runs after `reprovision.sh` and the boot smoke test pass; the button does not provision.

All per-customer runtime detail comes through the read seam in `src/lib/operator/runtime-read.ts`: single customer per call, read-only, audited, fail-closed. The console observes a running operator; it governs what the operator may do through the authored config it writes, not by querying or mutating the Machine live. The architecture behind these surfaces is at `/admin/playbook/operator-platform` and `/admin/playbook/architecture-map`.

## Settings

`/admin/settings` is the configuration hub. It links to:

- **Follow-ups** (`/admin/follow-ups`) - the cadence working list, tabbed Upcoming / Overdue / Completed with a type filter.
- **Google connect** (`/admin/settings/google-connect`) - the Google Calendar OAuth connection used for booking, showing the connected account or a connect button.

The **Lead generators** surface (`/admin/generators`) and the **Pipeline settings** page (`/admin/settings/pipelines`) were removed with the automated lead-gen retirement on 2026-07-01 (PRs #1610/#1616). Lead generation is now hand-personalized outreach (ADR 0059), not a configurable ingestion pipeline.

## Patterns that hold across the console

- **Auth.** Every page requires `role='admin'`; an API request returns 401 when not signed in and 403 when signed in without the admin role.
- **Empty states.** Every surface renders explicit prose that names the next action rather than leaving a blank, per `docs/style/empty-state-pattern.md`.
- **POST then redirect.** Mutations submit to an API endpoint that validates, writes, and redirects back with a status param the page turns into a flash banner.
- **Status badges and tone dots.** A shared badge component and a small set of tones (attention, error, muted, and the reserved completion green) signal health consistently.
- **Spine drift is loud.** Where the money model and the delivery model can diverge, the divergence is surfaced for manual reconciliation rather than silently reconciled.
- **No fabricated client-facing content.** Anything that can reach a client - quote content, SOW fields, invoice line items - renders authored data or an explicit TBD marker, never invented copy. The policy and the tests that enforce it are in `/admin/playbook/security-trust`.
