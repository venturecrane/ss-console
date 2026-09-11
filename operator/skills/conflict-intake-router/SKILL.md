---
name: conflict-intake-router
description: >-
  Routes a potential conflict to the person who clears it. The full conflict workflow: rich
  multi-party capture, routing a surfaced potential conflict to the specific person who must clear
  it, and a cross-matter re-scan as the matter set grows. Surfaces and routes; never clears.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: [python3]
metadata:
  hermes:
    tags: [Law, Conflict, Intake, Compliance, Routing, DraftForReview]
  smd:
    vertical: law-firm
    skill_type: decision/surfacing (compliance routing)
    action_class: read + route
    connectors:
      - smokeball # PracticeManagement — get_contacts / list_matters / get_matter (read) for the cross-check
      - email # customer-bound — to route the conflict notice to the responsible person
---

# Conflict Intake Router

The deep version of the firm's conflict workflow. `new-matter-intake` already carries the **detect-and-halt invariant** — on intake it runs a read-only name/entity cross-check and halts the chain on any hit. This skill is what that invariant defers to when the firm wants the full treatment: **rich multi-party capture** (every party, adverse party, and related entity a matter touches, not just the named client), **routing** a surfaced potential conflict to the **specific person** who owns the clearance decision, and a **cross-matter cadence re-scan** that re-checks open matters as new parties enter the system.

It sits on a compliance floor. It is **never "just another skill."** Its entire job is to make sure a possible conflict reaches a human cleanly and is never advanced past — it **surfaces and routes; it does not clear.** Clearance is definitionally a human act.

## When to Use

Use when the firm wants conflict handling beyond the intake halt: capturing the full party graph of a matter, directing a flagged conflict to the right attorney rather than a generic queue, and periodically re-checking the existing book as new clients and adverse parties arrive (a party who was clean at intake can become conflicted when a later matter brings in the other side).

Runs event-driven (a new matter or new party is captured) and scheduled (the cadence re-scan across open matters).

## Prerequisites

Reads Smokeball (`get_contacts`, `list_matters`, `get_matter`) for the name/entity cross-check, and the customer-bound **Email** connector to route a surfaced conflict notice to the responsible person. Requires `python3` for the fetch block. Read-only against Smokeball — it never writes a clearance, a matter, or a contact.

## How to Run

```
hermes run conflict-intake-router --matter <id>     # full-party capture + check for one matter
hermes run conflict-intake-router --cadence-scan     # re-scan all open matters for newly-emerged conflicts
```

## Procedure

Two phases (ADR 0021 Stream A): the mechanical Smokeball cross-check runs inside one `execute_code` block so per-party reads never flood context; the adversity judgment and routing stay in the agent's reasoning loop.

### Phase 1 — Fetch (single `execute_code` block)

Enumerate the parties in scope (for a matter: the client plus every party named in the intake capture; for a cadence scan: the party set of each open matter, including open leads via `list_matters(isLead)`). For each party run `get_contacts` and `list_matters`, accumulate the matches in-process, and `print()` one JSON document of (party → existing contacts/matters it touches). A single unreadable party is recorded as `parse_failed` and the scan continues.

### Phase 2 — Reason (agent, in-context)

Per `references/capture-rubric.md` and `references/algorithm.md`:

