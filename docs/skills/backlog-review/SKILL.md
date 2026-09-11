---
name: backlog-review
description: Reviews the ss-console open-issue backlog from a deterministic census rather than by reading issues. Runs scripts/backlog-census.ts, diffs it against the previous run, and brings the Captain prose - counts with denominators, the undecidable residue, a recommendation, and one specific question - then executes only the batch the Captain names.
version: 1.0.0
scope: venture:ss
owner: captain
status: stable
depends_on:
  mcp_tools:
    - crane_skill_invoked
    - crane_verify
  scripts:
    - scripts/backlog-census.ts
    - scripts/backlog/classify.ts
---

# /backlog-review - Review the backlog from measurement

> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "backlog-review")`. Non-blocking; if it fails, log and continue.

## Usage

```
/backlog-review                  # full pass: census, diff, gate, act
/backlog-review --report-only    # census + diff + prose, propose nothing, change nothing
/backlog-review --from <path>    # reclassify a saved snapshot, no network
```

Default target is `venturecrane/ss-console`. `BACKLOG_REPO` retargets the
census, and **you never retarget it to a private repo** — this repo is public
and the snapshot this skill commits carries every issue title and body verbatim.

## Where this skill lives

Canonical copy is `docs/skills/backlog-review/SKILL.md`.
`.agents/skills/backlog-review/SKILL.md` and
`.claude/commands/backlog-review.md` are **symlinks to it**, created by
`scripts/install-captain-skills.sh`. Both destinations are gitignored, so a
symlink is what keeps one authored file from becoming three drifting ones.

Merging this file does not make `/backlog-review` exist on any machine. Run the
installer on a fresh checkout. Edit only this file; the other two follow.

## The rule this skill is built around

**The Captain cannot see any artifact this run produces.** Not the census
markdown, not the snapshot JSON, not a directory listing. Only the text of your
messages reaches them.

So never write "the report is at `.stitch/audits/...`" and never ask them to
look at a file, a table, or a list of issue numbers you have not read yourself.
You do the reading. Then you present, in prose:

1. what the census counted, with denominators,
2. what moved since the last run, and why it moved,
3. what is mechanically decidable and what is residue,
4. your recommendation,
5. one specific question with named options.

See `feedback_captain_cannot_see_artifacts_gates_must_be_prose.md`.

## Why a census and not a reading

On 2026-08-24 a hand-built review of 154 open issues was **retracted twice in
one session**. The raw counts were never wrong — 154 open, 192 filed in August,
92 carrying acceptance criteria, all reproduced across two independent
instruments. All four retractions were interpretation running ahead of
measurement: a category asserted and never counted, a population claim
generalised from one comment thread, a regex that could not match this repo's
own `ss#NNNN` convention, and arithmetic done by hand.

That is a structural problem, so it got a structural fix. **Every number you
report comes from `scripts/backlog/classify.ts`. You may interpret them and you
may not produce them.** If you find yourself counting issues in your head, stop
and add a rule or a flag to the classifier instead.

The venture's other instrument shows the stakes: `crane_status` renders
"Triage Queue: Backlog is empty" over this backlog, because it reads `status:`
labels and most open issues carry none. A check that reports empty over a
140-issue backlog has measured nothing (Law 12).

---

## Step 1 - Preconditions

Four, and each has produced a wrong census.

1. **`git fetch origin`.** `readNamingCommits` reads `git log origin/main`. A
   stale `origin/main` under-reports the commits that name an issue, which
   silently moves rows into `never-worked` and `needs-probe` — the census will
   look tidier for being less informed. In a worktree this is the default state,
   not an edge case.
2. **`gh auth status`.** The census pages GraphQL. A credential failure throws
   with a named field rather than returning a short list, but confirm first.
3. **Dependencies.** `npx tsx` needs a current install. If the session's primer
   reported stale dependencies, run `npm ci` before trusting anything.
4. **Record the pipeline sha.** `git rev-parse origin/main`, in your notes and
   in the census filename's company. A verdict distribution is only comparable
   to another one produced by the same classifier: on 2026-08-24 the same
   snapshot yielded `autofile-duplicate` 9 before #2578 and 8 after, because
   that PR stopped calling a re-file somebody had worked a bare duplicate.

