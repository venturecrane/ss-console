---
title: Business Model
section: business
order: 2
owner: captain
review_every: 180
last_reviewed: "2026-10-21"
summary: The three offers and how a prospect reaches each, the problem framework we listen with, the consulting phases, and the verticals we lead with.
sources:
  - label: Venture model (problem framework, phases, verticals)
    href: .claude/rules/venture-model.md
  - label: Positioning spine (one front door, firm with flagship)
    href: docs/marketing/positioning-spine.md
  - label: ADR 0004 - Productized Operator Offering
    href: docs/adr/0004-productized-operator-offering.md
  - label: ADR 0067 - Hosted Agent self-serve SKU
    href: docs/adr/0067-hosted-agent-self-serve-sku.md
  - label: ADR 0090 - Hosted-agent roles
    href: docs/adr/0090-hosted-agent-roles.md
  - label: ADR 0040 - Operator positioning, law-first
    href: docs/adr/0040-operator-positioning-and-why-us.md
  - label: ADR 0001 - Taxonomy two-layer model
    href: docs/adr/0001-taxonomy-two-layer-model.md
  - label: Decision Stack
    href: docs/adr/decision-stack.md
---

SMD has three offers: the Operator built with a business (the flagship, a managed AI worker on a monthly fee), consulting projects at a fixed price, and the Operator bought self-serve (a personal agent at a published price). The two Operator offers are one product; only their commercial terms and starting capabilities differ (ADR 0100; the self-serve terms are ADR 0067). This page explains how a prospect reaches each one, the vocabulary we use to understand a business, and how a consulting project runs.

## Who buys

The buyer is the owner of an established, owner-led business with real operational load and the ability to pay. There is no revenue-band gate: the old "$750k to $5M" band was retired by [ADR 0003](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0003-lead-gen-pivot-actor-identity.md) and Decision #2 is superseded. We qualify in conversation. "Too big for one person, too small for a COO" describes the shape of the buyer; it is not a filter.

## Three offers, one firm

| Offer | Who it is for | How they reach it | Decision |
|---|---|---|---|
| **The Operator** (flagship) | A business whose people and systems leave recurring work falling through the gap between them | The assessment conversation, or directly when the prospect already knows it wants an agent | ADR 0004 (Decision #44); price ADR 0063 (Decision #50) |
| **Consulting** | A business with a bounded need: a process, a tool, an integration, a dashboard | The assessment conversation, then a proposal | Decisions #16 and #18 |
| **The Operator, self-serve** | An individual or household who wants an always-on agent and will not run a server | Self-serve checkout on `/agent`; SMD then sets the Operator up by hand from the self-serve preset | ADR 0067 (Decision #51), ADR 0100 |

**The assessment is the one front door on the website.** Every page except `/agent` leads to a single action, "start with an assessment," and the assessment leads to a recommendation, a project, or an Operator (Captain decision 2026-06-29, recorded at the top of `docs/marketing/positioning-spine.md`). The Operator is the flagship: the home page is built around it, and as of 2026-10-21 SMD's one paying client is an Operator client, a personal-injury law firm (seat `ashton-price`, `operator/customers/`).

**One firm, one voice, one product.** The Operator, however it is bought, and consulting are things the firm delivers, not separate identities. The firm's voice and positioning are the same across every offer (ADR 0004, positioning guardrails; see [Positioning & Voice](/admin/playbook/positioning-voice)).

**Roles.** An Operator can be sold as a **role**: a packaged job with its own persona, skills, routines and privacy rules, chosen per seat. The first role is `couples` (`operator/roles/couples/`). A role is not a **pack**: a pack tailors the Operator to an industry (`operator/verticals/`, see [Vertical Packs](/admin/playbook/vertical-packs)), a role defines a job an Operator does for a person or household (ADR 0090).

**What replaced the old retainer.** Decision #12 (an undefined post-project retainer) is superseded. After a consulting project, the client gets two weeks of included support (Decision #27); after that, further work is a new project or, if it fits, an Operator subscription (ADR 0004, lock 4).

How each offer is priced is on [Pricing & Economics](/admin/playbook/pricing-economics); how the money moves is on [Money](/admin/playbook/money).

## How we listen: the three-layer problem framework

We connect what an owner says to what we build with three layers (`.claude/rules/venture-model.md`). Keep them distinct.

**Layer 1: four root patterns** (internal, for our own thinking): the founder ceiling; invisible operational drag; revenue plateau; cash flow fragility.

**Layer 2: what owners actually say.** "I can't step away." "I can't find good people." "Customers slip through the cracks." "I don't know if we're making money." "Everything runs on spreadsheets." "Our systems don't talk to each other." "We've stalled." These are examples; the assessment listens for whatever comes up.

**Layer 3: six solution categories** (what we deliver):

1. Process design
2. Custom internal tools
3. Systems integration
4. Operational visibility
5. Vendor/platform selection
6. AI & automation

No dollar ranges attach to a category. Price comes from scoping each engagement.

### Two lists that must not be merged

The six categories above are the **delivery taxonomy**, the source of truth for marketing ([ADR 0001](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0001-taxonomy-two-layer-model.md)). A separate five-item list (`process_design`, `tool_systems`, `data_visibility`, `customer_pipeline`, `team_operations`, in `src/portal/assessments/extraction-schema.ts`) structures what the assessment captures. It began as the lead-generation list; that use was retired with the lead-gen machine (ADR 0060). If you ask an agent to change one list, tell it not to change the other.

## How a consulting project runs

Every consulting engagement has five phases (`.claude/rules/venture-model.md`):

| Phase | What happens |
| --- | --- |
| Assessment call | Walk through their day, "show me how you do X," find the top problems |
| Solution design | Choose the simplest tools, design the workflows, estimate scope and price, send the proposal |
| Implementation | Build templates, workflows and documents; configure tools; move data; connect systems |
| Training | Hands-on walkthrough, practice, written how-to documents, name an internal champion |
| Handoff and polish | Take feedback, adjust from real use, final handoff |

**Scope sets how heavy each phase is, never whether it happens.** Training might be a three-day program or one sentence ("on Tuesdays you click this button"). The detail is on [The Consulting Engagement](/admin/playbook/consulting-engagement). An Operator is stood up by a different method; see [Customer Lifecycle](/admin/playbook/customer-lifecycle).

## Where we lead the conversation, by vertical

These suggest where to start a conversation, not what to look for (`.claude/rules/venture-model.md`).

| Business type | Likely entry points |
| --- | --- |
| Home services (plumber, HVAC) | Scheduling, lead follow-up, keeping staff |
| Professional services (accountant, attorney) | Owner bottleneck, manual communication, pipeline |
| Retail, salon, spa | Scheduling, communication, financial visibility |
| Contractor, trades | Estimating and quoting, scheduling, keeping staff |
| Restaurant, food service | Team communication, inventory, financial visibility |

Consulting's launch verticals were home services and professional services, with contractor and trades third (Decision #3). The Operator goes to market law firms first ([ADR 0040](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0040-operator-positioning-and-why-us.md)), and acquisition is founder-run rather than automated (ADR 0060). Any business with real problems we can solve is eligible, whatever its vertical.

## Related pages

- [The Operator Thesis](/admin/playbook/operator-thesis): what the flagship is.
- [Pricing & Economics](/admin/playbook/pricing-economics): how each offer is priced.
- [Customer Lifecycle](/admin/playbook/customer-lifecycle): how a sale becomes a live client, for each offer.
- [Positioning & Voice](/admin/playbook/positioning-voice): how we talk about all of it.
