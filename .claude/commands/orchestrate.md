# /orchestrate - Fleet Sprint Orchestrator

Dispatches coding tasks across fleet machines using `crane` headless mode, monitors progress, and collects results. Extends `/sprint` (single-machine, local worktrees) to multi-machine fleet execution.

> **When to use fleet vs local?** See `docs/fleet-decision-framework.md` for the decision matrix.

## Arguments

```
/orchestrate <issue numbers> --repo <org/repo> [--venture <code>] [--dry-run] [--resume <sprint_id>]
```

- Issue numbers: space or comma-separated, `#` prefix optional
- `--repo <org/repo>`: target repository (required)
- `--venture <code>`: venture code (default: detect from repo name)
- `--dry-run`: show dispatch plan only, do not execute
- `--resume <sprint_id>`: resume a previous orchestration

Parse `$ARGUMENTS`:

1. Split on whitespace and commas
2. Strip `#` prefix from any token
3. Extract `--repo <value>` (required)
4. Extract `--venture <value>` (optional)
5. Extract `--dry-run` flag if present
6. Extract `--resume <sprint_id>` if present
7. Remaining tokens are issue numbers (must be positive integers)

If `--resume` is provided, skip to the Resume Logic section.

If no issue numbers remain after parsing (and no `--resume`), display usage and stop:

```
Usage: /orchestrate <issue numbers> --repo <org/repo> [--venture <code>] [--dry-run] [--resume <sprint_id>]

Examples:
  /orchestrate 42 45 51 --repo venturecrane/dc-console
  /orchestrate 42 45 --repo venturecrane/dc-console --dry-run
  /orchestrate --resume sprint_01ABC123
```

Store: `ISSUE_NUMBERS` (array), `REPO` (string), `VENTURE` (string), `DRY_RUN` (bool), `RESUME_ID` (string or null).

## Execution

### DISPATCH Phase

#### Step 1: Fetch and Analyze Issues

For each issue number, fetch via:

```bash
gh issue view {N} --repo {REPO} --json number,title,body,labels,state
```

**CRITICAL**: Make all `gh issue view` calls in parallel.

For each issue, extract from labels:

- **Priority**: `prio:*` label (P0/P1/P2/P3)
- **QA grade**: `qa-grade:*` label
- **Component**: `component:*` label

Also extract dependencies from body (same logic as `/sprint`):

- `depends on #N`, `blocked by #N`, `after #N`, `requires #N`

**Validation:**

- If any issue does not exist, stop: `Issue #{N} not found in {REPO}. Aborting.`
- If any issue has `state: closed`, stop: `Issue #{N} is closed. Remove it and re-run.`

Display issue summary table.

#### Step 2: Build Wave Plan

Same wave planning logic as `/sprint`:

- Extract explicit dependencies
- Schedule waves respecting dependencies and MAX_CONCURRENT per machine
- Detect cycles

For fleet orchestration, MAX_CONCURRENT per wave is the sum of available machine slots (see Step 3).

#### Step 2b: Pre-flight CI Check

Before dispatching any tasks, verify CI passes on `origin/main`. SSH to one healthy fleet machine and run the verify command in a temporary worktree:

```bash
ssh {machine} "cd ~/dev/{REPO_NAME} && git fetch origin main 2>&1 && git worktree add /tmp/preflight-$$ origin/main 2>/dev/null && cd /tmp/preflight-$$ && npm ci --prefer-offline > /dev/null 2>&1 && {VERIFY_COMMAND}; EXIT=$?; cd ~/dev/{REPO_NAME} && git worktree remove --force /tmp/preflight-$$ 2>/dev/null; exit $EXIT"
```

If the verify command fails, display the failures and ask the Captain using AskUserQuestion:

**"CI is broken on main. Fix first, or override and dispatch anyway?"**

Options: "Fix first (abort)" / "Override and dispatch"

- **Fix first**: Stop orchestration. Display: `CI broken on main. Fix before dispatching.`
- **Override**: Continue with a warning: `Warning: Dispatching despite CI failures on main. Agents may encounter pre-existing failures.`

#### Step 2c: Shared-File Overlap Detection

After fetching issues, scan issue bodies for file path references (patterns like `src/...`, `*.css`, `*.ts` paths). Also check if issue titles/bodies mention the same component names.

If 2+ issues in the same wave reference the same file, display a warning:

```
Warning: Potential merge conflict risk
  - {filename} referenced by #{A}, #{B}
Consider: Merge in order of fewest shared-file touches first, or split into separate waves.
```

This is advisory - it does not block dispatch.

#### Step 3: Fleet Health Check and Machine Assignment

**Available machines** (orchestrator host mac23 excluded from worker pool by default):

| Hostname | Max Concurrent |
| -------- | -------------- |
| m16      | 3              |
| mini     | 2              |
| mbp27    | 2              |
| think    | 1              |

For each machine, run a health check via `crane_fleet_dispatch` dry-run equivalent:

