# Deadline Miss Escalator - Output Format

One internal alert to the recipients case-alert routing resolves (central =
the authored red-flag recipients), triaged so the consequential items are read
first and the routine ones do not bury them. Nothing here is client- or
tribunal-bound. No em dashes anywhere; refer to a matter by its number and
the client's surname read off the matter's own title, never its caption; say
what an item is in the record's own words (the task subject, the calendar
title), never ours ("court-date", "task-deadline"); state the governing rule in
plain words, never as a citation.

**RENDERED IN CODE (WS-RENDER).** This document is the human spec the
renderer is tested against: `render.py` implements the templates below
literally, in `pre_run`, and the seat dispatches the result out of turn. The
model no longer composes this alert; every instruction below addressed to
"the turn" describes what the RENDERER does. The consequence line's closed
signal set and its authored phrases live in `render.py` (`consequence_line`):
a task-priority marker the record carries, or, on a seat that authored
`case_manager.date_prep`, a court date inside the prep window that no date-prep
brief has gone out for ("No prep note has gone out for this yet.") - an
unknown signal renders nothing. The line says what the item still needs, never
what kind of item it is: "a court date the firm authored" rendered under every
court date until 2026-09-28 and asked the reader for nothing. The rich examples in the template below
(deemed-admission, disbursement blocker, opposing-counsel held) have no
code-detectable authored source today and therefore do not render.

## The triaged alert (internal, to the red-flag recipient)

The alert leads with the few items that genuinely need a person today, collapses
the rest to per-matter counts, and numbers every line a reader can answer, so
they can reply in plain words ("got it on 1") and quiet one item without
silencing the rest. No code of any kind appears in the email.

**The digest supplies the VALUES and the MEMBERSHIP. The templates below supply
the WORDS and the MARKUP.** The turn never prints the projection's field names,
never prints its rows as data, and never invents a heading. Every `##` heading,
every `-` and every `1.` in the templates below is required literally in the
body you send: an alert whose lines carry no list marker renders as one run-on
paragraph in a mail client, which is what happened on 2026-08-25.

