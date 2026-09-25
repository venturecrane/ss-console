---
title: What We Owe Clients
section: operations
order: 10
summary: The obligation register - how every piece of work SMD owes a client gets recorded without anyone maintaining a list, how it is read from the terminal, how it gets closed only by a probe of the real system, and what it deliberately cannot see
sources:
  - label: docs/adr/0088-obligation-register.md (the decision)
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0088-obligation-register.md
  - label: migrations/0117_client_obligations.sql (the register)
    href: https://github.com/venturecrane/ss-console/blob/main/migrations/0117_client_obligations.sql
  - label: scripts/ci-reconcile-obligations.ts (import, probe, census, escalate)
    href: https://github.com/venturecrane/ss-console/blob/main/scripts/ci-reconcile-obligations.ts
  - label: .claude/bin/register (letter capture)
    href: https://github.com/venturecrane/ss-console/blob/main/.claude/bin/register
  - label: src/lib/db/obligations.ts (the reader the reconciler and CLI share)
    href: https://github.com/venturecrane/ss-console/blob/main/src/lib/db/obligations.ts
  - label: .claude/skills/eos/SKILL.md (Check I, where capture happens)
    href: https://github.com/venturecrane/ss-console/blob/main/.claude/skills/eos/SKILL.md
  - label: .claude/skills/sos/SKILL.md (the session-start line)
    href: https://github.com/venturecrane/ss-console/blob/main/.claude/skills/sos/SKILL.md
  - label: scripts/lib/seat-clients.mjs (which seats roll up to which client)
    href: https://github.com/venturecrane/ss-console/blob/main/scripts/lib/seat-clients.mjs
---

## The question this answers

"What do we owe Ashton & Price?" - and "what's on our plate?" across every client.

**You read it from a terminal**, by asking an agent, or directly:

```
.claude/bin/register list --client ashton-price
.claude/bin/register list
```

There is deliberately no web page. One shipped on 2026-09-17 and was deleted the
same day: the Captain works in terminal sessions and does not open the admin
console to look things up, so a page there would have decayed into a surface
nobody read and nothing checked. The register is queried where the work happens.

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

**No obligation goes overdue on an invented date.** Checking a quote grounds the *citation*, never the *interpretation* - a well-cited sentence can still be summarized wrongly. So a due date needs its own quote containing that date, and a row without one never reports as overdue. The residual extraction error stays somewhere it gets read deliberately rather than landing in an inbox.

That gate is strict enough that in practice almost nothing carries a date, which is why an undated row raises a different alarm on age alone. See "What a stalled row does now" below.

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

## Who records what we owe

Most rows arrive on their own. The nightly reconciler derives them from GitHub issues, open alert conditions and change requests, because a source a machine can enumerate is a source nobody has to remember.

The exception is prose. An obligation stated in a letter exists only in that letter, and the only thing that knows about it is the session that read the letter, for as long as that session lasts. So capture happens at session close: `/eos` Check I lists any client whose correspondence was read this session with nothing recorded, and the session either records what was promised or records that nothing was.

```
# Something was promised:
.claude/bin/register add --client ashton-price --kind deliverable --key <stable-key> \
  --what "<sentence>" --source <letter path> --quote "<verbatim from that letter>"

# Nothing was promised - record the considered pass:
.claude/bin/register add --kind none --client ashton-price --why "<why nothing is owed>"
```

The second form matters as much as the first. Without it, "we looked and owe nothing" is an absence, and an absence is indistinguishable from never having looked.

**Check I records; it never blocks.** It is not part of the Ship Gate and never becomes a reason a session cannot close. Client obligations legitimately span sessions - that is what a register is for. A gate there would make every promise a blocker, and within a week people would be inventing reasons to get past it.

**What the check cannot see.** The detector reads the session's engagement read log, which is written when a file is opened with the Read tool. A letter opened through the shell, reached by a search, or pasted into the prompt leaves no trace. A client it flags is real; a quiet session proves nothing.

