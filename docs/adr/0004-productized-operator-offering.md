---
title: Productized Operator Offering — Second Front Door, Hermes-Leaning Stack
date: 2026-05-13
status: accepted
captain: Scott Durgan
supersedes: decision-stack.md#decision-12-retainer-model
renamed-by: 0034-operator-product-naming.md
related-strategy: Episode "The $1M+ Solo AI Agent Business" (Greg Isenberg + Nick Vasilescu, 2026-05-12)
---

# ADR 0004 — Productized Operator Offering

**Status:** Accepted, **renamed 2026-06-01 by [ADR 0034](./0034-operator-product-naming.md).** The "Service name" item this ADR left open is now resolved: the product's customer-facing name is **Operator**. Everything else in this ADR — the flat-rate retainer SKU, the second-front-door positioning, the Hermes-leaning stack — stands unchanged. Where this ADR says "Operator," read "Operator."

**Status (original):** Accepted (Captain decision, 2026-05-13).

**Source:** Captain directive following [The Startup Ideas Podcast — "The $1M+ Solo AI Agent Business"](https://www.youtube.com/watch?v=BI-MNjm1tTQ) (Greg Isenberg + Nick Vasilescu, 2026-05-12). Transcript synthesis and strategic shape questions resolved in session 2026-05-13.

---

## Context

SMD Services has, since the April 2026 upmarket pivot, been positioned as a solutions consulting firm with six named delivery categories — process design, custom internal tools, systems integration, operational visibility, vendor/platform selection, and **AI & automation**. AI & automation has been a named capability since launch and lists "Custom AI and agent implementations" as a sub-capability in `CLAUDE.md`, but the firm has not productized any specific AI offering. Every engagement, including AI work, ships through the scope-based proposal funnel locked in Decision #16.

Two things were left abstract in that posture.

First, the **post-delivery retainer model** (Decision #12) was deliberately deferred — internal placeholder $200–$400/mo, single tier, "define after first delivery." This was the right call at the time because we had no engagements to learn from. But it leaves SMD without a recurring-revenue product, and the placeholder has begun to read as an artifact rather than a deliberate offering.

Second, the **AI agent build pattern** (cloud-hosted "Operator" agents with operator-managed infrastructure, sold at flat monthly rate) has matured rapidly in 2026. Operators like Nick Vasilescu (Orgo) are reporting durable $5K–$10K/mo unit economics on solo-operated agent businesses. The pattern collapses several of SMD's solution categories (AI & automation, custom internal tools, systems integration, operational visibility) into a single productized SKU with predictable infrastructure and tooling.

Captain's directive: add this offering to SMD's catalog. Two front doors, one firm.

## Decision

**Add a productized "Operator" offering as a second front door alongside the existing scope-based engagement funnel. Deprecate the undefined post-delivery retainer (Decision #12). Lean Hermes as the agent harness; evaluate everything else independently before adopting any other vendor's stack wholesale.**

### Four locks

**1. Productize as a flat-rate retainer SKU.** Operator is a named offering with a fixed monthly price. The customer signs up for a productized service, not a scoped engagement. Pricing shape is flat retainer (not metered, not credit-based, not scoped per-engagement). The specific monthly price is deferred to follow-on work pending stack cost analysis; the _shape_ of the pricing is locked here. **[Amended 2026-11-09: the retainer is still flat, but it is no longer true that nothing is metered. Some kinds of job are counted against an allowance per billing cycle that the engagement authors: chronology package pages ([ADR 0087](./0087-chronology-package-content-control.md), amendment of 2026-09-09) and, where a seat sets it, litigation status lists (`litigation_list_allowance_per_cycle`, read by `operator/workspace_broker/litigation_allowance.py`).]**

**2. Second front door, not replacement.** The scope-based assessment funnel (Decision #16, #18) remains the primary path for prospects whose objectives the firm needs to surface through conversation. Operator is the entry point for prospects who already know they want an agent. Two front doors, one firm — the firm-level voice and solutions-consulting positioning are unchanged.

**3. Hermes-leaning stack posture.** Hermes is the leading candidate for the agent harness. Every other component of the Operator stack (host/VM, MCP connector layer, email identity, memory layer, build harness) is evaluated independently before adoption. The principles from current market practice — cloud VMs over local hardware, MCP-bridged tooling, agents-building-agents, persistent memory layer, watchdog/observability — are durable; the specific vendors (Orgo, Composio, Agent Mail, Obsidian, Claude Code/Codex) are not pre-committed.

**4. Decision #12 (Retainer Model) is superseded.** The undefined $200–500/mo post-delivery retainer concept is retired. Operator replaces it as SMD's recurring-revenue product. Post-delivery support for scope-based engagements continues under Decision #27 (two-week async stabilization) as authored; if a scope-based engagement client wants ongoing support beyond that window, they are converted to an Operator subscription if the fit is right, or quoted a follow-on scope.

### What this decision does NOT lock

- **Specific monthly price.** Requires stack cost analysis (token spend, infra, support hours per customer). Filed as follow-on.
- **Specific tooling beyond Hermes-leaning.** Orgo, Composio, Agent Mail, Obsidian, Claude Code/Codex are _candidates_, not adoptions. Filed as follow-on.
- **Service contract terms** (notice, escalation, included scope, scope creep protocol for productized customers). Filed as follow-on.
- **Copy, landing surfaces, SOW variant.** Deferred until pricing locks.
- **Stack build itself.** Deferred until evaluation completes.
- **Service name.** "Operator" is the working term in this ADR. The customer-facing brand for the SKU may differ.

## Consequences

**Positive.**

- SMD gains its first concrete recurring-revenue product. Cash flow becomes more predictable than purely scope-based revenue.
- The April 2026 upmarket pivot is preserved. AI & automation was already a named capability; this decision makes it shippable as a productized SKU without rewriting the firm's positioning.
- Two front doors broaden addressable market: prospects who arrive cold get the assessment funnel; prospects who already know they want an agent get a productized entry point.
- The undefined retainer artifact is resolved cleanly. No "we'll figure it out after first delivery" overhang.
- Hermes-leaning posture with independent evaluation of everything else protects against vendor lock-in. The market is moving fast; a stack locked today is wrong by Q3.

**Negative / accepted.**

- Positioning surface increases. The marketing site, sales conversations, and Decision Stack now have to hold two compatible offers without letting the productized SKU drag the firm's identity into "the agent shop." Mitigation: practitioner-firm voice (#20, About.astro practitioner exception) and solutions-consulting positioning remain the firm-level frame. Operator is _one productized outcome we can deliver_, not the firm itself.
- The productized SKU pre-supposes the solution. SMD's posture is collaborative and objectives-first; productizing one specific solution creates a category of prospect who never goes through the assessment conversation. We accept this — the customer arriving at Operator has already done their own diagnosis. The conversation we'd have surfaced is the conversation they've had with themselves.
- Recurring revenue creates operational obligations (uptime, monitoring, customer success cadence) the firm has not yet had. Productized service delivery is structurally different from project delivery. The stack-build follow-on will need to specify watchdog, observability, and incident-response patterns before the first paid customer.
- Decision #12's deprecation removes a placeholder some collateral may reference. CLAUDE.md and the Decision Stack are updated as part of this ADR; any other surface referencing the old retainer will need to be swept in follow-on work.

**Out of scope.**

- Repositioning the firm. SMD remains a solutions consulting firm. Operator is a productized SKU, not a new firm identity.
- Building the agent stack. This ADR authorizes the offering; the build follows after stack evaluation and pricing analysis.
- Migrating existing scope-based clients to Operator. The two offerings serve different acquisition shapes; cross-conversion is opportunistic, not systematic.
- Specific pricing math. The market-practice figures cited above are reference points, not commitments. SMD's number comes from cost-up analysis once the stack is locked.

## Positioning guardrails

These extend Decision #20 (positioning standard) and the practitioner-firm exception (added 2026-05-03) to the Operator surface.

- **Firm-level voice stays solutions consulting.** Home page, About, and any firm-level surface remains framed as solutions consulting. Operator is a named offering within that frame, not a competing identity.
- **No "AI-powered firm" branding.** Per CLAUDE.md positioning: "A chef isn't hired for his knife, but he names the knife when it matters." Operator is the knife. SMD is the chef.
- **Operator copy follows the same anti-fabrication rules.** No invented timeframes, deliverables, consultant names, or commitments. Pattern A and Pattern B violations apply to Operator surfaces identically to scope-based engagement surfaces.
- **No false simplicity.** The "unlimited agents" framing common in market practice is rhetorical, not literal. Productizing requires honest scope language — what the customer gets, what they don't, what triggers a scope conversation. This is locked in the follow-on service contract terms.

## Implementation

**This ADR lands with:**

- `docs/adr/0004-productized-operator-offering.md` — this document.
- `docs/adr/decision-stack.md` — Decision #12 marked SUPERSEDED with pointer to this ADR; new Decision #44 (Productized Operator Offering, cross-layer) added pointing to this ADR; Appendix decision index updated.
- `docs/adr/index.md` — entry added for ADR 0004.
- `CLAUDE.md` — Pricing section's "$200-500/mo post-delivery retainer" line removed; replaced with a pointer to Decision #44 / ADR 0004. Priority 4 checklist's retainer line resolved.

**Filed as follow-on (separate issues, not in this PR):**

- Stack evaluation — Hermes harness confirmation, host/VM choice (Orgo eval, alternatives), connector layer (Composio eval, alternatives), email identity layer, memory layer (Obsidian eval, alternatives), build harness (Claude Code vs. Codex). Each line item produces a recommendation with cost and lock-in analysis.
- Pricing analysis — token spend per agent per customer per month, infra cost, support hours, target margin. Outputs the SMD monthly price.
- Service contract terms — notice period, escalation, scope creep protocol for productized customers, downtime SLA shape, customer success cadence.
- Service name — "Operator" customer-facing brand check (trademark search, vertical resonance).
- Copy and surfaces — landing section on `smd.services`, dedicated subpage, intake flow, SOW variant for productized retainer. Gated on pricing and service contract lock.
- Stack build — once evaluation completes, build the first SMD Operator end-to-end, agent-managed-by-agent pattern, watchdog and observability included. First customer is an internal SMD use case (eat our own cooking) before any paid customer.
- Sweep — search marketing site, portal copy, and any internal docs for references to the old $200–500/mo retainer; update or remove.

## References

- Episode: [The Startup Ideas Podcast — "The $1M+ Solo AI Agent Business"](https://www.youtube.com/watch?v=BI-MNjm1tTQ) (Greg Isenberg + Nick Vasilescu, 2026-05-12)
- Apple Podcasts: https://podcasts.apple.com/us/podcast/the-startup-ideas-podcast/id1593424985?i=1000767436638
- Doctrinal source for delivery taxonomy: `CLAUDE.md` → "The Business Model" → "Six solution categories"
- Doctrinal source for AI & automation sub-capabilities: `CLAUDE.md` → "The Business Model" → "AI & automation sub-capabilities"
- Decision #12 (Retainer Model — superseded by this ADR): `docs/adr/decision-stack.md`
- Decision #16 (Pricing Model — scope-based engagements, unchanged): `docs/adr/decision-stack.md`
- Decision #20 (Positioning standard — "we / our team" voice, practitioner-firm About exception): `docs/adr/decision-stack.md`
- Decision #27 (Post-handoff safety net for scope-based engagements, unchanged): `docs/adr/decision-stack.md`
