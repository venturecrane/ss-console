# Discovery Response Tracker - Output Format

The direction and the decision determine the shape. A deadline is always keyed to
`(matter, discovery-set, direction)`. Nothing is written to the calendar or sent before
the stated confirm.

## Shape A (inbound) - Present the response deadline for one-click confirm, engine active

```markdown
# Response deadline - <matter> - <type> served <date> (<method>) - inbound - YYYY-MM-DD

**Engine:** court-rules engine active (Smokeball-InfoTrack).
**Engine date:** <date read from the engine via the Smokeball matter> - read, not computed.
**Decision:** surfaced to <responsible attorney> to confirm. Not calendared yet.

## Confirm to <attorney> (internal)

> The engine's response deadline for <type> on <matter> is <date>. Confirm to place it on
> the calendar and the matter task. <one-click confirm, bound to this set>

## File note (create_memo)

[Operator] <Routine name> as of <localDate>
Response deadline for <type> served <date> on matter <matter number>: the court-rules engine shows <date>; not on the calendar yet.
<Attorney> to confirm the date so it goes on the calendar and the matter task.
```

## Shape B (inbound) - Present the response deadline for confirm, computed by hand

```markdown
# Response deadline - <matter> - <type> served <date> (<method>) - inbound - YYYY-MM-DD

**Engine:** not run here - firm computes by hand (configured).
**Proposed deadline (confirm):** base 30 days (§<2030.260|2031.260|2033.250>) from
<service date>, plus <method extension> (§<1013(a)|1013(c)|1010.6(a)(3)(B)>) = <date>.
<If a +2-court-day method or a possible local rule: final day marked "confirm - court-day
count / local rule not applied here.">
**Decision:** proposed for <responsible attorney> to confirm. Not calendared yet.

## Confirm to <attorney> (internal)

> Proposed response deadline for <type> on <matter>: <date> (30 days + <extension>,
> §<...>). Confirm to calendar it, or correct the date. <one-click confirm>

## File note (create_memo)

[Operator] <Routine name> as of <localDate>
Proposed response deadline for <type> served <date> on matter <matter number>: <date> (30 days plus <extension>, <statute>); not on the calendar yet.
<Attorney> to confirm or correct the date.
```

## Shape C (outbound) - Surface the compel track for a decision (past due or thin)

```markdown
# Opposing discovery - needs a decision - <matter> - <type> - outbound - YYYY-MM-DD

**Extension check:** <no extension recorded in the matter - but extensions are often
granted by email and may not be in the record: confirm none is on file | a recorded
extension moves the deadline to <date>, re-anchored, no flag>
**Observation:** <the response window passed on <date> with no response | a response
received <date> appears thin: <what was observed, factual, not a sufficiency ruling>> -
**unless an extension was granted (confirm none is on file).**
**Track (observation, not a citation):**

- <no / late / UNVERIFIED response → no-response track: objections generally waived; no
  meet-and-confer prerequisite and no 45-day clock. An unverified response is treated as no
  response (§2030.250 / _Appleton_).>
- <thin but VERIFIED response → compel-further track: a meet-and-confer declaration is
  required and the window runs from service of the verified response. The compel-further
  section and day-count belong to `meet-and-confer-drafter`.>
  <For RFAs no/late response: **higher severity - deemed-admissions exposure, §2033.280.**>
  **Decision (attorney):** informal meet-and-confer first, or a meet-and-confer letter?
  If a letter, it is drafted by `meet-and-confer-drafter` (that skill owns the letter and
  the compel-section citation). **This skill does not send anything, does not write the
  letter, and does not assert the compel section or the day-count.**

## File note (create_memo)

[Operator] <Routine name> as of <localDate>
Opposing discovery: <type> on matter <matter number>, <no response by <date> | a response received <date> looks thin>, unless an extension is on file.
<Attorney> to decide: informal meet-and-confer first, or a meet-and-confer letter.
```

## The confirmation memo (inbound, `create_memo` body written ON attorney confirm)

