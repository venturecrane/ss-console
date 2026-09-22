# Trust Balance Nudge - Output Format

## Shape A - Replenishment request (below floor)

```markdown
# Trust Replenishment - <client> - matter <id> - YYYY-MM-DD

**Available balance (read-only):** $<availableBalance> | **Floor:** $<floor> | **Shortfall:** $<shortfall>
**Source:** get_matter_balances → availableBalance (read-only)

## Replenishment request (DRAFT - reviewer sends)

> <factual, respectful request per voice.md - available balance, floor, shortfall, how to
> replenish; authored terms only; no invented consequence>

## Internal log (create_memo body)

> Trust balance $<balance> below floor $<floor> on matter <id>; replenishment request drafted; no funds moved.
```

## Shape B - No action (at/above floor)

```markdown
# Trust Balance OK - <client> - matter <id> - YYYY-MM-DD

**Available balance:** $<availableBalance> ≥ **Floor:** $<floor>. No nudge needed. (Internal memo only.)
```

## Shape C - Surface to human (read failed, or a move-money request)

```markdown
# ⚠ Trust - needs a human - <client> - matter <id> - YYYY-MM-DD

**Reason:** <balance read unavailable> | <client asked to move/reallocate funds - IOLTA decision for a human>
No balance guessed, no funds moved, nothing drafted financially.
```

## Rules

1. **No tool that moves money appears anywhere** - not in the draft, not in the tool calls.
2. **Only Shape A has a client-facing draft** (blockquote, drafted for review).
3. **Consequence language only if authored.**
4. **An unavailable balance is Shape C, never a guessed number.**
