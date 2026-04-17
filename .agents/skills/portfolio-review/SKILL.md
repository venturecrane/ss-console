---
name: portfolio-review
description: Portfolio Status Review
version: 1.0.0
scope: enterprise
owner: captain
status: stable
---

# /portfolio-review - Portfolio Status Review

> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "portfolio-review")`. This is non-blocking — if the call fails, log the warning and continue. Usage data drives `/skill-audit`.

Review and update venture portfolio data. Collects live signals, compares against current records, and presents changes for Captain approval.

## Status Taxonomy

| Status     | BVM Stages                  | Badge  | Criteria                                                                    |
| ---------- | --------------------------- | ------ | --------------------------------------------------------------------------- |
| `building` | IDEATION, DESIGN, PROTOTYPE | gray   | Code exists. No production deployment serving real users.                   |
| `beta`     | MARKET TEST                 | yellow | Production URL returns HTTP 200. Real users have access (even invite-only). |
| `live`     | SCALE, MAINTAIN             | green  | Generally available. No invite gate. Active users.                          |
| `paused`   | PIVOT/KILL                  | orange | Development stopped. Under evaluation. Captain decides.                     |
| `sunset`   | post-KILL                   | muted  | Shut down. Historical record.                                               |
| `internal` | any                         | blue   | Studio tooling. Not a user-facing product.                                  |

### Transition Evidence

- `building` -> `beta`: Production URL returns HTTP 200 AND at least one non-founder user has access
- `beta` -> `live`: Invite gate removed. In beta 2+ weeks with no critical bugs
- Any -> `paused`/`sunset`: Captain explicitly decides (never automated)

## Execution

### Step 1: Collect Signals

For each venture in `config/ventures.json` (all ventures, regardless of `showInPortfolio`):

1. **Real development activity** - For each repo, fetch the last 30 days of commits on main:

   ```
   gh api "repos/{org}/{repo}/commits?sha=main&since={30-days-ago-ISO}&per_page=100" --jq '.[] | {date: .commit.committer.date, message: .commit.message | split("\n")[0]}'
   ```

   Classify each commit as **real development** or **automated/infrastructure**. Automated commits match patterns like:
   - `chore: sync enterprise commands`
   - `chore: sync *` (any sync from another repo)
   - npm audit fix / security fix commits
   - Dependabot version bumps

   Report: total commits, real dev commits, last real dev commit date + message.

2. **Session history** - Use `crane_schedule(action: "session-history", days: 30)` to get session counts per venture. This shows where Captain time is actually going.

3. **Merged PR velocity** - For each repo, count PRs merged in the last 30 days:

   ```
   gh api "repos/{org}/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=100" --jq '[.[] | select(.merged_at != null and .merged_at > "{30-days-ago-ISO}")] | length'
   ```

4. **Open issues and PRs** - Use `gh api repos/{org}/{repo}` for open issue count and `gh api repos/{org}/{repo}/pulls` for open PR count.

5. **Production URL** - If `portfolio.url` is set, run `curl -s -o /dev/null -w "%{http_code}" {url}` to check HTTP status.

6. **VCMS executive summaries** - Use `crane_notes` MCP tool with `tag: "executive-summary"` to get current stage descriptions.

### Step 1b: Classify Venture Activity

Based on collected signals, classify each venture's activity level:

| Classification   | Criteria                                                                   |
| ---------------- | -------------------------------------------------------------------------- |
| **Active**       | Sessions in last 30d AND real dev commits in last 14d                      |
| **Low activity** | Real dev commits in last 30d but no sessions (e.g., one-off maintenance)   |
| **Parked**       | Zero sessions AND zero real dev commits in last 30d (only automated syncs) |

This classification is for reporting only - it does not automatically change venture status.

### Step 2: Detect Drift

Compare collected signals against current portfolio data. Flag:

- Status says `live` or `beta` but URL returns non-200 or is missing
- Status says `building` but URL is healthy (might be ready for `beta`)
- Venture classified as **parked** for a `building` venture - note but do not auto-promote to `paused` (Captain decision)
- BVM stage in VCMS doesn't match `bvmStage` in ventures.json
- `portfolio.url` being removed but URL still returns HTTP 200
- Open PR count > 20 (PR accumulation warning)
- Open issue count growing with no sessions (backlog drift)

### Step 2b: Collect Code Health

For each venture, search VCMS for the most recent `code-review` scorecard:

```
crane_notes tag="code-review" venture="{VENTURE_CODE}" limit=1
```

Extract the overall grade and review date. If no scorecard exists, mark as "-". If the review is older than 30 days, mark the grade with "(stale)".

### Step 3: Present Review Table

First, show where time is going:

```
### Where the Time Is Going

