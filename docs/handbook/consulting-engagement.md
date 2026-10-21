---
title: The Consulting Engagement
section: business
order: 6
owner: captain
review_every: 180
last_reviewed: "2026-10-21"
summary: The consulting offer - one bounded project from assessment to handoff, at one fixed price, where the assessment call is what we really sell.
sources:
  - label: Venture model (engagement phases, the assessment call is the product)
    href: .claude/rules/venture-model.md
  - label: Assessment call script
    href: docs/collateral/assessment-call-script.md
  - label: Pricing framework
    href: docs/collateral/pricing-framework.md
  - label: Proposal / SOW template
    href: docs/collateral/proposal-sow-template.md
  - label: Decision Stack (Decisions #10, #11, #14, #18, #19, #27, #28)
    href: docs/adr/decision-stack.md
---

A consulting engagement is one bounded project: we learn where a business is trying to go, design the right solution, build it, train the team and hand it off, for one fixed price. This page explains how the project runs and what is and is not promised, so you can follow one through the admin console and answer a client's questions about it.

## What it is

Consulting is one of SMD's three offers, beside the Operator (the flagship, a monthly managed worker; see [The Operator Thesis](/admin/playbook/operator-thesis)) and the Hosted Agent ([Business Model](/admin/playbook/business-model)). It is one project, one scope, one price; not a retainer and not a subscription. On the website a prospect reaches it through the assessment, which may end in a recommendation, a project, or an Operator (`docs/marketing/positioning-spine.md`).

The buyer is an owner-led business that has outgrown how it runs today, with real operational load and the ability to pay. There is no revenue-band gate (ADR 0003); we qualify in conversation.

The work draws on the six solution categories: process design, custom internal tools, systems integration, operational visibility, vendor/platform selection, and AI & automation. A single engagement usually combines two or three ([Business Model](/admin/playbook/business-model)).

As of 2026-10-21 SMD's one paying client is an Operator client (`operator/customers/`), and the website's case study comes from that client (`src/pages/case-studies/personal-injury-law-firm.astro`). The consulting process below is built into the admin console and documented in the collateral; whether a consulting engagement is under way at any moment is visible in the admin console under Clients, not recorded here.

## The assessment call is the product

What SMD sells is not configuring a tool; anyone can configure a tool. It is an experienced outsider seeing the operation with fresh eyes, naming the problems the owner is too close to see, ranking them ruthlessly ("these three first, everything else later"), and deciding so the owner does not spend six months researching (`.claude/rules/venture-model.md`). If the assessment is right, the build follows; if it is wrong, no amount of configuration saves the project. That is why the assessment is a paid product after the first three (Decision #13).

The call is a structured conversation, not an audit: opening, objectives, a walk through a normal day, then a summary in the owner's words. Objectives come before problems on purpose. Questions are woven in so the owner puts a number on what the problem costs them; when the owner says the number, it is theirs (Decision #15). The call ends with one commitment, a scope and price within a couple of days. On the call we do not quote a price, commit to a timeline, recommend tools by name, or push to close ([assessment call script](https://github.com/venturecrane/ss-console/blob/main/docs/collateral/assessment-call-script.md)).

## The five phases

Every engagement includes every phase; scope sets how heavy each one is (`.claude/rules/venture-model.md`).

1. **Assessment call.** Walk through their day, "show me how you do X," find the top objectives and the two or three biggest gaps.
2. **Solution design.** Choose the simplest tools, design the workflows, estimate scope and price, and send the proposal within 48 hours of the call (Decision #18), built with the [pricing framework](https://github.com/venturecrane/ss-console/blob/main/docs/collateral/pricing-framework.md).
3. **Implementation.** Build the templates, workflows and documents; configure tools; move data; connect systems. Most of the hours land here.
4. **Training.** Hands-on walkthrough, practice with real data, a written how-to for every process and tool, and an internal champion who can run and troubleshoot it after we leave (Decision #28).
5. **Handoff and polish.** Take feedback, adjust from real use, final handoff. Two weeks of async support follow (Decision #27).

If a proposal gets no answer, the follow-up is three touches over seven days, then the prospect is marked dead (Decision #19).

## How the six categories show up

The categories are not a menu the client picks from; they are how we scope. Each gap the assessment finds maps to one or more:

- "Leads don't get followed up" may need **process design** (a written follow-up routine), **custom internal tools** (an intake form, an automated follow-up) and **operational visibility** (a leads dashboard).
- "Our tools don't talk to each other" is **systems integration**.
- "I don't know if we're making money" is **operational visibility**, often after a **vendor/platform selection**, and only once the books are current (Decision #6).
- **AI & automation** only when AI clearly beats a non-AI answer.

The pricing framework carries typical hour ranges per category as starting points.

## What is and is not promised

The proposal or SOW states scope, price, payment terms and an explicit "what's not included" list ([SOW template](https://github.com/venturecrane/ss-console/blob/main/docs/collateral/proposal-sow-template.md); Decision #10).

- **A project price, not hourly.** The client sees one number, never the rate or the hours. SMD absorbs overruns up to the agreed scope; new scope is quoted separately (pricing framework).
- **Out-of-scope asks go in a parking lot**, logged as they come and settled at the pre-handoff review: folded in, proposed as follow-on work, or dropped with a reason (Decision #11).
- **Payment follows size**: 50/50 by default; at 40 hours or more, consider 40/30/30 (Decision #14; [Pricing & Economics](/admin/playbook/pricing-economics)).
- **Two weeks of included support**, async by email with a business-day response: questions about what we built, small fixes under 30 minutes, one extra walkthrough. The SOW template defaults to two weeks and allows a different window; the signed SOW is what binds (Decision #27).

A signed proposal carries specific timeframes and amounts because it is a contract, not marketing. Public content never does ([Positioning & Voice](/admin/playbook/positioning-voice)).

## Who decides what

The price on a proposal, any discount, and any change to scope or terms are the Captain's. Once a SOW is signed, invoices at the agreed amounts are yours to send or present ([Who Does What](/admin/playbook/who-does-what), [Money](/admin/playbook/money)).

## How it maps onto the admin console

A prospect moves through stages in the admin console; the assessment is a meeting; solution design produces a quote and SOW; signing turns the record into a client with an engagement; billing and the client portal handle money and the client's view. The step-by-step walk, including what happens automatically at signing, is [Customer Lifecycle](/admin/playbook/customer-lifecycle).
