---
title: The Operator Thesis
section: business
order: 3
owner: captain
review_every: 180
last_reviewed: "2026-10-21"
summary: What the Operator is, what it competes with, why it wins, and how we choose where to sell it.
sources:
  - label: ADR 0037 - The Operator Thesis
    href: docs/adr/0037-operator-thesis.md
  - label: ADR 0040 - Operator positioning and why-us (law-first)
    href: docs/adr/0040-operator-positioning-and-why-us.md
  - label: ADR 0004 - Productized Operator Offering
    href: docs/adr/0004-productized-operator-offering.md
  - label: ADR 0056 - Persona exposure and skill initiation entitlements
    href: docs/adr/0056-persona-exposure-skill-initiation-entitlements.md
  - label: ADR 0071 - The confirm ceiling
    href: docs/adr/0071-confirm-ceiling-and-hosted-agent-tier-ladder.md
  - label: ADR 0022 - Vertical Pack Architecture
    href: docs/adr/0022-vertical-pack-architecture.md
---

The Operator is a managed AI worker that SMD builds for one business and runs for it, doing the recurring work that falls between the business's people and its software. This page is the settled answer to what it is, what it competes with and how we choose markets, so that you, the agents and the Captain describe it the same way.

## What the Operator is

The Operator is SMD's flagship and its monthly product. Each client's Operator runs on its own isolated machine (a "seat"), connects to the systems the client already uses, and does only what the client has authorized (ADR 0004 defines the product; ADR 0037 defines what it is).

ADR 0037 was locked on 2026-06-02 because the description kept drifting between sessions: the Operator described as a fixed email-drafting tool, "it drafts and a human sends" described as its defining feature, and drafting described as its default. None of those is true, and none may be used to describe it.

## It competes with a hire, not with software

This is the most important tenet (ADR 0037, Tenet 1). The Operator's rival is the next person a business would hire: a coordinator, intake lead, paralegal, client-service associate or office manager. Never a software product.

Every system a business runs does part of the work. A person connects them: reads the email, updates the file, chases the document, logs it, books the follow-up. The Operator is that connecting work.

Three things follow:

- The client's existing systems (its case-management system, its email, its calendar) are **things we connect to, not competitors**. An incumbent adding an AI feature is another system to connect across.
- The more disconnected systems a business runs, the **more** an Operator is worth.
- We price against **a salary**, not a software subscription (ADR 0063; see [Pricing & Economics](/admin/playbook/pricing-economics)).

## Why it wins against a hire

1. **Memory that builds up.** Each client's Operator learns the firm and keeps what it learns; the memory belongs to the client (ADR 0008). A new hire starts from zero. See [Knowledge & Memory](/admin/playbook/knowledge-memory).
2. **Limits the client sets.** What the Operator may do is authored for each client, action by action, and enforced in code: whether it may act on its own, must ask first, may only draft, or may not act at all (ADR 0056, with the "ask first" level added by ADR 0071). See [Autonomy & Governance](/admin/playbook/autonomy-governance).
3. **No context-switching.** It does not drop a thread between systems or forget a handoff.
4. **Enterprise discipline at a small-business price.** Code-enforced limits, a record of where every fact came from, and an audit trail, for businesses that have never had them.

These are strengths, not the moat. Calling any single feature "the moat" is a mistake (Tenet 4).

## How we talk about it to a buyer

ADR 0040 turns the thesis into the words we use with law firms, the first market:

- **The line the rest hangs from:** it runs on the firm's own expertise and gets better at that firm over time. The client is the expert; we build and run the worker.
- **The boundary, stated correctly:** the Operator does paralegal and coordinator work, never independent legal judgment, advice or representation. Do not say "we don't do legal work"; that is inaccurate.
- **Do not open with "AI employee."** Lead with the seat and the outcome.
- **The managed service is half the offering:** we build it with the firm, implement it alongside the team, monitor it and support it.

## The six tenets of ADR 0037

Cite the tenet number when you or an agent reason from it.

1. **It competes with a hire, not with software.** Incumbent systems are things we connect to; price against a salary.
2. **It is configurable, not a tool with one use.** Each client's Operator is authored across skills, limits, voice, connections and memory. The only hard limit is whether we can connect to a system.
3. **No imposed defaults.** If something has not been configured, the Operator refuses it. That is a safety property, not what the product is. Ask "what did this client authorize?", never "what does the system assume?"
4. **The moat is the harness plus the guide plus the memory.** Code-enforced limits (the harness), the SMD people who configure it well for a specific business (the guide), and the client's own memory. Never one feature.
5. **Packs make it recognizable.** A pack tailors the Operator to an industry ([ADR 0022](https://github.com/venturecrane/ss-console/blob/main/docs/adr/0022-vertical-pack-architecture.md)); packs share parts, so each one makes the next easier. See [Vertical Packs](/admin/playbook/vertical-packs).
6. **We choose markets by reach and willingness to pay.** Go where the coordinator role is most expensive and needed, the audience cheapest to reach, and the willingness to pay against a salary highest. That is why law firms came first (ADR 0040).

## Where to go from here

- [Operator Platform](/admin/playbook/operator-platform): how it is built (for when an agent's report sends you there).
- [Autonomy & Governance](/admin/playbook/autonomy-governance): how the client's limits work.
- [Business Model](/admin/playbook/business-model): where the Operator sits among SMD's three offers.
