# Output format (task-list-keeper)

Every sentence is a constant in `lines.py`; this page shows the shapes. The
overlay's `casework_finish` assembles the message from the envelope this skill
writes, in this order, blocks separated by blank lines: the lead, "Done since
last time", "Closed just now", any write it could not make, the numbered
lines under their matter headings, the footer.

## Addressing

One message per recipient set, at most ten a run:

- a proposal goes to the matter's routed recipients (the firm's authored
  case-alert routing: under `matter_staff`, the responsible attorney), with the
  assisting staff copied when they are on the roster;
- a handover of an Operator-own task goes to the matter's assisting staff when
  one is on the roster, else to the routed recipient;
- a matter no authored route reaches gets no message (the fail-closed floor);
  its tasks are counted as `unroutable` on the heartbeat row.

A message with no numbered line is not sent: no decision, no message.

## Subject

`[Tasks] <count> tasks to review on your matters`

## Lead

`These tasks on your matters are past due. Each has my suggested call.`, or,
when every line is a handover, `I opened these tasks and can't finish them.
Each has my suggested call.` When more than `max_lines` lines were due, it
adds `<n> more tasks wait for the next review.` and, when the firm's schedule
names a day, `The next review is on <day>.`

## Numbered lines

Grouped under `matter <number>` (or the authored absence), numbered from 1 in
reading order. The label is the task subject reduced by
`digest_items.display_label` (a caption, a citation or a fabrication marker
drops the label; digits and dates are masked).

- Done: `"<label>", due <day>. Looks done: <a proof of service | a verification | a records file> dated <day> is on the matter. Suggest: close it.`
- Records chase resolved: `... Looks done: the records chase for it is resolved. Suggest: close it.`
- Stale: `... The matter is closed. Suggest: close it.` / `... Same task as another open one on this matter. Suggest: close it.`
- Open: `... Still open. Suggest: leave it open, and I won't list it again for <keep_quiet_days> days.`
- An Operator-own task: the same, prefixed `I opened this task.`
- Handover: `I opened "<label>", due <day> and can't finish it: <closed reason>. Suggest: assign it to you.` (or `It needs an owner at the firm.` when nobody can be named)
- At `surfaces`: the done or stale line ending `You can close it in Smokeball.`

## Closed just now (Operator-own, level handles)

`matter <number>: "<label>", due <day> (<evidence> is on file)`

## Done since last time (Job 3)

`matter <number>: a task I closed on <day>, <evidence> was on file`, joined on
one line after "Done since last time:". Each close is told once; the overlay
writes the `mentioned` row after the send. At `quiet: handles` the matter also
gets a memo: `Task list upkeep: I closed <n> tasks on this matter that the
record showed were done (<evidence>; ...).`

## Footer

`Reply here in words: say yes to all, or name the numbers to leave as they are. Nothing on your task list changes until you answer. This is an internal note; no client was contacted.`

No literal example numbers: a reader who copies an example answers a line they
never read.
