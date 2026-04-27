# Interaction Designer Contribution - Design Brief Round 2

**Author:** Interaction Designer (Sonnet 4.6)
**Date:** 2026-04-26
**Design Maturity:** Full system — inventory, gaps, and refinements only. No redesign of existing flows unless PRD explicitly flags a problem.
**Status:** Revised after cross-role review
**Sources consulted:** PRD §7-9, §14, §18; NAVIGATION.md v3.1; portal-ux-brief.md; admin-ux-brief.md; UI-PATTERNS.md; empty-state-pattern.md; Pattern 08 (Actions and Menus); existing src/pages/ tree; Round 1 contributions from Brand Strategist, Design Technologist, and Target User (Marcus).

---

## Changes from Round 1

1. **Portal Dashboard: hero card restructured for above-the-fold guarantee on 375px.** Marcus's Round 1 feedback was unambiguous: scope and price visible without scrolling, one CTA, no hunting. Updated the State 1 above-fold spec to enumerate exactly what fits and in what order, with explicit y-budget enforcement. Triggered by: Target User (Marcus), Round 1.

2. **Email Touchpoint Inventory added as new subsection under Feedback Patterns.** Marcus said "the invitation email earns the click or doesn't." Round 1 had a placeholder table; this round expands it into a full inventory: eight transactional emails, each with trigger, recipient, plain-text fallback requirement, and trust-signal spec. Triggered by: Target User (Marcus), Round 1.

3. **Signing view restructured: framed embed, not popup or takeover.** Marcus named visual whiplash at the signing moment as a trust killer. Updated Quote Detail breakdown to specify: our chrome (header, breadcrumb, contextual heading "Reviewing engagement scope") wraps the SignWell iframe; a pre-signing disclosure line explicitly names SignWell so the visual shift is expected, not a surprise. Triggered by: Target User (Marcus), Round 1.

4. **Invoice Detail rewritten to match professional invoice register.** Marcus compared the previous spec to "Etsy receipt" energy. Updated to specify: real invoice format (firm name, address, invoice number using firm numbering scheme, date, due date, line items with full descriptions, total in MoneyDisplay). Print-friendly. Downloadable PDF. No congratulatory framing. Triggered by: Target User (Marcus), Round 1.

5. **Warning state added to relevant screens.** Brand Strategist proposed `--ss-color-warning: #6b4f08` (Gap 3 in their contribution). I've mapped this token to three concrete interaction contexts: quote-near-expiry countdown, deposit-overdue status, and parking-lot items awaiting disposition. Triggered by: Brand Strategist, Round 1.

6. **Token name drift noted.** Brand Strategist and Design Technologist both flagged that UI-PATTERNS.md Rule 6 documents `space-section: 32px` and `space-card: 24px`, while compiled tokens are 48px and 32px respectively. All spacing citations in this document use the live token values (48/32/16/12). A follow-on issue to correct UI-PATTERNS.md Rule 6 documentation is noted in the Open Questions section. Triggered by: Brand Strategist §5.1, Design Technologist §Design Token Architecture. Does not affect interaction spec; affects documentation only.

7. **New component routes confirmed.** Design Technologist identified `SigningView`, `MagicLinkExpiredForm`, and `ParkingLotPanel` as new portal components. This round confirms each maps to a specific route or route state. Admin new components `PipelineKanban`, `ClientCard`, `QuoteLineItemEditor`, `SOWPreviewPane`, `FollowUpCard`, `TimeEntryLog`, `ExtractionPanel` are mapped to admin routes. Screen inventory updated accordingly. Triggered by: Design Technologist, Round 1.

8. **Navigation Model: deposit/payment status elevated.** Marcus said he should not have to navigate to Account → Settings → Billing to see if his deposit cleared. Updated Portal Dashboard breakdown: payment/invoice status appears as a visible chip on the engagement summary row. Updated Navigation Model to reinforce: billing is never a setting. Triggered by: Target User (Marcus), Round 1.

9. **Persistent tabs over hamburger confirmed with NAVIGATION.md citation.** Validated the decision against NAVIGATION.md §4.4. Added rationale: tab visibility = scannability of capability without additional taps; confirmed this is the live architectural decision. Icon set specified (Material Symbols Outlined, stable names). Triggered by: Brand Strategist (icon list proposal), Target User (persistent tabs preference), Round 1.

10. **Champion access (UX-001) confirmed deferred.** PRD §18 Phase 4, not MVP. Removed from MVP screen inventory where mistakenly implied; added explicit deferral note. Triggered by: Round 1 open question #4.

11. **Touch Target & Tap Affordance subsection added under Form Patterns.** 44×44px minimum was missing from Round 1 as a standalone section. Marcus's persona (phone in truck, sun glare) makes this non-negotiable. Added with WCAG 2.5.5 AAA citation. Triggered by: Design Technologist Round 1 §Accessibility, Target User Round 1 environment context.

12. **Email Touchpoint Inventory added under Feedback Patterns.** Expanded from Round 1's simple table to a full per-email spec. Triggered by: Target User (Marcus), Round 1 ("the invitation email earns the click or doesn't").

---

## Screen Inventory

### `smd.services` — Marketing (Public)

| URL                    | Purpose                                                  | Primary Action       | PRD Feature                 | Status |
| ---------------------- | -------------------------------------------------------- | -------------------- | --------------------------- | ------ |
| `/`                    | Marketing home — credibility, positioning, guide-persona | Book assessment      | Pre-sales (supports §2)     | Exists |
| `/get-started`         | Onboarding CTA / warm landing                            | Book assessment      | Pre-sales                   | Exists |
| `/scorecard`           | Self-serve assessment wizard                             | Complete & book      | Pre-sales                   | Exists |
| `/book`                | Assessment booking form                                  | Confirm booking      | Pre-sales / US-001 upstream | Exists |
| `/book/manage/[token]` | Booking management via email token (reschedule/cancel)   | Reschedule or cancel | US-001 upstream             | Exists |
| `/book/manage/`        | Fallback when no token                                   | Book a call instead  | —                           | Exists |
| `/contact`             | General inquiry form                                     | Submit inquiry       | Pre-sales                   | Exists |
| `/404`                 | Not-found, subdomain-aware                               | Back to home         | —                           | Exists |

Auth entry points (on `smd.services` domain, redirect to subdomain sessions):

| URL                  | Purpose                                         | Primary Action | PRD Feature        | Status |
| -------------------- | ----------------------------------------------- | -------------- | ------------------ | ------ |
| `/auth/login`        | Admin login (email + password / magic link)     | Sign in        | US-016             | Exists |
| `/auth/portal-login` | Client magic-link request                       | Request link   | US-010, BR-027     | Exists |
| `/auth/verify`       | Magic-link token consumption + session creation | Auto-redirect  | US-010, BR-028-030 | Exists |

---

### `portal.smd.services` — Client Portal (session-auth-client)

