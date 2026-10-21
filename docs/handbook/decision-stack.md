---
title: The Decision Stack
section: business
order: 8
owner: captain
review_every: 180
last_reviewed: "2026-10-21"
summary: The decisions you act on most, in one line each, then how SMD records its decisions and where to read them in full.
sources:
  - label: decision-stack.md (full reference)
    href: docs/adr/decision-stack.md
  - label: ADR index
    href: docs/adr/index.md
  - label: Decision count guard
    href: tests/docs-facts.test.ts
  - label: ADR 0065 - Operator offboarding and dunning
    href: docs/adr/0065-operator-offboarding-and-dunning.md
  - label: ADR 0060 - Retire the automated lead-gen machine
    href: docs/adr/0060-retire-automated-lead-gen-machine.md
  - label: ADR 0089 - Staff send-as on approval
    href: docs/adr/0089-staff-send-as-on-approval.md
---

SMD writes down every decision that defines the business, with its reasons, so nobody has to remember them or guess. This page gives you the dozen decisions you will act on most, in one line each, and then shows where the full record lives and how it is kept.

## Decisions you must know

Each line cites the decision; the full text is in [`docs/adr/decision-stack.md`](https://github.com/venturecrane/ss-console/blob/main/docs/adr/decision-stack.md) or the ADR named.

| # | In one line | What it means for you |
|---|---|---|
| #20 | We always write as "we" and "our team," never "I" or "the consultant" (the home page's founder section is the one exception) | Check every client email and draft against it |
| #16 | Consulting is priced per scope, from an internal hourly rate the client never sees; no price is ever published | Never quote the rate; quotes are the Captain's |
| #14 | Consulting is paid 50% at signing and 50% at handoff; 40/30/30 at 40 hours or more | The deposit invoice goes out automatically at signing |
| #13 | The first three assessments are free, then $250 credited toward the project; the free period is never advertised | Do not offer a free assessment in writing |
| #18 | A proposal goes out within 48 hours of the assessment call | Chase it if it has not gone |
| #19 | A proposal with no answer gets three touches over seven days, then the prospect is marked dead | Follow the cadence; no drip after day 7 |
| #11 | Out-of-scope asks during a project go in a parking lot and are settled at the pre-handoff review | Never say yes or no to new scope on the spot |
| #27 | After a consulting handoff there are two weeks of async support, then new work is a new scope | Route later asks to the Captain as a new scope |
| #44, #50 | The Operator is a flat monthly fee plus a stand-up fee; the figures are internal and never published | You invoice at the agreed amounts; the figures are in the private repository |
| #51 | The Hosted Agent is the one offer with a published price, on `/agent` only | Do not repeat that exception anywhere else |
| #52 | Client material (letters, proposals, agreements, dossiers) lives only in the private `venturecrane/engagements` repository | Never put client material in the public repository or a public page |
| #30 | A case study that names or quotes a client needs the client's approval; an anonymized one does not (amended 2026-09-14) | Ask before publishing anything that names a client |

Three ADRs you act on just as often:

- **ADR 0065, amended 2026-08-29:** a client may cancel with no notice period, effective at the end of the paid month; SMD gives 30 days. Payment failures follow a human-run ladder (day 0 outreach, day 14 pause, day 30 offboarding). See [Customer Lifecycle](/admin/playbook/customer-lifecycle).
- **ADR 0060:** the automated lead-generation machine is retired; acquisition is founder-run and law-first. Prospects are added by hand.
- **ADR 0089:** the Operator may send an email in a staff member's name only after that staff member approves the exact draft; an administrator may cancel such a draft, never send it ([Who Does What](/admin/playbook/who-does-what)).

## Two bodies of decisions

1. **The Decision Stack**: the business decisions (who we sell to, scope, pricing, the assessment, distribution, delivery), in six layers. Source: [`docs/adr/decision-stack.md`](https://github.com/venturecrane/ss-console/blob/main/docs/adr/decision-stack.md).
2. **The numbered ADRs** (architecture decision records): narrower choices, mostly about the Operator. Source: `docs/adr/`. Every ADR with a one-line summary is on the [ADR Index](/admin/playbook/adr-index).

They point at each other on purpose: the later Decision Stack entries (#42 onward) each name the ADR behind them.

## The six layers

Each layer builds on the ones before it; nothing in Layer 1 changes without checking the impact on every later layer (`decision-stack.md`).

| Layer | Name | What it decides |
|---|---|---|
| 1 | Buy Box and Vertical | Who we sell to: the ideal client, launch verticals, disqualifiers (Decisions #2 to #6) |
| 2 | Stack and Scope | What an engagement includes: the tool rubric, scope boundaries, the parking lot (#9 to #11) |
| 3 | Pricing and Payment | How we charge: scope-based pricing, the rate ladder, payment terms, the paid assessment, the owner-does-the-math questions (#12 to #16) |
| 4 | Assessment and Qualification | How the assessment runs: capture, assessment to proposal, follow-up (#17 to #19) |
| 5 | Distribution and Pipeline | How we find clients: networking, partners, referrals, messaging, pipeline math (#21 to #25) |
| 6 | Delivery Playbook | How we deliver and close out: champion, support window, feedback, reviews, case studies (#26 to #30) |

Decision #20, the "we" voice, sits above all six and applies to everything ([Positioning & Voice](/admin/playbook/positioning-voice)).

The cross-layer decisions #42 to #56 came later. #42 to #49 tie business decisions to the Operator's architecture, each citing its ADR. #50 to #56 followed as the Operator went into service:

- **#50** Operator launch pricing: flat monthly fee plus stand-up fee, internal (ADR 0063).
- **#51** Hosted Agent: the one published price (ADR 0067).
- **#52** Repository visibility: `ss-console` and `hermes-smd-overlay` are public; client material is in the private `venturecrane/engagements` (ADR 0081). Recorded in the appendix index only.
- **#53** The compliance evidence packet is signed by SMDurgan, LLC, not a named person, with its public key at a stable address on smd.services.
- **#54** A client on Microsoft email registers two apps, one read-only for the agent and one able to send for the broker, each limited to the Operator's mailbox (ADR 0078).
- **#55** "May the Operator reply to you" and "is firm staff" are separate facts, and the safety checks that depend on them change together.
- **#56** The medical chronology package is produced by an SMD-owned runner on the client's own machine, and that runner's checks are its content control (ADR 0087).

## How decisions are recorded

A decision becomes canon when the Captain authorizes it. It is then recorded in one of two ways:

- **A Decision Stack entry** with the decision in one line, the reasons, the risks accepted, and when and how the Captain authorized it. A superseded entry is never deleted; it is marked `SUPERSEDED` with a pointer to what replaced it. Three are superseded: #2 (revenue gate, by ADR 0003), #12 (retainer, by ADR 0004) and #43 (Outside View, retired 2026-05-04).
- **A numbered ADR** with its own status (accepted, proposed, superseded), date and links. ADRs amend and supersede each other; the ADR Index records how.

Either way, the reason is written down, a supersession is explicit, and nothing is asserted without who authorized it and when. Only the Captain makes or changes a decision; if an agent's work seems to need one changed, the agent brings it to the Captain rather than editing the record.

## The count

The header of `decision-stack.md` reads 38 active decisions across six layers, numbered through #56, with three superseded (#2, #12, #43). `tests/docs-facts.test.ts` counts the decision headings and fails the build if that header, `CLAUDE.md` or `docs/adr/index.md` disagree. Numbers #1, #7, #8, #31 to #41 and #52 have no heading of their own, so they are not counted (#31 to #41 are the deliverables queue).

## Where to go next

- Full text: [`docs/adr/decision-stack.md`](https://github.com/venturecrane/ss-console/blob/main/docs/adr/decision-stack.md)
- Every ADR in one line: [ADR Index](/admin/playbook/adr-index)
- Terms used in these decisions: [Glossary](/admin/playbook/glossary)
