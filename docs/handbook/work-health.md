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

**Since 2026-10-16 the list is worked before anyone reads it.** The triage agent on the ops machine ([The Ops Machine](/admin/playbook/ops-machine)) runs this evaluation hourly at :47 and emails team@ one report: handled, needs you, fixes to approve. The GitHub Actions run below no longer sends the list; it still sends the ACTION pages (a dead sign-in), so those arrive even if the ops machine is down. If the triage job stops running, the healthchecks.io check `smd-ops-triage` emails after 90 minutes of silence. What follows describes the evaluation both runs share.

- **Hourly, only when something on a client seat needs attention.** Subject: `[SMD Ops] Work health: N need attention (M new)`. The first line says the same in a sentence; then each client seat (A&P first) with its items.
- **NEW** lines carry a "To do". Items already reported in an earlier email are one line each with their age, "(open 2d)", so an unchanged problem never re-sends at full volume. Times are Phoenix time, marked MST.
- **One line per problem, never per occurrence.** A send refused every few seconds for a day is one line with the attempt count, first and last time. Repeats of one routine or one tool are one line with a count. A job that failed is one line, not one per signal about it.
- **Test seats are a count, not a list.** Seats whose `customer.yaml` says `seat.kind` is not `customer` (pilot-smokeball, smd-staging, scott) are counted on one "Test seats, not listed" line. A seat that could not be read, or a missed work-health run, is listed wherever it happens, because it usually means a pipeline every seat shares is broken. An hour with only test-seat items sends nothing; they ride on the daily all-clear.
- **Once a day**, on the first run at or after 15:00Z (08:00 Phoenix), a run with nothing listed still sends `[SMD Ops] Work health: all clear`. If neither email arrives in a day, the check itself is broken: look at the `work-health` workflow runs.
- **Open work only.** A refusal or failure followed by delivered work is history: it is counted on one "Answered by later work" line, never listed. A job that delivered while its lane flagged something of its own (a held offer on negotiation) is not "undelivered"; the lane's own alert covers the flag.
- **A seat that could not be read is an item**, never silence. "Could not read this seat's work ledger" means nothing about that seat is known.
- **ACTION pages.** A dead sign-in (Smokeball's token endpoint explicitly REFUSING the seat's token, SMD's mailbox sign-in check failing, a connection failing repeatedly) also gets its OWN email, subject `ACTION: <seat> Smokeball sign-in is dead` (or `mailbox sign-in is dead`), on every run while it stays dead. A Smokeball token that was renewed but not saved on the seat pages the same way, subject `ACTION: <seat> Smokeball sign-in dies at the next restart`: sign-in works until the seat restarts, then fails. The hourly Smokeball check is the only detector of a dead Smokeball token: the connector-health ledger never sees the scheduled jobs' Smokeball calls (PR #3144). A Smokeball seat whose hourly check is not scheduled, or a check that could not run (its own fault, never "dead"), is an ACTION line in the digest, not a page.
- **Not checked** is one line naming what the run could not vouch for (a seat that has never reported a heartbeat, which is being built rather than broken; a hosted agent, which gets machine checks only).

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
| Scheduled run has not run since T: N missed | Slots of a schedule the seat was PROVISIONED with (D1 `customer_configs`, not git) passed with no run and no recorded skip. | Check the seat's scheduler. |
| Schedule drift | git authors a different schedule than the seat runs; that routine is not graded until a rebuild makes them agree. | Rebuild the seat, or revert the authoring. |
| Run ended with nothing delivered / held and nobody told | A scheduled or held run ended silently (the terminal-state reconciler's finding), with a count when it repeats. | Check whether the work it owed was done. |
| A message the Operator tried to send on its own was blocked by the safety check | An out-of-turn send (an outcome letter, an alert) was refused by the outbound scan, N times. Nobody it was for has heard. | Find what it was sending (the seat's `agent.log` names it) and tell that person by hand. |
| A call was refused / failed / hit a limit | Refused, limit, failed or partial tool results in the last 24 hours, one line per tool and reason with a count. | If a person was waiting, check they got an answer. |
| New Operator error | A Sentry error reported in the last 24 hours. | Triage it in Sentry. |

## What the email never carries

Only what team@ alert mail already carries: lanes, matter numbers, a reason's first word, times, card ids. Never a sender, a subject line or a document name. The run's GitHub Actions log prints counts only, and its saved state holds hashes only, because ss-console is a public repository.

## Closing a held or failed job

The seat's ledgers have no cancel state. When a held or failed job needs no rerun, release or note (for example SMD's own test request), add it to `operator/contracts/work-health-closed.yaml` with a reason in a PR. It changes nothing on the seat; it stops the email asking for action. The repository is public: the job id and a reason that names no client.

## Changing it

- **The interval** is the `cron:` line of `.github/workflows/work-health.yml` and nothing else.
- **Lane limits, windows and the all-clear hour** are in `operator/contracts/work-health.yaml`.
- **The Smokeball sign-in check** runs on a seat when its `customer.yaml` authors a `connector-auth-check` cron row with `pre_run: pre_run.py`; it takes effect at that seat's next rebuild.