| URL                                          | Purpose                                                                                                                                                             | Primary Action                                  | PRD Feature                                  | Component(s)                                                        | Status                           |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- | -------------------------------------------- | ------------------------------------------------------------------- | -------------------------------- |
| `/portal`                                    | Dashboard — state-responsive home; proposal / engagement / safety-net views                                                                                         | Review & Sign (pre-sign) or Pay Now (post-sign) | US-010, US-011, US-013; PRD §7 Steps 3-7; §9 | `PortalHomeDashboard.astro`                                         | Exists                           |
| `/portal/quotes`                             | Proposal list                                                                                                                                                       | Row click to detail                             | US-011; PRD §9                               | `QuoteList.astro`                                                   | Exists                           |
| `/portal/quotes/[id]`                        | Quote detail — scope, price, pre-signing states; 5-state machine                                                                                                    | Review & Sign (sent state)                      | US-011, BR-018, BR-042                       | `QuoteDetail.astro`                                                 | Exists                           |
| `/portal/quotes/[id]` → signing state        | Signing surface — framed SignWell iframe within portal chrome; rendered as state within `quotes/[id]` (not a separate route per current implementation; see Note A) | (SignWell iframe action)                        | US-011, EC-002, UX-002                       | `SigningView.astro` (New)                                           | New (component)                  |
| `/portal/invoices`                           | Invoice list                                                                                                                                                        | Row click to detail                             | US-013, US-012; PRD §9                       | `InvoicesList.astro`                                                | Exists                           |
| `/portal/invoices/[id]`                      | Invoice detail — professional invoice format, line items, payment, PDF                                                                                              | Pay Now (unpaid state)                          | US-012, US-013, BR-036                       | `InvoiceDetail.astro`                                               | Exists (needs update)            |
| `/portal/documents`                          | Document library — flat list of engagement files                                                                                                                    | Download / Open                                 | PRD §7 Step 8; §9                            | `Documents.astro`                                                   | Exists                           |
| `/portal/engagement`                         | Engagement progress — milestone timeline, parking lot (Phase 5 read-only)                                                                                           | (informational)                                 | US-014, US-015; PRD §7 Step 7; Phase 5       | `EngagementProgress.astro`, `ParkingLotPanel.astro` (New, readonly) | Exists (portal); New (component) |
| `/auth/portal-login` with expired/used token | Magic-link recovery form                                                                                                                                            | Request new link                                | US-010, BR-027                               | `MagicLinkExpiredForm.astro` (New)                                  | New (component)                  |

