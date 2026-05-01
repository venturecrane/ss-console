---
title: Outside View — Unified Diagnostic with Three Input Depths and Portal-Resident Artifact
date: 2026-04-27
status: accepted
captain: Scott Durgan
supersedes: none
related-issues:
  - https://github.com/venturecrane/ss-console/issues/482
  - https://github.com/venturecrane/ss-console/issues/598
  - https://github.com/venturecrane/ss-console/issues/596
  - https://github.com/venturecrane/ss-console/issues/483
  - https://github.com/venturecrane/ss-console/issues/612
  - https://github.com/venturecrane/ss-console/issues/616
related-strategy: docs/strategy/lead-gen-strategy-2026-04-25.md
related-adr: 0001-taxonomy-two-layer-model.md
---

# ADR 0002 — Outside View: Unified Diagnostic with Three Input Depths

**Status:** Accepted (Captain decision, 2026-04-27, conversation on lead-magnet consolidation).

**Issues touched:** [#482](https://github.com/venturecrane/ss-console/issues/482), [#598](https://github.com/venturecrane/ss-console/issues/598), [#596](https://github.com/venturecrane/ss-console/issues/596), [#483](https://github.com/venturecrane/ss-console/issues/483), [#612](https://github.com/venturecrane/ss-console/issues/612), [#616](https://github.com/venturecrane/ss-console/issues/616).

**Source:** Conversation 2026-04-27 (PM/Eng + Captain dialog on three competing front doors). 5-persona site review (April 2026, referenced in #483). Lead-gen strategy [docs/strategy/lead-gen-strategy-2026-04-25.md](../strategy/lead-gen-strategy-2026-04-25.md).

---

## Context

The marketing site grew three lead-magnet surfaces in parallel, each authored in a different working session, each internally consistent, none reconciled with the others.

| Surface                                        | Direction               | Source of signal                  | Commitment | Status                                                                          |
| ---------------------------------------------- | ----------------------- | --------------------------------- | ---------- | ------------------------------------------------------------------------------- |
| `/get-started` ("Tell Us About Your Business") | Inward (prospect → us)  | Self-report, freeform form fields | Low–medium | Live; dual-mode (cold + post-booking prep)                                      |
| `/scorecard`                                   | Inward (prospect → us)  | Self-report, structured form      | Medium     | Live as form; #482 P1 rewrite to conversational voice/text agent (~42 dev-days) |
| `/scan`                                        | Outward (us → prospect) | Public footprint observed by us   | Very low   | Not yet shipped; #598 in active build per #596 GO recommendation                |

`/ai` sits beside these three as a fourth surface trying to serve as lead magnet, capability page, and method-credibility note simultaneously. The 5-persona site review (April 2026) found its anti-hype tone is conversion-earning, but the page is structurally disconnected because it carries three jobs at once.

The persona review surfaced three load-bearing findings:

- Copy works; proof is missing (4/5 personas cited no case studies; 2/5 converted from `/scorecard`; 3/5 cited generic framing as the reason they didn't).
- Pricing opacity blocks Mike-archetype buyers.
- Founder opacity blocks David/Carmen-archetype buyers.

The Captain identified the structural redundancy in 2026-04-27 conversation: `/get-started` and `/scorecard` are doing the same job (intake from the prospect) at different fidelities. `/scan` is structurally different — it is _us showing what we already see_ — but it feeds the same engine: assess the business, name the gaps, estimate the ROI of fixing them, suggest a next step.

The Captain also observed that `/scan` is functionally the same pipeline as the internal entity-enrichment used for outbound prospecting. Inbound and outbound were designed in separate sessions with separate data models. They should be unified.

The Captain-articulated objective for any of these surfaces is:

> _"Help clients see where there is room for improvement and how that improvement can result in a positive ROI worth exploring."_

Three operative words:

- **See** — the artifact's job is recognition, not scoring or shaming.
- **Improvement** — gaps between what is and what could be.
- **ROI worth exploring** — concrete enough that the prospect can decide whether a conversation is worth their time.

A separate Captain observation reframes the ROI problem cleanly: prospects already know what their pain is worth. They live with it daily. They do not need a stranger to dollarize it. What they do not know is whether and how it can be fixed. The diagnostic's job is therefore solvability + fix-shape + cost-shape, with the owner's own numbers doing the financial multiplication. This also defends CLAUDE.md's no-fabrication rule by construction — we never quote a dollar number we did not derive.

A final structural observation: a PDF in an inbox is the wrong artifact for a relationship that should grow over months and across multiple stakeholders (owner, partner, CFO, accountant). The portal (`portal.smd.services`) already provides a durable, role-aware, magic-link-authenticated home for clients. Extending it to prospects unifies the entire relationship lifecycle in a single surface, eliminates the prospect-to-client data handoff seam, and turns Scott's admin into a real CRM rather than an inbox-as-CRM workaround.

## Decision

**One product — the Outside View. Three input depths. One persistent artifact, resident in the portal.**

### 1. The product

The Outside View replaces `/get-started` (cold-inbound mode), `/scorecard`, and `/scan` as three named lead-magnet products. There is one product. The visitor chooses the depth at which to engage with it.

The name "Outside View" is doctrine. It frames the value proposition correctly: _we are an experienced outside observer; here is what we see when we look at your business; this is what anyone with our skills would see; nothing surveillance, nothing invasive, just public footprint plus pattern recognition._ The verb form on the marketing site ("see what we see") and the noun form for the artifact ("your Outside View") both work clean.

### 2. The three input depths

| Depth                   | Commitment                                        | Inputs                                                                   | Output confidence            | Earns the right to  |
| ----------------------- | ------------------------------------------------- | ------------------------------------------------------------------------ | ---------------------------- | ------------------- |
| **D1: Outside view**    | 30 sec (URL + email)                              | Public footprint (web, reviews, Google Business Profile, public records) | Lower — surface signals only | Offer D2            |
| **D2: Conversation**    | 15 min voice/text with our agent                  | D1 inputs + what the owner tells us                                      | Medium-high                  | Offer D3            |
| **D3: Assessment call** | 60 min, $0 for first 3 prospects, $250 thereafter | D1 + D2 + Scott's eyes on it                                             | Highest                      | Engagement proposal |

Same engine. Same data model. **Same artifact shape at every depth** — fields fill progressively as depth increases.

### 3. The artifact contract

Every observation in the Outside View — at every depth — uses the same five-field shape:

1. **What we see / what you said** — traceable to a source. Either a signal we observed (review pattern, website behavior, public filing, GBP data) or a statement the owner made. Hover or tap reveals the source.
2. **The pattern** — what this combination usually traces to. Plain language. No jargon. ("This usually means one of three things…")
3. **Solvability** — explicit statement of whether and how reliably we have seen this fixed. No hedging when we are confident; "insufficient data" when we are not.
4. **Shape of the fix** — the _category_ of intervention (process change, tool config, integration, automation, role or cadence change), with concrete examples. Effort estimate in days/weeks, not hours/dollars.
5. **What it changes** — the levers the fix moves (repeat-customer rate, overtime spend, tech retention, owner hours, etc.). The owner does the financial multiplication against their own numbers.

A sixth field appears at the artifact level: **priority** — which observation we would address first, with reasoning grounded in the other five fields.

### 4. ROI policy

**No fabricated dollars.** Ever. At any depth.

The diagnostic does not say "this pattern is costing you $40,000/year." It says: _"This pattern usually solves with [process change | tool | integration | automation | cadence change]. Effort is days, not weeks. The levers it moves are repeat-customer rate, overtime, and tech retention — you know what those are worth to you better than we do. Do that math against your own numbers and tell us if it is worth a longer conversation."_

This is more powerful than fabricated ROI for three reasons:

- The owner trusts their own numbers more than ours.
- Refusing to make up numbers is itself a trust-earning differentiator. Every other AI lead magnet on the market fabricates dollar values; we will not, and we will be visibly the kind of firm that will not.
- It defends the CLAUDE.md no-fabrication rule structurally, with no per-engagement compliance overhead.

When the prospect asks the artifact directly for ROI estimates (in the chat), the agent answers in the same shape: ranges of effort, levers moved, examples drawn from public benchmarks where they exist, "insufficient data" where they do not.

### 5. Persistent home: the portal

The artifact lives in the portal at `portal.smd.services`. It does not live in an inbox.

**Public form → magic link → portal.** `smd.services/outside-view` is the marketing surface — a public, unauthenticated page with the form (URL + email + Turnstile + data-source disclosure copy). Submission triggers the scan pipeline and emails the prospect a magic link. The link sets a `prospect`-role session on `portal.smd.services` and lands them at their Outside View.

**The prospect never sees the word "portal."** Email subject and CTA: _"Your Outside View is ready"_. Header on first landing: _"Your Outside View. This is what we see when we look at your business from outside. It's yours to keep, and it grows as you go deeper."_ The plumbing is invisible.

**Cookie boundary respected.** Magic-link verification sets the prospect session on `portal.smd.services` only, per the per-host cookie pattern in CLAUDE.md. The marketing site never holds a portal session.

### 6. Role progression

The portal grows visibility as the relationship deepens. Same codebase, same URLs, role-conditional rendering on the existing `PortalTabs`.

| Role               | Set when                                                   | Tabs visible                                                                    |
| ------------------ | ---------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `prospect`         | Magic-link click after `/outside-view` submit              | Outside View only                                                               |
| `prospect_engaged` | D2 conversation completed, or N reactions/questions logged | Outside View only — same tab, richer artifact                                   |
| `client_active`    | Engagement contract signed                                 | Outside View + Engagement + Quotes + Invoices + Documents                       |
| `client_inactive`  | Engagement complete                                        | All tabs persist; Outside View gains "re-run / compare to last time" affordance |

The entity ID is constant across role transitions. The portal URL is constant. The Outside View is the genesis tab and remains the longitudinal reference for the lifetime of the relationship.

### 7. Tier-up: offered, never enforced

Every observation in the artifact carries an inline "go deeper" affordance. The artifact header carries persistent "talk to our agent" (D2) and "talk to Scott" (D3) affordances. Visitors at any depth can jump to any other depth in one click.

The default suggested ramp is D1 → D2 → D3 because that is the path with the most prospect-friendly slope. The marketing-site primary CTA points at D1. But D2 and D3 are reachable in one click from anywhere — both from secondary nav on the marketing site and from inside the artifact. We meet the visitor where they are.

D1 ends with a tier-up to D2 (_"want a deeper look? Spend 15 minutes with us and we'll dig past what we can see"_), not a direct ask for the call. A 30-second commitment has not earned a $250 ask. D2 ends with the call ask — 15 minutes of useful conversation has earned that next step.

### 8. Innovation: the artifact is alive

The Outside View is not a deliverable. It is a persistent, interactive, growing artifact the prospect controls.

- **Interactive observations.** Each observation has reaction affordances ("not us — here's what's actually going on" / "exactly right"). Reactions enrich the artifact in real time and feed signal to admin.
- **Ask-anything chat.** A persistent chat at the artifact level lets the prospect ask the agent about any observation, any pattern, any related thing. The agent has full context from D1 (and D2 once that depth is reached) plus the broader assessment doctrine.
- **In-place upgrades.** D2 runs at `/portal/outside-view/conversation` _inside the same portal session_. The agent already knows everything from D1. No re-explanation. No second form. Conversation completes — same artifact, deeper.
- **Resumability.** They walk away mid-thought and come back tomorrow. The artifact remembers them; the agent remembers them. Bookmark the portal or click the magic link in their email. No 7-day expiry awkwardness; the standard 90-day data-retention from #482 still applies, with owner-initiated deletion available.
- **Longitudinal.** Six months in, the prospect can re-run the scan and compare. Two years in (post-engagement), they can see what changed. The artifact becomes a record of their operations over time. (Compare-over-time UI is v2; the data model supports it from v1.)

This is the AI-agent-native shape of a lead magnet. It is not a slightly-better PDF.

### 9. Admin: portal-as-CRM, not inbox-as-CRM

`/admin/entities/[id]` (existing) gains an **Outside View tab** showing:

- The artifact as the prospect sees it.
- Their reactions per observation ("not us" / "exactly right" markers).
- Full chat transcript (Q&A they had with the artifact).
- D2 transcript and audio (when present).
- Agent's confidence-scored triage recommendation: _"call now / let cook / low priority"_ with reasoning.
- Activity log: opened, expanded, reacted, asked, started D2, completed D2, requested D3, booked.

`/admin/follow-ups` (existing) is extended with prospect signals, sorted by signal strength × recency. Each row carries one-click actions (send note, schedule, archive).

**Notifications.** High-signal events surface to the admin notification bell without flooding it: D2 completed; 3+ questions asked of the artifact; "bring Scott in" clicked; returning visit after 7+ days idle; **outbound entity matched on inbound submit** (a prospect Scott emailed cold three weeks ago just self-identified — high signal).

**Daily digest email.** End-of-day summary to Scott of high-signal events only. Not the firehose; the things worth opening admin for.

**The unification win.** An entity Scott emailed cold and that just submitted to `/outside-view` appears in the same record with both signals visible. The seam between outbound and inbound is gone at the data layer. That is a real CRM, not three disconnected views.

### 10. Re-scoping the in-flight work

This decision does not throw away in-flight engineering. It re-aims it.

**#598 (`/scan` build) — re-aimed.** Continue building the pipeline per the GO recommendation in #596. Apply the unified artifact contract (five-field-per-observation shape, no fabricated dollars). The completion email becomes a magic-link email pointing at the portal artifact, not a PDF attachment. This is a small change to the email template and the result-rendering endpoint, not a fundamental redesign. The form moves from `/scan` to `/outside-view` (with `/scan` redirecting for any indexed inbound traffic).

**#482 (Scorecard conversational rewrite) — re-homed.** The voice/text conversation engine, the agent personality, the multimodal upload, the live-research pattern — all kept. The conversation runs at `/portal/outside-view/conversation` inside an already-authenticated portal session. The agent's opening turn changes from "tell me what your business does" to "we already pulled up your business — here's what we noticed; walk me through what's behind it." This is fundamentally better UX _and_ cuts conversation-flow design work because the warm-start replaces the cold-start. The `/scorecard` URL redirects to `/outside-view`. Re-scoped scope estimate to be confirmed by the engineer who picks it up; expected to shrink modestly because Phase 1 (scan) absorbs some prep work and the experience lives inside an existing context.

**#612, #616 (scan rendering / abuse controls) — kept as-is.** These are ground-truth quality gates and apply unchanged.

**#483 (`/ai` credibility-gap epic) — partially deferred.** The disconnection problem dissolves once `/outside-view` is the unified front door. The `/ai` refresh becomes one of N capability sibling pages rather than a peer to lead magnets. Sequence after Outside View ships.

### 11. Retirements

- **`/get-started` cold-inbound mode** — retired. Redirects to `/outside-view`. Cold-mode submissions over the past 90 days are exported to `entities` and threaded through the standard outbound triage.
- **`/get-started` post-booking prep mode** — moved to `/portal/booked` (or `/portal/prep`) since the booker is, by definition, prospect-or-better and has a portal session.
- **`/scorecard`** — redirects to `/outside-view`.
- **`/scan`** — redirects to `/outside-view`.
- **The "scorecard" name** — retired in user-facing copy. Internal references in code may stay where renaming is not load-bearing; new code uses "outside-view" naming.

## Consequences

### Positive

- **One front door, one product, one mental model for the visitor.** The "do I take the scorecard or do the scan or fill out the form" choice paralysis is structurally eliminated.
- **Unified data architecture.** One entity record. One Outside View record per entity, with depth fields filled progressively. Inbound and outbound feed the same store. Admin triage shows everything we know about an entity in one place.
- **No prospect-to-client data handoff seam.** When the engagement starts, Scott begins at hour 5 of relationship-context, not hour 0. That accelerates time-to-value, which is itself part of the value prop.
- **No-fabrication defended structurally.** The ROI policy (solvability + fix-shape, no fabricated dollars) cannot be violated by the artifact's content shape. The five-field contract bakes in honesty.
- **AI-agent-native differentiator.** A live, persistent, interactive artifact is materially different from "AI-generated lead magnet PDF." This is the kind of move only an AI-agent-native venture can make at this scope, and the persona review's "anti-hype tone is working" finding suggests the market is ready for it.
- **In-flight engineering is re-aimed, not wasted.** #598 and #482 both ship. Neither is duplicated. Both are more valuable in the unified shape than in the original three-tools shape.
- **Scott's day starts with admin, not inbox.** Real CRM-shaped triage with signal sorting, agent recommendations, and one-click actions.

### Negative / accepted

- **Bigger v1 build than "email a PDF."** Phase 1 ships the static-feeling artifact in the portal; the chat layer, in-place D2, and longitudinal compare are later phases. We accept the additional engineering scope because the differentiator is the entire reason to build it at all. A diluted v1 (PDF in inbox) would not earn the persistent-relationship value prop and would force a rebuild later.
- **Prospect-role addition to the portal.** Cookie boundaries, role-based rendering, and tab-conditional UI all require deliberate work. The existing `PortalTabs` and role infrastructure carry most of it; the rest is additive.
- **Cost per prospect rises slightly.** Chat-on-the-artifact burns tokens. With the rate-limiting from #598 (4-dimensional limiter, $0.14 median per scan) and per-prospect chat caps, total per-prospect cost stays well under $3 even at high engagement. Acceptable for the conversion uplift.
- **Two vocabularies remain in code briefly.** Existing "scorecard" and "scan" naming will linger in some files until the renames complete. ADR 0001 already accepts this kind of taxonomic friction; we accept it here too with a non-blocking cleanup task.
- **The `/ai` page disconnect is partially deferred.** Solving it fully requires the capability sibling pages (one of six). Outside View ships before that. Acceptable because the front-door reframe addresses the root cause; the sibling pages are downstream cleanup.

### Out of scope (v1)

Explicitly out of scope for the first ship cycle. These can be additive later without architectural disruption.

- **Co-viewer sharing (CFO, partner, accountant).** v1 is owner-only via magic link. Shareable read-only links are v2.
- **Comments and multi-stakeholder annotation.** Owner reactions in v1; collaborative annotation in v2.
- **Longitudinal compare-over-time UI.** Data model supports it from v1; UI is v2 once N≥2 scans per entity exist at scale.
- **Pattern-library benchmarks across prior assessments.** Gated on N≥30 prior assessments with diversity, per the existing CLAUDE.md no-fabrication rule.
- **Voice cloning, AI up-sell during sessions, public aggregate-pattern publishing.** Listed as out-of-scope in #482; remain out of scope here.

## Phase plan

Each phase ships independently and earns the next. None throws away prior work.

**Phase 1 — D1 ships, portal-resident, static-feeling artifact (~1.5–2 weeks).** `/outside-view` form on the marketing site. Magic-link email lands the prospect at `portal.smd.services/outside-view` with a `prospect`-role session. Portal renders the Outside View as the only tab. Artifact uses the five-field-per-observation contract. ROI policy enforced. Admin entity tab populated. Reuses the in-flight #598 pipeline.

**Phase 2 — Interactive layer (~1 week).** Reactions on observations. Ask-anything chat at the artifact level. Activity log feeding admin signals. Notification bell + daily digest.

**Phase 3 — D2 in the portal (~3 weeks).** "Go deeper" inline upgrade triggers `/portal/outside-view/conversation`. Voice + text conversation per the #482 scope, but warm-starting from D1 context. Conversation enriches the same artifact in place.

**Phase 4 — Persistence and shareability (~1 week).** Per-prospect URLs become invitee-shareable read-only. "Re-run later" flow. Multi-stakeholder views (read-only initially).

**Phase 5 — Longitudinal and pattern library (later).** Compare-over-time UI. Pattern-library benchmarks (gated on data accumulation per CLAUDE.md).

Total Phase 1–4 budget: ~6–8 weeks of fleet work, parallelizable. Phase 1 alone is largely in flight via #598.

## Open questions / deferred decisions

These do not block ADR acceptance but should be resolved before the relevant phase ships.

- **Voice provider for D2 (deferred from #482).** OpenAI Realtime vs. Vapi vs. Retell. ½-day spike before Phase 3. No impact on Phase 1.
- **Agent persona name and voice for D2.** Distinct from Scott. Warm, not sales-y. Captain to approve voice from ElevenLabs library before Phase 3.
- **Sender identity for the magic-link email.** `hello@smd.services` (branded) or Scott's personal address. Recommend branded with Scott's name in signature; Captain to confirm before Phase 1 ships.
- **Inactive-prospect retention beyond 90 days.** Default soft-delete after N days of inactivity, or never? Probably never (storage is cheap, the artifact is part of why this is generous). Confirm with Captain before Phase 4 introduces persistence semantics.
- **D2 entry from non-D1 visitors.** Direct `/portal/outside-view/conversation` entry for prospects who skip D1 (referrals, Vistage entries). Auth via the same magic-link flow but with a placeholder D1 stub. Confirm UX before Phase 3.

## Issues to file

- _Retire `/get-started` cold-mode → redirect to `/outside-view`. Move post-booking prep to `/portal/booked`._
- _Redirect `/scorecard` and `/scan` to `/outside-view` once Phase 1 ships._
- _Add `prospect` and `prospect_engaged` roles to the portal auth model. Conditionalize `PortalTabs` on role._
- _Add Outside View tab to `/admin/entities/[id]`. Extend `/admin/follow-ups` with prospect signals. Add notification rules and daily digest._
- _Update CLAUDE.md to reference this ADR in the "Current Phase" section and add Outside View to the lead-magnet language._

## Related

- ADR [0001 — Taxonomy Two-Layer Model](0001-taxonomy-two-layer-model.md) — observation vs. delivery taxonomy, applies to the agent's vocabulary inside D1/D2.
- [docs/strategy/lead-gen-strategy-2026-04-25.md](../strategy/lead-gen-strategy-2026-04-25.md) — Engine 1 strategy that this ADR realizes and unifies with the rest of the front door.
- CLAUDE.md § "No fabricated client-facing content" — hard constraint that the ROI policy and artifact contract defend by construction.
- CLAUDE.md § "Tone & Positioning Standard" — Outside View doctrine ("we work alongside you," objectives over problems, no fixed timeframes in marketing copy) governs all Outside View copy.
- Persona review (April 2026) — referenced in #483; informs the trust-earning shape of the artifact and the anti-hype tone applied to the agent's voice in D1 and D2.
