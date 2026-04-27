# Interaction Designer Contribution - Design Brief Round 3 (Final)

**Author:** Interaction Designer (Sonnet 4.6)
**Date:** 2026-04-26
**Design Maturity:** Full system
**Status:** Final after 3 rounds

---

## Changes from Round 2

1. **Tab affordance: icons+labels confirmed as mandatory on all viewports.** Round 2 left a gap — mobile bar received icons, desktop remained label-only. Marcus's Round 2 reaction to the `description` icon ("what does 'description' mean?") closes the question. Final position: persistent labels under every icon on all viewports. Cite: NN/g "Icon Usability" (https://www.nngroup.com/articles/icon-usability/). Icon names corrected: `description` → `work` for Proposals; `assignment` → `timeline` for Progress (Design Tech R2 used `receipt_long` for Progress which is the same as the Invoices icon — corrected here). Full rationale in §Navigation Model.

2. **Email content authority specified with Authoring Workflow subsection.** Round 2 catalogued 8 emails with subject lines and body intent. This round adds: who authors what, which fields are system-prefilled, which sentences require Captain review. Brand Strategist's voice rules apply to all authored prose. See §Email Touchpoint Inventory: Authoring Workflow.

3. **Parking lot: respect language and `ParkingLotPanel.astro` item spec.** Marcus's Round 2 reaction — "homework not collaboration" — requires each item to carry authored prose at four levels (title, decision framing, options with rationale, disposition). Status language changed from codes to human dispositions: "decided", "deferred to call", "out of scope". Concrete rendering spec for `ParkingLotPanel.astro` added.

4. **Stale visit recovery subsection added.** Marcus explicitly stated he checks the portal a few times a week, not daily. Home screen must orient him in under 3 seconds on return. Added "Last activity" banner, bell-icon count, "What's new since you were here" section with authored event prose, and cream-paper empty state.

5. **Mobile keyboard / form behavior added to Form Patterns.** Quote builder React island on mobile triggers keyboard pop-up that obscures inputs. Specified: scroll-into-view, keyboard dismissal, autocomplete attributes, `inputmode` hints for money and date fields.

6. **Email timing rules specified.** Quote-signed → countersigned fires within 5 minutes. Parking-lot-needs-decision batches to once per day. Per-email timing column added to inventory table.

7. **Pre-signing prep screen specified.** Marcus's "phishing-energy moment" at the SignWell iframe. New screen: before the iframe loads, one page summarizes total amount, authored deliverables list, legal acknowledgments. Sign button only enables after Marcus scrolls past the summary. References Pattern 03 (button hierarchy).

8. **Admin desktop-first breakpoints documented.** Admin assumes ≥1024px. Below 1024px shows a banner but does not break. Critical admin actions that remain mobile-functional specified.

9. **Champion access Appendix added (Phase 4 Preview).** Read-only engagement + parking lot access, separate magic link, no quote/invoice access. Specified now so Phase 1–3 decisions do not close off the architecture.

10. **Screen inventory re-verified.** Every PRD §8 feature traced to a screen. Every screen traced to a feature. Warning token corrected throughout: Brand Strategist finalized `--ss-color-warning: #7a5800` in Round 2 (not `#6b4f08`); all references updated.

11. **Icon set corrected.** Design Tech R2 `PortalTabs.astro` update spec contained two errors: `description` was mapped to Invoices when Invoices uses `receipt_long`, and `receipt_long` appeared for both Invoices and Progress. Canonical set resolved here with rationale.

12. **Warning token reference corrected.** Brand Strategist revised `--ss-color-warning` to `#7a5800` (7.14:1, AAA) in Round 2. All warning state specs updated from `#6b4f08` to `#7a5800`.

---

## Table of Contents

