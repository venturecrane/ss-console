# New Matter Intake - Extraction & Decision Rubric

How the agent makes each judgment in Phase 1–2. Operational, so grading is defensible.

## Field extraction (pass = recall + precision 100%)

| Field                    | Rule                                                                                                                                                                                                                                                                                                        |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Prospective client       | Name + contact verbatim. Distinguish sender from prospect if they differ. Where a petitioner writes on behalf of a beneficiary (e.g., an immigration spouse petition), **surface both** - the petitioning party and the beneficiary - and let the firm confirm whom it represents; never silently pick one. |
| Other named parties      | EVERY named person/business besides the prospect. Err toward over-capture; a missed party is a missed conflict check.                                                                                                                                                                                       |
| Situation                | The sender's own words, quoted. No legal restatement.                                                                                                                                                                                                                                                       |
| Practice area            | One authored area, or "two - surface both," or "outside authored areas." Never invent a fit.                                                                                                                                                                                                                |
| Referral source          | Formal `referral_source` set **only when a referrer is named**. A role-level mention ("my accountant," "a friend") is captured as **context** in the matter draft, not as a formal source. Never inferred.                                                                                                  |
| Statute-sensitive signal | Internal flag if a dated incident/notice/deadline is mentioned. Never client-facing, never a computed period.                                                                                                                                                                                               |

A field not present in the inquiry is **absent**, never filled with a plausible guess.

## Practice-area classification

Match the described situation to the firm's authored practice areas (`customer.yaml`). Decision order:

1. Clear single match → label it.
2. Plausibly two areas → surface both, pick neither, note "human to confirm area."
3. No authored area fits → "outside authored practice areas"; the acknowledgment stays a neutral receipt.

Classification is administrative routing, not a legal opinion about the matter.

## Statute-sensitive determination

Flag INTERNAL "statute-sensitive - verify deadline" when the inquiry names: an incident with a date (accident, injury, termination), a received notice/summons/deadline, or any "I was told I have until ...". A flag is a prompt for the firm to check its own calendar rules. It is never surfaced to the prospect and never turned into a number.

## Conflict-hit severity (drives HALT vs proceed)

| Result                                                                 | Action                                          |
| ---------------------------------------------------------------------- | ----------------------------------------------- |
| Exact name / same business / named adverse party is an existing client | HALT - CONFLICT-HOLD                            |
| Partial or ambiguous match (shared surname, maybe-same-person)         | HALT - surface as "possible, needs human check" |
| No match on any party                                                  | Proceed to draft                                |

Ambiguity always resolves to HALT. There is no "probably fine."

## Dedupe vs. conflict - same tool, two meanings

`get_contacts` match on the **prospect** → returning client; attach, do not duplicate (a dedupe event). `get_contacts`/`list_matters` match on an **adverse party** who is an existing client → a conflict event → HALT. Classify which one applies; do not let a dedupe match suppress a conflict check on the other parties.
