---
title: The Decision Stack
section: business
order: 8
owner: captain
review_every: 180
status: Rewrite pending
summary: How SMD records the decisions that define the venture, and the 6-layer model that organizes them
sources:
  - label: decision-stack.md (full reference)
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/decision-stack.md
  - label: ADR index
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/index.md
---

## What this page is

This is a pointer page. It explains how SMD records decisions and the structure those decisions sit in. It does not re-type the decisions themselves. For the full text, read the source documents linked below.

Two bodies of decision live in this repo:

1. **The Decision Stack** - the go-to-market decision corpus, organized in 6 layers. Source of truth: [`docs/adr/decision-stack.md`](https://github.com/venturecrane/ss-console/blob/main/docs/adr/decision-stack.md). This is the structure for the buy box, scope, pricing, assessment, distribution, and delivery decisions.
2. **The numbered ADRs** - narrower architectural choices, mostly about the Operator platform. Source of truth: the `docs/adr/` directory. The full list with one-line summaries is on [the ADR Index](/admin/playbook/adr-index).

The two overlap deliberately. The later Decision Stack entries (Decision #42 onward) cite the ADR that grounds them, so an architectural ADR and its corresponding go-to-market decision point at each other.

## The 6 layers

The Decision Stack is a record of every strategic go-to-market decision, grouped by layer. Each layer builds on the ones before it; per `decision-stack.md`, nothing in Layer 1 should change without evaluating the downstream impact across all subsequent layers.

| Layer | Name | What it decides |
|---|---|---|
| 1 | Buy Box and Vertical | Who we sell to: ideal client profile, launch verticals, qualification, disqualifiers (Decisions #2-#6) |
| 2 | Stack and Scope | What an engagement includes: tool evaluation rubric, scope boundaries, scope-creep protocol (Decisions #9-#11) |
| 3 | Pricing and Payment | How we charge: scope-based pricing, the internal rate ladder, payment terms, paid assessment, ROI anchor math (Decisions #12-#16) |
| 4 | Assessment and Qualification | How the assessment call runs: capture workflow, assessment-to-proposal transition, follow-up cadence (Decisions #17-#19) |
| 5 | Distribution and Pipeline | How we find clients: networking strategy, partnerships, referrals, outreach messaging, pipeline math (Decisions #21-#25) |
| 6 | Delivery Playbook | How we deliver and close out: internal champion, post-handoff safety net, feedback, reviews, case studies (Decisions #26-#30) |

There is also a venture-wide positioning standard (Decision #20) that sits above the layers and applies to all content without exception: "we / our team" voice, never "I / the consultant." See [Positioning & Voice](/admin/playbook/positioning-voice) for how that standard is enforced.

The cross-layer decisions (Decisions #42 through #56) were added later. Decisions #42-#49 map go-to-market decisions onto the Operator architecture and each cite an ADR (0001 through 0009, with #45 citing ADRs 0025 and 0035). Decisions #50-#56 followed as the Operator went into service:

- **#50** Operator launch pricing: a flat monthly retainer plus a one-time stand-up fee, internal and never published (ADR 0063).
- **#51** Hosted Agent self-serve SKU: a published-price personal agent subscription, the one page-scoped exemption to the no-published-prices rule (ADR 0067).
- **#52** Repository visibility: `ss-console` and `hermes-smd-overlay` are public, and client material lives in the private `venturecrane/engagements` repo (ADR 0081 revision). It is recorded in the appendix index only, with no body section.
- **#53** The compliance evidence packet is signed by SMDurgan, LLC rather than a named individual, with its public key at a stable URL on smd.services.
- **#54** A client on the Microsoft Graph email channel registers two app registrations, a read-only one for the agent and a send-capable one for the broker, each restricted to the Operator mailbox (ADR 0078).
- **#55** "May the Operator reply to you" and "is firm staff" are separate authored facts, and the content floor, send ceilings and taint gate move together when they change.
- **#56** The medical chronology package is produced by an SMD-owned runner on the client's own Machine, and that runner's gates, not the agent-turn content gates, are its content control (ADR 0087).

## How decisions are recorded

A decision becomes canon when the Captain authorizes it. The record then takes one of two shapes:

- **A Decision Stack entry** carries the issue number, the decision in one line, the rationale, the risks accepted, and the date and context of Captain authorization. When superseded, the entry is not deleted - it is marked `SUPERSEDED` with a pointer to what replaced it, and the original text is preserved as the historical record. Decision #2 (revenue gate, superseded by ADR 0003), Decision #12 (retainer model, superseded by ADR 0004) and Decision #43 (Outside View unified diagnostic, superseded 2026-05-04) are the three superseded entries.
- **A numbered ADR** captures a narrower architectural choice with its own status (`accepted`, `proposed`, `superseded`), date, and related-ADR links. ADRs amend and supersede each other in chains; the ADR Index records those relationships.

Both forms follow the same discipline: the *why* is written down, supersession is explicit rather than silent, and nothing is asserted without the authorization context attached.

## The count

The source header reads 38 active decisions across 6 layers, numbered through #56, with 3 superseded (#2, #12, #43). That figure counts the decision headings in `docs/adr/decision-stack.md`, and `tests/docs-facts.test.ts` derives it from those headings and fails CI when the header, CLAUDE.md, or `docs/adr/index.md` disagree. Numbers #1, #7, #8, #31-#41 and #52 have no heading section, so they are not in the count.

## Where to go next

- Full decision text: [`docs/adr/decision-stack.md`](https://github.com/venturecrane/ss-console/blob/main/docs/adr/decision-stack.md)
- Every ADR with a one-line summary and GitHub link: [ADR Index](/admin/playbook/adr-index)
- The vocabulary used across these decisions: [Glossary](/admin/playbook/glossary)
