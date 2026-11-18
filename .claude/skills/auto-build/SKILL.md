---
name: auto-build
description: The build process for any change a client, a seat or the Captain on a live surface will notice. Decides how much process the change needs from the files it touches, asks whether a build is needed at all, runs a dedicated design pass, critiques, gates on the Captain's approval, then builds, proves the real path once and carries the work to READY. Run it for client-surface work whether or not the Captain typed it.
version: 3.0.0
scope: venture:ss
owner: captain
status: stable
depends_on:
  mcp_tools:
    - crane_skill_invoked
---

# /auto-build - From an ask to READY (SMD Services)

> **Forked from crane-console on 2026-11-09.** The fork point is commit `9d4f3b8c`.
> Version 3 is a redesign, not a patch. Its evidence lives in
> `docs/doctrine/agent-operating-doctrine.md`, "Where designs miss".

> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "auto-build")`. Non-blocking; if it fails, continue.

Thirty days of fixes say where features go wrong: 13 of 192 fixes repaired wrong logic, 157 repaired something the design never considered. So this skill spends its effort on the design and on one real proof, and little anywhere else.

```
/auto-build [critics]     critics defaults to 2
```

## Step 0: How much process

Run `.claude/bin/tier <paths the change will touch>`, adding `--new` when the work adds something that does not exist yet. The command decides; you do not, and the Captain does not.

| Tier           | What the change goes through                                                                                                             |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `none`         | An ordinary PR. Leave this skill. If the command prints `review:`, the PR still gets the bug review in Step 6.                           |
| `fix`          | Write the `/wired` sentence in one line and the failing case as the one example. Then Step 6: replay that example, the bug review, ship, close. No plan mode. |
| `capability`   | Every step below.                                                                                                                        |

## Step 1: The ask

Before any design, write three things.

1. **Build or answer.** If the client or the Captain can have what they need today from what already exists, say so, with its time and cost, and stop until he chooses. A day spent building what an email would have answered is the costliest miss on record.
2. **The words.** Quote what was asked. For a client, quote what they were told: the letter, the site, the welcome message, `.claude/bin/register list --client <slug>`. Cite the path and line; never paste client text into this repo.
3. **Corrections on file.** Search memory for the topic (`crane_memory` recall, and the memory index) and list the standing corrections that apply. A correction already given is a requirement, not a suggestion.

## Step 2: The contract

Invoke `/wired` on the ask. It returns the act as a sentence, the gate chain and the feasibility probe. If a gate cannot be closed, stop and tell the Captain before planning.

## Step 3: The design

Call `EnterPlanMode`. Use Explore agents, on Haiku or Sonnet, to locate code.

**A dedicated design pass, every time.** One Fable Plan agent designs the change. It is never skipped and never done by the lead in passing. Models and effort per role are in the doctrine table "Model per role". Its brief carries the ask from Step 1, the contract from Step 2, and this instruction: "Read the code the change touches and everything it must interact with. Write the approach, the files and functions to change, what the reader will see, and how to verify it. State every risk and edge case as a concrete thing the build must handle (a named input, state, caller or seat), not as a general caution." In a blind test on ten past features, a pass like this caught or nearly caught six misses the real builds caught none of; a longer list of fixed questions did no better.

**Where designs have missed.** The designer reads the table under "Where designs miss" in `docs/doctrine/agent-operating-doctrine.md` as reference, not as a form. Its heaviest rows: real inputs nobody pictured, where and how the code runs, everything else that reads or lists the thing, how a job ends, something that already existed, over-building, and the cost of a run.

The plan file has these parts, in this order.

**First screen, for the Captain.** What the reader will be able to do. What is left out, and its share of the whole job. The examples. The bill. Nothing technical.

**The design**, from the design pass, with its named risks.

**Examples.** Three to seven sentences of the form "when this arrives, the reader receives that", with invented values and no client text. They cover each ending the change touches, the awkward inputs the design named, and one "what works today still works". For a job lane, an Opus agent that is not the builder turns them into answer-key rows and rows in `operator/runners/jobs/tests/lifecycle_matrix.py` before any code is written. A lane with no answer key gets one first.

**The bill.** PRs times the merge time (about 27 minutes for Operator code, about 17 otherwise; re-measure with `gh run list` when it matters), plus releases, plus the cost of a run measured on real inputs and stated as added cost with its basis.

**`## Premises`**, as `.claude/hooks/plan-premise-gate.mjs` requires, and three lines for the build: builders, the files each owns, the bill.

Do not call `ExitPlanMode` yet.

## Step 4: Critique

Invoke `/critique` with the critic count (default 2: one hunts faults, one argues for less). Revise the plan file. Fix what the critics raise in the plan; do not file it for later and do not leave it for the build.

## Step 5: Approval

Call `ExitPlanMode`. Approved: go on. Rejected: the skill ends, and the Captain's next message leads.

## Step 6: Build, prove, ship, close

**Build.** First, one real path runs end to end at $0 before any parallel builder starts (doctrine, Pace). Then at most 4 builders, on Opus, in the lead's worktree, each owning files no other builder touches. A builder that needs another's code starts when that code is in the tree, never when it merged or reached a seat. Builders do not commit or push: the lead commits, pushes and opens the PRs. Every builder's brief carries the winning line, the risk class, the examples, and this line verbatim: "You never wait on CI, the merge queue or a long command (no `until`/`sleep` loops, no `gh run watch`, no `--watch`). Return what you changed and the commands you ran; the lead commits, pushes, merges and watches." Before pushing Operator code, the lead runs the two CI jobs that fail most, the golden comparison and the job-runner tests, as `.github/workflows/operator-substrate.yml` runs them.

**Prove, once.**

- One $0 replay on real saved inputs through the code path the seat runs, covering the examples. Client documents are opened only in that client's ops workspace.
- Endings no saved job shows are proven by their lifecycle-table rows.
- One Opus bug review of the diff: wrong results, and any risk the design named that the build does not handle (for a `fix`, that the failing case now passes and nothing beside it broke). Two reviewers when `.claude/bin/tier` shows a send path or client lane, the second on sending, rollback and money.
- Never first on the client's seat. Never a paid proving run. What a $0 replay cannot show is listed as "watched on the first real request".

**Re-run rule.** A fix re-runs the replay. A reviewer reads a fix only when it touches a send path or client lane. If that read finds new faults, stop, re-plan, and bring the Captain a new plan: the design is at fault, and another round will not fix it.

**Two rules against drift.** A review finding is answered by removing code before adding a rule, and no guard is added without an example that fails without it. No new measuring tool is built inside a feature build and no check waits on a clock; adding a row to an existing answer key or table is the work.

**Report.** One line to the Captain at each stage: what finished, what is next, and when the next line comes. No term he has not been given. Technical and quality calls are made, not asked; he is asked only about spend, client wording and scope. Past the promised PR count, he gets one line with the new bill.

**Ship.** `npm run verify`, then the PR, then merge through the queue. All code reaches the seat in one release, then the config that switches it on. Destructive actions (production data, force-push, secret rotation, dropping schema, auth changes) still need his explicit yes.

**Close.** `npm run deadcode` is clean, so nothing new is uncalled. Every row of the `/wired` contract (for a `fix`, its one sentence) is closed against the five READY conditions, each with evidence. The closing message says what the reader can now do and what they still cannot. Then the session writes its handoff and ends; it never waits for a real request.
