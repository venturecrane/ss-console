---
paths:
  - "src/pages/**"
  - "src/components/**"
  - "docs/handbook/**"
  - "docs/adr/**"
  - "operator/verticals/**"
---

# Venture model and domain context

Moved here from the root `CLAUDE.md` on 2026-08-27 so it loads only when a session works on marketing pages, components, the handbook, ADRs, or vertical packs. For content, collateral, scoping, or pricing work that touches none of those paths, read this file first. The Tone & Positioning rules stay in the root file because they carry P0 client-content prohibitions.

## The Business Model

### Problem Framework

We use a three-layer model to connect research to delivery:

**1. Four root patterns** (internal, research-grounded):

- The founder ceiling
- Invisible operational drag
- Revenue plateau
- Cash flow fragility

**2. Owner-voiced symptoms** (external, what owners actually say):
"I can't step away." "I can't find good people." "Customers slip through the cracks." "I don't know if we're making money." "Everything runs on spreadsheets." "Our systems don't talk to each other." "We've stalled."

These are representative, not exhaustive. The assessment listens for whatever comes up.

**3. Six solution categories** (delivery taxonomy):

- Process design
- Custom internal tools
- Systems integration
- Operational visibility
- Vendor/platform selection
- AI & automation

No dollar ranges are attached to solution categories. Pricing comes from scope estimation per engagement.

**AI & automation sub-capabilities** (for agent reference when authoring copy or scoping engagements, not a list to publish verbatim):

