# Consult Scheduler - Algorithm

Source of truth for "good scheduling." Order is fixed: gate, then find times, then draft-and-surface. Phase 0 can stop the skill.

## Phase 0 - Gate

Before anything else: check the matter's conflict state. If it is on CONFLICT-HOLD or carries an unresolved conflict flag, **stop** - emit the blocked-on-conflict form (`output-format.md`) and do not read availability, propose, or book. Scheduling a halted matter is the worst failure this skill can commit.

## Phase 1 - Find times (read-only)

1. **Load the matter + attorney.** `get_matter(matter_id)` for the practice area and the responsible attorney (`personResponsibleStaffId`, a direct field); `get_staff` to resolve that id to the attorney's name.
2. **Load the rules** from `customer.yaml`: consult length for the matter's practice area, business hours, blackout windows (holidays, court days, recurring blocks), buffer minutes between events.
3. **Load availability.** `list_calendar_entries(from, to)` for the attorney over the candidate window - via the mail/calendar binding (Google/M365), not the Smokeball PM connector (Smokeball has no calendar resource).
4. **Compute candidate slots.** A slot is valid only if it: is inside business hours; is outside every blackout window; does not overlap any existing entry; honors the buffer on both sides; is exactly the consult length for the practice area. Produce 2–3 valid slots.
5. **Apply client preference within the rules.** If the client stated a preference, prefer slots near it - but a preference never promotes an invalid slot. If no valid slot is near the preference, propose the nearest valid alternatives and say so plainly.

## Phase 2 - Draft and surface (no autonomous write)

1. **Draft the confirmation** per `voice.md`: the proposed time(s), the consult length, logistics (video link / office address from `customer.yaml`). Scheduling only.
2. **Surface the calendar booking.** Emit the `create_calendar_entry` payload as a **proposal** for a human to confirm - do not execute it. (Connect step: once the write capability is verified and the engagement authors it on, this becomes an autonomous write; not this phase.)
3. **Log** internally with `create_memo`.

## A legal question in the scheduling thread

If the client's scheduling message also asks a legal question ("do I qualify?", "what should I bring to win?"), the confirmation handles the scheduling and notes the substantive question is for the consult itself. It never answers the legal question - that is the consult's job and the attorney's.

## What this algorithm is NOT

- Not an autonomous booker this phase (the write is surfaced).
- Not a rule-bender (a client preference never overrides a firm rule).
- Not a legal advisor (scheduling only).
- Not a scheduler for halted matters (Phase 0 gate).
