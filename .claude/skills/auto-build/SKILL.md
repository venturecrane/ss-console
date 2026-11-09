---
name: auto-build
description: Captain-invoked rigor pass for an implementation task. Establish a reachability contract with /wired when the effect reaches outside the repo, enter plan mode, build the plan, run /critique to revise, gate on user approval via ExitPlanMode, then execute autonomously under Auto Mode. Captain types `/auto-build [agents]` when they want the full plan-and-vet cycle before going autonomous; harness should not auto-suggest this skill (the trigger is a Captain judgment, not a pattern).
version: 1.2.0
scope: global
owner: captain
status: stable
depends_on:
  mcp_tools:
    - crane_skill_invoked
---

# /auto-build - Vetted plan-and-execute workflow

> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "auto-build")`. This is non-blocking — if the call fails, log the warning and continue. Usage data drives `/skill-audit`.

This skill orchestrates a fixed seven-step workflow the Captain runs by hand for non-trivial implementation tasks: establish a reachability contract with `/wired` → enter plan mode → build a plan → run `/critique` → exit plan mode for approval → execute autonomously under Auto Mode. It is a thin coordination layer over harness primitives and the existing `/wired` and `/critique` skills. It does not reinvent any of those pieces.

The skill is single-shot. There is no internal "revise loop." Re-run `/auto-build` if the approval gate is rejected.

## Arguments

```
/auto-build [agents]
```

- `agents` — number of critic agents `/critique` will spawn in Step 5. Default: **1** (Devil's Advocate). Pass through verbatim to `/critique`. The Captain knows the stakes at invocation; do not infer the count from plan content.

Parse the argument: if `$ARGUMENTS` is empty or not a number, default to 1. Store as `AGENT_COUNT`.

## Execution

### Step 1: Verify Auto Mode is active (fail-closed)

Scan the current system-reminder context for any of these known signals:

- `Auto Mode Active` — current canonical string
- `Auto-Accept Mode` — alternate string seen in some harness builds
- `auto_mode: true` or similar structured field if/when one ships

**If a match is found**, proceed to Step 3 silently.

**If NO match is found**, do NOT assume Auto Mode is off — assume the detection is unreliable. Halt with:

> `/auto-build` couldn't confirm Auto Mode is active. The skill needs autonomous execution after plan approval; without it, you'll be prompted on every tool call.
>
> Either: toggle Auto Mode on (Shift+Tab cycles modes) and re-run, OR reply `yes auto` to override and proceed anyway.

Wait for the Captain's response. Only proceed to Step 3 if they re-invoke after toggling, or reply with the literal `yes auto` override.

**Known fragility:** This is string-matching against harness output and will break if the harness changes its signal. Replace this step with a programmatic mode signal (env var, dedicated tool, structured field) when one ships. Until then, the synonym set + override is the best available.

### Step 2: Establish the reachability contract

Decide whether the ask's effect is observable outside this repo — a client, the Captain on a live surface, an Operator seat, a prospect on a marketing page.

**If yes**, invoke `/wired` via the Skill tool, passing the ask. It returns the contract: the act as a sentence, the terminal seam the change must land on, the gate chain enumerated backwards from that seam, and a feasibility probe. Steps 4 and 5 consume it — the plan is written against the contract, and the critics inherit it from conversation context.

**If no** — internal refactor, test-only change, or documentation — skip it and say so in one line. Do not grow a gate table on a typo fix; per `/wired`'s own scoping rule, an obstacle course that fires on everything gets routed around, and then it protects nothing.

**If the feasibility probe finds a gate that cannot be closed**, halt and surface it before entering plan mode. Planning around an unclosable gate is the entitlement-control failure the contract exists to prevent.

Order is not negotiable: a plan written against a component-shaped ask gets critiqued as a good component build, and the critique never notices the feature is unreachable.

### Step 3: Enter plan mode

Call `EnterPlanMode`. The harness requests Captain approval and provides a plan file path on entry. If the Captain rejects entry, the skill ends.

### Step 4: Build the plan

Follow the plan-mode discipline already documented in the system prompt:

- **Phase 1 (Initial Understanding):** launch up to 3 Explore subagents in parallel to map the affected code. Use the minimum number needed (often 1).
- **Phase 2 (Design):** launch up to 3 Plan subagents to design the approach.
- **Phase 3 (Review):** read critical files identified by the agents; resolve any open questions with `AskUserQuestion`.
- **Phase 4 (Final Plan):** write the plan to the plan file. Include Context, recommended approach (only the chosen one), critical file paths, reused functions/utilities, an `## Execution Mode` block (see below), and a Verification section. When Step 2 produced a contract, the plan must close every gate it enumerated or name the gate and who owns it — a plan that leaves a gate unassigned is not approvable.

