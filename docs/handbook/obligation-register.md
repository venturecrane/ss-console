---
title: What We Owe Clients
section: operations
order: 10
summary: The obligation register - how every piece of work SMD owes a client gets recorded without anyone maintaining a list, how it gets closed only by a probe of the real system, and what it deliberately cannot see
sources:
  - label: docs/adr/0088-obligation-register.md (the decision)
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0088-obligation-register.md
  - label: migrations/0117_client_obligations.sql (the register)
    href: https://github.com/venturecrane/ss-console/blob/main/migrations/0117_client_obligations.sql
  - label: scripts/ci-reconcile-obligations.ts (import, probe, census, escalate)
    href: https://github.com/venturecrane/ss-console/blob/main/scripts/ci-reconcile-obligations.ts
  - label: .claude/bin/register (letter capture)
    href: https://github.com/venturecrane/ss-console/blob/main/.claude/bin/register
  - label: src/pages/admin/obligations/index.astro (the fleet view)
    href: https://github.com/venturecrane/ss-console/blob/main/src/pages/admin/obligations/index.astro
---

## The question this answers

"What do we owe Ashton & Price?" - and "what's on our plate?" across every client.

Before the register, neither question had an answer. For one client, the work of keeping them served lived in nine different places: a task cleanup promised in correspondence letters 26/27/28, a per-cycle duty recorded in an agent memory file, a vendor consent expiring in March 2027, a delivered chronology whose ledger row still read `failed`, a pending vendor approval visible only as one session's mission line, and four unshipped fixes in GitHub.

The failure that shape produces is already documented in this enterprise. The cadence engine is a reminder queue with no forcing function; it reached **7 of 16 items overdue, one by 134 days**, while reporting itself healthy.

## Where the rows come from

Two routes, and the split matters.

**Derived, every run.** Most obligations already exist somewhere a machine can read. The reconciler imports them each pass:

| Source | Becomes |
| --- | --- |
| GitHub issues labelled `client:<slug>` | a product defect blocking that client |
| Open `fleet_alert_state` conditions | a renewal (an expiring token) or an incident |
| Open `operator_change_requests` | an external dependency |

**Captured, by hand, for letters only.** An obligation stated in a client letter is prose in a private repo and no source enumerates it. An agent records it with `.claude/bin/register add`, and the CLI refuses the row unless a verbatim quote from the letter is found in the letter.

The reason for that split: an earlier design had agents record everything as a byproduct of their work. Of the six real obligations above, only one arrives by a route that design could see.

## What makes a row trustworthy

**The quote is checked, not trusted.** An extractor's citation is right about five times in six. So the CLI normalizes the quote and the source (smart quotes, line wrapping, emphasis markers) and string-matches. No match, no row, and there is no override flag.

**Only dated obligations raise alarms.** Checking a quote grounds the *citation*, never the *interpretation* - a well-cited sentence can still be summarized wrongly. So a due date needs its own quote containing that date, and undated obligations appear on the page but never generate an alert. The residual error stays somewhere it gets read deliberately rather than landing in an inbox.

**Nothing closes itself.** A row reaches `verified` only when a scheduled CI run probed the real surface - the mailbox, the R2 object, the GitHub state, the seat. The database enforces it: the certifying run is a foreign key, so a certification naming a run that never happened is rejected outright.

**Failure parks; it never terminates.** `parked` carries its reason and a resume token and returns to any working state. This is the direct inverse of the medical-chronology ledger, where `failed` had no way out and a job that had actually been delivered stayed stranded in a state saying the opposite.

## How you hear about it

Overdue, unverifiable and capture-gap findings are written into the existing fleet alert store, which already emails `team@smd.services` for any non-cost alert. Nothing new pages you; the register writes to the surface that was already there.

Each run also opens or updates one rolling GitHub issue with its full report.

## The control that watches the watchers

Every other check validates rows that exist. None of them would notice the register quietly going empty - which is exactly how a reminder queue rots.

So each run prints a **coverage census**: how many artifacts each source holds, against how many obligations came from it. A source with artifacts and no obligations raises a capture gap. The run also prints **two** counts - every row, and the non-terminal rows it actually considered - because a single count cannot tell "nothing to do" from "the query is broken."

## What it deliberately cannot see

Stated plainly, because a register that implies full coverage is worse than one with known holes:

- **An obligation created in a phone call.** No artifact, no capture.
- **Correspondence read and acted on without any write.** Nothing signals it happened.
- **The contents of Smokeball.** CI has no Smokeball credential; the seat holds it. Those rows are marked `attested`, meaning we verify a receipt exists and freshness, never the filing itself.

## If you change X, update Y

| If you change | Update |
| --- | --- |
| The `kind` vocabulary or the state machine | `migrations/0117`, `src/lib/db/obligations.ts`, and this page's table |
| What a source imports | `scripts/ci-reconcile-obligations.ts` and the source table above |
| The alert conditions | `migrations/0118` CHECK, `src/lib/admin/fleet-alerts.ts`, and "How you hear about it" |
| The grounding rules | `.claude/hooks/lib/register.mjs` and "What makes a row trustworthy" |
