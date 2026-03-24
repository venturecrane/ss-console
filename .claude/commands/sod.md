# /sod - Start of Day

This command prepares your session using MCP tools to validate context, show work priorities, and ensure you're ready to code.

## Execution

### Step 1: Start Session

Call the `crane_sod` MCP tool to initialize the session.

The tool returns a structured briefing with:

- Session context (venture, repo, branch)
- Behavioral directives (enterprise rules)
- Continuity (recent handoffs)
- Alerts (P0 issues, active sessions)
- Weekly plan status
- Cadence briefing (overdue/due recurring activities)
- Knowledge base and enterprise context

### Step 2: Display Context Confirmation

Present a clear context confirmation box:

```
VENTURE:  {venture_name} ({venture_code})
REPO:     {repo}
BRANCH:   {branch}
SESSION:  {session_id}
```

State: "You're in the correct repository and on the {branch} branch."

### Step 3: Handle P0 Issues

If the Alerts section shows P0 issues:

1. Display prominently with warning icon
2. Say: "**There are P0 issues that need immediate attention.**"
3. List each issue

### Step 4: Check Work Plan

Query D1 for today's planned events:

- Call `crane_schedule(action: "planned-events", from: "{today}", to: "{today}", type: "planned")`
- **If found**: Display "Today: **{VENTURE} Work**, 6:30am - 10:30pm" in the context box
- **If not found**: Suggest "No work plan for today. Run `/work-plan` to set up your schedule."

Also check the weekly plan file status from the `crane_sod` response:

- **valid**: Note the priority venture and proceed
- **stale**: Warn user: "Weekly plan is {age_days} days old. Consider running `/work-plan`."
- **missing**: Suggest running `/work-plan`

### Step 5: Cadence Decision Prompt

The `crane_sod` response includes cadence status (overdue/due items).

For any overdue items:

1. Display the overdue items list
2. Ask: "**Want to execute any of these now, or skip?**"
3. If user chooses to execute: run the appropriate command (e.g., `/portfolio-review`)
4. If user skips: proceed to next step

Do NOT create Google Calendar events for cadence items. Do NOT create Apple Reminders here (that is `/work-plan`'s job).

### Step 6: STOP and Wait

**CRITICAL**: Do NOT automatically start working.

Present a brief summary and ask: **"What would you like to focus on?"**

If user wants to see the full work queue, call `crane_status` MCP tool.

## Wrong Repo Prevention

All GitHub issues created this session MUST target the repo shown in context confirmation. If you find yourself targeting a different repo, STOP and verify with the user.

## Troubleshooting

If MCP tools aren't available:

1. Check `claude mcp list` shows crane connected
2. Ensure started with: `crane vc`
3. Try: `cd ~/dev/crane-console/packages/crane-mcp && npm run build && npm link`
