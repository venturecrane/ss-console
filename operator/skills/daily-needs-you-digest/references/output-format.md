# Daily Needs-You Digest - Output Format

One digest per tick, written as one file note to the firm's digest home matter. The result of the
wake decision determines the shape. Two shapes only. Neither ever contains an outbound
draft, because this skill sends nothing and acts on nothing.

## Shape A - The batched digest (something needs a person)

The digest is one file note on the digest home matter, in the pack's file-note shape
(`_shared-write-posture.md`, section 5): the header line, one line of counts, one plain
line per item with the most time-critical first, and a last line saying what a person
needs to do first. Every item line names the matter by its number, the item, the
sourced date or age in the firm's local time, and the routine that owns the next
action. The digest points; it never acts.

No `#` headings, no bullets, no `>` quotes, no `**`, no table. At most 15 lines: when
more than 12 items are in band, list the 12 most time-critical and end the item lines
with "<K> more items are in band; the 12 most time-critical are listed."

**Every band is conditional.** A band with no in-band items has no line and no count.
It is never rendered as a zero or as "None": a band with nothing in it has nothing to
disclose, so omission hides nothing. (Shape B already covers the case where every band
is empty.)

```markdown
[Operator] <Routine name> as of <localDate>
<N> items across <M> open matters need a person: <count> deadlines near, <count> due soon, <count> unsigned, <count> stalled.
Deadline near: matter <matter number>, <deadline item>, <date> (<days> days). Owner: <routine>.
Due soon: matter <matter number>, <task>, due <date> (<days> days). Owner: <routine>.
Unsigned: matter <matter number>, <verification or signature item>, sent <date>, unsigned <N> days. Owner: <routine>.
Stalled: matter <matter number>, <open item>, no movement since <date> (<N> days). Owner: <routine>.
Reads failed this run: matter <matter number>; its items are not listed.
<What needs doing first, for which matter, and when to bring in the attorney.>
```

The training note rides in the item lines and the last line
(`_shared-training-output.md`): the owning routine is what comes next, and the last
line says when to bring in the attorney. No separate training block.

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
Matter <matter number>, <item>: under active escalation by <owning skill>.
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

The header and two lines. No sections. No padding. Plus the heartbeat, so the tick is auditable.

```markdown
[Operator] <Routine name> as of <localDate>
Nothing needs a person today across <M> open matters; waiting and on-track items are not listed.
Nothing to do.
```

The heartbeat row (`needs_you_digest_tick`, `decision_basis:
nothing_in_needs_you_band`) is written beside the note, never inside it.

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
   "Unsigned: 0" and no "None." line: the count and the lines all go. Rule
   2 already keeps out-of-band items out; this keeps the empty _structure_ out
   too. A band with no items has nothing to disclose. Shape B still governs the
   day when every band is empty.
