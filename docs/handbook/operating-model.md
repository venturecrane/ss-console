---
title: Operating Model & the Fleet
section: business
order: 10
owner: captain
review_every: 180
last_reviewed: "2026-10-21"
summary: How the Captain, the administrator and the AI agents divide SMD's work, how a piece of work moves between them, and how to direct an agent well.
sources:
  - label: Agent operating doctrine
    href: docs/doctrine/agent-operating-doctrine.md
  - label: CLAUDE.md (session mechanics, done means the client can do it, gone means gone)
    href: CLAUDE.md
  - label: Ops triage agent orders
    href: operator/ops-machine/TRIAGE.md
  - label: Fleet alerts schedule
    href: workers/fleet-alerts/wrangler.toml
  - label: Work-health schedule
    href: .github/workflows/work-health.yml
  - label: ADR 0035 - No imposed entitlement defaults
    href: docs/adr/0035-no-imposed-entitlement-defaults.md
---

SMD runs on three layers: the Captain makes the decisions that bind the firm, you run the business day to day, and AI agents do the technical work and report back with proof. This page explains how work moves between those layers and how to direct an agent; the exact list of who may approve what is on [Who Does What](/admin/playbook/who-does-what), and this page does not repeat it.

## The three layers

| Layer | Holds | In one sentence |
|---|---|---|
| **The Captain** | Strategy, pricing, contract terms, new commitments, spending, taking on or ending a client | Decides what binds the firm |
| **You, the administrator** | The daily and weekly routine, directing agents, routine client emails, invoices at set prices, routing change requests | Keeps the business running and brings the Captain only what is the Captain's |
| **AI agents** | Changes to the systems, live operations on client seats, investigation, drafting, monitoring | Do the work and prove it |

A fourth party sits beside these: **each client** decides how far its own Operator may go, within what its agreement allows. Neither SMD nor an agent sets a client's limits for them; an unconfigured action is refused (ADR 0035).

The rule underneath all of it is the first law of the agent doctrine: every call has exactly one owner, and the first step is to name it. Agents own execution and anything a repository or tool can answer; the Captain owns strategy, commitments and spend; the client owns its own posture (`docs/doctrine/agent-operating-doctrine.md`, Law 1).

## How work moves

| What arrives | Who handles it first | Goes to the Captain when |
|---|---|---|
| An alert in team@ (a seat stopped, a job missed, a connection down) | You read it; an agent investigates and fixes it ([When Something Goes Wrong](/admin/playbook/when-something-goes-wrong)) | Clearing a cost stop, or the fix would cost money or change a promise |
| A routine client email (scheduling, status, a billing question) | You | It would make a new commitment or change terms |
| A client asks for a change to their Operator | You route it ([Changing a Client's Operator](/admin/playbook/changing-a-clients-operator)); an agent does the work | It is a new kind of work that needs priced terms |
| An invoice at an agreed price | You send or present it ([Money](/admin/playbook/money)) | The amount does not match what was agreed |
| A prospect | The Captain (acquisition is founder-run, ADR 0060) | Always |
| Ending a client | The Captain decides; agents then run the offboarding ([Customer Lifecycle](/admin/playbook/customer-lifecycle)) | Always |

Live operations on a client's seat (rebuilding it, rotating a key, restarting it) are agent work. You do not need to run them or hand them to the Captain; ask an agent to do it and report back.

## Directing an agent

- **Give it a mission in one line.** A session starts by recording what it is for, and that line stays in front of the agent all session (`CLAUDE.md`, Session Mechanics). If you cannot say the mission in one line, the work is not ready to start.
- **Expect a short report that leads with the status.** A long reply opens with what the mission was, its status (OK, blocked, decision needed, or defect found), what was done and what is next (`CLAUDE.md`, reply contract). Bad news is never shortened.
- **Expect every fact to carry its source**: a command, a file, or the words "I'm inferring." A claim with no source should not be acted on (`CLAUDE.md`; doctrine Law 5).
- **"Done" means a client can do it.** A feature is done when someone performed the act on the system the client uses and saw it work, not when the code merged. A removal is done only when it is gone from every place it lived. Reports cite a check of the running system for each (`CLAUDE.md`, "Done means the client can do it" and "Gone means gone").
- **Agents finish the work in reach rather than filing it.** An agent files an issue only when the work truly cannot be done now (doctrine Law 13).
- **"Not now" stands until you lift it.** An agent stops before anything that costs money, touches a client or changes a promise, and brings it back ([Who Does What](/admin/playbook/who-does-what)).

## What is watched without anyone asking

- The fleet-alerts service checks every seat every two minutes and emails team@ when a seat's state changes (`workers/fleet-alerts/wrangler.toml`).
- The work-health check runs hourly at 23 minutes past the hour (`.github/workflows/work-health.yml`; [Work Health](/admin/playbook/work-health)).
- The ops triage agent investigates each new problem every hour and writes a report; it may not change code, deploy, restart anything or message a client (`operator/ops-machine/TRIAGE.md`).

What arrives from these, and when, is on [The Administrator's Week](/admin/playbook/administrators-week).

## Guardrails that do not bend

- Every change to the systems goes through a reviewed pull request; nothing is pushed straight to the live code, and a merge happens only when every required check passes ([Building the Platform](/admin/playbook/building-the-platform)).
- Client documents are opened only inside that client's ops workspace (doctrine Law 15).
- Client material lives only in the private `venturecrane/engagements` repository (Decision #52).
- No secret value is ever put in a message, a page or a chat (`CLAUDE.md`).

## Related pages

- [Who Does What](/admin/playbook/who-does-what): every position and every approval point.
- [Operating Cadence](/admin/playbook/operating-cadence): the rhythm of an agent's working session.
- [The Operator Thesis](/admin/playbook/operator-thesis): the same split, as the Operator offers it to a client.
