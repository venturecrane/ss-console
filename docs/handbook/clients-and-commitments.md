---
title: Clients & Commitments
section: running
order: 3
owner: captain
review_every: 30
last_reviewed: "2026-11-10"
summary: Which seats exist, which are clients and which are SMD's own, what we owe each client, and where the commercial detail lives.
sources:
  - label: Seat definitions (customer-yaml types)
    href: src/lib/operator/customer-yaml/types.ts
  - label: Seat directories (operator/customers)
    href: operator/customers
  - label: Fleet seat rule (rings.py)
    href: operator/bin/lib/fleet/rings.py
  - label: Obligation register CLI
    href: .claude/bin/register
  - label: Operator alerts page
    href: src/pages/admin/operator/alerts.astro
---

Every client the Operator serves runs on its own **seat**: an isolated machine with its own configuration, connections and records. This page lists the seats, says which ones serve clients and which are SMD's own, and shows you where to read what we owe each client.

## The seats

The table below is generated from the seat folders in the repository, so it cannot fall out of date without the build failing. It shows what each seat is. It deliberately does not show who pays or on what terms; that is commercial detail and lives in the private engagements repository (see [Handoff Companion](/admin/playbook/handoff-companion)).

<!-- BEGIN GENERATED: seat roster. Regenerate with `npm run handbook:roster -- --write`; tests/handbook-integrity.test.ts fails while it is stale. -->

Seats declared in the repository (`operator/customers/`). Whether each one is running right now is on the Operator fleet page in the admin console (`/admin/operator`), not here.

**Client seats**

| Seat | Purpose | Product | Role | Vertical |
|---|---|---|---|---|
| `ashton-price` | client | operator | none | law-firm |
| `ashton-price-marketing` | client | operator | marketing | law-firm |
| `brokery` | client | hosted-agent | none | real-estate |
| `captain-mark` | client | hosted-agent | couples | mixed |

**SMD's own: pre-production**

| Seat | Purpose | Product | Role | Vertical |
|---|---|---|---|---|
| `ap-staging` | internal | operator | none | law-firm |
| `smd-staging` | release-twin of `ashton-price` | operator | none | mixed |

**SMD's own: demo**

| Seat | Purpose | Product | Role | Vertical |
|---|---|---|---|---|
| `pilot-smokeball` | demo | operator | none | law-firm |

**SMD's own: internal**

| Seat | Purpose | Product | Role | Vertical |
|---|---|---|---|---|
| `scott` | internal | hosted-agent | none | mixed |
| `smd-guide` | internal | operator | guide | mixed |

<!-- END GENERATED: seat roster -->

## What the kinds mean

Every seat declares a kind, and the kind never changes over the seat's life (`src/lib/operator/customer-yaml/types.ts`).

| Kind | What it is | Real client data? |
|---|---|---|
| customer | A real client's seat, doing their real work | Yes |
| proving | SMD's own seat connected to real systems with fictional matters, used to prove new work before a client sees it | No |
| preprod | The permanent pre-production gate every release passes through first | No |
| internal | A seat that works on SMD itself rather than in a client's business | No |
| sandbox | Disposable, realistic, no real data | No |

The **product** column says which offering's commercial terms the seat is on: `operator` (built with the client) or `hosted-agent` (the stored value for the self-serve offering). Both are the one Operator; nothing on a seat branches on this column (ADR 0100). A **role** is a packaged job, such as `couples` ([ADR 0090](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0090-hosted-agent-roles.md)).

Only `customer` seats appear in the hourly work-health checks as individual lines; SMD's own seats are counted together, so a problem on a client seat is never buried under test noise.

## Kinds and purposes

A seat carries two labels that answer different questions. Its **kind** (above, in `customer.yaml`) says whose records are on it. Its **purpose** (in the seat's `seat.yaml`, [ADR 0107](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0107-seat-purpose-and-the-twin-gate.md)) says what it is for when SMD changes the Operator:

| Purpose | What it is for |
|---|---|
| client | Serves a client. Every client seat here is also kind `customer`. |
| release-twin | An exact copy of one client seat, named in its `twin_of`. Every release goes to the twin first, and the client seat takes a release only after its twin is done on it. |
| demo | Shows the product on SMD's own test data. It gates nothing. |
| internal | SMD's own work. |

Every tool that used to name a seat now asks for a purpose instead, so renaming a seat or adding one is an edit to its `seat.yaml` and `npm run gen:seat-registries`, nothing else. Today the one twin is `smd-staging`, the twin of `ashton-price`. It is not yet an exact copy: it binds no Smokeball connection. A later change renames it `ap-staging` and connects it to a test firm.

## What we owe

Every piece of work SMD owes a client is a row in the obligation register (see [What We Owe Clients](/admin/playbook/obligation-register) for how it works).

- **To read it,** ask an AI agent to run `.claude/bin/register list` (all clients) or `.claude/bin/register list --client <slug>` and summarize it for you. It is read from a terminal on purpose; there is no page for it. The agent needs a working copy of the repository and a signed-in Cloudflare tool, which the agent's own setup provides.
- **Anything overdue or unprovable** is flagged every night and shows on the Operator alerts page in the admin console (`/admin/operator/alerts`), which also emails team@smd.services. You do not have to run anything to learn that something is late.
- **When a client's letter promises something,** the session that read the letter records the promise before it closes. If you read client correspondence yourself, tell an agent what was promised so it is recorded.

## Where the commercial detail is

Agreements, proposals, correspondence and each client's dossier live in the private `venturecrane/engagements` repository under `operator/customers/<slug>/`, the same folder name as the seat. Pricing records live there under `pricing/`. This public handbook names the client and the seat, never the terms. See [Handoff Companion](/admin/playbook/handoff-companion) for how to get access.
