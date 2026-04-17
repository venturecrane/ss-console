---
name: sprint
description: Sequential sprint execution of GitHub issues on separate branches
version: 1.0.0
scope: enterprise
owner: captain
status: stable
---

# /sprint - Sequential sprint execution

> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "sprint")`. This is non-blocking — if the call fails, log the warning and continue. Usage data drives `/skill-audit`.

Takes a set of pre-selected GitHub issue numbers, builds an optimal wave-based execution plan, and implements them sequentially on separate branches. The prior step (human or planning agent) selects which issues go into the sprint. This skill handles execution.

Works in any venture console repo.

## Arguments

```
sprint <issue numbers> [--dry-run] [--parallel N]
```

- Issue numbers: space or comma-separated, `#` prefix optional
- `--dry-run`: show wave plan only, do not execute
- `--parallel N`: ignored in Codex mode (sequential execution), but preserved for argument compatibility

Parse the arguments provided by the user:

1. Split on whitespace and commas
2. Strip `#` prefix from any token
3. Extract `--dry-run` flag if present
4. Extract `--parallel N` if present (note: execution is sequential regardless)
5. Remaining tokens are issue numbers (must be positive integers)

If no issue numbers remain, display usage and stop:

```
Usage: sprint <issue numbers> [--dry-run]

Examples:
  sprint 42 45 51              # execute issues 42, 45, 51
  sprint 42, 45 --dry-run      # show plan only
```

Store: `ISSUE_NUMBERS` (array), `DRY_RUN` (bool).

## Execution

### Step 1: Detect Repo

**Repo context:**

```bash
basename "$(pwd)"          # e.g., ke-console
git remote get-url origin  # e.g., git@github.com:kidexpenses/ke-console.git
```

- `VENTURE_CODE`: basename of cwd minus `-console` suffix
- `REPO`: extract `org/repo` from the git remote URL
- `REPO_ROOT`: absolute path of the repo root (`git rev-parse --show-toplevel`)
- `VERIFY_COMMAND`: read the repo's CLAUDE.md and extract the verify command (typically `npm run verify`)

If not in a git repo, stop: "Not in a recognized repo. Run sprint from a venture console directory."

Display:

```
Sprint for {REPO}
Mode: Sequential execution
```

### Step 2: Fetch and Analyze Issues

For each issue number, fetch via:

```bash
gh issue view {N} --repo {REPO} --json number,title,body,labels,state
```

For each issue, extract from labels:

- **Priority**: `prio:*` label (P0/P1/P2/P3)
- **Component**: `component:*` label
- **Type**: `type:*` label
- **Status**: `status:*` label

Also extract from body:

- **AC count**: count checkbox items (`- [ ]` lines)
- **Body length**: word count

**Validation:**

- If any issue does not exist, stop: `Issue #{N} not found in {REPO}. Aborting.`
- If any issue has `state: closed`, stop: `Issue #{N} is closed. Remove it and re-run.`
- If any issue lacks `status:ready`, warn (do not block): `Warning: Issue #{N} has status:{current} instead of status:ready.`

Display issue summary table:

```
Issues:
| #   | Title                | Priority | Type  | Component     | Status | ACs | Body Words |
| --- | -------------------- | -------- | ----- | ------------- | ------ | --- | ---------- |
| 45  | Fix balance calc     | P0       | bug   | ke-api        | ready  | 4   | 120        |
```

### Step 3: Build Wave Plan

**Extract explicit dependencies:**

Scan each issue body (case-insensitive) for: `depends on #N`, `blocked by #N`, `after #N`, `requires #N`.

Build a dependency graph. Only track dependencies within the sprint issue set. Warn about external dependencies.

**Schedule waves:**

```
available = issues with no unresolved in-sprint deps
waves = []
completed = set()

while unscheduled issues remain:
    wave = available issues sorted: P0 first, then P1, P2, P3, then by issue number
    waves.append(wave)
    completed.update(wave issues)
    recompute available
```

If a cycle is detected, stop: `Dependency cycle detected among issues: #{A}, #{B}. Fix issue dependencies and re-run.`

