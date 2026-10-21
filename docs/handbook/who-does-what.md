---
title: Who Does What
section: running
order: 5
owner: captain
review_every: 30
last_reviewed: "2026-10-21"
summary: Every position at SMD, human and AI, with what it owns, what it may do alone, what it must escalate and how to stop it; then every point where a person must approve.
sources:
  - label: Agent operating doctrine
    href: docs/doctrine/agent-operating-doctrine.md
  - label: Ops triage agent orders
    href: operator/ops-machine/TRIAGE.md
  - label: Routine promotion checklist
    href: docs/runbooks/operator/enable-gate-checklist.md
  - label: Staged send runbook
    href: docs/runbooks/operator/staged-send.md
  - label: ADR 0089 staff send-as on approval
    href: docs/adr/0089-staff-send-as-on-approval.md
  - label: ADR 0071 confirm ceiling
    href: docs/adr/0071-confirm-ceiling-and-hosted-agent-tier-ladder.md
  - label: Portal entitlement dial
    href: src/pages/api/portal/products/operator/[instance]/entitlement.ts
---

SMD runs on a small number of people and a larger number of AI agents, and each has a defined job. The rule underneath all of it: agents do the work, the Captain owns strategy, commitments and spending, and each client decides how far its own Operator may go.

## Positions

| Position | Owns, and may do alone | Must escalate | How to stop it |
|---|---|---|---|
| **The Captain** (founder) | Strategy, pricing, contract terms, new commitments to a client, anything that spends SMD money, taking on or ending a client | Nothing; the Captain is the last stop | |
| **The Administrator** (you) | Running the daily routine; directing agents; sending routine client emails; sending or presenting invoices at prices already set; routing client change requests; accepting a proven new kind of job | Everything in the Captain's row, and anything you are unsure is yours | |
| **The client's Named Administrator and staff** | How far their Operator may go: changing a routine's level within the agreed ceiling, pausing their Operator where the agreement gives them that control, adding and removing portal users, teaching the Operator their firm's voice. Staff approve emails sent in their own name | Anything else goes to SMD as a change request | Their portal access is revoked in the portal; a Claude-connector grant is revoked by SMD |
| **Each client's Operator** (the seat) | The client's work, within exactly what the client authorized; anything not authorized is refused | A kind of job it has never done waits until SMD proves it | Pause the seat (an agent runs `operator/bin/pause-customer.sh`); it also stops itself when it hits a cost or safety limit |
| **The ops triage agent** (hourly, on SMD's ops machine) | Investigating every new problem and writing the hourly report | It may not change code, deploy, restart anything or message a client; every fix waits for approval | Ask an agent to stop it; its silence pages through healthchecks.io |
| **Client ops workspaces** (one per client) | The only place a client's documents are opened and worked on | Opening client documents on a laptop is a break-glass act and is recorded | An agent can destroy a workspace; a daily sweep checks each one |
| **Build agents** (Claude Code sessions) | Changing the systems through reviewed pull requests, merging when every check passes, and live operations on seats (rebuilds, key rotation, restarts), reporting each with proof | Anything that costs money, touches a client or changes a promise | Tell it "not now"; that stands until lifted |
| **Nightly checks** (GitHub workflows) | Detecting problems and reporting them | They only report; they never fix | |

## Every point where a person must approve

| What | Who approves | How |
|---|---|---|
| A new price, discount, credit, refund or payment term | The Captain | Before anything is sent |
| An invoice or billing email at an already-set price | You | Send or present in the console ([Money](/admin/playbook/money)) |
| Words SMD has the Operator send for us (a staged send or an SMD note) | The Captain approves the exact words, recipient and files | [Staged send runbook](https://github.com/venturecrane/ss-console/blob/main/docs/runbooks/operator/staged-send.md) |
| An email sent in a client staff member's name | That staff member, by replying to the draft | An administrator may cancel such a draft, never send it as someone else (ADR 0089) |
| An outside email on a routine set to `confirm` | A person at the client, saying yes in the same conversation | ADR 0071 |
| An outside email on a routine set to draft-for-review | A person at the client, who sends it themselves | ADR 0071 |
| Raising a routine to drafting under review | A reviewer's dated observations | [Promotion checklist](https://github.com/venturecrane/ss-console/blob/main/docs/runbooks/operator/enable-gate-checklist.md) |
| Raising a routine to acting on its own | The Captain, signed and dated | Promotion checklist |
| A new kind of job on a seat | You, after reading the agent's proof run | [Lane-proof runbook](https://github.com/venturecrane/ss-console/blob/main/docs/runbooks/operator/lane-proof.md) |
| First boot of a new client's seat | The Captain | [First-boot runbook](https://github.com/venturecrane/ss-console/blob/main/docs/runbooks/operator/first-boot.md) |
| Clearing a seat that stopped itself | The Captain for a cost stop; otherwise a signed-in admin with a written reason, after investigating | [Sticky-stop runbook](https://github.com/venturecrane/ss-console/blob/main/docs/runbooks/operator/sticky-stop-clear.md) |
| Merging a pull request | Nobody: every required check passing is the gate | [Building the Platform](/admin/playbook/building-the-platform) |

Three things no one may do, whatever the reason: open a client's documents outside that client's ops workspace, send a client anything nobody with the authority above has approved, and put a secret value in a message, a page or a chat.
