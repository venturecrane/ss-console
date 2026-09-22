# Stalled Matter Nudge - Output Format

## The scan output

```markdown
# Stalled Matter Scan - YYYY-MM-DD

**Window:** <N> days of no activity
**Matters scanned:** <M> | **Stalled:** <S> | **Waiting (not flagged):** <W> | **Held (separate):** <H>

## Stalled - needs a nudge

### matter <id> - <client> - quiet <D> days

**Last activity:** <date> (matter `LastUpdated`)

**Follow-up (DRAFT - reviewer sends):**

> <neutral follow-up per voice.md - surfaces + offers to reconnect; no next-step advice>

---

(more stalled matters...)

## Waiting - not flagged (auditable)

- matter <id> - <client>: quiet <D> days BUT open task "<name>" due <future date> → legitimately waiting.

## Held - surfaced separately (no client follow-up)

- matter <id> - <client>: on CONFLICT-HOLD; route to human for clearance, no follow-up drafted.
```

## Rules

1. **Only genuinely-stalled matters get a follow-up draft** (blockquote, drafted for review).
2. **The Waiting list is shown** so the firm can see what was excluded and why (specificity is auditable).
3. **Held matters never get a client follow-up.**
4. **No follow-up states a next legal step.**