```bash
ssh -o ConnectTimeout=5 -o BatchMode=yes {machine} "echo ok && df -BG / | awk 'NR==2{print \$4}'"
```

Exclude unhealthy machines. If no machines are healthy, stop:

```
No healthy fleet machines available. Fix connectivity and retry.
```

**Check reliability scores:**

Read `~/.crane/fleet-reliability.json` (if it exists) and compute each machine's success rate: `successes / dispatches * 100`. Machines below 70% success rate are deprioritized - assign them work only when higher-reliability machines are full. Display scores alongside health status.

**Assign issues to machines:**

Round-robin across healthy machines, respecting per-machine concurrency limits and reliability scores. Priority order: P0 first, then P1, P2, P3. Prefer machines with higher reliability scores.

Display the assignment plan:

```
Fleet Dispatch Plan:

Wave 1:
  #{45} [P0] Fix balance calc -> m16
  #{42} [P1] Add expense filter -> mini
  #{47} [P2] Update docs -> mbp27

Wave 2 (after Wave 1 merges):
  #{48} [P1] Refactor auth -> m16

Machines: 3 healthy (m16: 1/3, mini: 1/2, mbp27: 1/2)
```

#### Step 4: Write Sprint Cache

Generate a sprint ID: `sprint_{ULID-style}` (use `crypto.randomUUID()` to generate).

Write to `~/.crane/sprints/{id}.json`:

```json
{
  "id": "sprint_abc123",
  "venture": "dc",
  "repo": "venturecrane/dc-console",
  "current_wave": 1,
  "wave_plan": [[42, 45, 47], [48]],
  "dispatched": {},
  "created_at": "2026-02-20T10:00:00Z",
  "updated_at": "2026-02-20T10:00:00Z"
}
```

#### Step 5: Captain Approval

If `DRY_RUN` is true, stop: `Dry run complete. Re-run without --dry-run to execute.`

Ask the user using AskUserQuestion:

**"Execute Wave {N}? ({count} tasks across {machine_count} machines)"**

Options: "Execute" / "Abort"

If Abort, stop: `Orchestration aborted.`

#### Step 6: Dispatch

For each issue in the current wave, call `crane_fleet_dispatch`:

```
crane_fleet_dispatch({
  machine: "{assigned_machine}",
  venture: "{VENTURE}",
  repo: "{REPO}",
  issue_number: {N},
  branch_name: "{issue-number}-{slugified-title}"
})
```

Branch naming follows the same convention as `/sprint`: `{issue-number}-{slugified-title}`, truncated to 50 chars.

**CRITICAL**: Make all `crane_fleet_dispatch` calls in parallel (one message).

Update the sprint cache with dispatch metadata:

```json
{
  "dispatched": {
    "42": { "machine": "m16", "task_id": "task_abc123" },
    "45": { "machine": "mini", "task_id": "task_def456" }
  }
}
```

Display: `Wave {N} dispatched. Entering monitor loop.`

### MONITOR Phase

Poll loop with exponential backoff: start at 30s, max 5 min, back off when no status changes detected.

For each dispatched task in the current wave:

```
crane_fleet_status({
  machine: "{machine}",
  task_id: "{task_id}"
})
```

**CRITICAL**: Make all `crane_fleet_status` calls in parallel.

Track status transitions:

- `running` -> continue monitoring
- `success` (result.json has `status: success`) -> task complete
- `failed` (result.json has `status: failed`) -> task failed
- `crashed` (PID dead, no result.json) -> task failed
- `not_found` -> warn, continue checking

**Stuck detection**: If a task has been running for >10 minutes with no result.json, flag it:

```
Warning: Task {task_id} on {machine} for issue #{N} has been running for {minutes}min. Potentially stuck.
```

Display progress on each poll:

```
Monitor [2min elapsed]:
  #{42} on m16: running (5min)
  #{45} on mini: success - PR #260
  #{47} on mbp27: running (3min)
```

Loop until all tasks are terminal (success, failed, or crashed) or Captain intervenes.

### COLLECT Phase

#### Step 1: PR Status Check

For successful tasks, get PR and CI status:

```
crane_fleet_status({
  repo: "{REPO}",
  issue_numbers: [{successful_issue_numbers}]
})
```

Display PR summary:

```
Results:

| #   | Title              | Status  | PR    | CI      |
| --- | ------------------ | ------- | ----- | ------- |
| 42  | Fix balance calc   | SUCCESS | #260  | passing |
| 45  | Add expense filter | SUCCESS | #261  | pending |
| 47  | Update docs        | FAILED  | -     | -       |
```

#### Step 1b: Record Reliability Outcomes

For each task in the wave, record its outcome for machine reliability tracking. Update `~/.crane/fleet-reliability.json` by reading the file, incrementing the appropriate counter for the machine, and writing it back:

- Task succeeded (has result.json with `status: success`) -> increment `successes`
- Task failed (has result.json with `status: failed`) -> increment `failures`
- Task crashed (PID dead, no result.json) -> increment `crashes`

