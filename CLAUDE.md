# CLAUDE.md - SMD Services

This file provides guidance for Claude Code agents working in this repository.

## About This Venture

**SMD Services** is a services venture under SMDurgan, LLC. It is NOT a SaaS product — it is a consulting business that sells operations cleanup engagements to Phoenix-area small businesses (10–25 employees).

**Objective:** Launch the agency and reach profitability.

**Core offering:** Operations Cleanup — scope-based pricing per engagement. We work with the business owner to understand their objectives, identify what's getting in the way, and build the right solution: process documentation, tool selection, configuration, training, and handoff. Engagement length varies based on scope.

**Positioning:** We help growing businesses figure out what needs to change and build the right solution together. Not "AI-powered" anything. A chef isn't hired for his knife. The value is a collaborative team that works alongside the owner — someone who can see the operation with fresh eyes, make fast decisions on tools and processes, and implement in days what the owner has been meaning to do for months.

## What This Repo Is For

This repo is the operational hub for the SMD Services venture. It holds:

- Business collateral (assessment scripts, proposals, SOW templates, pricing)
- Client delivery templates (SOP templates, checklist frameworks, communication playbooks)
- Marketing materials (landing page copy, outreach templates, case study frameworks)
- The venture website (smd.services) when built
- Process documentation for how we run engagements

This is NOT a product codebase. There is no app to build (yet). The primary output of agent work here is **documents, templates, and strategy** — not code.

## Session Start

Every session must begin with:

1. Call the `crane_preflight` MCP tool (no arguments)
2. Call the `crane_sod` MCP tool with `venture: "ss"`

This creates a session, loads documentation, and establishes handoff context.

## Enterprise Rules

- **All changes through PRs.** Never push directly to main. Branch, PR, CI, QA, merge.
- **Never echo secret values.** Transcripts persist in ~/.claude/ and are sent to API providers.
- **Verify secret VALUES, not just key existence.**
- **Never auto-save to VCMS** without explicit Captain approval.
- **Scope discipline.** Discover additional work mid-task — finish current scope, file a new issue.
- **Escalation triggers.** Credential not found in 2 min, same error 3 times, blocked >30 min — stop and escalate.

## Tone & Positioning Standard

**These rules apply to ALL external-facing content: website copy, outreach, proposals, collateral, and any client-facing language. They also apply to internal content that may inform external copy (e.g., one-liners, scripts).**

### 1. Objectives over problems

Frame engagements around understanding business objectives, not just diagnosing problems. Recognizing a problem is a start, but without knowing the objective — what the business is trying to achieve — we can't truly solve it. The owner often knows the pain but hasn't articulated the goal. Part of our value is helping them discover the real objective through conversation. Don't give them a faster horse.

- **Do:** "We start by understanding where you're trying to go, then figure out what's in the way."
- **Don't:** "We diagnose your top problems and fix them."

### 2. Collaborative, not diagnostic

We are a peer working alongside the owner, not an expert arriving to audit them. The owner has the vision. We have operational experience. We figure it out together.

- **Do:** "We work alongside you," "Let's figure out what needs to change," "together"
- **Don't:** "We audit your operations," "We tell you what to fix," "We come in and pick the things that matter"

### 3. No fixed timeframes in external content

Timeframes are scoped per engagement, just like pricing. Do not publish specific durations for any phase of the engagement — the call, implementation, training, or support. Internally, use timeframes for planning, but never commit them in client-facing content.

- **Do:** "We start with a conversation," "Hands-on training with your team"
- **Don't:** "1-hour call," "10-day sprint," "60-minute training," "2-week support window"

### 4. No published dollar amounts

No dollar amounts appear on the website or in marketing materials. The client sees a project price in their proposal — never on a public page.

### 5. "Solution" not "systems" in marketing contexts

"Systems" sounds scary to business owners — it implies software and one more thing to learn. Not all solutions are software; sometimes it's a better process, a clearer role, or a simpler workflow. Use "solution" in positioning contexts. "System" is fine when referring to a specific literal tool (e.g., "data migration and system setup").

- **Do:** "Build the right solution," "the right solution to get you there"
- **Don't:** "Build better systems," "Your systems should keep up"

### 6. Voice standard (from Decision #20)

Always "we" / "our team." Never "I" / "the consultant." See Decision Stack #20 for full details.

---

## The Business Model

### The 6 Universal SMB Operations Problems

Every 5–50 employee business has 3–4 of these. The assessment call identifies which 2–3 are most acute.

| #   | Problem              | Owner Says                             | What's Actually Broken                                             |
| --- | -------------------- | -------------------------------------- | ------------------------------------------------------------------ |
| 1   | Owner bottleneck     | "I can't take a day off"               | No documented processes — everything lives in the owner's head     |
| 2   | Lead leakage         | "Leads fall through the cracks"        | No CRM, no follow-up system, pipeline runs on memory               |
| 3   | Financial blindness  | "I have no idea if we're making money" | QuickBooks 3 months behind, pricing based on gut feel              |
| 4   | Scheduling chaos     | "Double-bookings happen all the time"  | No centralized scheduling, no automated reminders                  |
| 5   | Manual communication | "I personally text every customer"     | Every message is manual, one-off, no templates or automation       |
| 6   | Team invisibility    | "I don't know what my team is doing"   | No task tracking, no accountability systems, no quality checklists |

### Pain Clusters by Vertical

