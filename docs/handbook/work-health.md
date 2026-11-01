---
title: Work Health
section: running
order: 21
owner: captain
review_every: 30
last_reviewed: "2026-10-21"
summary: The hourly check of the WORK on every live seat (requests with no reply, stuck or held jobs, missed and silent scheduled runs, failing connections, new errors), how it reaches you as the triage report and ACTION pages, and what each line means.
sources:
  - label: operator/bin/work-health.py - the evaluation
    href: https://github.com/venturecrane/ss-console/blob/main/operator/bin/work-health.py
  - label: operator/contracts/work-health.yaml - the rules (lane limits, windows, the all-clear hour)
    href: https://github.com/venturecrane/ss-console/blob/main/operator/contracts/work-health.yaml
  - label: .github/workflows/work-health.yml - the ACTION-page backstop (pages only since 2026-10-16)
    href: https://github.com/venturecrane/ss-console/blob/main/.github/workflows/work-health.yml
  - label: operator/ops-machine/bin/ops-triage - the hourly triage run on smd-ops
    href: https://github.com/venturecrane/ss-console/blob/main/operator/ops-machine/bin/ops-triage
  - label: operator/ops-machine/TRIAGE.md - the triage agent's standing orders
    href: https://github.com/venturecrane/ss-console/blob/main/operator/ops-machine/TRIAGE.md
  - label: operator/skills/connector-auth-check/pre_run.py - the hourly Smokeball sign-in check
    href: https://github.com/venturecrane/ss-console/blob/main/operator/skills/connector-auth-check/pre_run.py
  - label: workers/fleet-alerts/src/selftest.ts - the seat self-test page
    href: https://github.com/venturecrane/ss-console/blob/main/workers/fleet-alerts/src/selftest.ts
---

Every hour, a check reads every live seat and lists the work that needs a person: a request nobody answered, a job stuck or held, a scheduled run that did not happen, a broken sign-in. You do not read that list raw; an AI agent works it first and emails you only what it found and what is yours to decide, and a dead sign-in emails you directly.

## Why it exists

Machine health has always paged within minutes: a seat that stops reporting, a connection that fails three calls in a row, a new Sentry error. The work itself was not watched. On 2026-10-13 the alarm inventory found that a job stuck in "running" looked like work in progress forever, that the seat's own NO REPLY card switches off the moment any job exists and had never fired in production, that the daily reconcilers' findings (silent runs, missed scheduled runs) lived only in GitHub issues nobody read, and that A&P's Smokeball authorization died on a Saturday and nothing said so for 42 hours. The Captain's decision: one hourly check of the state of the work on every seat (`.github/workflows/work-health.yml` header).

## How it reaches you

Two separate runs share one evaluation (`operator/bin/work-health.py`):

| Run | Where | What it sends |
|---|---|---|
| **The triage agent** | `smd-ops`, hourly at :47 Phoenix time (`operator/ops-machine/crontab`) | Only when the evaluation has something NEW: the agent investigates each new item with read-only tools and emails team@ one report, `[SMD Ops] Triage: <H> handled, <N> need you`, in three parts: NEEDS YOU, HANDLED, FIXES TO APPROVE (`operator/ops-machine/TRIAGE.md`). A quiet hour sends nothing. |
| **The page backstop** | GitHub Actions, hourly at :23 (`.github/workflows/work-health.yml`) | Since 2026-10-16 (Captain) it runs with `--pages-only`: it sends only the ACTION pages below, never the list, so a dead sign-in still emails you even if the ops machine is down. |

The triage agent may not change code, deploy, rebuild or restart a seat, or message a client: every fix it proposes waits under FIXES TO APPROVE ([Who Does What](/admin/playbook/who-does-what)). If it stops running, the healthchecks.io check `smd-ops-triage` (hourly, 30 minutes grace, `operator/ops-machine/bin/ops-triage`) emails a failure notice; until that is fixed, an empty inbox means nothing.

