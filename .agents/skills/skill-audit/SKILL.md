---
name: skill-audit
description: Monthly skill health report. Walks every SKILL.md, parses frontmatter, computes staleness via git log, detects schema gaps, and surfaces zero-usage candidates. Run this once a month to keep the skill library healthy.
version: 1.0.0
scope: enterprise
owner: captain
status: stable
depends_on:
  mcp_tools:
    - crane_skill_audit
    - crane_schedule
---

# /skill-audit - Monthly Skill Health Report

> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "skill-audit")`. This is non-blocking — if the call fails, log the warning and continue. Usage data drives `/skill-audit`.

Run a repo-wide audit of every SKILL.md in the enterprise and global skill libraries. The report surfaces schema gaps, stale skills, and zero-usage candidates.

## Usage

```
/skill-audit
```

No arguments required. The audit runs across all scopes by default.

## What it checks

| Section         | What it surfaces                                                                                                      |
| --------------- | --------------------------------------------------------------------------------------------------------------------- |
| **Inventory**   | Total skill count, broken down by scope, status, and owner                                                            |
| **Schema gaps** | Skills missing one or more required frontmatter fields (`name`, `description`, `version`, `scope`, `owner`, `status`) |
| **Staleness**   | Skills whose `SKILL.md` has not been touched in git for more than 180 days                                            |
| **Zero-usage**  | Skills with zero invocations in the last 90 days — retirement candidates                                              |

Reference drift (broken `depends_on.mcp_tools`, `depends_on.files`, `depends_on.commands`) is NOT included in this tool - it requires invoking the `skill-review` CLI which cross-checks against live manifests. Run `/skill-review --all` for reference-drift details.

## Workflow

### Step 1: Run the audit

Call the MCP tool:

```
crane_skill_audit()
```

Default parameters:

- `scope`: `all` (enterprise + global)
- `stale_threshold_days`: 180

You can narrow the scope:

```
crane_skill_audit(scope: "enterprise", stale_threshold_days: 90)
```

### Step 2: Interpret the report

**Inventory** - Review the totals. A healthy library has no skills in `unknown` status. Skills in `draft` for more than 30 days should be reviewed.

**Schema gaps** - Each entry names the skill and the missing fields. Schema gaps should be fixed before the skill is marked `stable`. Open a PR to fill them.

**Staleness** - Skills not touched in 180+ days may be outdated. Review each:

- If the skill is still accurate: touch the file (whitespace-only commit) to reset the clock.
- If the skill is obsolete: get Captain directive, then open a PR that deletes the SKILL.md, dispatcher, `config/skill-owners.json` entry, and any code references.

**Zero-usage candidates** - Skills with zero invocations in 90 days. Either the skill isn't calling `crane_skill_invoked` (fix the SKILL.md), or it's genuinely unused. For genuine orphans, get Captain directive and retire in a single PR.

### Step 3: Record completion

After reviewing the report and taking any required actions, record the audit as complete:

```
crane_schedule(
  action: "complete",
  name: "skill-audit",
  result: "success",
  summary: "<one-line summary, e.g. '18 skills audited, 2 stale flagged, 0 schema gaps'>"
)
```

## Cadence

This skill is on a monthly cadence (`schedule_items` name: `skill-audit`). It surfaces in the `/sos` briefing when due.

## Notes

- Staleness is derived from `git log -1 --format=%cI -- <path>`. Files never committed show as `last_touched: unknown` and are treated as maximally stale.
- Skills with `backend_only: true` still appear in the audit - they just have no dispatcher parity requirement.
- This tool does not modify any SKILL.md. It is read-only.
