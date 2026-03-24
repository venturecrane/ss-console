# /sprint - Parallel Sprint Execution

This command takes a set of pre-selected GitHub issue numbers, builds an optimal wave-based execution plan, and spawns parallel coding agents to implement them simultaneously using git worktrees for isolation.

Works in any venture console repo. The prior step (human or planning agent) selects which issues go into the sprint. This command handles execution.

> **When to use local vs fleet?** See `docs/fleet-decision-framework.md` for the decision matrix.

## Arguments

```
/sprint <issue numbers> [--dry-run] [--parallel N]
```

- Issue numbers: space or comma-separated, `#` prefix optional
- `--dry-run`: show wave plan only, do not execute
- `--parallel N`: override max concurrent agents (default: machine-based lookup)

Parse `$ARGUMENTS`:

1. Split on whitespace and commas
2. Strip `#` prefix from any token
3. Extract `--dry-run` flag if present (remove from list)
4. Extract `--parallel N` if present (remove both tokens from list)
5. Remaining tokens are issue numbers (must be positive integers)

If no issue numbers remain after parsing, display usage and stop:

```
Usage: /sprint <issue numbers> [--dry-run] [--parallel N]

Examples:
  /sprint 42 45 51              # execute issues 42, 45, 51
  /sprint 42, 45 --dry-run      # show plan only
  /sprint 42 45 --parallel 4    # override concurrency limit
```

Store: `ISSUE_NUMBERS` (array), `DRY_RUN` (bool), `MAX_CONCURRENT` (number or null).

## Execution

### Step 1: Detect Repo and Machine

**Repo context:**

Run these commands to detect context:

```bash
basename "$(pwd)"          # e.g., ke-console
git remote get-url origin  # e.g., git@github.com:kidexpenses/ke-console.git
```

- `VENTURE_CODE`: basename of cwd minus `-console` suffix (e.g., `ke-console` -> `ke`)
- `REPO`: extract `org/repo` from the git remote URL
- `REPO_ROOT`: absolute path of the repo root (`git rev-parse --show-toplevel`)
- `VERIFY_COMMAND`: read the repo's CLAUDE.md and extract the verify command (typically `npm run verify`)

If not in a git repo or can't determine the remote, stop:

```
Not in a recognized repo. Run /sprint from a venture console directory.
```

**Machine resources:**

If `--parallel N` was provided, set `MAX_CONCURRENT = N`.

Otherwise, detect hostname:

```bash
hostname -s
```

Look up the default:

| Hostname | Max Concurrent |
| -------- | -------------- |
| mac23    | 3              |
| m16      | 3              |
| mini     | 2              |
| mbp27    | 2              |
| think    | 1              |
| (other)  | 2              |

Display:

```
Sprint for {REPO}
Machine: {hostname} | Max concurrent agents: {MAX_CONCURRENT}
```

### Step 2: Fetch and Analyze Issues

For each issue number in `ISSUE_NUMBERS`, fetch via:

```bash
gh issue view {N} --repo {REPO} --json number,title,body,labels,state
```

**CRITICAL**: Make all `gh issue view` calls in parallel (multiple Bash tool calls in one message).

For each issue, extract from labels:

- **Priority**: `prio:*` label (P0/P1/P2/P3)
- **QA grade**: `qa-grade:*` or `qa:*` label
- **Component**: `component:*` label
- **Type**: `type:*` label
- **Status**: `status:*` label

Also extract from body:

- **AC count**: count checkbox items (`- [ ]` lines)
- **Body length**: word count of the issue body

**Validation:**

- If any issue does not exist or `gh issue view` fails, stop: `Issue #{N} not found in {REPO}. Aborting.`
- If any issue has `state: closed`, stop: `Issue #{N} is closed. Remove it and re-run.`
- If any issue lacks `status:ready`, warn (do not block): `Warning: Issue #{N} has status:{current} instead of status:ready.`

Display issue summary table:

```
Issues:
| #   | Title                | Priority | Type  | Component     | Status | ACs | Body Words |
| --- | -------------------- | -------- | ----- | ------------- | ------ | --- | ---------- |
| 45  | Fix balance calc     | P0       | bug   | ke-api        | ready  | 4   | 120        |
| 42  | Add expense filter   | P1       | story | ke-app        | ready  | 6   | 280        |
| 47  | Update docs          | P2       | tech  | -             | ready  | 2   | 45         |
```

### Step 3: Build Wave Plan

**Extract explicit dependencies:**

Scan each issue body (case-insensitive) for these patterns:

- `depends on #N`
- `blocked by #N`
- `after #N`
- `requires #N`

Build a dependency graph. Only track dependencies within the sprint issue set. If a dependency references an issue NOT in the sprint, display a warning:

