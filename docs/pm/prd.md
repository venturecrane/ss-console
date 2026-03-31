# SMD Services Client Portal - Product Requirements Document

> Synthesized from 1-round, 6-role PRD review process. Generated 2026-03-30.

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Product Vision & Identity](#2-product-vision--identity)
3. [Target Users & Personas](#3-target-users--personas)
4. [Core Problem](#4-core-problem)
5. [Product Principles](#5-product-principles)
6. [Competitive Positioning](#6-competitive-positioning)
7. [MVP User Journey](#7-mvp-user-journey)
8. [MVP Feature Specifications](#8-mvp-feature-specifications)
9. [Information Architecture](#9-information-architecture)
10. [Architecture & Technical Design](#10-architecture--technical-design)
11. [Proposed Data Model](#11-proposed-data-model)
12. [API Surface](#12-api-surface)
13. [Non-Functional Requirements](#13-non-functional-requirements)
14. [Platform-Specific Design Constraints](#14-platform-specific-design-constraints)
15. [Success Metrics & Kill Criteria](#15-success-metrics--kill-criteria)
16. [Risks & Mitigations](#16-risks--mitigations)
17. [Open Decisions / ADRs](#17-open-decisions--adrs)
18. [Phased Development Plan](#18-phased-development-plan)
19. [Glossary](#19-glossary)
20. [Appendix: Resolved Issues](#appendix-resolved-issues)

---

## 1. Executive Summary

SMD Services runs a non-standard consulting lifecycle: assessment call (recorded via MacWhisper) -> transcript extraction via Claude -> scope-based quote -> variable-length engagement -> parking lot review -> invoicing -> post-engagement follow-up. No off-the-shelf platform models this workflow without forcing the business to compromise how it operates.

The product being specified is an internal operations platform and client-facing portal that operationalizes the 29 decisions locked in the Decision Stack (`docs/adr/decision-stack.md`) -- turning strategic choices about how SMD Services sells and delivers into software that enforces those choices by default.

The value is two-sided:

- **For the admin:** It replaces a fragile combination of spreadsheets, email threads, and memory that cannot scale past 2-3 concurrent engagements and does not project the operational maturity the business is selling.
- **For the client:** It delivers the professional experience the engagement promises before a single deliverable is in their hands. As one target client put it: "A $5,000-$9,000 engagement should come with a client experience that matches it. The portal is a trust signal. It either reinforces the price or undermines it. There is no neutral."

The MVP must be live and functional before the first assessment call. That is the forcing function. Everything else sequences from that constraint.

---

## 2. Product Vision & Identity

**Working Name:** Portal (TBD -- a named product is required before productization, but naming is not a Phase 1-5 blocker)

**Permanent URLs:**

- `portal.smd.services` -- client-facing
- `smd.services/admin` -- internal

**Tagline (internal positioning):** The system that runs the engagements the way the Decision Stack says they should run.

**What this is:**

A single-tenant operations platform and client portal for SMD Services. It is internal tooling for a consulting business, built on the same infrastructure stack as the marketing site (Astro + Cloudflare Pages). It is not a product SMD Services sells. It is the operational backbone of how SMD Services delivers what it sells.

**What this is NOT:**

- **Not a generic CRM.** The data model encodes SMD Services-specific concepts (the 6 universal SMB problems, parking lot protocol, internal champion, safety net period, assessment-to-quote pipeline) that no horizontal CRM will model correctly.
- **Not a SaaS product at MVP.** Multi-tenancy design decisions (org_id on every table, data access layer, Stripe Connect readiness) are included to avoid a rewrite if productization becomes real -- but organizational registration, billing for the portal itself, and org-level settings UI are explicitly deferred.
- **Not a replacement for consulting judgment.** The portal enforces process and captures data. The value of the engagement is still the experienced team seeing problems the owner cannot.
- **Not a feature set to gold-plate.** Five phases are specified. MVP is Phases 1-3. Phases 4 and 5 follow from actual engagement delivery, not from speculation.

**Voice standard (binding from Decision #20):** All client-facing portal copy uses "we / our team" throughout. Never "I" or "the consultant." This applies to email templates, portal UI copy, and notification language.

---

## 3. Target Users & Personas

### Persona 1 -- Marcus, the Home Services Owner (Client)

Marcus runs a 14-person HVAC company in Chandler that he built from a one-truck operation twelve years ago. He checks email on his phone between service calls and reviews anything important on his laptop after 7pm. He does not have a personal IT person and the last software he adopted willingly was QuickBooks, which his bookkeeper set up. He agreed to the assessment call because his scheduler quit three months ago and the replacement is struggling.

When Marcus receives the portal invitation email, he is on his phone. He will click the link immediately or not at all -- he does not bookmark things to review later. If the first screen requires him to create a password or navigate through an onboarding flow, he will close the tab. He needs to see his scope and price on the first authenticated screen, with a single clear action.

> "I don't know what 'safety_net' means. I don't know what 'handoff status' means. The portal needs to speak my language." -- Target Customer voice

Marcus's goals in the portal: understand what he's getting, confirm the price, sign, pay the deposit, and check in occasionally to see where things stand.

### Persona 2 -- Rachel, the Professional Services Champion (Secondary Client Contact)

Rachel is the office manager at a 19-person Phoenix insurance brokerage. The owner, David, signed the contract and is the decision maker, but Rachel is the internal champion -- identified during the assessment call as the person who would own the CRM and communication systems post-handoff. She is in her mid-thirties, highly organized, and comfortable with software.

Rachel was added as a second portal contact when the engagement was created. Her use case is different from David's: she wants to track milestones, understand what is being built, and prepare herself to receive the systems at handoff. She will use the document library when handoff docs are uploaded and will review the parking lot disposition. She represents the secondary but important audience that benefits most from the engagement progress view and the document library.

### Persona 3 -- Scott, the Principal / Admin (Internal Operator)

Scott is the sole admin at launch. He is a competent technical user who understands the data model. What he needs is speed and accuracy: the ability to move a prospect through the pipeline without friction, build a quote in under an hour, and know at a glance what requires attention today.

His primary interface is a MacBook in a browser. His mental model of the pipeline is the Decision Stack status progression: prospect -> assessed -> quoted -> active -> completed. The admin UI should mirror that mental model directly.

Highest-stress moments: (1) post-assessment, when he needs to build and send a quote within 48 hours; (2) at pre-handoff review, when he needs to disposition parking lot items and generate a completion invoice; and (3) whenever a follow-up is overdue.

> "The business I'm selling is operational discipline, and I don't have any operational discipline in how I run the business itself. That's the gap this portal is supposed to close." -- Target Customer (Admin Operator)

---

## 4. Core Problem

### For the consulting team

The SMD Services engagement lifecycle is non-standard. No off-the-shelf platform accommodates:

- Variable scope-based pricing derived from structured assessment extraction
- Contact role modeling (owner, decision maker, champion -- sometimes the same person, sometimes three different people)
- Parking lot protocol for scope management with follow-on quote generation
- Estimated vs. actual hours tracking for quote calibration over time
- The MacWhisper -> Claude extraction -> quote builder pipeline

The alternative -- spreadsheets, email threads, and memory -- doesn't scale past 2-3 concurrent engagements and doesn't project the operational maturity the business sells.

> "I had an assessment call last Thursday. I ran MacWhisper, got the transcript, pasted it into Claude, got the extraction output, and then... I copied pieces of it into a Google Doc. That doc is now the 'source of truth' for that prospect. It's not linked to anything." -- Target Customer (Admin Operator)

### For the client

The client experience from assessment through payment reflects the quality of the consulting engagement itself. Currently, proposals would go out as email attachments, invoices as manual PDFs, and document handoff as Google Drive links.

> "I signed it by printing it, signing by hand, scanning it, and emailing it back. That took 20 minutes and I did it at 10pm after my kids went to bed... If the engagement itself is going to be this much friction, I'm starting to wonder if I made the right call." -- Target Customer (Client Perspective)

A polished portal where clients log in, view their custom quote, sign electronically, track progress, pay invoices, and access deliverables communicates professionalism before the engagement starts.

---

## 5. Product Principles

These principles govern tradeoff decisions during build. When two requirements conflict, the higher-ranked principle wins.

1. **The business operates before the software is complete.** The first assessment call cannot wait for Phase 5. Each phase gates a specific business activity. Ship phases in order, ship them complete.

2. **The Decision Stack is the spec.** The 29 locked decisions in `docs/adr/decision-stack.md` are not suggestions. The software encodes them. If a requirement contradicts a locked decision, the decision wins and the requirement is wrong. Examples: pricing is never shown as hourly to clients (Decision #16); follow-up cadence is 3-touch over 7 days then dead (Decision #19); financial visibility has a hard prerequisite gate (Decision #6).

3. **Scope discipline is structural, not cultural.** The parking lot protocol (Decision #11) is not a communication guideline -- it is a database table with a three-option disposition and a pre-handoff review workflow. Scope management must be built into the system so it happens automatically, not remembered.

4. **The client experience reflects the engagement quality.** A client portal that takes 3 clicks to view their quote, sign electronically, and pay their deposit communicates operational maturity before the engagement starts. Every friction point in the client flow is a credibility risk. The client portal is not an internal tool -- it is a sales asset.

5. **No data siloed in the admin's head.** Every piece of engagement information -- assessment extraction output, parking lot items, contact roles, estimated vs. actual hours, follow-up status -- lives in the system. The business cannot scale past 2-3 concurrent engagements without this.

6. **Infrastructure simplicity over architectural elegance.** The stack (Astro + Cloudflare D1/R2/KV) is already deployed for the marketing site. Adding the portal and admin to the same Astro application is the correct call. Adding a second repo, a separate backend service, or a managed database introduces operational overhead the business cannot absorb at launch.

7. **Multi-tenancy is a seam, not a feature.** Every table has org_id. The data access layer enforces it. Nothing else for multi-tenancy is built until productization is a real decision with a real customer.

---

## 6. Competitive Positioning

### Build-vs-Buy Analysis

No off-the-shelf platform models the specific workflow described in this PRD. That is a defensible but not default position -- the build decision needs to be understood as a competitive bet. The following analysis documents the evaluation.

### Direct Competitors

| Platform    | Category                        | Threat Level | Key Gap for SMD Services                                                                     |
| ----------- | ------------------------------- | ------------ | -------------------------------------------------------------------------------------------- |
| HoneyBook   | All-in-one CRM/portal           | Low          | No assessment capture, no variable scope-based pricing, no parking lot                       |
| Dubsado     | CRM + workflow automation       | Low          | No time tracking, no assessment capture, no parking lot                                      |
| Bonsai      | Proposal + contract + invoicing | Low          | Built for hourly billing not scope-based; no assessment module                               |
| SuiteDash   | White-label portal + CRM        | Medium       | Broadest feature set but generic; requires heavy config; no consulting-specific concepts     |
| Copilot.com | Client portal platform          | Medium       | Closest on target market but no assessment/intake, no parking lot, $189-499/mo for API tiers |

### Feature Comparison (MVP Scope)

| Feature                                                        | Off-the-Shelf | SMD Portal |
| -------------------------------------------------------------- | ------------- | ---------- |
| Client management + status pipeline                            | Yes           | Yes        |
| Contact roles (owner / decision_maker / champion)              | No            | Yes        |
| Assessment capture + structured extraction storage             | No            | Yes        |
| MacWhisper -> Claude extraction pipeline                       | No            | Yes        |
| Scope-based quote builder (hours x rate, linked to assessment) | No            | Yes        |
| SOW PDF generation + embedded e-signature                      | Partial       | Yes        |
| Client portal (quote view + signing + invoices)                | Yes           | Yes        |
| Milestone-based payment triggers                               | No            | Yes        |
| Time tracking (actual vs. estimated per engagement)            | Partial       | Yes        |
| Parking lot protocol (log + disposition + follow-on quotes)    | No            | Yes        |
| Multi-tenancy foundation (org_id)                              | Partial       | Yes        |
| Monthly cost at 5 active engagements                           | $35-189       | ~$5        |

### Where Differentiation Is Genuine

1. **Assessment capture as a first-class data object.** No competing platform models an assessment call as a structured record with transcript, extraction JSON, mapped problems, complexity signals, and disqualification flags.
2. **Parking lot protocol with disposition tracking.** A proprietary delivery methodology (Decision #11) with structured data workflow and follow-on quote generation.
3. **Contact role model per engagement.** Owner / decision_maker / champion as distinct roles -- meaningful for the internal champion protocol (Decision #28).
4. **Quote accuracy intelligence loop.** Estimated vs. actual hours aggregated across problem types and verticals to calibrate future quoting.

### Where Differentiation Is Weaker

The client portal experience (quote view, e-signature, invoice, document library) is table stakes -- every competitor has it. The portal experience meets parity expectations but should not be framed as a competitive advantage. The differentiation is the consulting methodology encoded in the data model, not the client-facing UI.

### Critical Competitive Risk

The most dangerous competitor is not Copilot.com at $189/month -- it is SuiteDash at $19/month with unlimited-user flat pricing. A principal who does not want to build can configure SuiteDash in a weekend and cover 60-70% of requirements. The build decision is defensible only if: (a) the remaining 30-40% (assessment capture, parking lot, quote accuracy loop) delivers enough operational leverage to justify build time, or (b) productization is a genuine near-term ambition.

**Recommendation:** A formal build-vs-buy evaluation note should document that SuiteDash and Copilot.com were evaluated and found insufficient for specific named reasons.

---

## 7. MVP User Journey

### Client Journey: First Touch to Active Engagement

**Step 1 -- Portal invitation email (Resend)**
Client receives an email from `team@smd.services` with subject line referencing their business name and a single CTA button: "View Your Proposal." No navigation, no secondary links, no marketing copy. The magic link embedded in the CTA is the only action available.

**Step 2 -- Magic link verification**
Clicking the link hits the auth verification route. If the token is valid and unexpired, the user is immediately issued a session and redirected to the client dashboard. There is no intermediate "you are now logged in" screen. If the token is expired, the user lands on a one-field form: enter email, receive a new link. The expired token error state must be friendly and actionable.

**Step 3 -- Client dashboard (first authenticated view)**
The first thing the client sees is the proposal: their business name, the scope summary (2-3 problems being addressed in plain language), the project price, the payment structure (50% deposit, 50% at completion), and a "Review & Sign" primary button. On mobile, the price and the button are above the fold with no scrolling required. A secondary section below shows "What happens next" -- a three-step explainer: sign the proposal, pay the deposit, work begins.

**Step 4 -- Quote detail view**
Clicking "Review Full Scope" opens the full line-item breakdown by problem (described without jargon), engagement timeline, exclusions list, and SOW summary. No hourly rate or per-item pricing is visible -- only the total project price and payment structure.

**Step 5 -- E-signature (embedded SignWell iframe)**
The "Review & Sign" button transitions to the signing view. On desktop, the SignWell iframe occupies the main content area with the scope summary in a sidebar. On mobile, the iframe is full-width with a collapsible scope summary above. After signing, the client returns to the dashboard with a confirmation banner: "Proposal signed. Your deposit invoice is ready below."

**Step 6 -- Invoice view and payment**
The dashboard shows the deposit invoice with amount, due date, and a "Pay Now" button linking to the Stripe-hosted invoice page in a new tab. On return to the portal, the invoice status reflects "Paid" and a new section appears: "Your Engagement."

**Step 7 -- Active engagement dashboard**
Once the engagement is active (deposit paid), the dashboard shows: current status, current milestone with description and due date, timeline of upcoming milestones, and progress indicator. This is what the client checks periodically during the engagement. It requires no action -- purely informational. Milestones must be written in plain language: "Your scheduling system is set up and live," not "Phase 2: Implementation."

**Step 8 -- Document library (at handoff)**
Handoff documents appear as a flat list of files with names, upload dates, and download links. The signed SOW is always present from the moment it is generated.

**Step 9 -- Completion invoice and safety net**
At handoff, the completion invoice appears. Same pay flow as the deposit. The engagement then transitions to Safety Net status -- displayed in human language with the safety net end date and a "Questions? Email us" link.

### Admin Journey: Quote Build to Proposal Sent

**Step 1 -- Pipeline view (starting point)**
Default landing screen: all clients organized by status column (Prospect, Assessed, Quoted, Active, Completed). Each card shows: business name, vertical, days in current status, next action required. Cards with overdue follow-ups or quote expiration approaching are visually flagged.

**Step 2 -- Client record**
Clicking a client card opens: contact list, source attribution, assessment history, quote history, engagement history.

**Step 3 -- Assessment record**
Shows: scheduled/completed date, transcript upload status, structured extraction JSON rendered in readable format -- identified problems mapped to the 6 universal SMB problems, disqualification flags, champion name, complexity signals.

**Step 4 -- Quote builder**
Line items pre-populate from assessment extraction. Admin refines descriptions, sets estimated hours per item, reviews auto-calculated project price (hours x current rate). Configures payment structure (default 50/50). Previews SOW before generating PDF.

**Step 5 -- SOW generation and send**
Admin clicks "Generate SOW" -- PDF generated via Forme, stored in R2. Reviews in preview pane. "Send to Client" triggers: portal account creation, invitation email via Resend, quote status update to "sent," and automatic scheduling of the 3-touch follow-up cadence.

---

## 8. MVP Feature Specifications

### 8.1 User Stories (Phases 1-3)

#### Client Management

**US-001: Create a Prospect Record** (Phase 1)
As the admin, I want to create a new client record immediately after scheduling an assessment call, so that I have a place to track the prospect before any work begins.

Acceptance Criteria:

- Given the admin is logged in, when they submit the create-client form with business name, vertical, employee count, years in business, and source, then a client record is created with status "prospect" and appears in the pipeline view.
- Given required fields are missing (business_name is required), the system rejects the submission with a field-level error.
- Given `employee_count` is provided, it must be a positive integer between 1 and 999.

**US-002: Progress a Client Through Pipeline Stages** (Phase 1)
As the admin, I want to manually advance a client's status through the pipeline, so that the pipeline dashboard always reflects current reality.

Acceptance Criteria:

- Status transitions follow a directed graph: `prospect -> assessed -> quoted -> active -> completed` (forward chain). `dead` is reachable from any non-terminal status. `completed` and `dead` are terminal.
- When a client's status is set to "dead," all associated open quotes are marked "expired" or "declined" and all scheduled follow-ups are marked "skipped."

**US-003: Manage Contacts for a Client** (Phase 1)
As the admin, I want to add multiple contacts and assign each a role (owner, decision_maker, champion) per engagement.

Acceptance Criteria:

- Only one contact per engagement may hold `is_primary = 1`.
- A contact's email is optional at creation but required before a portal invitation can be sent.

#### Assessment Capture

**US-004: Create an Assessment Record** (Phase 1)
As the admin, I want to create an assessment record linked to a client and upload the MacWhisper transcript.

Acceptance Criteria:

- File stored in R2, `transcript_path` populated with R2 key and retrievable via admin interface.
- When a new assessment is created, client status advances to "assessed" if it was previously "prospect."

**US-005: Record Structured Assessment Extraction** (Phase 1)
As the admin, I want to paste the Claude extraction output into the assessment as structured data.

Acceptance Criteria:

- `extraction` field stores valid JSON with: `problems`, `complexity_signals`, `champion_candidate`, and `disqualification_flags`.
- Each `problems` value must be one of: `owner_bottleneck`, `lead_leakage`, `financial_blindness`, `scheduling_chaos`, `manual_communication`, `team_invisibility`.
- Invalid problem identifiers are rejected with a validation error.
- Identified problems are available to be selected as line items in the quote builder.

**US-006: Mark an Assessment as Disqualified** (Phase 1)
As the admin, I want to mark an assessment as disqualified with the specific disqualifier, so I can accurately track pipeline conversion rates.

Acceptance Criteria:

- Disqualified assessments cannot be converted to a quote.
- Marking an assessment as disqualified does not automatically set client status to "dead" -- the admin makes this determination.

#### Quote Builder

**US-007: Build a Quote from Assessment Findings** (Phase 2)
As the admin, I want to create a quote linked to a client and assessment, add line items for each problem being addressed with estimated hours, and have the system calculate the project price automatically.

Acceptance Criteria:

- `total_hours` = sum of all line item `estimated_hours`, `total_price` = `total_hours x rate` -- both computed by the system.
- The rate is frozen at quote creation time from the org settings.
- A quote must be linked to a completed (not disqualified) assessment.

**US-008: Configure Payment Structure on a Quote** (Phase 2)
As the admin, I want to configure the deposit percentage and milestone payment splits on a quote.

Acceptance Criteria:

- Default `deposit_pct` is 0.5 (50%). For engagements >= 40 hours, a 40/30/30 split is supported.
- `deposit_amount` = `total_price x deposit_pct`, always system-computed.
- `deposit_pct` between 0.01 and 1.00 inclusive.

**US-009: Generate and Send a SOW PDF** (Phase 2)
As the admin, I want to generate a SOW PDF and send it to the client via the portal.

Acceptance Criteria:

- PDF generated via Forme, stored in R2, `sow_path` populated.
- SOW contains: problems being solved, deliverables, project price (no hourly breakdown), payment terms, exclusions -- per Decision #18.
- Send action dispatches Resend email to primary contact with portal magic link.
- If SOW has not been regenerated since the last quote edit, send is blocked with: "The SOW needs to be regenerated -- your changes haven't been saved to the PDF yet."

#### Client Portal -- Quote & Signing

**US-010: Receive Portal Invitation and Access the Portal** (Phase 2)
As a client, I want to receive a branded email with a magic link and land on my portal dashboard without having to set a password.

Acceptance Criteria:

- Magic link token is single-use (marked `used_at` after first consumption).
- Expired tokens redirect to a recovery page with a single email input.
- Unauthenticated portal access redirects to the magic link request page.

**US-011: View and Sign the SOW** (Phase 2)
As a client, I want to view the full scope details and project price and sign the SOW electronically without leaving the portal.

Acceptance Criteria:

- Quote scope and project price visible on dashboard with "Review & Sign" action.
- SignWell iframe presents the SOW for signature within the portal.
- On `document.completed` webhook: quote status -> "accepted", signed PDF stored in R2.

#### Invoicing

**US-012: Automatically Create Deposit Invoice on Signing** (Phase 3)
As the system, when the SOW is signed, I create and send a deposit invoice via Stripe.

Acceptance Criteria:

- Deposit invoice created with `type = "deposit"`, `amount = deposit_amount`.
- Stripe invoice created via API with correct amount and payment methods.
- Duplicate `document.completed` webhooks do not create duplicate invoices.

**US-013: Client Pays Deposit via Stripe** (Phase 3)
As a client, I want to see my deposit invoice in the portal and pay it via Stripe.

Acceptance Criteria:

- "Pay Now" links to Stripe hosted invoice page.
- `invoice.paid` webhook updates status and triggers engagement activation.
- Overdue invoices show a warning indicator; "Pay Now" remains available.

**US-014: SOW Signature Triggers Engagement Creation** (Phase 3)
As the system, I update the quote, create the engagement record, and create the deposit invoice as an atomic operation on `document.completed`.

Acceptance Criteria:

- Quote status -> "accepted", engagement created (status: "scheduled"), deposit invoice created -- all in a single transaction.
- Invalid webhook signatures rejected with HTTP 401.
- Duplicate webhooks return HTTP 200 without creating duplicate records.

**US-015: Stripe Payment Activates Engagement** (Phase 3)
As the system, when `invoice.paid` fires for a deposit invoice, I activate the engagement.

Acceptance Criteria:

- Invoice status -> "paid", engagement status -> "active".
- Non-deposit `invoice.paid` events update invoice status only.

**US-016: Admin Login** (Phase 1)
As the admin, I want to log in with email + password or magic link.

Acceptance Criteria:

- Session created in KV, HttpOnly + Secure + SameSite=Lax cookie set.
- Invalid credentials produce a generic error (no email/password enumeration).
- Client sessions do not grant admin access.

### 8.2 Business Rules

All business rules are non-negotiable constraints derived from the Decision Stack.

**Client & Pipeline:**

- BR-001: `business_name` is required on client records.
- BR-002: `employee_count` outside 10-25 range triggers a soft warning (outside buy box), not a hard block.
- BR-003: Client `source` is required at creation for pipeline math accuracy.
- BR-004: Status transitions follow a directed graph. `completed` and `dead` are terminal.
- BR-005: Setting a client to "dead" expires open quotes and skips scheduled follow-ups.

**Assessment:**

- BR-009: Assessment must be linked to an existing client.
- BR-010: Transcript R2 keys stored in D1, never raw URLs. Presigned URLs generated on demand.
- BR-011: `problems` field accepts only the 6 canonical identifiers.
- BR-012: `financial_blindness` requires evaluation of the "books 30+ days behind" disqualifier (Decision #6). System surfaces a soft warning + admin confirmation.
- BR-013: Maximum 3 problems for engagement scope. Soft warning, no hard block.

**Pricing & Quote:**

- BR-016: Rate frozen at quote creation time. Future rate changes do not alter existing quotes.
- BR-017: `total_price = total_hours x rate` -- always system-computed, never manually entered.
- BR-018: Client-facing views show only the project price. Rate, total hours, and per-item hours are never exposed to the client.
- BR-019: A quote requires a completed (not disqualified) assessment.
- BR-020: Send action blocked if no primary contact has a valid email.

**Payment:**

- BR-021: Default 50/50 split. 40/30/30 structure available for 40+ hour engagements (Decision #14).
- BR-022: `deposit_amount` is always system-computed.
- BR-033: Deposit invoice auto-created on SOW signing webhook -- no manual admin trigger.
- BR-035: Engagement activation requires deposit `invoice.paid` webhook. Manual override available with notes field for offline payments.
- BR-036: ACH default, card fallback (Decision Log #6).

**Authentication:**

- BR-027: Client portal authentication is exclusively via magic link.
- BR-028: Magic link tokens are single-use.
- BR-029: Magic link tokens expire after 15 minutes (security standard; sessions are long-lived at 7 days).
- BR-030: Client portal is scoped to a single client's data only.
- BR-031: `org_id` filter is mandatory on all D1 queries via the data access layer.

**Webhook:**

- BR-034: Both SignWell and Stripe webhook signatures verified on every request.
- BR-037: Webhook handlers are idempotent. Duplicate delivery produces no duplicate records.
- BR-038: SOW signing webhook state changes (quote update + engagement creation + invoice creation) must succeed or fail as a unit.

**Engagement:**

- BR-039: Engagement lifecycle: `scheduled -> active -> handoff -> safety_net -> completed`. `cancelled` reachable from any non-terminal status.
- BR-040: Safety net end date = `handoff_date + 14 calendar days`, system-computed (Decision #27).
- BR-041: Time entries only against engagements in "active," "handoff," or "safety_net" status.
- BR-042: Hourly rate never appears in client-facing views, SOW PDFs, email templates, or client API responses.

### 8.3 Edge Cases

**EC-001: No Primary Contact at Quote Send.** Send is blocked with error: "No primary contact with an email address."

**EC-002: Magic Link Expired.** System explains the link expired and prompts to request a new one via email input.

**EC-003: Magic Link Clicked Twice.** Second click rejected; prompt to request a new link.

**EC-004: Duplicate SignWell Webhook.** Handler detects quote already "accepted," returns 200, no duplicate records.

**EC-005: Duplicate Stripe Webhook.** Handler detects invoice already "paid," returns 200, no duplicate state changes.

**EC-006: Rate Changes Between Draft and Send.** Quote retains the rate frozen at creation. Admin must create a new quote to re-price.

**EC-007: Financial Problem Flagged but Books Behind.** System surfaces warning when admin includes `financial_blindness` in quote line items with dirty-books disqualifier set. Admin must confirm or substitute.

**EC-008: Client Attempts to Pay Already-Paid Invoice.** Stripe handles natively -- hosted page shows invoice as paid.

**EC-009: SOW Signed but Stripe Invoice Creation Fails.** Webhook handler returns non-2xx, prompting retry. Admin notified if failures persist. System must not leave engagement active without an invoice.

**EC-010: Quote Sent to Wrong Email.** Admin corrects contact email and re-sends, triggering new magic link generation.

**EC-011: Assessment Without Identified Problems.** Assessment can be saved with empty problems array, but a quote cannot be created without at least one problem/line item.

**EC-012: Unauthorized Portal Access.** Middleware redirects to magic link request page. No client data visible.

**EC-013: R2 Upload Failure.** Upload fails gracefully. No partial path stored. Admin can retry.

**EC-014: Three-Milestone Mid-Point Invoice Timing.** In MVP, mid-point invoice created manually by admin. Automation deferred to Phase 4.

---

## 9. Information Architecture

### Admin Interface: Screen Inventory

**Global Navigation (persistent left sidebar or top nav, desktop):**
Pipeline (default landing) | Clients (list + search) | Follow-ups (cadence dashboard) | Reports (quote accuracy, pipeline conversion, revenue)

| Route                                  | Content                                                                                                                                                                                         |
| -------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/admin`                               | Pipeline view: status columns (Prospect / Assessed / Quoted / Active / Completed), client cards with business name + vertical + days in status + next action, overdue flags, quick-add prospect |
| `/admin/clients`                       | Search, filter by vertical/status/source, sortable table                                                                                                                                        |
| `/admin/clients/[id]`                  | Business profile, status badge + history, contacts list with add/edit, assessments list, quotes list, engagements list, notes                                                                   |
| `/admin/clients/[id]/assessments/[id]` | Scheduled/completed date, transcript upload, extraction output panel (problems, signals, champion, disqualifiers), status selector, notes                                                       |
| `/admin/clients/[id]/quotes/new`       | Client + assessment selector, line item editor (problem label, description, estimated hours), rate display, calculated totals, payment structure config, SOW preview, Generate/Send buttons     |
| `/admin/engagements/[id]`              | Client name + status, milestone timeline, time entry log, estimated vs. actual hours, contact roles, parking lot panel, invoices panel, follow-up cadence panel                                 |
| `/admin/follow-ups`                    | Overdue (flagged, sorted by days overdue), due today, upcoming (next 7 days), completed. Each: client name, type, date, one-click complete with notes                                           |
| `/admin/reports`                       | Pipeline funnel, quote accuracy, revenue, follow-up compliance rate                                                                                                                             |

### Client Portal: Screen Inventory

**Navigation:**

- Mobile: bottom tab bar (Dashboard, Documents, Invoices, Progress) -- tabs visible only when section has content
- Desktop: left sidebar with same items, business name at top, "Need help?" email link at bottom
- No account settings, notification center, or profile page in MVP

| Route                      | States                                                                                                                                                                                                                                                                                                        |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/portal/dashboard`        | **Pre-signature:** scope summary, project price, "Review & Sign" CTA, "What happens next" explainer. **Post-signature/pre-payment:** signed confirmation, deposit invoice card. **Active engagement:** status badge, current milestone, progress bar. **Safety net:** complete banner, end date, contact info |
| `/portal/quotes/[id]`      | Scope summary, line items (problem + description only -- no hours/rate), timeline, payment structure, exclusions, Review & Sign CTA                                                                                                                                                                           |
| `/portal/quotes/[id]/sign` | Scope reminder sidebar (collapsible on mobile), SignWell iframe (full-width mobile), progress indicator, post-signature confirmation                                                                                                                                                                          |
| `/portal/invoices`         | Invoice list with type, amount, status badge, due date, "Pay Now" button linking to Stripe. ACH recommended note                                                                                                                                                                                              |
| `/portal/documents`        | Flat file list with name, type, date, download button (presigned R2 URL). Empty state: "Documents will appear here as your engagement progresses."                                                                                                                                                            |
| `/portal/engagement`       | Status banner, milestone timeline, parking lot section (visible post-handoff review, Phase 5 scope)                                                                                                                                                                                                           |

### Interaction Patterns

**Magic Link Authentication:** Token valid -> immediate redirect (no intermediate screen). Token expired -> friendly recovery form. Token used -> same recovery flow. Returning authenticated user -> direct dashboard redirect.

**Quote Review and E-Signature:** SignWell iframe loads in main content. On mobile (< 768px), signing view is full-screen. Completion event replaces iframe with confirmation panel and "View Invoice" link. Error/unavailability shows fallback with email contact.

**Invoice Payment Flow:** "Pay Now" opens Stripe hosted invoice in new tab. Return state: "Processing" for up to 60 seconds pending webhook. No accusatory language for overdue invoices.

**Quote Builder Line Item Editor:** Assessment extraction pre-populates rows. Each row: problem label dropdown, description text input, hours numeric input. Tab between fields, Enter to add row, Delete on empty row to remove. Totals update in real time. Draft/Sent/Accepted states with appropriate editability.

**Pipeline Status Transitions:** Explicit admin actions, except webhook-driven automation (signing -> accepted, deposit paid -> active). Dead is always available with required reason. Dead records hidden by default with "Show dead" toggle.

**Follow-Up Cadence:** Quote sent auto-creates Day 2/5/7 follow-ups. Complete, skip-with-reason, and reschedule (Day 7 only, per Decision #19 exception) actions. Overdue at 24 hours past scheduled time, surfaced at top of dashboard and on pipeline card.

---

## 10. Architecture & Technical Design

### System Boundary Diagram

```
+------------------------------------------------------------------+
|                    Cloudflare Pages Project                        |
|                        (ss-console)                               |
|                                                                   |
|  +------------------+  +-------------------+  +-----------------+ |
|  |  smd.services    |  | smd.services/admin|  |portal.smd.serv. | |
|  |  (public pages)  |  | (admin interface) |  |(client portal)  | |
|  |  No auth         |  | Admin session     |  | Client session  | |
|  +------------------+  +-------------------+  +-----------------+ |
|           |                    |                     |            |
|           +--------------------+---------------------+            |
|                                |                                  |
|              +-----------------v------------------+               |
|              |        Astro SSR + Middleware        |               |
|              |  (auth check, org_id injection,      |               |
|              |   role gating on every request)      |               |
|              +-------------------------------------+               |
|                                |                                  |
|         +----------------------+------------------+               |
|         |                      |                  |               |
|  +------v------+   +-----------v-----+   +-------v------+        |
|  | Cloudflare  |   |  Cloudflare R2  |   | Cloudflare   |        |
|  |     D1      |   |  (documents,    |   |  Workers KV  |        |
|  |  (all data) |   |   transcripts)  |   |  (sessions)  |        |
|  +-------------+   +-----------------+   +--------------+        |
+------------------------------------------------------------------+
         |                    |
         v                    v
+-----------------+  +---------------------------------------------+
| External APIs   |  |  Webhooks inbound (POST /api/webhooks/*)     |
| - Stripe        |  |  - Stripe: invoice.paid, invoice.overdue,    |
| - SignWell      |  |           invoice.payment_failed              |
| - Resend        |  |  - SignWell: document.completed               |
| - Claude API    |  +---------------------------------------------+
|   (Phase 5)     |
+-----------------+
```

### Layered Architecture

Four distinct layers. No page component may call D1 directly.

```
+------------------------------------------+
|  Presentation Layer                       |
|  Astro pages (.astro), React islands      |
|  /admin/*, /portal/*, /auth/*             |
+------------------------------------------+
|  API Layer                                |
|  Astro API routes (/pages/api/*)          |
|  Webhook handlers                         |
|  Input validation (Zod schemas)           |
+------------------------------------------+
|  Service Layer (src/lib/)                 |
|  auth/, stripe/, signwell/, email/,       |
|  pdf/, storage/                           |
|  Business logic lives here only           |
+------------------------------------------+
|  Data Access Layer (src/lib/db/)          |
|  Typed D1 query functions                 |
|  Mandatory org_id injection               |
|  No raw SQL outside this layer            |
+------------------------------------------+
```

### Technology Stack

| Layer              | Technology            | Purpose                                                 |
| ------------------ | --------------------- | ------------------------------------------------------- |
| **Framework**      | Astro 5+ (SSR mode)   | Server-rendered pages, API routes, middleware           |
| **Hosting**        | Cloudflare Pages      | Static assets + Pages Functions (Workers)               |
| **Database**       | Cloudflare D1         | All structured data (SQLite at the edge)                |
| **File Storage**   | Cloudflare R2         | Documents (SOWs, transcripts, handoff docs)             |
| **Sessions**       | Cloudflare Workers KV | Fast session lookup for auth middleware                 |
| **Payments**       | Stripe API            | Invoicing, payment collection, webhook events           |
| **E-Signatures**   | SignWell API          | SOW signing, embedded iframe, webhook events            |
| **Email**          | Resend                | Transactional email (invitations, follow-ups, invoices) |
| **PDF Generation** | Forme (WASM)          | SOW PDF generation at the edge                          |
| **Language**       | TypeScript            | End to end                                              |

### Key Design Decisions

1. **Single Cloudflare Pages project, one Astro app.** Admin and portal are route groups within the same project. Simplifies CI/CD, eliminates cross-origin concerns, keeps D1/R2/KV bindings in a single `wrangler.toml`.

2. **Middleware as the auth enforcement point.** `src/middleware.ts` runs before every request, reads session from KV, injects `locals.user` and `locals.orgId`. Individual page components do not perform auth checks.

3. **Data access layer enforces `org_id`.** Every D1 query function accepts `orgId` as a required first argument. No bypass path. This is the multi-tenancy seam.

4. **Webhook handlers are idempotent.** Each handler checks current record state before applying a transition. A second delivery of `invoice.paid` when the invoice is already `paid` is a no-op.

5. **Magic links are single-use with hard expiry.** Token marked `used_at` on first consumption. Default expiry: 15 minutes. Sessions created from magic links are long-lived (7 days, renewable on activity).

6. **R2 documents are never served directly.** All access through presigned URLs (1-hour TTL) generated in `src/lib/storage/`. No R2 bucket is public.

### Application Structure

```
ss-console/
  src/
    middleware.ts             # Auth checks, session validation, role gating
    lib/
      db/                     # D1 query layer (typed queries, migrations)
      auth/                   # Session management, magic links
      stripe/                 # Stripe API client, webhook handlers
      signwell/               # SignWell API client, webhook handlers
      email/                  # Resend client, email templates
      pdf/                    # Forme templates for SOWs
      storage/                # R2 operations, presigned URL generation
    pages/
      api/                    # API routes (webhooks, CRUD operations)
      admin/                  # Admin pages (pipeline, quotes, engagements)
      portal/                 # Client portal pages (dashboard, documents)
      auth/                   # Login, magic link verification
    components/               # Shared UI components
  migrations/                 # D1 schema migrations
  templates/                  # PDF templates (SOW)
  wrangler.toml               # Cloudflare bindings (D1, R2, KV)
  astro.config.mjs
```

### Webhook Architecture

```
SignWell -> POST /api/webhooks/signwell
  document.completed:
    1. Look up quote by signwell_doc_id
    2. If already accepted -> return 200 (idempotent)
    3. Download signed PDF, upload to R2
    4. Update quote: status = accepted, signed_sow_path set
    5. Create engagement record (status: scheduled)
    6. Create deposit invoice
    7. Send deposit invoice + confirmation email

Stripe -> POST /api/webhooks/stripe
  invoice.paid:
    1. Look up invoice by stripe_invoice_id
    2. If already paid -> return 200 (idempotent)
    3. Update invoice: status = paid, paid_at, payment_method
    4. If deposit: engagement status -> active, send confirmation
    5. If completion: trigger safety_net transition
  invoice.payment_failed:
    1. Log failure, send admin notification
  invoice.overdue:
    1. Update status = overdue, send admin notification
```

---

## 11. Proposed Data Model

Migration order: `organizations` -> `clients` -> `users` -> `contacts` -> `assessments` -> `quotes` -> `engagements` -> `milestones` -> `engagement_contacts` -> `parking_lot` -> `invoices` -> `follow_ups` -> `time_entries` -> `magic_links`.

All primary keys use ULID (Universally Unique Lexicographically Sortable Identifier) -- time-sortable, URL-safe, no sequential record count leakage.

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ORGANIZATIONS
CREATE TABLE organizations (
  id              TEXT PRIMARY KEY,          -- ULID
  name            TEXT NOT NULL,
  slug            TEXT NOT NULL UNIQUE,
  domain          TEXT,
  stripe_account  TEXT,
  branding        TEXT,                      -- JSON: {logo_url, primary_color, secondary_color}
  settings        TEXT,                      -- JSON: {current_rate, deposit_pct_default}
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- CLIENTS
CREATE TABLE clients (
  id                TEXT PRIMARY KEY,
  org_id            TEXT NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
  business_name     TEXT NOT NULL,
  vertical          TEXT NOT NULL CHECK (vertical IN (
                      'home_services', 'professional_services',
                      'contractor_trades', 'retail_salon', 'restaurant', 'other'
                    )),
  employee_count    INTEGER CHECK (employee_count > 0),
  years_in_business INTEGER CHECK (years_in_business >= 0),
  source            TEXT CHECK (source IN (
                      'bni', 'chamber', 'referral_accountant', 'referral_client',
                      'website', 'linkedin', 'cold_outreach', 'other'
                    )),
  referred_by       TEXT,
  status            TEXT NOT NULL DEFAULT 'prospect' CHECK (status IN (
                      'prospect', 'assessed', 'quoted', 'active', 'completed', 'dead'
                    )),
  notes             TEXT,
  created_at        TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_clients_org_status ON clients (org_id, status);
CREATE INDEX idx_clients_org_vertical ON clients (org_id, vertical);

-- USERS (admin and client portal accounts)
CREATE TABLE users (
  id            TEXT PRIMARY KEY,
  org_id        TEXT NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
  email         TEXT NOT NULL,
  name          TEXT NOT NULL,
  role          TEXT NOT NULL CHECK (role IN ('admin', 'client')),
  client_id     TEXT REFERENCES clients(id) ON DELETE SET NULL,
  password_hash TEXT,                        -- bcrypt; admin only, NULL for clients
  last_login_at TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (org_id, email)
);
CREATE INDEX idx_users_email ON users (email);

-- CONTACTS
CREATE TABLE contacts (
  id          TEXT PRIMARY KEY,
  org_id      TEXT NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
  client_id   TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  email       TEXT,
  phone       TEXT,
  title       TEXT,
  notes       TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_contacts_org_client ON contacts (org_id, client_id);

-- ASSESSMENTS
CREATE TABLE assessments (
  id               TEXT PRIMARY KEY,
  org_id           TEXT NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
  client_id        TEXT NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
  scheduled_at     TEXT,
  completed_at     TEXT,
  duration_minutes INTEGER CHECK (duration_minutes > 0),
  transcript_path  TEXT,                     -- R2 object key
  extraction       TEXT,                     -- JSON: full Claude extraction response
  problems         TEXT,                     -- JSON array: up to 3 from canonical set
  disqualifiers    TEXT,                     -- JSON: {hard: [], soft: []}
  champion_name    TEXT,
  champion_role    TEXT,
  status           TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN (
                     'scheduled', 'completed', 'disqualified', 'converted'
                   )),
  notes            TEXT,
  created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_assessments_org_client ON assessments (org_id, client_id);
CREATE INDEX idx_assessments_org_status ON assessments (org_id, status);

-- QUOTES
CREATE TABLE quotes (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
  client_id       TEXT NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
  assessment_id   TEXT NOT NULL REFERENCES assessments(id) ON DELETE RESTRICT,
  version         INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  parent_quote_id TEXT REFERENCES quotes(id) ON DELETE SET NULL,
  line_items      TEXT NOT NULL,             -- JSON: [{problem, description, estimated_hours}]
  total_hours     REAL NOT NULL CHECK (total_hours > 0),
  rate            REAL NOT NULL CHECK (rate > 0),
  total_price     REAL NOT NULL CHECK (total_price > 0),
  deposit_pct     REAL NOT NULL DEFAULT 0.5 CHECK (deposit_pct > 0 AND deposit_pct <= 1),
  deposit_amount  REAL,
  status          TEXT NOT NULL DEFAULT 'draft' CHECK (status IN (
                    'draft', 'sent', 'accepted', 'declined', 'expired', 'superseded'
                  )),
  sent_at         TEXT,
  expires_at      TEXT,
  accepted_at     TEXT,
  sow_path        TEXT,                      -- R2 key: unsigned SOW PDF
  signed_sow_path TEXT,                      -- R2 key: signed SOW PDF
  signwell_doc_id TEXT,
  notes           TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_quotes_org_client ON quotes (org_id, client_id);
CREATE INDEX idx_quotes_org_status ON quotes (org_id, status);
CREATE INDEX idx_quotes_signwell ON quotes (signwell_doc_id) WHERE signwell_doc_id IS NOT NULL;

-- ENGAGEMENTS
CREATE TABLE engagements (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
  client_id       TEXT NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
  quote_id        TEXT NOT NULL UNIQUE REFERENCES quotes(id) ON DELETE RESTRICT,
  scope_summary   TEXT,
  start_date      TEXT,
  estimated_end   TEXT,
  actual_end      TEXT,
  handoff_date    TEXT,
  safety_net_end  TEXT,
  status          TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN (
                    'scheduled', 'active', 'handoff', 'safety_net',
                    'completed', 'cancelled'
                  )),
  estimated_hours REAL CHECK (estimated_hours > 0),
  actual_hours    REAL NOT NULL DEFAULT 0 CHECK (actual_hours >= 0),
  notes           TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_engagements_org_client ON engagements (org_id, client_id);
CREATE INDEX idx_engagements_org_status ON engagements (org_id, status);

-- ENGAGEMENT CONTACTS
CREATE TABLE engagement_contacts (
  id            TEXT PRIMARY KEY,
  engagement_id TEXT NOT NULL REFERENCES engagements(id) ON DELETE CASCADE,
  contact_id    TEXT NOT NULL REFERENCES contacts(id) ON DELETE RESTRICT,
  role          TEXT NOT NULL CHECK (role IN ('owner', 'decision_maker', 'champion')),
  is_primary    INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
  notes         TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (engagement_id, contact_id, role)
);
CREATE INDEX idx_engagement_contacts_engagement ON engagement_contacts (engagement_id);

-- MILESTONES
CREATE TABLE milestones (
  id              TEXT PRIMARY KEY,
  engagement_id   TEXT NOT NULL REFERENCES engagements(id) ON DELETE CASCADE,
  name            TEXT NOT NULL,
  description     TEXT,
  due_date        TEXT,
  completed_at    TEXT,
  status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
                    'pending', 'in_progress', 'completed', 'skipped'
                  )),
  payment_trigger INTEGER NOT NULL DEFAULT 0 CHECK (payment_trigger IN (0, 1)),
  sort_order      INTEGER NOT NULL DEFAULT 0,
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_milestones_engagement ON milestones (engagement_id, sort_order);

-- PARKING LOT
CREATE TABLE parking_lot (
  id                  TEXT PRIMARY KEY,
  engagement_id       TEXT NOT NULL REFERENCES engagements(id) ON DELETE CASCADE,
  description         TEXT NOT NULL,
  requested_by        TEXT,
  requested_at        TEXT NOT NULL DEFAULT (datetime('now')),
  disposition         TEXT CHECK (disposition IN ('fold_in', 'follow_on', 'dropped')),
  disposition_note    TEXT,
  reviewed_at         TEXT,
  follow_on_quote_id  TEXT REFERENCES quotes(id) ON DELETE SET NULL,
  created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_parking_lot_engagement ON parking_lot (engagement_id);

-- INVOICES
CREATE TABLE invoices (
  id                TEXT PRIMARY KEY,
  org_id            TEXT NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
  engagement_id     TEXT REFERENCES engagements(id) ON DELETE RESTRICT,
  client_id         TEXT NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
  type              TEXT NOT NULL CHECK (type IN (
                      'deposit', 'completion', 'milestone', 'assessment', 'retainer'
                    )),
  amount            REAL NOT NULL CHECK (amount > 0),
  description       TEXT,
  status            TEXT NOT NULL DEFAULT 'draft' CHECK (status IN (
                      'draft', 'sent', 'paid', 'overdue', 'void'
                    )),
  stripe_invoice_id TEXT UNIQUE,
  stripe_hosted_url TEXT,
  due_date          TEXT,
  sent_at           TEXT,
  paid_at           TEXT,
  payment_method    TEXT CHECK (payment_method IN ('ach', 'card', 'check', 'other')),
  notes             TEXT,
  created_at        TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_invoices_org_client ON invoices (org_id, client_id);
CREATE INDEX idx_invoices_org_status ON invoices (org_id, status);
CREATE INDEX idx_invoices_stripe ON invoices (stripe_invoice_id) WHERE stripe_invoice_id IS NOT NULL;

-- FOLLOW-UPS
CREATE TABLE follow_ups (
  id            TEXT PRIMARY KEY,
  org_id        TEXT NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
  client_id     TEXT NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
  engagement_id TEXT REFERENCES engagements(id) ON DELETE CASCADE,
  quote_id      TEXT REFERENCES quotes(id) ON DELETE CASCADE,
  type          TEXT NOT NULL CHECK (type IN (
                  'proposal_day2', 'proposal_day5', 'proposal_day7',
                  'review_request', 'referral_ask',
                  'safety_net_checkin', 'feedback_30day'
                )),
  scheduled_for TEXT NOT NULL,
  completed_at  TEXT,
  status        TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN (
                  'scheduled', 'completed', 'skipped'
                )),
  notes         TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_follow_ups_org_scheduled ON follow_ups (org_id, scheduled_for, status);
CREATE INDEX idx_follow_ups_quote ON follow_ups (quote_id) WHERE quote_id IS NOT NULL;
CREATE INDEX idx_follow_ups_engagement ON follow_ups (engagement_id) WHERE engagement_id IS NOT NULL;

-- TIME ENTRIES
CREATE TABLE time_entries (
  id            TEXT PRIMARY KEY,
  org_id        TEXT NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
  engagement_id TEXT NOT NULL REFERENCES engagements(id) ON DELETE CASCADE,
  date          TEXT NOT NULL,               -- ISO 8601 date (YYYY-MM-DD)
  hours         REAL NOT NULL CHECK (hours > 0 AND hours <= 24),
  description   TEXT,
  category      TEXT CHECK (category IN (
                  'assessment', 'solution_design', 'implementation',
                  'training', 'documentation', 'admin'
                )),
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_time_entries_engagement ON time_entries (engagement_id);

-- MAGIC LINKS
CREATE TABLE magic_links (
  id          TEXT PRIMARY KEY,
  email       TEXT NOT NULL,
  token       TEXT NOT NULL UNIQUE,          -- 32-byte cryptographically random, hex-encoded
  expires_at  TEXT NOT NULL,                 -- 15 minutes from creation
  used_at     TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_magic_links_token ON magic_links (token);
CREATE INDEX idx_magic_links_email_created ON magic_links (email, created_at);
```

### JSON Column Contracts

| Table           | Column          | Schema                                                                                                                                                       |
| --------------- | --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `organizations` | `branding`      | `{ logo_url: string \| null, primary_color: string, secondary_color: string }`                                                                               |
| `organizations` | `settings`      | `{ current_rate: number, deposit_pct_default: number }`                                                                                                      |
| `assessments`   | `extraction`    | Raw Claude API response, stored verbatim                                                                                                                     |
| `assessments`   | `problems`      | `Array<'owner_bottleneck' \| 'lead_leakage' \| 'financial_blindness' \| 'scheduling_chaos' \| 'manual_communication' \| 'team_invisibility'>` -- max 3 items |
| `assessments`   | `disqualifiers` | `{ hard: string[], soft: string[] }`                                                                                                                         |
| `quotes`        | `line_items`    | `Array<{ problem: string, description: string, estimated_hours: number }>`                                                                                   |

---

## 12. API Surface

All endpoints under `/api/` use Astro API routes. All responses are `application/json`. Authentication is enforced in middleware -- handlers assume `locals.user` and `locals.orgId` are present on protected routes.

### Authentication Conventions

- **Admin routes** (`/admin/*`, `/api/admin/*`): require `locals.user.role === 'admin'`
- **Portal routes** (`/portal/*`, `/api/portal/*`): require `locals.user.role === 'client'`
- **Webhook routes** (`/api/webhooks/*`): no session auth; verify provider signature in handler
- **Auth routes** (`/auth/*`, `/api/auth/*`): unauthenticated

HTTP status codes: `400` (validation), `401` (unauthenticated), `403` (forbidden), `404` (not found), `409` (conflict/state violation), `422` (unprocessable), `500` (internal).

### Auth Endpoints

**`POST /api/auth/magic-link`** -- Request a magic link for client portal login.

```
Request: { "email": string }
Response 200: { "ok": true }  // always 200 to prevent email enumeration
Side effects: Generate token (32-byte random, 15-min expiry), send via Resend
```

**`GET /api/auth/verify`** -- Consume a magic link and create a session.

```
Query: token=string
Response 302: Redirect to /portal or /admin
Side effects: Mark token used_at, create KV session, set HttpOnly/Secure/SameSite=Lax cookie
```

**`POST /api/auth/logout`** -- Invalidate session.

```
Response 302: Redirect to /auth/login
Side effects: Delete KV session, clear cookie
```

### Admin: Client Management

**`GET /api/admin/clients`** -- List/filter clients with cursor-based pagination.

```
Query: status, vertical, search, limit (default 50, max 200), cursor (ULID-based)
Response: { clients: [...], next_cursor: string | null }
```

**`POST /api/admin/clients`** -- Create a client record.

```
Body: { business_name (required), vertical (required), employee_count, years_in_business, source, referred_by, notes }
Response 201: { client: {...} }
```

**`GET /api/admin/clients/:id`** -- Full client record with contacts, assessments, quotes, engagements.

**`PATCH /api/admin/clients/:id`** -- Update client fields.

### Admin: Assessments

**`POST /api/admin/assessments`** -- Create assessment linked to client.

```
Body: { client_id (required), scheduled_at }
```

**`PATCH /api/admin/assessments/:id`** -- Update assessment fields (extraction, problems, status, etc.).

**`POST /api/admin/assessments/:id/transcript`** -- Upload transcript to R2.

```
Request: multipart/form-data, file (max 10MB)
Side effects: Upload to R2 at transcripts/{org_id}/{assessment_id}/{filename}
```

### Admin: Quotes

**`POST /api/admin/quotes`** -- Create quote from assessment.

```
Body: { client_id, assessment_id, line_items: [{problem, description, estimated_hours}], rate, deposit_pct, notes }
Response 201: { quote: { ..., total_hours, total_price, deposit_amount } }
```

**`POST /api/admin/quotes/:id/generate-sow`** -- Generate SOW PDF via Forme, store in R2.

```
Response: { sow_path, sow_url (presigned, 1hr) }
R2 path: sows/{org_id}/{quote_id}/sow-v{version}.pdf
```

**`POST /api/admin/quotes/:id/send`** -- Send quote to client.

```
Side effects: Create client user if needed, create SignWell document, update quote status/sent_at/expires_at, send invitation email, auto-schedule follow-up cadence
Response 409 if SOW not generated
```

**`POST /api/admin/quotes/:id/versions`** -- Create new version (client requests changes).

```
Side effects: Set parent quote to 'superseded', new quote starts as 'draft'
```

### Admin: Engagements

**`POST /api/admin/engagements`** -- Create engagement with milestones.

**`PATCH /api/admin/engagements/:id/status`** -- Explicit status transitions only.

```
Allowed: scheduled->active, active->handoff, handoff->safety_net, safety_net->completed, any->cancelled
handoff side effects: set handoff_date, safety_net_end (+14d), schedule review_request/referral_ask/feedback_30day
```

**`POST /api/admin/engagements/:id/time`** -- Log time entry.

```
Side effects: Update engagements.actual_hours += hours
```

**`POST /api/admin/engagements/:id/parking-lot`** -- Log parking lot item.

**`PATCH /api/admin/parking-lot/:id`** -- Disposition a parking lot item.

### Admin: Invoices

**`POST /api/admin/invoices`** -- Create invoice.

**`POST /api/admin/invoices/:id/send`** -- Create Stripe invoice and send.

```
Side effects: Create/retrieve Stripe Customer, set ACH default + card fallback, send via Stripe, update status/stripe_invoice_id/stripe_hosted_url
```

### Webhook Handlers

**`POST /api/webhooks/signwell`** -- Handle `document.completed`. HMAC-SHA256 signature verification. Full processing chain: look up quote, download signed PDF, store in R2, update quote status, create engagement, create deposit invoice, send confirmation.

**`POST /api/webhooks/stripe`** -- Handle `invoice.paid`, `invoice.payment_failed`, `invoice.overdue`. Stripe signature verification via SDK. Deposit paid triggers engagement activation. Completion paid triggers safety_net transition.

### Client Portal Endpoints

All require `role === 'client'`, scoped to client's own `client_id`.

**`GET /api/portal/me`** -- User and client info.

**`GET /api/portal/quotes`** -- Client's quotes (scope, price, status -- no rate/hours).

**`GET /api/portal/engagements/current`** -- Current engagement with milestones.

**`GET /api/portal/invoices`** -- Invoices with Stripe hosted URLs.

**`GET /api/portal/documents`** -- Documents with presigned R2 URLs (1hr expiry).

---

## 13. Non-Functional Requirements

### Performance Budgets

| Metric                      | Target        | Measurement           |
| --------------------------- | ------------- | --------------------- |
| Admin page load (SSR)       | < 800ms p95   | Workers response time |
| Portal page load (SSR)      | < 800ms p95   | Workers response time |
| API endpoint (CRUD)         | < 300ms p95   | Workers response time |
| PDF generation (Forme)      | < 3,000ms p95 | Workers CPU time      |
| R2 presigned URL generation | < 50ms p99    | In-process            |
| Magic link email delivery   | < 10s p95     | Resend delivery       |
| Stripe invoice creation     | < 2,000ms p95 | Includes Stripe API   |
| D1 indexed read             | < 10ms p95    | D1 query time         |
| D1 write                    | < 20ms p95    | D1 write time         |

### Security Requirements

| Requirement            | Implementation                                                             |
| ---------------------- | -------------------------------------------------------------------------- |
| All routes behind auth | Middleware enforces before handler                                         |
| Session tokens         | 32-byte random, KV-stored, HttpOnly + Secure + SameSite=Lax                |
| Magic link tokens      | 32-byte random, 15-min expiry, single-use                                  |
| CSRF protection        | SameSite=Lax on session cookie                                             |
| Webhook verification   | Stripe: `constructEvent()`. SignWell: HMAC-SHA256. Both before processing. |
| R2 access              | No public buckets. Presigned URLs with 1-hour TTL.                         |
| org_id enforcement     | Data access layer injects on every query. No bypass.                       |
| No client-side secrets | Astro SSR -- no API keys in hydrated JS bundles                            |
| Admin passwords        | bcrypt, min cost factor 12, timingSafeEqual comparison                     |
| Rate limiting          | Magic link: max 3 requests per email per 15 minutes via KV counter         |

### Scalability Targets (No Architecture Change Required)

| Dimension                     | Target        |
| ----------------------------- | ------------- |
| Concurrent active engagements | 50            |
| Total clients in D1           | 10,000        |
| Documents in R2               | 100,000 files |
| Webhook events per day        | 500           |
| Admin concurrent users        | 5             |

### Availability and Reliability

| Requirement           | Target                                                    |
| --------------------- | --------------------------------------------------------- |
| Uptime                | Cloudflare Pages/Workers SLA (99.9%+)                     |
| Data recovery         | D1 Time Travel, 30-day point-in-time recovery             |
| Signed SOW durability | R2 versioning on `sows/` prefix (immutable legal records) |
| Webhook replay        | Stripe retries for 72 hours; idempotent handlers required |

### Cost Ceiling

Monthly infrastructure cost at MVP scale: under $30.

| Service                 | At Launch (0-5 clients) | At Scale (10-20 clients/mo) |
| ----------------------- | ----------------------- | --------------------------- |
| Cloudflare Workers Paid | $5                      | $5                          |
| D1                      | Included                | ~$1                         |
| R2                      | Included (free tier)    | ~$1                         |
| Workers KV              | Included                | Included                    |
| Resend                  | Free (3K emails)        | $20 (50K tier)              |
| SignWell                | Free (25 docs)          | ~$20 (overage)              |
| Stripe                  | Transaction fees only   | Transaction fees            |
| **Total**               | **~$5 + Stripe**        | **~$47 + Stripe**           |

Stripe fee comparison per $5,000 invoice: ACH $17 (0.34%) vs. Card $165 (3.3%). Default to ACH.

---

## 14. Platform-Specific Design Constraints

### Primary Platform: Mobile Web (Client Portal)

The client portal's primary users are business owners who will open the magic link on a phone. Mobile-first is the design direction for the portal -- desktop is an adaptation of the mobile layout, not the reverse.

**Constraints:**

- **No hover-dependent interactions.** Every hover-triggered action must have an explicit tap target on mobile. Tooltips are tap-to-open.
- **44x44pt minimum touch targets** (Apple HIG / WCAG 2.5.5 AA). Applies to table rows, milestone indicators, document download buttons.
- **Viewport and iframe conflicts.** The SignWell signing iframe must be tested on iOS Safari and Chrome for Android. Container sized to device viewport height minus browser chrome, not fixed pixels.
- **Magic link from email clients.** In-app browsers (Gmail, Outlook) have different cookie/session behaviors. Session must survive redirect from in-app browser to default browser, or function correctly within the in-app browser. Test against Gmail iOS app before Phase 2 ships.
- **Bottom navigation.** Four sections (Dashboard, Documents, Invoices, Progress) in a bottom tab bar. No hamburger menu.

### Secondary Platform: Desktop Web (Admin Interface)

Admin is desktop-primary (Mac laptop). No mobile optimization required for admin in MVP.

- **Dense information display acceptable.** Pipeline kanban, quote builder, engagement dashboard can use smaller type and tighter spacing.
- **Keyboard navigation for quote builder.** Tab between fields, Enter to add row, Delete/Backspace on empty row to remove. Critical for the 48-hour post-assessment workflow.
- **Resizable sidebar panels.** Split-pane layout for client record and engagement dashboard.
- **Print/export for SOW preview.** Browser native print dialog for admin review before formal Forme generation.

### Accessibility Requirements

**Target:** WCAG 2.1 Level AA for both portal and admin.

- **Color contrast:** 4.5:1 for text on interactive elements. Status badges carry text labels, not just color. Focus indicators at 3:1 contrast.
- **Form accessibility:** `<label>` on every input (no placeholder-only). Error messages via `aria-describedby`. Required fields marked in label text.
- **Keyboard navigation:** Logical tab order. Modal focus trapping. SignWell iframe must not trap keyboard focus.
- **Dynamic content:** `aria-live="polite"` for status updates, `aria-live="assertive"` for errors. Loading states communicated to screen readers.
- **Screen reader testing:** VoiceOver on Safari (primary), NVDA on Chrome (secondary).
- **Reflow:** Content at 320px viewport width must not require horizontal scrolling (WCAG 1.4.10). Text resize up to 200% must not break layout.
- **Icons:** Accessible names via `aria-label` or visually hidden text. Decorative images have `alt=""`.
- **Automated testing:** axe-core scan before each phase ships. Manual keyboard + screen reader testing for signing flow (Phase 2), payment flow (Phase 3), document library (Phase 4).

---

## 15. Success Metrics & Kill Criteria

### MVP Success Metrics

Measured after the first completed engagement (estimated: 45-60 days from launch).

| Metric                                 | Target                           | How Measured                              |
| -------------------------------------- | -------------------------------- | ----------------------------------------- |
| Post-assessment time to sent quote     | < 2 hours                        | assessment.completed_at -> quote.sent_at  |
| Quote accuracy (est. vs. actual hours) | Within 20% by engagement 3       | time_entries.hours vs. quotes.total_hours |
| Client portal adoption                 | 100% -- no paper/email fallback  | Portal logins per engagement              |
| Follow-up cadence compliance           | Zero missed touches (Phase 5)    | follow_ups: scheduled vs. completed       |
| Deposit invoice payment cycle          | < 7 days from sent               | invoices.sent_at -> invoices.paid_at      |
| Completion invoice payment cycle       | < 7 days from sent               | invoices.sent_at -> invoices.paid_at      |
| Infrastructure cost ceiling            | < $30/month at 5+ engagements/mo | Cloudflare + Resend + SignWell billing    |

### Phase Gate Criteria

Each phase must meet its definition of done before Phase N+1 begins.

| Phase   | Gate                            | Definition of Done                                                                                     |
| ------- | ------------------------------- | ------------------------------------------------------------------------------------------------------ |
| Phase 1 | Before first assessment call    | Admin can create client, add contacts, record assessment, upload transcript, store extraction JSON     |
| Phase 2 | Before first proposal sent      | Quote built -> SOW generated -> client receives email -> client signs -> webhook confirms              |
| Phase 3 | Before first close              | Signature triggers deposit invoice -> client pays -> engagement active -> completion invoice available |
| Phase 4 | Before first delivery completes | Milestones, time entries, parking lot dispositioned, client sees progress and documents                |
| Phase 5 | Post-first engagement           | Follow-ups auto-schedule, BI views live, Claude API extraction triggerable                             |

### Kill Criteria

If any of the following are true at the 60-day mark, pause development and reassess:

| Kill Condition                           | Threshold                                            | Rationale                         |
| ---------------------------------------- | ---------------------------------------------------- | --------------------------------- |
| No clients in pipeline                   | Zero assessment calls booked                         | Distribution problem, not tooling |
| Admin working around the system          | Email/spreadsheet used for portal-supported workflow | Workflow model is wrong           |
| Client flow produces friction at signing | Client requires support to complete sign -> pay      | Portal is a credibility liability |
| Infrastructure cost exceeds ceiling      | > $30/month with < 5 active clients                  | Stack assumptions wrong           |

---

## 16. Risks & Mitigations

### Business Risks

**Risk 1: Portal build delays the first assessment call.**
The consulting business does not need the portal to make the first sale -- it needs the assessment call script and SOW template. The portal is an operations multiplier, not a launch prerequisite.
_Mitigation:_ Phase 1 is scoped to days. If Phase 1 is not complete before the first assessment call, the admin uses a manual assessment form. The business does not stop.

**Risk 2: Build timeline competes directly with revenue timeline.**
The venture's revenue target is $5,000 in 30 calendar days. If Phase 1 takes 2-4 weeks and Phase 2 another 2-4 weeks, the build consumes the window before the first proposal goes out.
_Mitigation:_ Define calendar dates for each phase, not just milestone descriptions. Name a fallback for Phase 2 delay (e.g., PandaDoc free tier for first 2 proposals).

**Risk 3: Scope of the portal expands during Phase 4-5.**
Once the system is running, feature requests will appear. The parking lot protocol should be applied to the portal itself.
_Mitigation:_ All feature requests go to GitHub issue backlog. Nothing built outside current phase scope. Roadmap reviewed at phase completion, not mid-phase.

**Risk 4: Pricing model flexibility not fully baked into quote builder.**
The pricing model is under review (per memory context). The PRD reflects scope-based pricing correctly: internal rate not exposed to clients, configurable in org settings.
_Mitigation:_ Rate field on quotes stores the rate at time of creation. Rate changes in org settings do not affect historical quotes.

### Market Risks

**Risk 5: Clients resist using a portal and prefer email.**
Target clients are 10-25 person business owners. Some will not be tech-forward.
_Mitigation:_ Magic link requires only an email address. First portal experience is their pre-populated quote. If a specific client is portal-resistant, admin can generate Stripe invoice link and DocuSign link manually. This is not the preferred path.

**Risk 6: The unique differentiators are invisible to buyers.**
Assessment capture, parking lot, and quote accuracy loop are internal features. The client-facing experience is functionally equivalent to HoneyBook Premium.
_Mitigation:_ Marketing should not lean on the portal as a selling point. The differentiation is the consulting methodology, not the software. The portal reinforces professionalism but does not replace it.

### Technical Risks

**Risk 7: Forme (WASM PDF generation) unproven in Workers environment.**
If Forme does not perform within the performance budget or exceeds the Workers 1MB script size limit, SOW generation breaks.
_Mitigation:_ Spike Forme integration in Phase 2 before building the full quote-to-SOW pipeline. Alternatives: Puppeteer-as-a-service (hosted), React PDF (browser-side), or lightweight HTML-to-PDF service.

**Risk 8: Webhook reliability is the backbone of automated state transitions.**
A missed or duplicated webhook leaves data in an inconsistent state.
_Mitigation:_ Idempotent handlers from day one. Stripe webhook signature verification mandatory. Log all webhook events to a separate table. Test delivery in staging before Phase 3.

**Risk 9: D1 cold start latency on first request per edge PoP.**
D1 cold starts are typically sub-100ms but can spike.
_Mitigation:_ Pre-warm with lightweight health-check route. Monitor P99 from Cloudflare Analytics.

**Risk 10: `org_id` enforcement bypassed by direct D1 query.**
If a page component calls D1 directly, the multi-tenancy seam is broken.
_Mitigation:_ Code review gate: no `env.DB.prepare()` outside `src/lib/db/`. ESLint rule to flag direct D1 access in `pages/` files.

**Risk 11: SignWell free tier is a fragile dependency.**
25 API documents/month free is a pricing page, not a contractual commitment.
_Mitigation:_ Accept at launch. Monitor usage at 15+ documents/month. Overage is $0.85/document -- acceptable.

### Competitive Risk Register

| Risk                                                     | Likelihood | Impact | Mitigation                                         |
| -------------------------------------------------------- | ---------- | ------ | -------------------------------------------------- |
| Phase 1 delayed, first assessment call proceeds manually | Medium     | Low    | Accept; build lean                                 |
| Phase 2 delayed, first proposal as email PDF             | Medium     | Medium | Accept with fallback (PandaDoc/DocuSign free tier) |
| SuiteDash at $19/mo is "good enough"                     | Low        | High   | Validate productization intent before Phase 3      |
| Copilot.com adds assessment/parking-lot features         | Low        | High   | Focus moat on SMD-specific workflow IP             |
| SignWell or Resend free tier changes                     | Low        | Low    | Accept; cost impact < $50/mo                       |

---

## 17. Open Decisions / ADRs

### Decisions Requiring Resolution

| #   | Decision                             | Required By           | Options                                              | Recommendation                                                  |
| --- | ------------------------------------ | --------------------- | ---------------------------------------------------- | --------------------------------------------------------------- |
| D1  | Product name                         | Before productization | TBD                                                  | Keep "portal" as placeholder through MVP                        |
| D2  | Forme vs. alternative PDF generation | Phase 2 start         | Forme (WASM edge), Puppeteer-as-a-service, React PDF | Spike Forme first. Decide before Phase 2 scope finalized.       |
| D3  | Admin auth method                    | Phase 1               | Email + password, magic link, or both                | Email + password for admin (single user), magic link for client |
| D4  | Stripe account setup                 | Before Phase 3        | New account under SMDurgan LLC                       | Locked. Create account and store credentials before Phase 3.    |
| D5  | SOW template layout                  | Phase 2               | Build against Decision #18 constraints               | Constraints locked. Layout is Phase 2 design execution.         |
| D6  | Follow-up email sender               | Phase 5               | `team@smd.services` (confirmed)                      | Confirm DNS/Resend configuration before Phase 5.                |
| D7  | Rate at first engagement             | Before first quote    | $150/hr launch rate (Decision #16)                   | Locked. Stored in organizations.settings JSON.                  |

### ADRs to Record

**ADR-001: ULID vs UUID for primary keys.** Recommended: ULID. Time-sortability enables cursor-based pagination and debug visibility.

**ADR-002: Astro SSR vs. islands for admin/portal UI.** Recommended: SSR for list/read pages, React islands for quote builder and engagement dashboard. Keep portal fully SSR for simplicity.

**ADR-003: Session storage -- KV vs. D1.** Confirmed: KV for sessions. D1 writes are slower than KV reads for the hot path of per-request session lookup.

**ADR-004: Forme validation.** Spike in Phase 1: generate sample SOW in Workers environment. If fidelity insufficient, evaluate HTML-to-PDF or pre-built PDF template with field injection.

**ADR-005: Follow-up cadence scheduling mechanism.** Phase 4: Admin manually marks follow-ups from dashboard (no cron dependency). Phase 5: Cloudflare Cron Triggers poll `follow_ups` table.

**ADR-006: R2 document naming convention.**

```
transcripts/{org_id}/{assessment_id}/{filename}
sows/{org_id}/{quote_id}/sow-v{version}.pdf
sows/{org_id}/{quote_id}/signed-sow.pdf
documents/{org_id}/{engagement_id}/{type}/{filename}
```

Where `type` is `handoff`, `sop`, or `training`. Formalize before Phase 2.

---

## 18. Phased Development Plan

Each phase is a discrete block. A feature belongs to exactly one phase. Definition of done is an end-to-end workflow, not a feature checklist.

### Phase 1 -- Foundation (Before First Assessment Call)

**Boundary:** Internal admin tooling only. No client-facing functionality. No payments. No e-signature.

**What ships:**

- Project scaffolding: Astro SSR + Cloudflare adapter, D1/R2/KV bindings, deployment pipeline
- Full schema: all tables, all migrations, SMD Services org seed data
- Admin authentication: email + password login, session management, auth middleware
- Client CRUD: create/edit/list clients with status progression and vertical tracking
- Contact CRUD: create/edit contacts, link to clients
- Assessment capture: create assessment, upload transcript to R2, store extraction JSON, map problems to 6 universal categories
- Claude extraction prompt (Deliverable #34)
- Pipeline view: list/filter all clients by status

**Definition of done:** Admin logs in, creates a client, adds contacts, records an assessment, uploads a transcript, pastes extraction output into structured JSON, and sees problems mapped to the 6 universal categories. Pipeline view shows client as "assessed."

### Phase 2 -- Quote to Contract (Before First Proposal Sent)

**Boundary:** Quote builder + SOW generation + client portal (quote view + signing only). No payment collection.

**What ships:**

- Quote builder: line item editor, auto-calculate price, payment structure config
- SOW PDF generation: Forme templates, generate PDF, store in R2
- SignWell integration: create signature request, embed iframe, webhook handler
- Client authentication: magic link login
- Client portal -- quote view: scope details, project price, "Review & Sign" action
- Client portal -- signing: embedded SignWell iframe
- Quote notification email via Resend with portal invitation + magic link
- Portal invitation: client account created on quote send

**Definition of done:** Admin builds a quote from an assessment, generates a SOW, sends it. Client receives invitation email, logs in via magic link, views the quote, signs the SOW. Webhook fires and marks the quote as accepted. Admin sees updated status in pipeline.

### Phase 3 -- Payments (Before First Close)

**Boundary:** Stripe invoicing end-to-end. Engagement creation gated on deposit paid.

**What ships:**

- Stripe integration: create invoices, configure ACH default + card fallback
- Invoice management admin: create/send/track invoices
- Stripe webhook handler: invoice.paid, invoice.overdue, invoice.payment_failed
- Client portal -- invoice view: list with status, Stripe hosted invoice link
- Auto-invoice on signing: deposit invoice auto-created on SignWell webhook
- Engagement auto-creation: engagement record created when deposit is paid
- Completion invoice: admin creates and sends manually from engagement view

**Definition of done:** SOW signature triggers deposit invoice auto-creation. Client pays via Stripe. Engagement activates. Admin can create and send completion invoice.

### Phase 4 -- Engagement Tracking (Before First Delivery Completes)

**Boundary:** Full engagement lifecycle.

**What ships:**

- Configurable milestones, status transitions, contact role assignment per engagement
- Time tracking: log entries, compare estimated vs. actual hours
- Safety net end date auto-calculation (handoff + 14 days)
- Parking lot: log items, disposition at pre-handoff review, generate follow-on quote stub
- Auto-completion invoice at handoff milestone
- Client portal -- progress view: milestone list with status indicators
- Client portal -- document library: upload/download handoff docs via presigned R2 URLs

**Definition of done:** Full engagement tracked from kickoff to handoff. Client sees milestones and status. Parking lot dispositioned. Documents accessible. Completion invoice auto-generates. Engagement transitions to safety_net.

### Phase 5 -- Automation and Intelligence (Post-First Engagement)

**Boundary:** Follow-up automation, business intelligence, Claude API integration.

**What ships:**

- Follow-up scheduler: auto-schedule full cadence on triggering events
- Follow-up dashboard: upcoming / overdue / completed with one-click complete
- Resend email templates: pre-built for all 7 follow-up types, "we" voice
- Business intelligence: quote accuracy (by problem type and vertical), pipeline conversion, revenue by period, follow-up compliance
- Claude API integration: trigger extraction from admin, review before committing
- Client portal -- parking lot view: read-only items and disposition

**Definition of done:** Follow-ups auto-schedule on quote sent and engagement handoff. Admin sees upcoming follow-ups. At least one follow-up email sent via Resend. Quote accuracy view shows estimated vs. actual for first completed engagement. Claude API extraction triggered, reviewed, and saved.

### Deferred -- Not in Any Phase

| Feature                                           | Reason                                                   |
| ------------------------------------------------- | -------------------------------------------------------- |
| Multi-tenant registration and org onboarding      | Not needed until productization                          |
| Portal billing and subscription management        | Productization decision required                         |
| Org-level settings UI                             | org_id and settings JSON built; UI deferred              |
| Admin impersonation of client view (AU-7)         | P2 -- useful for support, not required for launch        |
| Quote versioning (QB-11)                          | P2 -- build after first version-request scenario         |
| Pre-fill quote from assessment extraction (QB-10) | P2 -- defer until extraction quality proven              |
| Retainer invoice type                             | Decision #12: define retainer scope after first delivery |
| Admin role granularity within org                 | admin/client binary is correct for MVP                   |

---

## 19. Glossary

| Term                           | Definition                                                                                                                                                                                          |
| ------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Assessment**                 | A structured record of a diagnostic call with a prospect, including MacWhisper transcript, Claude extraction output, identified problems, and qualification signals.                                |
| **Champion**                   | The client-side employee identified during assessment who will own the implemented systems post-handoff (Decision #28).                                                                             |
| **Decision Stack**             | The 29 locked strategic decisions in `docs/adr/decision-stack.md` that govern how SMD Services sells and delivers. Source of truth for all business logic.                                          |
| **Deposit**                    | First payment (default 50% of project price) due at SOW signing. Gates engagement activation.                                                                                                       |
| **Engagement**                 | An active consulting project with a defined scope, milestones, and timeline. Created when deposit is paid.                                                                                          |
| **Extraction**                 | The structured JSON output produced by running a MacWhisper transcript through the Claude extraction prompt. Contains problems, complexity signals, champion candidate, and disqualification flags. |
| **Follow-up**                  | A scheduled touch point in a cadence (proposal follow-ups, review requests, referral asks, feedback surveys) tracked in the `follow_ups` table.                                                     |
| **Magic Link**                 | A single-use, time-limited URL sent via email that authenticates the recipient without a password.                                                                                                  |
| **Milestone**                  | A named deliverable or checkpoint within an engagement, with optional payment trigger.                                                                                                              |
| **org_id**                     | The organization identifier present on every table, enforced by the data access layer. The multi-tenancy seam.                                                                                      |
| **Parking Lot**                | A collection of out-of-scope requests captured during an engagement and dispositioned at the pre-handoff review as fold_in, follow_on, or dropped (Decision #11).                                   |
| **Pipeline**                   | The progression of clients through status stages: prospect -> assessed -> quoted -> active -> completed.                                                                                            |
| **Portal**                     | The client-facing web application at `portal.smd.services` where clients view quotes, sign SOWs, pay invoices, and access documents.                                                                |
| **Safety Net**                 | The 14-day async support period following handoff (Decision #27). Includes questions about built systems, minor fixes under 30 minutes, and one additional walkthrough.                             |
| **Six Universal SMB Problems** | Owner bottleneck, lead leakage, financial blindness, scheduling chaos, manual communication, employee retention. The diagnostic framework for assessment calls.                                     |
| **SOW**                        | Statement of Work. A 2-page PDF containing scope, deliverables, project price, payment terms, and exclusions (Decision #18).                                                                        |
| **ULID**                       | Universally Unique Lexicographically Sortable Identifier. Used for all primary keys.                                                                                                                |

---

## Appendix: Resolved Issues

All 20 issues from the PRD review panel have been resolved. Decisions made 2026-03-30.

| ID     | Issue                                 | Decision                                                                                                 |
| ------ | ------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| OQ-001 | Employee count outside 10-25 buy box  | **Soft warning, no hard block.** Some 8-9 person businesses are viable (Decision #2).                    |
| OQ-002 | Client `source` required or optional? | **Required.** Pipeline conversion data needs source attribution.                                         |
| OQ-003 | Single contact, multiple roles?       | **Yes.** Two separate role assignments per schema. Owner-as-champion is the common case.                 |
| OQ-004 | Financial problem / dirty books       | **Soft warning + admin confirmation.** Matches Decision #6 intent.                                       |
| OQ-005 | Quote builder max line items          | **Soft warning at 3, no hard block.**                                                                    |
| OQ-006 | Stale SOW PDF after quote change      | **Warning + confirmation required before send.** Regenerate PDF.                                         |
| OQ-007 | Magic link expiration                 | **15 minutes.** Sessions: 7 days.                                                                        |
| OQ-008 | Admin override for offline payment    | **Yes — with notes field.**                                                                              |
| OQ-009 | D1 transaction atomicity              | **Spike #85 created.** Validate `db.batch()` rollback before Phase 3.                                    |
| OQ-010 | Bounced invitation email              | **Admin corrects email and re-sends.**                                                                   |
| UX-001 | Champion portal access                | **Deferred to Phase 4.** Phase 2: owner/decision_maker only. Issue #86.                                  |
| UX-002 | SignWell iOS Safari verification      | **Explicit mobile test as Phase 2 DOD gate.** Redirect fallback if iframe fails.                         |
| UX-003 | Line items expose per-item pricing?   | **No.** Problem descriptions only, total project price only. Hard constraint (Decision #16).             |
| UX-004 | Empty state before quote ready        | **"Your proposal is being prepared" holding screen.**                                                    |
| CA-001 | Off-the-shelf platform trial          | **No trial needed.** Build decision confirmed.                                                           |
| CA-002 | Productization trigger                | **Deferred.** Define after 2-3 completed engagements with real data.                                     |
| CA-003 | Phase 2 delay fallback                | **PandaDoc or DocuSign free tier** for first 2 proposals if Phase 2 delayed.                             |
| TC-001 | Phase 1 ship date                     | **Build at speed.** AI agent team executes rapidly — no artificial date constraint.                      |
| TC-002 | Extraction-to-quote time target       | **< 45 min from "call ended" to "quote sent."**                                                          |
| TC-003 | Portal design standard                | **Stitch design system** applied to portal UI/UX. Professional design is a Phase 2 acceptance criterion. |

---

_SMD Services Client Portal - Product Requirements Document | Confidential_
_Source: docs/pm/prd-contributions/ (round-1 audit trail)_

<!-- Synthesis: 19 sections, 12711 words, 0 unresolved issues, 1 round, all resolved 2026-03-30 -->
