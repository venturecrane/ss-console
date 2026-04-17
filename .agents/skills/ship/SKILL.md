---
name: ship
description: Ship to Production
version: 1.0.0
scope: enterprise
owner: captain
status: stable
---

# /ship - Ship to Production

> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "ship")`. This is non-blocking — if the call fails, log the warning and continue. Usage data drives `/skill-audit`.

Commit, push, PR, CI, merge, and confirm deployment - all in one shot. Follows enterprise rules (branch, PR, CI) but executes the full pipeline without stopping.

```
/ship                    # ship all staged/unstaged changes
/ship --msg "fix nav"    # custom commit message
```

## Step 1: Pre-flight

1. Run `npm run verify`. If it fails, fix the issues. Do not proceed with failing verification.
2. Run `git status` to identify all changed files (staged + unstaged + untracked).
3. Run `git log --oneline -5` to see recent commit style.
4. Run `git branch --show-current` to check the current branch.

If there are no changes to ship, stop: "Nothing to ship."

## Step 2: Stage and Commit

1. Stage all modified and new files that are part of the feature work. Exclude:
   - `.claude/handoff.md`
   - `.mcp.json`
   - `.env*` files
   - Any file that looks like local config or secrets
2. Draft a conventional commit message from the diff. If `$ARGUMENTS` includes `--msg`, use that as the summary line instead.
3. Commit. Include the co-author line.

If the pre-commit hook fails, fix the issue and create a NEW commit (never amend).

## Step 3: Branch and Push

1. If on `main`, create a feature branch first:
   - Derive the branch name from the commit message: `feat/short-description` or `fix/short-description`
   - `git checkout -b {branch}`
2. Push: `git push -u origin {branch}`

If the pre-push hook fails (verification), fix and retry once. If it fails again, stop and report.

## Step 4: Create PR

Create the PR with `gh pr create`:

```bash
gh pr create --title "{commit summary}" --body "$(cat <<'EOF'
## Summary
{2-3 bullet points from the diff}

## Test plan
- [ ] `npm run verify` passes
- [ ] {context-specific test steps}

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

## Step 5: Wait for CI

Poll CI status every 15 seconds, up to 5 minutes:

```bash
gh pr checks {number} --watch --fail-fast
```

- If CI passes: proceed to merge.
- If CI fails on a check that is NOT related to this PR's changes (e.g., pre-existing NPM Audit warnings, Dependabot alerts), proceed to merge.
- If CI fails on a check related to this PR's changes: fix, commit, push, and wait again. Max 2 fix attempts.

## Step 6: Merge

```bash
gh pr merge {number} --squash --delete-branch
```

Then switch back to main and pull:

```bash
git checkout main && git pull
```

## Step 7: Confirm Deployment

Check for Vercel deployment status:

```bash
gh pr view {number} --json comments --jq '.comments[] | select(.author.login == "vercel[bot]") | .body' 2>/dev/null | head -5
```

Report the result:

```
Shipped. PR #{number} merged to main.
Deployment: {vercel preview URL or "check Vercel dashboard"}
```

## Error Handling

- If ANY step fails after 2 retry attempts, stop and report exactly what failed and where things stand (branch pushed? PR created? CI status?).
- Never force-push. Never skip hooks. Never push directly to main.
- If there are merge conflicts with main, rebase and retry once.

## Notes

- This command auto-merges. No confirmation pause.
- Pre-existing CI failures (NPM Audit, Dependabot) are not blockers.
- The command handles the "already on a feature branch" case (skips branch creation).
- Safe to run repeatedly - if there's nothing to commit, it skips to the push/PR/merge steps.