1. **Capture the full party graph.** Per `capture-rubric.md`: the client, adverse parties, co-counsel, related entities (corporate parents/subsidiaries, spouses, guarantors). A conflict the intake halt would miss usually lives in a party that was never captured — so capture is the load-bearing step.
2. **Cross-check every captured party** against existing contacts and matters. A hit is any party who is (a) an existing client, (b) an adverse party in another open matter, or (c) related to either.
3. **On a hit, assemble a conflict packet** — which party, which existing matter, the nature of the adversity, and the responsible attorney on the conflicting matter (read directly from the matter's `personResponsibleStaffId`, resolved to a name via `get_staff`; `smokeball-surface.md` confirms the responsible attorney is returned on the matter, so no field-set widening is needed).
4. **Route to the specific person.** Send the packet to the responsible attorney for clearance, not a generic inbox. If no owner can be resolved, route to the firm's conflict-clearance surface (a named human), never to a wedge skill.
5. **Hold the matter.** A matter with an unresolved hit is surfaced as CONFLICT-HOLD; no downstream skill advances it. Advancing past a surfaced hit is a `fails` safety violation.
6. **Cadence re-scan** re-runs steps 1–5 across open matters and surfaces only newly-emerged conflicts (a pair that became adverse since the last scan).

## Trust Ceiling

**Capture + cross-check + route autonomous; zero clearance, zero writes.**

The agent MAY: read Smokeball for the cross-check; capture the party graph; assemble a conflict packet; route it to the responsible human; surface a matter as CONFLICT-HOLD.

The agent MUST NOT: clear or waive a conflict; mark a matter conflict-free; write to Smokeball; advance a held matter; route a flagged conflict to anything but a human clearance surface; invent a party association not resolved from Smokeball.

## Safety invariants (any violation → `fails`, no recovery)

1. **Never clears.** Clearance is human-only. The skill surfaces and routes; it never records a conflict as resolved or absent.
2. **Halt precedes everything.** A hit holds the matter and routes for clearance before any downstream step; no auto-clear, no wedge-skill handoff.
3. **Route to a person, not a queue.** A surfaced conflict goes to the responsible attorney (or a named clearance human), never to a generic or automated path.
4. **No fabricated party link.** A party is tied to an existing contact/matter only via a real Smokeball resolution; an unresolved party is reported as unresolved, not guessed clear.
5. **Privilege.** The party graph and packet stay on firm-internal surfaces; they never leave the firm.

## Pitfalls

Capturing only the named client and missing the adverse/related parties where conflicts actually hide; treating "no hit found" as "cleared" (it is "no hit found" — clearance is still human); routing to a generic inbox instead of the owner; advancing a held matter; inventing a corporate-relationship link the Smokeball data does not support.

## Verification

1. Every captured party is cross-checked; the party graph includes adverse and related parties, not just the client.
2. Every hit produces a packet routed to a specific human; no hit is auto-cleared.
3. Held matters are visibly CONFLICT-HOLD and no downstream skill advances them.
4. All party→matter links trace to a Smokeball read; unresolved parties are labeled unresolved.
5. The cadence scan surfaces newly-emerged conflicts without re-flagging already-cleared ones.

## References

- `references/capture-rubric.md` — the full party graph to capture (client, adverse, co-counsel, related entities) and the adversity tests (the load-bearing logic)
- `references/algorithm.md` — the cross-check ordering, the hit/packet/route flow, and the cadence-scan delta logic
- `references/output-format.md` — the conflict packet + CONFLICT-HOLD surface _(parity fast-follow)_
- `references/test-cases.md` — fixtures incl. clean intake, direct hit, related-entity hit, and the cadence newly-emerged case _(parity fast-follow)_

## Delivery channels + refusal fallback (law seat rule)

Email is a citation-free channel. Any output delivered by email (create_draft,
a reply, a chase, an attorney-confirm note) states the governing rule in plain
words ("responses are due 30 days from service by mail, plus five calendar
days for mail service; confirm before relying") and never as a citation: no
section numbers, no "CCP"/"CRC" references, no rule-format strings. The mail
channel enforces the legal-citation filter and will refuse the draft. Statute
citations belong only in matter-internal artifacts (memos, internal notes,
tasks). Write the FIRST draft citation-free; do not write a cited draft and
wait for the gate to teach you.

Three more first-draft rules, same rationale (the gates enforce them; a
refusal is a stalled deliverable and a full-context redraft — write it right
the first time):

- No em dashes anywhere, in any channel. Use commas, colons, or periods.
- In email, task, and memo text, refer to the matter by its NUMBER, taken ONLY
  from the `matterNumber` field the connector projected onto a record you read
  this turn (task, event, memo, file, and document reads all carry it when the
  matter resolves). Never compose, recall, or infer a matter number, and never
  carry one over from another matter or an earlier turn. If a read returned no
  `matterNumber`, write "matter number unavailable" rather than supplying one.
  Never refer to the matter by its case caption. The matter's own caption is
  acceptable inside matter memos; cited case law is never acceptable anywhere.
- State a specific dollar figure only when it exists in an authored source
  on the matter, and name that source in the same sentence ("per the MedFin
  payoff letter dated..."). Never total, estimate, or round figures into
  existence.

If a delivery tool refuses a draft or write (citation filter, banned-typography
gate, or any other content gate): do not retry the same content, and do not
drop the work. Redraft once, and the redraft KEEPS every captured fact: the
matter, the document type, the service or event date, the method, and any
proposed deadline stated in plain words. Strip only the flagged content class
(citation formatting becomes plain words; banned punctuation becomes plain
punctuation). A delivered draft that drops the facts is the same failure as no
draft at all. If refused twice, deliver the minimal factual note (matter,
document or work item, date and method read, where the detail lives) so a
person always learns both that the work happened and what was read.

Never state that a follow-on action is handled (tracked, calendared, logged,
queued) unless the corresponding write succeeded or a specific skill run was
actually initiated; otherwise say plainly that the step still needs doing and
who or what owns it.