**Note A — Signing route disambiguation.** PRD §9 lists `/portal/quotes/[id]/sign` as a distinct route. Current implementation renders signing as a state within `quotes/[id]`. The `SigningView.astro` component is new and must be integrated at whichever surface is chosen. The decision — embedded state vs. distinct route — is noted as a pending resolution in Open Questions (see #1). Either approach is compatible with the framed-embed signing spec in this document.

**Champion access (UX-001): DEFERRED.** Per PRD §18 Phase 4, not MVP. Champion portal access requires a second magic-link invitation flow for the `engagement_contacts` champion role (Rachel persona). The data model (`engagement_contacts.is_champion`) exists. The auth flow and portal scoping are Phase 4 work. No champion screens appear in the MVP screen inventory.

---

### `admin.smd.services` — Admin Console (session-auth-admin)

| URL                                                           | Purpose                                                                       | Primary Action                | PRD Feature                    | Component(s)                                                        | Status                                               |
| ------------------------------------------------------------- | ----------------------------------------------------------------------------- | ----------------------------- | ------------------------------ | ------------------------------------------------------------------- | ---------------------------------------------------- |
| `/admin`                                                      | Admin dashboard — overdue follow-ups, pending signatures, quick-access        | (triage surface)              | US-016; PRD §9                 | —                                                                   | Exists                                               |
| `/admin/pipeline` (or `/admin/entities` with kanban grouping) | Pipeline kanban — Prospect / Assessed / Quoted / Active / Completed columns   | Navigate to entity            | US-001-002; PRD §9 pipeline IA | `PipelineKanban.astro` (New), `ClientCard.astro` (New)              | New (components); Exists (entities list, not kanban) |
| `/admin/entities`                                             | Client list — filterable                                                      | New entity                    | US-001, US-002                 | —                                                                   | Exists                                               |
| `/admin/entities/[id]`                                        | Client/entity detail — stage, contacts, assessments, quotes, engagements      | New assessment / New quote    | US-001-004                     | —                                                                   | Exists                                               |
| `/admin/entities/[id]/quotes/[quoteId]`                       | Quote builder — line items, pricing, SOW gen, SignWell send; SOW preview pane | Generate SOW / Send to Client | US-007-009                     | `QuoteLineItemEditor.astro` (New), `SOWPreviewPane.astro` (New)     | Exists (page); New (components)                      |
| `/admin/entities/[id]/meetings/[meetingId]`                   | Assessment/meeting detail                                                     | Complete assessment           | US-004-006                     | `ExtractionPanel.astro` (New)                                       | Exists (page); New (component)                       |
| `/admin/assessments/[id]`                                     | Assessment workspace — transcript upload, extraction, problem mapping         | Save extraction               | US-004-006, BR-011-013         | `ExtractionPanel.astro` (New)                                       | Exists (page); New (component)                       |
| `/admin/engagements/[id]`                                     | Engagement lifecycle — milestones, time entries, parking lot, invoices        | Log time / Advance status     | US-014-015; Phase 4            | `TimeEntryLog.astro` (New), `ParkingLotPanel.astro` (New, editable) | Exists (page); New (components)                      |
| `/admin/follow-ups`                                           | Follow-up queue — overdue, due today, upcoming                                | Complete / Skip               | Phase 5; PRD §9                | `FollowUpCard.astro` (New)                                          | Exists (page); New (component)                       |
| `/admin/analytics`                                            | Reports — pipeline funnel, revenue, follow-up compliance                      | (read-only)                   | Phase 5                        | —                                                                   | Exists                                               |
| `/admin/generators`                                           | Content generator catalog                                                     | Run generator                 | Phase 5 (Claude API)           | —                                                                   | Exists                                               |
| `/admin/generators/[type]`                                    | Single generator — inputs + preview + run history                             | Run                           | Phase 5                        | —                                                                   | Exists                                               |
| `/admin/settings/google-connect`                              | Google Calendar / Drive OAuth connect                                         | Connect                       | Phase 5                        | —                                                                   | Exists                                               |

---

## Key Screen Breakdowns

### 1. Portal Dashboard (`/portal`) — highest-stakes screen

**Design maturity note:** Exists. `PortalHomeDashboard.astro` and `portal/index.astro` implemented. This breakdown documents mandatory constraints and confirms above-the-fold discipline.

#### Above-the-Fold Contract (375px viewport, State 1 — pre-signature)

This is the make-or-break screen (Marcus, Round 1: "This is it. If I click the magic link and land somewhere that makes sense immediately — my company name, my scope, my price, one button — I'm in.").

The following content and nothing more must fit above the fold at 375px without scroll. Pixel budget assumes: iOS Safari with address bar shown = ~628px usable viewport height. Tab bar fixed at bottom = 64px. Usable content area = ~564px.

**Content stack (sequential, top to bottom), with approximate height allocation:**

```
PortalHeader (masthead, client name)            44px
─────────────────────────────────────────────
Eyebrow: "{BUSINESS NAME}" (text-label, mono    20px
caps, Archivo Narrow)
Engagement title (text-hero-mobile, Archivo     48px
weight 900, 1–2 lines max)
Scope summary (text-body-lg, 2–3 lines,         ~60px
authored plain language)
Project total (MoneyDisplay, hero size,         72px
tabular-nums, weight 900)
Payment structure caption (text-caption, e.g.   20px
"$3,500 now · $3,500 at completion" — authored
data only, NO fabricated fallback)
"Review and sign" primary CTA (full-width,      52px
solid primary, min-height 52px for safety)
─────────────────────────────────────────────
Total allocated                                 ~316px
Remaining buffer                                ~248px (used for
                                               spacing tokens
                                               between items)
```

Space between items uses `space-row` (12px) between eyebrow/title/summary elements, `space-stack` (16px) before and after the MoneyDisplay, `space-stack` (16px) above the CTA. Total rhythm overhead: ~100px. Resulting total: ~416px, within the 564px usable area.

**Invoice status chip on engagement summary row.** Per Marcus (Round 1): "I should never have to navigate through settings to find billing information." When a deposit invoice exists, a compact inline chip appears directly on the engagement summary row — not a separate navigation destination. Chip spec: `StatusPill` compact variant, tone = unpaid (attention color) or paid (complete color), label = "Deposit due" or "Deposit paid", rendered inline after the scope summary on the pre-signature card. This chip is rendered below the fold on 375px but visible with a single scroll — billing is never buried in navigation.

**Engagement status chip on active engagement row.** In State 3 (active engagement), the engagement summary row shows a compact "Latest invoice: {status}" chip. Chip resolves to the most recent invoice. No navigation required to see current payment status.

#### State Compositions (all four)

State 1 — Pre-signature (quote sent, not signed):

- Above fold: per the pixel budget above. One primary CTA only.
- Below fold: authored "What happens next" content if `next_step_text` is set; render nothing if absent (empty-state-pattern). Do not fabricate. No "We'll reach out to schedule kickoff." (Pattern A violation, CLAUDE.md).
- Invoice status chip: not yet applicable (no invoice exists).
- Empty-state if no quote: "Your proposal is being prepared." (UX-004 sanctioned phrase).

State 2 — Post-signature, pre-payment (quote accepted, deposit invoice sent):

- Above fold: "Proposal signed." prose (text-heading, complete color) → deposit invoice hero card: invoice number (firm numbering scheme), total, due date, "Pay now" primary CTA linking to Stripe hosted URL.
- Invoice status chip (on hero card): StatusPill compact, tone = unpaid or overdue. Warning state: if `invoice.due_date` is past, chip uses `--ss-color-warning` (#6b4f08) background with white text label "OVERDUE". This is the deposit-overdue warning state. See Warning States section.
- One primary CTA on the invoice card. Redundancy ban (Pattern 02): amount is displayed as the invoice amount; do not also say "50% deposit" in a redundant caption.

State 3 — Active engagement (deposit paid, engagement active):

- Above fold: current milestone name (h1, authored from `milestones.name`; if null render "TBD in SOW") → engagement status as prose subtitle (Pattern 01: detail-page state IS the page identity — prose, not pill) → "Latest invoice: {status}" chip.
- Below fold: upcoming milestone list (`TimelineEntry`); consultant block (gate on authored data).
- No progress bar (portal-ux-brief anti-pattern).

State 4 — Safety net (handoff complete):

- Above fold: "Engagement complete." prose → safety net end date (authored `engagements.safety_net_end`; render in natural date format "Ends May 15, 2026" not ISO in this prose context) → "Questions? Email us" link (authored consultant email or `team@smd.services`; no fallback fabrication).
- Below fold: completion invoice card if present.

#### Desktop Adaptation (1280px)

- Two-column: primary content left, consultant block + contact rail right.
- Right rail: consultant authored data (gate on all authored; render nothing if absent).
- Max content width: 1040px centered.
- Invoice status chip migrates from below-CTA position into a status rail in the right column.

#### Loading and Error States

- Loading: skeleton at section level. Full-width bars at heading and body positions. No delay before skeleton shows.
- Error: full-page error with contact link. "We ran into a problem loading your portal. Email us at team@smd.services." Authored consultant email preferred over generic if available. No generic "something went wrong" without a next step.

---

### 2. Quote Detail with Sign (`/portal/quotes/[id]`)

**Exists.** `QuoteDetail.astro` and `portal/quotes/[id].astro` implemented. Active UI-PATTERNS violations documented (Rule 1, Rule 2). Signing view updated to framed-embed spec per Marcus (Round 1).

#### Standard States (non-signing)

Five states (isSent / isSigned / isDeclined / isExpired / isSuperseded):

**State: isSent (unsigned — action surface)**

- Above fold: eyebrow "PROPOSAL" (text-label, mono caps — NOT a pill; Pattern 01 Rule 1 anti-pattern at quotes/[id].astro:207-210) → engagement title (h1, text-display) → scope summary (2-3 problems in plain language, text-body-lg) → total project price (MoneyDisplay, hero size, tabular-nums) → payment structure caption (authored only, exact dollar amounts — "3,500 now · $3,500 at completion" — not percentages) → primary CTA "Review and sign" (full-width, solid primary, thumb zone).
- Below fold: line items (problem description only — no hours, no per-item price; BR-018, BR-042), exclusions list (authored from SOW), timeline section ("TBD in SOW" if not set).
- Quote expiry warning: if `quote.expires_at` is within 72 hours and quote is still in sent state, render a warning callout above the CTA. Callout spec: hairline border in `--ss-color-warning` (#6b4f08), text in `--ss-color-warning`, label "EXPIRES {date and time}". No animated countdown. See Warning States section.

**State: isSigned (post-signature — informational)**

- Drop the primary CTA. Replace with signed confirmation block: "Signed {natural date}." (text-caption, complete color) — single rendering, no redundancy (Pattern 02 fix: remove pill AND confirmation block duplication at quotes/[id].astro:458-497; keep prose only).
- "View your deposit invoice" secondary link (tertiary button or inline text link).

**State: isExpired**

- Status as prose in subtitle: "This proposal expired on {natural date}." (text-heading).
- No CTA. "Contact us to discuss next steps." with authored email link.

**State: isDeclined / isSuperseded**

- Superseded: "A revised proposal is available." with link to new quote (existing implementation at quotes/[id].astro:252-255 — confirmed correct).

#### Framed Signing View (Key Screen Breakdown)

This is the make-or-break signing moment. Marcus (Round 1): "If the portal is cream and dark ink and the SignWell window is white with blue buttons and a different typeface, my brain registers that as 'you're not in Kansas anymore.' That creates hesitation at exactly the moment when I need to be confident enough to sign."

**Framing spec — the signing view is NEVER a popup, modal, or full-screen takeover.**

The signing view is a state within the quote detail page (or a distinct `/portal/quotes/[id]/sign` route — pending decision A). In either case:

**Portal chrome stays visible and intact:**

- `PortalHeader` rendered above the iframe as normal.
- Breadcrumb below header: "← Proposals / {Engagement Title}" (back-linked).
- Contextual heading: "REVIEWING ENGAGEMENT SCOPE" (text-label, mono caps, hairline underline below).

**Pre-signing disclosure line (mandatory):**
Before the iframe renders, one line of text in text-caption / text-secondary color: "The signing form below is provided by SignWell, a document-signing service." This sets the expectation that the visual environment will change. The user is not surprised; they were told. Place this line directly above the iframe, not in a help tooltip.

**SignWell iframe container:**

Mobile (< 768px):

- Scope reminder: collapsible accordion above the iframe, closed by default. Toggle label: "Review scope" (tertiary button, chevron icon). When expanded: shows engagement title, 2–3 problems in plain language, total price. When collapsed: single-line summary "Signing scope for {engagement title}". The client reads the scope first on the quote detail page; the collapsed state here avoids burying the iframe.
- Iframe: full-width, `height: calc(100dvh - var(--portal-header-height) - var(--tab-bar-height) - 48px)`. The 48px accounts for disclosure line + spacing. Avoid fixed pixel heights — iOS Safari chrome height is dynamic.
- Tab bar: remains visible. Signing does not suppress navigation.

Desktop:

- Two-column layout. Left column: SignWell iframe (primary). Right column: scope summary (sticky, not collapsible). Scope summary right column shows: engagement title, line item descriptions, payment structure, "Questions? Email us" link.
- Iframe height: fills the available viewport below header without requiring outer-page scroll.

**Post-signature return:**

- SignWell fires `document.completed` postMessage (or redirect) on completion.
- Portal handles event: transitions to isSigned state (or redirects to `/portal` with in-page confirmation strip).
- Confirmation is rendered within portal chrome — NOT in a separate window. The user never leaves the portal context.
- `aria-live="assertive"` on the confirmation panel (Design Technologist R1 recommendation confirmed).

**Iframe failure fallback:**
"The signing document isn't available right now. Email us at {authored email} and we'll send a direct link." No broken iframe border; replace the iframe container with this prose + email link.

#### Remediation Notes (Existing Screen)

- Pattern 01 violation at line 207-210: eyebrow "Proposal" rendered as pill → fix to text-label eyebrow.
- Pattern 02 violation at lines 458-497: "Signed" pill co-rendered with "Signed {date}" confirmation block → consolidate to single "Signed {natural date}." prose in complete color.
- Pattern 05 violations: 32 arbitrary inline sizes → convert to scale tokens.

---

### 3. Engagement Detail with Parking Lot (`/portal/engagement`)

**Exists.** `portal/engagement/index.astro` implemented. Active Pattern 02 violation documented (pill + prose redundancy at lines 125-131 and 144-148).

**Layout (mobile-first, 375px)**

Above fold:

- Section eyebrow: "ENGAGEMENT" (text-label, mono caps, hairline underline).
- Current milestone name (h1 or h2 — authored; if no in-progress milestone, most recent completed; if none, "TBD in SOW").
- Milestone description (text-body, authored, no fabrication).
- Engagement status as prose subtitle (Pattern 01 Rule 1: drop the pill, keep prose — fix for existing violation at lines 125-131).

Below fold:

- Timeline of milestones: `TimelineEntry` per milestone (name, status, completed_at). Status via dot + label (Pattern 01: single-item card context). Not pill.
- Parking lot section (Phase 5, read-only): gate on `parking_lot_items.length > 0`. Render nothing if empty. Each item shows: description, disposition (plain English — "Added to project" / "Deferred to follow-on" / "Not addressed"), and disposition note if authored.
  - Warning state for stale undispositioned items: if a parking lot item has `disposition = null` for more than 14 days, render a warning chip on the item row. Chip: `StatusPill` compact, tone = warning, label "NEEDS DECISION". See Warning States section. (Note: this warning appears on the admin surface; for the portal read-only view, stale items simply render without disposition tag, which is sufficient — no warning chip needed for the client.)
- Consultant block: gate on authored data.
- Safety net card (if status = safety_net): "Your engagement is in the support period. Ends {natural date}." + contact link. No fabricated timeframe.

**Tab visibility:** Per PRD §9, this tab only shows when an engagement is active. Confirmed: `EngagementProgress` tab rendered conditionally based on engagement existence.

**Remediation notes:**

- Pattern 02 violation at lines 125-131 and 144-148: status pill + prose redundancy → drop pill, keep prose only.
- Pattern 01: detail-page archetype.
- Pattern 05: arbitrary inline sizes throughout.

---

### 4. Invoice Detail — Professional Invoice Format (`/portal/invoices/[id]`)

**Exists (needs update).** `InvoiceDetail.astro` and `portal/invoices/[id].astro` implemented. Active Pattern 02 triple-redundancy violation at lines 450-461. Requires update to match professional invoice register.

Marcus (Round 1): "The deposit invoice should look like an invoice from a professional firm, not a receipt from an app... If the invoice looks like something I could have made in Canva in twenty minutes, I'm going to feel like I overpaid."

#### Invoice Document Structure

The invoice detail page renders a document, not a status card. The layout is editorial, not dashboard. Think: professional invoicing standard (letterhead structure), rendered in the Plainspoken Sign Shop palette.

**Invoice header block:**

- Firm name: "SMD SERVICES" (text-section-h, weight 900, all-caps, Archivo).
- Firm address: street, city, state, zip — authored from system config.
- Client business name and address block (authored from entity record).
- Invoice number: firm numbering scheme (e.g., "INV-2026-004") — NOT a raw UUID, NOT "INV-001" unless that is genuinely the firm's first invoice. The numbering scheme is a system configuration. `JetBrains Mono`, `text-label` size, mono caps.
- Invoice date and due date: "ISSUED April 15, 2026 | DUE April 29, 2026" (text-caption, Archivo Narrow caps).
- Horizontal hairline rule separating header from body.

**Line item table:**

- Column headers: "DESCRIPTION" / "AMOUNT" (text-label, Archivo Narrow caps, hairline underline).
- Line items: full descriptions from authored scope (not internal codes). Example: "Scheduling system design and implementation" not "Item 1". (text-body for description, MoneyDisplay price-row for amount, tabular-nums, right-aligned).
- Horizontal hairline between each row.
- Subtotal, then total: "TOTAL" in text-section-h, weight 900; amount in MoneyDisplay hero size, weight 900, right-aligned. This is the number Marcus reads first.
- No confetti. No congratulatory framing. No "Thanks for your business!" in a speech bubble.

**Payment section (below total):**

- States: isUnpaid / isOverdue / isPaid.
- isUnpaid: "Pay Now — $3,500" primary CTA (full-width, solid primary, 52px height). The amount is on the button. Marcus: "I need the amount on the button. I'm not going to click a pay button to find out how much I owe." Links to `stripe_hosted_url` in new tab.
- isOverdue: Same CTA. Warning callout above the CTA: hairline border in `--ss-color-warning`, label "OVERDUE — Due {natural date}", text in `--ss-color-warning`. See Warning States.
- isPaid: "Paid {natural date}." prose (text-caption, complete color). ONE rendering. Pattern 02 fix: remove "Paid" pill AND "Paid in full" caption at lines 450-461; keep only this prose. "Download receipt" ghost button if receipt URL exists.
- ACH note: text-caption, muted color, per BR-036.

**Footer block:**

- "Questions? Contact us at {authored email or phone}." Not a generic contact form link. A direct channel.
- Payment terms restatement: "Payment terms: net 14 days." (authored from engagement config).

**Print-friendly and PDF:**

- Browser print: the invoice layout must render cleanly without portal chrome. Use `@media print` CSS to suppress tab bar, header, and sidebar. Content column expands full page width on print. Print action: ghost button "Print invoice" (browser native print dialog).
- Downloadable PDF: "Download PDF" ghost button linking to a generated PDF or uploaded invoice PDF. If no PDF is available, suppress this button entirely (empty-state-pattern: render nothing, not a broken download link).

**Desktop adaptation:**

- Invoice document rendered in a constrained column (max-width ~720px, centered within the 1040px content area). Gives the document a genuine paper-document feel. Wide margins are intentional and professional.
- Print action and download action appear as a row of ghost buttons above the invoice document.

---

### 5. Admin Engagement Detail (`/admin/engagements/[id]`)

**Exists.** Desktop-first; dense information acceptable per PRD §14. No substantive changes from Round 1. Confirming component mapping for new items.

**New components mapped to this route:**

- `TimeEntryLog.astro` (New): rendered within the Time Entries collapsible panel, left column.
- `ParkingLotPanel.astro` (New, editable mode): rendered within the Parking Lot collapsible panel, left column. `readonly: false` prop. Admin can disposition items here.

**Warning state for parking lot:** Undispositioned parking lot items older than 14 days (`disposition = null`, `requested_at` is >14d ago) render a warning indicator. Within the admin engagement detail, the parking lot section header shows a count chip: "3 items need review" using `--ss-color-warning` background. Individual rows render the item description with a "STALE" mono-caps tag in warning color. This is the parking-lot-stale-item warning state. See Warning States section.

**Deposit/payment status:** Per Marcus (Round 1) applied to admin as well: payment status is not buried. The invoice panel in the right column shows current invoice status at a glance — no navigation required to confirm deposit cleared.

---

## Warning States

The Brand Strategist (Round 1) proposed `--ss-color-warning: #6b4f08` (deep amber-ochre, 6.72:1 on cream, AA-compliant). This token is pending PR. The following screens require concrete warning-state specs once the token is merged.

**Warning state contexts (three confirmed uses):**

### 1. Quote Near Expiry

**Screen:** `/portal/quotes/[id]`, state = isSent.
**Trigger:** `quote.expires_at` is within 72 hours and `quote.status = sent`.
**Visual spec:** Callout strip above the "Review and sign" CTA. Full-width, hairline left-border (3px, `--ss-color-warning`), background transparent (cream, consistent with identity — no filled warning backgrounds). Text in `--ss-color-warning`: "PROPOSAL EXPIRES {day, date at time}" (text-caption, mono caps for the label part, Archivo Narrow). No animated countdown clock. Plain text is sufficient and avoids the urgency-manipulation pattern Marcus would distrust.
**ARIA:** `role="alert"` on the callout (renders on page load when condition is true; screen readers announce).
**Does not appear:** in isSigned, isDeclined, isExpired, or isSuperseded states.

### 2. Deposit Overdue

**Screen:** `/portal/invoices/[id]`, state = isOverdue. Also: `/portal` dashboard State 2 invoice chip.
**Trigger:** `invoice.due_date` is past and `invoice.status = unpaid`.
**Visual spec (invoice detail):** Callout strip above the "Pay Now" CTA. Same hairline-left-border treatment as quote expiry. Text in `--ss-color-warning`: "OVERDUE — DUE {natural date}". "Pay Now — ${amount}" CTA unchanged (still primary, still burnt orange). The warning does not suppress the primary CTA. It contextualizes it.
**Visual spec (dashboard chip):** `StatusPill` compact variant, `--ss-color-warning` background, white text label "OVERDUE". This is the exception to the invoice-chip standard: overdue invoices use warning background, not attention/primary background.
**Admin side:** Admin engagement detail invoice panel shows overdue status chip in warning color. No separate screen needed; the chip in the right-column invoice panel is sufficient.

### 3. Parking Lot Stale Item (Admin Only)

**Screen:** `/admin/engagements/[id]`, parking lot panel.
**Trigger:** `parking_lot_items.disposition = null` AND `requested_at` is more than 14 days ago.
**Visual spec:** Section header chip "X items need review" in `--ss-color-warning`. Individual stale rows: "STALE" mono-caps tag (text-label, Archivo Narrow) at row-right in `--ss-color-warning`. Non-stale undispositioned rows: no warning tag (they are simply undispositioned, not yet overdue for review).
**Portal read-only view:** No warning shown to client for stale parking lot items. The client sees disposition or absence. Staleness is an internal operational concern.
**ARIA:** Section header chip is a `<span>` with visible text; no live region needed (admin loads the page fresh; the warning is static on load).

---

## Navigation Model

### `smd.services` — Marketing

**Pattern:** Pyramid with persistent top nav (NN/g §1.4).

- Persistent top nav: Logo left, primary links center/right, "Book a call" primary CTA rightmost.
- Footer: contact link, admin sign-in link (low-visibility).
- Max depth: 2.
- No portal or admin chrome on marketing pages.

---

### `portal.smd.services` — Client Portal

**Pattern:** Persistent bottom tabs on mobile; top tab strip on desktop. Source of authority: **NAVIGATION.md §4.4**.

**Why persistent tabs over hamburger:**
Tab visibility = scannability of capability without any additional taps. Marcus (Round 1) said explicitly: "I should be able to look at this for ten seconds and understand what's here and what I need to do." A hamburger hides destination options behind a tap, forcing the user to make a tap just to learn what navigation exists. Persistent tabs render the full navigation inventory at all times. Scannability without interaction is the decisive advantage, particularly for a user who knows he has limited attention and time.

This is the live architectural decision per NAVIGATION.md §4.4. It is not under review.

**Icon set (Material Symbols Outlined, FILL 0, wght 400, opsz 24):**

| Tab       | Icon name      | Rationale                                                                                                                        |
| --------- | -------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Home      | `home`         | Universal convention. No ambiguity.                                                                                              |
| Proposals | `description`  | Document with content lines. More specific than `article`. Clearly a proposal/document context.                                  |
| Invoices  | `receipt_long` | Bill/receipt register. Distinct from generic document. Marcus knows what a receipt is.                                           |
| Progress  | `assignment`   | Clipboard with checklist. Engagement progress / task-tracking register. `timeline` was considered but reads more like analytics. |
| Documents | `folder_open`  | Standard file/folder metaphor for a document library.                                                                            |

Note: `work` was proposed for Proposals by the Brand Strategist (Round 1). `description` is preferred because `work` reads as "work/job" in general, not as "a document you need to review and sign." Tab label "Proposals" + `description` icon is unambiguous.

**Mobile chrome (375px):**

- Persistent tab bar fixed at bottom of viewport, 64px height (accounts for icon 24px + label text-label 12px + safe area inset).
- Four tabs in MVP: Home / Proposals / Invoices / Progress. Documents as fifth tab if post-launch data shows it is high-frequency; otherwise accessible via a row on the Home dashboard or within Engagement. Keep at four for MVP.
- Tab labels: short nouns, no verbs. "Home" / "Proposals" / "Invoices" / "Progress".
- Active tab: solid variant of icon (FILL 1) + primary color label.
- Inactive tab: outline variant (FILL 0) + muted label.
- **Tab visibility rule (PRD §9):** Tabs appear conditionally based on data existence:
  - Home: always shown.
  - Proposals: shown when at least one quote exists for this client.
  - Invoices: shown when at least one invoice exists.
  - Progress: shown when an active engagement exists.
  - Documents: shown when at least one document is uploaded.
- No hamburger, no sidebar, no drawer (portal-ux-brief anti-pattern list).

**Desktop chrome (1280px):**

- Top sticky masthead: "SMD SERVICES" left (Archivo, weight 900, caps, text-label scale) + session context (client name, ISO date) right + logout link far right.
- Below masthead: horizontal tab row (same four tabs, rendered as nav bar, not bottom bar). Icon + label inline. Active tab: 2px bottom border in primary color.
- Max content width: 1040px centered.
- No persistent sidebar (portal-ux-brief anti-pattern: sidebar navigation banned).

**Back navigation:**

- Detail pages (quotes/[id], invoices/[id]) show back link: "← Proposals" / "← Invoices" (text-secondary, linked, text-caption).
- Persistent tabs always visible; active tab indicates current section.
- No breadcrumbs in portal (admin pattern; portal audience does not benefit from hierarchical location awareness).

**Billing is not a setting.** Per Marcus (Round 1): billing information is visible on the Home dashboard as a chip on the engagement summary row, and as the top content on the Invoices tab. There is no Settings → Billing navigation path. If an admin ever considers adding billing to a settings screen, this is an anti-pattern.

---

### `admin.smd.services` — Admin Console

**Pattern:** Hub-and-spoke with tabs (NAVIGATION.md §3.2; hub at `/admin`, spokes at entities, follow-ups, analytics).

**Desktop chrome:**

- Sticky top nav: "SMD SERVICES · ADMIN" left (Archivo weight 900, caps) + primary nav tabs center (Dashboard / Pipeline / Entities / Follow-ups / Analytics / Generators) + session email + sign-out right.
- Sub-navigation: breadcrumbs on all entity-scoped deep pages.
- No mobile optimization required (PRD §14).

**Breadcrumb spec (unchanged from Round 1):**

- Format: "Dashboard / Entities / {Entity Name} / {Leaf label}"
- Chevron separators, text-caption, Archivo Narrow.
- Last crumb: text-primary, not linked. Prior crumbs: text-secondary, linked.

---

## User Flows

### Flow 1: Magic-link login → First portal view → See scope

**Entry context:** Marcus receives the portal invitation email on his phone. Between service calls.

1. Marcus taps "View your proposal" in the Resend email. Magic link token in URL.
2. Browser opens `/auth/verify?token={token}`.
3. Server validates token → marks `magic_links.used_at` → creates KV session → sets `__Host-portal_session` → redirects 302 to `/portal`.
4. No intermediate "you are now logged in" screen. Redirect is immediate.
5. Marcus lands on `/portal`, State 1 (pre-signature).
6. Above fold (375px): eyebrow → engagement title → scope summary → project total → payment structure caption → "Review and sign" CTA. No scrolling required. CTA top edge sits at y ≤ 560px (within 564px usable area).
7. Persistent tab bar visible at bottom: Home (active) / Proposals / — (other tabs suppressed until data exists).

**Expired token path (EC-002):**
3a. Token expired (>15 min). Server redirects to `/auth/portal-login?error=expired`.
4a. `MagicLinkExpiredForm.astro` renders: "That link has expired. Enter your email to get a new one." Single email input, primary submit. No account creation. No jargon.
5a. Marcus enters email. `POST /api/auth/magic-link`. Response always 200 (no email enumeration, BR-027-030).
6a. "Check your email for a new link." in-page confirmation. No redirect.

**Already-used token path (EC-003):**
Treat identical to expired. Same `/auth/portal-login?error=used` destination. Same friendly message. Do not expose "already used" as a distinct error (prevents session-fixation fishing).

---

### Flow 2: Quote review → Framed signing → Return to portal

**Entry context:** Marcus has arrived at the portal (Flow 1 complete). On his phone.

1. Marcus sees "Review and sign" CTA above the fold. Taps it.
2. Navigation to `/portal/quotes/[id]` (quote detail, isSent state).
3. Above fold: engagement title → scope summary → total → payment caption → "Review and sign" CTA.
4. Below fold: full line-item descriptions, exclusions, timeline.
5. Marcus taps "Review and sign."
6. **Signing view activates (within portal chrome):**
   - Portal header remains visible.
   - Breadcrumb: "← Proposals / {Engagement Title}".
   - Contextual heading: "REVIEWING ENGAGEMENT SCOPE" (text-label, mono caps).
   - Pre-signing disclosure: "The signing form below is provided by SignWell, a document-signing service." (text-caption, text-secondary color). One line. Before the iframe.
   - Scope accordion (mobile): collapsed by default. "Review scope" toggle above iframe.
   - SignWell iframe: full-width, full remaining viewport height. No visual break in page structure — the iframe is a content region within the portal frame, not a foreign page.
7. Marcus signs. SignWell fires `document.completed`.
8. Webhook processes (server-side). Portal detects completion.
9. Marcus sees isSigned state rendered within portal chrome. "Signed {natural date}." prose. "View your deposit invoice" secondary link.
10. OR: redirect to `/portal` home in State 2 with in-page strip: "Your proposal is signed. Your deposit invoice is ready below." (dismissible on tap, anchored in-page — not a toast, which Marcus may miss).
11. Dashboard State 2: deposit invoice hero card with "Pay now — $3,500" primary CTA.

---

### Flow 3: Parking lot review → Comment / Accept / Defer (Admin)

**Entry context:** Scott (admin), MacBook, nearing handoff.

(Flow unchanged from Round 1. Warning state addition: if any items are flagged as stale — disposition = null, >14 days — the section header chip now shows "X items need review" in warning color. This is the only change to the parking lot admin flow.)

---

## Form Patterns

### Input Styles

- Text inputs: full-width, border `--ss-color-border`, 0 radius, text-body, text-primary, surface background.
- Label: above input always (WCAG §4.1). text-label token, text-primary. Required: "(required)" in label text.
- Focus state: 2px ring, 2px offset, `--ss-color-action`.
- Disabled: border-subtle, text-muted, `not-allowed` cursor.

### Validation Timing

- On submit for required fields.
- On blur for format-critical fields (email, phone).
- Real-time for quote builder line item totals.

### Error Placement

- Field-level: directly below input. text-caption, error color. aria-describedby. Complete sentence.
- Form-level (admin): top-of-form strip, sticks until condition resolves.
- Portal single-field forms: inline below input, friendly language.

### Required Indicators

- Label text: "{Field name} (required)".
- aria-required="true" on input.
- Optional fields: no "(optional)" unless more optional than required (NN/g convention).

### Touch Target and Tap Affordance

Marcus's use context — phone in truck, possible sun glare, moderate finger size — requires rigorous touch target discipline. The standard is 44×44px per WCAG 2.5.5 (AAA), adopted as a hard floor for this portal because the persona requires it.

**Minimum target sizes (all interactive portal elements):**

| Element                        | Minimum size                        | Implementation note                                                                  |
| ------------------------------ | ----------------------------------- | ------------------------------------------------------------------------------------ |
| Tab bar items                  | 64px height (full bar height)       | Full bar height ensures entire bottom zone is tappable, not just the icon+label area |
| Primary CTA buttons            | 52px height, full-width             | Full-width eliminates off-target taps on mobile                                      |
| Ghost / secondary buttons      | 44px height minimum                 | Even text-only ghost buttons need explicit min-height                                |
| List row tap targets           | Full-row width, 56px height minimum | Extend tap zone beyond visible text; do not restrict to a small icon or label        |
| Back / crumb links             | 44px height minimum via padding     | Small link text padded to 44px tall tap zone                                         |
| Disclosure toggles (accordion) | 44px height minimum                 | "Review scope" accordion toggle on signing view                                      |
| Overflow menu triggers (admin) | 44px × 44px                         | Kebab icon must be padded to square 44px                                             |
| Icon buttons in header         | 44px × 44px                         | Contact icons in PortalHeader                                                        |
| Pagination / load-more         | 44px height                         | If used in lists                                                                     |

**Implementation pattern for undersized elements:**
Use transparent padding to reach 44px without altering visual size. Example for a back-link:

```
min-height: 44px;
display: inline-flex;
align-items: center;
padding-block: 10px;  /* lifts the tap zone above and below the text */
```

**What this is NOT:** This is not an instruction to make every button visually oversized. A ghost button at 44px height renders with standard internal padding and the correct type scale. The 44px minimum is the outer tap surface, not the visible fill height.

**Sun glare note:** The Plainspoken Sign Shop identity — dark ink (15.91:1 contrast) on cream, primary CTA in burnt orange (4.63:1 white text on primary bg) — holds up in direct sunlight better than soft gray SaaS palettes. No additional affordance needed for glare beyond the existing token contrast values and confirmed by Marcus (Round 1: "The dark ink on cream should be fine if the contrast is actually good").

### Keyboard Navigation (Quote Builder)

Per PRD §9 and §14: Tab between fields, Enter to add a new row, Delete/Backspace on empty row to remove it. React island only (`client:load` on admin quote builder page only — does not inflate portal JS bundle).

---

## Feedback Patterns

### Toast and Notification Style

- Transient success toasts: portal only, for low-stakes confirmations where state change is visible on screen.
- Toast position: bottom-center on mobile (above tab bar — tab bar is fixed; toast must not overlap it), top-right on desktop.
- Duration: 4 seconds auto-dismiss; tap to dismiss early.
- No celebratory animation.
- Text: past tense, concrete. "Link sent." not "Sent successfully!"

### Success Confirmations

- Portal signing: in-page prose replacement (isSigned state IS the confirmation). No toast.
- Invoice paid: page state on next load. A Resend email fires (client's primary record).
- Admin parking lot disposition: inline row update. No toast.
- Admin stage transition: brief top-of-page strip, auto-dismisses after 4 seconds.

### Destructive Confirmations

- Pattern: inline-expand within action row or narrowly scoped dialog. Not full-page modal.
- Text: concrete. "Cancel this engagement? Milestones and invoices remain on file."
- Two actions: destructive CTA (error fill, specific label) + neutral "Go back".
- Never auto-confirm.

### Progress Indicators

- Page loading: skeleton at section level (portal), skeleton at panel level (admin). No full-page spinner.
- Form submit: disable button, replace label with spinner + "Sending...". Prevents double-submit.
- SignWell iframe: "Loading signing document..." centered in iframe container. Spinner. Not skeleton.
- R2 upload: linear progress bar or indeterminate spinner. "Uploading..." label. On complete: success strip.
- File download: no progress indicator; presigned R2 URL opens in new tab.

---

### Email Touchpoint Inventory

Marcus (Round 1): "The invitation email earns the click or doesn't." Email is not a secondary channel — it is the primary activation surface. For Marcus, the email is how he knows the portal exists, how he returns to sign, how he confirms payment. Every transactional email must earn trust, reduce friction, and include a single action.

**Trust signal requirements (all emails):**

- From: `team@smd.services` — recognizable domain, not a third-party ESP subdomain.
- Reply-to: authored consultant email — replies go to a human.
- Plain-text fallback: required on every email. Marcus may be on an email client that blocks HTML or images.
- No marketing graphics, no unsubscribe footer that implies a newsletter relationship. These are transactional emails, not marketing.
- Subject line must include client business name or a direct reference to the prior interaction. Generic subjects fail the click test.

**Inventory:**

#### Email 1 — Portal Invitation (Quote Sent)

**Trigger:** Admin sends quote via `POST /api/admin/quotes/:id/send` (US-009).
**Recipient:** Client primary contact (email from `entity_contacts` where `is_primary = true`).
**Subject:** `{Business Name} — your proposal from SMD Services`
**Body (key elements):**

- Salutation with contact name (authored from `entity_contacts.name` — do not use "Business Owner" fallback; if `name` is null, no salutation rather than fabricated name).
- One-line scope reference: the engagement title or a plain-language summary of the top problem discussed. Must sound specific to this client, not generic.
- Project total (plain text — "$7,500 total / $3,750 deposit at signing").
- Single CTA button: "View Your Proposal" — magic link URL to `/auth/verify?token={token}`.
- Magic link expiry notice (text-only): "This link expires in 15 minutes. If it expires, visit {portal-login-URL} to request a new one."
- No secondary links. No "Learn more about SMD Services." No footer marketing copy.
  **Plain-text fallback:** "View your proposal: {magic-link-url}. Link expires in 15 minutes. If expired, request a new one at {portal-login-url}."
  **Trust signals:** Business name in subject. Specific scope reference. Real dollar amounts. No account creation required (stated explicitly or implied by frictionless magic link).

#### Email 2 — Quote Signed by Client (Confirmation to Client)

**Trigger:** SignWell `document.completed` webhook received, `quotes.status` updated to `accepted` (US-011).
**Recipient:** Client primary contact.
**Subject:** `{Business Name} — proposal signed. Deposit invoice enclosed.`
**Body:**

- Confirmation: "Your proposal for {engagement title} is signed. A copy of the signed document will be available in your portal."
- Deposit invoice summary: invoice number, amount, due date, payment link (Stripe hosted URL).
- Single CTA: "Pay Deposit — $3,750" (Stripe hosted URL).
- Contact information for questions.
  **Plain-text fallback:** Text version of invoice summary + Stripe URL.
  **Trust signals:** Engagement title (specific). Deposit amount (exact dollars). Invoice number (professional format). Direct contact information.

#### Email 3 — Quote Countersigned / Firm Acknowledgment

**Trigger:** Admin reviews and confirms the signed quote (or automatically on webhook receipt if no manual review step in current flow).
**Recipient:** Client primary contact.
**Subject:** `{Business Name} — we've received your signed proposal`
**Body:**

- Acknowledgment that the firm has the signed document.
- What happens next: authored from engagement record (`engagement.next_step_text`) or, if null, no "what happens next" section (empty-state-pattern — do not fabricate "We'll be in touch soon.").
- Contact information.
  **Plain-text fallback:** Plain text with same content.
  **Trust signals:** Named engagement title. No fabricated next-step copy (P0 per CLAUDE.md).

#### Email 4 — Invoice Issued

**Trigger:** Admin creates a new invoice (deposit or milestone or completion).
**Recipient:** Client primary contact.
**Subject:** `{Business Name} — invoice #{invoice_number} from SMD Services`
**Body:**

- Invoice summary: invoice number, amount, due date, description (line items from authored data).
- Single CTA: "Pay Invoice — ${amount}" (Stripe hosted URL).
- ACH note per BR-036.
  **Plain-text fallback:** Plain text invoice summary + Stripe URL + ACH note.
  **Trust signals:** Invoice number. Specific amount. Firm name and address in header.

#### Email 5 — Payment Confirmed

**Trigger:** Stripe `invoice.paid` webhook received (US-013).
**Recipient:** Client primary contact.
**Subject:** `{Business Name} — payment received ($3,750)`
**Body:**

- Confirmation: "We received your payment of $3,750 for invoice #{invoice_number}."
- Link to portal to view receipt: "View your receipt in the portal" (portal URL, not magic link — client is already authenticated or can request a new link).
- Next milestone or next step (authored from engagement record; if null, omit).
  **Plain-text fallback:** "Payment of $3,750 received. Invoice #{number}. View receipt at {portal-url}."
  **Trust signals:** Specific dollar amount. Invoice number. Receipt accessibility without hunting through a portal.

#### Email 6 — Parking Lot Item Needs Decision

**Trigger:** Admin adds a parking lot item and marks it for client visibility (Phase 5 feature; item `visible_to_client = true`).
**Recipient:** Client primary contact.
**Subject:** `{Business Name} — one item needs your input`
**Body:**

- Plain language description of the item.
- Three-way choice framing: plain language, no anchoring toward any option. "We found this during the engagement and wanted to flag it. You can decide: add it to the current project, defer it to a future engagement, or let it go. There's no wrong answer."
- CTA: "Review in portal" (portal engagement page, authenticated).
  **Plain-text fallback:** Full text description + portal URL.
  **Trust signals:** Specific item description (not generic). Non-pressured framing (Marcus: "I need to believe that what's in the parking lot is genuinely 'we found this and you should know about it,' not 'here's how we get another invoice'").

#### Email 7 — Engagement Complete Summary

**Trigger:** Admin advances engagement status to `complete` (handoff phase).
**Recipient:** Client primary contact.
**Subject:** `{Business Name} — project complete`
**Body:**

- Completion statement: "Your {engagement title} engagement is complete."
- Summary of what was delivered: authored from engagement record (list of completed milestones with their plain-language names). Do NOT use generic phrases ("we streamlined your operations"). If milestone names are not authored in plain language, render milestone titles only — no narrative fabrication.
- What's in their portal now: document library count if documents were uploaded.
- Safety net period: "Your support period ends {natural date}." (authored `safety_net_end`). If `safety_net_end` is null, omit. Do not fabricate a date.
- Contact for questions.
  **Plain-text fallback:** Plain text with same milestone list and dates.
  **Trust signals:** Named milestones (specific, not generic). Exact dates. No fabricated "great outcomes" language.

#### Email 8 — Magic Link (Re-auth)

**Trigger:** Client requests a new magic link from `/auth/portal-login`.
**Recipient:** The email address entered by the client.
**Subject:** `Sign in to your SMD Services portal`
**Body:**

- One sentence: "Here is your portal sign-in link."
- Single CTA: "Sign in" (magic link URL).
- Expiry notice: "This link expires in 15 minutes."
  **Plain-text fallback:** Magic link URL.
  **Trust signals:** Domain recognition (`smd.services`). No account password required. Short expiry communicated (not hidden).

---

## Responsive Strategy

### Portal — Mobile-First

**Primary breakpoint: 375px (phone, updated from 390px to match iPhone 13 mini / common minimum)**

- Single-column throughout.
- Persistent tab bar fixed at bottom, 64px height.
- Content scrolls behind tab bar — bottom padding = tab bar height + `space-stack` (16px).
- Above-fold constraint: primary CTA must be visible without scroll per pixel budget documented in Dashboard breakdown above.
- Signing iframe: `height: calc(100dvh - var(--portal-header-height) - var(--tab-bar-height) - 48px)`. Not fixed pixels.
- No horizontal scroll (WCAG 1.4.10 reflow).

**Desktop adaptation: 1280px**

- Max content width: 1040px centered.
- Two-column layout on dashboard and quote detail (when iframe not active).
- Tab bar migrates to horizontal top nav strip below masthead.
- Invoice document: constrained column (~720px) within 1040px for professional document register.

**Touch target compliance:** Per Touch Target subsection above. Every interactive element meets 44×44px.

### Admin — Desktop-First

**Primary breakpoint: ≥1024px**

- Sticky top nav with tab links.
- Side-by-side panels on entity and engagement detail.
- Dense tables with `space-row` (12px) vertical rhythm.
- No mobile optimization in MVP. Graceful degradation acceptable.

**Keyboard navigation (quote builder):**

- Tab order: problem dropdown → description → hours → next row.
- Enter on hours: add new row.
- Backspace/Delete on empty hours: remove row.
- React island (`client:load`, admin quote builder only).

### Marketing — Standard Responsive

- Breakpoints: 375px / 768px / 1280px.
- Standard patterns; no portal constraints apply.

---

## Open Design Questions and Gaps

1. **Signing route disambiguation.** Is `/portal/quotes/[id]/sign` a distinct URL or a state within `quotes/[id]`? The framed-embed signing spec in this document is compatible with either approach. If deep-linking from the invitation email directly to the signing context is required (which PRD §7 Step 5 implies), a distinct route is necessary. Recommend resolving before Phase 2 ships. Current: state within `quotes/[id]`.

2. **Dashboard tab visibility gating implementation.** Conditional tab rendering based on data presence needs documented test coverage: pre-signature shows Proposals only; post-signing shows Invoices; post-activation shows Progress; post-upload shows Documents. Logic must be server-side (Astro SSR) to avoid tab flicker on load.

3. **"What happens next" content reconciliation.** UX-004 holding phrase ("Your proposal is being prepared.") applies pre-quote. Post-quote, authored `next_step_text` or nothing. No three-step fabricated explainer. Confirmed.

4. **Champion portal access (UX-001): DEFERRED — Phase 4.** `engagement_contacts` data model exists with champion role. Phase 4 adds second magic-link invitation flow for champion. No MVP screens required.

5. **Parking lot portal view timing.** Items appear in the portal ONLY after admin has dispositioned them (or flagged `visible_to_client = true` for Phase 5 client notification). No undispositioned items visible to client during engagement.

6. **UI-PATTERNS.md Rule 6 documentation correction.** Rule 6 documents `space-section: 32px` and `space-card: 24px`. Live compiled token values are 48px and 32px. The documentation should be corrected to match the compiled token. This document (Round 2) uses the live values. Tracking as a follow-on issue; not blocking for Round 2.

7. **Invoice numbering scheme.** The invoice detail spec calls for a firm numbering scheme (e.g., "INV-2026-004"). This requires a system-level counter or a config that is not a raw UUID or "INV-001" unless genuinely the first invoice. The DB schema and invoice creation flow need to implement a human-readable reference number. If this is not yet implemented, the invoice detail must render whatever reference the system generates rather than a fabricated number — but the design goal is a professional numbering format.

8. **`warning` token merge gate.** Three concrete warning states are specified in this document. All depend on `--ss-color-warning: #6b4f08` being merged. Until the token PR is merged, these states should use a placeholder (text in text-secondary or text-muted) so they do not silently fall back to `--ss-color-attention` (burnt orange) which conflicts with the primary CTA color.

9. **Pattern 08 (admin) vs. portal.** Confirmed: Pattern 08 (overflow menus) applies to admin list surfaces only. Portal list surfaces use master-detail pattern; no row-level overflow menu needed.

---

## Appendix: Email Touchpoint Quick Reference

| #   | Email                    | Trigger              | Recipient      | Primary CTA             | Plain-text required |
| --- | ------------------------ | -------------------- | -------------- | ----------------------- | ------------------- |
| 1   | Portal invitation        | Quote sent           | Client primary | View Your Proposal      | Yes                 |
| 2   | Proposal signed (client) | SignWell webhook     | Client primary | Pay Deposit — ${amount} | Yes                 |
| 3   | Countersigned / Firm ACK | Quote accepted       | Client primary | (no CTA or portal link) | Yes                 |
| 4   | Invoice issued           | Invoice created      | Client primary | Pay Invoice — ${amount} | Yes                 |
| 5   | Payment confirmed        | Stripe webhook       | Client primary | View receipt (portal)   | Yes                 |
| 6   | Parking lot item         | Admin flags visible  | Client primary | Review in portal        | Yes                 |
| 7   | Engagement complete      | Admin marks complete | Client primary | (portal link)           | Yes                 |
| 8   | Magic link re-auth       | Client requests link | Email entered  | Sign in                 | Yes                 |

**All eight emails:** no marketing graphics, no unsubscribe footer implying newsletter, from `team@smd.services`, reply-to authored consultant email.