This data feeds back into Step 3 of the DISPATCH phase for future sprints.

#### Step 2: Handle Failures

For each failed task, ask the user using AskUserQuestion:

**"Issue #{N} failed: {reason}. Retry or skip?"**

Options: "Retry" / "Skip"

- **Retry**: Call `crane_fleet_dispatch` again for this issue on the same machine (or a different one if the original is unhealthy). Max 1 retry per issue. Update sprint cache.
- **Skip**: Mark as skipped, continue.

If retrying, loop back to MONITOR for just the retried tasks.

#### Step 3: Captain Merge Approval

Once all PRs have passing CI (or Captain decides to proceed), ask:

**"Merge {N} PRs? (squash merge in dependency order)"**

Options: "Merge all" / "Select which to merge" / "Skip merge"

- **Merge all**: For each successful PR in dependency order:

  ```bash
  gh pr merge {PR_NUMBER} --repo {REPO} --squash
  ```

  If a merge fails (conflict), stop and escalate:

  ```
  Merge conflict on PR #{N}. Resolve manually and re-run with --resume {sprint_id}.
  ```

- **Select**: Ask which PRs to merge, then merge those in order.
- **Skip**: Leave PRs open for manual review.

#### Step 4: Update Labels and Cleanup

For each successfully merged PR:

```bash
gh issue edit {N} --repo {REPO} --remove-label "status:ready" --add-label "status:done"
```

For each successful but unmerged PR:

```bash
gh issue edit {N} --repo {REPO} --remove-label "status:ready" --add-label "status:review"
```

Clean up worktrees on target machines (best-effort):

```bash
ssh {machine} "cd {repo_path} && git worktree remove --force ~/.crane/tasks/{task_id}/worktree 2>/dev/null; rm -rf ~/.crane/tasks/{task_id}"
```

#### Step 5: Advance to Next Wave

If more waves remain in the wave plan:

1. Update sprint cache: `current_wave += 1`
2. Display: `Wave {N} complete. Advancing to Wave {N+1}.`
3. Loop back to DISPATCH Step 3 (fleet health check) with the next wave's issues.

If all waves complete:

```
Orchestration complete.

Sprint: {sprint_id}
Waves: {total}
Issues: {succeeded}/{total} succeeded
PRs merged: {merged_count}
```

Remove sprint cache file.

## Resume Logic (`--resume`)

When `--resume <sprint_id>` is provided:

1. **Read sprint cache**: Load `~/.crane/sprints/{sprint_id}.json` for dispatch metadata.

   If file not found, stop: `Sprint {sprint_id} not found. Check ~/.crane/sprints/`

2. **Query GitHub for actual state**: For all issues in the sprint's wave plan:

   ```
   crane_fleet_status({
     repo: "{repo}",
     issue_numbers: [{all_issue_numbers}]
   })
   ```

3. **Query fleet for task state**: For all dispatched tasks:

   ```
   crane_fleet_status({
     machine: "{machine}",
     task_id: "{task_id}"
   })
   ```

4. **Reconcile state** (GitHub wins over cache):
   - PR merged -> issue is done, regardless of cache
   - PR open -> task succeeded, resume at COLLECT
   - Task running (PID alive) -> resume at MONITOR
   - Task crashed (PID dead, no result.json) -> treat as failed
   - No PR and no task running -> treat as failed

5. **Determine resume point**:
   - If all current wave PRs are merged -> advance to next wave DISPATCH
   - If PRs are open but not merged -> resume at COLLECT
   - If tasks are still running -> resume at MONITOR
   - If all current wave tasks failed -> offer retry or skip, then advance

6. Display reconciled state and resume point. Ask Captain for confirmation before continuing.

## Notes

Sprint cache schema is shown in DISPATCH Step 4. It is a **local performance cache**, NOT source of truth - GitHub is authoritative.

- **mac23 excluded from worker pool**: The orchestrator host doesn't run workers by default to avoid resource contention.
- **Fleet health checks before every wave**: Machines can go offline mid-sprint. Re-check before dispatching each wave.
- **Exponential backoff**: Monitor polling starts at 30s intervals, backs off to 5min max when no changes detected. Resets to 30s when a status change is observed.
- **GitHub as source of truth**: Resume reconstructs state from observable reality (GitHub PRs + fleet task status), not from the cache file. The cache accelerates resume but is never trusted over live state.
- **Structured results**: Sprint workers write `result.json` to their worktree on completion. The fleet status tool reads this for structured status - no log scraping.
- **Defense in depth**: Shell arguments are escaped in fleet-exec.sh and fleet-dispatch.ts. Issue bodies are passed via `gh issue view`, not inline.
- **Single retry**: Failed tasks get at most one retry. Second failure is final.
- **Merge order**: PRs are merged in dependency order (Wave 1 before Wave 2, within a wave: by issue number).
