---
name: sos
description: Start of session for SMD Services — briefing, cadence, worktree backstop, and what we owe clients.
---

# /sos - Start of Session (SMD Services)

> **Forked from crane-console on 2026-09-17.** This is SMD's own copy and no
> longer receives enterprise updates; improvements originate here. ss-console is
> the primary venture, so the session lifecycle is authored rather than
> inherited. The fork point is commit `da86b27c` (verbatim copy); everything
> after it is ours. Crane's copy still lives at `.claude/commands/sos.md` and is
> re-synced on every `crane ss` launch, which is exactly why this file lives in
> `.claude/skills/` — nothing in the crane toolchain writes here.
>
> **If `/sos` runs crane's copy instead of this one**, precedence between
> `.claude/commands/` and `.claude/skills/` went the other way. `.claude/commands/`
> is not in Claude Code's documented skill loader at all, so there is no stated
> rule and this was settled empirically. The fallback ladder, in order: add
> `"skillOverrides": {"sos": "off"}` to `.claude/settings.json` and re-check —
> but verify it disabled crane's copy and not this one, since both are named
> `sos`; failing that, rename this skill directory and use the new command.
> Deleting `.claude/commands/sos.md` does not work: the launcher restores it.

> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "sos")`. This is non-blocking — if the call fails, log the warning and continue. Usage data drives `/skill-audit`.

1. Call `crane_sos` MCP tool (returns formatted briefing).
2. Call `crane_schedule(action: "planned-events", from: "{today}", to: "{today}", type: "planned")`.
3. **Orphan worktree backstop** — see below. Auto-remove provably-safe orphans from sessions that ended unnaturally; surface the rest. Compute a one-line worktree status line for the briefing.
4. **What we owe clients** — see below. Compute a one-line `[owed]` status line.
5. Display briefing. Prepend the worktree status line from Step 3 and the `[owed]` line from Step 4 (omit either when it has nothing to say). Highlight any Resume block or P0 issues.
6. If cadence items overdue, ask: "Execute any now, or skip?"
7. **STOP.** If Resume block: "Previous session was working on [summary]. Resume or focus elsewhere?" Otherwise: "What would you like to focus on?"

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

## Step 4 — What we owe clients

The obligation register (ADR 0088, `docs/handbook/obligation-register.md`) holds every piece of work SMD owes a client. It is read from a terminal; there is deliberately no page. This step is what makes it arrive without the Captain going to look.

**Implementation.** Run `.claude/bin/register list --json` and compose one line from the rows.

Each row is `{obligation_id, client, customer_slug, kind, stable_key, state, due_at, age_days}`. **There is no `what` field and there must not be** — this line lands in a session transcript, transcripts persist under `~/.claude` and go to model providers, and ss-console is a public repo. Counts and slugs say what is owed and to whom; reading the text is a separate deliberate act (`register list`, no flag).

**Line composition:**

- Zero rows → omit the line entirely.
- Otherwise: `[owed] <N> open across <M> client(s) (<slug> <n>, …). Oldest <D>d.` Append ` None dated.` when no row carries a `due_at`, because only a dated row can go overdue and that is worth knowing. Append ` <K> overdue.` when any `due_at` is in the past.
- Command exits non-zero (D1 unreachable, wrangler missing) → say so in one line: `[owed] register unreachable — <first line of stderr>`. **Do not omit it silently.** At a Captain-invoked step, a stated failure is right where a silent hook line would be wrong: an empty register and an unreachable one must never look identical.

Examples:

- `[owed] 11 open across 2 clients (ashton-price 7, smd-services 4). Oldest 34d. None dated.`
- `[owed] 3 open across 1 client (ashton-price 3). Oldest 61d. 1 overdue.`
- `[owed] register unreachable — wrangler: command not found`

Note SMD Services is itself a client here: obligations on our own seats (`pilot-smokeball`, `smd-staging`, `scott`) roll up to `smd-services` via `scripts/lib/seat-clients.mjs`. There is no internal-versus-external split anywhere in the register.

## Rules

- All GitHub issues this session target the repo shown in context. Targeting a different repo? STOP.
- Do NOT start working automatically.
- Do NOT create calendar events for cadence items.
- If MCP tools unavailable: check `claude mcp list`, ensure started with `crane ss`.
