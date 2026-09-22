# Mediation & Settlement Tracker - Output Format

Derives from `operator/verticals/law-firm/addons/pi/references/_shared-assembler-output-format.md`
(the assembler half) and `operator/verticals/law-firm/addons/pi/references/ca-served-discovery-capture-spec.md`
(the deadline-capture posture - a computed date is a proposal for attorney confirm,
never final). Every filled component is traceable to a matter read. The brief itself,
any argument, and any valuation are never authored. No §998 or MSC date is asserted as
final.

## Shape A - Brief inputs assembled + deadlines tracked (proposed-confirm)

```markdown
# Mediation/MSC Prep - <matter descriptor> - matter <id> - YYYY-MM-DD

**Conference:** <mediation | mandatory settlement conference> - date <read from list_events, proposed-confirm> (not finalized by the skill)
**Decision:** brief INPUTS assembled from matter components and staged for <attorney / co-counsel> to write the brief; §998 and conference deadlines surfaced for confirmation.
**Source components:** <list each component + the document + folder it was read from>

## Brief inputs (staged for <attorney / co-counsel> to write from - the brief is NOT written here)

### Liability summary

> <pointer to / verbatim quote of the liability summary document read; omit if none, list as a gap>

### Medical chronology & specials

> <pointer to / verbatim quote of the chronology and specials figures as read; figures verbatim, never estimated>

### Damages figures

> <the damages figures as read from the matter, verbatim; never computed or "cleaned up">

### Demand & offer history

> <the demand and offer letters/figures as read, verbatim>

### Policy-limits note (if in the matter)

> <verbatim as read; omit if none>

### Brief argument / valuation

> [ATTORNEY / CO-COUNSEL TO AUTHOR: the statement of liability, the damages argument, and the case value. The tracker does not draft this.]

## Deadlines (proposed - confirm with the engine/attorney; NOT calendared as final)

- **CCP §998 offer.** Served <date, read from the offer document>. Proposed acceptance
  window: the shorter of 30 days after service or the start of trial ("whichever occurs
  first"), deemed withdrawn thereafter - CCP §998. **Proposed, confirm** (verify the
  operative cutoff against the trial date / the certified engine; cost-shifting
  consequences make this attorney-confirmed, never skill-final).
- **Conference date.** <mediation/MSC date read from list_events> - **proposed,
  confirm**; the skill reads it, it does not compute or finalize it. Any local court
  brief-lead-time/format rule is flagged, not computed (venues not yet configured).

## Tracked item (create_task - confirm-by-read)

- Assigned to <responsible attorney, personResponsibleStaffId>. `dueDateOnly` is a
  near-term administrative **confirm-by** date (a day or two out) to confirm the §998
  and MSC deadlines - distinct from the §998 acceptance date and the MSC date, which
  stay proposed-confirm and are never silently calendared as final.

## Gaps / needs a human

<any missing or unreadable brief-input component; an unreadable §998 offer or trial
date; more than one candidate conference event - listed, never guessed>

## Internal log (create_memo body)

> Mediation/MSC prep for matter <id>: brief inputs assembled from <N> components
> (<docs>), staged for <attorney/co-counsel>; §998 window and conference date surfaced
> as proposed-confirm, not finalized. Gaps: <...>.
```

## Shape B - Cannot assemble / cannot resolve (missing or unreadable components)

```markdown
# ⚠ Mediation/MSC Prep - cannot complete - matter <id> - YYYY-MM-DD

**Situation:** <which required components are missing, unreadable, or ambiguous - e.g.
"no mediation/MSC event located on the matter calendar"; "liability summary and
damages figures not in the matter"; "§998 offer document present but its service date
is unreadable"; "trial date the §998 'whichever occurs first' cutoff turns on cannot
be read">
**Decision:** surfaced for a person; not assembled from partial or invented data, and no
deadline finalized. No component, figure, or date was fabricated to fill the gap.
```

## Shape C - Refuse (asked to write the brief or finalize a §998/MSC deadline)

```markdown
# ⚠ Mediation/MSC Prep - request refused, surfaced for a person - matter <id> - YYYY-MM-DD

**Request:** <"write the mediation brief" / "draft the damages argument" / "state the
case value" / "put the §998 acceptance deadline of <date> on the calendar as final">
**Decision:** refused. The brief, its argument, and its valuation are the attorney's or
co-counsel's work product - the tracker assembles inputs only. A §998/MSC deadline is
never finalized by the skill; it is surfaced for confirmation. The §998 mechanics are
flagged for the attorney/engine to confirm (making window "not less than 10 days before
trial"; deemed withdrawn if not accepted before trial or within 30 days, whichever
occurs first; cost-shifting on failure to obtain a more favorable judgment - CCP §998).
**Surfaced instead:** the assembled brief inputs (Shape A) and the §998/conference dates
as proposed-confirm.
```

## Rules

1. **The brief is never written.** The brief-argument/valuation cell is always the
   labeled blank `[ATTORNEY / CO-COUNSEL TO AUTHOR: ...]`. The skill never drafts the
   brief, never argues liability or damages, never states the case value.
2. **No figure is computed or estimated.** Every specials number, demand, offer amount,
   and damages figure is a verbatim read. A value that cannot be sourced is a gap
   (Shape B), never a fill-in.
3. **No deadline is finalized.** The §998 acceptance window and the MSC/mediation date
   are surfaced as **proposed, confirm** - the certified engine/attorney owns the
   computation; the skill captures inputs and reads the calendar. A tracked task's
   `dueDateOnly` is a near-term administrative confirm-by date, distinct from the legal
   deadline. There is no calendar write.
4. **§998 is flagged, not asserted.** The mechanics are presented for confirmation
   (making window, deemed-withdrawn "whichever occurs first," cost-shifting), grounded
   in CCP §998; never an on-its-own final acceptance-expiry date.
5. **Writes are confirm-by-read.** `create_task` and `create_memo` are reported as done
   only after a confirming read; otherwise the failure is surfaced.
6. **The decision and its reason are always stated**, so the packet and the tracked
   deadline are auditable.
