# /eod - End of Day Handoff

Auto-generate handoff from session context. The agent summarizes - never ask the user.

## Usage

```
/eod
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

**In Progress:** Unfinished work

- Where things were left off
- Partial implementations
- Pending reviews

**Blocked:** Items needing attention

- Blockers encountered
- Questions for PM
- Decisions needed
- External dependencies

**Next Session:** Recommended focus

- Logical next steps
- Priority items
- Follow-ups needed

### 3. Show User for Confirmation

Display the generated handoff and ask:

```
Here's the handoff I generated:

[show handoff content]

Save to D1? (y/n)
```

Only ask this single yes/no question. Do not ask user to write or edit the summary.

### 4. End Work Day

Call `POST /work-day` with `action: "end"` via the `upsertWorkDay` API method.

### 5. Save Handoff via MCP

Call the `crane_handoff` MCP tool with:

- `summary`: The synthesized handoff text
- `status`: One of `in_progress`, `blocked`, or `done` (infer from context)
- `issue_number`: If a primary issue was being worked on

This writes to D1 via the Crane Context API. The next session's `crane_sod` will read it.

### 6. Report Completion

```
Handoff saved to D1. Next session will see this via crane_sod.
```

## Key Principle

**The agent summarizes. The user confirms.**

The agent has full session context - every command run, every file edited, every conversation turn. It should synthesize this into a coherent handoff without asking the user to remember or summarize anything.

The only user input is a yes/no confirmation before saving.
