---
title: The Consulting Engagement
section: product
order: 6
summary: The scope-based consulting product - one bounded engagement that runs assessment to handoff, priced per scope, where the assessment call is the product
sources:
  - label: Engagement Phases & "The Assessment Call Is the Product" (CLAUDE.md)
    href: https://github.com/venturecrane/ss-console/blob/main/CLAUDE.md
  - label: Assessment Call Script
    href: https://github.com/venturecrane/ss-console/blob/main/docs/collateral/assessment-call-script.md
  - label: Pricing Framework
    href: https://github.com/venturecrane/ss-console/blob/main/docs/collateral/pricing-framework.md
  - label: Proposal / SOW Template
    href: https://github.com/venturecrane/ss-console/blob/main/docs/collateral/proposal-sow-template.md
---

## What the consulting engagement is

The consulting engagement is one of SMD's three front doors. It is a scope-based, bounded piece of work: we sit down with a business owner, understand where they are trying to go, design a solution, build it, train their team, and hand it off. One engagement, one scope, one price. It is not a retainer and not a subscription. The other front door is the [Operator](/admin/playbook/operator-thesis), a productized monthly offering; this page is about the consulting product only.

The shape of the buyer is an owner-led business that has outgrown how it runs today: too big for one person to hold in their head, too small to justify a full-time COO, with real operational load and the ability to pay for a solution. There is no revenue-band qualification gate (the old "$750k-$5M" band was retired per ADR 0003). Qualification happens in conversation, not by filtering on a guessed revenue figure.

The work spans six solution categories, the delivery taxonomy that is SMD's source of truth for what we build: process design, custom internal tools, systems integration, operational visibility, vendor/platform selection, and AI & automation. A single engagement usually combines two or three of these. See [Business Model](/admin/playbook/business-model) for the full taxonomy.

## The assessment call is the product

The most important idea on this page: the value SMD sells is not configuring a tool. Anyone can configure HubSpot. The value is the assessment itself - an experienced outsider seeing the operation with fresh eyes, naming the problems the owner is too close to see, prioritizing ruthlessly ("these three things first, everything else later"), and making decisions so the owner does not research for six months.

This reframes the whole engagement. The build is downstream of a good assessment. If the assessment is right, the deliverables almost design themselves; if it is wrong, no amount of tool configuration saves the engagement. That is why the assessment is a paid product in its own right, not free discovery (see the paid-assessment section below).

The assessment call is run as a structured conversation, not an audit. It moves through opening, objectives, a day-in-the-life walkthrough, and a summary. The objectives come before the problems on purpose: the owner usually knows the pain but has not articulated the goal, and part of our value is helping them discover the real objective. We do not give them a faster horse. The script weaves in "ROI anchor" questions so the owner quantifies their own cost of inaction - when the owner says the number out loud, it is theirs, not ours. The call ends with a summary in the owner's words and one commitment: a scope and price within a couple of days. We do not quote on the call, commit to a timeline, or recommend specific tools by name. The full guide is the [assessment-call-script](https://github.com/venturecrane/ss-console/blob/main/docs/collateral/assessment-call-script.md).

## The five phases

Every engagement includes every phase. What changes is how heavy each one is - scope determines depth, not presence. Training may be a multi-day program or a single "on Tuesdays you click this button." Implementation may be a multi-week build or a one-afternoon script. The phases (per CLAUDE.md "Engagement Phases"):

**1. Assessment call.** Walk through their day, "show me how you do X," surface the top objectives and the two or three most acute operational gaps. This is the product (above). It produces the input to solution design.

**2. Solution design.** Choose the simplest tools that solve the problem, design the workflows, estimate scope and price, and send the proposal. This is where the six solution categories turn into concrete deliverables. The output is a proposal/SOW sent within 48 hours of the call, derived through the [pricing framework](/admin/playbook/pricing-economics).

**3. Implementation.** Build the templates, workflows, and documentation; configure tools; migrate data; connect systems. This is where most of the hours land. The deliverables are whatever closes the gaps named in the assessment - a lead-intake form and follow-up automation, a dispatch runbook, a financial dashboard, an internal tool, an integration between two systems the owner picked.

**4. Training.** Hands-on walkthrough with the team, practice with real data, written "how-to" documentation for every process and tool set up, and identification of an internal champion who can own and troubleshoot the solution after we leave. A champion identified on the assessment call carries through to here.

**5. Handoff and polish.** Handle feedback, adjust based on real use, deliver the final handoff. A two-week async stabilization period follows the final handoff (Decision #27), included in the engagement price.

## How the six solution categories show up in delivery

The six categories are not separate products the client picks from a menu. They are the delivery taxonomy - the vocabulary we use internally to scope and price. In an engagement, the assessment surfaces operational gaps, and each gap maps to one or more categories during solution design:

- A gap like "leads don't get followed up" pulls in **process design** (a written follow-up runbook), **custom internal tools** (an intake form, a follow-up automation), and **operational visibility** (a leads dashboard).
- A gap like "our tools don't talk to each other" pulls in **systems integration**.
- A gap like "I don't know if we're making money" pulls in **operational visibility**, often gated on a **vendor/platform selection** decision and clean books as a precondition.
- **AI & automation** is a named category we reach for only when AI clearly beats a non-AI solution. We do not force an AI angle onto an engagement that does not need one.

The pricing framework carries typical hour ranges per category, used as scoping starting points. See [Pricing & Economics](/admin/playbook/pricing-economics).

## The paid assessment entry point

The assessment is a paid product, the operational expression of "the assessment call is the product." The launch-period first three are free; after that it carries a credited fee, applied against any engagement that follows. The exact figures and credit window are owned by [Pricing & Economics](/admin/playbook/pricing-economics).

Pricing the assessment does two things at once: it filters for owners serious enough to pay, and it asserts that the diagnostic carries value independent of the build. The scheduling rules live in the booking flow.

## What is and is not committed

The engagement is governed by a proposal/SOW that states scope, price, payment terms, and an explicit "what's not included" boundary. A few rules hold across every engagement:

- **Project price, not hourly.** The client sees one number. They never see the hourly rate or the hour estimate. Price is tied to the deliverable, not the time; SMD absorbs overruns up to the agreed scope boundary, and scope creep becomes a separately scoped addition.
- **Payment structure follows engagement size**, from a two-part deposit-and-handoff split on smaller engagements to a three-milestone structure on larger ones. The thresholds and percentages are owned by [Pricing & Economics](/admin/playbook/pricing-economics) (Decision #14).
- **Two-week stabilization is included**, not an open-ended support promise. Work beyond the original scope goes through a separate conversation - a follow-on scope, or conversion to an Operator subscription if the fit is right.

The window is two weeks by decision ([Decision #27](https://github.com/venturecrane/ss-console/blob/main/docs/adr/decision-stack.md)): async through a dedicated email thread, a 24-hour business-day response, covering questions about what we built, minor configuration fixes under 30 minutes, and one additional team-member walkthrough. The SOW template treats two weeks as the default and lets it be adjusted per engagement; the signed SOW is the binding term.

The proposal carries specific timeframes and dollar amounts because it is a signed contract, the product of the assessment conversation, not marketing copy. Public-facing content never carries fixed timeframes or dollar amounts.

## How this maps onto the system

The product motion described here is operated through the admin and client surfaces. A prospect moves through entity stages, an assessment call is captured as a meeting, solution design produces a quote/SOW, signing transitions the engagement into delivery, and billing/portal handle the money and client-facing view. That end-to-end walk-through - and which surface owns each step - is the [Customer Lifecycle](/admin/playbook/customer-lifecycle).

