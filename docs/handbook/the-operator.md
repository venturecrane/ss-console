---
title: The Operator, in Plain Terms
section: product
order: 0
owner: captain
review_every: 90
last_reviewed: "2026-10-21"
summary: What the Operator is, the shapes it is sold in, what a client's Operator does day to day, how its limits work, and where the engineering detail lives.
sources:
  - label: ADR 0037 The Operator Thesis
    href: docs/adr/0037-operator-thesis.md
  - label: ADR 0004 The productized Operator offering
    href: docs/adr/0004-productized-operator-offering.md
  - label: ADR 0040 Operator positioning and why-us
    href: docs/adr/0040-operator-positioning-and-why-us.md
  - label: ADR 0007 One isolated machine per customer
    href: docs/adr/0007-per-customer-machine-isolation.md
  - label: ADR 0067 Hosted Agent
    href: docs/adr/0067-hosted-agent-self-serve-sku.md
  - label: ADR 0090 Hosted-agent roles
    href: docs/adr/0090-hosted-agent-roles.md
  - label: ADR 0075 Routine levels
    href: docs/adr/0075-routine-grid-compositional-enforcement.md
  - label: ADR 0035 No imposed entitlement defaults
    href: docs/adr/0035-no-imposed-entitlement-defaults.md
  - label: ADR 0056 Persona exposure and skill initiation
    href: docs/adr/0056-persona-exposure-skill-initiation-entitlements.md
  - label: ADR 0071 The confirm level
    href: docs/adr/0071-confirm-ceiling-and-hosted-agent-tier-ladder.md
  - label: ADR 0089 Staff send-as on approval
    href: docs/adr/0089-staff-send-as-on-approval.md
  - label: ADR 0052 The portal is a management console
    href: docs/adr/0052-operator-portal-management-console-not-data-surface.md
  - label: Seat kinds and products
    href: src/lib/operator/customer-yaml/types.ts
  - label: Client wording for the three routine levels
    href: src/lib/portal/operator/tier-language.ts
  - label: Lane-proof runbook (new kinds of job)
    href: docs/runbooks/operator/lane-proof.md
  - label: Operator context for engineers
    href: operator/CLAUDE.md
---

The Operator is the main thing SMD sells: an AI worker that does a business's coordinator and paralegal-style work inside that business's own systems, within limits the client sets. This page explains it without engineering terms, so you can describe it to a client, read an agent's report about it, and know which page holds the detail.

## What it is

The Operator competes with a hire, not with software. Every system a business runs does part of the work; a person usually connects them, reading the email, updating the record, chasing the document and booking the follow-up. The Operator does that connecting work (ADR 0037, Tenet 1). The systems it works in are things it connects to, not competitors.

It has no fixed job. Each client's Operator is set up for that client: which skills it has, what it may do, how it writes, which systems it reaches, and what it remembers (ADR 0037, Tenet 2). Industry starting points are called packs ([Vertical Packs](/admin/playbook/vertical-packs)).

How SMD talks about it is set by ADR 0040. The line is "it runs on your firm's expertise and gets better at your firm every week": the client is the expert, SMD builds and runs the worker, and the firm teaches it. For a law firm it does paralegal and coordinator work, never independent legal judgment, advice or representation. In sales and marketing, do not open with "AI employee"; lead with the role it fills and the outcome ([Positioning & Voice](/admin/playbook/positioning-voice)). Inside SMD, and in the portal design, "the client's AI employee" is used as plain shorthand (ADR 0052).

## The shapes it comes in

| Shape | What it is | Source |
|---|---|---|
| **The Operator** | A governed worker for a business, set up with the client and run by SMD as a managed service. The law-firm Operator is the one in production. | ADR 0004, ADR 0040 |
| **The Hosted Agent** | An always-on personal agent on the same platform, for a person or a household rather than a firm. It is reached over Telegram and email from approved senders only. | ADR 0067 |
| **A hosted-agent role** | A packaged job for a Hosted Agent, chosen per seat. The first role is `couples`, a shared agent for two people; SMD sets each one up by hand and all setup happens in the agent's own channels. | ADR 0090 |

Prices are never published and are not in this handbook; see [Pricing & Economics](/admin/playbook/pricing-economics) for where they live.

## What a seat is

