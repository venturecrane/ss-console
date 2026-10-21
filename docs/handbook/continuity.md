---
title: Continuity
section: running
order: 8
owner: captain
review_every: 30
last_reviewed: "2026-10-21"
summary: What happens when the Captain cannot be reached, what keeps running on its own, what waits, and which parts of SMD still depend on one person.
sources:
  - label: The ops machine (logins it runs on)
    href: docs/handbook/ops-machine.md
  - label: Disaster recovery
    href: docs/handbook/disaster-recovery.md
  - label: Fleet alerts worker
    href: workers/fleet-alerts/wrangler.toml
---

If the Captain cannot be reached, SMD keeps serving its clients but takes on nothing new. This page says what runs on its own, what waits for the Captain, and where SMD still depends on one person, with what has been decided about each.

## The standing posture

Decided by the Captain on 2026-10-21, until a successor is named:

- **The clients' Operators keep running**, doing exactly what each client authorized.
- **Agents keep fixing and reporting.** Problems are investigated and repaired as usual, and live operations on seats (rebuilds, restarts, key rotation) are agent work that needs no one's go-ahead. The hourly triage agent runs on its own; an agent that changes anything has to be started by a person, and today that needs the Captain's login (the dependency table below).
- **Nothing new goes to a client** until the Captain is back: no new price, offer, scope, commitment or contract, and no message SMD would normally have the Captain approve. Routine service continues: an administrator, if there is one, still sends status updates and invoices at prices already set ([Money](/admin/playbook/money)).

## What runs on its own

| Keeps running | Why it can |
|---|---|
| Each client's Operator and its scheduled work | It acts within the client's own authorization, which needs no SMD approval day to day |
| Monitoring: the 2-minute alerter, the hourly triage report, the nightly checks | They run on schedules and email team@; healthchecks.io pages if one of them stops |
| Monthly retainer billing | Stripe charges each client's chosen method on schedule |
| Deploys of website and console fixes already merged | Merging needs every check to pass, not a person's approval, and a merge deploys the website and consoles by itself (a change to a seat still needs an agent to rebuild it) |

## What waits for the Captain

Everything in the Captain's row of [Who Does What](/admin/playbook/who-does-what), in particular:

- a failed payment: the client keeps access, and the invoice simply stays overdue, because pausing and cancelling are the Captain's steps;
- clearing a seat that stopped itself on its cost limit;
- raising any routine to acting on its own, and the first boot of a new client;
- anything SMD itself would say to a client beyond routine service.

## Where SMD still depends on one person

| Dependency | What it means today | Decided remedy |
|---|---|---|
| The GitHub organization has one member | Only the Captain can grant anyone access to SMD's code and settings | When an administrator is named, they get their own membership |
| All of SMD's Fly.io apps (every client seat and the ops machines) are in the Captain's personal Fly account | No one else can see or act on a seat directly | Move the apps to an SMD-owned Fly organization when an administrator is named |
| AI agents, the ops machine and every client ops workspace run Claude on the Captain's personal login | No agent session can start without that login | The administrator gets their own seat on every vendor, Claude included |
| The GitHub organization, the Cloudflare account and the secrets vault are shared with Venture Crane, another venture, and a required merge check calls a Venture Crane service | A change on that side can stop SMD from merging or deploying | A written agreement that SMD keeps that access |

Who holds each account, its recovery method and how it transfers is private material for the companion ([Handoff Companion](/admin/playbook/handoff-companion)).

## Named successor

None is named yet. When one is, this page records who it is and what authority they hold while the Captain is away.

## When something breaks badly

For data loss, a leaked key or a vendor outage, see [Disaster Recovery](/admin/playbook/disaster-recovery). Two of those plans are not written yet: restoring the main database from backup, and what to do if a key leaks or a vendor goes down.