- AI strategy conversations and readiness assessment
- AI tool selection and rollout
- Custom AI and agent implementations
- Team training and enablement on AI tools
- Non-AI workflow automation (scripts, integrations that don't require AI)

**Taxonomy two-layer model.** Resolved in [ADR 0001](../../docs/adr/0001-taxonomy-two-layer-model.md) (Captain decision 2026-04-27, [#591](https://github.com/venturecrane/ss-console/issues/591)); the observation half was retired with the automated lead-gen machine by [ADR 0060](../../docs/adr/0060-retire-automated-lead-gen-machine.md) (2026-07-01). The six-category list above is the **delivery taxonomy** — what engagements we offer. It is the marketing and doctrinal source of truth. The five-category schema (`process_design`, `tool_systems`, `data_visibility`, `customer_pipeline`, `team_operations` — defined in `src/portal/assessments/extraction-schema.ts`) survives repurposed as the **client-assessment extraction taxonomy**: it structures what the assessment call captures, consumed by the assessment extraction flow, not by outreach. The two layers remain deliberately distinct: assessments speak the extraction taxonomy internally, marketing speaks delivery, and the consultant translates between them. Agents editing either side must not silently change the other.

### Pain Clusters by Vertical

These suggest where to lead the conversation, not which problems to look for. The assessment listens for whatever comes up across the full range of symptoms.

| Business Type                                | Likely Entry Points                                   |
| -------------------------------------------- | ----------------------------------------------------- |
| Home services (plumber, HVAC)                | Scheduling + lead follow-up + employee retention      |
| Professional services (accountant, attorney) | Owner bottleneck + manual communication + pipeline    |
| Retail/salon/spa                             | Scheduling + communication + financial visibility     |
| Contractor/trades                            | Estimating/quoting + scheduling + employee retention  |
| Restaurant/food service                      | Team communication + inventory + financial visibility |

### Engagement Phases

| Phase            | Activities                                                                        |
| ---------------- | --------------------------------------------------------------------------------- |
| Assessment call  | Walk through their day, "show me how you do X," identify top 3 pain points        |
| Solution design  | Choose simplest tools, design workflows, estimate scope and price, send proposal  |
| Implementation   | Build templates/workflows/docs, configure tools, migrate data, connect systems    |
| Training         | Hands-on walkthrough, practice, deliver "how to" docs, identify internal champion |
| Handoff + polish | Handle feedback, adjust based on real use, final handoff                          |

**Phases scale per engagement.** Every engagement includes every phase. What changes is how heavy each one is. Training may be a three-day program or a single "on Tuesdays you click this button." Implementation may be a multi-week build or a one-afternoon script. Scope determines depth, not presence.

### Pricing

- **Internal rate:** $175/hr at launch, then $200/hr after first case study, then $250/hr, then $300/hr with volume
- **Engagement range:** scoped per engagement. Smallest engagements (targeted automation scripts, AI pilots) start around $2,500. Below that, assessment overhead exceeds delivery value. Largest engagements have no fixed ceiling. Nothing published externally.
- **Paid Assessment:** $250, applied toward engagement if they proceed. First 3 assessments free.
- **Recurring revenue product:** Productized Operator offering — flat-rate monthly retainer SKU, second front door alongside the scope-based consulting funnel. Launch price locked 2026-07-04: a flat monthly retainer plus a one-time stand-up fee, internal and never published; the figures live in `venturecrane/engagements:pricing/` ([ADR 0063](../../docs/adr/0063-operator-launch-pricing.md) / Decision #50; supersedes ADR 0004's deferred-pricing clause). See [ADR 0004](../../docs/adr/0004-productized-operator-offering.md) / Decision #44 for the SKU shape. The prior undefined post-delivery retainer is superseded.
- **Post-handoff support for scope-based engagements:** Two-week async stabilization included (Decision #27). Beyond that, customers are quoted a follow-on scope or converted to an Operator subscription if the fit is right.
- **No dollar amounts published externally.** Client sees a project price, not hourly rate.

### The Assessment Call Is the Product

The value is NOT configuring HubSpot. Anyone can do that. The value is:

1. An experienced outsider seeing their operations with fresh eyes
2. Identifying the problems they can't see because they're too close
3. Prioritizing ruthlessly — "these 3 things first, everything else later"
4. Making decisions for them so they don't research for 6 months

## Current Phase: Pre-Launch

> Carried over as written. This checklist predates the first Operator engagement (Ashton & Price, in flight since 2026-08) and has not been re-audited; treat the unchecked items as a historical priority list, not current status.

We are in the **pre-launch phase**. Nothing has been sold yet. The immediate priorities are:

### Priority 1: Collateral to Start Selling

- [ ] Assessment call script (structured conversation guide, objectives-first)
- [ ] Proposal/SOW template (what gets sent after the assessment, reflecting full solution range)
- [ ] Pricing framework (scope estimation across all 6 solution categories)
- [ ] One-pager / leave-behind (physical or PDF for networking, guide positioning)

### Priority 2: Go-to-Market

- [ ] Vertical selection for initial targeting (pick ONE vertical to start)
- [ ] Outreach strategy (how to find and reach first 5 prospects; includes Vistage, EO Arizona, local networking)
- [x] Landing page — smd.services live; rebuilt to the firm-with-flagship structure 2026-06 (home, `/operator`, `/about`, `/industries`, `/patterns`, `/contact`)
- [x] ~~**Outside View**~~ — retired 2026-05-04 in PR #702 (user-visible surface) and #703 (infrastructure). Public-footprint scraping turned out not to surface anything useful. ADR 0002 is superseded. The lead-magnet surfaces (`/scan`, `/scorecard`, `/get-started`, `/outside-view`) middleware-301 to home for permanent-bookmark backwards compat.
- [ ] Pipeline math (how many conversations to sustain profitability)
- [ ] Phased geographic approach (Phoenix in-person first, remote-capable after proof of model)

### Priority 3: Delivery Readiness

- [ ] Tool and solution matrix (across all 6 solution categories, including custom internal tools, integrations, and AI & automation)
- [ ] SOP templates (reusable frameworks filled in per client)
- [ ] Client onboarding checklist (what we need from them before Day 1)
- [ ] Quality checklist templates (reusable across engagements)

### Priority 4: Business Model Refinement

- [x] Payment terms (50% deposit at signing, 50% at completion; 3-milestone for 40+ hr engagements)
- [ ] Paid assessment entry point ($250 applied toward engagement, first 3 free)
- [x] ~~Recurring retainer model~~ — superseded 2026-05-13 by [ADR 0004](../../docs/adr/0004-productized-operator-offering.md) (productized Operator SKU). Stack evaluation, pricing analysis, service contract terms, and stack build filed as follow-ons against ADR 0004.
- [x] Client data management system — the D1-backed admin console exists (`src/pages/admin/`: clients, assessments, quotes, engagements, billing)

## Domain Context

- **Geography:** Phoenix metro (Phase 1, in-person default), remote-capable
- **Target:** Established, owner-led businesses with real operational load and the ability to pay for a solution. No revenue-band gate — we work with any business that can pay and benefit, and qualification happens in conversation, not by filtering on a guessed revenue figure (see ADR 0003; the automated pipeline that once enforced a gate was retired entirely by ADR 0060). The "too big for one person, too small for a COO" framing still captures the shape of the buyer. For the Operator specifically, the target profiles are defined by the vertical packs in `operator/verticals/`.
- **Buyer:** The owner. Sometimes the office manager, but the owner writes the check.
- **Competition:** Traditional consultancies ($15-50k+ engagements, slow), fractional CTOs/COOs (ongoing cost, no bounded deliverable), EOS implementers (framework-locked), managed IT providers (technical only). Nobody does assessment + implementation + handoff as bounded, scope-priced engagements.
- **Referral sources:** Vistage, EO Arizona, fractional CFOs, local networking groups (BNI, chamber of commerce), accountants/bookkeepers, commercial insurance agents, SBA/SCORE
