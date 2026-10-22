---
title: Overview
section: business
order: 1
owner: captain
review_every: 180
last_reviewed: "2026-10-21"
summary: What SMD Services is, what it sells, who buys it, and who runs it. Start here.
sources:
  - label: Positioning spine (firm with flagship)
    href: docs/marketing/positioning-spine.md
  - label: Operator Thesis (ADR 0037)
    href: docs/adr/0037-operator-thesis.md
  - label: Productized Operator offering (ADR 0004)
    href: docs/adr/0004-productized-operator-offering.md
  - label: Hosted Agent self-serve SKU (ADR 0067)
    href: docs/adr/0067-hosted-agent-self-serve-sku.md
  - label: Hosted-agent roles (ADR 0090)
    href: docs/adr/0090-hosted-agent-roles.md
  - label: Retire the automated lead-gen machine (ADR 0060)
    href: docs/adr/0060-retire-automated-lead-gen-machine.md
  - label: Seat definitions (operator/customers)
    href: operator/customers
  - label: Decision Stack
    href: docs/adr/decision-stack.md
---

SMD Services is a small software and AI solutions firm whose flagship product is the Operator, a managed AI worker that does the recurring work between a business's people and its software. This page tells you what SMD sells, who buys it, where the business stands today, and how the work is split between the Captain, you and the AI agents.

## What SMD is

SMD Services is a venture of SMDurgan, LLC. It is a services business, not a software company: every client gets something built and run for them, not a login to a product. On the website the firm describes itself as "a software and AI solutions firm for small businesses" with the Operator as its flagship (`src/pages/index.astro`; the "firm with flagship" structure is locked in `docs/marketing/positioning-spine.md`). The objective is to reach profitability.

## Three things SMD sells

| Offer | What the client gets | How it is priced |
|---|---|---|
| **The Operator** (the flagship) | A managed AI worker built for one business, running on its own isolated machine, working inside the client's own systems under limits the client sets ([The Operator Thesis](/admin/playbook/operator-thesis)) | A flat monthly fee plus a one-time stand-up fee. The figures are internal and never published (ADR 0063, Decision #50) |
| **Consulting** | A bounded project: we learn where the business is trying to go, design the right solution, build it, train the team and hand it off ([The Consulting Engagement](/admin/playbook/consulting-engagement)) | A fixed project price per engagement, quoted after the assessment (Decision #16) |
| **The Hosted Agent** | An always-on personal AI agent run safely by SMD on the same machinery as the Operator. Some are sold as a packaged job called a **role**; the first role is `couples` (ADR 0090) | The one published price, on the `/agent` page, with self-serve checkout (ADR 0067, Decision #51) |

On the website, every path except the `/agent` page leads to one conversation, the assessment, and the assessment leads to whichever of these fits: a recommendation, a project, or an Operator (`docs/marketing/positioning-spine.md`, Captain decision 2026-06-29).

## Where the business stands (2026-10-21)

- **One paying client, an Operator client.** A personal-injury law firm, seat `ashton-price`, started its paid service in the client portal on 2026-09-15 after a pilot (`operator/customers/ashton-price/customer.yaml`, the billing-cycle note). Everything we owe that client is in the [obligation register](/admin/playbook/obligation-register).
- **The other seats.** Two hosted-agent seats are set up as client seats but paid for by SMD while they are proven (ADR 0090). The rest are SMD's own: a proving seat, the pre-production seat and an internal seat. The full list is in [Clients & Commitments](/admin/playbook/clients-and-commitments).
- **Acquisition is founder-run and law-first.** The automated lead-generation machine was retired on 2026-07-01 (ADR 0060); the Operator goes to market through law firms first (ADR 0040). See [Roadmap & Current Phase](/admin/playbook/roadmap).

## Who SMD serves

Established, owner-led businesses with real operational load and the ability to pay. There is no revenue-band gate; we qualify in conversation (ADR 0003; Decision #2 is superseded). The buyer is the owner. For consulting the default geography is Phoenix, in person for the first engagements and remote-capable after that (Decision #5). The Operator's first market is law firms (ADR 0040).

## The thesis in one line

The Operator competes with a hire, not with software (ADR 0037, Tenet 1). The question a buyer asks is not "is this better than my case-management system" but "is this better than hiring another coordinator." What makes it hard to copy is the combination of code-enforced limits, the people at SMD who configure it well, and the client's own memory that builds up over time (Tenet 4).

## Who runs it

The Captain (the founder) owns strategy, pricing, contract terms, new commitments, spending, and taking on or ending a client. You, the administrator, run the business day to day: the daily routine, routine client emails, invoices at prices already set, and directing the agents. AI agents do the technical work and the live operations on client seats, each reporting with proof. Each client decides how far its own Operator may go. The full split is in [Who Does What](/admin/playbook/who-does-what); how the three layers work together is in [Operating Model](/admin/playbook/operating-model).

## Where to go next

- [Start Here](/admin/playbook/start-here): the reading order for your first week.
- [Business Model](/admin/playbook/business-model): the three offers in more depth.
- [The Operator Thesis](/admin/playbook/operator-thesis): what the flagship is and why it wins.
- [Money](/admin/playbook/money): how clients pay.