## Step 2 - Run the census

```bash
DATE=$(date +%F)
npx tsx scripts/backlog-census.ts \
  --snapshot ".stitch/audits/backlog-snapshot-$DATE.json" \
  > ".stitch/audits/backlog-$DATE.md"
```

Both artifacts are tracked, on purpose. The snapshot is what makes the census
arguable instead of asserted: anyone can run `--from` against it and get the
same answer back, and the next run diffs against it rather than re-arguing.

**`--from` reproduces only because it classifies at the snapshot's own
`fetchedAt`.** Until 2026-08-31 it used the wall clock, and the same 08-24
snapshot reclassified seven days later reported `never-worked` 9 → 18 and
`needs-probe` 94 → 85 with nobody having touched the backlog: nine issues had
crossed the 30-day line on the calendar alone. Pass `--now` only to ask a
deliberate what-if ("which rows go stale next week"), never to get a fresh
number.

## Step 3 - Diff against the previous run

Find the most recent prior `.stitch/audits/backlog-*.md`. Compare verdict
counts, and **attribute every movement to one of three causes before reporting
it**:

| Movement           | Cause                               | How to tell                                                                |
| ------------------ | ----------------------------------- | -------------------------------------------------------------------------- |
| Population changed | issues opened or closed             | `totalOpen` moved                                                          |
| Rules changed      | the classifier was edited           | `git log scripts/backlog/classify.ts` since the prior run                  |
| Clock moved        | age-keyed rows crossed `STALE_DAYS` | reclassify the OLD snapshot with `--from`; anything that moves is calendar |

The third row is the one that lies. Run `--from` on the previous snapshot: it
reproduces that run exactly, so any difference between it and the new census is
real. Never report a delta you have not attributed.

## Step 4 - Gate 1: the census, in prose

Bring the Captain, in the message body:

- **Population.** Open now, open at the previous census, and the net.
- **The decidable share and the residue**, each `n of N`. As of 2026-08-31:
  140 open, of which `needs-probe` 88 (63%), `commits-unticked` 36 (26%),
  `gate-held` 11 (8%), `never-worked` 4, `tick-blocked` 1, `close-acs-met` 0,
  `autofile-duplicate` 0. Roughly a third of the backlog is decidable by rule
  and the rest is not. **That residue is real and no rule set will fake it
  away** — do not present it as a gap in the tool or as work you failed to do.
- **What moved since last run, attributed** (Step 3).
- **The batches you propose**, by verdict, each with its issue numbers and what
  the action would be.
- **Your recommendation** and **one question with named options.**

Never propose "clean up the backlog". Propose a named batch of numbered issues
with one action each.

## Step 5 - Act, one verdict class at a time

Only on an explicit yes, and only the batch named. Closing an issue is an
external-record mutation that notifies watchers; batching several classes into
one approval is how a "yes" to the safe half executes the unsafe half. See
`feedback_no_batching_destructive_or_gated_actions.md`.

**The close hazard that governs everything below.**
`.github/workflows/unmet-ac-on-close.yml` **reopens** any issue closed with
unchecked acceptance criteria. So closing an issue that still has an unticked
box does not tidy the backlog — it produces a bot reopen, and the next census
reads that reopen as `gate-held`. You would manufacture the exact verdict class
you were clearing, and the 11 `gate-held` rows standing today are what that
looks like accumulated. **Never close an issue with `acsUnchecked > 0`.** Either
the criterion is met and gets ticked with its evidence, or it is not met and the
issue stays open.

