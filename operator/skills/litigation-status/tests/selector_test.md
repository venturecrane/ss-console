# Selector Test: litigation-status

Blind cross-skill selector simulation: does the router queue this skill for an
administrator's request for the firm's litigation status list, and NOT for its
near-neighbors?

## Synthetic queries (each selects this skill, REQUEST mode)

| Query (from a Named Administrator)                                       | Scope                   |
| ------------------------------------------------------------------------ | ----------------------- |
| "Send me a fresh litigation status list."                                | every case              |
| "Can I get the litigation case review list?"                             | every case              |
| "Which of our complaints are filed, served and answered? Need the list." | every case              |
| "The litigation list for Pat's cases, please."                           | `attorneys: ["Pat"]`    |
| "Litigation status for Pat Doe and Sam Roe."                             | `attorneys` (two names) |

## Boundary (should NOT select this skill)

- A client: "Where are we on my case?" → `matter-status-responder` (one
  client's own matter is never this list).
- A staff member: "Has the defendant on 900201 been served yet?" → a general
  request about ONE matter.
- "Draft our responses to their RFAs on 900201." → the document drafting
  class; the list is never a drafted document.
- "Put the litigation list on a schedule every morning." → this skill's REQUEST
  mode queues the one list asked for now and says the weekday run is switched
  on by the firm through SMD; nothing is scheduled from a turn.

## Refusals the selector must preserve

- A sender who is not a Named Administrator: a polite decline naming the
  reservation; nothing submitted.
- An attorney name the broker cannot place: the broker's sentence, relayed.
- A forwarded or quoted request: nothing submitted.

## Result

Pending. To be run on pilot-smokeball against the full router population
(the status responder, the trackers and the drafting lanes are the
near-neighbors) once the queued litigation lane is enabled there.
