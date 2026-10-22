---
title: The Administrator's Week
section: running
order: 2
owner: captain
review_every: 30
last_reviewed: "2026-10-21"
summary: Everything that arrives in the team inbox or the GitHub issue list, what each means, who acts, and what a quiet day really looks like.
sources:
  - label: Fleet alerts worker (2-minute and Monday crons)
    href: workers/fleet-alerts/wrangler.toml
  - label: Work-health workflow (hourly ACTION pages)
    href: .github/workflows/work-health.yml
  - label: Ops triage agent orders
    href: operator/ops-machine/TRIAGE.md
  - label: Ops machine crontab
    href: operator/ops-machine/crontab
  - label: Request cards (NO REPLY, NEW KIND OF ASK)
    href: src/lib/operator/request-card.ts
  - label: Retainer payment webhook
    href: src/lib/webhooks/stripe-subscription-handler.ts
  - label: Cost anomaly worker
    href: workers/cost-anomaly/wrangler.toml
---

Almost everything SMD's systems want you to know arrives as an email to **team@smd.services**, from "SMD Services Ops". The rest arrives as a GitHub issue that no email announces, which is why reading the issue list is part of the routine.

## What a quiet day looks like

There is **no daily "all clear" email**. On a quiet day the team inbox is simply empty. An empty inbox is trustworthy only because the watchers are themselves watched: if the 2-minute alerter, the hourly triage agent, or a client's ops workspace stops running, healthchecks.io emails a failure notice. So a quiet day is an empty inbox **and** no healthchecks.io notice **and** no new open P0 or P1 issue.

## The daily routine

