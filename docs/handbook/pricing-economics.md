---
title: Pricing & Economics
section: business
order: 5
owner: captain
review_every: 180
last_reviewed: "2026-10-21"
summary: How each of the three offers is priced and why, where the private Operator figures live, how new work is priced, and the cost lines the Captain watches. All consulting and Operator figures are internal; the Hosted Agent price is the one published exception (ADR 0067).
sources:
  - label: Decision Stack (Decisions #13, #14, #16, #25, #27, #50, #51)
    href: docs/adr/decision-stack.md
  - label: ADR 0004 - Productized Operator Offering
    href: docs/adr/0004-productized-operator-offering.md
  - label: ADR 0063 - Operator launch pricing (figures private)
    href: docs/adr/0063-operator-launch-pricing.md
  - label: ADR 0062 - Operator cost plane
    href: docs/adr/0062-operator-cost-plane.md
  - label: ADR 0067 - Hosted Agent self-serve SKU
    href: docs/adr/0067-hosted-agent-self-serve-sku.md
  - label: Pricing-figures guard test
    href: tests/pricing-figures.test.ts
  - label: Card processing fee
    href: src/lib/pricing/card-fee.ts
---

Each offer has its own pricing shape: consulting is a fixed project price built from an internal hourly rate, the Operator is a flat monthly fee plus a one-time stand-up fee, and the Hosted Agent has a published monthly price. Prices are the Captain's to set; you invoice at prices already set, and anything new, discounted or changed goes to the Captain ([Who Does What](/admin/playbook/who-does-what)).

## The governing rule

No dollar amount appears on the website or in marketing (Decision #16; [Positioning & Voice](/admin/playbook/positioning-voice), rule 4). The client sees a price in their proposal or agreement, never on a public page, and never the hourly rate. The one exception is the Hosted Agent price on `/agent` (ADR 0067).

This handbook is in a public repository (Decision #52), so the Operator's figures are not written here at all. They live in the private `venturecrane/engagements` repository under `pricing/`, and `tests/pricing-figures.test.ts` fails the build if they reappear in this one. The consulting figures below are internal policy; do not quote them to a client.

How money is collected (subscriptions, invoices, the card fee, failed payments) is on [Money](/admin/playbook/money). This page is about what the prices are and why.

## Consulting

### The internal rate ladder

The rate rises as the firm builds proof (Decision #16). It is used to build a quote; the client never sees it.

| Tier | Rate | When to move up |
| --- | --- | --- |
| Launch | $175/hr | Starting rate |
| First reference | $200/hr | After the first completed engagement with a case study |
| Established | $250/hr | Consistent pipeline and 2 to 3 case studies |
| Volume | $300/hr | Referral-driven inbound and demonstrated results |

Moving up a tier is a pricing decision, so it is the Captain's.

### How a quote is built

The assessment is the pricing conversation (Decisions #16 and #18):

1. The assessment call finds the problems to solve.
2. Solution design estimates the hours for each (tool selection, configuration, documentation, training).
3. Quote = estimated hours times the current rate.
4. The client sees one fixed project price, never an hourly breakdown.
5. Estimated against actual hours is tracked on each engagement to improve later quotes.

The smallest engagements start around $2,500 at the launch rate; below that, the assessment costs more than the work is worth. There is no ceiling (Decision #16).

### The paid assessment

The first three assessments are free; after that an assessment costs $250, credited in full toward the engagement if the client goes ahead. The switch to paid happens after the first delivered engagement. The free period is internal policy, never a marketing offer (Decision #13).

### Payment terms

50% deposit at signing, 50% at completion (the handoff session). For engagements of 40 hours or more, consider 40% deposit, 30% at a mid-point milestone named in the SOW, and 30% at completion (Decision #14). See [Money](/admin/playbook/money) for how these invoices go out.

### After handoff

Two weeks of async support are included (Decision #27). After that, more work is a new scoped project or, if it fits, an Operator subscription (ADR 0004, lock 4). The old undefined retainer (Decision #12) is superseded.

### Pipeline planning model

The planning model assumes an average engagement around $7,500 against a $200k annual target: about 27 engagements a year, or 2 to 3 closes a month. The real limit is how many assessment calls can be run, about 3 to 4 a week at steady state (Decision #25). It is a planning assumption written before the first sale, not a measured figure.

## The Operator

**Shape.** A flat monthly fee: not metered, not per task, not scoped per engagement (ADR 0004, lock 1). Plus a one-time stand-up fee for provisioning, configuration and onboarding (ADR 0063).

**The figures.** Locked by the Captain on 2026-07-04 and kept in `venturecrane/engagements:pricing/operator-launch-pricing.md` (ADR 0063, Decision #50). Each client's actual price is in that client's agreement in the same repository. Ask the Captain for access ([Handoff Companion](/admin/playbook/handoff-companion)).

**Why the price is what it is (ADR 0063):**

- It is set against the cost of the hire it replaces, a fully loaded coordinator's salary, never against a software subscription (ADR 0037, Tenet 1).
- It sits above the cost model's floor. The earlier working figure turned out to be that floor, mostly the Captain's own supervision time; priced there, SMD would have broken even.
- At this price the cost alarm (below) only trips when something is genuinely wrong, not in an ordinary month.

**How it is paid.** The client starts the monthly subscription themselves in the portal; the stand-up fee is a separate implementation invoice. Bank debit is the default and has no fee; card adds a 3% processing fee as its own line (`src/lib/pricing/card-fee.ts`; [Money](/admin/playbook/money)).

**New kinds of work (change requests).** The monthly fee covers the work named in the client's agreement: the routines and settings recorded for the client, the connected systems, and the ordinary questions and tasks staff bring the Operator. A request for a new kind of work is a change request. In the agreement drafted for SMD's one Operator client (section 2.7, in the private repository), SMD may do the first instances as a trial, says so when it accepts, and measures what each costs. It then proposes written terms: included as ordinary work, included up to an allowance with anything beyond it quoted first, or a change to the fee. Nothing becomes a regular part of the service until the client accepts in writing. Every open change request is a row in the [obligation register](/admin/playbook/obligation-register). Proposing terms sets a price, so it is the Captain's.

## The Hosted Agent

The one published price: **$79 a month**, with the first 25 founding subscriptions at **$49 a month for as long as they stay active**, enforced by a Stripe coupon capped at 25 uses (ADR 0067, Decision #51; the price shows on `src/pages/agent.astro`). The customer brings their own Anthropic key with a spend limit they set, so the AI usage cost is theirs; SMD's cost is the machine and support time. A plan-retirement clause in the product terms bounds the founding-price promise. The two hosted-agent seats running today are SMD's own and SMD pays for them (ADR 0090; [Clients & Commitments](/admin/playbook/clients-and-commitments)).

## The cost lines the Captain watches

- **The kill line.** If a seat's running cost goes above 40% of its monthly price for two months in a row, the Captain reviews whether the seat still makes sense (ADR 0062). The cost dashboard at `/admin/operator/costs` shows each seat against this line; nothing acts on it automatically.
- **Pilot and SMD-owned seats** carry the list price for this calculation while being invoiced nothing, so the dashboard still reads true (ADR 0063).
- What SMD spends on vendors each month is on [Money](/admin/playbook/money).

## Related pages

- [Money](/admin/playbook/money): how every price on this page is collected.
- [Positioning & Voice](/admin/playbook/positioning-voice): the no-published-price rule.
- [The Operator Thesis](/admin/playbook/operator-thesis): why the Operator is priced against a salary.
