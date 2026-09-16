---
title: Pricing & Economics
section: business
order: 4
summary: The internal rate ladder, engagement range, payment terms, paid assessment, and the Operator and Hosted Agent pricing postures - consulting and Operator figures internal; the Hosted Agent price is the one published exception (ADR 0067)
sources:
  - label: Decision Stack (Decisions #13, #14, #16)
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/decision-stack.md
  - label: CLAUDE.md - Pricing
    href: https://github.com/venturecrane/ss-console/blob/main/CLAUDE.md
  - label: ADR 0004 - Productized Operator Offering
    href: https://github.com/venturecrane/ss-console/blob/main/docs/adr/0004-productized-operator-offering.md
---

## The governing rule

No dollar amounts appear on the website or in marketing materials (CLAUDE.md, Pricing; Decision #16 pricing guardrails). The client sees a project price in their proposal, never on a public page, and never the hourly rate. **Every figure on this page is internal.** This page is admin-only documentation; the numbers here are for planning and quoting, not for publication. The positioning rule behind this lives in `/admin/playbook/positioning-voice`.

## The rate ladder

The internal hourly rate advances as the firm builds proof (Decision #16, internal rate schedule):

| Tier | Rate | Trigger to advance |
| --- | --- | --- |
| Launch | $175/hr | Starting rate |
| First reference | $200/hr | After the first completed engagement with a case study |
| Established | $250/hr | Consistent pipeline plus 2-3 case studies |
| Volume | $300/hr | Referral-driven inbound plus demonstrated results |

The rate is internal math. The client pays for outcomes, not hours (Decision #16). The value is an experienced team that can see the problems the owner cannot, decide fast, and implement in days.

## How quoting works

The assessment is the pricing conversation (Decision #16, Decision #18):

1. The assessment call identifies the problems to solve.
2. The solution design phase estimates hours per problem (tool selection, configuration, documentation, training).
3. Quote = estimated hours times the current rate.
4. The quote is presented to the client as a fixed project price - "Operations Cleanup: $X,XXX" - never an hourly breakdown.
5. Internal tracking compares estimated versus actual hours per engagement to calibrate future quotes.

## Engagement range

Scoped per engagement (CLAUDE.md, Pricing; Decision #16 guardrails):

- **Smallest engagements** start around **$2,500** at the launch rate (targeted automation scripts, AI pilots). Below that, assessment overhead exceeds delivery value.
- **Largest engagements** have **no fixed ceiling.**

Nothing about this range is published externally.

## Paid assessment

The assessment is a paid entry point that converts into the engagement (Decision #13):

- **First 3 clients:** free. The bottleneck at launch is getting anyone on a call.
- **After that:** **$250**, applied in full toward the engagement fee if they proceed.
- The trigger to flip from free to paid is the first delivered engagement - once there is something to point to, the $250 is easy to justify.
- The free period is internal policy, never a marketing offer.
- Raising to $500 may be appropriate once brand recognition reduces friction; revisit after pipeline is established.

## Payment terms

Standard terms are a 50/50 split (Decision #14):

- **50% deposit at signing** to book the engagement start date. The deposit protects against cancellation.
- **50% at completion**, defined as the handoff session. The completion payment aligns with delivered value.
- The SOW specifies the completion milestone clearly so there is no ambiguity.

**For larger engagements (40+ hours):** consider a three-milestone structure - **40% deposit / 30% at a mid-engagement checkpoint / 30% at completion** - with the mid-point milestone defined in the SOW against specific deliverables.

## The Operator pricing posture

The Operator is a productized flat-rate retainer SKU ([ADR 0004](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0004-productized-operator-offering.md), lock 1). The *shape* of the pricing is locked: a fixed monthly price, not metered, not credit-based, not scoped per engagement. The customer signs up for a productized service, not a scoped engagement.

The **specific price is locked and internal, never published**: **a flat monthly retainer plus a one-time stand-up fee** (figures in `venturecrane/engagements:pricing/`) ([ADR 0063](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0063-operator-launch-pricing.md), Decision #50, Captain 2026-07-04). The number prices at the salary anchor (the Operator competes with a hire; the displaced coordinator seat runs roughly $60k/yr loaded) and sits above the committed cost model's floor - the earlier working baseline turned out to be ~90% Captain labor, so pricing at it would have been break-even. At the locked MRR the cost-plane kill criterion (seat COGS above 40% of MRR for two consecutive months, ADR 0062) trips at a level, making a trip a genuine anomaly signal. The client sees the price in their proposal; no dollar amount appears on any public surface.

## Change requests on the Operator

The flat fee buys the work named in the agreement: the routines in Schedule A-1 at their recorded settings, the connectors in Exhibit A, and the ordinary questions and tasks people bring the Operator. Anything else is a change request, and the agreement's new-work clause (service agreement section 2.7, rewritten 2026-09-16) says how one is handled. We say so when we accept it. We may perform the first instances as a trial and we measure what each one costs, because a price set before measuring is a guess, and routine 11 taught what a guess costs: the July definition of the medical chronology priced a memo, and the product the firm actually needed cost twenty times more per matter. After the trial we propose terms in writing, one of three outcomes: included as ordinary work when the cost is negligible; included up to an allowance sized to the firm's measured volume, with a one-off overrun quoted before it starts, when the cost tracks records read or documents produced; or a change to the fee when the work is of a different scale, or when an allowance is exceeded on a sustained basis. Nothing becomes a regular part of the service until the firm accepts the written entry. The three drafted documents of September 2026 (a demand letter, a case-evaluation memo, a mediation brief) are the first change request under this process; their proposal follows a census of the firm's own historical volume, never a number picked from three requests.

## The Hosted Agent pricing posture

The Hosted Agent is the second recurring SKU ([ADR 0067](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0067-hosted-agent-self-serve-sku.md), Decision #51): a self-serve subscription at **$79/month**, with the first 25 founding seats at **$49/month for the life of the subscription** (enforced by a Stripe forever-coupon with a 25-redemption cap). This is the one deliberate exception to the no-published-price rule: self-serve requires a visible price, so the `/agent` product page publishes it, recorded as a page-scoped exemption in the positioning spine's decision log and in the guard tests. The Operator's price stays internal; nothing else on the site shows a dollar amount.

The customer supplies their own Anthropic API key (with a spend limit set in their own console), so inference cost is theirs by design; SMD's seat cost is the Fly Machine plus support minutes, which keeps the ADR 0062 COGS gate honest at this price point. Founding-price obligations are bounded by a plan-retirement clause in the product terms, not open-ended.

Post-handoff support for scope-based engagements is separate from the Operator: a two-week async stabilization period is included (Decision #27). Beyond that window, the client is quoted a follow-on scope or converted to an Operator subscription if the fit is right (Decision #44 / ADR 0004, lock 4).

## Pipeline economics

The pipeline model assumes a $7,500 average engagement against a $200k annual target, which works out to roughly 27 engagements per year, or 2-3 closes per month (Decision #25). The real constraint is assessment-call capacity (roughly 3-4 per week at steady state), not outreach volume. The full pipeline math lives in the Decision Stack; see `/admin/playbook/decision-stack`.

## Related pages

- `/admin/playbook/positioning-voice` - the no-published-dollar-amounts rule and the tone standard.
- `/admin/playbook/business-model` - the two front doors and the engagement phases the quote is built against.
- `/admin/playbook/operator-thesis` - why the Operator is priced against a salary, not a software seat.
