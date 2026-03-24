# /critique - Plan Critique & Auto-Revision

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

| #   | Perspective               | Focus                                                                                                   |
| --- | ------------------------- | ------------------------------------------------------------------------------------------------------- |
| 1   | Devil's Advocate          | Flaws, risks, edge cases, false assumptions, failure modes. "What could go wrong?"                      |
| 2   | Simplifier                | Over-engineering, unnecessary complexity, simpler alternatives. "Is there a simpler way?"               |
| 3   | Pragmatist                | Feasibility, hidden costs, timeline realism, operational burden. "Will this actually work in practice?" |
| 4   | Contrarian                | Fundamentally different approaches, paradigm challenges. "What if the entire framing is wrong?"         |
| 5   | User/Stakeholder Advocate | End-user impact, UX consequences, stakeholder concerns. "How does this affect the people who use it?"   |
| 6   | Security & Reliability    | Failure modes, data integrity, security surface, recovery paths. "How does this break?"                 |

- **1 agent** gets "Devil's Advocate" (the comprehensive default - covers risks, gaps, AND simpler alternatives in one pass).
- **2+ agents** each get a distinct perspective from the list above.
- **If AGENT_COUNT exceeds 6**, wrap around and assign "Senior" variants (e.g., agent 7 = "Senior Devil's Advocate" with instruction to dig deeper than agent 1).

### Step 3: Spawn Critic Agents

Launch `AGENT_COUNT` agents **in a single message** using the Task tool (`subagent_type: general-purpose`).

**CRITICAL**: All Task tool calls MUST be in a single message to run in true parallel.

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

### Step 6: Done

After presenting the revised plan, ask:

**"Revised plan above. Want to proceed, run another round of critique, or adjust something?"**

Do NOT automatically start implementing. Wait for the user.

---

## Notes

- **No files written**: Critique and revision happen in conversation, not on disk. The plan isn't a file - it's the working approach.
- **Context-dependent**: The quality of critique depends on how much context is in the conversation. A vague "I'll fix the bug" produces vague critique. A detailed technical plan produces detailed critique.
- **Fast by default**: 1 agent, no confirmation step, auto-revise. The whole flow should complete in one shot.
- **Agent type**: All critic agents use `subagent_type: general-purpose` via the Task tool.
- **Parallelism**: All agents launch in a single message for true parallel execution.
- **No rounds**: Unlike `/prd-review` and `/design-brief`, critique is single-pass by design. If the user wants another round, they run `/critique` again - the revised plan is now the conversation context.