1. [Screen Inventory](#1-screen-inventory)
2. [Key Screen Breakdowns](#2-key-screen-breakdowns)
3. [Warning States](#3-warning-states)
4. [Navigation Model](#4-navigation-model)
5. [User Flows](#5-user-flows)
6. [Form Patterns](#6-form-patterns)
7. [Feedback Patterns](#7-feedback-patterns)
8. [Responsive Strategy](#8-responsive-strategy)
9. [Appendix A: Champion Access — Phase 4 Preview](#appendix-a-champion-access--phase-4-preview)

---

## 1. Screen Inventory

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

Auth entry points:

| URL                  | Purpose                                         | Primary Action | PRD Feature        | Status |
| -------------------- | ----------------------------------------------- | -------------- | ------------------ | ------ |
| `/auth/login`        | Admin login (email + password / magic link)     | Sign in        | US-016             | Exists |
| `/auth/portal-login` | Client magic-link request                       | Request link   | US-010, BR-027     | Exists |
| `/auth/verify`       | Magic-link token consumption + session creation | Auto-redirect  | US-010, BR-028-030 | Exists |

---

### `portal.smd.services` — Client Portal

| URL                                          | Purpose                                                                                                | Primary Action                                  | PRD Feature                                  | Component(s)                                                        | Status                           |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------ | ----------------------------------------------- | -------------------------------------------- | ------------------------------------------------------------------- | -------------------------------- |
| `/portal`                                    | Dashboard — state-responsive home; proposal / engagement / safety-net views                            | Review & Sign (pre-sign) or Pay Now (post-sign) | US-010, US-011, US-013; PRD §7 Steps 3-7; §9 | `PortalHomeDashboard.astro`                                         | Exists                           |
| `/portal/quotes`                             | Proposal list                                                                                          | Row click to detail                             | US-011; PRD §9                               | `QuoteList.astro`                                                   | Exists                           |
| `/portal/quotes/[id]`                        | Quote detail — scope, price, pre-signing states; 5-state machine                                       | Review & Sign (sent state)                      | US-011, BR-018, BR-042                       | `QuoteDetail.astro`                                                 | Exists                           |
| `/portal/quotes/[id]` → pre-signing state    | Pre-signing prep screen — summary of total, deliverables, acknowledgments; Sign button gated on scroll | (gate → reveal iframe)                          | US-011, EC-002, UX-002                       | `PreSigningPrep.astro` (New)                                        | New                              |
| `/portal/quotes/[id]` → signing state        | Signing surface — framed SignWell iframe within portal chrome                                          | (SignWell iframe action)                        | US-011, EC-002, UX-002                       | `SigningView.astro` (New)                                           | New (component)                  |
| `/portal/invoices`                           | Invoice list                                                                                           | Row click to detail                             | US-013, US-012; PRD §9                       | `InvoicesList.astro`                                                | Exists                           |
| `/portal/invoices/[id]`                      | Invoice detail — professional invoice format, line items, payment, PDF                                 | Pay Now (unpaid state)                          | US-012, US-013, BR-036                       | `InvoiceDetail.astro`                                               | Exists (needs update)            |
| `/portal/documents`                          | Document library — flat list of engagement files                                                       | Download / Open                                 | PRD §7 Step 8; §9                            | `Documents.astro`                                                   | Exists                           |
| `/portal/engagement`                         | Engagement progress — milestone timeline, parking lot (Phase 5 read-only)                              | (informational)                                 | US-014, US-015; PRD §7 Step 7; Phase 5       | `EngagementProgress.astro`, `ParkingLotPanel.astro` (New, readonly) | Exists (portal); New (component) |
| `/auth/portal-login` with expired/used token | Magic-link recovery form                                                                               | Request new link                                | US-010, BR-027                               | `MagicLinkExpiredForm.astro` (New)                                  | New (component)                  |

**Note A — Signing route disambiguation.** PRD §9 lists `/portal/quotes/[id]/sign` as a distinct route. Current implementation renders signing as a state within `quotes/[id]`. `SigningView.astro` and `PreSigningPrep.astro` are specified for the distinct-route pattern. Decision pending; see Open Design Decisions #1.

**Champion access (UX-001): DEFERRED to Phase 4.** See Appendix A for the concept spec so Phase 1–3 architecture does not paint into a corner.

---

### `admin.smd.services` — Admin Console

| URL                                         | Purpose                                                                  | Primary Action                | PRD Feature            | Component(s)                                                        | Status                                   |
| ------------------------------------------- | ------------------------------------------------------------------------ | ----------------------------- | ---------------------- | ------------------------------------------------------------------- | ---------------------------------------- |
| `/admin`                                    | Admin dashboard — overdue follow-ups, pending signatures, quick-access   | (triage surface)              | US-016; PRD §9         | —                                                                   | Exists                                   |
| `/admin/pipeline`                           | Pipeline kanban — Prospect / Assessed / Quoted / Active / Completed      | Navigate to entity            | US-001-002; PRD §9     | `PipelineKanban.astro` (New), `ClientCard.astro` (New)              | New (components); Exists (entities list) |
| `/admin/entities`                           | Client list — filterable                                                 | New entity                    | US-001, US-002         | —                                                                   | Exists                                   |
| `/admin/entities/[id]`                      | Client/entity detail — stage, contacts, assessments, quotes, engagements | New assessment / New quote    | US-001-004             | —                                                                   | Exists                                   |
| `/admin/entities/[id]/quotes/[quoteId]`     | Quote builder — line items, pricing, SOW gen, SignWell send              | Generate SOW / Send to Client | US-007-009             | `QuoteLineItemEditor.astro` (New), `SOWPreviewPane.astro` (New)     | Exists (page); New (components)          |
| `/admin/entities/[id]/meetings/[meetingId]` | Assessment/meeting detail                                                | Complete assessment           | US-004-006             | `ExtractionPanel.astro` (New)                                       | Exists (page); New (component)           |
| `/admin/assessments/[id]`                   | Assessment workspace — transcript upload, extraction, problem mapping    | Save extraction               | US-004-006, BR-011-013 | `ExtractionPanel.astro` (New)                                       | Exists (page); New (component)           |
| `/admin/engagements/[id]`                   | Engagement lifecycle — milestones, time entries, parking lot, invoices   | Log time / Advance status     | US-014-015; Phase 4    | `TimeEntryLog.astro` (New), `ParkingLotPanel.astro` (New, editable) | Exists (page); New (components)          |
| `/admin/follow-ups`                         | Follow-up queue — overdue, due today, upcoming                           | Complete / Skip               | Phase 5; PRD §9        | `FollowUpCard.astro` (New)                                          | Exists (page); New (component)           |
| `/admin/analytics`                          | Reports — pipeline funnel, revenue, follow-up compliance                 | (read-only)                   | Phase 5                | —                                                                   | Exists                                   |
| `/admin/generators`                         | Content generator catalog                                                | Run generator                 | Phase 5 (Claude API)   | —                                                                   | Exists                                   |
| `/admin/generators/[type]`                  | Single generator — inputs + preview + run history                        | Run                           | Phase 5                | —                                                                   | Exists                                   |
| `/admin/settings/google-connect`            | Google Calendar / Drive OAuth connect                                    | Connect                       | Phase 5                | —                                                                   | Exists                                   |

---

## 2. Key Screen Breakdowns

### 2.1 Portal Dashboard (`/portal`) — Highest-Stakes Screen

**Design maturity note:** Exists. `PortalHomeDashboard.astro` and `portal/index.astro` implemented. This breakdown documents mandatory constraints and confirms above-the-fold discipline.

#### Stale Visit Recovery

Marcus explicitly stated (Round 2): "I check it when the email tells me to. Maybe Sunday night." The design assumes multi-day gaps between visits. When Marcus returns after 4+ days, the home screen must orient him in under 3 seconds without hunting.

**"Last activity" banner — top of home screen, always:**

```
Last activity: 4 days ago — [authored event description]
```

- Placement: directly below `PortalHeader`, above the primary engagement card. Full-width, cream surface, hairline bottom border.
- Time expression: relative ("4 days ago", "6 hours ago", "yesterday"). No ISO timestamps.
- Event description: authored prose from the `engagement_events` log — not a system-generated label. "Scope updated to include scheduler-replacement training" not "scope_changed event at 2026-04-22T15:34:00Z". If `last_event.prose` is null, render nothing — do not fabricate a summary.
- If no prior visit recorded (first session): omit the banner entirely. The dashboard's primary CTA is sufficient orientation.

**Bell icon (notification count):**

- Appears in `PortalHeader` right cluster alongside existing contact icons.
- Shows a count badge (ink on cream background, hairline border) of items requiring Marcus's action: unsigned quotes + unpaid invoices + parking lot items awaiting his decision.
- Zero count: bell icon visible, no badge. Do not hide the bell.
- Tap target: 44×44px minimum.

**"What's new since you were last here" section:**

- Rendered below the primary engagement card, above any secondary content.
- Heading: "Since your last visit" (text-heading, Archivo weight 600). If Marcus has never logged in before, heading is suppressed entirely.
- Content: chronological list of authored events since `session.last_seen_at`. Each event is a `TimelineEntry` — authored prose, date in natural format. No system-generated event labels.
- Events that qualify: milestone advanced, document uploaded, parking lot item dispositioned, admin note to client (if that feature exists), invoice issued or paid.
- Empty state (nothing new): cream paper. No content. No "You're all caught up!" No reassuring copy. The absence of new items IS the information. Do not fabricate reassurance.
- Max visible: 4 items. "Show all activity" tertiary link if more.

#### Above-the-Fold Contract (375px viewport, State 1 — pre-signature)

Unchanged from Round 2. Pixel budget documented; CTA top edge must sit at y ≤ 560px within the ~564px usable area.

```
PortalHeader (masthead, client name)            44px
─────────────────────────────────────────────
Last activity banner (if returning user)        ~28px
─────────────────────────────────────────────
Eyebrow: "{BUSINESS NAME}" (text-label)         20px
Engagement title (text-hero-mobile, w900)       48px
Scope summary (text-body-lg, 2-3 lines)         ~60px
Project total (MoneyDisplay hero, w900)         72px
Payment structure caption (text-caption)        20px
"Review and sign" primary CTA (full-width)      52px
─────────────────────────────────────────────
Total with rhythm overhead                      ~416px within 564px usable
```

#### State Compositions (all four)

State 1 — Pre-signature: per Round 2 spec. No change.

State 2 — Post-signature, pre-payment: per Round 2 spec. Warning color updated to `#7a5800`.

State 3 — Active engagement: per Round 2 spec. No change.

State 4 — Safety net: per Round 2 spec. No change.

---

### 2.2 Quote Detail with Pre-Signing Prep and Signing (`/portal/quotes/[id]`)

#### Pre-Signing Prep Screen (New — `PreSigningPrep.astro`)

Marcus (Round 2): "The worst thing that can happen is visual whiplash. If the portal is cream and dark ink and the SignWell window is white with blue buttons and a different typeface, my brain registers that as 'you're not in Kansas anymore.'"

The phishing-energy moment is not just visual — it is about entering an irreversible financial commitment without a clear summary of what you're agreeing to. The pre-signing prep screen addresses both.

**Purpose:** Before the SignWell iframe loads, Marcus sees one full-screen summary of what he is about to sign. The Sign button is visible in the viewport but disabled until he has scrolled past the summary content. This enforces the "read first, sign second" behavior without adding a mandatory confirmation modal that could feel patronizing.

**Screen structure:**

```
PortalHeader (visible, cream)
Breadcrumb: "← Proposals / {Engagement Title}"
─────────────────────────────────────────────
Section heading: "BEFORE YOU SIGN"
(text-label, mono caps, hairline underline below)

Summary block:
  Project total (MoneyDisplay, hero size, weight 900)
  Payment structure (authored: "$3,500 now · $3,500 at completion")
  Hairline rule

Deliverables list:
  Heading: "WHAT'S INCLUDED" (text-label, mono caps)
  Each deliverable: authored `line_items.title` and 1-sentence description.
  If `line_items` is empty: "Deliverables are detailed in the full proposal above."
    (Do not fabricate scope language — empty-state-pattern)

Legal acknowledgments:
  Heading: "WHAT YOU'RE AGREEING TO" (text-label, mono caps)
  Two authored sentences (system config, reviewed by Captain):
    e.g. "Signing this document confirms your agreement to the scope and payment
    terms described in the proposal. A countersigned copy will be emailed to you."
  NOT a wall of legalese. Two sentences maximum.

Scroll anchor: invisible `id="sign-anchor"` at bottom of this block.

"Continue to signing →" primary CTA button
  - Default state: disabled (opacity 50%, cursor not-allowed)
  - Enabled state: after IntersectionObserver fires on `#sign-anchor`
  - Pattern 03 (Button hierarchy): this is the only primary CTA on this screen.
  - Label: "Continue to signing →" not "Sign now" — the signing happens in the next step.
  - On click: transitions to SigningView (below)
```

**Scroll-gate behavior:**

An `IntersectionObserver` watches `#sign-anchor` (placed after the legal acknowledgments). When it enters the viewport, the button becomes enabled. This is a minimal inline `<script>` tag in the Astro component — not a React island. If JavaScript is disabled: button is enabled by default (progressive enhancement, not a hard gate).

**ARIA:**

- Disabled button: `aria-disabled="true"` (not `disabled` attribute, which removes keyboard focus). Screen reader announcement: "Continue to signing, button, dimmed." Users who navigate by keyboard or AT can still tab to the button before it enables — the AT announces the state.
- Scroll-gate for AT users: after the observer enables the button, no announcement is made (the state change is visual; AT users can confirm by re-reading the button's state). Do not use `aria-live` for the button state change — it is not urgent information.

**Why not a modal confirmation instead:**
A confirmation modal ("Are you sure you want to sign?") creates a binary choice at a moment of commitment. It is a last-second interruption. The pre-signing prep screen is a preparation space, not a confirmation gate. Marcus reads at his own pace, then proceeds when ready. There is no modal.

#### Framed Signing View (`SigningView.astro` — New)

Per Round 2 spec. No substantive changes. Warning token reference corrected to `#7a5800`.

The pre-signing disclosure line ("The signing form below is provided by SignWell, a document-signing service.") remains directly above the iframe. Marcus has already been prepared by the prep screen; the disclosure confirms the seam without surprise.

---

### 2.3 Engagement Detail with Parking Lot (`/portal/engagement`)

#### `ParkingLotPanel.astro` Item Rendering Spec (New)

Marcus (Round 2): "The parking lot is supposed to feel like collaboration, not homework. Each item needs to explain what it is, why it matters, and what was decided."

Each parking lot item rendered in `ParkingLotPanel.astro` (read-only portal view) must carry four authored data fields. Without all four, the item is not shown to the client (admin has not completed their authoring obligation).

**Item data model requirements (authored fields — no fabrication):**

| Field              | Type                                                  | Who authors            | Rules                                                                                                                                                                                                |
| ------------------ | ----------------------------------------------------- | ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `title`            | Short string (≤ 80 chars)                             | Admin at item creation | Plain language. Names the thing: "Job completion tracking" not "scope_expansion_002".                                                                                                                |
| `decision_framing` | 1-2 sentences                                         | Admin at review        | Frames the choice in the client's language: "During the scheduling work we noticed your techs don't log job completion times, which delays billing by a day or more. We wanted to flag it."          |
| `options`          | Array of 2-3 objects: `{label, rationale}`            | Admin at review        | Each option in plain language with 1-sentence rationale: `{label: "Add to current project", rationale: "We can build this in with the scheduling system — adds about a week."}`. Minimum 2 options.  |
| `disposition`      | Enum: `decided` / `deferred_to_call` / `out_of_scope` | Admin at disposition   | Human label. Not a status code.                                                                                                                                                                      |
| `disposition_note` | 1-2 sentences                                         | Admin at disposition   | "We added this to the current scope after your call on April 18." Required if `disposition = decided` or `disposition = out_of_scope`. Optional if `deferred_to_call` (the call itself is the note). |

**Item rendering (portal, read-only):**

```
[Item title — text-heading, Archivo weight 600]

[decision_framing — text-body, authored prose]

[Options considered:]
  • [option.label]: [option.rationale]  (text-body-lg, hairline left-border on list)
  • [option.label]: [option.rationale]

[Disposition block:]
  Hairline rule
  "DECIDED" / "DEFERRED TO CALL" / "OUT OF SCOPE"
    (text-label, Archivo Narrow mono caps, disposition color)
  [disposition_note — text-caption, text-secondary color]
```

**Disposition colors:**

- `decided`: `--ss-color-complete` (olive, #4a6b3e) — positive resolution
- `deferred_to_call`: `--ss-color-text-secondary` (subdued ink, #4a423c) — neutral, pending
- `out_of_scope`: `--ss-color-text-muted` (muted ink, #8a7f73) — acknowledged but not acted on

**Empty state:** If no parking lot items have `visible_to_client = true` and `all_authored_fields_present = true`: section is not rendered. No "Nothing here yet" copy. No illustration. The section does not exist if it has no content — empty-state-pattern.

**Render gate:** The parking lot section in the portal renders only when `engagement.status = complete` (handoff phase) or when admin has explicitly flagged items for visibility. During active engagement, clients do not see the parking lot.

**Admin editing view (`ParkingLotPanel.astro` editable mode):** `readonly: false` prop. Admin can author all four fields, set disposition, and toggle `visible_to_client`. Stale warning state (disposition = null AND requested_at > 14 days) appears as section header chip: "X items need review" in `--ss-color-warning` (#7a5800). Individual stale row tag: "STALE" (text-label, Archivo Narrow, warning color).

---

### 2.4 Invoice Detail — Professional Invoice Format (`/portal/invoices/[id]`)

Per Round 2 spec. Warning token corrected to `#7a5800` throughout. No other substantive changes.

---

### 2.5 Admin Engagement Detail (`/admin/engagements/[id]`)

Per Round 2 spec with the following addition:

**Admin desktop-first breakpoints:**

Admin layout assumes a minimum viewport of 1024px. This is documented here rather than in the responsive section because it is an admin-specific architectural decision, not a general product decision.

| Viewport     | Admin behavior                                                                                                                                                                                                                   |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ≥1024px      | Full admin layout: sticky top nav, side-by-side panels, dense tables, all features accessible.                                                                                                                                   |
| 768px–1023px | Admin is usable but not optimized. Layout may collapse to single column on entity detail and engagement pages. Warning banner: "For the best experience, use a screen wider than 1024px." Banner is informational, not blocking. |
| <768px       | Same warning banner. No additional breakpoint-specific layout work in MVP. Critical admin actions remain functional (see below).                                                                                                 |

**Critical admin actions that remain mobile-functional:**

Even on a phone (< 768px), the following actions must not be blocked by layout breakage:

| Action                         | Screen                                      | Why it must work on mobile                                                         |
| ------------------------------ | ------------------------------------------- | ---------------------------------------------------------------------------------- |
| Disposition a parking lot item | `/admin/engagements/[id]` → ParkingLotPanel | Emergency during a client call — Marcus asks about an item, admin needs to respond |
| Send a new magic link          | `/admin/entities/[id]`                      | Client locked out; admin on the road                                               |
| Mark a quote as sent           | `/admin/entities/[id]/quotes/[quoteId]`     | Admin may trigger sends from phone                                                 |
| View engagement status         | `/admin/engagements/[id]`                   | Status-check while away from desk                                                  |

These four actions are designed and tested at 375px. All other admin actions degrade gracefully (usable but not optimized) at <768px.

---

## 3. Warning States

Warning token corrected from Round 2. Brand Strategist finalized `--ss-color-warning: #7a5800` (7.14:1 on cream, WCAG AAA for normal text). All warning state specs use this value.

### 3.1 Quote Near Expiry

**Screen:** `/portal/quotes/[id]`, state = isSent.
**Trigger:** `quote.expires_at` is within 72 hours and `quote.status = sent`.
**Visual spec:** Full-width callout strip above the "Review and sign" CTA. Hairline left-border (3px solid, `--ss-color-warning`), cream background. Text in `--ss-color-warning` (#7a5800): "PROPOSAL EXPIRES {day, date at time}" (text-caption, Archivo Narrow mono caps for the label). **Paired with action:** The "Review and sign" CTA is directly below the callout — Marcus has the action immediately. No warning without an action.
**ARIA:** `role="alert"` on the callout (screen readers announce on load).
**Does not appear in:** isSigned, isDeclined, isExpired, isSuperseded states.

### 3.2 Deposit Overdue

**Screen:** `/portal/invoices/[id]`, state = isOverdue. Also: `/portal` dashboard State 2 invoice chip.
**Trigger:** `invoice.due_date` is past AND `invoice.status = unpaid`.
**Visual spec (invoice detail):** Callout strip above the "Pay Now" CTA. Same hairline-left-border treatment. Text: "OVERDUE — DUE {natural date}". **Paired with action:** "Pay Now — ${amount}" CTA is the next element — action is always adjacent to the warning.
**Visual spec (dashboard chip):** `StatusPill` compact variant, `--ss-color-warning` text on cream surface (not warning background — ink on warning background for body text passes at 15.91:1, but the pill compact variant uses text color on cream per the token usage rules). Label: "OVERDUE".
**Marcus's requirement:** "Warnings are how you get me defensive... Any warning has to be paired with a clear next action. Otherwise it's just yelling at me." No warning without an adjacent actionable CTA.

### 3.3 Parking Lot Stale Item (Admin Only)

Per Round 2 spec. Warning color `#7a5800`.

**Portal read-only view:** No warning chip. Marcus never sees "STALE" labels. Staleness is an internal operational concern — he only sees fully authored, dispositioned items.

---

## 4. Navigation Model

### 4.1 `smd.services` — Marketing

Per Round 2 spec. No change.

---

### 4.2 `portal.smd.services` — Client Portal

**Final position: persistent labels on all viewports.**

#### Tab Affordance — Icons + Labels: Final Decision

**Cited authority:** NN/g "Icon Usability" (https://www.nngroup.com/articles/icon-usability/):

> "Icons need to be paired with text labels. Without text labels, icons routinely fail usability tests... Users often have to guess what an icon means. This guessing is not fast."

Marcus confirmed this in Round 2: "I had to stop on 'description.' What is the 'description' tab? I had to keep reading to figure out that it's probably the proposal detail. That's too much guessing."

The NN/g position is unambiguous: persistent labels are required for navigation icons that are not universally recognized (home is the exception; everything else needs a label). The system has five tabs. Only `home` passes the universal-recognition threshold. The other four — Proposals, Invoices, Progress, Documents — require labels on every viewport.

**Implementation:** Icons and labels both visible at all times. No icon-only mode. No label-hiding at constrained widths. If labels do not fit, the tab bar reduces icon size (from 24px to 20px) and label size (from 12px to 11px) before hiding labels.

**Canonical tab set (final):**

| Tab                  | Label     | Icon (inactive, FILL 0) | Icon (active, FILL 1)   | Rationale                                                                |
| -------------------- | --------- | ----------------------- | ----------------------- | ------------------------------------------------------------------------ |
| `/portal`            | Home      | `home`                  | `home` (filled)         | Universal. No label ambiguity.                                           |
| `/portal/quotes`     | Proposals | `description`           | `description` (filled)  | Document with lines. Paired with "Proposals" label — ambiguity resolved. |
| `/portal/invoices`   | Invoices  | `receipt_long`          | `receipt_long` (filled) | Receipt register. Distinct from document.                                |
| `/portal/engagement` | Progress  | `timeline`              | `timeline` (filled)     | Timeline metaphor matches milestone model. Distinct from `receipt_long`. |
| `/portal/documents`  | Documents | `folder`                | `folder` (filled)       | Library register. Deferred to 5th tab post-launch.                       |

**Round 2 Design Tech error corrected:** The R2 PortalTabs spec mapped `receipt_long` to both Invoices (slot 03) and Progress (slot 03 anchor). `timeline` is the correct Progress icon. `assignment` (clipboard) was considered but reads as "task list" not "engagement timeline." `timeline` is preferred.

**Mobile chrome (375px):**

- Tab bar fixed at bottom, 64px height minimum (icon 20px + label 11px text-label + 8px padding-block + safe-area-inset).
- Four tabs in MVP: Home / Proposals / Invoices / Progress. Documents as fifth post-launch if access data justifies it.
- Tab labels: Archivo Narrow, 11px, 500 weight, 0.06em tracking. Rendered below icon with 4px gap.
- Active: icon FILL 1 + `--ss-color-primary` label. Inactive: icon FILL 0 + `--ss-color-text-muted` label.
- Tab visibility gating (PRD §9): Home always shown. Proposals: at least one quote exists. Invoices: at least one invoice exists. Progress: active engagement exists. Documents: at least one document uploaded.
- No hamburger. No sidebar. No drawer.

**Desktop chrome (1280px):**

- Horizontal tab row below masthead. Icon + label inline (not stacked). Active: 2px bottom border in `--ss-color-primary`.
- Same four tabs. Label always visible.

**Back navigation:** Detail pages show back link ("← Proposals" / "← Invoices"). Text-secondary, text-caption, 44px tap zone. Persistent tab bar always visible below.

**Billing is not a setting.** Billing information is visible on the Home dashboard (invoice chip on engagement summary row) and on the Invoices tab. There is no Settings → Billing path.

---

### 4.3 `admin.smd.services` — Admin Console

**Pattern:** Hub-and-spoke with tabs (NAVIGATION.md §3.2).

**Desktop chrome (≥1024px):**

- Sticky top nav: "SMD SERVICES · ADMIN" left (Archivo weight 900, caps) + primary nav tabs center (Dashboard / Pipeline / Entities / Follow-ups / Analytics / Generators) + session email + sign-out right.
- Sub-navigation: breadcrumbs on all entity-scoped deep pages.

**Below 1024px:**

- Banner: "ADMIN IS BEST VIEWED ON A WIDER SCREEN" (text-label, Archivo Narrow, ink on cream, hairline bottom border). Not a blocking modal. The admin still works.
- Critical mobile-functional actions: per §2.5 table above.

**Breadcrumb spec:**

- Format: "Dashboard / Entities / {Entity Name} / {Leaf label}"
- Chevron separators, text-caption, Archivo Narrow.
- Last crumb: text-primary, not linked. Prior crumbs: text-secondary, linked.

---

## 5. User Flows

### Flow 1: Magic-link login → First portal view → See scope

Per Round 2 spec. No change.

---

### Flow 2: Quote review → Pre-signing prep → Framed signing → Return to portal

**Updated from Round 2 to include pre-signing prep screen.**

1. Marcus sees "Review and sign" CTA above the fold. Taps it.
2. Navigation to `/portal/quotes/[id]` (quote detail, isSent state).
3. Above fold: engagement title → scope summary → total → payment caption → "Review and sign" CTA.
4. Below fold: full line-item descriptions, exclusions.
5. Marcus taps "Review and sign."
6. **Pre-signing prep screen activates (`PreSigningPrep.astro`):**
   - Portal chrome (PortalHeader) remains visible and intact.
   - Breadcrumb: "← Proposals / {Engagement Title}".
   - Section heading: "BEFORE YOU SIGN" (text-label, mono caps, hairline underline).
   - Project total at hero scale (Plainspoken weight 900).
   - Payment structure (authored).
   - Deliverables list (authored `line_items.title` + 1-sentence descriptions).
   - Legal acknowledgments (2 authored sentences — Captain-reviewed system config).
   - "Continue to signing →" primary CTA — disabled until Marcus scrolls past `#sign-anchor`.
7. Marcus reads and scrolls. CTA enables.
8. Marcus taps "Continue to signing →."
9. **Signing view activates (`SigningView.astro`):**
   - PortalHeader remains visible.
   - Pre-signing disclosure: "The signing form below is provided by SignWell, a document-signing service."
   - Scope accordion (mobile): collapsed. "Review scope" toggle.
   - SignWell iframe: full-width, full remaining viewport height.
10. Marcus signs. SignWell fires `document.completed`.
11. Webhook processes server-side.
12. Marcus sees isSigned state in portal chrome. "Signed {natural date}." prose. "View your deposit invoice" secondary link.

---

### Flow 3: Returning user (4 days later) → Orient → Act

1. Marcus gets no email (nothing urgent). Logs in Sunday evening via saved portal URL or by requesting a new magic link.
2. Lands on `/portal` home screen.
3. **"Last activity" banner:** "Last activity: 4 days ago — Scope updated to include scheduler-replacement training."
4. Bell icon in PortalHeader: badge showing "1" (one item needs attention — unpaid deposit invoice).
5. **"What's new since your last visit" section (below primary card):**
   - "April 22 — Scope updated to include scheduler-replacement training." (authored prose)
   - "April 21 — Invoice INV-2026-001 issued. $3,500 due April 29." (authored prose)
6. Primary engagement card (State 2): deposit invoice hero card with "Pay Now — $3,500" CTA. Above the fold.
7. Marcus taps "Pay Now." Opens Stripe hosted URL.
8. Payment completes. Returns to portal. Email confirmation fires within 5 minutes.

---

### Flow 4: Parking lot review (Admin) → Author → Client sees

1. Scott (admin) is wrapping up the scheduling engagement. Navigates to `/admin/engagements/[id]`.
2. Opens ParkingLotPanel (editable). One item flagged "STALE" — `disposition = null`, 16 days old.
3. Scott authors all four fields: title, decision_framing, two options with rationales.
4. Sets disposition: `decided`. Authors disposition_note: "Added to project after April 18 call."
5. Toggles `visible_to_client = true`.
6. Portal: Marcus logs in. Parking lot section now visible on `/portal/engagement`. One item.
7. Marcus reads: title, the decision framing in plain language, the two options that were considered, the outcome: "DECIDED — Added to project after April 18 call."
8. Reaction Marcus should have: "They were paying attention. They found this, they thought it through, they told me what they decided." Not: "Here's my homework."

---

## 6. Form Patterns

### 6.1 Input Styles

- Text inputs: full-width, border `--ss-color-border`, 0 radius, text-body, text-primary, surface background.
- Label: above input always (WCAG §4.1). text-label token, text-primary. Required: "(required)" in label text.
- Focus state: 2px ring, 2px offset, `--ss-color-action`.
- Disabled: border-subtle, text-muted, `not-allowed` cursor.

### 6.2 Validation Timing

- On submit for required fields.
- On blur for format-critical fields (email, phone).
- Real-time for quote builder line item totals.

### 6.3 Error Placement

- Field-level: directly below input. text-caption, error color. `aria-describedby`. Complete sentence.
- Form-level (admin): top-of-form strip, sticks until condition resolves.
- Portal single-field forms: inline below input, friendly language.

### 6.4 Required Indicators

- Label text: "{Field name} (required)".
- `aria-required="true"` on input.
- Optional fields: no "(optional)" unless more optional than required (NN/g convention).

### 6.5 Touch Target and Tap Affordance

Marcus's use context — phone in truck, possible work gloves, sun glare — requires rigorous touch target discipline. Floor: 44×44px per WCAG 2.5.5 (AAA).

| Element                        | Minimum size                        | Implementation note                                    |
| ------------------------------ | ----------------------------------- | ------------------------------------------------------ |
| Tab bar items                  | 64px height (full bar height)       | Full bar height tappable, not just icon+label area     |
| Primary CTA buttons            | 52px height, full-width             | Full-width eliminates off-target taps on mobile        |
| Ghost / secondary buttons      | 44px height minimum                 | Even text-only ghost buttons need explicit min-height  |
| List row tap targets           | Full-row width, 56px height minimum | Extend tap zone beyond visible text                    |
| Back / crumb links             | 44px height minimum via padding     | Small link text padded to 44px tall tap zone           |
| "Continue to signing →" CTA    | 52px height, full-width             | Pre-signing prep screen — same as primary CTA spec     |
| Disclosure toggles (accordion) | 44px height minimum                 | Scope reminder toggle on signing view                  |
| Overflow menu triggers (admin) | 44px × 44px                         | Kebab icon padded to square 44px                       |
| Icon buttons in header         | 44px × 44px                         | Bell icon and contact icons in PortalHeader            |
| Parking lot option rows        | 44px height minimum                 | Tappable if interactive; text-only rows may be smaller |

**Implementation:**

```css
min-height: 44px;
display: inline-flex;
align-items: center;
padding-block: 10px;
```

### 6.6 Mobile Keyboard and Form Behavior (Quote Builder)

The `QuoteLineItemEditor.astro` React island (admin only) presents complex forms. On mobile (<768px), the keyboard pop-up obscures active inputs. Spec:

**Scroll-into-view on focus:**

```ts
// On every input focus event in the React island:
input.addEventListener('focus', () => {
  setTimeout(() => {
    input.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, 300) // 300ms delay gives keyboard time to animate open
})
```

**Keyboard dismissal:**

- Tapping any non-interactive row area (whitespace, row label) should dismiss the keyboard. Implement via `onPointerDown` on the row container that calls `document.activeElement.blur()`.
- "Done" button on iOS keyboard (via `returnKeyType` on React Native, or natural browser behavior on HTML inputs) should advance to next field (`Tab`-order traversal).

**`autocomplete` attributes:**
| Input | `autocomplete` value |
|-------|---------------------|
| Currency / money | `off` (amounts are unique per engagement; browser suggestions are wrong) |
| Date | `off` (same reason) |
| Client name (admin forms) | `organization` |
| Contact name | `name` |
| Email | `email` |
| Phone | `tel` |

**`inputmode` hints:**
| Input type | `inputmode` |
|-----------|-------------|
| Money / decimal amount | `decimal` |
| Integer hours | `numeric` |
| Date (text field) | `none` (date picker handles it) |
| Email | `email` |
| Phone | `tel` |

**Why `inputmode="decimal"` for money:** On iOS, `inputmode="decimal"` shows the numeric keypad with a decimal point — the correct keyboard for dollar amounts. `inputmode="numeric"` shows the numeric keypad without a decimal point on some iOS versions. `type="number"` triggers a stepper on some browsers that is wrong for currency. Use `type="text" inputmode="decimal"` for all money fields in the React island.

### 6.7 Keyboard Navigation (Quote Builder)

Tab between fields. Enter on hours: add new row. Backspace/Delete on empty hours: remove row. React island (`client:load`, admin quote builder page only).

---

## 7. Feedback Patterns

### 7.1 Toast and Notification Style

- Transient success toasts: portal only, low-stakes confirmations where state change is visible.
- Toast position: bottom-center on mobile (above tab bar), top-right on desktop.
- Duration: 4 seconds auto-dismiss; tap to dismiss early.
- No celebratory animation.
- Text: past tense, concrete. "Link sent." not "Sent successfully!"

### 7.2 Success Confirmations

- Portal signing: in-page prose replacement (isSigned state IS the confirmation). No toast.
- Invoice paid: page state on next load. Resend email fires (client's primary record).
- Admin parking lot disposition: inline row update. No toast.
- Admin stage transition: brief top-of-page strip, auto-dismisses after 4 seconds.

### 7.3 Destructive Confirmations

- Pattern: inline-expand within action row or narrowly scoped dialog. Not full-page modal.
- Text: concrete. "Cancel this engagement? Milestones and invoices remain on file."
- Two actions: destructive CTA (error fill, specific label) + neutral "Go back".
- Never auto-confirm.

### 7.4 Progress Indicators

- Page loading: skeleton at section level (portal), skeleton at panel level (admin).
- Form submit: disable button, replace label with spinner + "Sending...". Prevents double-submit.
- SignWell iframe: "Loading signing document..." centered in iframe container.
- R2 upload: linear progress bar or indeterminate spinner. "Uploading..." label.

---

### 7.5 Email Touchpoint Inventory

**Eight transactional emails. Per-email timing specified.**

Marcus (Round 2): "The email matters more than the portal on day one. If it looks like spam, nothing else matters."

**Trust signal requirements (all emails):**

- From: `team@smd.services` — recognizable domain.
- Reply-to: authored consultant email — replies go to a human.
- Plain-text fallback: required on every email.
- No marketing graphics, no unsubscribe footer that implies newsletter relationship.
- Subject line includes client business name or direct reference to prior interaction.

---

#### Authoring Workflow

Each email has three content layers:

**Layer 1 — System-prefilled (no authoring required):** Client name, business name, engagement title, dollar amounts (from `invoices.amount_cents`), invoice number, due dates, portal URLs, magic link URLs. The system assembles these from database fields. Never fabricated.

**Layer 2 — Admin-authored per engagement:** The one-line scope reference in Email 1. The "what happens next" text in Email 3 (from `engagement.next_step_text`). The parking lot item title and decision framing in Email 6. The milestone names in Email 7 (from `milestones.name`). These are authored by admin when setting up or advancing the engagement.

**Layer 3 — Captain-reviewed system config (prose templates):** The surrounding sentences in each email that are not engagement-specific — the greeting structure, the contextual framing around the CTA, the legal acknowledgment language. These are authored once, reviewed by Captain, and stored as system config. Brand Strategist's voice rules apply (no em dashes, no parallel structures, no polished AI phrasing, no fixed timeframes). **No surrounding prose is fabricated at runtime.** If a Layer 3 template is missing, the email sends with Layer 1 + Layer 2 content only — no fabricated filler.

**Who reviews before ship:**

- Email 1 (Portal Invitation): Captain reviews the Layer 3 template before the first real quote is sent to a client.
- Email 6 (Parking Lot): Captain reviews the three-way framing template before Phase 5 ships.
- All others: Layer 3 templates reviewed by Captain during Phase 2 QA.

---

#### Email 1 — Portal Invitation (Quote Sent)

**Trigger:** Admin sends quote via `POST /api/admin/quotes/:id/send` (US-009).
**Recipient:** Client primary contact.
**Timing:** Immediate on trigger.
**Subject:** `{Business Name} — your proposal from SMD Services`
**Body (key elements):**

- Salutation with contact name (authored `entity_contacts.name`; if null, no salutation — do not substitute "Business Owner").
- One-line scope reference: authored `engagement.proposal_summary` or engagement title. Must be specific to this client.
- Project total in plain text.
- Single CTA: "View Your Proposal" (magic link).
- Magic link expiry notice.
  **Authoring layer:** Name + total = Layer 1. Scope reference = Layer 2. Surrounding framing = Layer 3.

#### Email 2 — Proposal Signed (Confirmation to Client)

**Trigger:** SignWell `document.completed` webhook → `quotes.status` updated to `accepted`.
**Recipient:** Client primary contact.
**Timing:** Within 5 minutes of webhook receipt. If webhook processing exceeds 5 minutes, fire email as soon as processing completes — do not hold.
**Subject:** `{Business Name} — proposal signed. Deposit invoice enclosed.`
**Body:** Signing confirmation + deposit invoice summary (invoice number, amount, due date, Stripe URL). Single CTA: "Pay Deposit — ${amount}".
**Authoring layer:** All amounts + invoice number = Layer 1. Surrounding framing = Layer 3.

#### Email 3 — Countersigned / Firm Acknowledgment

**Trigger:** Admin reviews and confirms the signed quote (or automatic on webhook receipt if no manual review step).
**Recipient:** Client primary contact.
**Timing:** Within 5 minutes of trigger. The client signed; they expect acknowledgment quickly.
**Subject:** `{Business Name} — we've received your signed proposal`
**Body:** Acknowledgment + what happens next (from `engagement.next_step_text`; if null, no "what happens next" section — empty-state-pattern). Contact information.
**Authoring layer:** Business name = Layer 1. Next-step text = Layer 2 (or absent). Surrounding framing = Layer 3.

#### Email 4 — Invoice Issued

**Trigger:** Admin creates a new invoice.
**Recipient:** Client primary contact.
**Timing:** Immediate on trigger.
**Subject:** `{Business Name} — invoice #{invoice_number} from SMD Services`
**Body:** Invoice summary (number, amount, due date, description). Single CTA: "Pay Invoice — ${amount}". ACH note per BR-036.
**Authoring layer:** All invoice fields = Layer 1. Line item descriptions = Layer 2. Surrounding framing = Layer 3.

#### Email 5 — Payment Confirmed

**Trigger:** Stripe `invoice.paid` webhook.
**Recipient:** Client primary contact.
**Timing:** Within 5 minutes of webhook receipt.
**Subject:** `{Business Name} — payment received ($3,750)`
**Body:** Confirmation of payment amount and invoice number. Portal link to view receipt. Next milestone or next step (from engagement record; if null, omit).
**Authoring layer:** Amount + invoice number = Layer 1. Next milestone = Layer 2 (if authored). Surrounding framing = Layer 3.

#### Email 6 — Parking Lot Item Needs Decision

**Trigger:** Admin flags a parking lot item with `visible_to_client = true` (Phase 5 feature).
**Recipient:** Client primary contact.
**Timing:** Batched. Not one email per item — one email per day containing all items flagged `visible_to_client = true` since the last batch. Batch fires once daily at 9am client local time (or system default if timezone not set). If multiple items are added in one day, they arrive in a single email — not a flood.
**Subject:** `{Business Name} — {N} item(s) from your engagement need your input` (N = count of items in batch)
**Body:** Plain language framing (Layer 3 template). For each item: authored title + authored decision_framing. CTA: "Review in portal" (portal engagement page).
**Authoring layer:** Item titles + decision framing = Layer 2 (required; if not authored, item is not included in batch). Surrounding framing = Layer 3.

#### Email 7 — Engagement Complete Summary

**Trigger:** Admin advances engagement status to `complete`.
**Recipient:** Client primary contact.
**Timing:** Immediate on trigger.
**Subject:** `{Business Name} — project complete`
**Body:** Completion statement. Summary of completed milestones (authored `milestones.name` list; if milestone names are not authored in plain language, render titles only — no narrative fabrication). Document library count if documents uploaded. Safety net end date (authored `safety_net_end`; if null, omit). Contact for questions.
**Authoring layer:** Milestone names = Layer 2. Safety net date = Layer 1 (from DB field). Surrounding framing = Layer 3.

#### Email 8 — Magic Link (Re-auth)

**Trigger:** Client requests a new magic link from `/auth/portal-login`.
**Recipient:** The email address entered.
**Timing:** Immediate. Magic links expire in 15 minutes — delay is unacceptable.
**Subject:** `Sign in to your SMD Services portal`
**Body:** One sentence. Single CTA: "Sign in" (magic link). Expiry notice.
**Authoring layer:** Magic link URL = Layer 1. Everything else = Layer 3 (minimal).

---

**Email timing summary:**

| #   | Email                    | Timing                           | Batch? |
| --- | ------------------------ | -------------------------------- | ------ |
| 1   | Portal invitation        | Immediate on trigger             | No     |
| 2   | Proposal signed          | Within 5 min of webhook          | No     |
| 3   | Countersigned / firm ACK | Within 5 min of trigger          | No     |
| 4   | Invoice issued           | Immediate on trigger             | No     |
| 5   | Payment confirmed        | Within 5 min of webhook          | No     |
| 6   | Parking lot item         | Daily at 9am (all pending items) | Yes    |
| 7   | Engagement complete      | Immediate on trigger             | No     |
| 8   | Magic link re-auth       | Immediate                        | No     |

---

## 8. Responsive Strategy

### 8.1 Portal — Mobile-First

**Primary breakpoint: 375px (iPhone 13 mini / common minimum)**

- Single-column throughout.
- Persistent tab bar fixed at bottom, 64px height.
- Content scrolls behind tab bar — bottom padding = tab bar height + `space-stack` (16px).
- Above-fold constraint: primary CTA must be visible without scroll per pixel budget in §2.1.
- Signing iframe: `height: calc(100dvh - var(--portal-header-height) - var(--tab-bar-height) - 48px)`. Not fixed pixels.
- Pre-signing prep: single-column, scroll-gated CTA. Works at 375px.
- No horizontal scroll (WCAG 1.4.10 reflow).

**Desktop adaptation: 1280px**

- Max content width: 1040px centered.
- Two-column layout on dashboard and quote detail.
- Tab bar migrates to horizontal top strip below masthead.
- Invoice document: constrained column (~720px) for professional document register.
- Pre-signing prep: same single-column structure (document register, not a two-column).

**Touch target compliance:** Per §6.5. Every interactive element meets 44×44px.

### 8.2 Admin — Desktop-First

**Primary breakpoint: ≥1024px**

- Sticky top nav with tab links.
- Side-by-side panels on entity and engagement detail.
- Dense tables with `space-row` (12px) vertical rhythm.
- Below 1024px: warning banner + graceful degradation. Critical actions mobile-functional per §2.5.

**Keyboard navigation (quote builder):**

- Tab order: problem dropdown → description → hours → next row.
- Enter on hours: add new row.
- Backspace/Delete on empty hours: remove row.
- React island (`client:load`, admin quote builder only).

### 8.3 Marketing — Standard Responsive

Breakpoints: 375px / 768px / 1280px. Standard patterns; no portal constraints apply.

---

## Appendix A: Champion Access — Phase 4 Preview

**Deferred per PRD §18 Phase 4. Specified here so Phase 1–3 architecture does not close off the option.**

Marcus (Round 2): "If we ever get to the point where Maria can log in and see what's going on without bothering me, that would actually be useful. Defer it, but don't forget it."

### Concept

Maria is Marcus's operations manager. She needs to know what the firm is delivering and where the engagement stands. She does not need to see the quote price, the invoice amounts, or the payment history — those belong to Marcus as the business owner and signatory.

### User Role Model (to be preserved in Phase 1–3 data model)

| Role               | Auth                                                       | Can see                                                                  | Cannot see                                                        |
| ------------------ | ---------------------------------------------------------- | ------------------------------------------------------------------------ | ----------------------------------------------------------------- |
| `client` (Marcus)  | Magic link, primary contact                                | All portal content                                                       | —                                                                 |
| `champion` (Maria) | Separate magic link, `engagement_contacts.role = champion` | Engagement progress, milestone timeline, parking lot (after disposition) | Quote pricing, invoice amounts, payment history, proposal signing |

The `engagement_contacts` table already has the `is_champion` column (per PRD). Phase 1–3 must not remove this column or make assumptions that `entity_contacts.is_primary = true` is the only portal-auth-capable contact.

### Phase 4 Work Required

- Second magic-link invitation flow: admin sends champion invite separately from client invite. Different email template, different subject ("Maria, you've been added to {Business Name}'s engagement with SMD Services").
- Scoped portal session: champion session carries a `role = champion` claim. Portal routes check this claim and suppress quote/invoice content.
- Separate tab visibility: champion sees Home (engagement status) and Progress (milestone timeline, parking lot). Does not see Proposals or Invoices tabs.
- No quote pricing visible to champion under any state. If a champion navigates directly to `/portal/quotes/[id]`, they receive a 403 or a "This section is visible to {Business Name}'s account only" message.

### What Phase 1–3 Must Not Do

- Do not hardcode `session.role === 'client'` checks in a way that precludes a second role value.
- Do not remove `is_champion` from `engagement_contacts`.
- Do not design the portal URL structure to assume a single authenticated user per engagement.
- Do not build the Invoices or Proposals tabs in a way that cannot be conditionally suppressed by role at the Astro SSR layer.

---

## Open Design Decisions

Every item below requires a human decision. Options are provided with brief rationale. A recommendation is made where the evidence points clearly to one answer.

---

**Decision 1: Signing route — distinct URL or state within `quotes/[id]`**

**Question:** Is the pre-signing prep screen and signing view rendered at `/portal/quotes/[id]/sign` (a distinct URL) or as a state within `/portal/quotes/[id]`?

**Options:**

A. Distinct route `/portal/quotes/[id]/sign` — matches PRD §9 spec. Enables direct deep-linking from an email CTA to the signing surface. Enables back-navigation to the quote detail. Cleaner URL semantics.

B. State within `/portal/quotes/[id]` — current implementation pattern. Simpler routing. No back-navigation needed (the breadcrumb handles return). Slightly simpler server-side logic.

**Why it matters:** The invitation email's CTA currently links to `/portal/quotes/[id]`. If we add a pre-signing prep screen before the iframe, the email CTA and the "Review and sign" button on the quote detail may both need to reach the prep screen. If the prep screen is a distinct route, the email CTA can link directly to it. If it's a state, both entry points flow through the quote detail first.

**Recommendation:** Distinct route (`/portal/quotes/[id]/sign`). The pre-signing prep screen is a meaningful stop in the signing journey, not a transient UI state. Deep-linking to it from email is valuable. PRD §9 already specifies the distinct URL.

**Needs:** Captain decision before Phase 2 ships.

---

**Decision 2: Documents tab — MVP or post-launch**

**Question:** Is the Documents tab included in the MVP tab bar (5 tabs) or deferred to post-launch (4 tabs, documents accessible via dashboard row)?

**Options:**

A. 5 tabs at MVP: Home / Proposals / Invoices / Progress / Documents. Apple HIG allows up to 5. Documents are part of the engagement deliverable — clients will want them at handoff.

B. 4 tabs at MVP, Documents accessible from dashboard: keeps the tab bar focused on the two high-frequency actions (signing and paying). Documents are low-frequency until handoff. Add the tab post-launch when access data confirms frequency.

**Why it matters:** Marcus (Round 2) said "I don't need a fifth tab for documents in MVP." But Marcus is commenting before the handoff phase when he'll want those documents. The usage pattern may change after engagement completion.

**Recommendation:** 4 tabs at MVP (per Marcus's explicit preference). Documents accessible from the engagement summary card on Home dashboard ("View project files" secondary link). Add the 5th tab if post-launch session data shows > 20% of portal sessions include a documents page view.

**Needs:** Captain decision before Phase 2 ships.

---

**Decision 3: `--ss-color-warning` value — `#7a5800` or `#6b4f08`**

**Question:** Brand Strategist revised the warning token from `#6b4f08` (Round 1) to `#7a5800` (Round 2). Design Tech Round 2 still shows `#6b4f08` in the token JSON. Which value ships?

**Options:**

A. `#7a5800` (Brand Strategist R2 final) — warmer, more yellow-amber. 7.14:1 on cream (WCAG AAA). Visually distinct from olive (success) in low-light. Preferred by Brand Strategist.

B. `#6b4f08` (Design Tech R2) — deeper amber-ochre. 6.72:1 on cream (WCAG AA-adjacent). Lower risk of reading as "golden" in certain ambient lighting.

**Why it matters:** Token must be locked before warning states are implemented. A mismatch between the Brand Strategist and Design Tech contributions creates a conflict in what actually ships.

**Recommendation:** `#7a5800`. Brand Strategist's Round 2 final is authoritative for color decisions. AAA contrast margin is better. Design Tech R2 JSON entry should be updated to `#7a5800` before the token PR merges.

**Needs:** Captain confirmation + Design Tech JSON update before token PR merges.

---

**Decision 4: Pre-signing prep scroll gate — JS-gated or always-enabled**

**Question:** The "Continue to signing →" CTA is disabled until Marcus scrolls past the acknowledgments (via IntersectionObserver). Should this gate be enforced, or should the button be always-enabled with a visual nudge to scroll?

**Options:**

A. JS-gated (recommended spec): Button disabled until scroll anchor is in viewport. Progressive enhancement — if JS disabled, button is always-enabled. Clear enforcement of "read before sign."

B. Always-enabled with scroll nudge: Button is always active. A "scroll to review all terms" hint appears above the button if the anchor hasn't entered the viewport. Gentler UX; respects that Marcus may already have read the scope on the quote detail page.

**Why it matters:** Pattern 03 (Button hierarchy) says the primary action should be visible and not obscured. A disabled primary CTA may read as broken — "why can't I tap this?" — before Marcus realizes he needs to scroll.

**Recommendation:** JS-gated (Option A), but with a clear explainer adjacent to the disabled button: "Scroll to review the summary above, then continue." This text appears only when the button is disabled and disappears when it enables. Marcus sees the reason before he hits confusion.

**Needs:** Captain decision before Phase 2 ships.

---

**Decision 5: Parking lot batching threshold for client email**

**Question:** Email 6 (Parking Lot Item) batches to once-daily at 9am. If admin adds 3 items in one day, they all arrive in one email. If admin adds 1 item per week for 3 weeks, Marcus gets 3 separate emails. Is the batching window correct?

**Options:**

A. Daily batch (9am local) — current recommendation. Prevents item-per-email flood if admin is working through several items at once. May delay notification by up to 24 hours.

B. Immediate per item — Marcus gets an email the moment each item is flagged. No delay. Risk: if admin adds 5 items in an hour, Marcus receives 5 emails. Annoying.

C. Weekly digest — one email per week with all pending parking lot items. Lowest frequency, lowest disruption. Risk: items sit unreviewed for up to a week.

**Why it matters:** Marcus checks email between service calls, not on a schedule. He has said that parking lot items should feel like collaboration, not homework. A flood of individual emails reads as homework. A weekly digest reads as the firm isn't sure these items are urgent.

**Recommendation:** Daily batch (Option A). Captures the balance between immediacy and non-flooding. Admin should be encouraged to batch their own authoring — add all items for a given review session before end of day, so they land in one email.

**Needs:** Captain decision before Phase 5 ships.

---

**Decision 6: Champion invite — who triggers it**

**Question (Phase 4 preview):** When champion access is implemented, who sends the champion magic-link invitation — admin via the admin console, or Marcus via a portal self-service flow?

**Options:**

A. Admin-triggered: Scott sends the champion invite from `/admin/engagements/[id]`. Marcus tells Scott "add Maria." Simple; no portal self-service complexity at Phase 4.

B. Client-triggered: Marcus initiates from a portal settings or engagement page. More empowering for Marcus; more complex to build securely (Marcus should not be able to add arbitrary email addresses without admin review).

**Why it matters:** Phase 4 architecture must accommodate whichever path is chosen. Option A requires admin UI additions only. Option B requires a portal self-service flow, email confirmation loop, and admin approval step.

**Recommendation:** Admin-triggered (Option A) for Phase 4 MVP. If Marcus frequently requests this verbally, revisit Option B in Phase 5.

**Needs:** Captain decision before Phase 4 scoping.
