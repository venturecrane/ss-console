# /docs-refresh - Enterprise Docs Refresh

Review and update enterprise documentation site content at `crane-console.vercel.app`. Identifies stale pages and enriches them with data from existing sources.

## Arguments

```
/docs-refresh [scope]
```

- No argument: Audit mode - lists stale pages, does NOT modify files
- Venture code (`vc`, `dfg`, `sc`, `ke`, `dc`): Update all 3 pages for one venture
- Page type (`metrics`, `roadmaps`, `overviews`): Update one page type across all ventures
- Single page (`vc/metrics`, `dfg/roadmap`): Update one page

## Execution

### Step 1: Audit

Read all files in `docs/ventures/*/`, `docs/company/`, `docs/operations/`. Report line count, TBD count, stale flag (>2 TBDs or <20 lines).

**If no scope provided, STOP after audit. Ask Captain which scope to run.**

### Step 2: Gather Data

For pages in scope, pull from:

- **Overviews**: `config/ventures.json` + `docs/design/ventures/{code}/design-spec.md` + VCMS exec summaries
- **Metrics**: BVM stage from ventures.json + VCMS strategy/prd notes + stage-appropriate defaults
- **Roadmaps**: `gh issue list --repo venturecrane/{code}-console` + `crane_handoffs` + SOD data

### Step 3: Draft

Write updated markdown matching existing good pages (DFG/SC overviews as templates). Use real data. Preserve existing substantive content. Target: overviews 40-70 lines, metrics 25-35 lines, roadmaps 25-40 lines.

### Step 4: Approve

Present drafts to Captain. Show before/after line counts and data sources. **Wait for approval.**

### Step 5: PR

Branch `docs/refresh-{scope}-{date}`, write files, verify `cd site && npm run build`, commit, push, `gh pr create`.

## Notes

- Template variables (`{{portfolio:table}}`) are handled by the build script — don't duplicate that data
- After merge, Vercel auto-rebuilds the site
- Quality bar: 0 unjustified TBDs, 20+ lines, real data
