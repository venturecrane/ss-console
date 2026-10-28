# Runbook: a firm asked for a kind of job its seat has never done

You are here because team@ got a `[SMD Ops] <slug> NEW KIND OF ASK` card, or
`lane-proof.sh <slug> status` lists a job waiting on a proof.

## The rule

No new kind of job goes live on a real firm request until it has passed a full
dry run on a real matter of that client (the Captain, 2026-10-13). About 45 of
the 52 defects A&P met between 09-01 and 10-13 happened in a lane's first one to
three live uses; wherever a dry run ran first, nothing reached the firm.

So the seat does not run it. The job stays **queued** (never failed: a failed
job is terminal), the firm's acknowledgement stays true (the work is queued),
and SMD is paged within minutes. The target is a released job within hours.

## What a lane is

A lane key names a kind of job: the skill that queues it, plus the document
class for drafting, whose classes are different jobs.

| Key                             | Proof run stops before                                            |
| ------------------------------- | ----------------------------------------------------------------- |
| `medical-chronology-maintainer` | the upload stage (package staged under `out/`)                    |
| `demand-letter-drafter`         | filing (letter, gap audit, attorney notes under `out/`)           |
| `document-drafter.<class>`      | filing; caption corrections are journaled, not written            |
| `litigation-status`             | filing the workbook; runs on a copy of the real state cursor      |
| `negotiation-watch`             | any Negotiation Details write or notice (its dry run, ss#3119)    |
| `case-file-manager.full`        | filing (File Statuses rendered, nothing filed or emailed)         |
| `case-file-manager.recheck`     | filing, as `.full`; reads only what changed since the last run    |
| `case-file-manager.apply`       | the first write (approvals and fresh reads checked, nothing made) |

An in-turn skill (`file-work-requests`, the router) is never held: the turn is
the act. Its first live use pages once, so SMD reads that first answer.

The proven set per seat is `operator/customers/<slug>/lane-proofs.yaml` (seed,
reviewed in git) plus the runtime events `medchron lane-proof accept|revoke`
writes on the seat's volume. Both are root-owned on the seat: the agent cannot
prove its own lanes.

## The path from page to release

1. **Read the card.** It names the seat, the requester, the subject, the lane
   key and the job id it is holding.
2. **Check nothing is running on the lane.** `lane-proof.sh <slug> status`.
   A proof run waits for its lane to be idle by itself; this tells you when.
3. **Run the proof.**
   `operator/bin/lane-proof.sh <slug> dry-run <job_id> --wait 3600`
   The lane runs the held job's twin: the real pipeline (pull, every paid stage,
   every gate, the pre-delivery review, the render) on the request's own matter.
   Nothing is filed and nothing is emailed: the runner stops at its write stage
   and the Smokeball connector refuses every write while the run is marked a
   proof, so a runner that forgets its stop fails its proof instead of writing.
   The firm's job row does not move. Spend is real and is in `proof.json`.
4. **Read what it made.**
   `operator/bin/lane-proof.sh <slug> pull <job_id>` copies the twin to
   `$TMPDIR/lane-proof/<slug>/<job_id>` (refused inside a git checkout: it is
   client material). Read every document as the attorney would. A passed proof
   run is necessary, not sufficient: the 2026-10-12 discovery set passed its own
   gates with a false declaration in it.
5. **Fix and re-run, or accept.** A defect is fixed in code, merged, deployed
   (a reprovision; never while a job is running), and the proof re-run. When it
   is right, record the read with `crane_verify` and accept:
   `operator/bin/lane-proof.sh <slug> accept <job_id> --evidence vfy_... --by <you>`
   `accept` refuses without a passed proof run of that job and a verify id.
6. **The job releases itself.** On the lane's next tick the daemon clears the
   hold note from the ledger row (before running: a stale reason on a delivered
   row once told a requester something false), claims the job and runs it live.
   The deliver turn replies to the requester as for any job.
7. **Write it down.** Add the lane to the seat's `lane-proofs.yaml` with the
   job id, the date and the verify id, in a PR, so a recreated volume keeps it.

## Proving a lane before the firm asks

Do not wait for a client request to find out whether a lane passes. `dry-run`
also takes a FINISHED job of the lane whose job dir still keeps its envelope
(job dirs are wiped 72 h after they end): the twin runs that real request again,
nothing is filed or emailed, the finished job's row and dir are untouched, and
no allowance is used. Pick the most recent real request of that kind, read the
artifacts, and `accept` on a pass. The next request of that kind then runs
straight through.

**When the only request of a kind is about to be wiped.** A finished job's
dir goes 72 h after it ends. While its lane is still being fixed, keep its
envelope: `operator/bin/lane-proof.sh <slug> keep <job_id>` copies the request
(never the records: a proof run pulls the matter afresh) to
`/run/smd-medchron/proof-envelopes/<lane>/`, root-only, for 14 days, and
`dry-run` finds it there after the job dir is gone.

## Undoing a proof

`operator/bin/lane-proof.sh <slug> revoke <key> --why "..." --by <you>`: new
jobs of that kind wait again. A revocation in `lane-proofs.yaml` (the
`revoked:` list) wins over every runtime proof until the file changes.

## What this does not cover

- An in-turn ask is paged, not held.
- A misread ask filed under a proven lane (the agent chose the kind) runs: the
  pre-delivery review is the control for that, not this gate.
- A held job nobody proves waits indefinitely. `status` lists every hold with
  its age; the card fires once per held job.