"Rendered verbatim" below means the COUNTS and the BANDING are copied, not
recomputed. It has never meant "print the digest". `<number>` in every template
below is the
digest item's `matter_number` - the connector's code join on the gate's own
pull (ss #2390), copied verbatim. When it is null: `matter_number_absent:
no_number_on_record` renders "matter with no number on record" (the firm's record carries
no number); any other absence renders "matter number unavailable". Never a
GUID, never a composed or remembered number. Section membership, per-matter groups, item
numbers, section counts, and the subject line are all computed by the pre-run
gate over the full item universe; the turn re-counts nothing and moves nothing
across bands. Subject semantics: `<N>` counts the items a person must act on
by name, "Needs you today" plus "Open without a task id", and never the "Also open"
overflow (ss #2405: the 2026-08-14 subject said "37 need you" when 5 needed a
person; 2026-09-24: a recipient holding only blanket items read "0 need you").
When that count is exactly one, the subject names the item instead of counting
it ("[Deadlines] Okafor: Final Status Conference Fri Oct 2"), falling back to
the count ("1 date needs you") only when the item has neither a surname nor a
number (`digest_items.subject_line`). The `[Deadlines]` prefix never changes:
inbox routing reads it (`matter-inbox-router`).
Membership in the top band is deterministic and PER RECIPIENT: the digest is
split by recipient, then each recipient's stable firing items are re-banded,
top five by authored priority marker, then most overdue, then stable
tie-breaks (`digest_items.needs_you_key`), and the rest go to "Also open". Before
2026-09-24 the top five were picked seat-wide and split afterwards, so a
recipient whose items ranked sixth got "0 need you". The footer paragraph is a
SIBLING of the lists, never nested inside one (the 2026-08-14 HTML rendered it
as a list child). When the digest carries `probe_artifacts`, render one plain
footer line naming the excluded count and any stale probe task ids awaiting
teardown (ss #2403).

**Every section below is conditional.** A section with nothing in it is OMITTED
whole: its heading, its count, and its body all go. It is never rendered as a
zero-count heading over the word "None". Only "Needs you today" is unconditional,
because an alert with nothing in that band is not sent at all (rule 8). Skipping
an empty section is not hiding anything: an item exists in exactly one band, so a
band with no items has nothing to disclose. See rule 9.

```markdown
Subject: [Deadlines] <surname or number>: <what> <when> [exactly one item]
Subject: [Deadlines] <N> dates need you, <Mon D> [any other count]

## Needs you today (<count>)

<preamble, one of three; see below>

1. <number> <surname>: <what>, <when> (<overdue N days | today | tomorrow | in N days>)
   <one plain line of what it still needs: the authored signal only, e.g.
   "the task is marked URGENT in Smokeball" / "No prep note has gone out for
   this yet.">
2. ...

## Also open (<count> across <M> matter(s)) [omit section if 0]

More open items past the top five, one line per matter. Answering a matter's
number covers all of its items; each one is listed in Smokeball.

6. <number> <surname>: <k> more open item(s)
7. ...

<N> more overdue task(s) are in <Day>'s task review. [case-manager seats with task_cleanup only; omit if 0]

On a seat that authored `case_manager.task_cleanup`, this line takes the
overdue tasks out of "Also open": they are in the weekly task review
(`task-list-keeper`), so the digest counts them instead of listing them, and
raises none of them. `<Day>` is the weekday of the firm's own review schedule;
with no single day it reads "the next task review". Court dates and tasks not
yet overdue stay in "Also open". Unconfigured seats never render this line.

Done since last time: <line>; <line>. [case-manager seats with quiet only; first in the body; omit if none]

On a seat that authored `case_manager.quiet`, the body opens with this line
(done, then needs you): one `done_since.py` line per task the Operator closed on
the record's evidence or date-prep step it ran at "Handles it" that nobody has
been told about, for this recipient's matters. The skeleton never carries it,
and it never makes a digest of its own. The dispatch lists the items as
`casework_mentions`; the overlay writes each `mentioned` row after a FULL send
only. Unconfigured seats never render this line.

## Under active escalation elsewhere (<count> across <M> matter(s)) [omit section if 0]

Already raised, shown so it is not double-counted. No action here.

- <number> <surname>: <k> item(s) under active escalation (last raised <date>).

## Awaiting clearance (<count>) [omit section if 0]

Held matters with an approaching date. Surfaced for a person to clear; never a
client-facing step.

- <number> <surname>: <what>, <when> (<relative>). The matter is on CONFLICT-HOLD.

## Open without a task id (<count>) [omit section if 0]

Items with no task id in Smokeball, one line per matter. Answering a matter's
number covers every item listed under it.

8. <number> <surname>: <k> open item(s) with no task id
   - <number> <surname>: <what>, <when> (<relative>)
   - ...

Reply to this email with the numbers you have, or say all. Each one you answer
goes quiet for <ack_snooze_days> days; finishing it in Smokeball clears it for
good. This is an internal note; no client was contacted.
```

**The numbers** are assigned in code (`digest_items.number_firing`) in the
order the lines render: one per needs-you item, then one per "Also open"
matter, then one per "Open without a task id" matter, continuous from 1. The
number on a line IS the `n` on that item's `fired` rows (a matter's number is
on every one of its rows), so what the reader types resolves to exactly the
rows behind the line. The footer carries no example numbers: a reader who
copies an example answers an item they never read.

**Every numbered line starts `N. matter ` or `N. NNNN-`**, single items and
group lines alike. The overlay's reply parser uses exactly those two shapes
(`^[\s>]*\d{1,3}\.\s+(?:matter\b|\d{4}-)`, `reply_items.py` at the pinned
overlay) to recognize quoted digest text in a reply and refuse to read numbers
from it, so a line that breaks them would let a quoted digest be read as an
answer. A matter number shaped `NNNN-...` ("2026-PI-105") leads its line bare;
any other number keeps the word in front ("matter 201520 Crawford"), and an
absent one reads "matter with no number on record" or "matter number
unavailable" (`digest_items.head_words`). The item lines listed under an "Open
without a task id" group are `-` bullets, never numbered.

**No number without a row.** Numbering stops at the envelope's append cap
(`dispatch_envelope._MAX_APPENDS_PER_DISPATCH`): a line whose rows would not
all fit under the cap renders with a `-` and no number, as does every line
after it. A body with no numbered line at all (the skeleton rung, or any body
delivered with no raise rows behind it) carries no invitation to reply; its
footer reads "Finishing an item in Smokeball clears it. This is an internal
note; no client was contacted." Elsewhere and clearance lines are never
numbered: there is nothing to answer.

**The needs-you preamble** says what the order is, and only when the order
carries information: "Ranked by what the record says, most consequential
first." when any item carries an authored priority marker; "Most overdue
first." when the items' dates differ; no preamble line at all when the items
are indistinguishable. `<M> matter(s)` is singular for one matter.

**`<what>`** is the Smokeball task subject or calendar title reduced at
parse time (`digest_items.display_label`) to what the send gate will pass: the
`[Operator]` stamp stripped; a `" - "` segment carrying a case caption (`v.`,
`vs.`, `versus`, `in re`) cut off with everything after it, so "Final Status
Conference - Okafor v. Grand Valley Market (Dept 47)" reads "Final Status
Conference"; any date, dollar figure, case or matter number, other identifier,
or run of three or more digits masked as `…`; markdown characters neutralized;
capped at 100 characters. A caption in the first segment, a legal citation, or
a fabrication marker renders no label, and the line says "a task" or "a
calendar date" instead. The raw subject never enters the digest, the wake
payload, or the envelope. Known limit: "Also open" lines carry a count, not
task labels.

**`<surname>`** is the client's surname read in code off the matter record's
own `title` (`digest_items.matter_name`): the title's first `" - "` segment
must be the matter's number, the second is the client ("Okafor, Denise" reads
"Okafor"). The connector's pull attaches the title from the same `/matters/{id}`
read that supplies the number, so it costs no extra call. A title in any other
layout, several clients, or anything that is not a plain surname renders no
surname, and the line names the matter by number alone. Never the caption:
"Okafor v. Grand Valley Market" is exactly what the send gate's citation scan
refuses in a pre-rendered body, because the dispatch runs before the turn reads
anything and the handoff seeds no captions; a refused full body falls back to
the counts-only skeleton.

**`<when>`** is the authored date spelled out, never computed: "due Jul 8,
2026" for a task, "Fri Oct 2, 2026" for a calendar date. The year stays in the
body so the send gate's per-line pairing still sees the matter number beside
its date and judges the pair against what this run read. The subject drops the
year ("Fri Oct 2", "due Jul 8", "overdue since Jul 8"): it carries no matter
number to pair with.

## The confirmation reply (internal, after an ack)

**A plain-word reply** ("got it on 1 and 3", "all") is confirmed with the
`confirmation_text` that `reply_verdicts` returns, sent verbatim and
nothing else. The tool renders it in code from what it actually wrote: which
numbers went quiet (naming each item), for how long, and which numbers are
still open. When it wrote nothing (the reply named no number, named a number
the digest does not have, or the thread holds no digest rows), its text is a
question back to the reader; that is sent verbatim too. An empty
`confirmation_text` means no reply is sent at all (no verified reply, an
automatic reply, or a sender who is not rostered). The turn never composes,
trims, or adds to any of these.

**A legacy reply quoting `ACK-XXXXXX` codes** (a digest sent before the
numbered format) is confirmed with the template below: it enumerates exactly
what was acked and counts what remains, so an under-ack (a mail client
trimming quoted text) stays visible.

```markdown
Acknowledged <A> item(s): <ACK-XXXXXX> matter <number>, <label>; ...
Still open and not acked: <R> item(s). They will surface again on the next run.
Acked items go quiet for <ack_snooze_days> days unless resolved sooner in Smokeball.
```

## What this must never look like

The 2026-08-25 alert, sent and unusable. Every line below is a real defect, and
all of them are one deviation: the turn wrote its own shape instead of the
templates above.

```
NEEDS YOU TODAY                                          <- not a heading, no count

2026-PI-101 | task-deadline | due 2026-08-26 | 1 day out <- the projection's fields
No Operator raise on record.                                printed as a data row
ACK: ACK-YED4HY

UNDER ACTIVE ESCALATION ELSEWHERE (no action required from you)   <- invented title

2026-PI-106 | task-deadline | authored 2026-07-08 | 48 days overdue | last raised ...
2026-PI-106 | task-deadline | authored 2026-07-08 | 48 days overdue | last raised ...
... 36 more, 20 of them the same matter, none distinguishable from another
```

Four things went wrong and each has a rule above:

1. **No `##`, no `-`, no `1.`.** The only markdown block marker in the whole
   4,280-character body was a single `---`. The html renderer had no heading and
   no list item to emit, so every band collapsed into one run-on paragraph.
2. **Field names reached the reader.** `authored_date` became the English word
   "authored", so a court date one day out read "authored 2026-08-26", which
   says the opposite of what it means. Use the templates' own words:
   `<what>, <when> (in N days)`.
3. **A band was rendered flat.** 38 rows, 20 for one matter. Both grouped bands
   arrive pre-collapsed; render one line per matter, never one per item.
4. **An item-scoped truth read as a matter-scoped contradiction.** "No Operator
   raise on record" is true of that ITEM. Directly above 20 rows of the SAME
   matter marked "last raised", it reads as the alert contradicting itself. Say
   "no prior raise on this item".

## Rules

1. **Triage by authored signal only.** Order "Needs you today" by what the
   record carries: a task-label marker (CRITICAL / URGENT / HIGH PRIORITY on the
   Smokeball task), a consequential category (deemed-admission exposure, a money
   or disbursement blocker, opposing-counsel inbound held), then overdue age.
   Never invent an urgency the data does not state. If nothing carries a high
   signal, the top block is simply the most overdue items, plainly labeled.
2. **Up to five in the top block, per recipient.** More than five is not a
   priority list. Everything else is a per-matter count in Also open, answerable
   by the matter's one number. Nothing in the record says an overflow
   item is routine, so the band never calls it that.
3. **Numbers, never codes.** Each needs-you item carries its own number, and
   answering it quiets only that item. An "Also open" matter or an "Open
   without a task id" matter carries one number covering all of its items. The
   rows still carry the legacy `ACK-XXXXXX` token so codes already sent keep
   working, but no code is printed.
4. **One disclaimer, in the footer.** The reply mechanics and the "internal
   note; no client was contacted" line appear once, at the end, not per item.
5. **Reader-facing section names.** "Needs you today", "Also open", "Under
   active escalation elsewhere", "Awaiting clearance", "Open without a task id". No internal ladder jargon
   (no "notify" / "re-route" / "re-surface") in the reader's copy.
6. **Every rung is internal.** No client or tribunal send on any path. With no
   authored red-flag recipient, the alert has nowhere to fire and does not fire
   (fail-closed); the escalation ledger still records the fire for the record.
7. **Dates and figures are authored, never computed.** The alert names the
   authored date and its source label, never an estimated or derived date, and
   states a dollar figure only when an authored source on the matter carries it.
8. **A `SUPPRESSED_WAKE` row stands in for the whole alert on a quiet tick.** It
   is the heartbeat; the agent does not wake to send an empty alert.
9. **An empty section is omitted whole, never rendered as a zero.** No
   `## Also open (0 across 0 matters)` followed by "None." - the heading,
   the count, and the body all go. The 2026-07-15 alert carried two real items
   under four consecutive zero-count headings; the reader scrolled past more
   nothing than something, and the top block is the whole point of the triage.
   A band with no items has nothing to disclose (an item lives in exactly one
   band), so omission hides nothing. The reader learns what needs them, not
   which internal bands the escalator maintains.
