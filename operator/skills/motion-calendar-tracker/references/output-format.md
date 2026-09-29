# Motion Calendar Tracker - Output Format

One matter, one surface. The surface is **the record, organized** - every row names
its Smokeball source, and anything the record does not support is a **gap**, not a
value. There are no outbound drafts here: this skill only surfaces and writes an
internal log.

## Shape A - Motion calendar surface (the current, sourced picture)

```markdown
# Motion Calendar - <matter description> - matter <id> - YYYY-MM-DD

**Matter status:** <Open | Pending | ...> · **Responsible:** <staff>
**Window:** <from>-<to> · **Sources:** <N> events, <M> tasks

## Filed

| Motion                                        | Filed by        | Filed (source)                 | Status (source)                                                         |
| --------------------------------------------- | --------------- | ------------------------------ | ----------------------------------------------------------------------- |
| <e.g. Motion to Compel Further RFP Responses> | firm / opposing | <date> (task <id> / memo <id>) | <filed / opposed / submitted / heard - each from a record item, or "-"> |

## Due

| Item                     | Date   | Source                 | Note                                                                                                                       |
| ------------------------ | ------ | ---------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| <e.g. Opposition to MTC> | <date> | task <id> / event <id> | authored by a human                                                                                                        |
| <e.g. Reply to MSJ>      | -      | -                      | not calendared - anchor: hearing <date> (event <id>); MSJ runs on §437c (not §1005(b)) - deadline lane to confirm the rule |

## Hearings

| Motion   | Hearing date | Dept/time               | Source     |
| -------- | ------------ | ----------------------- | ---------- |
| <motion> | <date>       | <dept/time if in event> | event <id> |

## Gaps & Confirms (surfaced - NOT filled)

- <hearing <date> (event <id>) has no matching filed motion in the record - confirm>
- <MTC filed <date> (task <id>) has no hearing date set - confirm>
- <opposition/reply windows for <motion> not calendared - hand to the deadline lane>
- <event "MSJ?" (event <id>) is ambiguous - cannot place; confirm what it is>
```

## Shape B - Gap / ambiguity dominates (nothing clean to surface)

When the record is too thin or too ambiguous to assemble a trustworthy calendar, do
**not** manufacture one. Lead with the gap.

```markdown
# ⚠ Motion Calendar - gaps to resolve - matter <id> - YYYY-MM-DD

**Situation:** <hearing on the calendar with no filed motion | motion filed with no
hearing | status reported in a note but unconfirmed | event too ambiguous to place>
**What the record shows:** <the sourced item(s), verbatim-anchored, e.g. "event <id>:
'MSJ hearing' <date>; no filed-MSJ item in tasks or memos">
**What it does NOT show:** <the missing piece - stated as missing, never inferred>
**Decision:** surfaced for a person. Nothing computed, drafted, or asserted. This is a
judgment the skill does not make on its own.
```

## File note (create_memo)

One note per matter. The tables above are this run's report, never the note: a
note carries no table. Every hearing time is the event's `localTime` on its
`localDate` ("Oct 6 at 9:30 a.m."), never its `startTime`.

```markdown
[Operator] <Routine name> as of <localDate>
Motion calendar assembled: <X> motions filed, <Y> due, <V> hearings (next: <motion> on <date> at <time>, Dept <dept>); gaps: <gaps, or none>. Opposition and reply windows run off the hearing under the rule for the motion type (§1005(b) for a noticed motion, §437c for summary judgment).
<Attorney> to confirm and calendar <the un-calendared windows, or the gap named above>, or: Nothing to do.
```

## Rules

1. **No outbound drafts in any shape.** This skill surfaces and logs internally; it
   never drafts a motion, opposition, reply, or client/court message.
2. **Every Filed / Due / Hearing row names its source** (event id / task id / memo id).
   A row with no source is not a row - it is a Gap.
3. **A missing due date is never computed.** Authored dates are surfaced with their
   source; un-authored windows are surfaced as an **anchor + gap** for the deadline
   lane, never as a stated date.
4. **A missing hearing date or motion status is a Gap**, surfaced with what the record
   does and does not show - never a plausible fill.
5. **A hearing outcome appears only if the record states it, and occurrence is never
   inferred from a passed date.** The record shows a hearing was _set_ for a date, not
   that it was held - hearings get continued, vacated, or taken off calendar. A passed
   hearing date with no minute order or disposition in the record is "hearing was set
   for <date> (event <id>); no minute order or disposition in the record - confirm
   whether it was held, continued, or vacated," never "heard <date>" and never
   "granted"/"denied."
6. **A reported-but-unstructured status is surfaced as "reported in <source>,
   unconfirmed,"** never re-asserted in the skill's own voice.