```
Warning: #{X} depends on #{Y}, which is not in this sprint. Treating as external (resolved).
```

**Schedule waves:**

```
available = issues with no unresolved in-sprint deps
waves = []
completed = set()

while unscheduled issues remain:
    wave = []
    for issue in available (sorted: P0 first, then P1, P2, P3, then by issue number):
        if len(wave) >= MAX_CONCURRENT: break
        wave.append(issue)
    waves.append(wave)
    completed.update(wave issues)
    recompute available (deps satisfied by completed set, not already scheduled)
```

If a cycle is detected (no available issues but unscheduled remain), stop:

```
Dependency cycle detected among issues: #{A}, #{B}, #{C}. Fix issue dependencies and re-run.
```

**Advisory warnings:**

If two issues in the same wave share a `component:*` label, display:

```
Warning: Issues #{X} and #{Y} share component:{name} - may have file conflicts. Merge in listed order.
```

**Display the full wave plan:**

```
Wave Plan:

Wave 1 (execute now):
  #{45} [P0] Fix balance calc - component:ke-api
  #{42} [P1] Add expense filter - component:ke-app
  #{47} [P2] Update docs - (no component)

Wave 2 (next run, after merging Wave 1):
  #{48} [P1] Refactor auth - component:ke-api (depends on #45)

Note: Wave 2 depends on Wave 1. Merge Wave 1 PRs before re-running.
```

If there is only one wave, omit the "next run" note.

**If `DRY_RUN` is true: stop here.** Display "Dry run complete. Re-run without --dry-run to execute."

### Step 4: Approval

Ask the user using AskUserQuestion:

**"Execute Wave 1? ({N} agents will be spawned)"**

Options: "Execute" / "Abort"

If Abort, stop: "Sprint aborted. Re-run with adjusted arguments."

### Step 4b: Pre-flight CI Check

Before creating worktrees, verify CI passes on `origin/main`. Create a temporary worktree, run verify, and clean up:

```bash
cd {REPO_ROOT} && git fetch origin main 2>&1
git worktree add {REPO_ROOT}/.worktrees/_preflight origin/main 2>/dev/null
cd {REPO_ROOT}/.worktrees/_preflight && npm ci --prefer-offline > /dev/null 2>&1 && {VERIFY_COMMAND}
# Clean up regardless of result
cd {REPO_ROOT} && git worktree remove --force {REPO_ROOT}/.worktrees/_preflight 2>/dev/null
```

If the verify command fails, display the failures and ask the Captain using AskUserQuestion:

**"CI is broken on main. Fix first, or override and dispatch anyway?"**

Options: "Fix first (abort)" / "Override and dispatch"

- **Fix first**: Stop sprint. Display: `CI broken on main. Fix before dispatching.`
- **Override**: Continue with a warning: `Warning: Dispatching despite CI failures on main. Agents may encounter pre-existing failures.`

### Step 5: Set Up Worktrees

**Check for active sprint:**

```bash
cat {REPO_ROOT}/.worktrees/.sprint.lock 2>/dev/null
```

If the lock file exists, read the PID from it. Check if the process is alive:

```bash
kill -0 {PID} 2>/dev/null
```

- If alive: stop with `Another sprint is active (PID {PID}). Wait for it to finish or kill that process first.`
- If dead (stale lock): warn `Stale sprint lock found (PID {PID} is dead). Cleaning up and proceeding.` Remove the lock and any leftover worktrees.

**Create lock:**

```bash
mkdir -p {REPO_ROOT}/.worktrees
echo "$$" > {REPO_ROOT}/.worktrees/.sprint.lock
```

**Add `.worktrees/` to `.gitignore`** if not already present:

Check if `.worktrees/` or `.worktrees` appears in `{REPO_ROOT}/.gitignore`. If not, append it:

```bash
echo '.worktrees/' >> {REPO_ROOT}/.gitignore
```

**Sync gate - fetch remote main:**

Before creating any worktrees, fetch the latest remote main. Worktrees branch directly from `origin/main` to eliminate stale-base bugs and race conditions.

```bash
cd {REPO_ROOT} && git fetch origin main
```

**Create worktrees** for each Wave 1 issue:

Branch naming: `{issue-number}-{slugified-title}` where slugified-title is the issue title lowercased, spaces replaced with hyphens, non-alphanumeric characters (except hyphens) removed, truncated to 50 chars, trailing hyphens stripped.

For each issue:

```bash
git worktree add {REPO_ROOT}/.worktrees/{issue-number} -b {branch-name} origin/main
```

This creates the worktree branching directly from the remote tracking ref - no dependency on local main being up to date.