Each client's Operator runs on its own **seat**: one isolated machine with its own configuration, connections, records and audit log. No two clients share a machine (ADR 0007). A seat's configuration is a reviewed file in the repository (`operator/customers/<slug>/customer.yaml`), not a setting someone flips; [Changing a Client's Operator](/admin/playbook/changing-a-clients-operator) explains how it changes. The list of seats, and which are clients and which are SMD's own, is on [Clients & Commitments](/admin/playbook/clients-and-commitments).

## What a client's Operator does day to day

**Routines.** Most of its work is a list of routines agreed with the client, each with a trigger (a schedule, an incoming email, a change in a system) and a level. The agreement's list is the routine grid (`operator/customers/<slug>/routine-grid.yaml`). Each routine runs at one of three levels (ADR 0075):

| Level (internal name) | What the client sees it called | What it means |
|---|---|---|
| flag-only | "Surfaces it" | It reads and tells someone; it changes nothing |
| prepare-and-route | "Prepares it for you" | It drafts the work for a person to check and send |
| auto-handle | "Handles it" | It does the work on its own |

The client-facing words come from `src/lib/portal/operator/tier-language.ts`; the internal names never appear on a client page. Some routines are fixed at one level by the agreement, and some kinds of work never auto-handle ([Changing a Client's Operator](/admin/playbook/changing-a-clients-operator), section 2).

**Requests by email.** People the client has authorized can email their Operator and ask for things. It answers if the request is within what it is allowed to do. For every such request, the seat sends SMD a **request card**: what was asked and how it was answered, or an alarm if nothing answered within 30 minutes ([Glossary](/admin/playbook/glossary), [The Administrator's Week](/admin/playbook/administrators-week)).

**Jobs.** Larger pieces of work run as jobs, for example a medical chronology, a demand letter, or a drafted document of a given class. The law-firm Case File Manager is one: it reviews a caseload, files a current File Status in each file, and numbers the changes it proposes for a person to approve ([The Case File Manager](/admin/playbook/case-file-manager)). A kind of job a seat has never done does not run on a real request until SMD has proved it on a dry run with that client's data; meanwhile it waits, and team@ gets "NEW KIND OF ASK" (`docs/runbooks/operator/lane-proof.md`). You accept the proof; the Operator cannot approve new kinds of work for itself ([Who Does What](/admin/playbook/who-does-what)).

**What the client sees.** The client manages their Operator in the Operator console inside their portal: what it is set up to do, the record of what it did, who has access, and the controls the client is allowed to operate ([The Operator Console](/admin/playbook/operator-console)). The work itself is reviewed where it lives, in the client's email or system, not in the portal (ADR 0052).

## How its limits work

- **The client authors what it may do.** There is no built-in posture. Every kind of action is either allowed, allowed with a person's check, or refused, as written into that client's configuration (ADR 0037, Tenet 3; ADR 0035).
- **Unset means refused.** An action nobody configured is refused: no send, no draft. Reading is the one thing that does not need to be granted (ADR 0035, ADR 0056).
- **A person's check comes in two forms** (ADR 0071). With **confirm**, the Operator asks a person in the same conversation and sends only after they say yes. With **draft for review**, it leaves a draft and the person sends it themselves. An email in a staff member's own name goes out only after that person approves the exact draft by replying to it (ADR 0089).
- **It cannot raise its own limits.** A client's administrator may move a routine's level within the ceiling the agreement sets; anything above that is a change to the agreement ([Who Does What](/admin/playbook/who-does-what)).
- **Everything it does is recorded.** Each action is written to an audit record the Operator itself cannot change or delete. The client can read it in the portal at any time ([Security & Trust](/admin/playbook/security-trust)).
- **It can be stopped.** A seat can be paused by SMD, by the client where the agreement gives them that control, and it stops itself when it hits a cost or safety limit ([Changing a Client's Operator](/admin/playbook/changing-a-clients-operator), section 6).

## What can go wrong, as the client sees it

From the client's side, a problem usually looks like one of these: the Operator did not answer a request, a scheduled routine did not happen, it asks them to sign in to a connected system again, it refused something they expected it to do, a job is waiting, or it is paused. Each has a matching alert to team@ and a first action, listed on [When Something Goes Wrong](/admin/playbook/when-something-goes-wrong). A refusal is often correct: it means nobody authorized that action, and the fix is a change request, not a repair.

## Where the engineering detail lives

| For | Read |
|---|---|
| The runtime, the seat, how it is built and released | [Operator Platform Architecture](/admin/playbook/operator-platform) |
| Limits, action classes, the safety gates | [Autonomy & Governance](/admin/playbook/autonomy-governance) |
| What it remembers and how | [Knowledge & Memory](/admin/playbook/knowledge-memory) |
| The systems it connects to and how people reach it | [Connectors & Channels](/admin/playbook/connectors-channels) |
| Why it is built this way | [The Operator Thesis](/admin/playbook/operator-thesis) |
