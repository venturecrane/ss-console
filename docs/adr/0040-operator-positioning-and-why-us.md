---
title: Operator Positioning & Why-Us — Law-First GTM
date: 2026-06-07
status: accepted
captain: Scott Durgan
amends: 0037-operator-thesis.md
related-adr: 0004-productized-operator-offering.md, 0005-external-send-identity.md, 0022-vertical-pack-architecture.md, 0025-autonomy-ceilings-configurable-exposure-vs-initiation.md, 0034-operator-product-naming.md, 0035-no-imposed-entitlement-defaults.md, 0038-operator-vertical-delivery-method.md, 0039-operator-led-assessment-funnel.md
related-doc: docs/specs/operator/competitive-landscape.md
---

# ADR 0040 — Operator Positioning & Why-Us (Law-First GTM)

**Status:** Accepted (Captain decision, 2026-06-07).

**Purpose.** The citable answer to _how we position the Operator and answer a buyer's "why you, why this,"_ law-first. It derives from [ADR 0037](./0037-operator-thesis.md) (the thesis) and turns it into the words we use in market. All downstream collateral — the sales talk-track + objection responses, and the marketing pages (`src/pages/operator.astro`, `src/pages/packs/law-firm.astro`) — derives from this ADR; it is not authored independently. The evidence behind the competitive posture lives in the [competitive landscape reference](../specs/operator/competitive-landscape.md).

**Why this exists.** Across strategy sessions the positioning kept drifting back to stale framings (send-identity-as-the-product, "we don't do legal work," fixed-limit language, "AI employee" as the opener). This ADR locks the corrected framing so collateral is generated _from_ it rather than re-derived — and re-importing the errors — each time.

## Context

Small law firms (solo–20 attorneys) are saturated with AI pitches and, having watched peers get sanctioned over hallucinated citations, ask the same thing across the table: _why you, why this._ This ADR is the answer, and the rip of the framings that undercut it.

## Decision — the positioning

### 1. Compete with a hire, not software (the frame)

Everything a firm can buy today is one of three things: software they run themselves, a service that stops at the front desk, or people they manage. We are none of those — a worker we build and run. Salary is the **frame** (price it against the hire it replaces, never against a software seat), **not** the moat: "priced like a hire" is the entire AI-employee category's default register and is not, by itself, a differentiator (see the reference doc).

### 2. The spine (the why-us)

**"It runs on your firm's expertise and gets better at your firm every week."** The client is the expert; we are not. We build the worker, the firm teaches it (intake questions, voice, rules, practice areas), and the per-firm memory compounds and stays the firm's — it does not walk out when staff leave. This is the one claim competitors cannot shortcut (it requires tenure inside the specific firm), and it is the line the rest hangs from.

### 3. The pillars (each hangs off the spine)

