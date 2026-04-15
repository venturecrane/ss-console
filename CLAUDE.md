# CLAUDE.md - SMD Services

This file provides guidance for Claude Code agents working in this repository.

## About This Venture

**SMD Services** is a solutions consulting venture under SMDurgan, LLC. We sell scope-based consulting engagements to growing businesses ($750k-$5M revenue, expanding to $10M). This is NOT a SaaS product. It is a services business.

**Objective:** Launch the venture and reach profitability.

**Core offering:** We work alongside business owners to understand where they're trying to go, figure out what's slowing them down, and build the right solution together. Solutions range from process documentation and tool configuration to custom internal tools, system integrations, and operational dashboards. Engagement length and pricing are scoped per project.

**Geography:** Phoenix-based, in-person default for Phase 1 (first 5 clients), remote-capable.

**Positioning:** The client is the hero, we are the guide. Collaborative, objectives-first. The value is enterprise operational discipline applied to businesses that have never had access to it, delivered at speed and pricing that works for their stage. Not "AI-powered" anything. A chef isn't hired for his knife.

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

### No fabricated client-facing content

Any information displayed to a client (timelines, schedules, deliverables, pricing, deposit terms, guarantees, consultant names, dates, scope language, post-signing promises, first-person sentences about future business behavior) MUST come from data authored for that specific engagement. That means database columns populated by a human-reviewed admin flow, CMS content, or source files explicitly reviewed by Captain.

**Two violation patterns are prohibited:**

- **Pattern A (committed template sentences that imply uncontracted commitments).** Hardcoded sentences in source, even ones that interpolate authored values, that promise specific business behavior the engagement has not contracted. Real examples from the 2026-04-15 audit:
  - `'We'll reach out to schedule kickoff.'` (`src/lib/portal/states.ts:138`)
  - `'Work begins within two weeks of signing.'` (`src/pages/portal/quotes/[id].astro:72`)
  - `'Replies within 1 business day.'` (`src/components/portal/ConsultantBlock.astro:136`)
  - `'A 2-week stabilization period follows the final handoff.'` (`src/lib/pdf/sow-template.tsx:529`)

- **Pattern B (runtime fabrication from non-authoritative fields).** Values rendered from sources never authored as client-facing content: placeholder defaults, parsed or derived text, brief-borrowed copy. Real examples from the audit:
  - The 3-week schedule constant (`'We shadow and observe.'` / `'We redesign together.'` / `'Training and handoff.'`) at `src/pages/portal/quotes/[id].astro:79-83`, stripped by hotfix #378
  - `overview: 'Operations cleanup engagement as discussed during assessment.'` injected into every SOW PDF at `src/pages/api/admin/quotes/[id].ts:110`
  - `contactName: primaryContact?.name ?? 'Business Owner'` at `src/pages/api/admin/quotes/[id].ts:101` (a SOW signed as "Business Owner" is a compliance risk)

**If authored data is missing:** render nothing or an explicit "TBD in SOW" marker. See `docs/style/empty-state-pattern.md`. Never invent plausible content.

**Enforcement.** Violations are P0. Merge gate is `.github/workflows/scope-deferred-todo.yml` (blocks TODO-deferred ACs without the `scope-deferred` label). Issue-close gate is `.github/workflows/unmet-ac-on-close.yml` (reopens issues closed with unchecked ACs).

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

**Applies to marketing content only.** This rule does not apply to signed contractual documents (SOW PDFs, invoices, countersigned agreements). A signed SOW is a contract where specific timeframes are the product of the conversation, not marketing copy. Timeframes in signed documents stay as authored.

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

### Problem Framework

We use a three-layer model to connect research to delivery:

**1. Four root patterns** (internal, research-grounded):

- The founder ceiling
- Invisible operational drag
- Revenue plateau
- Cash flow fragility

**2. Owner-voiced symptoms** (external, what owners actually say):
"I can't step away." "I can't find good people." "Customers slip through the cracks." "I don't know if we're making money." "Everything runs on spreadsheets." "Our systems don't talk to each other." "We've stalled."

These are representative, not exhaustive. The assessment listens for whatever comes up.

**3. Five solution categories** (delivery taxonomy):

- Process design
- Custom internal tools
- Systems integration
- Operational visibility
- Vendor/platform selection

No dollar ranges are attached to solution categories. Pricing comes from scope estimation per engagement.

### Pain Clusters by Vertical

These suggest where to lead the conversation, not which problems to look for. The assessment listens for whatever comes up across the full range of symptoms.

| Business Type                                | Likely Entry Points                                   |
| -------------------------------------------- | ----------------------------------------------------- |
| Home services (plumber, HVAC)                | Scheduling + lead follow-up + employee retention      |
| Professional services (accountant, attorney) | Owner bottleneck + manual communication + pipeline    |
| Retail/salon/spa                             | Scheduling + communication + financial visibility     |
| Contractor/trades                            | Estimating/quoting + scheduling + employee retention  |
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

- **Internal rate:** $175/hr at launch, then $200/hr after first case study, then $250/hr, then $300/hr with volume
- **Engagement range:** $5,000-$15,000+ depending on scope
- **Paid Assessment:** $250, applied toward engagement if they proceed. First 3 assessments free.
- **Retainer (post-delivery):** $200-500/mo for ongoing support and optimization. Model holds but we define the details after the first delivery.
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

- [ ] Assessment call script (structured conversation guide, objectives-first)
- [ ] Proposal/SOW template (what gets sent after the assessment, reflecting full solution range)
- [ ] Pricing framework (scope estimation across all 5 solution categories)
- [ ] One-pager / leave-behind (physical or PDF for networking, guide positioning)

