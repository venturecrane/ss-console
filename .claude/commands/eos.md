# /eos - End of Session Handoff

Auto-generate handoff from session context. The agent summarizes - never ask the user.

## Usage

```
/eos
```

## Execution Steps

### 1. Gather Session Context

The agent has full conversation history. Additionally, gather:

```bash
# Get repo info
REPO=$(git remote get-url origin | sed -E 's/.*github\.com[:\/]([^\/]+\/[^\/]+)(\.git)?$/\1/')

# Get commits from this session (last 24 hours or since last handoff)
git log --oneline --since="24 hours ago" --author="$(git config user.name)"

# Get any PRs created/updated today
gh pr list --author @me --state all --json number,title,state,updatedAt --jq '.[] | select(.updatedAt | startswith("'$(date +%Y-%m-%d)'"))'

# Get issues worked on (from commits or conversation)
gh issue list --state all --json number,title,state,updatedAt --jq '.[] | select(.updatedAt | startswith("'$(date +%Y-%m-%d)'"))'
```

### 2. Synthesize Handoff (Agent Task)

Using conversation history and gathered context, the agent generates a summary covering:

**Accomplished:** What was completed this session

- Issues closed/progressed
- PRs created/merged
- Problems solved
- Code changes made

**In Progress:** Unfinished work — write as pickup instructions for an agent with NO conversation history

- Include specific file paths, function names, and branch names
- State exactly where you stopped: "Function X is partially implemented in file Y"
- List what remains as numbered steps

**Blocked:** Items needing attention

- Blockers encountered
- Questions for PM
- Decisions needed
- External dependencies

**Next Session:** Recommended focus — write as an ordered action plan

- "1. Open src/foo.ts and complete the retryWithBackoff() function"
- "2. Run npm test — expect 2 new tests to pass"
- "3. Create PR for issue #123"

### 3. Display and Save (Auto-Save)

Display the generated handoff, then **immediately save it to D1 without asking for confirmation.** Do not prompt the user with "Save to D1?" or any yes/no question. Just show the summary and save.

### 4. End Work Day

Call `POST /work-day` with `action: "end"` via the `upsertWorkDay` API method.

### 5. Save Handoff via MCP

Call the `crane_handoff` MCP tool with:

- `summary`: The synthesized handoff text
- `status`: One of `in_progress`, `blocked`, or `done` (infer from context)
- `issue_number`: If a primary issue was being worked on

This writes to D1 via the Crane Context API. The next session's SOD will read it.

**Important:** When status is `in_progress`, the full summary is shown to the next session's SOD briefing. Write "In Progress" and "Next Session" as if giving instructions to another developer who has zero context from this conversation.

#### Cross-venture sessions

If work was done across multiple ventures this session (e.g., started in dc-console then switched to crane-console), write a separate handoff for each venture:

1. Identify all ventures that had meaningful work this session (commits, PRs, code changes, issue progress).
2. For each venture EXCEPT THE LAST, call `crane_handoff` with `venture: "<code>"` AND `final: false`. The `final: false` flag tells the API to create the handoff record but keep the session active so the next call doesn't 409 with "Session is not active".
3. For the FINAL venture, call `crane_handoff` without `final` (or with `final: true`). This call ends the session.
4. Each handoff summary should cover only the work relevant to that venture.

Example for a session that touched both `dc` and `vc`:

```
crane_handoff(summary: "Rebuilt AI assist panels...", status: "done", venture: "dc", final: false)
crane_handoff(summary: "Added /ship skill, command sync...", status: "done", venture: "vc")
```

Order doesn't strictly matter, but the LAST call must omit `final: false` (or pass `final: true`) so the session terminates cleanly.

### 6. Report Completion

```
Handoff saved to D1. Next session will see this via crane_sos.
```

## Key Principle

**The agent summarizes. The agent saves. No confirmation needed.**

The agent has full session context - every command run, every file edited, every conversation turn. It should synthesize this into a coherent handoff without asking the user to remember or summarize anything.

Auto-save directly to D1. Never ask "Save to D1?" or any confirmation question.