- **Configurable entitlements** — the firm sets what the worker does and what it does on its own, and changes that anytime; enforced in code, journaled, never self-escalating ([0025](./0025-autonomy-ceilings-configurable-exposure-vs-initiation.md), [0035](./0035-no-imposed-entitlement-defaults.md)). Not a fixed posture.
- **Privacy** — the worker runs in the firm's isolated environment; we see system health and the action journal, never client content; content access happens only on the firm's explicit, logged authorization. _The privileged-access support protocol is a build/design item, not yet shipped — do not claim it as live until it is._
- **Customer control** — the firm holds the keys: configurations, logs, tuning, data export, corrective action.
- **The firm's voice** — a configurable persona that writes in the firm's voice from its own samples. For a profession whose written word _is_ the product, this is what makes output usable rather than cosmetic (Voice Layer 2 / [#855](https://github.com/venturecrane/ss-console/issues/855) is the active build). **[Amended 2026-11-09: the mechanism changed. The firm's voice is a written specification per output class, distilled from the firm's own documents and authored with the firm ([ADR 0083](./0083-authorship-model-output-classes.md)). The sample-transform mechanism that reduced samples to a fingerprint was removed on 2026-11-07 (ADR 0083, amendment of that date).]**
- **Engine-agnostic + token-rich** — it runs the best available model and we do not ration the compute, so it can check its own work (the answer both to the hallucination fear and to "why does it cost more than a $400 tool"); and because the harness is independent of any one model vendor, the worker improves underneath the firm and is not a single-vendor bet ([ADR 0037](./0037-operator-thesis.md) Tenet 4 made concrete, and the hedge against substrate-vendor risk).
- **The managed service is half the offering** — we build the employee manual with the firm, implement alongside the team, monitor it, and support it ongoing. This is what defeats the category's number-one deal-killer (adoption / shelfware).

### 4. What "deep" means for us

Depth in the **operator** — capability, configuration, the guide methodology, governance, monitoring — plus a _thin_ per-vertical pack (connectors, compliance floors, workflow scaffolds). **Not** domain expertise: that is the client's, captured in configuration and compounded in memory. This is precisely why the agnostic-substrate-plus-packs bet compounds where single-vertical-deep (become-the-domain-expert, e.g. Harvey/Eve/Crosby) cannot — the non-compounding axis (domain knowledge) is contributed by the client, not built by us. "We are not law experts; the client is" is why the thesis works, not a weakness in it. This is the firm-wide "client is the hero and the expert, we are the guide" standard expressed in product form.

### 5. The boundary, stated correctly

The Operator does **paralegal and coordinator work** — connective and substantive support — never **independent legal judgment, advice, or representation** (the license line, the same line a human paralegal works within). Collateral must **not** say "we don't do legal work"; that is inaccurate and undersells the product.

## The rip — framings superseded for positioning and collateral

These were correct-at-the-time mechanisms or shorthand that drifted into _identity_. None may appear as product identity in any collateral:

- **Draft-for-review send is not the identity or the mantra.** It is one authored option among configurable entitlements ([0025](./0025-autonomy-ceilings-configurable-exposure-vs-initiation.md)/[0035](./0035-no-imposed-entitlement-defaults.md)). Do not present it as what the Operator "is."
- **"We don't do legal work" is wrong.** Replace with the paralegal / license-line framing (§5).
- **Fixed-limit language** ("never sends," "never moves money," "never clears a conflict") **is not product identity.** Those are configurable dials the firm sets; presenting them as inherent contradicts the harness thesis.
- **Do not open with "AI employee."** That framing lowers trust and raises replacement fear ([ADR 0039](./0039-operator-led-assessment-funnel.md) / the BCG finding). Lead with the seat and the outcome; "AI" stays downstream.
- **Breadth opens the door but is copyable.** Lead with it to get in the room; do not rest the moat on it (the market is converging on breadth from both ends).
- **Keep the horizontal "serves any business" story out of the room.** It is the investor/strategy narrative; in a partner conversation it dilutes the "built for your firm" intimacy. Breadth is reassurance _only_ once depth is correctly located in "runs on your firm's expertise" (§2/§4).

## Competitive posture (summary; full evidence in the reference doc)

- No competitor occupies the full cell (managed + governed/private + whole-coordinator + firm-voice + per-firm memory + salary-priced, for small firms). It is under-occupied but a **closing window (~12–18 months)**, approached from both ends (front-office walking into coordination; substantive platforms walking into intake).
- Watch-list: **Clio** (incumbent on the system of record — "assistant → staff" is the canary), **Caseflood / CaseGen / LawFirmIgnite / Blueshoe** (legal entrants), **Legal Soft VA+** (managed staffing with the architecture inverted — human-operator/AI-tool), **Vendasta-armed agencies** (commoditization from below), **Anthropic** (nearest architectural neighbor and our own substrate — engine-agnosticism is the hedge).
- The deep edges (per-customer isolation, firm voice, compounding memory) **hold on competitors' silence, not on disproof.** Making them **provable and demonstrable** — the onboarding teach-back, the employee manual on screen, the journal showing a correction holding — is the highest-leverage product work, because it converts three claims into a moat. Treat incumbents as connection targets, not rivals ([ADR 0037](./0037-operator-thesis.md) Tenet 1).

## Amendment (2026-07-04) — Competitive response: managed vertical platforms (EvenUp-class)

Captain decision, 2026-07-04 (issue [#1685](https://github.com/venturecrane/ss-console/issues/1685)), from the strategic-review finding that the pilot vertical acquired its first funded adjacent competitor after this ADR was written: **EvenUp** ($2B valuation, $150M Series E) launched Pre-Litigation-as-a-Service in May 2026 — AI plus their own US-based case-management staff running the PI pre-lit lifecycle (claim setup, records retrieval, demand prep, carrier negotiation, lien resolution), with more than $10M in subscriptions during early testing. "Nobody does this" is no longer sayable in PI pre-lit, and a prospect will raise them. **Eve** (nightly Auditor + drafting/chasing agents) is the closest software-only overlap; intake AI (Smith.ai, Gideon, Lawmatics) competes for one slice of the coordinator job.

**The counter-story is custody, coverage, configurability — in that order:**

1. **Custody.** PLAAS is a platform the firm routes matters through; the Operator is the firm's own employee inside the firm's own systems — their Smokeball, their entitlements, their audit trail, their voice, their governance. The work never leaves the firm's custody, and the memory the Operator builds belongs to the firm's seat, not to a vendor's platform (the "deep edges" above are exactly what an outsourced pre-lit vendor cannot offer).
2. **Coverage.** PLAAS is pre-lit only. The Operator covers what pre-lit isn't: the litigation phase, calendaring, vendor chasing, intake coordination, internal ops — the connective tissue across everything the firm runs, which is the hire it replaces (ADR 0037 Tenet 1).
3. **Configurability.** The Operator is authored per firm, not a bet on one practice-area workflow that a vertical vendor commoditizes.

**The honest-broker beat is part of the answer, not a concession:** for a firm whose need is pure pre-lit demand volume, PLAAS is a reasonable buy, and we say so plainly — the Operator can work alongside it, coordinating the parts PLAAS doesn't touch. This keeps the answer in the guide's voice and out of competitor-bashing.

**Where this lives (naming posture):** competitors are never named on public pages. The frame lives here and in the derived talk-track objection block (`docs/collateral/operator-law-talk-track.md`), used when a prospect raises them. The law pack page already carries the custody frame implicitly and stays as-is until the A&P proof lands — proven copy is not reworked ahead of proof.

**Tripwire:** EvenUp expanding beyond pre-lit into general firm operations triggers an out-of-cycle competitive note and a re-read of this frame. The §Competitive posture watch-list is extended accordingly.

## GTM (parked here for continuity, not decided here)

Getting in front of firms is a **trust-borrowing**, not an awareness, problem. The chosen first lever is **trusted intermediaries** who already serve small firms and feel their operational mess as their own (legal bookkeepers / trust-accounting specialists and Clio Certified Consultants first; fractional CFOs phase-1.5; malpractice carriers and bar practice-management advisors later). Founder-led direct outreach is right for the first cohort but should carry one degree of warmth and lead with a ten-minute live demo, not a pitch. A first-cohort intermediary target list is compiled. This track is parked; it is referenced here for continuity.

## Consequences

- **Collateral derives from this ADR.** The sales talk-track + objection responses and the marketing-page revisions are generated from §§1–5 and must not reintroduce any framing in the rip.
- **A provable-trio product mandate** falls out of the competitive posture: per-customer isolation, firm voice, and compounding memory must become demonstrable, or the why-us stays a claim.
- **Marketing copy is authored to the firm's voice standard** (no em dashes, no AI register, no fabricated content; voice-driven sections authored by Captain). This ADR supplies the argument, not the final public wording.

## Verification

We are following this decision when: collateral leads with the hire frame and the spine (§2), never with "AI employee"; no surface reasserts send-identity-as-the-product, fixed limits, or "we don't do legal work" as identity; the boundary is stated as the paralegal / license line; competitive analyses rank on the cell and treat incumbents as connection targets; and the provable-trio is tracked as product work.

## References

- [ADR 0037 — The Operator Thesis](./0037-operator-thesis.md) (parent; this operationalizes it into positioning)
- [ADR 0004](./0004-productized-operator-offering.md) / [ADR 0034](./0034-operator-product-naming.md) (the SKU and naming)
- [ADR 0005](./0005-external-send-identity.md) / [ADR 0025](./0025-autonomy-ceilings-configurable-exposure-vs-initiation.md) / [ADR 0035](./0035-no-imposed-entitlement-defaults.md) (entitlements — the basis of the rip)
- [ADR 0022](./0022-vertical-pack-architecture.md) (packs), [ADR 0038](./0038-operator-vertical-delivery-method.md) (delivery method), [ADR 0039](./0039-operator-led-assessment-funnel.md) (assessment funnel + the trust finding)
- [Competitive landscape reference](../specs/operator/competitive-landscape.md) (the evidence behind the competitive posture)
