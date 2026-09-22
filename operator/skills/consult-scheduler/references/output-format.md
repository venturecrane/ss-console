# Consult Scheduler - Output Format

Two shapes. The conflict gate decides whether the skill produces a booking proposal at all.

## Shape A - Booking proposal (matter clear)

```markdown
# Consult Scheduling - <client name> - matter <id> - YYYY-MM-DD

**Responsible attorney:** <name>
**Practice area / consult length:** <area> / <N> min
**Rules applied:** business hours <range>; blackout windows respected; buffer <N> min; no double-book

## Proposed times (all rule-valid)

1. <Day, Date, Time> (<tz>)
2. <Day, Date, Time>
3. <Day, Date, Time>

_Client preference:_ <stated preference> - <honored / nearest valid alternative because ...>

## Calendar entry (PROPOSAL - human confirms the write)

> create_calendar_entry(summary="Consult - <client>", start_at=<...>, end_at=<...>,
> calendar_owner_id=<attorney>, matter_id=<id>, location=<...>)
>
> - NOT executed; surfaced for human confirmation.

## Confirmation (DRAFT - reviewer sends)

> <plain-text confirmation, per voice.md - scheduling only>

## Internal log (create_memo body)

> Consult proposed for matter <id>; <N> rule-valid times offered; confirmation drafted; calendar write surfaced for confirm.
```

## Shape B - Blocked on conflict

```markdown
# ⛔ Scheduling blocked - matter <id> - YYYY-MM-DD

Scheduling is halted: this matter carries an unresolved conflict / CONFLICT-HOLD. No times proposed, no booking, no confirmation drafted. Route to a human for conflict clearance before scheduling.
```

## Rules

1. **Every proposed slot is rule-valid** - no blackout, no overlap, correct length, inside hours, buffered.
2. **The calendar entry is always a PROPOSAL** (`>` block, marked NOT executed), never an autonomous write this phase.
3. **The confirmation is scheduling-only** - no legal matter content.
4. **Shape B produces nothing schedulable** - it is a stop.