When the responsible attorney confirms an inbound deadline (Shape A or Shape B), the
skill writes the calendar event and matter task and, in the same step, a `create_memo`
that records the confirmation as an auditable bookkeeping entry, in the file-note shape.
The memo MUST carry all four facts: who confirmed, the day, the date, the source:

```markdown
[Operator] <Routine name> as of <localDate>
Response deadline for <discovery type> on matter <matter number> confirmed by <responsible attorney full name> on <date confirmed>: <the confirmed deadline>. Source: <Smokeball court-rules engine | proposed by Operator>.
Nothing to do.
```

- **Confirming attorney's full name** comes from the roster (`get_staff` on the matter's
  `personResponsibleStaffId`), never a bare staff id and never a guessed name.
- **The day it was confirmed** is the firm's local day, written the way the firm
  writes a date; never an ISO timestamp or a UTC clock.
- **The confirmed date** is the date the attorney approved, verbatim.
- **The source** names which branch produced the date: `Smokeball court-rules engine`
  when the engine's date was read (Shape A), `proposed by Operator` when the skill
  computed the date by hand for confirm (Shape B). This keeps the provenance auditable:
  a reader can always tell whether the confirmed date was engine-read or by-hand-proposed.

The memo is written only after the attorney confirms; nothing about the confirmation is
logged before it. The confirmation memo does not restate the compel section or any
day-count the skill is not entitled to assert.

## Shape D - Surface to a human (fail-closed)

```markdown
# ⚠ Discovery deadline - needs a human - <matter> - YYYY-MM-DD

**Situation:** <proof of service unreadable / method or date ambiguous | discovery type
unclear | deadline mode (engine vs by-hand) unconfigured | engine active but the
discovery-response event cannot be identified or there are multiple candidates | court-day
count, §2016.060 final-day roll (final date on a weekend/holiday), or local rule in play |
outbound deadline passed but an extension cannot be ruled out from the record |
"late"/"thin" cannot be established from the record | no attorney confirm yet and deadline
near>
**Decision:** surfaced for a person. Nothing computed as final, nothing calendared,
nothing sent. This is a judgment the skill does not make on its own.
```

## Rules

1. **No shape writes to the calendar or sends to a party.** Inbound writes the
   event/task only **after** the attorney confirm; outbound Shape C flags a decision and
   never drafts or sends the letter.
2. **Engine active → read, never recompute** (Shape A). Firm-by-hand → compute as a
   proposal only (Shape B). Mode unconfigured, or an engine active but its
   discovery-response event cannot be identified (or multiple candidates) → Shape D, never
   a guess and never a silent default.
3. **Every computed date shows its arithmetic and its grounded statute.** A date with no
   cited window is not presented; it is surfaced (Shape D).
4. **"Appears thin" is a surfaced observation, never a sufficiency ruling.** The attorney
   makes the legal call.
5. The decision and its reason are always stated, so the deadline chain is auditable.
6. **Final-day roll (§2016.060).** A computed final date that lands on a weekend or court
   holiday is marked "confirm - rolls to the next court day (§2016.060)"; the skill does
   not compute the rolled day. This applies to every method, including calendar-day mail
   extensions.
7. **Outbound never asserts "late" over a possible extension.** A recorded extension
   overrides the computed date; an unrecordable email extension that cannot be ruled out
   makes "late" unestablished → Shape D, never a bare late flag. Every past-due observation
   is coupled with "unless an extension is on file - confirm none is."
8. **Outbound surfaces the track, never the compel section.** No/late/unverified → the
   no-response track; thin verified → the compel-further track (window from service of the
   verified response). The compel section and day-count belong to `meet-and-confer-drafter`.
9. **The inbound confirmation memo carries all four fields.** On attorney confirm, the
   `create_memo` records the confirming attorney's full name (from `get_staff`), an
   day it was confirmed (the firm's local day), the confirmed date, and the source branch (`Smokeball court-rules
engine` vs `proposed by Operator`). A confirmation logged without any one of the four
   is incomplete. Nothing is logged before the confirm.
