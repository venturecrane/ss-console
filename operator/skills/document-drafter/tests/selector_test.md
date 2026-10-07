# Selector Test: document-drafter

Blind cross-skill selector simulation: does the router queue this skill for an
administrator's request for one of the five litigation documents, and NOT for
its near-neighbors?

## Synthetic queries (each selects this skill, REQUEST mode)

| Query (from a Named Administrator)                                         | Class                                 |
| -------------------------------------------------------------------------- | ------------------------------------- |
| "Draft the mediation brief on 900201."                                     | `mediation_brief`                     |
| "Please draft special interrogatories and RFPs to the defendant on Doe."   | `discovery_set`                       |
| "Draft our responses to their RFAs on 900201; they were served last week." | `discovery_response`                  |
| "Can you draft a memo to the file on the liability issues in 900201?"      | `memo`                                |
| "Depo outline for the defendant driver's deposition on 900201, please."    | `depo_outline`                        |
| "Draft a memo on 900201 and a depo outline for the treating doctor."       | `memo` + `depo_outline` (two submits) |

## Boundary (should NOT select this skill)

- "Draft the demand on 900201." → `demand-letter-drafter` (the demand class
  outranks every drafting class; a demand is never this skill).
- "Add a note to file that the client called." / "Put a memo on the matter in
  Smokeball." → an ordinary note or general request. A note in the record is
  never a drafted memo document and never a paid job.
- "Send the mediation brief to the mediator as me." → nothing is sent: this
  skill never sends outside the firm, and send-as never carries work product.
- "What should we ask for at mediation?" → no skill. Settlement authority and
  the target figure are the attorney's; surface it.
- "Draft the responses to the served sets." on a seat where `document-drafter`
  will not load → the attorney drafting class (`discovery-response-drafter`
  where the seat carries it).

## Refusals the selector must preserve

- A sender who is not a Named Administrator: a polite decline naming the
  reservation; nothing submitted, and no word about which classes are on.
- A class the firm has not switched on: the broker's sentence, relayed as
  given ("A deposition outline isn't switched on for your firm").
- A forwarded or quoted request: nothing submitted.

## Result

Pending. To be run on pilot-smokeball once the queued drafting lane is enabled
there, against the full drafting population (the four in-turn drafters, the
demand drafter and the trackers), since the near-neighbors are that set.
