# /sos - Start of Session

> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "sos")`. This is non-blocking — if the call fails, log the warning and continue. Usage data drives `/skill-audit`.

1. Call `crane_sos` MCP tool (returns formatted briefing).
2. Call `crane_schedule(action: "planned-events", from: "{today}", to: "{today}", type: "planned")`.
3. **Orphan worktree backstop** — see below. Auto-remove provably-safe orphans from sessions that ended unnaturally; surface the rest. Compute a one-line worktree status line for the briefing.
4. Display briefing. Prepend the worktree status line from Step 3 (omit if no orphans found and no warning). Highlight any Resume block or P0 issues.
5. If cadence items overdue, ask: "Execute any now, or skip?"
6. **STOP.** If Resume block: "Previous session was working on [summary]. Resume or focus elsewhere?" Otherwise: "What would you like to focus on?"

## Step 3 — Orphan Worktree Backstop

Closes the exit-side gap from PR #789's parallel-isolation system. `/eos` is the deterministic primary path; this step handles sessions that ended unnaturally (closed terminal, force-quit, kernel panic).

**Implementation.** Call `mcp__crane__crane_worktree_doctor({ apply: true })`. The tool runs all four safety gates (lock-triage with alive/dead-PID detection, lsof, fresh-HEAD, clean-tree + merged-via-PR-or-log-or-cherry) inside the MCP boundary, executes destructive cleanup for provably-safe entries, and returns a JSON document. **No inline destructive bash** — the auto-mode classifier denies destructive Bash invocations regardless of `permissions.allow`; MCP tools are past the classifier.

**JSON shape returned by the tool:**

```
{
  "scanned": <number>,
  "deferred_by_cap": <number>,
  "cleaned": [{ "id": "<name>", "branch": "<branch>", "reason": "clean+merged+pr|ff|squash", "unlocked": <bool> }],
  "needs_review": [{ "id": "<name>", "branch": "<branch>", "reason": "dirty: N | N commit(s) ahead | fresh HEAD | locked: <reason> | live process | lsof timeout" }],
  "errors": [{ "id": "<name>", "message": "<error>" }],
  "apply": true
}
```

**Briefing line composition** (parse the JSON):

- All of `cleaned` / `needs_review` / `errors` empty AND `deferred_by_cap === 0` → omit the worktree line entirely.
- Otherwise: `Worktrees: cleaned <N> orphan(s) [<ids, max 5>]; <M> needs review [<id>: <reason>, max 5]; <K> deferred (cap); <E> error(s) [<id>: <message>]` — drop empty sections; truncate ID lists past 5 with `...`. Render `(unlocked)` annotation when `cleaned[i].unlocked === true`.

Examples:

- `Worktrees: cleaned 2 orphan(s) [robust-fluttering-oasis, shiny-toasting-prism]`
- `Worktrees: 1 needs review [test-pending: 1 commit(s) ahead]`
- `Worktrees: cleaned 6 orphan(s) [agent-a0324558 (unlocked), agent-a09306ec (unlocked), ...]; 13 needs review [agent-a1705f92: dirty: 11, agent-a17f5148: dirty: 11, ...]`

**Rollback.** If the MCP call is denied (very rare; would mean the classifier inspects MCP tool internals contrary to current docs), call with `apply: false` instead — the tool still classifies and returns a "would clean" report; the briefing remains accurate; Captain runs cleanup manually until the issue is resolved.

## Rules

- All GitHub issues this session target the repo shown in context. Targeting a different repo? STOP.
- Do NOT start working automatically.
- Do NOT create calendar events for cadence items.
- If MCP tools unavailable: check `claude mcp list`, ensure started with `crane vc`.