The `## Execution Mode` section is a fenced YAML block with a fixed schema. Step 7 parses this verbatim — no prose lists, no free-form bullets:

```yaml
mode: solo # or: team
# the rest is required only when mode: team
team:
  pr_strategy: one-per-workstream # or: bundled
  workstreams:
    - name: <slug> # required, unique within the plan
      subagent_type: <agent-type> # required (e.g., general-purpose, Explore)
      scope: <one-line scope> # required; if pr_strategy=one-per-workstream, must assert disjoint files
      blocked_by: [<name>, ...] # optional; every entry must resolve to a declared name
      blocks: [<name>, ...] # optional; symmetric with blocked_by
      isolation: in-process # default; or: worktree (must justify in scope line)
```

Heuristic for choosing — encode it explicitly so it isn't vibes-based:

> Default `solo`. Choose `team` only when **(a)** ≥2 genuinely independent workstreams (different packages, surfaces, or layers with disjoint files), or **(b)** identical pattern applied across N targets (e.g., parallel mirror updates across `.claude` + `.gemini` + global). **Cap N at 4 per wave.** For N>4, declare batched waves: define ≤4 workstreams in the first wave and chain a second wave via `blocks`/`blocked_by`, or run sequential `/auto-build` invocations per issue if the work is already issue-cut. Coordination tax usually exceeds parallelism gain on single-package work — when in doubt, `solo`.

Do not call `ExitPlanMode` yet. Step 6 owns the exit.

### Step 5: Invoke `/critique`

Call the `/critique` skill via the Skill tool, passing `AGENT_COUNT`:

```
/auto-build           → /critique 1   (default Devil's Advocate)
/auto-build 3         → /critique 3   (Devil's Advocate + Simplifier + Pragmatist)
/auto-build 6         → /critique 6   (full panel)
```

**When the plan declares `mode: team`**, before invoking `/critique` append a one-line focus note to the conversation context: "this plan declares team-mode; critics must challenge the Solo/Team call against the heuristic and verify workstream files are disjoint per `pr_strategy`." This nudges the critic without requiring a `/critique` edit. Critics may downgrade `team`→`solo`, upgrade `solo`→`team`, change the workstream count, or flip `pr_strategy`; reflect any such change in the YAML block like any other revision.

`/critique` reads the conversation, spawns critics in parallel, and returns a revised plan with `### Changes Made` and `### Critiques Acknowledged but Not Adopted` sections.

**Apply the revised plan to the plan file.** Editing the plan file is allowed under plan-mode rules (it is the only writable file). The plan file is the source of truth the Captain will see at the approval gate, so it must reflect the post-critique state.

If `/critique` reports "no plan identifiable," something went wrong in Step 4. Re-enter Step 4 — do not attempt to exit plan mode with an unwritten plan.

### Step 6: Gate on Captain approval

Call `ExitPlanMode`. The harness presents the revised plan to the Captain.

- **Approved** → proceed to Step 7.
- **Rejected** → the skill ends. The Captain re-runs `/auto-build` (or any other command) to start a fresh flow.

We do not model a revise loop. ExitPlanMode rejection delivers Captain feedback as a new user turn, at which point the Captain's next message supersedes the in-progress skill — there is no reliable contract that `/auto-build` regains control with prior context intact. Re-running is cheap; pretending the loop works is dishonest.

### Step 7: Execute

#### Step 7.0: Parse and validate the Execution Mode block

First action after approval: read the `## Execution Mode` YAML block from the plan file.

1. Parse the fenced YAML. On parse failure, halt with a single message naming the malformed line. **Never spawn a partial team.**
2. Validate when `mode: team`: every workstream has all required fields (`name`, `subagent_type`, `scope`); every `blocked_by`/`blocks` entry resolves to a declared `name`; `len(workstreams) ≤ 4`; if `pr_strategy: one-per-workstream`, the workstream `scope` lines collectively assert disjoint files (the parser does not enforce file-glob non-overlap programmatically — it asserts the plan declared disjointness). On validation failure, halt with the failed check named.
3. Log telemetry: `crane_skill_invoked(skill_name: "auto-build", status: "started")` with metadata noting `mode`, `N`, `pr_strategy` if applicable. Best-effort; never blocks.
4. On success, fork to 6a (solo) or 6b (team).

#### Step 7a: Solo execution

Implement per the approved plan. Auto Mode is active (verified in Step 1), so:

