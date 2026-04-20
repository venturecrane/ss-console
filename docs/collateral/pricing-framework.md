---
title: Pricing Framework
sidebar:
  order: 3
---

# Pricing Framework

**Purpose:** Turn assessment-call findings into a project price. Internal reference for scoping and quoting every engagement. Not client-facing. Never shared externally; never attached to a proposal.

**Who uses it:** Anyone scoping a proposal (Scott today, delivery team later). The output is a single project price that goes into `proposal-sow-template.md`.

**Input:** Output of the assessment call — objectives, 2-3 operational gaps, complexity signals, champion identified.

**Output:** One project price, payment structure, and a rough internal hours budget.

---

## Core model

- **We quote project prices, not hourly rates.** The client sees one number. They never see the hourly rate or the hour estimate.
- **The hourly rate is an internal multiplier.** It scales with track record:
  - **Launch rate: $175/hr** (current, through first case study)
  - **$200/hr** — after first case study ships with a referenceable outcome
  - **$250/hr** — after three case studies or when pipeline exceeds capacity
  - **$300/hr** — volume rate once booked 3+ months out
- **Floor:** roughly **$2,500**. Below that, assessment overhead exceeds delivery value. If a scope estimates under $2,500, either bundle more or pass.
- **Ceiling:** none. Large engagements are priced at scope × rate with no cap.
- **Price is a project price, not a time-and-materials estimate.** We absorb overruns up to the agreed scope boundary. Overruns caused by scope creep become separate scoped additions.

---

## Scope estimation — the step-by-step

### 1. Convert findings into deliverables

For each of the 2-3 operational gaps identified on the assessment call, list the specific deliverables required to close the gap.

**Example — gap: "leads don't get followed up"**

Deliverables:

- Single intake form that captures every lead source in one place
- Automated follow-up sequence (first touch within 1 hour, 3-day nudge, 7-day close-out)
- Dashboard view showing open leads by age and source
- Written runbook covering "what happens when a lead comes in"
- Champion trained to operate and adjust the sequence

Deliverables are what the client gets in their hands at handoff. Not process steps, not our internal work.

### 2. Estimate hours per deliverable

