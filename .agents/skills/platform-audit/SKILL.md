---
name: platform-audit
description: Platform Audit
version: 1.0.0
scope: enterprise
owner: captain
status: stable
---

# /platform-audit - Platform Audit

> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "platform-audit")`. This is non-blocking — if the call fails, log the warning and continue. Usage data drives `/skill-audit`.

End-to-end senior-engineer audit of the crane operating system. Catches sprawl, dead code, incomplete migrations, and accumulated cruft before they compound. Produces a kill / fix / invest list a Captain can act on.

## Scope

**In scope (the support OS):**

- `workers/` (crane-context, crane-mcp-remote, crane-watch)
- `packages/` (crane-mcp, crane-test-harness)
- `.agents/skills/`, `.agents/agents/`, `.claude/settings.json` hooks
- `CLAUDE.md`, `AGENTS.md`, `GEMINI.md` instruction files
- The MCP tool surface (local + remote)
- D1 schemas in `workers/crane-context/migrations/`
- `docs/` tree
- `crane_docs` uploaded documents (via `crane_doc_audit`)
- `MEMORY.md` and satellite files
- `scripts/` shell scripts
- `.github/workflows/`

**Out of scope:**

- Individual venture product codebases (vc-web, dc-marketing, dfg-console, sc-console, kidexpenses) - those are `/code-review` or `/enterprise-review`.
- Anything outside the crane-console repo.

## Process

### 1. Recon (inline, cheap)

- `ls` of `workers/`, `packages/`, `scripts/`, `.agents/`, `docs/`, `.github/workflows/`
- Count skills, migrations, scripts, workflows
- `git log --oneline -20` for recent activity

### 2. Spawn 6 parallel Explore agents

Launch all six in a single message. Each gets a focused brief and reports under 1500 words. Each must end with kill / fix / keep lists with file paths and line numbers.

| #   | Domain                    | Look for                                                                                                                                                                                                                                           |
| --- | ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | **Workers & packages**    | God files (>500 LOC), dead exports, duplication across workers (esp. GitHub signature validation, HTTP clients, auth), layer violations, `console.log` in production paths, test pathology (over-mocking, test files larger than source)           |
| 2   | **Skills, agents, hooks** | Skill overlap (lifecycle / review / editorial clusters), AGENTS.md vs GEMINI.md drift, settings.json deny/allow precedence, skill bloat (SKILL.md >10KB), CLAUDE.md content that should be in fetched docs                                         |
| 3   | **MCP tool surface**      | Schema token cost at session start, action-enum sprawl in single tools (red flag: tool with >5 actions), dead tools (grep for usage), local-vs-remote MCP drift                                                                                    |
| 4   | **D1 datastores**         | Dead tables (defined in schema, never written), schema sprawl (tables with 25+ columns), missing indices on hot paths, vague `meta_json`/`details_json` columns, migration history smells (drop-and-rebuild patterns, missing baseline migrations) |
| 5   | **Documentation**         | Stale subdirs (>30 days), contradictions (same fact in 3 places with 3 versions), fragmented bootstrap instructions, `crane_docs` manifest health, `MEMORY.md` sprawl, orphaned handoffs                                                           |
| 6   | **Scripts & ops**         | Bootstrap script overlap, incomplete migrations (find legacy + new running in parallel), dead scripts (verify with grep), failing GitHub Actions workflows (root cause, not just "it's red")                                                       |

### 3. Synthesize as a senior-engineer report

Required sections:

- **TL;DR** - 3-4 sentences
- **Critical / fix this week** - 5-10 items max with file:line refs and exact fixes
- **Inventory** - sized table
- **Findings by domain** - condensed (don't repeat the deep-dive agent reports verbatim)
- **Cross-cutting themes** - patterns across domains (incomplete migrations, inline duplication, god files, hardcoded indexes, etc.)
- **Kill list / Fix list / Invest list**
- **Risk assessment**
- **What was deliberately not audited**

### 4. Save the report

Write to `docs/reviews/{YYYY-MM-DD}-platform-audit.md`. Use the actual current date.

### 5. Compare to the prior audit

Find the most recent prior audit: `ls -t docs/reviews/*-platform-audit.md`. If one exists, surface:

- **Still on the list** - items that haven't been addressed (this is the most important section)
- **New** - sprawl that accumulated since last audit
- **Resolved** - progress

If no prior audit exists on disk, check branch `audit/platform-audit-skill-and-report` for the original 2026-04-11 report: `git show audit/platform-audit-skill-and-report:docs/reviews/2026-04-11-platform-audit.md`

### 6. Record completion

Log to the Cadence Engine:

```
crane_schedule(action: "complete", name: "Platform Audit", result: "success", summary: "{grade or headline}", completed_by: "crane-mcp")
```

### 7. Offer to act on findings

Ask the Captain whether to:

1. File the critical-list items as GitHub issues (with appropriate labels)
2. Open a PR for the mechanical kill-list deletions
3. Drill into any specific finding

## Honesty bar

Be unflinching. Name files, line numbers, and specific anti-patterns. Acknowledge what's well-built - but the point is to catch problems early, not to write a flattering report. The Captain wants the version a senior team would deliver in an out-brief.

## Rules

- Must run from crane-console only.
- All GitHub issues this session target venturecrane/crane-console. Targeting a different repo? STOP.
- Never write the report to the previous audit's filename - always use today's date.
- Do NOT execute the kill list automatically. Always ask first.
- Use `model: "sonnet"` for Explore agents. Reserve opus for the synthesis step.
