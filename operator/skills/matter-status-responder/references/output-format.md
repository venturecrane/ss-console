# Matter Status Responder - Output Format

## Shape A - Status reply (privilege + gate clear)

```markdown
# Matter Status - <client> - matter <id> - YYYY-MM-DD

**Requester:** <name> (client on matter - verified)
**Sourced from:** get_matter (incl. personResponsibleStaffId), list_tasks, list_calendar_entries (calendar binding), recent notes

## Status reply (DRAFT - reviewer sends)

> <plain-text reply per voice.md: where it stands, what happened recently,
> what's next - each a sourced fact; unknowns stated as "the team will confirm">

## Sources (audit)

- Stage: <from get_matter>
- Recent activity: <from notes/tasks>
- Next step: <from tasks/calendar> | _(not in record - flagged in reply)_
```

## Shape B - Privilege block

```markdown
# ⚠ Status request - non-client - matter <id> - YYYY-MM-DD

Requester <name> is not the client/authorized contact on this matter. **No status disclosed.** Surface to a human to verify the relationship before any response.
```

## Shape C - Conflict-hold / route to human

```markdown
# ⛔ Status request - matter on hold - matter <id> - YYYY-MM-DD

Matter is on CONFLICT-HOLD. Routed to a human; no status drafted.
```

## Rules

1. **Only Shape A contains a client-facing draft.**
2. **Every status fact has a source line.** An unsourced "fact" is fabrication.
3. **No prediction, opinion, advice, or outcome reassurance** anywhere in the reply.
