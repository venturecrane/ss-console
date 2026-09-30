# Motion Calendar Tracker - Output Format

One matter, one surface. The surface is **the record, organized**: every row names
the Smokeball record it came from the way a person finds it, and anything the record
does not support is a **gap**, not a value. There are no outbound drafts here: this
skill only surfaces and writes a file note.

**Name a record by what a person sees, never by its id** (`_shared-write-posture.md`,
section 5): a calendar entry by its subject and date ("the MSJ hearing entry on Oct 6"),
a task by its title and due date ("the task File opposition, due Sep 22"), a note by
its date ("the note of Sep 14"). A paralegal finds a record by what it says; the
audit log holds the ids.

## Shape A - Motion calendar surface (this run's report, never the note)

```markdown
# Motion Calendar - matter <matter number> - <today, the firm's way>

Matter status: <Open | Pending | ...>. Responsible: <staff name>.
Window: <from> to <to>. Read: <N> calendar entries, <M> tasks.

## Filed

| Motion                                        | Filed by        | Filed (source)                                   | Status (source)                                                   |
| --------------------------------------------- | --------------- | ------------------------------------------------ | ----------------------------------------------------------------- |
| <e.g. Motion to Compel Further RFP Responses> | firm / opposing | <date> (the task "<title>" / the note of <date>) | <filed / opposed / submitted / heard, each from a record, or "-"> |

## Due

| Item                     | Date   | Source                                              | Note                                                                                   |
| ------------------------ | ------ | --------------------------------------------------- | -------------------------------------------------------------------------------------- |
| <e.g. Opposition to MTC> | <date> | the task "<title>" / the calendar entry "<subject>" | set by a person                                                                        |
| <e.g. Reply to MSJ>      | -      | -                                                   | not on the calendar; hearing <date>; summary judgment has its own timing rule, confirm |

## Hearings

| Motion   | Hearing date | Dept and time                          | Source                         |
| -------- | ------------ | -------------------------------------- | ------------------------------ |
| <motion> | <date>       | <Dept, localTime, if the entry has it> | the calendar entry "<subject>" |

## Gaps to confirm (surfaced, not filled)

- <the "<subject>" hearing on <date> has no matching filed motion in the record; confirm>
- <the motion filed <date> (task "<title>") has no hearing set; confirm>
- <opposition and reply for <motion> are not on the calendar; confirm and calendar>
- <the calendar entry "MSJ?" on <date> cannot be placed; confirm what it is>
```

## Shape B - Gap or ambiguity dominates (nothing clean to surface)

When the record is too thin or too ambiguous to assemble a trustworthy calendar, do
**not** manufacture one. Lead with the gap.

```markdown
# Motion Calendar - gaps to resolve - matter <matter number> - <today, the firm's way>

Situation: <hearing on the calendar with no filed motion | motion filed with no
hearing | status reported in a note but unconfirmed | entry too ambiguous to place>.
What the record shows: <the item(s), named by subject and date, e.g. "the calendar
entry 'MSJ hearing' on Oct 6; no filed-MSJ task or note">.
What it does not show: <the missing piece, stated as missing, never inferred>.
Nothing computed, drafted, or asserted; this is for a person to decide.
```

## File note (create_memo)

One note per matter. The tables above are this run's report, never the note: a note
carries no table. Every hearing time is the event's `localTime` on its `localDate`
("Oct 6 at 9:30 a.m."), never its `startTime`.

**A matter with nothing on the motion calendar** (no motion, no hearing, no motion
task) gets exactly two lines after the header, and nothing else: no rule sentence, no
"attorney to confirm".

```
[Operator] <Routine name> as of <localDate>
No motions on file.
Nothing to do.
facts <facts_digest from the wake line, only when it gives one>
```

**A matter with motions**: the found line names what is on the calendar by subject and
date. The rule sentence appears **only when a motion with a hearing date exists on this
matter and its opposition or reply is not on the calendar**, once, in plain words. The
to-do line names a person only when there is something to confirm; otherwise it is
"Nothing to do."

```
[Operator] <Routine name> as of <localDate>
<X> motions filed, <Y> due, <V> hearings; next: <motion> hearing on <date> at <time>, Dept <dept>. <Only when a window is missing: The opposition and reply for <motion> are not on the calendar; they are counted back from the hearing date, and summary judgment has its own longer timing.> <Gaps: <each gap named by subject and date>, or nothing.>
<Attorney> to confirm and calendar <the missing opposition and reply, or the gap named above>, or: Nothing to do.
facts <facts_digest from the wake line, only when it gives one>
```

The `facts` line is the wake line's `facts_digest` for this matter, copied exactly
(`_shared-write-posture.md`, section 5). No `facts_digest` on the wake line, no facts
line. What this routine did on earlier runs belongs in the report, not the file:
the note speaks only about the matter.

## Rules

1. **No outbound drafts in any shape.** This skill surfaces and writes a file note;
   it never drafts a motion, opposition, reply, or client/court message.
2. **Every Filed / Due / Hearing row names its source by subject and date** (the
   calendar entry "<subject>" on <date>, the task "<title>" due <date>, the note of
   <date>), never by id. A row with no source is not a row: it is a gap.
3. **A missing due date is never computed.** Dates a person set are surfaced with
   their source; windows nobody set are surfaced as the hearing date plus the gap,
   never as a stated date.
4. **A missing hearing date or motion status is a gap**, surfaced with what the record
   does and does not show, never a plausible fill.
5. **A hearing outcome appears only if the record states it, and occurrence is never
   inferred from a passed date.** The record shows a hearing was _set_ for a date, not
   that it was held; hearings get continued, vacated, or taken off calendar. A passed
   hearing date with no minute order or disposition in the record reads "the hearing
   was set for <date> (calendar entry "<subject>"); no minute order or ruling in the
   record; confirm whether it was held, continued, or vacated," never "heard <date>"
   and never "granted" or "denied."
6. **A reported-but-unstructured status is surfaced as "reported in the note of
   <date>, unconfirmed,"** never re-asserted in the skill's own voice.
7. **Nothing to report is two lines**, the header aside: "No motions on file." and
   "Nothing to do." The note speaks only about the matter.
