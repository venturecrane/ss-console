# /portfolio-review - Portfolio Status Review

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

For each venture in `config/ventures.json` where `portfolio.showInPortfolio` is `true`:

1. **GitHub activity** - Use `gh api repos/{org}/{venture-code}-console` to get last push date and open issue/PR count
2. **Production URL** - If `portfolio.url` is set, run `curl -s -o /dev/null -w "%{http_code}" {url}` to check HTTP status
3. **VCMS executive summaries** - Use `crane_notes` MCP tool with `tag: "executive-summary"` to get current stage descriptions
4. **Cloudflare resources** - Use `workers_list` and `d1_databases_list` MCP tools to inventory deployed infrastructure

Also check ventures with `showInPortfolio: false` (like vc and smd) but only for basic health.

### Step 2: Detect Drift

Compare collected signals against current portfolio data. Flag:

- Status says `live` or `beta` but URL returns non-200 or is missing
- Status says `building` but URL is healthy (might be ready for `beta`)
- No commits in 30+ days for a `building` venture
- BVM stage in VCMS doesn't match `bvmStage` in ventures.json
- `portfolio.url` being removed but URL still returns HTTP 200

### Step 2b: Collect Code Health

For each venture, search VCMS for the most recent `code-review` scorecard:

```
crane_notes tag="code-review" venture="{VENTURE_CODE}" limit=1
```

Extract the overall grade and review date. If no scorecard exists, mark as "-". If the review is older than 30 days, mark the grade with "(stale)".

### Step 3: Present Review Table

Display collected data in a table:

```
### Portfolio Review - {date}

| Venture | Status | Proposed | BVM Stage | Code Health | Last Commit | URL Health | Signals |
|---------|--------|----------|-----------|-------------|-------------|------------|---------|
| Kid Expenses | building | building | PROTOTYPE | B | 2d ago | n/a | 3 open issues |
| Durgan Field Guide | building | building | PROTOTYPE | C (stale) | 5d ago | n/a | 1 open PR |
| Draft Crane | building | building | IDEATION | - | 14d ago | n/a | D1 exists |
| Silicon Crane | building | building | IDEATION | - | 30d ago | n/a | No activity |
```

If any drift was detected, display it prominently:

```
### Drift Detected
- Silicon Crane: No commits in 30+ days - consider `paused`?
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
