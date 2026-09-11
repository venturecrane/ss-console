# Deadline Miss Escalator — Output Format

One internal alert to the recipients case-alert routing resolves (central =
the authored red-flag recipients), triaged so the consequential items are read
first and the routine ones do not bury them. Nothing here is client- or
tribunal-bound. No em dashes anywhere; refer to a matter by its number, never
its caption; state the governing rule in plain words, never as a citation.

**RENDERED IN CODE (WS-RENDER).** This document is the human spec the
renderer is tested against: `render.py` implements the templates below
literally, in `pre_run`, and the seat dispatches the result out of turn. The
model no longer composes this alert; every instruction below addressed to
"the turn" describes what the RENDERER does. The consequence line's closed
signal set and its authored phrases live in `render.py` (`consequence_line`):
a task-priority marker the record carries, or the court-date label — an
unknown signal renders nothing. The rich examples in the template below
(deemed-admission, disbursement blocker, opposing-counsel held) have no
code-detectable authored source today and therefore do not render.

## The triaged alert (internal, to the red-flag recipient)

The alert leads with the few items that genuinely need a person today, collapses
the routine confirmations to per-matter counts, and carries a per-item ACK code
so the reader can acknowledge one item without silencing the rest.

**The digest supplies the VALUES and the MEMBERSHIP. The templates below supply
the WORDS and the MARKUP.** The turn never prints the projection's field names,
never prints its rows as data, and never invents a heading. Every `##` heading,
every `-` and every `1.` in the templates below is required literally in the
body you send: an alert whose lines carry no list marker renders as one run-on
paragraph in a mail client, which is what happened on 2026-08-25.

"Rendered verbatim" below means the COUNTS and the BANDING are copied, not
recomputed. It has never meant "print the digest". `<number>` in every template
below is the
digest item's `matter_number` — the connector's code join on the gate's own
pull (ss #2390), copied verbatim. When it is null: `matter_number_absent:
no_number_on_record` renders "no number on record" (the firm's record carries
no number); any other absence renders "matter number unavailable". Never a
GUID, never a composed or remembered number. Section membership, per-matter groups, code
lists, section counts, and the subject line are all computed by the pre-run
gate over the full item universe; the turn re-counts nothing and moves nothing
across bands. Subject semantics changed with ss #2405: `<N>` counts ONLY the
"Needs you today" band (the 2026-08-14 subject said "37 need you" when 5
needed a person and 32 were routine confirms — earlier alerts' subjects
counted everything, so do not read them as evidence of a count bug under the
new scheme). Membership in the top band is deterministic: the up-to-5 most
overdue firing items with stable identity; ordering within the band and each
item's one-line consequence remain the turn's prose. The footer paragraph is a
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
Subject: [Deadlines] <N> need you, YYYY-MM-DD

## Needs you today (<count>)

Ranked by what the record says, most consequential first. Three to five items.

1. matter <number>, <label> <date> (<overdue by N days | due in N days>) [ACK-XXXXXX]
   <one plain line of why it is consequential: the authored signal only, e.g.
   "an unverified response is treated as no response" / "disbursement blocked
   until the lien payoff is confirmed" / "opposing-counsel letter held N days">
2. ...

## Admin confirms (<count> across <M> matters) [omit section if 0]

Routine confirmations, collapsed per matter. Reply with a matter's ACK codes to
clear its items, or open the item in Smokeball.

- matter <number>: <k> routine confirmation(s). [ACK-XXXXXX] [ACK-XXXXXX] ...
- ...

## Under active escalation elsewhere (<count> across <M> matters) [omit section if 0]

Already raised, shown so it is not double-counted. No action here.

- matter <number>: <k> item(s) under active escalation (last raised <date>).

## Awaiting clearance (<count>) [omit section if 0]

Held matters with an approaching date. Surfaced for a person to clear; never a
client-facing step.

- matter <number>: on CONFLICT-HOLD with <label> <date> approaching.

## Blanket-ack only (<count>) [omit section if 0]

Items with no stable task id, so they carry no individual ACK code. A blanket
acknowledgement (below) acks exactly the ones quoted here.

- matter <number>: <label> <date> (<overdue by N days | due in N days>).

Reply with the ACK code(s) above to acknowledge. Reply ESCALATION_ACKNOWLEDGED
to ack every item quoted in this message; items you do not quote stay open. An
acked item goes quiet for <ack_snooze_days> days, then re-surfaces if it is still
open in Smokeball. Completing the item in Smokeball is the only thing that closes
it. This is an internal alert to a person at the firm; no client message has been
sent.
```

## The confirmation reply (internal, after an ack)

When a rostered person replies acking codes, the confirmation reply enumerates
exactly what was acked and counts what remains, so an under-ack (a mail client
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
   says the opposite of what it means. Use the item's `label` and the templates'
   own words: `<label> <date> (due in N days)`.
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
2. **Three to five in the top block.** More than five is not a priority list.
   Everything else is a per-matter count in Admin confirms, with its items
   reachable by their ACK codes.
3. **Per-item ACK codes, keyed on the stable task id.** Each item with a stable
   Smokeball id carries its own `ACK-XXXXXX`. Acking one code suppresses only
   that item. Items with no stable id carry no code and live in Blanket-ack only;
   a blanket ack covers exactly the items quoted in the message.
4. **One disclaimer, in the footer.** The ack mechanics and the "internal alert,
   no client message sent" line appear once, at the end, not per item.
5. **Reader-facing section names.** "Needs you today", "Admin confirms", "Under
   active escalation elsewhere", "Awaiting clearance". No internal ladder jargon
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
   `## Admin confirms (0 across 0 matters)` followed by "None." — the heading,
   the count, and the body all go. The 2026-07-15 alert carried two real items
   under four consecutive zero-count headings; the reader scrolled past more
   nothing than something, and the top block is the whole point of the triage.
   A band with no items has nothing to disclose (an item lives in exactly one
   band), so omission hides nothing. The reader learns what needs them, not
   which internal bands the escalator maintains.