| Business Type                                | Lead With                                             |
| -------------------------------------------- | ----------------------------------------------------- |
| Home services (plumber, HVAC)                | Scheduling + lead follow-up + team visibility         |
| Professional services (accountant, attorney) | Owner bottleneck + manual communication + pipeline    |
| Retail/salon/spa                             | Scheduling + communication + financial visibility     |
| Contractor/trades                            | Estimating/quoting + scheduling + team accountability |
| Restaurant/food service                      | Team communication + inventory + financial visibility |

### Engagement Phases

| Phase            | Activities                                                                        |
| ---------------- | --------------------------------------------------------------------------------- |
| Assessment call  | Walk through their day, "show me how you do X," identify top 3 pain points        |
| Solution design  | Choose simplest tools, design workflows, estimate scope and price, send proposal  |
| Implementation   | Build templates/workflows/docs, configure tools, migrate data, connect systems    |
| Training         | Hands-on walkthrough, practice, deliver "how to" docs, identify internal champion |
| Handoff + polish | Handle feedback, adjust based on real use, final handoff                          |

### Pricing

- **Operations Cleanup:** Scope-based. Internal rate: $150/hr at launch → $175/hr → $200/hr with volume
- **Paid Assessment (standalone):** $250 — the audit call as a standalone product
- **Retainer (post-delivery):** $200–500/mo for ongoing support and optimization
- **No dollar amounts published externally.** Client sees a project price, not hourly rate.

### The Assessment Call Is the Product

The value is NOT configuring HubSpot. Anyone can do that. The value is:

1. An experienced outsider seeing their operations with fresh eyes
2. Identifying the problems they can't see because they're too close
3. Prioritizing ruthlessly — "these 3 things first, everything else later"
4. Making decisions for them so they don't research for 6 months

## Current Phase: Pre-Launch

We are in the **pre-launch phase**. Nothing has been sold yet. The immediate priorities are:

### Priority 1: Collateral to Start Selling

- [ ] Assessment call script (the structured conversation guide for the audit call)
- [ ] Proposal/SOW template (what gets sent after the assessment call)
- [ ] Pricing framework (scope estimation → hours × rate → project price)
- [ ] One-pager / leave-behind (physical or PDF for networking)

### Priority 2: Go-to-Market

- [ ] Vertical selection for initial targeting (pick ONE vertical to start)
- [ ] Outreach strategy (how to find and reach first 5 prospects)
- [ ] Landing page (smd.services — simple, credibility-focused)
- [ ] Pipeline math (how many conversations → proposals → closes to sustain profitability)

### Priority 3: Delivery Readiness

- [ ] Tool recommendation matrix (for each of the 6 problems, 2–3 tool options by business size/type)
- [ ] SOP templates (reusable frameworks the consultant fills in per client)
- [ ] Client onboarding checklist (what we need from them before Day 1)
- [ ] Quality checklist templates (reusable across engagements)

### Priority 4: Business Model Refinement

- [x] Payment terms — 50% deposit at signing, 50% at completion (3-milestone for 40+ hr engagements)
- [ ] Paid assessment as separate entry point ($250 standalone)
- [ ] Recurring retainer model ($200–500/mo post-delivery)
- [ ] Client data management system (D1 or similar for assessments, quotes, engagements, invoicing)

## Domain Context

- **Geography:** Phoenix, Arizona metro area
- **Target:** 10–25 employee businesses — the "too big for one person, too small for a COO" zone
- **Buyer:** The owner. Sometimes the office manager, but the owner writes the check.
- **Competition:** Generic "business consultants" (vague), Managed IT providers (technical only), Bookkeepers/accountants (financial only). Nobody is doing the full operations audit + implementation in a scoped engagement with fixed project pricing.
- **Referral sources:** Local networking groups (BNI, chamber of commerce), accountants/bookkeepers, commercial insurance agents, SBA/SCORE

## Tech Stack

- **Website:** Astro on Cloudflare Pages (when built)
- **Domain:** smd.services
- **Language:** TypeScript
- **No product/app planned** — this is a services business. Tech is for marketing and internal tools only.

## Build Commands

```bash
npm install             # Install dependencies
npm run dev             # Local dev server
npm run build           # Production build
npm run test            # Run tests
npm run lint            # Run linter
npm run typecheck       # TypeScript validation
npm run verify          # Full verification
npm run format          # Format with Prettier
```

## Instruction Modules

Fetch the relevant module when working in that domain.

| Module              | Key Rule                                      | Fetch for details                             |
| ------------------- | --------------------------------------------- | --------------------------------------------- |
| `secrets.md`        | Verify secret VALUES, not just key existence  | Infisical, vault, API keys                    |
| `content-policy.md` | Never auto-save to VCMS; agents ARE the voice | VCMS tags, storage rules, editorial, style    |
| `team-workflow.md`  | All changes through PRs; never push to main   | Full workflow, QA grades, escalation triggers |

Fetch with: `crane_doc('global', '<module>')`

## Key Reference

- **Decision Stack:** `docs/adr/decision-stack.md` (29 locked decisions across 6 layers — buy box, scope, pricing, assessment, distribution, delivery. Source of truth for all collateral and processes.)
- **Package 2 Deep Dive:** `~/Desktop/services-package-2-deep-dive.md` (full problem analysis, delivery model, positioning)
- `docs/` — Venture documentation as it develops

---

_Update this file as the venture evolves. This is the primary context for AI agents._
