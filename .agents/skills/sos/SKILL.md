---
name: sos
description: Start of Session
version: 1.0.0
scope: enterprise
owner: captain
status: stable
---

# /sos - Start of Session

> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "sos")`. This is non-blocking — if the call fails, log the warning and continue. Usage data drives `/skill-audit`.

1. Call `crane_sos` MCP tool (returns formatted briefing).
2. Call `crane_schedule(action: "planned-events", from: "{today}", to: "{today}", type: "planned")`.
3. Display briefing. Highlight any Resume block or P0 issues.
4. If cadence items overdue, ask: "Execute any now, or skip?"
5. **STOP.** If Resume block: "Previous session was working on [summary]. Resume or focus elsewhere?" Otherwise: "What would you like to focus on?"

## Rules

- All GitHub issues this session target the repo shown in context. Targeting a different repo? STOP.
- Do NOT start working automatically.
- Do NOT create calendar events for cadence items.
- If MCP tools unavailable: check `claude mcp list`, ensure started with `crane vc`.