**SMD Services is itself a client.** Obligations on our own seats (`pilot-smokeball`, `smd-staging`, `scott`) roll up to `smd-services`. There is no internal-versus-external split in the register: there is a client list, and we are on it. Identity is the default in `scripts/lib/seat-clients.mjs`, so onboarding a client needs no change there - only a new seat of our own does.

## How a row closes

A row leaves the list when the nightly run moves it to `verified`. It gets there two ways, depending on where it came from.

**A derived row closes from its source.** When the issue closes, the alert condition clears, or the change request is completed, the run walks the row through `delivered` to `verified` on its own. Nobody marks it. A declined change request is settled differently: nothing was delivered, so the run moves its row to `cancelled`, never to `verified`. Because a closed `client:<slug>` issue is taken as the evidence, close one only when that client can actually do the thing, not when the PR merges.

**A letter row closes from the letter that kept it.** When a session sends the letter that delivers on a promise, it records the delivery:

```
.claude/bin/register deliver --client ashton-price --key <stable-key> \
  --evidence <path of the SENT letter> --quote "<verbatim from that letter>"
```

The command reads the letter off the engagements repo's `origin/main`, never the file on disk, because a letter in a working tree may be a draft that never went out. It refuses a letter that is not merged, the letter that made the promise, an archive it could not fetch, and a quote the letter does not contain. It moves the row to `delivered` and reads the write back. It cannot go further: `verified` needs a reconcile run, so the nightly run is still what certifies.

CI holds no credential for the private engagements repo, so this evidence is **attested**, the same class as a Smokeball filing. The command is the thing that can see the archive, and the receipt it leaves (the letter pinned to the commit it was read at, plus the time it was read) is what CI certifies against. The receipt proves the letter was archived as sent. It does not prove the email left the mailbox; the archive is the venture's record of what was sent.

Until 2026-09-19 neither route existed. Nothing moved any row out of `open`, the run counted derived rows whose source had cleared as "verified this run" while leaving them open, and a promise kept in a sent letter stayed on the list forever. `/eos` Check I now asks for deliveries as well as captures.

## What a stalled row does now

Only a dated obligation can go overdue, and a due date is only accepted with its own quote containing that date. In practice almost nothing carries one, which left a gap: an undated row could sit open forever while the nightly run reported the register converged. That is the failure this register was built to replace, reproduced inside it.

So an undated obligation that stays open becomes a finding on age alone - a warning at 30 days, critical at 60 - and that finding counts toward the run's exit code rather than merely raising an alert beside a green report. Thirty days is one billing cycle. The ladder applies only to hand-captured rows; a derived GitHub row is ordinary backlog that other surfaces already show.

## If you change X, update Y

| If you change | Update |
| --- | --- |
| The `kind` vocabulary or the state machine | `migrations/0117`, `src/lib/db/obligations.ts`, and this page's table |
| What a source imports | `scripts/ci-reconcile-obligations.ts` and the source table above |
| The alert conditions | `migrations/0118` CHECK, `src/lib/admin/fleet-alerts.ts`, and "How you hear about it" |
| The grounding rules | `.claude/hooks/lib/register.mjs` and "What makes a row trustworthy" |
| Who captures, and when | `.claude/skills/eos/SKILL.md` Check I, `CLAUDE.md`, and "Who records what we owe" below |
| How a row closes, or what counts as delivery evidence | `.claude/hooks/lib/register.mjs` (`deliver`), `certify` in `scripts/ci-reconcile-obligations.ts`, `tests/register-deliver.test.ts`, and "How a row closes" |
| Which seats roll up to which client | `scripts/lib/seat-clients.mjs` and "Who records what we owe" below |
| What the session-start line says | `.claude/skills/sos/SKILL.md` Step 4 and the `--json` allowlist in `.claude/hooks/lib/register.mjs` |