- No confirmation prompts on routine tool calls.
- File edits, test runs, commits proceed without per-action approval.
- **Destructive actions still require explicit confirmation.** Per CLAUDE.md and the Auto Mode reminder: production systems, data deletion, force-push, secret rotation, account creation, dropping schema, modifying auth, removing access controls. Auto Mode is not a license to destroy.
- If the plan touches code in this repo, end with `npm run verify`.
- Then commit, push, and open a PR per `pr-workflow.md`.
- **Then run `/code-review:code-review {number}` against it.** That is the official plugin (reviews the diff), not our `/code-review` (standing-state audit). It scores 0-100 and posts nothing below 80, so silence is the expected result. Put whatever it does post in the handback — `/auto-build` ends at "PR open, CI green", so this informs the Captain's merge rather than blocking one.

#### Step 7b: Team execution

1. `TeamCreate` with `team_name = auto-build-{slug}` and a description summarizing the plan goal.
2. For each workstream, `TaskCreate` with `subject` and `description` (lifted from the YAML `scope` plus any plan-section detail), and `addBlockedBy`/`addBlocks` per declared dependencies.
3. **Default isolation is in-process.** Only spawn with `isolation: "worktree"` when the workstream YAML declared it explicitly. If any teammate uses worktree isolation, run the **isolation probe** (step 5 below) before issuing real task content.
4. Spawn one Agent per workstream with `team_name`, `name = {workstream-name}`, `subagent_type` (per plan), and **`mode: "acceptEdits"`** so teammates execute autonomously instead of pausing on every tool call. Captain's session continues as team lead. Auto Mode covers the Captain's session only — `mode: "acceptEdits"` on each Agent call is what gives teammates autonomous behavior. Don't drop the parameter assuming inheritance.
5. **Worktree isolation probe (only if any workstream uses `isolation: worktree`):** for each such teammate, send via `SendMessage` a one-line probe asking the agent to reply with the output of `pwd && git rev-parse --show-toplevel`. Require the response to match the expected `.claude/worktrees/<name>` path. On mismatch: shut down all teammates via `SendMessage({type: "shutdown_request"})`, call `TeamDelete`, halt with a message naming the failed teammate. This guards the P0 anti-pattern `verify-worktree-cwd-isolation-does-not-silently-fail` — silent cwd-resolves-to-parent-repo with `acceptEdits` enabled is the worst-case combination.
6. Captain's session monitors teammate messages, reassigns or unblocks tasks, gathers PR URLs.
7. On completion, gather PRs per `pr_strategy`. For `one-per-workstream`: collect each teammate's PR URL. For `bundled`: the plan must name the integrating actor (specific teammate or Captain's session); follow that. Then shut teammates via `SendMessage({type: "shutdown_request"})` and `TeamDelete`.
8. Final `npm run verify` from Captain's session if the plan touched code in this repo.
9. Telemetry: `crane_skill_invoked(skill_name: "auto-build", status: "completed")` with `duration_ms`. On any abort path above, use `status: "failed"` with `error_message`. Best-effort.

End-of-session summary: one or two sentences (per `/own-it` rule 5).

## Anti-patterns

The skill must explicitly forbid these. The Captain has flagged each one in feedback memory; do not regress:

1. **No deferral revisions.** When `/critique` raises an issue, fix it in the plan — do not file a follow-up ticket and call the plan done. (Per `feedback_critique_deferrals.md`, `feedback_kill_dont_file.md`.)
2. **No "I'll figure it out during execution."** If a step is unclear after critique, revise the plan in Step 5; do not punt to runtime.
3. **No degradation to ask-mode in Step 7.** Routine questions are yours to decide (per `/own-it` rule 1). Only stop for guardrails-gated actions (`crane_doc('global', 'guardrails.md')`).
4. **No theater.** Plan is one scrollable screen. End-of-session summary is two sentences. No ceremonial status updates mid-step.
5. **No team-for-team's-sake.** Coordination tax > parallelism gain on most single-package work. Per the heuristic in Step 4, default `solo` and only escalate when the work is genuinely independent or pattern-replicated across N targets.
6. **No unbounded fan-out.** Cap parallelism at 4 per wave. For larger N, declare batched waves via `blocks`/`blocked_by`, or run sequential `/auto-build` invocations per issue if the work is already GitHub-issue-cut. Six concurrent Agents in `acceptEdits` mode saturate the harness and the Captain's review queue.

## Notes

- **Composability:** `/auto-build` ends at "PR opened, CI green, ready to merge." It does not invoke `/ship`. If the Captain wants ship-too, they invoke `/ship` after.
- **Critique depth:** Single round per `/critique`'s own design. The Captain can re-invoke `/critique` manually if they want another round before approval.
- **Trigger ownership:** Captain-invoked, not harness-suggested. The description field reflects this. Do not lower the bar by self-suggesting `/auto-build` in conversational responses; let the Captain decide.
