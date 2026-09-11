# SMD Services | Decision Stack - Complete Reference

**Layers 1-6 | Buy Box through Delivery Playbook**

---

## What This Document Is

A complete record of every strategic decision made across 6 layers of the SMD Services go-to-market build. Each decision includes the options considered, the rationale, risks accepted, and downstream impact. This document is the source of truth for all agents, collateral, and operational processes built on top of it.

---

## Quick Reference

|                         |                                                                                                                                                                                      |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Business**            | SMD Services - operations consulting                                                                                                                                                 |
| **Core offering**       | Solutions consulting - scope-based pricing per engagement                                                                                                                            |
| **Engagement length**   | Variable - scoped per engagement based on assessment findings                                                                                                                        |
| **Target market**       | Phoenix metro (Phase 1); owner-led businesses qualified in conversation, not by a revenue gate (ADR 0003). Lead verticals: home services + professional services + contractor/trades |
| **Objective**           | Launch the agency and reach profitability                                                                                                                                            |
| **Rate**                | $175/hr at launch → $200/hr after first case study → $250/hr → $300/hr                                                                                                               |
| **Payment terms**       | 50% deposit at signing, 50% at completion                                                                                                                                            |
| **Assessment**          | Free for first 3 clients, then $250 applied toward engagement                                                                                                                        |
| **Voice standard**      | We / our team throughout. Never I / the consultant.                                                                                                                                  |
| **Decisions locked**    | 38 active decisions across 6 layers (including venture-wide #20 positioning standard), numbered through #56; 3 superseded (#2, #12, #43)                                             |
| **Deliverables queued** | 11 artifacts ready to build                                                                                                                                                          |

---

## Venture-Wide Positioning Standard (#20)

> **Hard rule - applies to all content without exception**
>
> SMD Services is a multi-disciplined consulting team. The principal is the point of entry and client relationship owner. The team is the delivery mechanism. All external-facing and internal content defaults to "we" voice. Never "I" or "the consultant." This is not a solo operator. The agent team is real capacity.

|                    |                                                                                                                 |
| ------------------ | --------------------------------------------------------------------------------------------------------------- |
| **Always use**     | We / our team / we'll fix / our approach / we deliver                                                           |
| **Never use**      | I / the consultant / my approach / I'll fix                                                                     |
| **Scope**          | All client-facing collateral, all issue bodies, all delivery playbook language, all website copy                |
| **Why it matters** | Removes the "what if you're sick" objection. Positions the engagement as a capable agency, not a solo operator. |

---

# Layer 1 - Buy Box and Vertical

All five decisions in this layer are foundational. Layers 2-6 build on top of these. Nothing in this layer should be changed without evaluating the downstream impact across all subsequent layers.

---

## Decision #2 - Revenue-Based Qualification

> **SUPERSEDED 2026-06-03.** Revenue is no longer a qualification gate. The operational layer dropped the $750k-$5M filter in [ADR 0003](0003-lead-gen-pivot-actor-identity.md), and the venture-wide no-revenue-band-anchoring standard retired the band as targeting doctrine. We work with any business that can pay and benefit, qualified in conversation. The original decision is preserved below as the historical record.

**Issue:** smdservices/ss-console #2

**Decision: Revenue-based qualification ($750k-$5M primary)**

Revenue correlates better to budget and operational complexity than headcount. A $4M professional services firm with 6 employees is a different engagement than a $600k landscaping crew with 12.

**Rationale**

- Primary range: $750k-$5M (the PrimePath plateau zone)
- Expansion: $5M-$10M after 5+ completed engagements
- Floor at $750k keeps pipeline accessible at launch
- Employee count becomes a contextual signal, not a gate

**Risks Accepted**

- Wider range means more variability in engagement complexity

---

## Decision #3 - Launch Verticals

**Issue:** smdservices/ss-console #3

**Decision: Home services + professional services as lead verticals, problem-qualified (not vertical-gated)**

**Home Services (plumber, HVAC, electrical, pest control, landscaping)**

- Pain cluster: scheduling chaos, lead leakage, employee retention
- High target density in Phoenix
- Owner is always the buyer, fast decision-making
- Concrete ROI: missed appointments = measurable lost revenue

**Professional Services (accountant, attorney, insurance, consultant)**

- Pain cluster: owner bottleneck, manual communication, pipeline leakage
- Higher willingness to pay
- Strong referral networks - one happy accountant refers three more
- Longer sales cycle - monitor and adjust if pipeline stalls

**Contractor/Trades** added as third vertical after 2-3 home services engagements. Close enough to home services that learnings transfer.

No retail, restaurant, or healthcare in year one.

Any business with qualifying problem signals is eligible regardless of vertical. Vertical expertise deepens with case studies, not by pre-selection.

---

## Decision #6 - Financial Visibility in Core Package

**Issue:** smdservices/ss-console #6

**Decision: In core with hard prerequisite gate - books current within 30 days**

Financial blindness is emotionally resonant. Owners respond to it. But "fix their books" disguised as a bullet point is a 20-hour remediation project that blows margin.

- Books must be current within 30 days at time of assessment - hard gate, no exceptions
- If prerequisite not met: swap the problem for another from the six on the call
- Framing the swap: "We focus on problems we can solve within the engagement scope"
- Refer bookkeeping remediation to a partner - creates a reciprocal relationship

---

## Decision #4 - Disqualification Criteria

**Issue:** smdservices/ss-console #4

**Decision: 4 hard disqualifiers + 5 soft flags**

**Hard Disqualifiers - Automatic No**

1. Not speaking to the owner/decision-maker
2. Scope exceeds a single engagement phase (follow-on phases available, but multi-phase scope needs to be broken into discrete engagements)
3. No tech baseline at all - no email, no internet, no existing tools
4. Business in crisis mode (active layoffs, pending closure)

**Soft Disqualifiers - Yellow Flag, Probe on Call**

1. No internal champion - at least one employee must own the solution post-delivery
2. Books more than 90 days behind - swap the financial problem, not automatic disqualify
3. No willingness to change - diagnosis with no intent to act, pass
4. Revenue below $500k
5. More than 3 decision-makers involved

**Budget Signal Proxy**
Don't ask for revenue directly. Look for: 3+ years in business, consistent payroll, not in crisis mode.

---

## Decision #5 - Ideal Client Profile

**Issue:** smdservices/ss-console #5

**Decision: Synthesis of all Layer 1 decisions**

|                        |                                                                                                                                                                                                                              |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Revenue**            | Not a gate — qualified in conversation, not by a revenue figure (ADR 0003; Decision #2 superseded)                                                                                                                           |
| **Geography**          | Phoenix metro (Phase 1), in-person default for assessments                                                                                                                                                                   |
| **Years in business**  | 3+                                                                                                                                                                                                                           |
| **Verticals**          | Home services, professional services, contractor/trades (problem-qualified, not vertical-gated)                                                                                                                              |
| **Pain profile**       | 2-3 problems surfaced during assessment mapping to the six solution categories in CLAUDE.md (process design, custom internal tools, systems integration, operational visibility, vendor/platform selection, AI & automation) |
| **Psychographics**     | Owner knows something isn't working, will invest to change it, makes decisions without committee                                                                                                                             |
| **Disqualifiers**      | Not owner, scope exceeds single phase, no champion, no tech baseline, in crisis                                                                                                                                              |
| **Where to find them** | BNI, Phoenix Chamber, PHCC, ACCA, ASCPA, accountant referrals, Vistage, EO                                                                                                                                                   |

---

# Layer 2 - Stack and Scope

---

## Decision #9 - Tool Evaluation Framework

**Issue:** smdservices/ss-console #9

**Decision: Rubric-based evaluation with a bias toward keep**

We do not lock a default tool stack at the venture level. The team can configure nearly any tool. The question on every engagement is: work with what the client has, or replace it.

**The Five-Criteria Rubric - All Must Pass**

1. Solves the defined problem
2. Client can maintain it post-handoff without our team
3. Cost-appropriate for a 10-25 person business
4. Team-configurable within the sprint window
5. Client already has it - strong preference to keep if 1-4 pass

> **Default posture:** Bias toward keep. The question on Day 1 is whether what they have passes the rubric - not what we would pick from scratch. Replacement is the exception.

---

## Decision #10 - Scope Boundary Language for SOW

**Issue:** smdservices/ss-console #10

**Decision: Positive scope definition + 4-item exclusion list**

**What's In**
Problem diagnosis, process documentation, tool configuration, one handoff training session, written handoff doc. Scoped to 2-3 problems agreed at assessment.

**What's Out - The Four Hard Exclusions**

1. Bookkeeping remediation or catch-up - books must be current at kickoff
2. Data migration from legacy systems
3. Ground-up product development (consumer apps, SaaS products)
4. Ongoing support beyond the handoff session

Note: custom internal tools, integrations, dashboards, and workflow automation built to solve a diagnosed problem ARE in scope.

---

## Decision #11 - Scope Creep Protocol

**Issue:** smdservices/ss-console #11

**Decision: Parking lot protocol - log everything, resolve at pre-handoff review**

- Day 1: tell the client — everything outside the agreed problems goes into a parking lot doc
- During engagement: team logs every out-of-scope request with a one-line description. No in-the-moment no.
- Pre-handoff review (scheduled as the second-to-last session): each parking lot item gets one of three outcomes — fold into handoff, propose as follow-on, or drop with explanation

> **Why parking lot over change orders:** The parking lot protects scope without saying no in the moment, creates a natural conversation that surfaces follow-on revenue, and demonstrates to the client that nothing got lost. That last point is a referral driver.

---

# Layer 3 - Pricing and Payment

---

## Decision #16 - Pricing Model

**Issue:** smdservices/ss-console #16

**Decision: Scope-based pricing. No published price. Rate-based quoting from assessment findings.**

Every engagement is different. The assessment identifies the problems; the solution design determines the scope; the scope determines the price. No fixed price is published externally.

**Internal Rate Schedule**

| Tier                | Rate    | Trigger to Advance                               |
| ------------------- | ------- | ------------------------------------------------ |
| **Launch**          | $175/hr | Starting rate                                    |
| **First reference** | $200/hr | After first completed engagement with case study |
| **Established**     | $250/hr | Consistent pipeline + 2-3 case studies           |
| **Volume**          | $300/hr | Referral-driven inbound + demonstrated results   |

**How Quoting Works**

1. Assessment call identifies 2-3 problems
2. Solution design phase estimates hours per problem (tool selection, configuration, documentation, training)
3. Quote = estimated hours × current rate
4. Presented as a fixed project price to the client — they see "Operations Cleanup: $X,XXX", not an hourly breakdown
5. Internal tracking compares estimated vs. actual hours per engagement to calibrate future quotes

**Pricing Guardrails**

- Never publish a dollar amount on the website or marketing materials
- Never share the hourly rate with clients — they see a project price
- The assessment call is the pricing conversation — "we'll design a solution and send you a scope and price"
- Engagement range: scoped per engagement. Smallest engagements start around $2,500 at launch rate; below that, assessment overhead exceeds delivery value. Largest have no fixed ceiling. Nothing published externally. See CLAUDE.md solution taxonomy as source of truth for the categories being scoped.

_The value is not the hours. The value is an experienced team that can see the problems the owner can't, make decisions fast, and implement in days. The rate is internal math — the client pays for outcomes._

---

## Decision #14 - Payment Terms

**Issue:** smdservices/ss-console #14

**Decision: 50% deposit at signing. 50% at completion.**

- 50% at contract signing to book the engagement start date
- 50% due at engagement completion — defined as the handoff session
- SOW language specifies the completion milestone clearly so there is no ambiguity

**Why completion, not a mid-engagement checkpoint:**

With variable-length engagements, a fixed day number no longer works. Completion is the natural trigger — the client has seen the work, the handoff session demonstrates value, and the second payment is expected. The deposit protects against cancellation; the completion payment aligns with delivered value.

**For larger engagements (40+ hours):**

Consider a three-milestone structure: 40% deposit / 30% at mid-engagement checkpoint / 30% at completion. Define the mid-point milestone in the SOW based on the specific deliverables.

---

## Decision #13 - Paid Assessment

**Issue:** smdservices/ss-console #13

**Decision: Free for first 3 clients, then $250 applied toward engagement**

- First 3 clients: free assessment. Bottleneck at launch is getting anyone on a call.
- Trigger to flip to paid: first engagement delivered. Once there is something to point to, the $250 is easy to justify.
- Never publicize the free period - internal policy, not a marketing offer.
- The $250 applies in full toward the engagement fee if they proceed.
- Revisit after pipeline is established. Raising to $500 may be appropriate once brand recognition reduces friction.

---

## Decision #15 - ROI Anchor Math

**Issue:** smdservices/ss-console #15

**Decision: Owner does the math. We ask the questions.**

The anchor math is not a PDF we email. It is 1-2 questions per problem type asked during the assessment. When the owner says the number out loud, it is theirs - not ours. That is the close.

| Problem                  | Questions and Anchor                                                                                                                                                         |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Owner bottleneck**     | Hours/week they spend on approvals x their hourly rate. 10 hrs x $150 = $1,500/week constrained.                                                                             |
| **Lead leakage**         | Lost leads/month x average job value. 2 leads x $500 = $1,000/month walking out.                                                                                             |
| **Scheduling chaos**     | Missed appointments/month x cost per. 4 x $200 = $800/month.                                                                                                                 |
| **Manual communication** | Pain opener, not a math closer. Owner recognizes it immediately.                                                                                                             |
| **Financial blindness**  | Pivot to pain framing. Cost is peace of mind, not a recoverable dollar.                                                                                                      |
| **Employee retention**   | "How many people have you lost in the last year? What does it cost to hire and train a replacement?" The owner knows the number — and it's always higher than they expected. |

---

## Decision #12 - Retainer Model — SUPERSEDED 2026-05-13

> **Superseded by Decision #44 / ADR 0004 (Productized Operator Offering).** The undefined "$200-$400/month, define after first delivery" placeholder is retired. SMD's recurring-revenue product is the productized Operator SKU. Post-handoff support for scope-based engagements continues under Decision #27 (two-week async stabilization); customers wanting ongoing support beyond that window are quoted a follow-on scope or converted to an Operator subscription if the fit is right. The decision below is preserved as historical context; do not implement against it.

**Issue:** smdservices/ss-console #12

**Decision: No retainer at launch. Define after first delivery.**

- Internal placeholder: $200-$400/month, single tier, system monitoring + monthly check-in + minor updates
- Trigger: after first engagement, debrief on what the client asked for that we couldn't deliver in the engagement. That list becomes the retainer scope.
- Pre-handoff parking lot items are the retainer product design session - not a pricing exercise done in advance.

> **If asked at pre-handoff review:** "We offer ongoing support - let us put together a simple proposal after we close out the engagement." Do not improvise scope or price on the spot.

---

# Layer 4 - Assessment and Qualification

---

## Decision #17 - Assessment Call Capture

**Issue:** smdservices/ss-console #17

**Decision: MacWhisper Pro for ambient transcription + Claude for structured extraction**

**The Workflow**

1. Assessment call opens on Mac (Zoom, Google Meet, or phone-over-AirPods)
2. MacWhisper Pro runs in the background, capturing system audio - no bot, no announcement to the client
3. Call ends - speaker-separated transcript is ready in MacWhisper
4. Transcript pasted into Claude with the standard extraction prompt (Deliverable #34)
5. Agent outputs: completed capture doc + SOW draft simultaneously
6. Review and approve - total post-call time under 10 minutes

**MacWhisper Pro Notes**

- $69-79 one-time license, fully local, audio never leaves the machine
- Speaker identification included in v12+
- Arizona is a one-party consent state - legally clean for solo-operator use
- Must be running before the call starts - add to pre-call checklist

---

## Decision #18 - Assessment to Proposal Transition

**Issue:** smdservices/ss-console #18

**Decision: Assessment identifies problems. Solution design produces the quote. Proposal out within 48 hours.**

The assessment call is diagnostic, not a closing conversation. Every engagement differs in scope, so the quote requires a solution design phase after the call.

**Post-Assessment Flow**

1. **End of call:** Summarize findings verbally. Set expectation: "We'll put together a solution and scope — you'll have it within a couple days."
2. **Same day:** MacWhisper transcript → Claude extraction → structured capture doc with identified problems, complexity signals, and scope drivers
3. **Solution design (internal, 1-2 hours):** Select tools, estimate hours per problem, identify dependencies and risks. This produces the quote.
4. **Proposal out within 48 hours** of the assessment call. Speed still matters — but the quote is now informed, not guessed.

**SOW Format Requirements**

- 2 pages max — scope section + terms section
- PDF with DocuSign or Dropbox Sign signature fields
- Includes: problems being solved, specific deliverables, timeline estimate, project price, payment terms, exclusions
- No hourly breakdown visible to client — project price only
- Includes a soft engagement slot hold: tentative start date with a 5-day confirmation deadline

**Call Close Language (Revised)**

> "Based on what we've talked through today, we can see a clear path to fixing X, Y, and Z. We'll design the solution and send you a scope and price within a couple of days. Sound good?"

---

## Decision #19 - Follow-up Cadence

**Issue:** smdservices/ss-console #19

**Decision: 3-touch cadence over 7 days, then mark dead**

| Touch           | Content                                                                                                                   |
| --------------- | ------------------------------------------------------------------------------------------------------------------------- |
| **Day 2**       | Confirm receipt. Short, no pressure. "We sent over the scope yesterday - wanted to make sure it landed."                  |
| **Day 5**       | Value add. One specific observation from the call tied to their business. Shows the team was already working the problem. |
| **Day 7**       | Soft deadline. Reference the sprint slot hold. Offer to reschedule. Clean yes or reschedule - no pressure language.       |
| **After Day 7** | Mark dead. Free the mental bandwidth and sprint slot. No drip, no monthly check-in.                                       |

_Exception: if a prospect explicitly says they need more time, extend the Day 7 touch by 5-7 days once. After that, move on._

---

# Layer 5 - Distribution and Pipeline

---

## Decision #21 - Networking Group Strategy

**Issue:** smdservices/ss-console #21

**Decision: BNI + Chambers + Vertical Associations, sequenced by time-to-first-client**

| Priority                  | Channel                                                                                                              |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| **Week 1 (fastest path)** | Accountant/bookkeeper outreach (#22). Does not require joining anything.                                             |
| **Week 1-2**              | Greater Phoenix Chamber + 1-2 sub-regional chambers (Scottsdale, Chandler, Mesa).                                    |
| **Week 2-4**              | Join one BNI chapter in the "business consultant" or "operations consultant" category. Build for 60-90 day flywheel. |
| **Ongoing**               | PHCC (plumbing/HVAC), ACCA (HVAC/air), ASCPA (accountants). One event per vertical in first 60 days.                 |

**How to Show Up at Any Event**

- Lead with a problem, not a title
- Use the vertical-specific one-liner (Deliverable #32)
- The ask is a conversation, not a sale. Goal: book an assessment call.

---

## Decision #22 - Accountant and Bookkeeper Partnership

**Issue:** smdservices/ss-console #22

**Decision: Co-value positioning, no fee, concrete reciprocal referral**

- No fee changes hands in either direction
- Framing: "We fix the operational mess that makes your clients hard to work with"
- The concrete reciprocal: when we disqualify a prospect for dirty books, we make a warm handoff to our partner bookkeeper - that's a real referral in their direction
- Close the loop after every referred engagement: short update to the referring accountant on what we fixed

**Week 1 Outreach**

- Identify 5-10 Phoenix-area bookkeepers and small CPA firms serving 10-25 person businesses
- LinkedIn and direct email. 3-4 sentences max. Introduction, not a pitch.
- Goal: 20-minute call to establish the relationship, not close anything

---

## Decision #23 - Client Referral Incentive

**Issue:** smdservices/ss-console #23

**Decision: No formal incentive. Explicit ask at handoff. 30-day check-in reinforcement.**

**Handoff Ask (Primary)**

> "If you know another owner dealing with the same kind of chaos, we'd genuinely appreciate the introduction. We make it easy for them."

**30-Day Check-in (Secondary)**

> "How's [specific thing we built] holding up? If anyone comes to mind who could use the same kind of help, we'd love the intro."

---

## Decision #24 - Outreach Messaging Per Vertical

**Issue:** smdservices/ss-console #24

**Decision: Vertical-specific message and channel**

**Home Services**

|               |                                                                                               |
| ------------- | --------------------------------------------------------------------------------------------- |
| **One-liner** | We help home services companies stop losing jobs to scheduling chaos and missed follow-ups.   |
| **ROI hook**  | Ask them how many leads they lose in a month and what each job is worth. They'll do the math. |
| **Channels**  | PHCC/ACCA events, BNI chapter, Greater Phoenix Chamber, shared vendor introductions           |

**Professional Services**

|               |                                                                                                                               |
| ------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| **One-liner** | We help professional services firms get the owner out of day-to-day operations so the business can grow.                      |
| **ROI hook**  | Ask them how many hours a week they spend on things only they can approve and what their time is worth. They know the number. |
| **Channels**  | Accountant/bookkeeper referrals, ASCPA events, LinkedIn outreach to firm owners                                               |

**Technology/Integration**

|               |                                                                                                                            |
| ------------- | -------------------------------------------------------------------------------------------------------------------------- |
| **One-liner** | We help growing businesses replace spreadsheet workflows with tools that actually work together.                           |
| **ROI hook**  | Ask them how many hours a week their team spends copying data between systems. The answer is always worse than they think. |
| **Channels**  | BNI, Chamber events, accountant/bookkeeper referrals, LinkedIn                                                             |

---

## Decision #25 - Pipeline Math Model

**Issue:** smdservices/ss-console #25

**Decision: Updated model for higher engagement value. 15-20 warm touches/week. 25-30% close rate.**

At $7,500 average engagement, the $200k annual target requires roughly 27 engagements per year. That works out to 2-3 closes per month.

| Stage                            | Rate   | Rationale                                      |
| -------------------------------- | ------ | ---------------------------------------------- |
| Outreach touch - conversation    | 20%    | Warm networking context                        |
| Conversation - assessment booked | 40%    | Pain is real, ask is low-commitment            |
| Assessment - proposal sent       | 80%    | If they booked, they are interested            |
| Proposal - close                 | 25-30% | Higher price point, fewer but better-fit deals |

**Working Backwards from Steady State (2-3 engagements/month)**

- 3 closes / 28% = **11 proposals needed**
- 11 proposals / 80% = **14 assessments needed**
- 14 assessments / 40% = **35 conversations needed**
- 35 conversations / 20% = **175 outreach touches needed**
- ~15-20 warm outreach touches per week to maintain pipeline

Fewer touches than the original model, but higher quality. The shift from 25 cold-ish touches to 15-20 warm ones reflects the networking and referral channels actually producing leads.

**Content and Inbound Channel**

As case studies and the website go live, inbound leads should supplement outreach. The goal is for inbound to carry 30-40% of the pipeline within 6 months of first case study publication.

**Weekly Pipeline Health Check**

- Healthy: 2+ assessment calls booked/completed per week, at least 1 proposal out at any time
- Anemic: no assessment calls in a given week, proposal out 7+ days with no response

> **The real constraint** is still assessment call capacity. We need roughly 3-4 assessments per week at steady state. That's the bottleneck, not the outreach volume.

---

# Layer 6 - Delivery Playbook

---

## Decision #28 - Internal Champion

**Issue:** smdservices/ss-console #28

**Decision: Identify at assessment. Orient on Day 1. 3-part enablement standard by handoff.**

**At Assessment Call**
Ask: "Who on your team would own this after we finish - someone who'd be the go-to person for the new process?" No credible answer is a soft disqualifier.

**On Day 1**
Orient the champion directly. 15-minute briefing: what we're building, why, and what their role will be at handoff.

**The Enablement Standard - All 3 by Handoff**

1. Can explain why the system was built the way it was - the reasoning, not just the steps
2. Can operate it without our team present
3. Can diagnose and handle the most common failure modes without calling us

**Fallback: Owner as Champion**
Accept it, but bound the role. The handoff doc includes a "Champion Tasks" section listing only the specific recurring actions the owner needs to take. Nothing open-ended.

---

## Decision #27 - Post-Handoff Safety Net

**Issue:** smdservices/ss-console #27

**Decision: 2-week async stabilization period starting at handoff**

**Handoff Language**

> "For the next two weeks, if anything comes up with the systems we built - a question, something behaving unexpectedly, a team member who needs a second walkthrough - reach out and we'll sort it out. After that, any new work goes through a follow-on scope conversation."

**Channel and Terms**

- Dedicated email thread started at handoff - not a phone number
- 24-hour response time, business days only. Set this expectation at handoff.
- Included: questions about systems we built, minor config fixes under 30 minutes, one additional team member walkthrough
- Not included: new problems, changes beyond fixing what's broken, anything scope-adjacent

Out-of-scope requests during the safety net window: _"That's outside what we built in the engagement - happy to put together a quick scope for a follow-on. Want us to take a look?"_

---

## Decision #29 - Client Feedback Collection

**Issue:** smdservices/ss-console #29

**Decision: Verbal at handoff, written survey 30 days post-handoff**

**Handoff Session - Verbal Check-in (5 minutes, end of handoff)**

1. What worked better than you expected?
2. What would you change?
3. Is there anything that felt unclear at handoff?

_Capture responses in a notes doc immediately after - not during. Asking while taking notes kills the conversation._

**30 Days Post-Handoff - Written Survey (4 questions, sent in check-in email)**

1. Are the systems we built still being used day-to-day?
2. What's the most noticeable change in how your team operates? (open text)
3. Any friction points that came up after we left? (open text)
4. Would you refer us to another owner? (Yes / Not yet / No, with optional "who" field)

Question 4 doubles as a referral prompt. Same email, same moment, no extra step.

---

## Decision #26 - Review Request Process

**Issue:** smdservices/ss-console #26

**Decision: Verbal ask at handoff session. Automated email 2 days post-handoff.**

**Handoff Session - Verbal Ask**

> "We'd really appreciate a Google review when you get a chance - we'll send you a direct link so it takes two minutes."

**Post-Handoff Email (2 days after handoff)**

- Google Business review link (primary - shows up in search, highest value)
- LinkedIn recommendation link (secondary - valuable for professional services vertical)
- Framing: "Either one takes about two minutes and means a lot to a small team."
- Ask once clearly. Do not follow up on whether they left a review.

---

## Decision #42 - Taxonomy Two-Layer Model

**Issue:** smdservices/ss-console #591

**ADR:** [docs/adr/0001-taxonomy-two-layer-model.md](./0001-taxonomy-two-layer-model.md)

**Decision: Two taxonomies, deliberately distinct. The 6-category list is the _delivery_ taxonomy (what we offer). The 5-category list is the _observation_ taxonomy (what we detect from public data). Outreach speaks observation; marketing speaks delivery; the assessment call is the bridge.**

**Delivery taxonomy (6 categories - source of truth: CLAUDE.md):**

1. Process design
2. Custom internal tools
3. Systems integration
4. Operational visibility
5. Vendor/platform selection
6. AI & automation

Used on: marketing site, pricing framework, SOWs, Decision Stack language. This is the doctrinal positioning.

**Observation taxonomy (5 IDs - source of truth: `src/portal/assessments/extraction-schema.ts`):**

1. `process_design`
2. `tool_systems`
3. `data_visibility`
4. `customer_pipeline`
5. `team_operations`

Used on: lead-gen prompts, scorecard, assessment intake extraction, entity-signal metadata. This is the operational pain we can detect from public data.

**Why two layers.** The lists were authored by different sessions for different purposes and were never reconciled. Forcing one into the other would degrade both — the lead-gen schema is pain-detection-shaped, the marketing list is engagement-offering-shaped. The credibility gap (a prospect arriving from outreach hearing observation language and seeing delivery language on the site) closes through honest framing, not a forced merge.

**Implementation.**

- Marketing site (`src/components/WhatYouGet.astro`) gains one clarifying paragraph below the delivery list: this is how we deliver, not a checklist we run against the business; the right solution comes from the assessment conversation.
- Lead-gen prompts already use 5-cat exclusively. An integration test asserts this stays true.
- CLAUDE.md "Taxonomy divergence note" updated to reference the ADR and note resolution.

**Captain authorized:** 2026-04-27, lead-gen strategy walkthrough. See [`docs/strategy/lead-gen-strategy-2026-04-25.md`](../strategy/lead-gen-strategy-2026-04-25.md), Diagnosis section item 4 and Decisions Locked table row 1.

---

## Decision #43 - Outside View Unified Diagnostic (cross-layer) — SUPERSEDED 2026-05-04

> **Superseded.** Outside View was retired in PR #702 (user-visible surface) and #703 (infrastructure). Public-footprint scraping turned out not to surface anything useful, and a half-retired state caused a P0 client-portal bug. The decision below is preserved as historical context; do not implement against it.

**Issue:** smdservices/ss-console — Outside View build issues filed 2026-04-27 (PR-A/B/C of Phase 1).

**ADR:** [docs/adr/0002-outside-view-unified-diagnostic.md](./0002-outside-view-unified-diagnostic.md) (superseded)

**Decision: One product, three input depths, one persistent artifact resident in the portal. The Outside View replaces `/scorecard`, `/scan`, and `/get-started` (cold-mode) as three competing lead-magnet products.**

The marketing site grew three lead-magnet surfaces in parallel — `/get-started` ("Tell Us About Your Business" form), `/scorecard` (structured form), and `/scan` (public-footprint diagnostic, shipped 2026-04-27 in PRs #608/#613/#615/#617/#619). The Captain identified the structural redundancy: all three feed the same engine — assess the business, name the gaps, suggest a next step — at different intake fidelities. They should be one product.

**The product.** "Outside View" is doctrine. The verb form on the marketing site ("see what we see") and the noun form for the artifact ("your Outside View") frame the value proposition correctly: an experienced outside observer; what we see when we look at your business; nothing surveillance, nothing invasive, just public footprint plus pattern recognition.

**Three input depths:**

| Depth                   | Commitment                                    | Inputs                                               | Earns               |
| ----------------------- | --------------------------------------------- | ---------------------------------------------------- | ------------------- |
| **D1: Outside view**    | 30 sec (URL + email)                          | Public footprint (web, reviews, GBP, public records) | Offer D2            |
| **D2: Conversation**    | 15 min voice/text with our agent              | D1 inputs + what the owner tells us                  | Offer D3            |
| **D3: Assessment call** | 60 min, $0 for first 3, $250 thereafter (#13) | D1 + D2 + Scott's eyes                               | Engagement proposal |

Same engine, same data model, same artifact shape. Fields fill progressively as depth increases.

**Persistent home in the portal.** The artifact lives at `portal.smd.services/outside-view`, not in an inbox. Magic-link bridges the public form on `smd.services/outside-view` to the portal session. Adds a `prospect` role alongside `client`. The portal grows visibility as the relationship deepens — prospect → client_active → client_inactive — without a data-handoff seam at engagement signing.

**Cross-layer impact.**

- **Layer 1 (Buy Box).** Defines the product the prospect first encounters. Outside View is what they hire when they say yes.
- **Layer 3 (Pricing).** Extends Decision #15 (ROI Anchor Math — "owner does the math, we ask the questions") from the assessment call into the asynchronous artifact. The artifact never quotes a fabricated dollar amount; it provides solvability + fix-shape + cost-shape, and the owner does the financial multiplication against their own numbers. Defends CLAUDE.md no-fabrication rule by construction.
- **Layer 4 (Assessment).** D2 is a re-home of the conversational scorecard rewrite (formerly tracked in #482, now Phase 3 of Outside View). The assessment call (D3) is unchanged in shape but warm-starts from D1+D2 context.
- **Layer 5 (Distribution).** Replaces the three competing lead-magnet products with one. Marketing site primary CTA points at D1 (`/outside-view`). D2/D3 reachable in one click from anywhere.
- **Layer 6 (Delivery).** Portal-as-CRM: admin extensions surface prospect signals, agent triage, daily digest. Inbound (`/outside-view`) and outbound (existing entity-enrichment pipeline) feed the same data model — Scott's admin shows everything we know about an entity in one place.

**Extends Decision #20 (positioning standard).** "Outside View" is the canonical lead-magnet name in copy. "Scorecard," "scan," and "Tell Us About" are retired terms. Voice standard ("we / our team," never "I / the consultant") applies inside the artifact and the conversation.

**v1 simplifications (per critique).** v1 stores the existing `RenderedReport` shape from the shipped /scan pipeline as `outside_views.artifact_json` with versioning at the boundary. Canonical five-field-per-observation contract from ADR 0002 §3 ships in v2 as a forward migration. Phase 1 must not redesign the artifact during a re-aim — anti-fabrication hardening from #617 is preserved.

**Captain authorized:** 2026-04-27, lead-magnet consolidation conversation + /critique 3 review (Devil's Advocate, Simplifier, Pragmatist). See ADR 0002 for full context, depth specifications, artifact contract, and phase plan.

---

## Decision #44 - Productized Operator Offering (cross-layer)

**ADR:** [docs/adr/0004-productized-operator-offering.md](./0004-productized-operator-offering.md)

**Supersedes:** Decision #12 (Retainer Model)

**Decision: Add a productized Operator offering as a second front door alongside the existing scope-based engagement funnel. Flat monthly retainer SKU. Lean Hermes as the agent harness; evaluate everything else independently before adopting any other vendor's stack wholesale. Two front doors, one firm — firm-level voice and solutions-consulting positioning unchanged.**

**Four locks:**

1. **Productize as a flat-rate retainer SKU.** Fixed monthly price, not metered, not credit-based, not scoped per engagement. Specific monthly price deferred to follow-on pending stack cost analysis; the _shape_ of the pricing is locked here.
2. **Second front door, not replacement.** The scope-based assessment funnel (Decisions #16, #18) remains the primary path for prospects whose objectives we need to surface through conversation. Operator is the entry point for prospects who already know they want an agent.
3. **Hermes-leaning stack posture.** Hermes is the leading candidate for the agent harness. Every other component (host/VM, MCP connector layer, email identity, memory layer, build harness) is evaluated independently before adoption. Durable principles: cloud VMs over local hardware, MCP-bridged tooling, agents-building-agents, persistent memory layer, watchdog/observability.
4. **Decision #12 superseded.** The undefined $200-500/mo post-delivery retainer concept is retired. Operator replaces it as SMD's recurring-revenue product. Post-handoff support for scope-based engagements continues under Decision #27 (two-week async stabilization).

**Cross-layer impact.**

- **Layer 1 (Buy Box).** Adds a productized SKU as a second front door. Does not change the collaborative-guide ICP posture (Decision #5); the Operator adds a self-diagnosed entry, and its targets are the vertical packs in `operator/verticals/`, selected on market-driven criteria (ADR 0037).
- **Layer 3 (Pricing).** Adds a flat retainer pricing shape distinct from scope-based quoting (Decision #16, unchanged for the consulting funnel). Specific number deferred.
- **Layer 5 (Distribution).** Adds a second acquisition path. Prospects who arrive knowing they want an agent skip the assessment funnel and convert directly to a productized retainer.
- **Layer 6 (Delivery).** Introduces productized service obligations (uptime, monitoring, customer success cadence) that the firm has not yet had. Stack build follow-on must specify watchdog, observability, and incident-response patterns before the first paid customer.

**Positioning guardrails.**

- Firm-level voice stays solutions consulting. Operator is a named offering within that frame, not a competing identity.
- No "AI-powered firm" branding. Operator is the knife; SMD is the chef.
- Operator copy follows the same anti-fabrication rules (Pattern A / Pattern B in CLAUDE.md). No invented timeframes, deliverables, or commitments.
- No false simplicity. "Unlimited agents" framing common in market practice is rhetorical, not literal. Productized scope language is honest — what the customer gets, what they don't, what triggers a scope conversation.

**Captain authorized:** 2026-05-13, podcast-driven strategic conversation (The Startup Ideas Podcast — "The $1M+ Solo AI Agent Business," Greg Isenberg + Nick Vasilescu, 2026-05-12). See ADR 0004 for full context, decision shape, consequences, and follow-on backlog.

---

## Decision #45 - Entitlement Is Configurable; External Send Is One Authored Option

**ADR:** [docs/adr/0005-external-send-identity.md](./0005-external-send-identity.md) (amended) → [0025](./0025-autonomy-ceilings-configurable-exposure-vs-initiation.md) → [0035](./0035-no-imposed-entitlement-defaults.md)

**Decision: Entitlement is configurable across the full spectrum of the harness — per capability and action-class, on independent axes (exposure, initiation, external send, autonomy). The harness imposes no default posture; it faithfully enforces what an engagement authors. An entitled action with no authored entitlement is fail-closed (refused — no send, no draft).**

Draft-for-review external send (the persona has no external sending identity; drafts go to the approver, who reviews and sends from their own account) is **one configurable option an engagement may author** — valuable for compliance, and pinnable by a regulated-vertical pack as a non-raisable constraint where required. It is **not** a default and **not** an architectural invariant: this decision originally read "architectural, not advisory" / "promotion to autonomous is not available," which ADR 0025 overturned and ADR 0035 finished correcting by removing the residual default-framing. The persona remains fully visible internally regardless of the external exposure configuration.

**Cross-layer impact (Layer 6 - Delivery).** The product expresses its full range — from draft-everything to trusted-autonomous-send — per action class, per customer. Compliance posture is an authored configuration (and a regulated-vertical pin), code-enforced and audited, not an assumed baseline.

**Captain authorized:** 2026-05-20 (original); amended 2026-05-29 (ADR 0025); corrected 2026-06-02 (ADR 0035 — no imposed defaults).

---

## Decision #46 - Capability-Adapter Pattern (Operator architectural invariant)

**ADR:** [docs/adr/0006-capability-adapter-pattern.md](./0006-capability-adapter-pattern.md)

**Decision: Skills bind to abstract capability interfaces. Vendor adapters implement the interfaces. `customer.yaml` declares which adapter implements which capability for each customer. Three-layer separation — capability interface, adapter, wiring — is architectural.**

Eleven capability interfaces: `PracticeManagement`, `Email`, `Calendar`, `DocumentStorage`, `ESign`, `CourtAccess`, `Payments`, `Accounting`, `IntakeCRM`, `CallTracking`, `InternalComms`. Adding a new vendor is one adapter, not new skill variants. Per-customer adapter swap is configuration.

**Cross-layer impact (Layer 6 - Delivery).** Defines the scalability model for vendor coverage in the Operator SKU.

**Captain authorized:** 2026-05-20 per ADR record.

---

## Decision #47 - Per-Customer Machine Isolation (Operator architectural invariant)

**ADR:** [docs/adr/0007-per-customer-machine-isolation.md](./0007-per-customer-machine-isolation.md)

**Decision: One Fly.io Machine per customer. No shared runtime across customers. Multi-tenancy is achieved through deployment isolation, not runtime tenancy.**

Each customer gets dedicated D1, R2, and Vectorize bindings; dedicated OAuth tokens; pinned content-hash SHA of the Hermes runtime. Boot-time invariant verifies the Machine's storage bindings include only its own customer's namespaces. The control plane (SMD's operational layer) is a single multi-tenant application; customer runtime Machines are not.

**Cross-layer impact (Layer 6 - Delivery).** Defines the productized service obligations called out in Decision #44 (uptime, monitoring per-Machine, incident-response patterns).

**Captain authorized:** 2026-05-20 per ADR record.

---

## Decision #48 - Customer-Owned Memory Artifact (Operator architectural invariant)

**ADR:** [docs/adr/0008-customer-owned-memory-artifact.md](./0008-customer-owned-memory-artifact.md)

**Decision: Customer memory (rules, person-mappings, voice samples, corrections, audit log) lives in customer-specific D1, R2, and Vectorize namespaces bound to the customer's Machine. The customer owns the artifact contractually and operationally. The platform supports portable export and verifiable deletion on offboarding.**

The "no lock-in" claim from Decision #44 / ADR 0004 is backed by architecture, not goodwill. GDPR / CCPA / state-privacy-law right-to-export and right-to-erasure map onto platform operations.

**Cross-layer impact (Layer 6 - Delivery).** Defines the data-ownership boundary for Operator customer contracts.

**Captain authorized:** 2026-05-20 per ADR record.

---

## Decision #49 - Cross-Machine Query Prohibition (Operator architectural invariant)

**ADR:** [docs/adr/0009-cross-machine-query-prohibition.md](./0009-cross-machine-query-prohibition.md)

**Decision: No customer's Hermes Machine can query another customer's data, by any mechanism, at any layer. Enforced by (1) boot-time storage-binding check that refuses to start on namespace violation, and (2) a shared-catalog CI merge gate that blocks platform-level catalog entries containing customer-specific content.**

Cross-customer learning is not available as a feature. Platform improvements are SMD-authored from human-readable insights, never derived from runtime data propagation. Pairs with Decisions #47 (Machine isolation) and #48 (memory ownership).

**Cross-layer impact (Layer 6 - Delivery).** Defines the cross-customer perimeter for the Operator SKU; the answer to compliance counsel's "could another customer's data ever inform ours?" question.

**Captain authorized:** 2026-05-20 per ADR record.

---

## Decision #50 - Operator Launch Pricing

**ADR:** [docs/adr/0063-operator-launch-pricing.md](./0063-operator-launch-pricing.md)

**Decision: The Operator launch price is a flat-rate monthly retainer plus a one-time stand-up fee. Internal, never published; the client sees the price in their proposal. The figures live in `venturecrane/engagements:pricing/`.**

Prices at the salary anchor (ADR 0037: compete with a hire — the displaced coordinator seat runs ~$60k/yr loaded), above the Review 5 cost floor (which was ~90% labor and would have been break-even as a price). At the locked MRR the COGS>40% kill criterion trips at a seat cost — a genuine anomaly signal rather than ordinary-month noise. Pilot/dogfood seats carry list price for gate purposes while invoiced at $0.

**Cross-layer impact (Layer 3 - Pricing).** Supersedes ADR 0004's deferred-pricing clause; the retainer _shape_ (flat, not metered) is unchanged. Arms the cost-plane kill gate (ADR 0062).

**Captain authorized:** 2026-07-04 per ADR record.

---

## Decision #51 - Hosted Agent Self-Serve SKU

**ADR:** [docs/adr/0067-hosted-agent-self-serve-sku.md](./0067-hosted-agent-self-serve-sku.md)

**Decision: Launch a second recurring SKU, Hosted Agent — a self-serve $79/month subscription (first 25 founding seats $49/month via a Stripe forever-coupon) for an always-on personal Hermes agent, BYO Anthropic key, concierge-provisioned on the Operator substrate, with published pricing on its own product page.**

Competes with DIY and the commodity hosting floor by design (a deliberate, scoped carve-out from ADR 0037 Tenet 1 — the Operator itself still competes with a hire and its pricing stays internal per Decision #50). Launch channels are constrained to Telegram plus allowlisted-sender email with draft-for-review external sends, mapping onto ADR 0032's deferred public-exposure checklist. Checkout is self-serve; provisioning stays Captain-run behind named automation seams.

**Cross-layer impact (Layer 3 - Pricing, Layer 5 - Distribution).** First published price on any SMD surface (page-scoped exemption recorded in the positioning spine and guard tests); creates the entry rung of the hosted-to-Operator ladder.

**Captain authorized:** 2026-07-06 per session directive (skunkworks operation).

---

## Decision #53 - Compliance Evidence Packet Signs as the Entity

**Spec:** [docs/specs/operator/compliance-evidence-packet.md](../specs/operator/compliance-evidence-packet.md)

**Decision: The compliance evidence packet is signed by SMDurgan, LLC, not by a named individual. A client firm obtains the public half from a stable URL on smd.services.**

The spec as authored hardcoded `name: "Scott Durgan"` / `email: "scott@smd.services"` in the `captain_signature` block. The contracting party on the service agreement is SMDurgan, LLC. A packet whose attesting name differs from the liable entity is a gap opposing counsel notices, and the packet's whole purpose is evidentiary use outside our custody (service agreement §4.5: delivered in full on termination).

Key delivery is a stable published URL rather than the signed agreement or an onboarding handoff, so a firm — or their counsel arriving years later — can verify without contacting us. This is a standing obligation: the URL stays live and the fingerprint stays stable across rotations.

**Not a blocker for the export itself.** The packet self-discloses `signature="unsigned-stub"` (`operator/adapter/evidence/manifest.py:38`) and integrity currently rests on per-artifact SHA-256. The client-facing per-matter export (#2122) ships on that basis; signing is a separate follow-on and must not gate the export path behind key material.

**Cross-layer impact (Layer 6 - Delivery).** Amends the packet spec's signature block. Creates a published-surface commitment on smd.services.

**Captain authorized:** 2026-08-13.

---

## Decision #54 - Two Microsoft Graph App Registrations Required Per Client

**Related:** ADR [0078](./0078-client-custody-email-channel.md) (client-custody email channel), ADR [0010](./0010-per-customer-oauth-token-storage.md) (client-custodied secrets)

**Decision: A client firm on the Microsoft Graph email channel registers two applications in its own tenant — a read-only app for the agent and a send-capable app for the broker — each restricted to the pinned operator mailbox by an Exchange `ApplicationAccessPolicy`. This is required, not preferred.**

A Graph app-only token is always `/.default`, so one registration cannot hold two permission sets. Two apps is therefore the only way this channel gets a vendor-enforced send fence, matching what the AgentMail channel gained after ss#2258. With one app the broker's key is the agent's key: the governed path is fenced by us, but a rogue in-agent path can still mint its own token and transmit.

The provisioner already stages per-customer `MSGRAPH_SEND_TENANT_ID__<CID>` / `MSGRAPH_SEND_CLIENT_ID__<CID>` / `MSGRAPH_SEND_CLIENT_SECRET__<CID>` and needs no code change to use a second app (`operator/bin/provision-customer.sh:735-756`). Its current fallback to the read app's values was authored as a migration affordance; under this decision that fallback becomes a defect and the provisioner refuses rather than warns.

**Cost accepted.** One extra app registration and access policy per client, performed by their IT during stand-up. Onboarding documentation must surface the requirement before stand-up day, not during it.

**Cross-layer impact (Layer 6 - Delivery).** Standing requirement on every future M365 client. Closes the credential-shape class behind ss#2258 on the channel most professional-services firms will use.

**Captain authorized:** 2026-08-13.

---

## Decision #55 - Reply Authorization and Staff Status Are Separate Authored Facts

**Issues:** [#2263](https://github.com/venturecrane/ss-console/issues/2263), [#2271](https://github.com/venturecrane/ss-console/issues/2271), [#2264](https://github.com/venturecrane/ss-console/issues/2264), [#2167](https://github.com/venturecrane/ss-console/issues/2167)

**Decision: "May the Operator reply to you" and "is firm staff" become separate authored facts. Not a validator warning, not a documented convention — separate fields.**

`scope.inbound_allow_from` answers the first question but is also passed as the internal-staff roster to `classify_recipients_typed`, and `_classify_one_typed` returns INTERNAL on an inbound-roster match before consulting the typed roster. The natural way for a firm to enable autonomous replies to its own client is to add that client to that list, which silently reclassifies them as staff and exempts them from the content floor (ADR [0072](./0072-recipient-aware-proactive-send.md) / ss#1932). The firm authored "reply to my client" and got "treat my client as staff." Nothing warns.

The console validator compounds it: `src/lib/operator/customer-yaml/sections-scope.ts:268` rejects any address appearing on both `inbound_allow_from` and a typed `outbound_roster` class, so the typed roster can never say CLIENT for a reply-authorized address. The reply-lane matter gate is therefore unreachable in every configuration a client is permitted to author — a control that cannot fire, not merely one left unconfigured.

**The coupling is mandatory.** Unblocking the matter gate without moving the content floor is a net loss: a reply-authorized client would still classify INTERNAL at the floor, which relaxes it. Per ss#2263 this change moves the content floor, the send ceilings, and the taint gate together. Either they move in one coordinated release or the work stops and reports. ss#2264 rides with it — a party set can only close via `get_matter`, so without a completeness signal on the contact-filtered listing the gate stays a no-op even once reachable.

**Already live, not to be rebuilt.** Overlay#240 (`658169e`, an ancestor of the pinned `ec3fb713`) makes the matter gate's exemption ignore an inbound-roster INTERNAL when the typed roster says CLIENT. The gate is deployed and has never had a configuration that lets it fire.

**Existing configs must not regress.** A&P today is `@ashtonandprice.com` plus `scott@smd.services` with no `outbound_roster` authored, which ss#2263 records as correct for them.

**Cross-layer impact (Layer 6 - Delivery).** Posture change across three safety controls. An ADR follows from the implementing design rather than preceding it.

**Captain authorized:** 2026-08-13.

---

## Decision #56 - The Chronology Package Is the Runner's Product, and the Runner's Gates Are Its Content Control

**Issues:** [#2618](https://github.com/venturecrane/ss-console/issues/2618), [#2611](https://github.com/venturecrane/ss-console/issues/2611), [#2439](https://github.com/venturecrane/ss-console/issues/2439)

**Decision: routine 11 delivers the chronology package the firm has been receiving, built by an SMD-owned runner on the firm's own Machine and filed into the matter through the connector, outside any agent turn. The agent-turn content gates never inspect it; the runner's own claim audit, extractive sweep, cross-client gate, and provenance gate are the registered control. The running chronology memo stays an agent write behind the gates and is reformatted to pass them.**

The August 2026 sprint delivered sixteen chronology packages from a laptop, at about $30 in model spend each after two optimization rounds, and about $800 to $1,600 of the Captain's time each. The margin lever is the Captain's time, not the tokens. The agent-turn gates refused the running memo three times on 2026-08-19 because a chronology inherently carries the amounts, codes, and dates the gates exist to catch; they were never a fit for a 100-page audited document.

**Cross-layer impact (Layer 5 - Delivery, Layer 3 - Pricing).** Exhibit A now defines the two forms and attaches the 2,000-document monthly allowance to the package (engagements #89); the seat authors that one figure and nothing else about the package. Nothing is signed; the wording goes into the signature copies before the firm signs.

**Captain authorized:** 2026-08-27 (option (a)); recorded as ADR 0087, 2026-08-29.

---

## Decision #30 - Case Study Creation

**Issue:** smdservices/ss-console #30

**Decision: Agent-drafted from Day 30 feedback, client-approved, one-page format**

**One-Page Format: Three Sections**

1. **The situation** (2-3 sentences) - named problem, specific context, vertical
2. **What we did** (2-3 sentences) - problems addressed, what changed operationally
3. **The outcome** - client's own words from Day 30 survey, plus any metrics

**Approval Process**

- Agent drafts from assessment capture doc + Day 30 survey
- Email to client: "If it looks right, just reply 'looks good' and we'll use it"
- One follow-up at 5 days if no response. If still no response: file as internal only, do not publish.

**Never Included**

- No client logo or photo without explicit written permission
- No engagement price
- No "about SMD Services" section
- No methodology description

---

# Deliverables Queue

All 11 artifacts are scaffolded as GitHub issues in smdservices/ss-console. Every deliverable is fully specified - the decisions that define it are locked. Ready to build.

| #   | Deliverable                               | Week | Layer                  |
| --- | ----------------------------------------- | ---- | ---------------------- |
| 31  | Accountant and bookkeeper intro email     | 1    | Layer 5 - Distribution |
| 32  | Vertical one-liners for networking events | 1    | Layer 5 - Distribution |
| 33  | LinkedIn profile copy                     | 1    | Layer 5 - Distribution |
| 34  | MacWhisper extraction prompt              | 1    | Layer 4 - Assessment   |
| 35  | SOW template                              | 1    | Layer 4 - Assessment   |
| 36  | Assessment call script with ROI anchors   | 2    | Layer 4 - Assessment   |
| 37  | 3-touch follow-up email sequence          | 2    | Layer 4 - Assessment   |
| 38  | Pre-handoff review and completion script  | 2    | Layer 6 - Delivery     |
| 39  | Handoff document template                 | 3    | Layer 6 - Delivery     |
| 40  | Day 30 feedback survey                    | 3    | Layer 6 - Delivery     |
| 41  | Case study template and agent prompt      | 3    | Layer 6 - Delivery     |

## Build Order Rationale

**Week 1 - Opens the pipeline**

- #31 Accountant intro email: fastest path to a first warm lead
- #32 Vertical one-liners: needed before any networking event
- #33 LinkedIn profile copy: credibility check for every prospect who looks us up
- #34 MacWhisper extraction prompt: needed before the first assessment call
- #35 SOW template: needed before the first close attempt

**Week 2 - Runs the pipeline**

- #36 Assessment call script: structured guide with ROI anchors baked in
- #37 Follow-up email sequence: three ready-to-send templates
- #38 Pre-handoff review script: parking lot review, referral ask, review request

**Week 3+ - Closes the loop**

- #39 Handoff doc template: the client's operating manual post-engagement
- #40 Day 30 feedback survey: captures real-world adoption signal
- #41 Case study template and prompt: turns every engagement into social proof

---

# Appendix - Decision Index

| Issue | Decision                                                                                                                                                                                                                                                    |
| ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| #2    | Revenue-based qualification - SUPERSEDED (ADR 0003); revenue is no longer a gate                                                                                                                                                                            |
| #3    | Launch verticals - home services + professional services + contractor/trades, problem-qualified                                                                                                                                                             |
| #4    | Disqualification criteria - 4 hard stops, 5 soft flags                                                                                                                                                                                                      |
| #5    | Ideal client profile - synthesis                                                                                                                                                                                                                            |
| #6    | Financial visibility - in core with 30-day prerequisite gate                                                                                                                                                                                                |
| #9    | Tool evaluation framework - rubric-based, bias toward keep                                                                                                                                                                                                  |
| #10   | Scope boundary language - positive definition + 4 exclusions                                                                                                                                                                                                |
| #11   | Scope creep protocol - parking lot, pre-handoff review                                                                                                                                                                                                      |
| #12   | Retainer model - SUPERSEDED 2026-05-13 by #44 (see ADR 0004)                                                                                                                                                                                                |
| #13   | Paid assessment - free for first 3, then $250                                                                                                                                                                                                               |
| #14   | Payment terms - 50% deposit at signing, 50% at completion                                                                                                                                                                                                   |
| #15   | ROI anchor math - owner does the math, we ask the questions                                                                                                                                                                                                 |
| #16   | Pricing model - scope-based, $175/hr → $200 → $250 → $300 rate progression                                                                                                                                                                                  |
| #17   | Assessment capture - MacWhisper Pro + Claude extraction                                                                                                                                                                                                     |
| #18   | Assessment to proposal - solution design phase, SOW within 48 hours                                                                                                                                                                                         |
| #19   | Follow-up cadence - 3-touch over 7 days, then mark dead                                                                                                                                                                                                     |
| #20   | Positioning standard - we voice, team framing (venture-wide)                                                                                                                                                                                                |
| #21   | Networking strategy - BNI + chambers + vertical associations                                                                                                                                                                                                |
| #22   | Accountant partnership - co-value, no fee, warm handoff                                                                                                                                                                                                     |
| #23   | Client referral incentive - no formal incentive, ask at handoff                                                                                                                                                                                             |
| #24   | Outreach messaging - vertical-specific message and channel                                                                                                                                                                                                  |
| #25   | Pipeline math - 15-20 touches/week, 2-3 engagements/month, 25-30% close                                                                                                                                                                                     |
| #26   | Review request - verbal at handoff, automated email 2 days later                                                                                                                                                                                            |
| #27   | Safety net - 2-week async from handoff                                                                                                                                                                                                                      |
| #28   | Internal champion - identify at assessment, orient Day 1                                                                                                                                                                                                    |
| #29   | Feedback collection - verbal at handoff, survey 30 days later                                                                                                                                                                                               |
| #30   | Case study workflow - agent-drafted, client-approved, one page                                                                                                                                                                                              |
| #42   | Taxonomy two-layer model - 5-cat observation, 6-cat delivery (see ADR 0001)                                                                                                                                                                                 |
| #43   | Outside View unified diagnostic - one product, three depths, portal-resident artifact (see ADR 0002)                                                                                                                                                        |
| #44   | Productized Operator offering - flat-rate retainer SKU, second front door, Hermes-leaning stack (see ADR 0004)                                                                                                                                              |
| #45   | Entitlement is configurable; draft-for-review external send is one authored option, not a default or invariant (see ADRs 0025, 0035)                                                                                                                        |
| #46   | Capability-adapter pattern - skills bind to capability interfaces; adapters implement; customer.yaml wires (see ADR 0006)                                                                                                                                   |
| #47   | Per-customer Machine isolation - one Fly.io Machine per customer; deployment isolation, not runtime tenancy (see ADR 0007)                                                                                                                                  |
| #48   | Customer-owned memory artifact - per-customer namespaces; portable export; verifiable deletion (see ADR 0008)                                                                                                                                               |
| #49   | Cross-Machine query prohibition - boot-time binding check + shared-catalog merge gate (see ADR 0009)                                                                                                                                                        |
| #50   | Operator launch pricing - flat monthly retainer + stand-up fee, internal, never published (see ADR 0063)                                                                                                                                                    |
| #51   | Hosted Agent self-serve SKU - $79/mo BYO-key personal Hermes agent, 25 founding seats at $49/mo (see ADR 0067)                                                                                                                                              |
| #52   | Repository visibility - ss-console PUBLIC (2026-08-01; going private 07-27 was containment, not policy), hermes-smd-overlay PUBLIC (provisioning depends on it); client material lives in the private venturecrane/engagements repo (see ADR 0081 revision) |
| #53   | Compliance evidence packet signs as SMDurgan, LLC, not a named individual; public key at a stable URL on smd.services; signing never gates the per-matter export                                                                                            |
| #54   | Two Microsoft Graph app registrations required per client - read-only for the agent, send-capable for the broker, each mailbox-restricted; the single-app fallback becomes a provisioning refusal                                                           |
| #55   | Reply authorization and staff status are separate authored facts; the content floor, send ceilings and taint gate move in one coordinated release (#2263, #2271, #2264, #2167)                                                                              |

---

_SMD Services - Decision Stack | Confidential_
