---
name: context-refresh
description: Enterprise Context Refresh
version: 1.0.0
scope: enterprise
owner: captain
status: stable
---

# /context-refresh - Enterprise Context Refresh

> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "context-refresh")`. This is non-blocking — if the call fails, log the warning and continue. Usage data drives `/skill-audit`.

Audit and update all enterprise context: D1 docs, VCMS executive summaries, and venture metadata. Produces a refresh report and records cadence completion.

## Arguments

```
/context-refresh [venture-code | --audit-only]
```

- No argument: Full audit + fix across all ventures
- Venture code (e.g., `vc`): Audit + fix for a single venture
- `--audit-only`: Report only, no changes

## Execution

### Step 1: Audit Documentation

Run the doc audit to get the full status matrix:

- **All ventures**: `crane_doc_audit(all: true)`
- **Single venture**: `crane_doc_audit(venture: "{code}")`

Display the audit results showing present, missing, and stale docs per venture.

### Step 2: Auto-Regenerate Stale and Missing Docs

Unless `--audit-only` is set:

- **All ventures**: `crane_doc_audit(all: true, fix: true)`
- **Single venture**: `crane_doc_audit(venture: "{code}", fix: true)`

This regenerates all auto-generable missing AND stale docs. The content-hash guard prevents no-op uploads when source files haven't changed.

Report which docs were generated, refreshed, unchanged, or failed.

### Step 3: Check Executive Summary Freshness

For each venture (or the specified venture), check the executive summary age:

```
crane_notes(tag: "executive-summary", venture: "{code}")
```

Flag any summary older than 30 days as stale. For stale summaries:

1. Gather current data:
   - Read `config/ventures.json` for portfolio metadata (status, BVM stage, tagline, tech stack)
   - Check recent handoffs for the venture (last 3 from SOS continuity)
   - Check recent VCMS notes tagged `prd` or `strategy` for the venture
2. Draft an updated executive summary
3. **Present the draft to Captain for approval before saving**
4. If approved, update via `crane_note(action: "update", id: "{note_id}", content: "...")`

### Step 4: Verify ventures.json Accuracy

Read `config/ventures.json`. For each venture:

- Check that `capabilities` match reality (look for `workers/*/src` for `has_api`, `migrations/` for `has_database`)
- Check that `portfolio.bvmStage` matches the executive summary
- Check that `repos` list is current

Flag any drift but do NOT auto-fix - present findings to Captain.

### Step 5: Display Refresh Report

```
## Context Refresh Report

| Venture | Docs | Exec Summary | Drift |
|---------|------|--------------|-------|
| vc      | 3/3 refreshed | Updated (was 49d old) | None |
| sc      | 3/3 unchanged | Stale (49d) - pending approval | None |
...

### Summary
- Docs: N generated, M refreshed, K unchanged, J failed
- Exec Summaries: X updated, Y pending approval, Z current
```

### Step 6: Record Cadence Completion

```
crane_schedule(action: "complete", name: "context-refresh", result: "{success|warning|failure}", completed_by: "crane-mcp-{hostname}", summary: "{brief outcome}")
```

Result mapping:

- `success`: All docs refreshed, all exec summaries current or updated
- `warning`: Some docs failed or exec summaries still stale (pending approval)
- `failure`: Audit or regeneration failed entirely

## Rules

- Auto-regenerated docs (machine-derived from source files) upload without approval
- Executive summaries ALWAYS require Captain approval before update - never auto-save
- Must run from crane-console (needs local repo access for all ventures via `~/dev/{code}-console`)
- Do NOT modify `config/ventures.json` without Captain approval
- Do NOT create GitHub issues for drift findings - just report them