Use the hour ranges in [Solution category reference](#solution-category-reference) below. Estimate conservatively — add, don't shave.

Include:

- Design / research / discovery
- Implementation / build
- Testing with real data
- Documentation
- Training and handoff with the champion
- One round of post-handoff revisions

Don't include:

- Sales overhead (assessment call, proposal writing) — priced separately via the $250 assessment fee
- Ongoing support after handoff — that's the retainer

### 3. Apply the rate

Total hours × current rate = project price.

Round to the nearest $250 for amounts under $10k; round to the nearest $500 above $10k. Never quote an odd number (no $4,237.50 — that's $4,250).

### 4. Sanity-check against typical shapes

Compare the total to the engagement shape presets below. If it's wildly off, either the scope is wrong (too much or too little) or the complexity estimate is off. Revisit before sending.

### 5. Decide the payment structure

Per [Decision #18 in the decision stack](../adr/decision-stack.md):

- **Under 40 hours:** 50% deposit at signing, 50% at handoff.
- **40+ hours:** 3-milestone — 40% at signing, 30% at midpoint milestone, 30% at handoff.

The midpoint milestone is defined in the SOW (typically first deliverable complete or halfway through planned calendar time, whichever is earlier).

---

## Solution category reference

Typical hour ranges per solution category. Use as a starting point; every engagement is different.

### 1. Process design

Writing down how something works so it can be handed off, trained, or improved. Often paired with another category — a new process alone is rarely the whole engagement.

| Scope              | Hours | Examples                                                                                                 |
| ------------------ | ----- | -------------------------------------------------------------------------------------------------------- |
| One process        | 4-10  | New-client onboarding checklist. Dispatch handoff runbook. Estimating template.                          |
| Process cluster    | 12-25 | A full workflow (lead → quote → job → invoice) documented with roles, handoffs, and exception paths.     |
| Operating playbook | 25-60 | Multi-role, multi-department manual. What every role does, how they connect, how to onboard someone new. |

### 2. Custom internal tools

Scripts, internal web apps, AI implementations — anything we build that runs on the client's side after we leave.

| Scope                  | Hours  | Examples                                                                                             |
| ---------------------- | ------ | ---------------------------------------------------------------------------------------------------- |
| Targeted script        | 6-15   | A Google Apps Script that syncs one sheet to another. A Zapier workflow with 3-5 steps.              |
| Internal tool (small)  | 20-40  | A single-screen web app — e.g., a custom quote generator. A lead-intake form with CRM write-through. |
| Internal tool (medium) | 40-80  | Multi-screen tool with auth. A purpose-built dashboard. A client-portal MVP.                         |
| Internal tool (larger) | 80-160 | A full custom workflow app with roles, reporting, and integrations.                                  |

AI implementations scale by complexity:

- AI prompt workflow (no custom code): 6-15 hours
- AI agent (tool use, multi-step): 25-50 hours
- Custom AI feature integrated into existing software: 40-100 hours

### 3. Systems integration

Making two or more existing tools talk to each other. The client picks the tools; we wire them up.

| Scope                    | Hours  | Examples                                                                                        |
| ------------------------ | ------ | ----------------------------------------------------------------------------------------------- |
| Single integration       | 4-10   | One-way Zapier between two SaaS tools.                                                          |
| Bi-directional sync      | 10-25  | Two tools kept in sync with conflict handling, via Zapier/Make or a light custom script.        |
| Multi-tool orchestration | 25-60  | 4+ tools in a flow, with error handling, retries, and observability. Usually Make or n8n-level. |
| Custom integration       | 40-120 | Something a no-code platform can't do — direct API calls, webhooks, custom business logic.      |

### 4. Operational visibility

Dashboards, reporting, and the data pipelines behind them. Almost always sits on top of existing tools — we don't replace the source of truth, we surface it.

| Scope                | Hours | Examples                                                                                  |
| -------------------- | ----- | ----------------------------------------------------------------------------------------- |
| Single dashboard     | 8-20  | A one-page view of the 5 numbers that matter. Built on Google Sheets or Looker Studio.    |
| Reporting pipeline   | 20-45 | Pulls from 2-3 tools, cleans data, updates on a schedule. Serves multiple dashboards.     |
| Financial visibility | 30-60 | P&L monthly, AR aging, cash runway. Often requires bookkeeping cleanup as a prerequisite. |

**Precondition: clean books.** If the client's books are more than 30 days behind, financial visibility is out of scope until a bookkeeper catches them up. Note this in the SOW.

### 5. Vendor / platform selection

Helping the client pick the right tool. Sometimes standalone, often a precondition to #1-4.

| Scope                   | Hours | Examples                                                                                  |
| ----------------------- | ----- | ----------------------------------------------------------------------------------------- |
| Short consult           | 2-5   | A meeting + a one-page recommendation. Client owns the purchase.                          |
| Evaluation              | 8-20  | 3-tool shortlist, demo scheduling, pro/con comparison, recommendation memo.               |
| Selection + negotiation | 10-25 | Everything above, plus vendor contract review and pricing negotiation.                    |
| Migration               | 20-80 | Selecting + migrating data + setting up the new tool + training. Merge with #2 as needed. |

### 6. AI & automation

When AI is the right answer. We do AI work only when it clearly beats a non-AI solution. Don't force an AI angle.

| Scope                      | Hours  | Examples                                                                                           |
| -------------------------- | ------ | -------------------------------------------------------------------------------------------------- |
| AI readiness assessment    | 3-8    | Half-day conversation + written memo: what AI can/can't do for this business right now.            |
| AI-first workflow (small)  | 10-25  | Prompt-engineered workflow replacing a manual task (e.g., intake triage, content summarization).   |
| Custom AI agent            | 30-80  | Multi-step agent with tool use — customer-service triage, lead qualification, document extraction. |
| AI-enabled product feature | 40-150 | Production AI feature integrated into the client's own software or customer-facing flow.           |
| Team enablement / training | 5-20   | Workshop + custom prompt library + office hours for a team learning to use AI tools daily.         |

---

## Engagement shape presets

Use these as sanity-checks, not quotes. Every engagement is scoped fresh.

| Shape                       | Hours   | Typical project price at $175/hr |
| --------------------------- | ------- | -------------------------------- |
| Targeted automation / pilot | 15-25   | $2,500-$4,500                    |
| Single-problem engagement   | 30-55   | $5,000-$9,500                    |
| Multi-problem engagement    | 70-130  | $12,000-$22,500                  |
| Platform build / migration  | 140-220 | $24,500-$38,500                  |
| Extended engagement         | 220+    | $38,500+                         |

Rule of thumb: a "three problems identified on the assessment call" engagement is usually the multi-problem shape. Targeted automation is for when the client wants one specific thing fixed fast.

---

## Pricing the assessment call itself

- **First 3 paid assessments: free** (launch-period case-study builders).
- **After that: $250 per assessment**, collected upfront via the booking flow.
- **The $250 is applied as credit** against any engagement that follows within 30 days.
- **If they don't engage**, the $250 is kept. The assessment is the product.

Scheduling is already wired through `/book` with these rules encoded in booking logic.

---

## Retainer structure (post-delivery)

After an engagement handoff, offer (don't push) a monthly retainer:

- **$200/mo — lightweight support.** Async questions, minor tweaks, check-ins every 4-6 weeks.
- **$350/mo — active support.** Up to 2 hours of hands-on work per month, weekly async check-ins.
- **$500/mo — managed support.** Up to 4 hours, bi-weekly calls, priority response.

Retainers are quoted in a separate short document, not in the engagement SOW. Decide the exact package shape after first delivery (we don't have enough post-engagement behavior data to commit a package yet — capture what retainer clients actually ask for and iterate).

---

## Boundary conditions

### Walk away or split when...

- **Scope exceeds capacity without a phase boundary.** If an engagement would need 200+ hours and we have no natural split, it's too big for a single SOW. Propose Phase 1 + a second SOW at phase 1 delivery.
- **Books are more than 30 days behind** and financial visibility is in scope. Require bookkeeping cleanup first; refer to a bookkeeper if needed.
- **The decision-maker isn't signing.** If the proposal goes to someone who has to "run it up the flagpole," hold. Get on a call with the actual decision-maker before quoting.
- **Scope keeps growing during proposal negotiation.** Freeze scope. Offer to scope the additions separately.
- **The client is insisting on tool choices that don't fit the problem.** Pass, or scope narrower. We don't sell configuration for tools we know won't deliver the outcome.
- **Below the $2,500 floor.** Either bundle the scope into something bigger, convert to a paid consultation, or refer out.

### Split into phases when...

- Engagement exceeds ~150 hours total and there's a natural delivery boundary.
- Financial visibility work depends on bookkeeping cleanup happening first.
- Platform migration + process redesign are both needed — migrate first, then redesign on top of the new tool.
- The client wants to see value before committing to the full scope. Phase 1 is a real deliverable, not a discovery phase.

Each phase gets its own SOW and its own deposit + balance structure. Phase 2 isn't contingent on phase 1 — the client can walk after phase 1 if they're not getting value.

---

## What NOT to do

- **Never publish dollar amounts externally.** Landing page, marketing, one-pager, outreach — no numbers. The client sees a price only in their proposal.
- **Never break out the hourly rate in a proposal.** The project price is the project price.
- **Never itemize "hours per deliverable" in the client-facing SOW.** That invites line-item negotiation. Internal doc only.
- **Never scope for free while "thinking about it."** Sending a detailed SOW draft without a signed proposal is free consulting — unpaid scoping is scope creep's first cousin.
- **Never quote on the call.** "We'll design the solution and send you a scope and price within a couple of days" is the only close language (see `assessment-call-script.md`).
- **Never give a ballpark without scope.** If they push ("roughly what are we talking about?"), say: "It depends on what we find. Engagements typically land between $5k and $25k, but we won't know until we design the solution."
- **Never round down under pressure.** The quote is the quote. If they can't afford it, offer a narrower scope, not a discount.

---

## Handling common pricing questions

**"How much does this cost?"** (on the assessment call)

> "Depends on the scope. Most engagements land between $5k and $25k, but we won't know the exact number until we design the solution. You'll have a proposal within a couple of days."

**"Can you do it cheaper?"** (after proposal sent)

> "The price is tied to the scope. We can narrow the scope if the full engagement isn't the right fit right now — let's look at which piece matters most and we can scope a smaller Phase 1."

**"I was expecting something more like $[smaller number]."**

> "Help me understand what you were expecting for that number. We'll either find a narrower scope that lands there, or we'll figure out that it's a bigger job than either of us thought."

**"What if we only get part of the value?"**

> "We absorb that. The price is tied to the deliverable, not the time. If the scope is what we agreed, the price is what we agreed. If scope changes during the work, we flag it and quote the delta separately."

**"Why is it so much for a few weeks of work?"**

> "It's not a few weeks of work — it's 60+ hours of focused work compressed into a few weeks. The compression is part of what you're paying for. A larger firm would bill this at 2-3× and take 3× as long."

---

## Worked example

**Assessment call with a 12-person plumbing outfit. Owner's objectives:**

- Step back from dispatch within 6 months
- Stop losing leads at the intake point
- Know weekly whether the business is profitable

**Operational gaps identified:**

1. Dispatch process lives in owner's head — no runbook, no backup
2. Lead intake fragmented across phone, website, Facebook — no central list, no follow-up discipline
3. Bookkeeper is 45 days behind — no reliable view of margins

**Scope estimate:**

| Gap                            | Deliverables                                                                 | Hours |
| ------------------------------ | ---------------------------------------------------------------------------- | ----- |
| 1. Dispatch process            | Runbook, role definition for dispatcher champion, training sessions          | 18    |
| 2. Lead intake                 | Single intake form, 3-step follow-up automation, leads dashboard, runbook    | 32    |
| 3. Financial visibility        | [Out of scope] — refer bookkeeper to catch up first. Re-scope after cleanup. | —     |
| Training + handoff + revisions | Champion training, written "how-to", one round of post-handoff tweaks        | 10    |

**Total: 60 hours × $175 = $10,500**

Rounded to **$10,500**. Falls in multi-problem engagement shape range. 40+ hours → 3-milestone payment (40 / 30 / 30 = $4,200 / $3,150 / $3,150).

Financial visibility deferred to a Phase 2 SOW after bookkeeping is caught up. Owner is informed verbally on the proposal call, captured in the "What's not included" section of the SOW.

**What the client sees:** a proposal that reads "Project: $10,500" and describes deliverables by problem. Payment structure is "$4,200 at signing, $3,150 at the midpoint milestone (dispatch runbook delivered), $3,150 at final handoff." No hourly breakdown. No reference to the $175 rate.

---

## Related documents

- [Assessment Call Script](./assessment-call-script.md) — the conversation that produces the input to this framework
- [Proposal / SOW Template](./proposal-sow-template.md) — the output document this framework feeds
- [Decision Stack](../adr/decision-stack.md) — the 29 locked decisions behind this pricing model

---

## Versioning notes

- **v1 (2026-04-19):** Initial framework. Rate at $175/hr. Six solution categories sourced from CLAUDE.md's taxonomy.
- Review cadence: after first 3 paid assessments, revisit hour ranges against actuals. Tighten or loosen based on data.
