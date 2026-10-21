---
title: Changing a Client's Operator
section: running
order: 6
owner: captain
review_every: 30
last_reviewed: "2026-10-21"
summary: How a client asks for a change, who approves it, how it is made, how you confirm it landed, and how to pause or stop a seat.
sources:
  - label: Portal change requests
    href: src/lib/portal/operator/change-request.ts
  - label: Admin change-request inbox
    href: src/pages/admin/operator/requests.astro
  - label: Routine lifecycle runbook
    href: docs/runbooks/operator/routine-lifecycle.md
  - label: Lane-proof runbook
    href: docs/runbooks/operator/lane-proof.md
  - label: Config publish on merge
    href: scripts/ci-publish-customer-configs.sh
  - label: Pause script
    href: operator/bin/pause-customer.sh
  - label: Sticky-stop clear runbook
    href: docs/runbooks/operator/sticky-stop-clear.md
  - label: Decommission script
    href: operator/bin/decommission-customer.sh
---

A client's Operator does exactly what its configuration says, and that configuration is a reviewed file in the repository, not a setting someone flips. Some changes the client makes themselves in their portal; everything else comes to SMD, is approved by the right person, and is made by an agent through a pull request.

## 1. How a change is asked for

| The client... | What happens |
|---|---|
| Clicks **Request a change** in their portal | It is recorded and emailed to team@ ("Operator change request"). It appears in the admin inbox at `/admin/operator/requests`. |
| Emails their Operator with a request | The Operator answers it if it is allowed to, and a request card tells team@ what happened. |
| Asks for a kind of job the seat has never done | The job waits, and team@ gets "NEW KIND OF ASK". An agent runs a proof; you read the result and accept it or decline. The Operator cannot approve its own new kinds of work. |
| Wants a routine itself changed (what it does, its limits) | That is a change to the agreement's list of routines, made in writing with the client. Work the agreement does not cover is new work, scoped and priced by the Captain. |

## 2. What the client changes alone

In the portal, the client's principal can, without SMD:

- move a routine's level up or down, but never above the ceiling the agreement sets; it applies right away and is logged;
- pause and resume their Operator, where the agreement gives them that control;
- add and remove the people who can use the portal;
- teach the Operator their firm's voice and output style by instructing it directly, if they are listed as one of its administrators.

A routine's level is one of: flag-only (it reads and tells someone), prepare-and-route (it drafts for a person to send), and auto-handle (it acts on its own). Anything touching deadlines or money, or sending to opposing counsel or a court, never auto-handles (ADR 0075).

## 3. Who approves a change that comes to SMD

| Change | Approves |
|---|---|
| Fixing something so it works as already agreed | You |
| A new kind of job the agreement already covers | You, after the proof run |
| Raising a routine to acting on its own | The Captain |
| Anything that adds scope, changes price or terms, or commits SMD to something new | The Captain |

Record the decision on the request in `/admin/operator/requests`: acknowledge, resolve ("SMD made the change") or decline. The resolver's name and time are stamped on it.

## 4. How the change is made

You ask an agent; the agent edits the client's configuration file (`operator/customers/<slug>/customer.yaml`), opens a pull request, and merges it when the checks pass. What happens next depends on what changed:

- **Some settings apply within about 15 seconds of the merge**, with no restart: routine levels and permissions, who may email the Operator, the Operator's administrators, escalation contacts, and similar. The exact list is the overlay's `config_applier/safety.py`; if a change mixes live and non-live settings, none of it applies live.
- **Everything else needs a rebuild of the seat**: which systems it connects to, the AI model, memory, the seat's role, people and spaces, and more. The agent rebuilds the seat and reports the result; it first checks that no job is running or held.
- **A new or changed skill** goes out as a skills release, with no restart. **A change to the Operator's code** goes out as a release, ring by ring across the fleet ([Deployment & Release](/admin/playbook/deployment-release)).

## 5. Confirming it landed

Ask the agent for proof that it landed, not just that it merged. It can show the configuration-change record on the seat, the change in the seat's config history (`/admin/operator/config-history/<slug>`), or a live read from the seat. A nightly check also compares what is running with what was approved and opens an issue when they differ.

## 6. Pausing and stopping

| Need | How | Who |
|---|---|---|
| Pause a seat (it stops working but stays reachable for diagnosis) | An agent runs `operator/bin/pause-customer.sh <slug> --reason "..."`, and `--resume` to undo | You decide; an agent does it |
| After any serious incident | Pause first, then lower the routines involved to flag-only ([promotion checklist](https://github.com/venturecrane/ss-console/blob/main/docs/runbooks/operator/enable-gate-checklist.md)) | You decide; an agent does it |
| A seat stopped itself (cost or safety limit) | Investigate first, then clear it with the **Clear stop** form in the admin console, with a reason. Nothing resumes it on its own | Cost stop: the Captain. Other stops: you, after the investigation ([sticky-stop runbook](https://github.com/venturecrane/ss-console/blob/main/docs/runbooks/operator/sticky-stop-clear.md)) |
| Cut a client's Claude-connector access immediately | Revoke the grant; it takes effect on the next call, and only an admin can issue it again (ADR 0057) | You |
| End a client's seat for good | `operator/bin/decommission-customer.sh`, which rehearses by default and asks for the name twice before it destroys anything | The Captain decides; an agent runs it ([Customer Lifecycle](/admin/playbook/customer-lifecycle), section 11) |
