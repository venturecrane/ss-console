# Daily Needs-You Digest - Output Format

One digest per tick, written to the firm's internal notes surface. The result of the
wake decision determines the shape. Two shapes only. Neither ever contains an outbound
draft, because this skill sends nothing and acts on nothing.

## Shape A - The batched digest (something needs a person)

Counts up top; the most time-critical section first. Every line names the matter, the
item, the sourced date/age, and the **owning skill/step** for the next action. The
digest points; it never acts.

**Every band below is conditional.** A band with no in-band items is OMITTED whole

- heading, count, and body. It is never rendered as a zero-count heading over
  "None". A band with nothing in it has nothing to disclose, so omission hides
  nothing; it just stops the reader scrolling past empty structure to reach the two
  lines that need them. (Shape B already covers the case where every band is empty.)

```markdown
# Needs a person today - YYYY-MM-DD - <N> item(s) across <M> open matter(s)

## Deadlines near (<count>)

- <matter> (<matter id>) - <deadline item>, <date> (<days> days) - owns: <skill/step>
- ...

## Due soon (<count>)

- <matter> (<matter id>) - <task>, due <date> (<days> days) - owns: <skill/step>
- ...

## Unsigned (<count>)

- <matter> (<matter id>) - <verification/signature item>, sent <date>, unsigned (<N> days) - owns: client-verification-tracker
- ...

## Stalled (<count>)

- <matter> (<matter id>) - <open item>, no movement since <date> (<N> days) - owns: <skill/step>
- ...

## Notes for a paralegal (training)

> Per item, one short line: what needs doing, why it matters (the governing rule where
> the owning step has one, e.g. an unverified response is treated as no response,
> §2030.250), which step owns it, and when to bring the attorney in. Explanatory, not
> advisory. Short.

## Read-failures (only if any)

- <matter id> - reads failed this tick (parse_failed); surfaced, not hidden.
```

### Items already under active escalation (dedup pointer)

An item another skill is actively escalating is NOT listed in full in its band.
It renders as a one-line pointer instead, so the digest and the escalator do not
both hand the reader the same item on the same morning.

"Active escalation" is read from the escalation ledger (the shared
`escalation_ledger.py` state), NOT from same-day prediction: the digest fires
before the escalator, so "escalated to you separately today" would be false.
An item is under active escalation when the ledger holds a `fired` or `chased`
event for it, by another skill, whose age is within the firm's
`escalation.refire_days` window. Render it as:

```markdown
- <matter> (<matter id>) - <item>: under active escalation by <owning skill>.
```

The pointer carries **no date from the ledger**. The ledger is the Operator's own
record of what it sent, not the firm's record, and the identifier gate certifies
dates only from the firm's system of record. A "last raised <date>" copied from
the ledger is refused on every write, and the retries count toward the seat's
refusal brake: on 2026-09-21 pilot-smokeball's digest drafted the memo nine
times, and the seven that carried ledger dates were all refused. The owning
skill already tells the reader when it raised the item.

The pointer is a pure surface line. The digest reads the ledger; it never writes
it and never acts on the item.

## Shape B - The quiet-day digest (nothing genuinely needs a person)

One line. No sections. No padding. Plus the heartbeat, so the tick is auditable.

```markdown
# Needs a person today - YYYY-MM-DD

Nothing needs a person today across <M> open matter(s). Waiting/on-track items are not
listed. (Heartbeat: needs_you_digest_tick, decision_basis: nothing_in_needs_you_band.)
```

## Rules

1. **Neither shape contains an outbound draft or an action.** Every line is a pointer
   to the skill/step that owns the next action, never the action itself.
2. **Only in-band items appear in Shape A.** Due soon / deadline near / unsigned /
   stalled, per the firm's authored windows. Legitimately-waiting items (open task with
   a future due date beyond the window) are excluded, not demoted into a section.
3. **A quiet day is Shape B, always.** Never pad Shape B into Shape A to look useful,
   and never manufacture urgency for an item that is fine.
4. **Every item traces to a Smokeball read.** No invented item, date, age, or urgency.
   Missing data is shown as missing.
5. **Ordering is by time-criticality**, most urgent first, so the firm reads the top
   and stops when it has what it needs.
6. **A tick always leaves a heartbeat row** (Shape A or Shape B). A silent suppression
   is a failure.
7. **An item under active escalation renders as a one-line pointer, never a full
   band entry.** Active escalation is defined off the escalation ledger (a
   `fired`/`chased` event by another skill within `escalation.refire_days`), not
   off same-day prediction. The digest reads the ledger; it never writes it.
8. **An empty band is omitted whole, never rendered as a zero.** No
   `## Unsigned (0)` followed by "None." - heading, count, and body all go. Rule
   2 already keeps out-of-band items out; this keeps the empty _structure_ out
   too. A band with no items has nothing to disclose. Shape B still governs the
   day when every band is empty.
