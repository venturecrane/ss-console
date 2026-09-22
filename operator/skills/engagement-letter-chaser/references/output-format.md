# Engagement Letter Chaser - Output Format

The decision determines the shape.

## Shape A - Nudge due

```markdown
# Engagement Chase - <client> - matter <id> - YYYY-MM-DD

**Status:** sent <date>, unsigned (<N> days); last nudge <date or "none">; nudge <#> of <max>
**Decision:** nudge due

## Nudge (sent or held per the authored ceiling; see SKILL.md invariant 4)

> <short, warm reminder per voice.md: floor-clean body (#1878) that points to where
> to complete and return the letter; offers to answer questions with the team;
> interprets nothing>

## Internal log (create_memo body)

> Engagement nudge <#> drafted for matter <id>; letter sent <date>, still unsigned.
```

## Shape B - Signed (log + stop)

```markdown
# Engagement Signed - <client> - matter <id> - YYYY-MM-DD

**Decision:** signature logged; cadence stopped; matter advances to active.

## Internal log (create_memo body)

> Engagement letter for matter <id> signed <signed_date>; chase cadence stopped; matter active.
```

## Shape C - Wait

```markdown
# Engagement Chase - <client> - matter <id> - YYYY-MM-DD

**Status:** sent <date>, unsigned; last nudge <date> (<N> days ago, interval <interval>)
**Decision:** within cadence - wait, no nudge.
```

## Shape D - Surface to human (declined / expired / max reached)

```markdown
# ⚠ Engagement Chase - needs a human - <client> - matter <id> - YYYY-MM-DD

**Status:** <declined | expired | max nudges (<max>) reached>
**Decision:** surfaced for a human - this is a relationship/decision call, not another nudge.
```

## Rules

1. **Only Shape A contains a client-facing body** (blockquote); whether it sends or is held follows the firm's authored ceiling (SKILL.md invariant 4).
2. **The Shape A body is floor-clean (#1878)** - no "sign"/"signature"/"engagement letter"/"attorney" in the outbound text (substitution table in `references/voice.md`). Internal shapes (headers, status lines, memo bodies) keep the precise words: the floor scans what leaves the firm, not the matter file.
3. **No term of the letter appears interpreted** anywhere.
4. The decision and its reason are always stated, so the cadence is auditable.
