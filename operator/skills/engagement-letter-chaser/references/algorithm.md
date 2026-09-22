# Engagement Letter Chaser - Algorithm

Source of truth for the cadence decision and the no-interpretation line.

## Gate

If the matter is on CONFLICT-HOLD, stop - surface "chase paused, conflict clearance pending." Do not read status or draft.

## Decision table

Inputs: `sent_date`, `signed` (+ `signed_date`), `status` (pending | declined | expired), `last_nudge_date`, cadence rules (`interval_days`, `max_nudges`).

| State                                                                        | Action                                                                                              |
| ---------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `signed`                                                                     | Log the signature (`create_memo`), stop the cadence, draft **no** nudge. Matter advances to active. |
| `declined` or `expired`                                                      | **Surface to a human** - a decision/relationship event, not a nudge. Draft no nudge.                |
| pending, never nudged, `today − sent_date ≥ interval`                        | Draft a nudge (nudge #1).                                                                           |
| pending, nudged, `today − last_nudge_date ≥ interval`, nudges-so-far `< max` | Draft a nudge (next #).                                                                             |
| pending, `today − (last_nudge or sent) < interval`                           | **Wait** - draft nothing. Over-nudging is a failure.                                                |
| pending, nudges-so-far `≥ max`                                               | **Surface to a human** - do not nudge again.                                                        |

## Drafting the nudge

Per `voice.md`: short, warm, low-pressure. State that the letter the firm sent to get started is waiting to be completed, point to where to complete and return it, offer to answer questions **with the team**. Name no term, no obligation, no clause. The body is floor-clean (#1878; substitution table in `voice.md`): no "sign"/"signature", no "engagement letter", no "attorney" in the outbound text.

## The terms question

If a client reply asks what a clause means, whether a fee term is negotiable, or what an obligation entails: **do not answer.** The nudge/response acknowledges the question and routes it to the responsible attorney ("happy to set up a few minutes with the team to walk through that" - the routing is to the attorney; the body says "the team", which is floor-clean). Interpreting a term of the letter is legal advice.

## What this algorithm is NOT

- Not a term explainer (UPL).
- Not an over-nudger (cadence respect).
- Not a sender (drafts only).
- Not a chaser of signed/declined/expired/held letters.
