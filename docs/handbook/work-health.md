---
title: Work Health
section: operations
order: 11
summary: The hourly email to team@ about the state of the WORK on every live seat - requests with no reply, stuck or held jobs, missed and silent scheduled runs, failing connections, new errors - what each line means and what to do with it.
sources:
  - label: operator/bin/work-health.py - the run
    href: https://github.com/venturecrane/ss-console/blob/main/operator/bin/work-health.py
  - label: operator/contracts/work-health.yaml - the rules (lane limits, windows, the all-clear hour)
    href: https://github.com/venturecrane/ss-console/blob/main/operator/contracts/work-health.yaml
  - label: .github/workflows/work-health.yml - the schedule (the interval knob)
    href: https://github.com/venturecrane/ss-console/blob/main/.github/workflows/work-health.yml
  - label: operator/skills/connector-auth-check/pre_run.py - the hourly Smokeball sign-in check
    href: https://github.com/venturecrane/ss-console/blob/main/operator/skills/connector-auth-check/pre_run.py
---

## Why it exists

Machine health has always paged within minutes: a seat that stops reporting, a connection that fails three calls in a row, a new Sentry error. The work itself was not watched. On 2026-10-13 the alarm inventory found that a job stuck in "running" looked like work in progress forever, that the seat's own NO REPLY card switches off the moment any job exists and had never fired in production, that the daily reconcilers' findings (silent runs, missed scheduled runs) lived only in GitHub issues nobody read, and that A&P's Smokeball authorization died on a Saturday and nothing said so for 42 hours. The Captain's decision: one email to team@ about the state of the work on every seat, hourly for now.

## What arrives, and when

- **Hourly, only when there is something to act on.** Subject: `[SMD Ops] Work health: N new, M still open (ashton-price 3, ...)`. A&P first, then every other live seat.
- **NEW** lines carry the full text and a "To do". **OPEN Nh** lines are items already reported in an earlier email, one line each with their age, so an unchanged problem never re-sends at full volume.
- **Once a day**, on the first run at or after 15:00Z (08:00 Phoenix), a run with nothing to report still sends `[SMD Ops] Work health: all clear`. If neither email arrives in a day, the check itself is broken: look at the `work-health` workflow runs.
- **A seat that could not be read is an item**, never silence. "Could not read this seat's work ledger" means nothing about that seat is known.
- **Not checked this run** lists what the run could not vouch for (today: Smokeball sign-in on seats where the hourly check has not been deployed yet).

## What each line means

| Line | What went wrong | What to do |
| --- | --- | --- |
| NO reply | A firm person's request has had no reply for 30 minutes or more. A job starting does not excuse it: the acknowledgement is owed either way. | Find the request by its card id (the team@ request card's `Card:` line starts with it, or `.claude/bin/requests`) and answer it or find out why the Operator did not. |
| Reply held | The Operator wrote a reply and a gate held it; nothing was sent. | Read the held reply; release or rewrite it. |
| Job queued / running too long | A job has not started, or has run past its lane's limit (drafting 2h, demand 6h, chronology 12h, litigation 3h, negotiation 1h). | Check the job on the seat; a hung runner looks like progress forever. |
| Job held / failed | A job ended without delivering. The reason's first word is shown (`review_held`, `request_incomplete`); the rest stays on the seat. Drops off after 72 hours, or as soon as a later job in the same lane on the same matter delivers. | Decide: rerun, release, or tell the requester why. |
| Smokeball refusing sign-in | The hourly sign-in check, the sign-in tool, or a job failed on authorization, and nothing has succeeded since. | Re-consent the seat's Smokeball connection. |
| Connection failing / mailbox not read | The seat's own counters show repeated failures, or the mail poller has not succeeded for 15 minutes. | Check the credentials and the vendor. |
| Open alert | A pager condition opened earlier and has not closed. | Work the alert email's runbook. |
| Scheduled run never ran | An authored schedule slot passed with no run and no recorded skip (the cron-slot watchdog's finding). | Check the seat's scheduler. |
| Run ended with no outcome / held and nobody told | A scheduled or held run ended silently (the terminal-state reconciler's finding). | Check whether the work it owed was done. |
| A call was refused / failed / hit a limit | Refused, limit, failed or partial tool results in the last 24 hours, one line per tool and reason with a count. | If a person was waiting, check they got an answer. |
| New Operator error | A Sentry error reported in the last 24 hours. | Triage it in Sentry. |

## What the email never carries

Only what team@ alert mail already carries: lanes, matter numbers, a reason's first word, times, card ids. Never a sender, a subject line or a document name. The run's GitHub Actions log prints counts only, and its saved state holds hashes only, because ss-console is a public repository.

## Changing it

- **The interval** is the `cron:` line of `.github/workflows/work-health.yml` and nothing else.
- **Lane limits, windows and the all-clear hour** are in `operator/contracts/work-health.yaml`.
- **The Smokeball sign-in check** runs on a seat when its `customer.yaml` authors a `connector-auth-check` cron row with `pre_run: pre_run.py`; it takes effect at that seat's next rebuild.