What you do with the report is in [The Administrator's Week](/admin/playbook/administrators-week): decide the NEEDS YOU items that are yours, and bring the Captain the rest and the fixes to approve.

## How the list is built

The list below is what the evaluation produces and what the triage agent reads. You see it only through the agent's report, or if you ask an agent to run the evaluation for you.

- **Subject:** `[SMD Ops] Work health: N need attention (M new)`. Then each client seat (A&P first) with its items. Times are Phoenix time, marked MST.
- **NEW** lines carry a "To do". Items already reported are one line each with their age, "(open 2d)", so an unchanged problem never repeats at full volume.
- **One line per problem, never per occurrence.** A send refused every few seconds for a day is one line with the attempt count, first and last time. A job that failed is one line, not one per signal about it.
- **Test seats are a count, not a list.** Seats whose `customer.yaml` says `seat.kind` is not `customer` (`pilot-smokeball`, `smd-staging`, `scott`) are counted on one "Test seats, not listed" line. A seat that could not be read, or a missed hourly run, is listed wherever it happens, because it usually means something every seat shares is broken.
- **Open work only.** A refusal or failure followed by delivered work is counted on one "Answered by later work" line, never listed.
- **A seat that could not be read is an item**, never silence: "Could not read this seat's work ledger" means nothing about that seat is known.
- **Not checked** names what the run could not vouch for: a seat that has never reported a heartbeat (being built, not broken). Every other seat gets the same checks, the self-serve offering included (ADR 0100 W6, 2026-10-31); each check grades only what the seat authors, so a seat with no Smokeball connector, no Graph mailbox or no schedule is never graded on one.
- **The daily all-clear** (`[SMD Ops] Work health: all clear` at 08:00 Phoenix) still exists in the evaluation but has not been emailed since 2026-10-16: neither run sends the list or the all-clear. A silent triage agent is caught by its healthchecks.io check instead.

## ACTION pages

A dead sign-in gets its own email on every hourly run while it stays dead, from the page backstop:

| Subject | What it means | Who acts |
|---|---|---|
| `ACTION: <seat> Smokeball sign-in is dead` | Smokeball refused the seat's token; that seat's Smokeball work has stopped | You: get the client to sign in again; an agent fixes anything on SMD's side |
| `ACTION: <seat> Smokeball sign-in dies at the next restart` | A renewed token was not saved on the seat: it works until the seat restarts, then every Smokeball call fails | An agent fixes the saving before the seat restarts; the fix may still need the client to sign in again, which is yours to arrange |
| `ACTION: <seat> mailbox sign-in is dead` | SMD's hourly sign-in check of the seat's mailbox failed; if the seat's own connection is healthy, SMD's copy of the secret is stale | An agent checks the mailbox's app credentials |
| `ACTION: <seat> <connection> connection is failing` | A connection failed twice in a row, or the mail reader has not succeeded for 15 minutes | An agent checks the credentials and the vendor |

### The seat self-test page

Separately from this list, every seat checks ITSELF every hour: the image runs the same check list the release verify runs (`operator/templates/seat-checks.sh`, by `operator/templates/selftest.sh`) and reports the result on its heartbeat. When it finds a failed check, the fleet-alerts worker emails `[SMD Ops] ALERT <seat>: Seat self-test failed a check` (condition `selftest_failing`, `workers/fleet-alerts/src/selftest.ts`), naming the failed checks. Each seat also checks the signed spec its configuration is published as: when the seat cannot verify it, or is still holding an older one 15 minutes after a newer one was published, the worker emails `[SMD Ops] ALERT <seat>: Seat cannot verify its signed config spec` (condition `spec_unverified`, `workers/fleet-alerts/src/spec.ts`). While this runs in shadow mode the seat keeps applying its configuration as before, so the email is about the check, not about a seat that stopped.

| When it opens | When it closes |
|---|---|
| A boot run with a failed check: on the first report, because a seat that boots wrong must not stay quiet for an hour. A seat whose self-test stopped reporting for three hours (`selftest-loop-dead`) or whose results cannot be read: on the first report. An hourly run: when two runs in a row failed, because one hourly red is a race as often as a fault. | The next run with no failed check (a `RECOVERED` reply in the same thread). |

Who acts: an agent. The email names the checks; each check's own detail is on the seat in `/run/smd-selftest/results.json`, and what each check proves is in `seat-checks.sh` beside it. A warning (a busy lane, a vendor that did not answer, the identifier-gate lever) is never a page.

The hourly Smokeball check is the only detector of a dead Smokeball token: the connector-health ledger never sees the scheduled jobs' Smokeball calls (PR #3144). A Smokeball seat whose hourly check is not scheduled, or a check that could not run, is an ACTION line in the list, not a page.

## What each line means

"Agent" in the last column means the triage agent has usually already investigated, and any further action is work you direct an AI agent to do. The few calls that are yours are marked "You".

| Line | What went wrong | What to do |
| --- | --- | --- |
| NO reply | A firm person's request has had no reply for 30 minutes or more. A job starting does not excuse it: the acknowledgement is owed either way. | Agent: find the request by its card id (`.claude/bin/requests`) and find out why. You: make sure the person is answered. |
| Reply held | The Operator wrote a reply and a gate held it; nothing was sent. | Agent reads the held reply and proposes release or rewrite. Releasing words to a client follows [Who Does What](/admin/playbook/who-does-what). |
| Job queued / running too long | A job has not started, or has run past its lane's limit (drafting 2h, demand 6h, chronology 12h, litigation 3h, case file 6h, negotiation 1h). | Agent: check the job on the seat; a hung runner looks like progress forever. |
| Job held / failed | A job ended without delivering. The reason's first word is shown (`review_held`, `request_incomplete`); the rest stays on the seat. Drops off after 72 hours, or as soon as a later job in the same lane on the same matter delivers. | You decide, on the agent's findings: rerun, release, or tell the requester why. |
| Smokeball refusing sign-in | The hourly sign-in check, the sign-in tool, or a job failed on authorization, and nothing has succeeded since. | You: get the client to re-consent the seat's Smokeball connection. |
| Connection failing / mailbox not read | The seat's own counters show repeated failures, or the mail poller has not succeeded for 15 minutes. | Agent: check the credentials and the vendor. |
| Open alert | A pager condition opened earlier and has not closed. | Agent: work the alert ([When Something Goes Wrong](/admin/playbook/when-something-goes-wrong)). |
| Scheduled run has not run since T: N missed | Slots of a schedule the seat was PROVISIONED with (D1 `customer_configs`, not git) passed with no run and no recorded skip. | Agent: check the seat's scheduler. |
| The Case File Manager's weekday run is paused / missed a caseload | The newest fire of the last day could not queue a review: the Smokeball connection has under seven days left (or its age is unknown), no job started before the firm's cutoff, or the broker refused. SMD was paged once when it began (`operator/workspace_broker/casefile_schedule.py`); this line repeats daily, never as a page (`operator/bin/lib/work_health_casefile.py`). | You: get the client to re-consent Smokeball when that is the reason. Agent: everything else. |
| Schedule drift | The repository authors a different schedule than the seat runs; that routine is not graded until a rebuild makes them agree. | Agent: rebuild the seat, or revert the authoring. |
| Run ended with nothing delivered / held and nobody told | A scheduled or held run ended silently (the terminal-state reconciler's finding), with a count when it repeats. | Agent: check whether the work it owed was done. |
| A message the Operator tried to send on its own was blocked by the safety check | An out-of-turn send (an outcome letter, an alert) was refused by the outbound scan, N times. Nobody it was for has heard. | Agent: find what it was sending (the seat's `agent.log` names it). You: decide how that person is told. |
| A call was refused / failed / hit a limit | Refused, limit, failed or partial tool results in the last 24 hours, one line per tool and reason with a count. | Agent: check whether a person was waiting and got an answer. |
| New Operator error | A Sentry error reported in the last 24 hours. | Agent: triage it in Sentry. |

## What the list never carries

Only what team@ alert mail already carries: lanes, matter numbers, a reason's first word, times, card ids. Never a sender, a subject line or a document name. The GitHub Actions log prints counts only, and the saved state holds hashes only, because ss-console is a public repository (`operator/bin/work-health.py`).

## Closing a held or failed job

The seat's ledgers have no cancel state. When a held or failed job needs no rerun, release or note (for example SMD's own test request), an agent adds it to `operator/contracts/work-health-closed.yaml` with a reason, in a pull request. That changes nothing on the seat; it stops the job being listed. The repository is public: the entry carries the job id and a reason that names no client.

## Changing it

All of these are agent work through a pull request.

- **The interval** of the page backstop is the `cron:` line of `.github/workflows/work-health.yml`. The triage agent's schedule is `operator/ops-machine/crontab` on `smd-ops` ([Client Ops Workspaces](/admin/playbook/ops-machine)).
- **Lane limits, windows and the all-clear hour** are in `operator/contracts/work-health.yaml`.
- **The Smokeball sign-in check** runs on a seat when its `customer.yaml` authors a `connector-auth-check` cron row with `pre_run: pre_run.py`; it takes effect at that seat's next rebuild.