**Advisory warnings:** If two issues in the same wave share a `component:*` label, warn about potential file conflicts.

**Display the wave plan.** For sequential execution, all issues in a wave are executed one at a time. Multiple waves indicate dependency ordering.

**If `DRY_RUN` is true: stop here.** Display "Dry run complete. Re-run without --dry-run to execute."

### Step 4: Approval

Ask the user: "Execute this sprint? ({N} issues will be implemented sequentially)"

Options: "Execute" / "Abort"

If Abort, stop.

### Step 5: Execute Issues Sequentially

For each issue, in wave order (Wave 1 first, then Wave 2, etc.), and within each wave in priority order:

**5a. Create branch**

Branch naming: `{issue-number}-{slugified-title}` where slugified-title is the issue title lowercased, spaces replaced with hyphens, non-alphanumeric characters removed, truncated to 50 chars, trailing hyphens stripped.

```bash
git checkout main
git pull origin main
git checkout -b {branch-name}
```

If the branch already exists, ask the user: reuse the existing branch or create with a `-2` suffix.

**5b. Implement the issue**

Execute the following steps for each issue:

1. Read `CLAUDE.md` for project conventions and build commands.
2. Explore the relevant code. Read nearby files to understand patterns.
3. Implement the change. Make minimal, focused changes. Do not refactor unrelated code.
4. Run verification:
   ```bash
   {VERIFY_COMMAND}
   ```
   Fix failures and re-run. If you cannot pass after 3 attempts, STOP and report the failure. Do NOT open a PR with failing verification.
5. Stage specific changed files (not `git add -A`), commit with a conventional message referencing the issue number.
6. Push: `git push -u origin {branch-name}`
7. Open PR:

   ```bash
   gh pr create --repo {REPO} --base main \
     --head {branch-name} --title "{type}: {description}" \
     --body "## Summary
   {1-2 sentence summary}

   ## Changes
   - {change 1}
   - {change 2}

   ## Test Plan
   - [ ] {test step 1}
   - [ ] {test step 2}

   Closes #{NUMBER}

   Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
   ```

**5c. Record result**

Track for each issue: PR URL (or FAILED with reason), files changed, verify status, decisions made.

**5d. Update labels**

For each successful PR:

```bash
gh issue edit {NUMBER} --repo {REPO} --remove-label "status:ready" --add-label "status:review"
```

**5e. Handle failures**

If an issue fails, ask the user: "Issue #{N} failed: {reason}. Retry or skip?"

- **Retry**: Delete the branch, recreate from main, try again. One retry max per issue. Second failure is final.
- **Skip**: Mark as incomplete, continue to next issue.

**5f. Return to main**

After each issue (success or failure), switch back to main:

```bash
git checkout main
```

### Step 6: Report

Display results:

```
Sprint Complete: {succeeded}/{total} issues

| #   | Title              | Status  | PR    |
| --- | ------------------ | ------- | ----- |
| 45  | Fix balance calc   | SUCCESS | #67   |
| 42  | Add expense filter | SUCCESS | #68   |
| 47  | Update docs        | FAILED  | -     |
```

If there were multiple waves and later waves remain unexecuted:

```
Remaining waves: {count} (issues: #{A}, #{B})
Next: merge the PRs above, then run: sprint {remaining issue numbers}
```

Done.

---

## Notes

- **Sequential execution**: Issues are implemented one at a time on separate branches from main. This avoids worktree complexity while maintaining isolation.
- **Single-wave execution**: Only Wave 1 is executed per run. User merges those PRs, then re-runs for Wave 2.
- **Branches from main**: Every branch starts from current main. PRs are independently reviewable.
- **Retry semantics**: Failed issues get a fresh branch from main. One retry max per issue. Second failure is final.
- **No complexity estimation**: Issue metadata (AC count, body length) is displayed for human judgment. No S/M/L/XL heuristics.
- **Time awareness**: Small issues (single file, clear fix) should aim for under 10 minutes. Medium (multi-file feature) under 20 minutes. Large (cross-cutting) under 30 minutes.
