---
title: Taxonomy Two-Layer Model — Observation (5-cat) and Delivery (6-cat)
date: 2026-04-27
status: accepted
captain: Scott Durgan
supersedes: none
related-issue: https://github.com/venturecrane/ss-console/issues/591
related-strategy: docs/strategy/lead-gen-strategy-2026-04-25.md
---

# ADR 0001 — Taxonomy Two-Layer Model

**Status:** Accepted (Captain decision, 2026-04-27, lead-gen strategy walkthrough).

**Issue:** [#591](https://github.com/venturecrane/ss-console/issues/591) — Resolve taxonomy schism (5-cat observation, 6-cat delivery).

**Source:** [docs/strategy/lead-gen-strategy-2026-04-25.md](../strategy/lead-gen-strategy-2026-04-25.md), Diagnosis section, item 4.

---

## Context

Two parallel taxonomies have been growing in this codebase, each in its own subsystem, each internally consistent, each documented by a different working session.

**The 6-category delivery taxonomy** (CLAUDE.md "The Business Model"):

1. Process design
2. Custom internal tools
3. Systems integration
4. Operational visibility
5. Vendor/platform selection
6. AI & automation

This is the doctrinal positioning — the kinds of engagements we offer. It governs the marketing site (`src/components/WhatYouGet.astro`, `src/components/CaseStudies.astro`), the pricing framework, and the Decision Stack. It is the language a prospect's first website visit sees.

**The 5-category observation taxonomy** (`src/portal/assessments/extraction-schema.ts`):

1. `process_design`
2. `tool_systems`
3. `data_visibility`
4. `customer_pipeline`
5. `team_operations`

This is the operational pain we can detect from public data — review patterns, job postings, new business filings, partner conversations. It governs the lead-gen pipelines (`src/lead-gen/prompts/*`), the scorecard, and the entity-signal metadata used in the admin console.

The two lists were authored at different times by different sessions for different purposes. They were never reconciled. Until this ADR, CLAUDE.md flagged the divergence as a follow-on (now resolved here).

The lead-gen strategy walkthrough on 2026-04-27 surfaced the consequence: outreach copy generated from the observation taxonomy referenced concepts the prospect's first website visit did not mention. A prospect reading "We noticed your customer pipeline shows signs of leakage" then landing on a site that talks about "process design, custom internal tools, systems integration..." has to bridge the two lists themselves. That's a credibility gap on the most important hand-off in the funnel.

## Decision

**Keep both lists. Distinguish their purposes.**

- The 6-category list is the **delivery taxonomy** — the kinds of engagements we offer. It stays the source of truth for the marketing site, pricing, SOWs, and Decision Stack language.
- The 5-category list is the **observation taxonomy** — the operational signals we can detect from public data. It stays the source of truth for lead-gen prompts, the scorecard, and assessment intake extraction.
- The two layers do not need to map 1:1. The assessment conversation is the bridge — it connects what we observe to what we'll build.

In practice:

- Outreach speaks observation ("we noticed customer follow-up patterns in your reviews").
- Marketing speaks delivery ("we deliver process design, custom internal tools, systems integration, operational visibility, vendor/platform selection, AI & automation").
- The assessment call is where the consultant translates between the two — listening for whatever the owner names, then proposing one or more delivery moves that address the goal underneath.

The marketing site adds a single clarifying paragraph below the delivery list explaining that the list is how we deliver, not a checklist we run against the business. No rewrite of the doctrinal categories. No retitling. The bridge is conversational, not structural.

## Consequences

**Positive.**

- Both subsystems retain the vocabulary their authors optimized for. The lead-gen schema is pain-detection-shaped; the marketing list is engagement-offering-shaped. Forcing one into the other would degrade both.
- The credibility gap closes through honest framing rather than a forced merge. The marketing copy now anticipates the question a prospect arriving from outreach will ask.
- Future agents editing either side know which list governs which surface. CLAUDE.md is updated to point at this ADR.

**Negative / accepted.**

- Two vocabularies remain in the codebase. Cross-cutting work (e.g., a future "explain how this signal maps to a delivery move" feature) will need to maintain a small explicit mapping rather than a single shared enum. We accept this complexity as the cost of preserving both lists' fitness for purpose.
- Documentation surface grows by one paragraph in WhatYouGet and one ADR. This is a deliberate non-rewrite — the doctrinal positioning text is unchanged.

**Out of scope.**

- Building a runtime mapping table between observation IDs and delivery categories. The assessment call is the mapping. If we ever ship a feature that needs a literal lookup, we'll author that mapping then.
- Renaming any IDs. `process_design` already happens to coincide with the marketing label "Process design"; the rest deliberately do not, and we leave them alone.

## Implementation

- `src/components/WhatYouGet.astro` — clarifying paragraph appended below the delivery list.
- `src/lead-gen/prompts/*` — confirmed already 5-cat exclusive; no changes needed.
- `tests/landing-page.test.ts` and friends — no doctrinal changes; existing tests stay green.
- New integration test asserts the lead-gen prompts only reference the 5 observation IDs (string-search assertion).
- `CLAUDE.md` "Taxonomy divergence note" is updated to reference this ADR and note resolution.
- `docs/adr/decision-stack.md` adds Decision #31 pointing to this ADR.

## References

- Issue: [#591](https://github.com/venturecrane/ss-console/issues/591)
- Strategy: [`docs/strategy/lead-gen-strategy-2026-04-25.md`](../strategy/lead-gen-strategy-2026-04-25.md), Diagnosis section item 4 ("Taxonomy schism") and Decisions Locked table row 1
- Doctrinal source for delivery taxonomy: `CLAUDE.md` → "The Business Model" → "Six solution categories"
- Schema source for observation taxonomy: `src/portal/assessments/extraction-schema.ts` (`PROBLEM_IDS`, `PROBLEM_LABELS`)
- Lead-gen prompts: `src/lead-gen/prompts/job-qualification-prompt.ts`, `src/lead-gen/prompts/new-business-prompt.ts`, `src/lead-gen/prompts/review-scoring-prompt.ts`, `src/lead-gen/prompts/partner-nurture-prompt.ts`
