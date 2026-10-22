---
title: When Something Goes Wrong
section: running
order: 7
owner: captain
review_every: 30
last_reviewed: "2026-10-21"
summary: From the alert to the fix to what the client hears, for each kind of problem, with who acts at each step.
sources:
  - label: Incident response ladder (ADR 0064)
    href: docs/adr/0064-operator-service-commitments.md
  - label: Fleet alert conditions
    href: workers/fleet-alerts/src/conditions.ts
  - label: Sticky-stop clear runbook
    href: docs/runbooks/operator/sticky-stop-clear.md
  - label: Routine promotion checklist (demotion after a SEV1)
    href: docs/runbooks/operator/enable-gate-checklist.md
---

Problems reach you as an email to team@, a GitHub issue, or a client telling you. Your job is to judge how serious it is, get an agent working on it, and make sure the client hears what they are owed when they are owed it; the [Incident Response](/admin/playbook/incident-response) page is the full procedure behind this summary.

## First, how serious is it

SMD's service commitment is a severity ladder, not an uptime percentage (ADR 0064):

| Severity | What it is | Work starts | The client hears |
|---|---|---|---|
| SEV1 | The Operator is down, or it acted outside what the client authorized | Immediately, any day | Within 24 hours, then at least daily until resolved |
| SEV2 | Degraded: a connection broken, skills failing, drafts not flowing, a limit tripped | Same business day | If they can see it, with updates as facts change |
| SEV3 | A question, something cosmetic, a configuration request | Next business day | In the same thread |

## Then, what to do

| What you see | What it means | First action | Who |
|---|---|---|---|
| `ALERT <seat>: Machine not heartbeating` | The seat is not reporting in; treat it as down (SEV1) | Ask an agent to find out why and restore it | You direct; an agent fixes |
| `ALERT <seat>: Seat stopped itself (HARD_STOP)` | A cost or safety limit stopped the seat on purpose | An agent investigates the cause before anything is cleared; nothing restarts it on its own | Cost stop: the Captain clears it. Otherwise you, after the investigation |
| `Scheduled work not firing`, `Cron scheduler broken`, an issue about routines with no ending | The Operator is not doing its scheduled work | Ask an agent to check the seat's scheduler and the last runs | You direct; an agent fixes |
| `Connector failing`, `ACTION: ... sign-in is dead`, `credential expiring` | A connection to the client's systems is broken or about to break | If the client must sign in again, email them; otherwise an agent fixes our side | You |
| `SHORTFALL`, `NO REPLY`, a `failed` or `held` job | A client asked for something and did not get it | Make sure it is answered or redone; an agent finds out why | You |
| A client says the Operator did something it should not have | Possibly acting outside authorization (SEV1) | Pause the seat, then lower the routines involved to flag-only; an agent investigates | You decide and tell the Captain; an agent pauses |
| `Audit chain head missing`, `Unaudited send detected`, `Audit-log integrity violation` | The record of what the Operator did may be incomplete | An agent investigates the same day | You direct; the Captain decides what the client is told |
| `Seat went back to an older image`, `matches no fleet release`, `customer.yaml other than the published one`, `skills version other than its pointer` | The seat is not running what was authored for it, so the client may be getting different behavior from what was set up | An agent finds out why the seat diverged and brings it back; the alert says what differs | You direct; an agent fixes |
| `Web edge not answering` | The website and portal are down from outside | An agent investigates at once | You direct; an agent fixes |
| A healthchecks.io failure notice | A watcher went silent, so other alerts may be missing | An agent restores the watcher first | You direct; an agent fixes |

## What the client hears

Status updates within the ladder above are routine client emails, and you may send them. Anything beyond a status update is the Captain's: an apology that offers something, a credit, or a change to what the Operator does going forward, because each is a new commitment. When in doubt, draft it and send it to the Captain first.

## After it is fixed

An agent writes a short incident note in `docs/runbooks/operator/incidents/` and the fix lands as a reviewed change with proof. After a SEV1, the routines involved stay at flag-only until the root cause is fixed and the incident has been rehearsed against the fix (the demotion rule in [Incident Response](/admin/playbook/incident-response)). For data loss, a leaked key or a vendor outage, see [Disaster Recovery](/admin/playbook/disaster-recovery) and [Continuity](/admin/playbook/continuity).