### Priority 2: Go-to-Market

- [ ] Vertical selection for initial targeting (pick ONE vertical to start)
- [ ] Outreach strategy (how to find and reach first 5 prospects; includes Vistage, EO Arizona, local networking)
- [ ] Landing page (smd.services, credibility-focused, guide positioning)
- [ ] Pipeline math (how many conversations to sustain profitability)
- [ ] Phased geographic approach (Phoenix in-person first, remote-capable after proof of model)

### Priority 3: Delivery Readiness

- [ ] Tool and solution matrix (across all 5 solution categories, including custom internal tools and integrations)
- [ ] SOP templates (reusable frameworks filled in per client)
- [ ] Client onboarding checklist (what we need from them before Day 1)
- [ ] Quality checklist templates (reusable across engagements)

### Priority 4: Business Model Refinement

- [x] Payment terms (50% deposit at signing, 50% at completion; 3-milestone for 40+ hr engagements)
- [ ] Paid assessment entry point ($250 applied toward engagement, first 3 free)
- [ ] Recurring retainer model ($200-500/mo, define after first delivery)
- [ ] Client data management system (D1 or similar for assessments, quotes, engagements, invoicing)

## Domain Context

- **Geography:** Phoenix metro (Phase 1, in-person default), remote-capable
- **Target:** $750k-$5M revenue businesses, expanding to $10M. The "too big for one person, too small for a COO" framing still works, but the revenue range replaces employee count as the primary gate.
- **Buyer:** The owner. Sometimes the office manager, but the owner writes the check.
- **Competition:** Traditional consultancies ($15-50k+ engagements, slow), fractional CTOs/COOs (ongoing cost, no bounded deliverable), EOS implementers (framework-locked), managed IT providers (technical only). Nobody does assessment + implementation + handoff as bounded, scope-priced engagements.
- **Referral sources:** Vistage, EO Arizona, fractional CFOs, local networking groups (BNI, chamber of commerce), accountants/bookkeepers, commercial insurance agents, SBA/SCORE

## Tech Stack

- **Website:** Astro on Cloudflare Pages (when built)
- **Domain:** smd.services
- **Language:** TypeScript
- **No product/app planned** — this is a services business. Tech is for marketing and internal tools only.

## Three-Subdomain Architecture

One Astro app, one Cloudflare Pages project, three custom domains. Routing is handled by `src/middleware.ts` — not by separate deployments.

| Host                  | Serves                                   | Auth role |
| --------------------- | ---------------------------------------- | --------- |
| `smd.services`        | Marketing pages                          | Public    |
| `admin.smd.services`  | Admin console (rewritten to `/admin/*`)  | `admin`   |
| `portal.smd.services` | Client portal (rewritten to `/portal/*`) | `client`  |

**How the rewrite works.** The middleware inspects `hostname`. On `admin.smd.services`, paths get `/admin` prepended unless they already start with `/admin`, `/api/admin`, `/auth`, or `/api/auth`. Same pattern for `portal.smd.services`. The admin source files still live under `src/pages/admin/*` — the subdomain is a front door.

**Cookie boundaries.** Session cookies are per-host (no `Domain` attribute). Admin cookies only live on `admin.smd.services`. Client cookies only live on `portal.smd.services`. An admin cookie that lands on the apex (from pre-migration logins) is proactively cleared on next visit.

**Backwards compat.** `smd.services/admin/*` and `smd.services/auth/login` 301 to the admin subdomain — old bookmarks still work.

**Env vars.** `APP_BASE_URL` (marketing, SignWell webhooks), `ADMIN_BASE_URL` (OAuth redirect URI, outbound admin links — strict, no fallback), `PORTAL_BASE_URL` (portal links, falls back to `APP_BASE_URL`). See `src/lib/config/app-url.ts`.

## Local Dev

`.mcp.json` is user-local config (gitignored). Create it in the repo root with at minimum the `crane` MCP entry. It is not checked in.

Subdomain-based routing keys off `hostname.startsWith('admin.')` / `portal.`. At `localhost:4321` neither fires, which is usually fine — just hit `/admin/*` and `/portal/*` paths directly.

**For full-fidelity subdomain testing**, add to `/etc/hosts`:

```
127.0.0.1 admin.localhost
127.0.0.1 portal.localhost
```

Then `http://admin.localhost:4321/` and `http://portal.localhost:4321/` exercise the rewrite. Set matching values in `.dev.vars` (e.g. `ADMIN_BASE_URL=http://admin.localhost:4321`) so outbound-URL builders emit the right origin.

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

| Module              | Key Rule                                      | Fetch for details                          |
| ------------------- | --------------------------------------------- | ------------------------------------------ |
| `secrets.md`        | Verify secret VALUES, not just key existence  | Infisical, vault, API keys                 |
| `content-policy.md` | Never auto-save to VCMS; agents ARE the voice | VCMS tags, storage rules, editorial, style |
| `team-workflow.md`  | All changes through PRs; never push to main   | Full workflow, escalation triggers         |

Fetch with: `crane_doc('global', '<module>')`

## Key Reference

- **Decision Stack:** `docs/adr/decision-stack.md` (29 locked decisions across 6 layers — buy box, scope, pricing, assessment, distribution, delivery. Source of truth for all collateral and processes.)
- **Package 2 Deep Dive:** `~/Desktop/services-package-2-deep-dive.md` (full problem analysis, delivery model, positioning)
- `docs/` — Venture documentation as it develops

---

_Update this file as the venture evolves. This is the primary context for AI agents._
