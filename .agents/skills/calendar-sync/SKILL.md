---
name: calendar-sync
description: Calendar Sync
version: 1.0.0
scope: enterprise
owner: agent-team
status: stable
---

# /calendar-sync - Calendar Sync

> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "calendar-sync")`. This is non-blocking — if the call fails, log the warning and continue. Usage data drives `/skill-audit`.

Create actual session events on Google Calendar from D1 session history. Planned events are never modified or deleted — they represent the work plan and coexist with actuals.

## Usage

```
/calendar-sync
```

## Execution

### Step 1: Fetch Session History

Call `crane_schedule(action: "session-history", days: 7)` to get ended sessions aggregated by venture and date.

This returns entries like:

```json
{
  "venture": "vc",
  "work_date": "2026-03-21",
  "earliest_start": "2026-03-21T13:30:00Z",
  "latest_end": "2026-03-21T21:00:00Z",
  "session_count": 2
}
```

Only `status = 'ended'` sessions are included. Abandoned sessions are excluded (heartbeat data is unreliable).

### Step 2: Create/Update Actual Events

For each venture/day entry from session history:

1. Query existing actual events: `crane_schedule(action: "planned-events", from: "{work_date}", to: "{work_date}", type: "actual")`

2. **If an actual event already exists for this venture/day**:
   - Update the Google Calendar event times if they differ
   - Update the D1 record: `crane_schedule(action: "planned-event-update", id: "{id}", start_time: "{actual_start}", end_time: "{actual_end}", sync_status: "synced")`

3. **If no actual event exists for this venture/day**:
   - Create a new Google Calendar event with actual venture and times
   - Create a D1 record: `crane_schedule(action: "planned-event-create", ...)` with type='actual'

**IMPORTANT: Never modify, replace, or delete planned events.** Planned events are the work schedule recorded via `crane_schedule`. They stay on the calendar regardless of what actually happened. The calendar shows both planned blocks and actual session blocks side by side.

### Step 3: Display Summary

Show a table of synced sessions:

```
## Calendar Sync Summary

| Date | Venture | Start | End | Sessions | Action |
|------|---------|-------|-----|----------|--------|
| 2026-03-21 | VC | 6:30am | 2:00pm | 2 | updated |
| 2026-03-20 | VC | 7:00am | 5:00pm | 3 | created |
| 2026-03-22 | VC | 10:00am | 6:00pm | 4 | already synced |

Synced: 2 days, Already synced: 1 day
```

## Calendar Result

After sync, Google Calendar should show:

- **Past days**: Both planned blocks (from `crane_schedule`) AND actual session blocks (from D1)
- **Future days**: Planned blocks only (from `crane_schedule`)
- Planned and actual events coexist — they are not mutually exclusive

## Timezone

All times stored in UTC in D1. Display conversion uses UTC-7 (America/Phoenix, no DST).
