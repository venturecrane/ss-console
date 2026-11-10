---
name: critique
description: Plan Critique & Auto-Revision
version: 2.0.0
scope: venture:ss
owner: captain
status: stable
---

# /critique - Plan Critique & Auto-Revision (SMD Services)

> **Forked from crane-console on 2026-11-09.** This is SMD's own copy and no
> longer receives enterprise updates; improvements originate here.
> The fork point is commit `ec197443`
> (crane `.agents/skills/critique/SKILL.md`, the
> last commit to touch it; unchanged at crane `origin/main` `55c766a6`);
> everything after it is ours. It lives in `.claude/skills/` for the reason
> `/sos` does, and the load order is the one recorded in
> `.claude/skills/wired/SKILL.md`: this project skill wins over crane's synced
> `.claude/commands/critique.md`. If `/critique` runs crane's copy instead,
> follow the fallback ladder in `.claude/skills/sos/SKILL.md`.
>
> **Changes since the fork:**
>
> 1. **Every critic runs on Fable** (`model: "fable"` on each Agent call).
>    Crane's copy spawned critics with no model, so they inherited the
>    session's model, whatever it was. The venture's routing rule: Fable plans,
>    designs and critiques; Opus builds; Sonnet or Haiku only search or re-run a
>    named probe (VERDICT section 5, PREV-16, 2026-11-09). Measure critic use
>    against Fable's weekly limit; each critic carries the full session
>    baseline.
> 2. **Critics are kept, and what was overruled leads the plan** (wave 2,
>    PREV-3, 2026-11-09). When a plan file is in use, each critic's raw output
>    is saved beside it in `<plan file without .md>.critics/<perspective>.md`,
>    and the revised plan's FIRST section is `## Critic findings not adopted`,
>    carrying each critic's own Verdict line and top issue titles with the
>    reply beside each. The document-library plan of 2026-11-06 overruled its
>    critique in a list inside a revision-history section
>    (`docs/plans/document-library/design-2026-11-06.md:124`), where nobody
>    approving the plan looked. `.claude/hooks/plan-premise-gate.mjs` denies
>    the plan-mode exit when the critics directory exists and the section is
>    missing or leaves a critic out.

> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "critique")`. This is non-blocking — if the call fails, log the warning and continue. Usage data drives `/skill-audit`.

This command spawns critic subagents to challenge the current plan or approach in conversation, then auto-revises based on the critique.

No files required - works against whatever plan, proposal, or approach is in the current conversation context.

## Arguments

```
/critique [agents]
```

- `agents` - number of critic agents to spawn in parallel (default: **1**). More agents = more perspectives, but slower and more expensive.
  - **1 agent**: Comprehensive critic. Fast. Good for quick sanity checks.
  - **2-3 agents**: Multiple specialized perspectives. Good for important decisions.
  - **4+ agents**: Full panel. Use sparingly - for high-stakes architectural or strategic decisions.

Parse the argument: if `$ARGUMENTS` is empty or not a number, default to 1. If it's a number, use that value.

Store as `AGENT_COUNT`.

## Execution

### Step 1: Identify the Plan

Scan the current conversation for the most recent plan, approach, or proposal. This could be:

- A plan written during plan mode
- A proposed implementation approach
- A technical design or architecture decision
- A strategy or workflow proposal
- Any structured "here's what I'm going to do" statement

**If no plan is identifiable**, stop and tell the user:

> I don't see a plan or proposal in our current conversation to critique.
>
> Describe your approach first, then run `/critique`.

**If a plan IS found**, capture it as `PLAN_TEXT` and display a brief confirmation:

```
Critiquing: {one-line summary of what's being critiqued}
Agents: {AGENT_COUNT}
```

Do NOT ask for confirmation - proceed immediately.

### Step 2: Assign Critic Perspectives

Select perspectives based on `AGENT_COUNT`. Always assign from the top of this list:

| #   | Perspective               | Focus                                                                                                              |
| --- | ------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| 1   | Devil's Advocate          | Flaws, risks, edge cases, false assumptions, failure modes. "What could go wrong?"                                 |
| 2   | Simplifier                | Over-engineering, unnecessary complexity, simpler alternatives. "Is there a simpler way?"                          |
| 3   | Pragmatist                | Feasibility, hidden costs, timeline realism, operational burden. "Will this actually work in practice?"            |
| 4   | Product Strategist        | Scope, ROI, opportunity cost, venture priority fit. "Is this the right thing to build right now, vs. what's next?" |
| 5   | Contrarian                | Fundamentally different approaches, paradigm challenges. "What if the entire framing is wrong?"                    |
| 6   | User/Stakeholder Advocate | End-user impact, UX consequences, stakeholder concerns. "How does this affect the people who use it?"              |
| 7   | Security & Reliability    | Failure modes, data integrity, security surface, recovery paths. "How does this break?"                            |

- **1 agent** gets "Devil's Advocate" (the comprehensive default - covers risks, gaps, AND simpler alternatives in one pass).
- **2+ agents** each get a distinct perspective from the list above.
- **If AGENT_COUNT exceeds 7**, wrap around and assign "Senior" variants (e.g., agent 8 = "Senior Devil's Advocate" with instruction to dig deeper than agent 1).

### Step 3: Spawn Critic Agents

Launch `AGENT_COUNT` agents **in a single message** using the Agent tool (`subagent_type: general-purpose`, `model: "fable"`). Every critic gets `model: "fable"`, whatever model the session runs on; a critic spawned without it inherits the session's model, which is the defect this fork exists to fix. In a harness without subagent support, run each perspective sequentially yourself and label each critique section.

**CRITICAL**: All Agent tool calls MUST be in a single message to run in true parallel.

Each agent receives:

- The full `PLAN_TEXT`
- Their assigned perspective
- Conversation context summary (what problem is being solved, key constraints mentioned)

Agent prompt template:

```
You are a {PERSPECTIVE_NAME} critic reviewing a plan. Your job is to find weaknesses and suggest improvements from your specific angle.

## The Plan Being Critiqued

{PLAN_TEXT}

## Context

{BRIEF_SUMMARY_OF_WHAT_PROBLEM_IS_BEING_SOLVED_AND_KEY_CONSTRAINTS}

## Your Perspective: {PERSPECTIVE_NAME}

{PERSPECTIVE_DESCRIPTION}

## Instructions

1. Start with `## {PERSPECTIVE_NAME} Critique`
2. **Strengths** (1-3 bullets): What's good about this plan? Acknowledge what works before attacking.
3. **Issues Found** (numbered list): Each issue must include:
   - **The problem**: What's wrong or risky
   - **Why it matters**: Impact if ignored
   - **Suggested fix**: A concrete alternative or mitigation - not just "think about this more"
4. **Alternative Approach** (optional): If you see a fundamentally better path, describe it briefly. Only include this if the alternative is genuinely superior, not just different.
5. **Verdict**: One line - "Proceed as-is", "Proceed with fixes", or "Reconsider approach"

CONSTRAINTS:
- Be specific and concrete. "This might have issues" is useless. "The database query in step 3 will table-scan because there's no index on user_id" is useful.
- Every issue MUST have a suggested fix. Critique without solutions is just complaining.
- Don't pad. If the plan is solid from your perspective, say so in 2-3 lines and move on. A short critique of a good plan is more valuable than a long critique full of nitpicks.
- Prioritize. If you find 10 issues, lead with the 3 that matter most.
- Do NOT write files. Return your critique as your final response message.
```

Wait for all agents to complete.

### Step 3b: Keep the Critics Beside the Plan File

**When a plan file is in use** (plan mode names one, `~/.claude/plans/<name>.md`, or the plan under critique is a file in the repo), save each critic's final response, verbatim and unedited, to:

```
<plan file path without .md>.critics/<perspective>.md
```

`<perspective>` is the perspective name in lowercase with hyphens: `devils-advocate.md`, `simplifier.md`, `pragmatist.md`, `senior-devils-advocate.md`. One file per critic, written by you (the critics themselves write nothing). A second round of `/critique` on the same plan replaces the files of the perspectives it re-ran and leaves the others.

The files are what the plan gate reads. They are the critics' own words, so the plan cannot quietly drop a finding it chose not to act on. When no plan file is in use (a plan stated only in conversation), skip this step and Step 5's section is part of the revised plan in conversation instead.

### Step 4: Synthesize (if AGENT_COUNT > 1)

If multiple critics ran, synthesize their output before revising:

1. Read all critic responses
2. Deduplicate overlapping issues (if 2+ critics flagged the same thing, note the convergence - it's more credible)
3. Rank issues by severity and frequency
4. Note any contradictions between critics (one says "too simple," another says "too complex")
5. Present a brief **Critique Summary**:

```
## Critique Summary ({AGENT_COUNT} perspectives)

### Consensus Issues (flagged by 2+ critics)
- ...

### Unique Issues
- ...

### Contradictions
- ...

### Verdicts
- Devil's Advocate: Proceed with fixes
- Simplifier: Reconsider approach
- ...
```

If `AGENT_COUNT == 1`, skip synthesis - use the single critic's output directly.

### Step 5: Auto-Revise

Using the critique (or synthesized critique), revise the plan:

1. Address each issue that has a "Suggested fix" - apply fixes that improve the plan without changing its fundamental intent
2. If a critic suggested "Reconsider approach" AND provided a concrete alternative, evaluate whether the alternative is genuinely better. If so, adopt it. If not, note why the original approach is preferred despite the criticism.
3. If critics contradicted each other, make a judgment call and note the tradeoff
4. Do NOT address nitpicks that don't materially improve the plan

Present the revised plan clearly:

```
## Revised Plan

{THE_REVISED_PLAN}

### Changes Made
1. {What changed and why, referencing which critic triggered it}
2. ...

### Critiques Acknowledged but Not Adopted
1. {What was raised, why it wasn't adopted}
```

**When a plan file is in use, write the not-adopted findings into the plan file as its FIRST section** (directly under the title), in this shape:

```
## Critic findings not adopted

| Critic | Verdict (the critic's own line) | Top issues | Not adopted, and why |
|---|---|---|---|
| Devil's Advocate | Proceed with fixes | 1. {issue title} 2. {issue title} | {issue}: {the reply} |
| Simplifier | Reconsider approach | 1. {issue title} | (all adopted) |
```

- **One row per file in `<plan>.critics/`.** Name the critic by its perspective, as the file is named.
- **The Verdict cell is the critic's own Verdict line, copied, not summarized.** "Proceed with fixes" stays "Proceed with fixes"; it is never rewritten as "Revise" or "Fine with changes".
- **Top issues are the critic's own titles**, at most three, in its order.
- **The last cell holds every finding you did not adopt, each with its reply**, or `(all adopted)`.
- **When every finding of every critic was adopted**, write instead the exact line:

```
Critic findings not adopted: none
```

The plan gate (`.claude/hooks/plan-premise-gate.mjs`) denies leaving plan mode while `<plan>.critics/` exists and the plan has neither this section naming every critic, with the critic's verdict where it gave one of the three, nor the none line.

### Step 6: Done

After presenting the revised plan, ask:

**"Revised plan above. Want to proceed, run another round of critique, or adjust something?"**

Do NOT automatically start implementing. Wait for the user.

---

## Notes

- **Files written only beside a plan file**: when a plan file is in use, the critics' raw output goes to `<plan>.critics/` and the not-adopted section into the plan file (Step 3b, Step 5). Otherwise critique and revision happen in conversation, not on disk.
- **Context-dependent**: The quality of critique depends on how much context is in the conversation. A vague "I'll fix the bug" produces vague critique. A detailed technical plan produces detailed critique.
- **Fast by default**: 1 agent, no confirmation step, auto-revise. The whole flow should complete in one shot.
- **Agent type and model**: All critic agents use `subagent_type: general-purpose` with `model: "fable"` via the Agent tool.
- **Parallelism**: All agents launch in a single message for true parallel execution.
- **No rounds**: Unlike `/prd-review` and `/design-brief`, critique is single-pass by design. If the user wants another round, they run `/critique` again - the revised plan is now the conversation context.