| | Sessions (30d) | Merged PRs | Real Dev Commits | Activity | Focus |
|---|---|---|---|---|---|
| Venture Crane (platform) | 27 | 71 | 85 | Active | Calendar, fleet, docs |
| SMD Services | 19 | 57 | 64 | Active | Greenfield build |
| Draft Crane | 6 | 36 | 33 | Active | Design system overhaul |
| Kid Expenses | 0 | 5 | 3 | Parked | One-off maintenance |
```

Then the status review:

```
### Honest Status Assessment

| Venture | Status | Proposed | BVM Stage | Code Health | Last Real Dev | Activity | Signals |
|---------|--------|----------|-----------|-------------|---------------|----------|---------|
| Kid Expenses | building | building | PROTOTYPE | B (stale) | Mar 15 | Parked | 54 issues, 15 PRs |
| Draft Crane | building | building | PROTOTYPE | B | 2d ago | Active | 21 issues, 6 PRs |
```

"Last Real Dev" must be the date of the last non-automated commit. Never use `pushed_at` or include automated syncs in this column.

If any drift was detected, display it prominently:

```
### Drift Detected
- Silicon Crane: Parked (0 sessions, 0 real dev commits in 30d)
- crane-console: 27 open PRs - cleanup pass needed
```

### Step 4: Captain Approval

Ask the Captain:

**"Approve these portfolio statuses?"**

Options:

- **"Approve all"** - Accept all proposed statuses as shown
- **"Review individually"** - Walk through each venture one by one
- **"Skip"** - No changes this review

If "Review individually": for each venture, show current vs proposed status and ask Captain to confirm or override.

### Step 5: Publish Updates

After Captain approves:

**Step A: Update crane-console**

1. Update `config/ventures.json`:
   - Set `lastPortfolioReview` to today's date (YYYY-MM-DD)
   - Update any portfolio fields the Captain changed (status, bvmStage, description, url, etc.)
2. Commit: `chore: portfolio review {date}`
3. Push to main

**Step B: Update vc-web**

1. Read the updated `config/ventures.json`
2. Update `vc-web/src/data/ventures.ts`:
   - Sync venture statuses, descriptions, techStack, and URLs
   - Update `lastReviewed` date
   - Only include ventures where `showInPortfolio: true`
3. Commit: `chore: sync portfolio data from review {date}`
4. Push to main

These are independent git operations. If Step B fails, report it and the Captain can re-run. `ventures.json` is the source of truth; `ventures.ts` is a derivative.

After both git pushes complete, record the completion in the Cadence Engine:

```
crane_schedule(action: "complete", name: "portfolio-review", result: "success", summary: "Portfolio reviewed and published")
```

### Step 6: Align VCMS (if needed)

If any BVM stage or description changed, update the corresponding VCMS executive summary using `crane_note` MCP tool with action `update`.

## Notes

- Never auto-promote to `paused` or `sunset` - those are Captain-only decisions
- Signals are ephemeral - collected live, displayed once, not persisted
- `config/ventures.json` is the single source of truth for portfolio data
- `vc-web/src/data/ventures.ts` is a derivative that can be regenerated anytime
- **Never use GitHub's `pushed_at` field for activity reporting.** It reflects pushes to any branch (including fleet dispatch and automated syncs) and is misleading. Always use main branch commit history with automated commits filtered out.
- **"Last commit" must mean "last real development commit."** Automated syncs, Dependabot bumps, and CI-triggered commits do not count as development activity.
- The report should answer: "Is time allocation aligned with venture priority?" not just "what happened in git?"
