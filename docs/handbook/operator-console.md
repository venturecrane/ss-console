---
title: The Operator Console
section: product
order: 1
owner: captain
review_every: 90
last_reviewed: "2026-10-21"
summary: The part of the client portal where a client manages their Operator - what it is set up to do, the record of what it did, who has access, and the few controls the client may operate themselves
sources:
  - label: src/pages/portal/products/operator/ (console surfaces)
    href: https://github.com/venturecrane/ss-console/tree/main/src/pages/portal/products/operator
  - label: src/pages/api/portal/operator/ (change request and settings actions)
    href: https://github.com/venturecrane/ss-console/tree/main/src/pages/api/portal/operator
  - label: src/pages/api/portal/products/operator/ (pause, level dial, roles, secrets)
    href: https://github.com/venturecrane/ss-console/tree/main/src/pages/api/portal/products/operator
  - label: src/lib/operator/authority.ts (authority domains)
    href: src/lib/operator/authority.ts
  - label: src/lib/portal/operator/tier-language.ts (routine level wording)
    href: src/lib/portal/operator/tier-language.ts
  - label: src/lib/portal/operator/onboarding-read.ts (onboarding steps)
    href: src/lib/portal/operator/onboarding-read.ts
  - label: ADR 0052 - the portal is a management console, not a data surface
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0052-operator-portal-management-console-not-data-surface.md
  - label: migration 0038 - portal Clerk subscriptions substrate (roles)
    href: https://github.com/venturecrane/ss-console/blob/main/migrations/0038_portal_clerk_subscriptions_substrate.sql
  - label: docs/design/operator/ (portal-management design)
    href: https://github.com/venturecrane/ss-console/tree/main/docs/design/operator
  - label: ADR 0069 - Operator legibility and progressive configurability
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0069-operator-legibility-progressive-configurability.md
  - label: ADR 0085 - voice and output shape are established conversationally
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0085-conversational-establishment-voice-output-shape.md
---