| Verdict              | Action                                             | The trap                                                                                                                                                                                                                                                                                                                                                                        |
| -------------------- | -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `autofile-duplicate` | Close, commenting with the canonical lower number. | The classifier already refuses to call a re-file somebody worked a duplicate (`untouched` in `decide()`). Never loosen that: on 2026-08-24 three re-files carried live threads — five merged commits, an analysis with a verify ID, and a prior session's decision to keep two issues paired. Duplicate-closing any of them destroys the thread while reporting a tidy backlog. |
| `gate-held`          | **Satisfy the gate.** Do not close.                | Re-closing trips the same gate and changes nothing but the timestamps. Read why the gate fired first; usually it is unticked ACs, which means the work is not done.                                                                                                                                                                                                             |
| `close-acs-met`      | Read the ACs, then close.                          | Ticked boxes are the issue's claim about itself. An AC tagged `(runtime)` ticked with no `crane_verify` ID is exactly what `runtime-ac-proof.yml` blocks at PR time and what the entitlement-control incident produced: four honest PRs, a green epic, and a client who could not perform the act. Check for `(runtime)` rows before closing.                                   |
| `tick-blocked`       | Reconcile the linked PR against each unticked AC.  | The PR merged; that is not the same as the criterion being met.                                                                                                                                                                                                                                                                                                                 |
| `commits-unticked`   | Reconcile per issue. **Never bulk-tick.**          | The largest decidable class (36 of 140). A commit subject naming an issue proves something landed, not that the issue's criteria are satisfied. Bulk-ticking here is self-certification at scale.                                                                                                                                                                               |
| `never-worked`       | Ask the Captain: still wanted, or close as stale?  | Only 4 of 140 today. This is a judgment, not a rule outcome — the census says nobody worked it, never that it does not matter.                                                                                                                                                                                                                                                  |
| `needs-probe`        | Step 6.                                            | —                                                                                                                                                                                                                                                                                                                                                                               |

## Step 6 - The residue

`needs-probe` is roughly 63% of the backlog and will not be cleared in a
session. Work it in bounded batches the Captain sizes.

For each issue in the batch: read it, read what its ACs actually say, check
whether the named surface still exists, and land it on one of the decidable
verdicts **with a citation** — a `file:line`, a command's output, a merged sha,
a `crane_verify` ID. Then bring the batch back as prose.

**A gap in your context is a question, not a finding** (Law 4). An issue you
cannot decide stays `needs-probe` and is reported as such. Never guess a verdict
to shrink the residue, and never report "no rule decides this" as a defect in
the tool — it is the tool declining to invent an answer.

## Step 7 - Close out

1. **Report what changed**: issues closed, ACs ticked with their evidence, gates
   satisfied, residue examined and residue remaining — each `n of N`.
2. **Record a `crane_verify`** for the census run: method `fresh_process`, the
   command, and the verdict table as output. That is what lets a later session
   ask what the backlog looked like on this date without re-arguing it.
3. **Commit both artifacts** with the classifier sha in the message. A census
   nobody can reproduce is an assertion.
4. **Write a memory only if the run changed a fact** — a new verdict class, a
   rule that proved wrong, a hazard nobody had hit. A finished review is not by
   itself a reason to write one.
5. **File issues for classifier defects, do not patch around them.** If you had
   to reason past a wrong verdict, the next session will too. Fix the rule and
   add its falsifier to `tests/backlog-classify.test.ts`.

---

## Failure modes this has actually produced

| Symptom                                                    | Cause                                                                                                  |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| A census tidier than the last with no work done between    | Stale `origin/main`; `namingCommits` under-read, rows fell into `never-worked`                         |
| `never-worked` doubled overnight                           | The clock, not the backlog — age-keyed rows crossed 30 days (fixed 2026-08-31; reproduce with `--now`) |
| An issue you closed is open again with a bot comment       | `unmet-ac-on-close` reopened it; it had unticked ACs                                                   |
| `gate-held` grows every review                             | Issues being re-closed instead of having their gate satisfied                                          |
| Verdict counts differ from a report committed the same day | The classifier changed between them; compare `git log scripts/backlog/classify.ts`                     |
| "Triage Queue: Backlog is empty"                           | `crane_status` reads `status:` labels; most open issues carry none                                     |
| A duplicate close that erased a live discussion            | The `untouched` guard was bypassed or loosened                                                         |

## Related

- `reference_backlog_census_tool.md` - the tool's own design rules
- `feedback_captain_cannot_see_artifacts_gates_must_be_prose.md` - why every gate here is prose
- `feedback_check_must_be_able_to_fail.md` - why the classifier's tests assert both directions
- `feedback_sampled_absence_is_not_evidence.md` - why every count prints its denominator
- `feedback_no_batching_destructive_or_gated_actions.md` - why Step 5 is one class at a time
- `feedback_a_citation_is_not_coverage.md` - why Step 6 demands a citation per row
- Law 4 and Law 12 (`docs/doctrine/agent-operating-doctrine.md`)