If the branch already exists, ask the user: reuse the existing branch or create with a `-2` suffix.

If the worktree path already exists, remove it first:

```bash
git worktree remove --force {REPO_ROOT}/.worktrees/{issue-number} 2>/dev/null
```

**Install dependencies** in parallel (one Bash call per worktree, all in one message):

```bash
cd {REPO_ROOT}/.worktrees/{issue-number} && npm ci --prefer-offline 2>&1 | tail -5
```

Display: `Worktrees ready. Dependencies installed.`

### Step 6: Execute Wave 1

**Spawn all agents in ONE message** using the Task tool (`subagent_type: sprint-worker`).

**CRITICAL**: All Task tool calls MUST be in a single message to run in true parallel.

Each agent receives only dynamic context. Static behavioral instructions (working directory discipline, verification workflow, result.json format, constraints) are defined in the `sprint-worker` agent definition at `.claude/agents/sprint-worker.md`.

---

**Coding Agent Prompt (dynamic context only):**

```
## Assignment
- Issue: #{NUMBER} - {TITLE}
- Worktree: {WORKTREE_PATH}
- Branch: {BRANCH_NAME} (already checked out)
- Repo: {REPO}
- Verify command: {VERIFY_COMMAND}

## Issue Details
{FULL_ISSUE_BODY}
```

---

**After all agents complete:**

Parse each agent's final message:

- Look for `PR_URL:` line to extract PR URL
- Look for `FAILED:` line to detect failure
- Extract files changed and verification status

**For each successful PR:**

Update the issue label from `status:ready` to `status:review`:

```bash
gh issue edit {NUMBER} --repo {REPO} --remove-label "status:ready" --add-label "status:review"
```

**For each failure:**

Ask the user per failed issue using AskUserQuestion:

**"Issue #{N} failed: {reason}. Retry or skip?"**

Options: "Retry" / "Skip"

- **Retry**: Remove the worktree (`git worktree remove --force ...`), fetch origin main, recreate fresh from `origin/main` (`git worktree add ... -b {branch} origin/main`), reinstall dependencies, and spawn one new `sprint-worker` agent with the same dynamic context prompt. After retry completes, process its result (update labels on success, report failure on second failure without further retry).
- **Skip**: Mark as incomplete, continue.

### Step 7: Report and Cleanup

Display wave results:

```
Wave 1 Complete: {succeeded}/{total} issues

| #   | Title              | Status  | PR    |
| --- | ------------------ | ------- | ----- |
| 45  | Fix balance calc   | SUCCESS | #67   |
| 42  | Add expense filter | SUCCESS | #68   |
| 47  | Update docs        | FAILED  | -     |
```

If there are remaining waves:

```
Remaining waves: {count} (issues: #{A}, #{B})
Next: merge the PRs above, then run: /sprint {remaining issue numbers}
```

If all waves are done:

```
All sprint issues processed.
```

**Cleanup:**

Remove worktrees and lock file:

```bash
git worktree remove --force {REPO_ROOT}/.worktrees/{N}   # for each issue
rm -f {REPO_ROOT}/.worktrees/.sprint.lock
rmdir {REPO_ROOT}/.worktrees 2>/dev/null                  # remove dir if empty
```

Branches are preserved (they back the open PRs).

Done.

---

## Notes

- **Single-wave execution**: Only Wave 1 is executed per run. User merges those PRs, then re-runs for Wave 2. This eliminates inter-wave state management and the dependency-branching problem.
- **Worktrees inside repo**: `.worktrees/` lives at the repo root and is gitignored. No external directory management needed.
- **Branches from origin/main**: Every branch starts from `origin/main` (fetched fresh before worktree creation). Eliminates stale-base bugs. PRs are independently reviewable.
- **Orchestrator owns side effects**: Label updates happen here, not in the coding agents. Agents only push code and open PRs.
- **Retry semantics**: Failed agents get a fresh worktree from `origin/main`. One retry max per failed issue. Second failure is final.
- **Lock file**: Prevents concurrent sprint runs from colliding. PID-based with stale detection.
- **Agent type**: All coding agents use `subagent_type: sprint-worker` via the Task tool. Static behavioral instructions live in `.claude/agents/sprint-worker.md`; only dynamic context (issue, worktree, branch, repo, verify command) is passed in the Task prompt.
- **Structured results**: Sprint workers write `result.json` to the worktree root on completion (success or failure). The orchestrator can read this for structured status instead of parsing agent messages.
- **Parallelism**: All Wave 1 agents launch in a single message for true parallel execution.
- **No complexity estimation**: Issue metadata (AC count, body length) is displayed for human judgment. No S/M/L/XL heuristics.
