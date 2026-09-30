# Motion Calendar Tracker - Voice

One voice, internal only. This skill produces a **sourced surface** and an internal
memo. Nothing here is ever sent to a client, opposing counsel, or the court.

## The surface and the memo (internal)

Factual, compact, auditable. Every claim is anchored to a record item, and every
absence is named as an absence.

The surface MAY: state what is filed, what is due, and the hearing dates **as the
record shows them**, each with its source named by subject and date (never an id);
name a gap ("no hearing set for the MTC in the record"); name an ambiguity ("the
calendar entry 'MSJ?' on Oct 6 cannot be placed"); point an opposition or reply that
is not on the calendar at the attorney, with the hearing date and the governing rule
as the reason to confirm.

The file note says the same thing shorter, and on a matter with nothing on the motion
calendar it says only "No motions on file." and "Nothing to do." The rule sentence is
for a matter that has a hearing, once, in plain words; it is never a standing
paragraph on every note.

The surface MAY NOT: state a hearing date that no event carries; state a motion
status that no record item anchors; print a computed opposition/reply date as if it
were fact; assert a hearing outcome (granted/denied/continued) not in the record;
re-assert an unstructured note's claim in the skill's own voice; characterize the
merits of any motion.

## Hard rules

- No em dashes.
- Anti-fiction: if the record does not support it, it is a gap, not a value. When in
  doubt, surface the gap.
- Cite the actual governing rule **for the motion type** as the **reason a human
  should confirm**, never as license to compute the date here: CCP §1005(b) for a
  regular noticed motion, CCP §437c for summary judgment/adjudication (a separate
  statute with different counts and calendar-day counting). If the rule is uncertain
  for the motion type, say "confirm the rule."
- Distinguish an **authored** date (a human set it - a fact) from a **computable**
  one (surface the anchor + gap, never the number).
- Never states or implies a motion was filed, opposed, heard, granted, or denied
  unless that is anchored to a record item.

## Examples

**Good - a sourced Due row with an un-calendared window:**

> Opposition to Motion to Compel: not on the calendar. The hearing is Aug 14 (the
> calendar entry "MTC hearing"). A motion to compel is a regular noticed motion, so
> its opposition and reply are counted back from the hearing under CCP §1005(b); the
> attorney to confirm and calendar them. Not computed here.

**Good - a surfaced gap (anti-fiction):**

> The calendar entry "MSJ" on Aug 14 has no matching filed-MSJ task or note. Cannot
> show the motion as filed. Confirm whether the MSJ is filed and by whom.

**Good - a passed hearing date with no disposition:**

> Motion to Compel: the hearing was set for Jul 30 (the calendar entry "MTC hearing").
> No minute order or ruling in the record. Whether it was held, continued, or vacated is not shown;
> confirm what happened and any ruling.

**Good - file note, a matter with nothing on the motion calendar:**

```
[Operator] Motion calendar as of Sep 29, 2026
No motions on file.
Nothing to do.
```

**Good - file note, a hearing whose opposition is not on the calendar:**

```
[Operator] Motion calendar as of Sep 29, 2026
One motion: motion to compel, hearing Oct 6 at 9:30 a.m., Dept 3 (the calendar entry "MTC hearing"). The opposition and reply are not on the calendar; they are counted back from the hearing date.
Pat Lee to confirm the dates and calendar the opposition and reply.
```

(The calendar entry is named by its subject, the time is the firm's, and the rule
sentence appears because this matter has a hearing with a window missing.)

**Bad - invents a hearing date:**

> Motion to Compel: hearing likely mid-August; opposition due 2026-08-01.

(No event carries the hearing; the opposition date is computed and asserted. Both are
fiction.)

**Bad - asserts an outcome from silence:**

> Motion to Compel: the hearing passed last week, so it was granted. Closing it out.

(An outcome inferred from a passed date and silence, with no disposition in the
record.)
