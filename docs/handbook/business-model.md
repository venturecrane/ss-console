---
title: Business Model
section: business
order: 2
owner: captain
review_every: 180
status: Rewrite pending
summary: Three front doors, the three-layer problem framework, the engagement phases, and pain clusters by vertical
sources:
  - label: CLAUDE.md - The Business Model
    href: https://github.com/venturecrane/ss-console/blob/main/CLAUDE.md
  - label: ADR 0004 - Productized Operator Offering
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0004-productized-operator-offering.md
  - label: ADR 0001 - Taxonomy Two-Layer Model
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0001-taxonomy-two-layer-model.md
  - label: Decision Stack
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/decision-stack.md
---

## What the business is

SMD Services is a solutions consulting venture under SMDurgan, LLC. We sell scope-based consulting engagements to growing businesses (CLAUDE.md, About This Venture). This is not a SaaS product; it is a services business. The objective is to launch the venture and reach profitability.

The buyer is the owner of an established, owner-led business with real operational load and the ability to pay for a solution. There is no revenue-band qualification gate: the old "$750k-$5M" band was retired in [ADR 0003](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0003-lead-gen-pivot-actor-identity.md), and Decision #2 in the Decision Stack is superseded. We qualify in conversation, not by filtering on a guessed revenue figure. The "too big for one person, too small for a COO" framing still captures the *shape* of the buyer; it is not a filter.

## Three front doors, one firm

The firm has three acquisition paths into the same firm-level identity (ADR 0004, locks 1 and 2; ADR 0067):

1. **Scope-based consulting engagements.** The firm's front door: the home page leads every visitor to the assessment conversation (`src/pages/index.astro` header, "One door: the assessment"). The prospect arrives without a fixed solution in mind; we surface their objectives through an assessment conversation, design a solution, and quote a fixed project price. This is the funnel locked in Decisions #16 and #18.
2. **The Operator SKU.** A productized flat-rate monthly retainer ([ADR 0004](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0004-productized-operator-offering.md)). The entry point for prospects who already know they want an agent and have done their own diagnosis. See `/admin/playbook/operator-thesis` for what the Operator is.
3. **The Hosted Agent SKU.** A self-serve monthly subscription for an always-on personal Hermes agent on the Operator substrate ([ADR 0067](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0067-hosted-agent-self-serve-sku.md), Decision #51). The only SKU with published pricing and a buy CTA (page-scoped exemption to the positioning locks); checkout is self-serve, provisioning is concierge. It targets a different buyer (the individual who wants an agent and will not run a server) and doubles as the entry rung of the hosted-to-Operator ladder.

Three front doors, one firm. The firm-level voice and solutions-consulting positioning are unchanged across all of them; the Operator and the Hosted Agent are productized outcomes we deliver, not the firm itself (ADR 0004, positioning guardrails). The old undefined post-delivery retainer (Decision #12) is superseded by the Operator SKU.

The Operator is the flagship: the home page is built flagship-forward around it (`src/pages/index.astro`), and as of 2026-10-21 the venture's one paying client is an Operator client (the law-firm seat in `operator/customers/`). The Hosted Agent also packages predefined jobs as **roles** (a role is a hosted-agent job with its own persona, skills, routines and privacy model, selected per seat; [ADR 0090](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0090-hosted-agent-roles.md)); the first role is `couples` (`operator/roles/`). What each client is owed is in the [obligation register](/admin/playbook/obligation-register); client-specific commercial detail lives in the private `venturecrane/engagements` repository, never in this public handbook.

## The three-layer problem framework

We use a three-layer model to connect research to delivery (CLAUDE.md, The Business Model). The layers are deliberately distinct and must not be conflated.

**Layer 1 - Four root patterns** (internal, research-grounded):

- The founder ceiling
- Invisible operational drag
- Revenue plateau
- Cash flow fragility

**Layer 2 - Owner-voiced symptoms** (external, what owners actually say): "I can't step away." "I can't find good people." "Customers slip through the cracks." "I don't know if we're making money." "Everything runs on spreadsheets." "Our systems don't talk to each other." "We've stalled." These are representative, not exhaustive; the assessment listens for whatever comes up.

**Layer 3 - Six solution categories** (the delivery taxonomy):

1. Process design
2. Custom internal tools
3. Systems integration
4. Operational visibility
5. Vendor/platform selection
6. AI & automation

No dollar ranges are attached to solution categories. Pricing comes from scope estimation per engagement; see `/admin/playbook/pricing-economics`.

### A note on the two taxonomies

The six-category list above is the **delivery taxonomy** and is the marketing and doctrinal source of truth ([ADR 0001](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0001-taxonomy-two-layer-model.md)). A separate five-category schema (`process_design`, `tool_systems`, `data_visibility`, `customer_pipeline`, `team_operations`, defined in `src/portal/assessments/extraction-schema.ts`) is the **client-assessment extraction taxonomy**: it structures what an assessment call captures. It began as the lead-generation observation taxonomy; that use was retired with the automated lead-gen machine ([ADR 0060](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0060-retire-automated-lead-gen-machine.md)). The two layers are deliberately distinct: assessments speak the extraction taxonomy internally, marketing speaks delivery, and the consultant translates between them. Do not silently change one when editing the other.

## The engagement phases

Every scope-based engagement runs through five phases (CLAUDE.md, Engagement Phases):

| Phase | Activities |
| --- | --- |
| Assessment call | Walk through their day, "show me how you do X," identify top pain points |
| Solution design | Choose the simplest tools, design workflows, estimate scope and price, send proposal |
| Implementation | Build templates, workflows, and docs; configure tools; migrate data; connect systems |
| Training | Hands-on walkthrough, practice, deliver "how to" docs, identify the internal champion |
| Handoff + polish | Handle feedback, adjust based on real use, final handoff |

**Scope sets phase depth, not phase presence.** Every engagement includes every phase. What changes is how heavy each one is. Training may be a multi-day program or a single "on Tuesdays you click this button." Implementation may be a multi-week build or a one-afternoon script. Scope determines depth, not presence. The full assessment-to-proposal flow and delivery playbook live in `/admin/playbook/consulting-engagement`.

## Pain clusters by vertical

These suggest where to lead the conversation, not which problems to look for (CLAUDE.md, Pain Clusters by Vertical). The assessment listens across the full range of symptoms.

| Business type | Likely entry points |
| --- | --- |
| Home services (plumber, HVAC) | Scheduling + lead follow-up + employee retention |
| Professional services (accountant, attorney) | Owner bottleneck + manual communication + pipeline |
| Retail / salon / spa | Scheduling + communication + financial visibility |
| Contractor / trades | Estimating/quoting + scheduling + employee retention |
| Restaurant / food service | Team communication + inventory + financial visibility |

The consulting launch verticals were set as home services and professional services, with contractor/trades third (Decision #3). In practice the Operator went to market law-first ([ADR 0040](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0040-operator-positioning-and-why-us.md)), and its first client is a personal-injury law firm. Any business with qualifying problem signals is eligible regardless of vertical; vertical expertise deepens with case studies, not by pre-selection.

## Related pages

- `/admin/playbook/operator-thesis` - what the Operator is.
- `/admin/playbook/pricing-economics` - the rate ladder, engagement range, and Operator pricing posture.
- `/admin/playbook/consulting-engagement` - the assessment-to-handoff delivery model in depth.
- `/admin/playbook/positioning-voice` - the tone and positioning standard that governs all client-facing language.