The Operator console is the part of a client's portal where they manage their Operator: they read what it is set up to do and what it did, manage who has access, and use the few controls their agreement hands them, such as pausing it or moving a routine's level. Everything else they ask for with a **Request a change** button, which lands in the admin inbox for SMD to act on ([Changing a Client's Operator](/admin/playbook/changing-a-clients-operator)); no button in the console touches the client's actual work.

## What the client can do here, at a glance

| The client wants to... | Where | Who at the client |
|---|---|---|
| See what their Operator does | The Operator page (the one-pager) | Everyone with access |
| See what it did | Activity | Everyone with access |
| Read the full record for one matter or reference | Compliance, then Audit record | Principal and compliance |
| Pause or resume it | Settings, Pause control, if their agreement gives them that control | Principal |
| Move a routine's level, within the agreed ceiling | Settings, Autonomy per routine, if their agreement gives them that control | Principal |
| Add or remove people, change roles | Settings, Team access | Principal |
| Reconnect a system whose sign-in expired | Connections | Principal, where allowed |
| Change anything else | **Request a change** on the relevant section | Principal |

"Principal" is the client's own administrator; the signed agreements call this person a Named Administrator.

## What the console is, and is not

When a client subscribes to the Operator, the portal grows the console at `/portal/products/operator/`. ADR 0052 (accepted 2026-06-20) fixed what it may be: **the management console for the client's AI employee, and nothing else.** It does three jobs, which match the only three things SMD stores:

- **Direct** - the terms of the job: what the Operator may do, which systems it connects to, which skills are active. Backed by the client's configuration.
- **Account** - the record of what the Operator did and under what authority. Backed by the audit record.
- **Administer** - the relationship: team and roles, escalation contacts, subscription.

It is not a system of record, not a mirror of the client's systems, not a workspace, and not specific to one industry (ADR 0052 section 2). It holds no client business data. **No portal action touches client work** (section 4): there is no approve-draft or send button. A person reviews and sends a draft where it lives, in their email or system, or over the conversation with the Operator. The only buttons change the job itself: a role, a routine's level, a pause, a connection.

ADR 0052 section 6 also sets the rule for referring to a client's records: an opaque handle the console never interprets. The audit record page follows it today by treating the reference a client types as plain text compared for equality; the underlying field is still named for matters, and the move to the general `ref { connector, id }` shape is recorded as a follow-on in `src/pages/portal/products/operator/[instance]/compliance/audit-record.astro`.

The product itself is explained in [The Operator, in Plain Terms](/admin/playbook/the-operator), with engineering detail in [Operator Platform Architecture](/admin/playbook/operator-platform), [Autonomy & Governance](/admin/playbook/autonomy-governance) and [Knowledge & Memory](/admin/playbook/knowledge-memory).

The console ships almost no browser JavaScript. Every change is an HTML form that posts to an endpoint, which checks who is asking, their role and the authority setting on the server, acts, and redirects back with a status message.

## Two things decide what each person can do

### Who you are: the three roles

Reaching the console needs a signed-in user linked to the client, an active Operator subscription, and at least one role. The roles (migration 0038; `src/lib/portal/operator-access.ts`) are:

- **principal** - the client's administrator; the only role that manages people and operates any control.
- **staff** - day-to-day users; they read the record, status and connections.
- **compliance** - an oversight reader: the audit record, retention and evidence, with no power to change configuration or people.

A user with no role sees an access-pending page.

### What SMD has handed over: authority domains

Every control belongs to an **authority domain**, and for each one the client's configuration says who operates it: SMD (`managed`) or the client as well (`client`) (`src/lib/operator/authority.ts`, ADR 0041). Nothing is handed over by default: an absent or unset setting means `managed` for every domain. A managed control renders **Read and Request**: the client sees the current state and can file a change request. A domain set to `client` renders **Read and Operable** for a principal. SMD keeps full control either way.

The setting lives in the client's `customer.yaml` (`authority:`), so changing it is a reviewed pull request. The admin console's Authority tab records the intent to change it, but the change takes effect only through that pull request (`src/pages/api/admin/operator/[customer]/authority.ts`).

The dual rendering is done by `DomainSurface` and `RequestChangeForm`. A request posts to `/api/portal/operator/change-request`, is emailed to team@, and appears in the admin inbox at `/admin/operator/requests` ([The Admin Console](/admin/playbook/admin-console)). The switchable domains:

| Domain | Governs |
|---|---|
| `configuration` | Persona, skills, scope, business hours, escalation contacts |
| `trust` | Routine levels and action limits, within the agreed ceiling and the industry floor (ADR 0056) |
| `connectors` | Connect, reconnect, and who holds the credential |
| `runtime` | Pausing and resuming, and other controls the configuration exposes |
| `memory` | Reviewing and dismissing what the Operator has learned |
| `people_access` | Users, roles, time away, voice profiles |
| `compliance` | Evidence packets, retention, holds |
| `observability` | Health, connector and stop actions |

Two domains are never handed over: `provisioning` (stand up, resize, pause from SMD's side, decommission) and `cost`, which the client never even reads.

## Honest when there is nothing to show

Several pages read live state from the client's seat through the runtime read path (ADR 0043). The activity log comes from the seat's own audit log, the "where is it right now" line comes from the seat's heartbeat (ADR 0023; `src/lib/portal/operator/aliveness.ts`), and the drafts-waiting count comes from the seat's pushed `draft_queue_depth` (`src/lib/portal/operator/home.ts`). Each fails closed: if the seat cannot be reached or has not reported yet, the page shows an honest empty state, never invented content ([Security & Trust](/admin/playbook/security-trust)).

## The Operator page (the one-pager)

`/portal/products/operator` lists the client's Operators and goes straight to the one when there is only one. The Operator's own page is the **one-pager** (`docs/design/operator/05-console-blueprint.md` section 5, amended 2026-07-15): the whole Operator on one page, with a status header and then four sections in this order, with an "On this page" rail to jump between them:

- **Persona** - who it is and how it presents itself.
- **Duties** - every routine with its trigger, the level it is running at now, and the highest level it may be raised to. Each routine's wording comes from the client's agreement and is held to it by the check described in `docs/runbooks/operator/routine-lifecycle.md`.
- **Access** - which mailboxes it can see and which systems it is connected to.
- **People** - who may email it and whom it may write to, escalation contacts, blocks, and who is on the account.

The rule is one page to read, one page to act: changes happen in Settings. Each section keeps its own request-a-change path; a section with nothing authored is left out entirely. Paused, not subscribed and access-pending states render plainly.

Retired pages redirect so old links keep working: `work/` and `people/` go to the matching one-pager section, `account/` goes to Settings, `configure/` goes to the one-pager, and `scope/` goes to People.

The client's home card shows "N drafts waiting for review" when the seat reports drafts waiting (`src/lib/portal/home-cards.ts`). That is a count, not a queue; there is no draft list here (ADR 0052 section 4).

## Account: the record of what it did

- **Activity** (`activity/`) - everything the Operator did, filterable, readable by every role. It also shows each pause, resume and level change, with who made it. The audit record stores facts about the Operator's own actions (who, what kind of action, which system, on what authority, the outcome), never the content of documents or emails (ADR 0052 section 5). For principal and compliance it shows the retention setting; an evidence packet is requested from SMD, because self-service export has not been built.
- **Audit record** (`compliance/audit-record`) - the client types the reference their own system uses (for a law firm, a matter) and reads every recorded action against it with what authorized it. Principal and compliance only, and open to every client whether or not the compliance dashboard is switched on, because the agreement promises the client this record at any time.
- **Calendar** (`calendar/`) - items the Operator put on the client's calendar and items it proposed for someone to approve, with conflict checks (`src/lib/portal/operator/calendar.ts`).

## Direct: what it is set up to do

- **Skills** (`skills/`) - the answer to "what can it do?": each skill on the Operator and what sets it off (on request, on a schedule, on an event). Readable by every role; the only action is a change request (ADR 0069).
- **Settings** (`settings/`) - the page where things change. Readable by every role. It holds:
  - **Pause control** (`runtime` domain). Where the client has that control, a principal can pause or resume with a required reason. A pause stops all activity at once and stays until someone resumes it. The seat must confirm the pause before it is logged, and every pause and resume is logged with who and why (`src/pages/api/portal/products/operator/[instance]/pause.ts`).
  - **Autonomy per routine** (`trust` domain). Where the client has that control, a principal moves a routine between "Surfaces it", "Prepares it for you" and "Handles it" (`src/lib/portal/operator/tier-language.ts`), with a required reason. It applies to the running seat at once and is logged. It can never go above the ceiling in the agreement or the industry floor; routines fixed by the agreement show their level with nothing to change (`src/pages/api/portal/products/operator/[instance]/entitlement.ts`).
  - **Plan & billing**, shown only once a billing relationship exists, with the Manage Billing link to Stripe. Read-only; the subscription is SMD's domain.
  - **Operational alerts**: who is told about red flags and failures.
  - Links to **Connections**, **Team access** and **Advanced**.
  - It is also where a client lands after connecting a system, with a success or failure message.
- **Advanced** (`settings/advanced`) - two things, neither of which changes the Operator from here. First, a read-only view of the firm's established voice and document shapes. Second, a typed form over the client's `customer.yaml` that validates a proposed change and records it as submitted; it writes nothing, because the configuration file in the repository is the authority and is published to the seat when a pull request merges (`src/pages/api/portal/operator/settings/customer-yaml-update.ts`, ADR 0012). Fields SMD controls show as locked and are refused even if the form is bypassed. Every submission, accepted or rejected, is audited.

### Voice and document shape are set by talking to the Operator

ADR 0085 (2026-08-02) moved this off the console. A client administrator tells the Operator, through a channel they already use with it, to read named examples and establish the firm's voice or a document's shape. The Operator derives the rules and submits them through a checked path that confirms who asked and runs automatic checks before anything is installed. It takes effect on completion, and the Operator's reply lists any rule the firm's own examples broke. The console's part is visibility: what has been established, by whom and when, and corrections proposed by others waiting for an administrator. A person's own writing preferences are a separate layer any user can set for themselves.

## Administer: connections, team, onboarding

- **Connections** (`connections/`) - each connected system with its status and who holds the credential. Where the client operates the `connectors` domain, a principal reconnects a system that uses a sign-in, or enters a key through a write-only field whose value never appears in a URL, log or database (`connectors/[connector]/secret`; ADR 0042). The sign-in flow uses a signed, 10-minute state that ties the request to the client, the system and the person (`src/pages/portal/products/operator/oauth/[connector]/`), and records the outcome without any token.
- **Team** (`team/`) - who is on the account, their roles, last sign-in and who is away. The controls are in **Team access** (`settings/users`), principal only: grant and revoke roles and invite people. A principal cannot remove their own last principal role, which would lock the client out. When SMD manages the roster, these become read-and-request.
- **Onboarding** (`onboarding/`) - a two-step checklist for a new client: invite the team, and connect systems. Each step's status comes from the same data its page reads (`src/lib/portal/operator/onboarding-read.ts`).

## Everything is recorded

Role grants, revokes and invitations, every Advanced form submission (including rejected ones), every connector key entered, every pause and resume, and every routine level change are recorded with who did it. Pauses and level changes act on the running seat through its own gate, which must confirm before the change is recorded. Every other configuration change is made through the client's configuration file and a pull request, never by editing the running seat. The tables behind this are in [Data Model](/admin/playbook/data-model); what the record proves is in [Security & Trust](/admin/playbook/security-trust).