1. **Read the team inbox.** For each email, the table below says what it means and who acts.
2. **Read the open P0 and P1 issues** at [github.com/venturecrane/ss-console/issues](https://github.com/venturecrane/ss-console/issues?q=is%3Aopen+label%3Aprio%3AP0%2Cprio%3AP1). The nightly safety checks report here, and all but one of them nowhere else (listed below). If an issue is new, ask an AI agent to investigate it and report back in plain words.
3. **Act on the triage report's "NEEDS YOU" items.** Decide what is yours under [Who Does What](/admin/playbook/who-does-what); forward the rest to the Captain with the triage agent's own options and pick.
4. **Check for anything a client is waiting on:** a NO REPLY card, a held job, or a sign-in that is about to expire.

## Weekly

- **Monday morning:** "Weekly: what the Operator refused". Each refusal is either a scope conversation to have with the client or a setting SMD got wrong. An empty week sends nothing.
- **Monday:** a "Hermes upstream ... available" issue when the Operator's runtime has a new release. Upgrading is a deliberate, staged release with its own plan (the Hermes upgrade plans in [Disaster Recovery](/admin/playbook/disaster-recovery)); ask an agent whether this one matters and what it would take.
- **Monday:** the "Handbook: pages due for review" issue, when it is open. It lists pages whose sources changed after they were last checked, and pages past their review window; ask an agent to re-check each against its sources.
- **Once a week, ask an agent to read you the obligation register** ([Clients & Commitments](/admin/playbook/clients-and-commitments)), so nothing we owe goes stale without you noticing.

## What arrives in the inbox

"You" means the administrator. "An agent" means you ask an AI agent to do it and report back.

| Email subject starts with | When | What it means | Who acts |
|---|---|---|---|
| `[SMD Ops] ALERT <seat>: ...` then `Re: ...` or `RECOVERED` | Checked every 2 minutes; one email when a problem starts, one when it clears | A seat stopped heartbeating, stopped itself, stopped running scheduled work, a connector is failing, or the website is down from outside | You: ask an agent to investigate and fix; see [When Something Goes Wrong](/admin/playbook/when-something-goes-wrong) |
| `ALERT <seat>: Connector credential expiring`, then daily `STILL OPEN` | Before a client sign-in expires | The client must sign in again or that work stops | You: email the client to re-consent |
| `ACTION: <seat> ... sign-in is dead` | Hourly, repeating until fixed | A sign-in already failed; that seat's work in that system has stopped | You: get the client to sign in again; an agent fixes anything on our side |
| `[SMD Ops] Triage: N handled, M need you` | Hourly, only when something new happened | The triage agent already investigated each item. It cannot change anything itself | You: decide the "NEEDS YOU" items that are yours; the Captain approves "FIXES TO APPROVE" |
| `[SMD Ops] SHORTFALL <seat>: ...` | At most every 30 minutes per seat | The Operator could not give a client what they asked for | You: learn of it before the client complains; an agent finds out why |
| `[SMD Ops] <seat> NO REPLY after N m: ...` | 30 minutes after an unanswered client request | A client is waiting | You: make sure it is answered |
| `[SMD Ops] <seat> NEW KIND OF ASK, job waiting: ...` | When a client asks for a job that seat has never done | The job is held until we prove the seat does it right | An agent runs the proof; you read the result and accept it or say no ([lane-proof runbook](https://github.com/venturecrane/ss-console/blob/main/docs/runbooks/operator/lane-proof.md)) |
| `... job delivered / failed / held`, `... first reply in N m` | Every request and job | A trail of the Operator's work | You: only failed and held need action |
| `[SMD Ops] <seat> send refused: ...` | When a safety gate stopped an outgoing email | A routine could not reach a person, on purpose | An agent investigates; you decide whether the client needs telling |
| `ALERT <seat>: Sentry issue alert` / `healthchecks.io grace expired` / `Audit-log integrity violation` / `obligation alert` | When it happens | An error, a stopped watcher, a tampered record, or overdue client work | You: ask an agent to investigate and report |
| `Retainer payment failed - <client>` | When a monthly charge fails | No automatic action was taken | The Captain: the steps that follow are in [Money](/admin/playbook/money) |
| `Hosted Agent purchase ...`, `Stripe webhook: unreadable ...` | On a purchase or a billing record that could not be matched | A new self-serve customer, or billing data to reconcile by hand | You |
| `New inquiry`, `Contact form`, `New booking`, `Rescheduled`, `Cancelled`, `Operator change request`, `Hosted Agent intake submitted` | When someone acts on the website or portal | Business, not alarms | You: reply, schedule, or route a change request ([Changing a Client's Operator](/admin/playbook/changing-a-clients-operator)) |
| `[SMD] Booking system alert` | When calendar sync or booking email breaks | Prospects may not be able to book | You: ask an agent |
| A healthchecks.io failure notice | When a watcher stops | One of the watchers above went silent; until fixed, an empty inbox means nothing | You: ask an agent at once |

The daily cost-anomaly digest goes to the Captain's own inbox, not team@.

## What reports only as a GitHub issue

These nightly checks open or update a `prio:P0` or `prio:P1` issue and send no email (the obligation check is the one exception; it also emails).

| Issue title | What it means |
|---|---|
| Audit chain head missing from a seat export | A seat's audit record may have been cut or rewritten (P0, the most serious kind) |
| Runtime control probe suite exit ... | A safety control stopped refusing what it should refuse, or could not be tested |
| Routine runs with no terminal state | A scheduled routine ended without delivering, refusing or escalating |
| Cron slots with neither wake row | A scheduled slot passed and the seat recorded nothing |
| Obligation register: overdue or unverifiable work | Work we owe a client is late or cannot be proven done |
| customer_configs projection drift / R2 seat config drift | What is running differs from what was approved |
| Unaudited send detected / AgentMail lists suppressing rostered recipients | An email left a seat without an audit record, or a list is blocking someone it should reach |

Every scheduled job, wherever it runs, is listed on [Scheduled Jobs](/admin/playbook/scheduled-jobs).

When one of these checks cannot run at all, its GitHub Actions run turns red but opens no issue. An agent reviewing the Actions page once a day catches that; ask for it as part of the daily issue read.
