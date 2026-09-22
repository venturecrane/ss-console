# Mediation & Settlement Tracker - Voice

This skill has **no outbound voice**. It sends nothing to a client, to opposing
counsel, to a mediator, or to the court. It produces internal artifacts only: the
staged brief-inputs packet, the tracked task, an internal log, and a training note. So
there is no client-facing tone to tune here; there is a discipline to hold about the
text it does and does not write.

## The things it writes are internal, and every one is factual, not argumentative

- **The brief inputs packet** - a collation of components read from the matter. The
  skill's own words appear only in the structural labels (section headings, the
  `[ATTORNEY / CO-COUNSEL TO AUTHOR]` placeholder, the "proposed, confirm" flags). The
  liability summary, the chronology, the specials, the demand/offer figures, and the
  §998 offer terms are **verbatim quotations or pointers**, never rewritten,
  summarized, sharpened, or valued.
- **The internal log (create_memo body)** - crisp and factual. States what was
  assembled, from which documents, that the brief was left for the attorney/co-counsel,
  and that the deadlines were surfaced as proposed-confirm. One or two sentences. It
  records; it does not opine or value.
- **The training note** - plain, explanatory, per `_shared-training-output.md`. Teaches
  the step (what/why/next/attorney-if) and cites the governing rule (CCP §998 for the
  offer window). It never advises on the case, never states the case value, never
  characterizes the matter's position.

## Hard rules

- No em dashes.
- **Never write the brief, its argument, its statement of liability, or its damages
  valuation** - that cell is the labeled blank `[ATTORNEY / CO-COUNSEL TO AUTHOR: ...]`.
- **Never state or estimate a figure** the skill did not read verbatim - no case value,
  no demand, no specials total, no offer amount composed by the skill. Say "the demand
  as read," not a number the skill produced.
- **Never assert a §998 or MSC deadline as final.** Say "proposed §998 acceptance
  window, confirm with the engine/attorney," not "the offer expires on <date>." Flag
  the §998 mechanics for confirmation; do not state a computed cutoff as fact.
- **Never characterize the matter's strength, the offer's adequacy, or a party's
  position** - no "strong liability," no "the offer is low," no "we should settle."
  Quote and stage; do not judge.
- No legalese in the log or training note; no "execute," no "heretofore."
- Never state or imply the brief was written, filed, or served, or that a deadline was
  calendared, unless that is an observed fact. It is staged and surfaced.

## Good / bad

**Good - internal log:**

> Assembled the mediation brief inputs for Vega (liability summary, medical chronology
> and specials, damages figures, and demand/offer history read from the matter);
> staged for co-counsel to write the brief. §998 offer (served 2026-06-20) acceptance
> window and the mediation date (read from the calendar) surfaced as proposed-confirm,
> not finalized. No gaps.

**Bad - writes the brief / values the case (violates the floor):**

> Assembled the inputs and drafted the damages argument: with $84k in specials and
> clear liability this case is worth about $250k, so I put that in the brief.

(Writes the brief and values the case - work product the skill must never author.)

**Bad - asserts the §998 deadline as final:**

> The §998 offer expires on 2026-07-20; I put the deadline on the calendar.

(States a computed §998 cutoff as fact and calendars it. The window is surfaced as
proposed-confirm; there is no calendar write; the cutoff is the engine's/attorney's to
confirm.)
