# Selector Test: demand-letter-drafter

Blind cross-skill selector simulation: does Hermes pick this skill for a demand-drafting
request, and NOT for its near-neighbors?

## Synthetic query

> "Draft the policy-limits demand on Alvarez from the file: the report, the records and
> bills, the chronology, the wage-loss docs, and the depos."

## Expected selection

`demand-letter-drafter`. The query asks to **draft the demand letter** from the matter
record. That is this skill's sole job. It drafts against the firm's skeleton, reserves
the demand figure and the exceeds-limits question to the attorney, and delivers inside
the firm.

## Boundary (should NOT select this skill)

- "Pull together what the mediation brief will need on Alvarez." →
  `mediation-settlement-tracker` (assembles brief INPUTS and tracks the section 998 and
  MSC posture; it does not write the brief, and it is not the demand).
- "What is the DHCS lien at now, and chase the payoff." → `lien-ledger-tracker` (owns
  lienholders, asserted amounts, and status; this skill reads that ledger into the
  demand's lien paragraph and never computes a reduction).
- "Keep the medical chronology current as the new records come in." →
  `medical-chronology-maintainer` (maintains the chronology; this skill consumes it for
  ordering only and cites the underlying records).
- "Draft the meet-and-confer on the defense's thin interrogatory responses." →
  `meet-and-confer-drafter` (a connective letter to opposing counsel from
  attorney-flagged deficiencies; different artifact, different ceiling, different
  recipient).
- "Chase Dr. Nakamura's office for the outstanding records." → `medical-records-chaser`.
- "Draft the responses to the defense's requests for production." →
  `document-drafter` where the seat carries it (a served discovery instrument, not a
  settlement offer).

## Near-neighbor risk and the distinguisher

The two live risks are `mediation-settlement-tracker` (both touch settlement figures and
policy limits) and the sibling drafting skills (all four load the same discipline and
pass the same delivery gate).

The distinguisher against the tracker is **who writes the advocacy**: the tracker
collates inputs and explicitly refuses to author the brief, while this skill is a
work-product drafting lane that writes the letter. The distinguisher against the sibling
drafters is **the artifact and its recipient**: a demand is a settlement offer prepared
for transmission to a carrier, which is why settlement authority is this skill's bright
line and why its send posture is stricter than any sibling's (no external send at all,
not even draft-for-a-person-to-send by the Operator's own mail path).

A query that asks what the case is worth, what to demand, or whether to accept an offer
selects **no skill**. That is settlement authority, and the correct response is to
surface it to the attorney.

## Result

Pending. To be run as a blind cross-skill selector simulation once the four drafting
skills are on a seat together, since the sibling drafters are the real near-neighbor
population and simulating against an incomplete lane would not prove the boundary. The
tracker and connective-letter boundaries above are the cases to exercise first.
