# Interaction Designer Contribution - Design Brief Round 1

**Author:** Interaction Designer (Sonnet 4.6)
**Date:** 2026-04-26
**Design Maturity:** Full system — inventory, gaps, and refinements only. No redesign of existing flows unless PRD explicitly flags a problem.
**Sources consulted:** PRD §7-9, §14, §18; NAVIGATION.md v3.1; portal-ux-brief.md; admin-ux-brief.md; UI-PATTERNS.md; empty-state-pattern.md; Pattern 08 (Actions and Menus); existing src/pages/ tree

---

## Screen Inventory

### `smd.services` — Marketing (Public)

| URL                    | Purpose                                                  | Primary Action       | PRD Feature                                | Status |
| ---------------------- | -------------------------------------------------------- | -------------------- | ------------------------------------------ | ------ |
| `/`                    | Marketing home — credibility, positioning, guide-persona | Book assessment      | Pre-sales (no PRD feature ID; supports §2) | Exists |
| `/get-started`         | Onboarding CTA / warm landing                            | Book assessment      | Pre-sales                                  | Exists |
| `/scorecard`           | Self-serve assessment wizard                             | Complete & book      | Pre-sales                                  | Exists |
| `/book`                | Assessment booking form (Calendly-embed or equivalent)   | Confirm booking      | Pre-sales / US-001 upstream                | Exists |
| `/book/manage/[token]` | Booking management via email token (reschedule/cancel)   | Reschedule or cancel | US-001 upstream                            | Exists |
| `/book/manage/`        | Fallback when no token in query string                   | Book a call instead  | —                                          | Exists |
| `/contact`             | General inquiry form                                     | Submit inquiry       | Pre-sales                                  | Exists |
| `/404`                 | Not-found, subdomain-aware                               | Back to home         | —                                          | Exists |

Auth entry points (on `smd.services` domain, redirect to subdomain sessions):

| URL                  | Purpose                                         | Primary Action | PRD Feature        | Status |
| -------------------- | ----------------------------------------------- | -------------- | ------------------ | ------ |
| `/auth/login`        | Admin login (email + password / magic link)     | Sign in        | US-016             | Exists |
| `/auth/portal-login` | Client magic-link request                       | Request link   | US-010, BR-027     | Exists |
| `/auth/verify`       | Magic-link token consumption + session creation | Auto-redirect  | US-010, BR-028-030 | Exists |

---

### `portal.smd.services` — Client Portal (session-auth-client)

| URL                        | Purpose                                                                           | Primary Action                                  | PRD Feature                                            | Status                                                                                        |
| -------------------------- | --------------------------------------------------------------------------------- | ----------------------------------------------- | ------------------------------------------------------ | --------------------------------------------------------------------------------------------- |
| `/portal`                  | Dashboard — state-responsive home; proposal / engagement / safety-net views       | Review & Sign (pre-sign) or Pay Now (post-sign) | US-010, US-011, US-013; PRD §7 Steps 3-7; §9 portal IA | Exists                                                                                        |
| `/portal/quotes`           | Proposal list — all proposals sent to this client                                 | Row click to detail                             | US-011; PRD §9                                         | Exists                                                                                        |
| `/portal/quotes/[id]`      | Quote detail — scope, price, signing states; 5-state machine                      | Review & Sign (sent state)                      | US-011, BR-018, BR-042                                 | Exists                                                                                        |
| `/portal/quotes/[id]/sign` | Signing surface — embedded SignWell iframe with scope sidebar                     | (SignWell iframe action)                        | US-011, EC-002, UX-002                                 | **New** — no file at this path; signing is currently rendered within `quotes/[id]` as a state |
| `/portal/invoices`         | Invoice list — all invoices for this client                                       | Row click to detail                             | US-013, US-012; PRD §9                                 | Exists                                                                                        |
| `/portal/invoices/[id]`    | Invoice detail — amount, status, Pay Now (Stripe hosted), receipt                 | Pay Now (unpaid state)                          | US-012, US-013, BR-036                                 | Exists                                                                                        |
| `/portal/documents`        | Document library — flat list of all engagement files                              | Download / Open                                 | PRD §7 Step 8; §9                                      | Exists                                                                                        |
| `/portal/engagement`       | Engagement progress — milestone timeline, parking lot (Phase 5), safety net state | (informational; no primary action in MVP)       | US-014, US-015; PRD §7 Step 7; Phase 4                 | Exists                                                                                        |

**Gap noted:** PRD §9 specifies `/portal/quotes/[id]/sign` as a distinct route for the signing surface. The existing codebase renders signing as a state transition within `quotes/[id]`. This should be clarified — either formalize the signing state as a route or document the embedded-state decision as intentional. If the signing iframe is rendered at a distinct URL, deep-linking from the invitation email lands directly in the signing context per PRD §7 Step 5.

---

### `admin.smd.services` — Admin Console (session-auth-admin)

| URL                                         | Purpose                                                                                      | Primary Action                              | PRD Feature                        | Status                                                      |
| ------------------------------------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------- | ---------------------------------- | ----------------------------------------------------------- |
| `/admin`                                    | Admin dashboard / operator home — overdue follow-ups, pending signatures, quick-access queue | (No single CTA; operator triage surface)    | US-016; PRD §9 admin IA            | Exists                                                      |
| `/admin/entities`                           | Client list — all entities, filterable by stage/vertical/source                              | New entity                                  | US-001, US-002                     | Exists (note: PRD uses "clients"; codebase uses "entities") |
| `/admin/entities/[id]`                      | Client/entity detail — stage, contacts, assessments, quotes, engagements, notes              | New assessment / New quote (context-driven) | US-001-003, US-004                 | Exists                                                      |
| `/admin/entities/[id]/quotes/[quoteId]`     | Quote builder — line items, pricing, SOW gen, SignWell send                                  | Generate SOW / Send to Client               | US-007, US-008, US-009             | Exists                                                      |
| `/admin/entities/[id]/meetings/[meetingId]` | Assessment/meeting detail (linked from entity)                                               | Complete assessment                         | US-004, US-005, US-006             | Exists                                                      |
| `/admin/assessments/[id]`                   | Assessment workspace — transcript upload, extraction, problem mapping                        | Save extraction                             | US-004, US-005, US-006, BR-011-013 | Exists                                                      |
| `/admin/engagements/[id]`                   | Engagement lifecycle — milestones, time entries, parking lot, invoices, follow-ups           | Log time / Advance status                   | US-014, US-015; Phase 4 features   | Exists                                                      |
| `/admin/follow-ups`                         | Follow-up queue — overdue, due today, upcoming, completed                                    | Complete / Skip (per cadence row)           | Phase 5; PRD §9                    | Exists                                                      |
| `/admin/analytics`                          | Reports — pipeline funnel, quote accuracy, revenue, follow-up compliance                     | (read-only)                                 | Phase 5; PRD §9                    | Exists                                                      |
| `/admin/generators`                         | Content generator catalog — outreach drafts, proposals, follow-ups                           | Run generator                               | Phase 5 (Claude API)               | Exists                                                      |
| `/admin/generators/[type]`                  | Single generator — inputs + preview + run history                                            | Run                                         | Phase 5                            | Exists                                                      |
| `/admin/settings/google-connect`            | Google Calendar / Drive OAuth connect                                                        | Connect                                     | Phase 5                            | Exists                                                      |

**Gaps against PRD §9 admin IA:**

1. **Pipeline view at `/admin`** — PRD §9 specifies the default admin landing as a kanban-style pipeline (Prospect / Assessed / Quoted / Active / Completed columns with client cards). The existing `/admin/index.astro` is an operator home with counts and alerts. These can coexist if the entity list at `/admin/entities` is the pipeline, but the naming divergence (`clients` vs. `entities`) and the absent kanban layout need reconciling. Recommend: `/admin/entities` with status-column grouping IS the pipeline; or add a `/admin/pipeline` view as an alias.

2. **Quote list at the entity level** — PRD implies multiple quote versions per client. The `/admin/entities/[id]/quotes/[quoteId]` path handles the detail; confirm a quote-list surface exists within entity detail (currently collapsible panel, not a list page — acceptable per admin-ux-brief density model).

3. **Parking lot admin write surface** — PRD §12 exposes `POST /api/admin/engagements/:id/parking-lot` and `PATCH /api/admin/parking-lot/:id`. These actions live within `/admin/engagements/[id]`. No separate route needed; document the inline-panel approach.

4. **Client portal signing route disambiguation** (see portal gap above) — admin side: after SOW send, admin sees `quote.status = sent`. No admin action needed until webhook fires.

---

## Key Screen Breakdowns

### 1. Portal Dashboard (`/portal`) — highest-stakes screen

**Design maturity note:** Exists. The `PortalHomeDashboard.astro` component and `portal/index.astro` are implemented. This breakdown documents what the screen must accomplish per PRD, and where alignment gaps exist.

**Layout (mobile-first, 390px)**

The dashboard is state-responsive — it renders one of four distinct compositions depending on `quote.status`, `invoice.status`, and `engagement.status`. Each composition has a single above-the-fold answer to "what do I do next?"

State 1 — Pre-signature (quote sent, not signed):

- Above fold: Business name eyebrow (text-label, mono caps) → scope summary headline (2-3 problems in plain language, text-display weight) → project price (IBM Plex Mono, money scale, tabular-nums) → payment structure caption (text-caption: "50% now, 50% at completion" — authored data only; no fabricated fallback) → primary CTA "Review and sign" (solid primary, full-width, 44px min height, right-thumb zone)
- Below fold: "What happens next" — gate this entirely on `engagement?.next_step_text`; render nothing if absent (empty-state-pattern). Do not fabricate "We'll reach out to schedule kickoff." (Pattern A violation, explicitly cited in CLAUDE.md).
- Empty-state: If no quote exists yet, render: "Your proposal is being prepared." (per UX-004 resolution; this is the one sanctioned holding-screen phrase).

State 2 — Post-signature, pre-payment (quote accepted, deposit invoice sent):

- Above fold: "Proposal signed." prose (text-heading, complete color) → deposit invoice card (reference line, amount in mono, due date, "Pay now" primary CTA linking to Stripe hosted URL in new tab)
- Note: "Pay now" is the one primary. No secondary CTA on the invoice card. Redundancy ban (Pattern 02): if amount is displayed as the invoice amount, do not also say "50% deposit"; the reference line carries the type.

State 3 — Active engagement (deposit paid, engagement active):

- Above fold: engagement status as prose in subtitle position (Pattern 01: detail-page state IS the page identity — prose, not pill) → current milestone name (authored from `milestones.name`; if null render "TBD in SOW") → milestone description if authored
- Below fold: upcoming milestone list (TimelineEntry component); consultant block (gate on `engagement?.consultant_name` — render nothing if absent per empty-state-pattern)
- No progress bar of any kind (portal-ux-brief anti-pattern list; explicitly prohibited)

State 4 — Safety net (handoff complete, safety net active):

- Above fold: "Engagement complete." prose → safety net end date (authored `engagements.safety_net_end`; render in ISO date format per identity) → "Questions? Email us" link (authored consultant email or `team@smd.services`; no fallback fabrication)
- Below fold: completion invoice card if present

**Desktop adaptation (1280px):**

- Two-column layout: primary content (scope/invoice/milestone) left, consultant block + contact rail right
- Right rail carries consultant photo (placeholder per photo-placeholder-rule if absent), next scheduled touchpoint (authored or absent — never fabricated)
- Max content width 1040px centered

**Content hierarchy (mobile):**

- h1: Business name or engagement title
- h2: Section labels (Proposal / Engagement / Documents)
- h3: Sub-sections within sections
- No heading skips (Pattern 04 compliant)

**Empty state:** Each section gates on its data. Sections with no data render nothing. The "proposal being prepared" copy (UX-004) is the only sanctioned holding phrase. No other fabricated copy.

**Loading state:** Skeleton at section level. No content-shaped skeletons (avoid implying structure that doesn't exist). Full-width gray bars at heading and body positions. Show within 0ms of navigation (no delay before skeleton).

**Error state:** Full-page error with consultant contact link. "We ran into a problem loading your portal. Email us at team@smd.services." No generic "something went wrong" without a next step. No 500-page without contact affordance.

---

### 2. Quote Detail with Sign (`/portal/quotes/[id]`)

**Exists.** `QuoteDetail.astro` component and `portal/quotes/[id].astro` page implemented. Two active UI-PATTERNS violations documented (Rule 1, Rule 2) — noted for remediation.

**Layout (mobile-first, 390px)**

Five states (per portal-ux-brief): isSent / isSigned / isDeclined / isExpired / isSuperseded

State: isSent (unsigned — action surface)

- Above fold: eyebrow "Proposal" (text-label, mono caps — NOT a pill; see Pattern 01 Rule 1 anti-pattern at quotes/[id].astro:207-210) → engagement title (h1, text-display) → scope summary (2-3 problems in plain language, text-body-lg) → total project price (IBM Plex Mono, money scale) → payment structure caption (authored only) → primary CTA "Review and sign" (full-width, solid primary, thumb zone)
- Below fold: line items (problem description only — no hours, no per-item price; BR-018, BR-042), exclusions list (authored from SOW), timeline section (TBD in SOW if not set)

State: isSigned (post-signature — informational)

- Drop the primary CTA. Replace with signed confirmation block: "Signed {ISO date}." (text-caption, complete color) — single rendering, no pile. Pattern 02 fix: remove the pill AND the "Signed {date}" redundant block; keep only the prose confirmation with date (see Rule 2 anti-pattern at quotes/[id].astro:458-497).
- "View your deposit invoice" secondary link (tertiary button or inline text link)

State: isExpired

- Status as prose in subtitle: "This proposal expired on {ISO date}." (text-heading)
- No CTA. "Contact us to discuss next steps." with authored email link.

State: isDeclined / isSuperseded

- Inline status prose per state. Superseded state renders: "A revised proposal is available." with link to new quote (per existing implementation at quotes/[id].astro:252-255 — this is cited as correct by empty-state-pattern.md).

**Desktop adaptation:**

- Two-column: scope details left (primary column), signing action card right (sticky, eye-level)
- SignWell iframe (when signing): full-width in primary column; scope summary collapsible sidebar (right column collapses to accordion on iframe active)

**Empty state:** If `quote.line_items` is empty or `quote.sow_path` is null, do not render a scope section with fabricated content. Render the section header and "Scope details will appear here once the proposal is finalized." — this is a TBD-marker context (the client expects to see scope).

**Loading state:** Skeleton at card level for invoice card, skeleton lines for line items.

**Error state:** If SignWell iframe fails to load: "The signing document isn't available right now. Email us at {authored email} and we'll send a direct link." (UX-002: iframe fallback required as Phase 2 DOD gate).

**Remediation notes for existing screen:**

- Pattern 01 violation at line 207-210: eyebrow "Proposal" rendered as pill → fix to text-label eyebrow
- Pattern 02 violation at line 458-497: "Signed" pill co-rendered with "Signed {date}" confirmation block → keep confirmation block only, remove pill when state is signed
- Pattern 05 violations: 32 arbitrary inline sizes → convert to scale tokens

---

### 3. Engagement Detail with Parking Lot (`/portal/engagement`)

**Exists.** `portal/engagement/index.astro` implemented. Active Pattern 02 violation documented (pill + prose redundancy at lines 125-131 and 144-148).

**Layout (mobile-first, 390px)**

This is the status-checker surface. The question it answers: "Where are we and what's next?"

Above fold:

- Section eyebrow: "ENGAGEMENT" (text-label, mono caps, hairline underline per identity)
- Current milestone name (h1 or h2 depending on page hierarchy — authored from `milestones.name`, status = in_progress; if no in-progress milestone, most recent completed; if none, "TBD in SOW")
- Milestone description (text-body, authored — no fabrication)
- Engagement status as prose subtitle (Pattern 01 Rule 1: detail page uses prose, not pill — this is the fix for the existing violation at lines 125-131; drop the pill, keep prose)

Below fold:

- Timeline of milestones: `TimelineEntry` component per milestone (name, status, completed_at if set). Status via dot + label pattern (Pattern 01: single-item card context) or eyebrow treatment. Not pill — milestones are not dense-list context.
- Parking lot section: Phase 5 — read-only list of parking lot items and their dispositions. Gate entirely on `parking_lot_items.length > 0`; render nothing if empty (no "Your parking lot is empty" message — client does not expect this section to exist).
- Consultant block: gate on `engagement?.consultant_name`; omit if absent.
- Safety net card (if status = safety_net): "Your engagement is in the support period. Ends {ISO date}." + contact link. No fabricated timeframe language (the 14-day window comes from `safety_net_end` authored field, not hardcoded prose).

**Desktop adaptation:**

- Timeline expands to readable width (max 680px content column)
- Consultant block moves to right rail

**Empty state:** If no engagement exists (pre-activation state), render nothing on this tab — the persistent nav tab for "Progress" should not be shown if there is no active engagement (PRD §9: "tabs visible only when section has content").

**Loading state:** Skeleton milestone entries (3 rows of skeleton lines).

**Error state:** Inline at section level: "Couldn't load engagement details. Refresh to try again."

**Remediation notes:**

- Pattern 02 violation at lines 125-131 and 144-148: engagement status pill + "Current Milestone: {status}" prose redundancy → drop the pill, keep prose only
- Pattern 01 applies: detail-page archetype, state in prose
- Pattern 05: arbitrary inline sizes throughout

---

### 4. Invoice Detail (`/portal/invoices/[id]`)

**Exists.** `InvoiceDetail.astro` component and `portal/invoices/[id].astro` implemented. Active Pattern 02 triple-redundancy violation documented (lines 450-461).

**Layout (mobile-first, 390px)**

States: isUnpaid / isOverdue / isPaid

State: isUnpaid or isOverdue:

- Reference line: "REF {invoice_id} / ISSUED {ISO date}" (IBM Plex Mono caps, text-label, hairline underline) — status tag at right end of reference line if overdue: rectangular mono-cap tag "OVERDUE" in error color
- Amount: MoneyDisplay at hero size (IBM Plex Mono, tabular-nums)
- Due date: text-caption below amount (natural language in prose context: "due Friday" — or ISO in data row context)
- Primary CTA: "Pay now" (full-width, solid primary, 44px min height, links to `stripe_hosted_url` in new tab)
- Secondary: "Download invoice" ghost button (if PDF link exists)
- ACH recommended note (small text-caption, muted): per BR-036 and PRD §9 ("ACH recommended note")
- Consultant block: gate on consultant data

State: isPaid (receipt surface):

- Amount: MoneyDisplay (same hero size)
- Single prose confirmation: "Paid {ISO date}." (text-caption, complete color) — ONE rendering only. Pattern 02 fix: remove the "Paid" pill AND the "Paid in full" caption; keep only "Paid {date}." prose (see Rule 2 anti-pattern at invoices/[id].astro:450-461).
- No CTA. "Download receipt" ghost button if receipt URL exists.

**Empty state:** Not applicable — invoice detail requires an invoice record to render; 404 if not found.

**Loading state:** Skeleton at hero amount position + skeleton for metadata rows.

**Error state:** If `stripe_hosted_url` is null: "Payment link unavailable. Email us at {authored email} to arrange payment." No broken "Pay now" button.

**Email touchpoint:** After Stripe payment, `invoice.paid` webhook fires → system sends payment confirmation email via Resend. The portal reflects the "Paid" state on next load (no real-time update; page-load freshness is acceptable per portal-ux-brief constraints).

**Remediation notes:**

- Pattern 02 triple-redundancy violation at lines 450-461: "Paid" pill + "Paid in full" caption + "Paid {date}" prose → consolidate to single "Paid {ISO date}." prose in complete color
- Pattern 05: 27 arbitrary inline sizes → convert to scale tokens

---

### 5. Admin Engagement Detail (`/admin/engagements/[id]`)

**Exists.** Desktop-first surface; dense information display acceptable per platform constraints (PRD §14).

**Layout (desktop-first, ≥1024px)**

This is the operational nerve center for an active engagement. Admin needs speed and accuracy over aesthetics.

Above fold (sticky breadcrumb + status bar):

- Breadcrumb: "Dashboard / Entities / {Entity Name} / Engagement" (caption, chevron-separated; last crumb primary color, not linked)
- Engagement title and reference line: "REF ENG-{id}" (IBM Plex Mono caps)
- Status tag: rectangular mono-cap (statusBadgeClass helper) — current engagement status
- Stage-transition button group: "Advance to Handoff" (primary) or appropriate transition label; "Cancel engagement" (destructive, rightmost); secondary actions as ghost buttons. Labels drive meaning — no rainbow color-coding (admin-ux-brief).

Primary content (two-column split):

Left column (primary — ~65% width):

- Milestones panel: collapsible, open by default. Table: milestone name / status tag / due date / completed date / payment trigger flag. Inline "Add milestone" at bottom (tertiary button). Tab + Enter keyboard nav.
- Time entries panel: collapsible. Table: date / hours / category / description. Running total estimated vs. actual hours (IBM Plex Mono tabular-nums). "Log time" inline form or inline-expandable row.
- Parking lot panel: collapsible. Table: description / requested by / requested at / disposition tag. "Add item" at bottom. Disposition actions per row: "Fold in" / "Follow on" / "Drop" — overflow menu (Pattern 08: View → action options → separator → Drop in error color).
- Activity / context log: chronological timeline of state changes and notes. Mono-caps date + actor prefix per identity. "Add note" inline.

Right column (~35% width, sticky):

- Client context card: business name, primary contact name, contact roles (Owner / Decision Maker / Champion as labeled rows)
- Invoice panel: deposit invoice status + amount + paid date; completion invoice status + "Create completion invoice" action when engagement reaches handoff; any milestone invoices
- Contact roles panel: engagement_contacts table (contact name, role, is_primary flag)
- Document upload zone: Phase 4 — drag-and-drop or file picker; uploaded documents list with presigned R2 download URLs

**Empty states:**

- No milestones: "Add milestones to track progress." (inline link to add)
- No time entries: "No time logged yet."
- No parking lot items: render nothing (operator knows this section exists; empty state is obvious)
- No documents: "No handoff documents uploaded yet." (Phase 4)

**Loading state:** Skeleton at panel level (each collapsible shows skeleton lines while loading).

**Error state:** Top-of-page error strip (not toast — admin errors must stick per admin-ux-brief). "Failed to load engagement. {Specific reason}. Refresh or contact engineering."

---

## Navigation Model

### `smd.services` — Marketing

**Pattern:** Pyramid with persistent top nav (NN/g §1.4)

- Persistent top nav: Logo left, primary nav links center/right (How it works / Who we help / Get started), "Book a call" primary CTA rightmost
- Footer: contact link, admin sign-in link (low-visibility)
- Max depth: 2 (home → any page)
- No mobile hamburger in MVP; if needed, use a text-only disclosure menu (not a drawer)
- No portal or admin chrome visible on marketing pages

### `portal.smd.services` — Client Portal

**Pattern:** Persistent tabs (NN/g §3.1; NAVIGATION.md §4.4 decision, v3 → v3.1 migration)

**Decision rationale (from NAVIGATION.md §4.4):** Hub-and-spoke disqualified because 2 of 3 high-frequency tasks (pay-invoice, review-sign-proposal) have `return_locus=external` (Stripe, SignWell). Persistent tabs allow users to return to any section after external exits without relying on the hub.

**Mobile chrome (390px):**

- Persistent tab bar fixed at bottom of viewport
- Four tabs: Dashboard (home icon) / Proposals (document icon) / Invoices (receipt icon) / Progress (timeline icon)
- Documents accessible via Documents tab within the persistent nav (5th tab considered; keep at 4 per Apple HIG bottom nav maximum; Documents is the least-visited surface and can live in a 5th tab if needed post-launch — defer)
- Tab labels: short nouns, no verbs — "Home" / "Proposals" / "Invoices" / "Progress"
- Active tab: solid icon + primary color label
- Inactive tab: outline icon + muted label
- **Tab visibility rule:** per PRD §9, tabs only show when the section has content. In pre-signature state: "Proposals" tab always shows (quote exists). "Invoices" shows after quote signed. "Progress" shows after engagement active. Documents shows after first document uploaded. Implementation: render tabs conditionally based on data presence, not route.
- No hamburger, no sidebar, no drawer
- Maximum depth from any portal page: 2 taps to any primary feature from home (PRD §14 platform constraint — verified: Home → [tab] → list → detail = 3 taps; Home → ActionCard on dashboard → invoice detail = 1 tap; Home → tab → detail via list = 2 taps)

**Desktop chrome (1280px):**

- Top sticky masthead: "SMD SERVICES" left (IBM Plex Mono caps) + ISO date right + logout link
- Below masthead: horizontal tab row (same 4 tabs, rendered as a nav bar) — no left sidebar
- Client label: "Client · {Business Name}" (IBM Plex Mono caps, text-label, below masthead or inline)
- Contact icons: email / SMS / phone affordances in header right area, gates on authored consultant contact data
- Max content width: 1040px centered with generous side margins (per portal-ux-brief)
- No persistent sidebar (portal-ux-brief anti-pattern list: "Sidebar navigation" is explicitly banned)

**Back navigation:**

- Detail pages (quotes/[id], invoices/[id]) show a back button ("← Proposals" / "← Invoices") linking to the parent list
- Persistent tabs always visible; tab state indicates current location
- No breadcrumbs in portal (client audience; breadcrumbs are an admin pattern)

### `admin.smd.services` — Admin Console

**Pattern:** Hub-and-spoke with tabs (NAVIGATION.md §3.2; hub at `/admin`, spokes at entities, follow-ups, analytics)

**Desktop chrome:**

- Sticky top nav: "SMD SERVICES · Admin" left (IBM Plex Mono caps) + primary nav tabs center (Dashboard / Entities / Follow-ups / Analytics / Generators) + session email + sign-out right
- Tabs correspond to top-level sections; current section highlighted
- Sub-navigation via breadcrumbs on deep pages: "Dashboard / Entities / {Name} / Quote Builder"
- Settings link in nav (low-visibility; far right or in a utility area)
- No mobile optimization required (PRD §14); layout assumes ≥1024px

**Breadcrumb spec:**

- Format: "Dashboard / Entities / {Entity Name} / {Leaf label}" — per admin-ux-brief
- Chevron separators, text-caption size, sans-serif
- Last crumb: text-primary, not linked; all prior crumbs: text-secondary, linked
- Breadcrumbs appear on all entity-scoped deep pages; absent on top-level list/dashboard pages

**Max depth:** 3 (Dashboard → Entity → Quote Builder / Assessment / Engagement)

**Sidebar panels:** Not top-level nav; used within entity detail for collapsible sections (engagements, quotes, invoices, assessments, contacts)

---

## User Flows

### Flow 1: Magic-link login → First portal view → See scope

**Entry context:** Marcus receives the portal invitation email on his phone. He is between service calls. He taps the "View your proposal" CTA immediately.

1. Marcus taps "View your proposal" in the Resend email. The magic link token is embedded in the URL.
2. Browser opens `/auth/verify?token={token}` (or the in-app browser within Gmail iOS app — test required per UX-002 analog for magic links).
3. Server-side: middleware validates token (not used, not expired) → marks `magic_links.used_at` → creates KV session → sets `__Host-portal_session` cookie → redirects 302 to `/portal`.
4. No intermediate "you are now logged in" screen renders. Redirect is immediate.
5. Marcus lands on `/portal`. Dashboard renders in pre-signature state.
6. Above fold: his business name eyebrow → scope summary headline → project price in IBM Plex Mono → "Review and sign" primary CTA. No scrolling required on 390px viewport (PRD §7 Step 3 constraint verified by portal-ux-brief: "[Pay invoice] button's top edge sits at y ≤ 700px" — same applies to "Review and sign" CTA in pre-signature state).
7. Below fold: authored "What happens next" content (if `next_step_text` is set) or nothing (empty-state-pattern).
8. Persistent tab bar shows at bottom: "Home" (active) / "Proposals" / — (other tabs hidden until data exists).

**Expired token path (EC-002):**
3a. Token is expired (>15 minutes). Server redirects to `/auth/portal-login?error=expired`.
4a. Portal login page renders with friendly message: "That link has expired. Enter your email to get a new one." (one-field form; no account creation required; no jargon).
5a. Marcus enters email. `POST /api/auth/magic-link` fires. Response is always 200 (no email enumeration, BR-027-030). Resend sends new link.
6a. "Check your email for a new link." confirmation renders on same page. No redirect.

**Already-used token path (EC-003):**
3b. Token already marked `used_at`. Same recovery flow as expired — same `/auth/portal-login?error=used` destination with same friendly message. Do not expose "this link has already been used" as a distinct error; it enables session-fixation fishing. Treat as expired.

---

### Flow 2: Quote review → SignWell signing → Return to portal with countersigned status

**Entry context:** Marcus has arrived at the portal (Flow 1 complete). He is on his phone, still between calls.

1. Marcus sees the "Review and sign" primary CTA above the fold on `/portal`. Taps it.
2. Navigation options:
   - Option A (current implementation): CTA transitions the dashboard to a signing sub-view (state machine within the same page).
   - Option B (PRD §9 spec): CTA navigates to `/portal/quotes/[id]` → then to `/portal/quotes/[id]/sign`. Recommend Option B for deep-linkability; the invite email CTA can link directly to the signing surface.
3. **On `/portal/quotes/[id]` (quote detail, isSent state):**
   - Above fold: engagement title → scope summary (2-3 problems in plain language; no hours, no per-item price; BR-018) → total project price → payment structure caption → "Review and sign" primary CTA
   - Below fold: full line-item descriptions, exclusions, timeline
4. Marcus taps "Review and sign." Navigation to signing context.
5. **Signing surface (either state in quotes/[id] or `/portal/quotes/[id]/sign`):**
   - On mobile (< 768px): SignWell iframe renders full-width. Collapsible scope summary accordion above (collapsed by default — PRD §7 Step 5; keeping scope accessible without burying the iframe).
   - On desktop: scope summary sidebar left (sticky), SignWell iframe in primary column.
   - Loading state: "Loading signing document..." with spinner (not skeleton — iframe load is not predictable in shape).
   - Iframe failure fallback: "Document unavailable. Email us at {authored email} to sign via a direct link." (UX-002 mitigation).
6. Marcus signs within the SignWell iframe. SignWell fires `document.completed` webhook.
7. Webhook handler (server-side):
   - Validates HMAC-SHA256 signature (BR-034)
   - Checks for idempotency: if quote already `accepted`, returns 200 (EC-004, BR-037)
   - Downloads signed PDF from SignWell → uploads to R2 at `sows/{org_id}/{quote_id}/signed-sow.pdf`
   - Updates `quotes.status = accepted`, sets `accepted_at`
   - Creates engagement record (status: `scheduled`)
   - Creates deposit invoice (type: `deposit`, amount = `deposit_amount`)
   - Sends deposit invoice + confirmation email via Resend
8. **Portal return:** After SignWell external flow completes, SignWell redirects back to the portal. Marcus sees:
   - If on quote detail: isSigned state renders. "Signed {ISO date}." prose confirmation. "View your deposit invoice" secondary link.
   - OR: auto-redirect to `/portal` dashboard, now in post-signature state, with deposit invoice card prominent.
   - Recommend: redirect to `/portal` with a session-level banner "Your proposal is signed. Your deposit invoice is ready below." (not a toast — Marcus may not see it; prefer anchored in-page element). Dismiss on tap.
9. Dashboard now shows deposit invoice ActionCard: amount, due date, "Pay now" primary CTA.

**Email touchpoint:** Confirmation email via Resend fires at step 7. Subject: "{Business name} — your proposal is signed. Invoice enclosed." Do not engineer around this email — it is the client's record and the invoice trigger.

---

### Flow 3: Parking lot review → Comment / Accept / Defer

**Entry context:** Scott (admin) has received notification that the engagement is nearing handoff. He needs to disposition all parking lot items before issuing the completion invoice. He is on his MacBook at his desk.

**Pre-condition:** Engagement is in `active` status. Parking lot items exist with `disposition = null`.

1. Scott navigates to `/admin/engagements/[id]`. Breadcrumb: "Dashboard / Entities / {Entity Name} / Engagement."
2. Parking lot panel is visible in the left column (collapses are expanded by default when items are pending; pending items are visually flagged — status tag "UNDISPOSITIONED" in attention color, or simply no disposition tag).
3. Parking lot table renders: description / requested by / requested at / disposition (empty for undispositioned items).
4. For each item, row has a kebab overflow menu (Pattern 08: overflow menu at row end, always visible):
   - Menu order: "Mark as fold-in" → "Create follow-on quote stub" → (separator) → "Drop"
   - No inline action buttons per row — the overflow menu IS the action surface (Pattern 08: one inline action max; here the default action is none since all three options require a decision).
   - Destructive option: "Drop" last, separated, error color (apple HIG + Pattern 08 ordering).
5. Scott selects "Mark as fold-in" on an item.
6. Inline modal (or inline-expand within the row — prefer inline-expand to avoid deep modal stacks per admin-ux-brief): "Fold-in note (optional):" → text input → "Confirm" primary + "Cancel" ghost.
7. Scott types a note, taps "Confirm."
8. `PATCH /api/admin/parking-lot/{id}` fires with `{ disposition: "fold_in", disposition_note: "{text}", reviewed_at: now }`.
9. Row updates inline: disposition tag "FOLD IN" (rectangular, mono caps) + note displayed in a secondary row or tooltip. No page reload.
10. Scott selects "Create follow-on quote stub" on another item.
11. `PATCH /api/admin/parking-lot/{id}` fires with `{ disposition: "follow_on" }`. System creates a draft quote stub (prefilled with the parking lot description as a line item). `follow_on_quote_id` is populated.
12. Row updates: "FOLLOW ON" disposition tag + inline link "View quote stub →" linking to the new draft quote at `/admin/entities/[id]/quotes/{new_quote_id}`.
13. Scott selects "Drop" on a third item.
14. Destructive confirmation: not a full modal — inline-in-row expand: "Drop this item? It won't appear in the follow-on proposal." → "Drop" (destructive, error fill) + "Cancel" (ghost).
15. Confirm → `PATCH /api/admin/parking-lot/{id}` with `{ disposition: "dropped" }`. Row renders "DROPPED" tag (muted/neutral tone).
16. When all items are dispositioned, the section header shows a completion indicator: "All items reviewed." (prose, no celebratory animation — Plainspoken register).
17. Scott can now advance the engagement status to "Handoff" via the stage-transition button. System computes `safety_net_end = handoff_date + 14 days` (BR-040). Completion invoice is created or triggered.

**Empty state (no parking lot items):** Render nothing in the parking lot panel. Scott is an operator — he knows whether the engagement had scope expansions. Do not show "No parking lot items" messaging; just omit the panel header.

**Email touchpoint:** No system email fires on parking lot disposition. These are internal operational actions. Client sees parking lot items in Phase 5 portal view (read-only) after disposition.

---

## Form Patterns

### Input styles

- Text inputs: full-width, border `--color-border`, 0 radius (identity-consistent), `text-body` size, `--color-text-primary` color, `--color-surface` background
- Label: above input always — never placeholder-only (WCAG §4.1, accessibility requirements; PRD §14). `text-label` token, `--color-text-primary`, required fields marked "(required)" in label text not with asterisk alone
- Focus state: 2px ring, 2px offset, `--color-action` (action color per identity)
- Disabled state: `--color-border-subtle` border, `--color-text-muted` text, `not-allowed` cursor

### Validation timing

- **On submit only for required fields** (not aggressive inline validation). Portal clients (Marcus) should not see red errors while still typing.
- **On blur for format-critical fields** (email, phone number) — validate format after the user leaves the field.
- **Real-time for the quote builder line item totals** — `total_price` updates as hours change (PRD §9 quote builder interaction: "Totals update in real time"). This is the one real-time validation context; it's numeric, not error-state.

### Error placement

- **Field-level:** error message appears directly below the offending input, visually connected. `text-caption` size, `--color-error` color. `aria-describedby` links message to input. Message is complete: "Business name is required." not "Required."
- **Form-level (admin quote send):** top-of-form error strip for blocking conditions (e.g., "No primary contact with a valid email. Add a contact before sending." — EC-001). Strip stays visible until the condition is resolved. Does not auto-dismiss (admin-ux-brief: "toasts are ephemeral; operators need the error to stick").
- **Portal forms (email input on /auth/portal-login):** inline below input, friendly. "Enter a valid email address." No field-level strip needed for a single-field form.

### Required indicators

- Label text: "{Field name} (required)" — spelled out, not symbol-only
- Accessibility: required attribute on input, aria-required="true"
- Optional fields: no "(optional)" label unless the form has more optional than required fields (in which case label required fields and leave optional unmarked per NN/g convention)

### Keyboard navigation (quote builder)

Per PRD §9 and §14: Tab between fields, Enter to add a new line item row, Delete/Backspace on an empty row to remove it. This is the admin-critical keyboard pattern for the post-assessment quote build workflow.

---

## Feedback Patterns

### Toast/notification style

- Transient success toasts: used in portal for low-stakes confirmations where the state change is visible on screen. Toasts are ephemeral by intent — use only when the confirmation is reinforcing visible state, not when it IS the communication.
- Toast position: bottom-center on mobile (above the tab bar — critical: tab bar is fixed; toast must not overlap it), top-right on desktop
- Duration: 4 seconds auto-dismiss, tap/click to dismiss early
- No celebratory animation (Plainspoken register: evidence over reassurance)
- Text: past tense, concrete. "Link sent." not "Magic link sent successfully!" not "All set!"

### Success confirmations

- **Portal signing:** in-page prose replacement, not toast. The isSigned state IS the confirmation. (Pattern 02: one signal per fact — if the page state shows "Signed April 14," a toast that also says "Signed!" is noise.)
- **Invoice paid:** page state updates on next load. The isPaid state is the confirmation. A Resend email also fires (the client's primary record of payment).
- **Admin parking lot disposition:** inline row update. Tag appears; no toast needed for individual item dispositions.
- **Admin stage transition:** brief top-of-page confirmation strip: "Moved to Handoff." auto-dismisses after 4 seconds. Admin watches for this to confirm the action landed.

### Destructive confirmations

- Pattern: inline-expand within the action row or a narrowly scoped dialog — not a full-page modal
- Confirm text: concrete and specific. "Cancel this engagement? Milestones and invoices remain on file." (per admin-ux-brief copy example)
- Two actions: destructive CTA (error fill, specific label — "Cancel engagement") + neutral "Go back" or "Keep engagement"
- Never auto-confirm destructive actions. Always require an explicit tap on the destructive CTA.
- Portal: "Decline this proposal?" — if this becomes a feature. Not in MVP scope.

### Progress indicators

- **Page loading:** skeleton at section level (portal), skeleton at card/panel level (admin). No full-page spinner.
- **Form submit:** disable the submit button, replace label with an inline spinner + "Sending..." During the `POST /api/admin/quotes/:id/send` flow (which triggers PDF check, SignWell document creation, Resend email, follow-up scheduling), the button may be loading for 2-3 seconds. Disable + label prevents double-submit (EC-004 prevention on the client side; server-side idempotency handles the rest).
- **SignWell iframe:** "Loading signing document..." centered within the iframe container while the iframe src loads. Spinner centered. Not skeleton — iframe layout is not predictable.
- **R2 upload (transcript):** linear progress bar (or indeterminate spinner if progress events unavailable from the fetch). "Uploading transcript..." label. On complete: "Transcript uploaded." success strip. On failure: "Upload failed. Try again." retry button.
- **File download (documents):** no progress indicator — presigned R2 URL opens in new tab. The browser handles download progress natively.

### Email as a channel

The following user-facing actions trigger a Resend email. The portal reflects the same state change on next load — email and portal are parallel channels, not sequential:

| Trigger                         | Email sent                                        | Portal reflects                                                   |
| ------------------------------- | ------------------------------------------------- | ----------------------------------------------------------------- |
| Quote sent (`US-009`)           | Portal invitation with magic link + scope summary | Quote appears in `/portal/quotes`, dashboard shows pre-sign state |
| SOW signed (`US-011` webhook)   | Deposit invoice + confirmation                    | Dashboard → post-sign state; deposit invoice visible              |
| Deposit paid (`US-013` webhook) | Engagement activation confirmation                | Dashboard → active engagement state                               |
| Follow-up Day 2/5/7 (Phase 5)   | Proposal follow-up email                          | No portal state change                                            |
| Handoff complete                | Completion invoice + handoff confirmation         | Dashboard → completion state; completion invoice visible          |

Design implication: the portal's state changes MUST be visible on the next page load, not dependent on the client reading the email. The email is a prompt; the portal is the record.

---

## Responsive Strategy

### Portal — mobile-first

**Primary breakpoint: 390px (phone)**

- Single-column layout throughout
- Persistent tab bar fixed at bottom (44px minimum touch target per row, 48px recommended for tab bar height to accommodate icon + label)
- Content scrolls behind the tab bar — bottom padding on page content equal to tab bar height + 16px
- Above-fold constraint: primary CTA must be visible at y ≤ 700px (portal-ux-brief acceptance criterion) without scroll. Achieved by limiting pre-CTA content to: eyebrow + headline + price + optional caption + CTA.
- Signature iframe: full-width (`width: 100%; height: calc(100vh - {masthead height} - {tab bar height})`). Not fixed pixels — respects device viewport per UX-002 constraint.
- No horizontal scroll at any breakpoint (WCAG 1.4.10 reflow).

**Desktop adaptation: 1280px**

- Max content width 1040px centered
- Two-column layout on dashboard (primary content + right rail)
- Tab bar migrates to horizontal top nav below masthead
- Sidebar (not left-rail nav — portal-ux-brief bans sidebar navigation; this means the persistent nav moves to a top tab strip, not a sidebar)
- SignWell iframe: two-column (sidebar scope summary left, iframe right)

**Touch target compliance (WCAG 2.5.5 AA):**

- All tab bar items: 44x44pt minimum
- All document download buttons: 44px height minimum
- All milestone status indicators that are interactive: 44pt touch target
- Row-level tappable areas extend full width (not just the CTA within the row)

### Admin — desktop-first

**Primary breakpoint: ≥1024px**

- Sticky top nav with tab links
- Side-by-side panels on entity detail and engagement detail
- Dense tables with tight row spacing (`space-row` token: 12px)
- Resizable panels: left/right column split on entity detail allows admin to expand the primary content area (PRD §14 admin constraint)
- Print/export: browser native print for SOW preview (PRD §14)

**No mobile optimization in MVP.** If accessed on phone, layout degrades gracefully — stacked single column, horizontal scroll on tables. This is acceptable for an internal operator tool used primarily on a MacBook.

**Keyboard navigation (quote builder):**

- Tab order: problem label dropdown → description textarea → hours input → (implicit: next row)
- Enter on hours input: adds new row (focus moves to new row's problem label)
- Backspace/Delete on empty hours input: removes row (focus moves to previous row's hours)
- This requires a React island for the line item editor — pure Astro SSR cannot handle this interactivity. (ADR-002 calls for React islands for quote builder; this confirms the decision.)

### Marketing — standard responsive

- Breakpoints: 390px (mobile), 768px (tablet), 1280px (desktop)
- No portal-specific constraints
- Mobile nav: text-based disclosure menu or hamburger for the marketing top nav (standard pattern; not constrained by PRD's portal mobile rules)

---

## Open Design Questions and Gaps

The following questions arose from the PRD-to-screen mapping. They are not blocking issues but should be resolved before the Phase 2 implementation sprint:

1. **Signing route disambiguation.** Is `/portal/quotes/[id]/sign` a distinct URL or a state within `quotes/[id]`? The PRD §9 lists it as a distinct route; the current codebase renders it as a state. If the signing view is deep-linked from the invitation email, a distinct route is required. Recommend resolving before Phase 2 ships.

2. **Dashboard tab visibility gating.** PRD §9: "tabs visible only when section has content." The implementation logic for which tabs appear in which states needs to be documented and tested (pre-signature: show Proposals tab but not Invoices; post-signing: show Invoices; post-activation: show Progress; after first document upload: show Documents).

3. **"What happens next" content.** PRD §7 Step 3 describes a "What happens next" three-step explainer on the first authenticated screen. Per empty-state-pattern, this must gate on authored data (`next_step_text`). But PRD resolution UX-004 says: "Your proposal is being prepared" is the holding screen. These need reconciling — the holding phrase applies pre-quote, not post-quote. Post-quote, authored next-step content should display or nothing should display. No fabricated three-step explainer.

4. **Champion portal access (UX-001).** Deferred to Phase 4 per resolution. Document the data model implication: `engagement_contacts` already has the champion role; portal auth currently scopes to a single `client_id`. Phase 4 will require a second magic-link invitation flow for the champion contact (Rachel persona).

5. **Parking lot portal view timing.** PRD §9 lists parking lot section under `/portal/engagement` with Phase 5 scope note. Confirm: parking lot items appear in the portal ONLY after admin dispositions them at pre-handoff review, not during the engagement. This prevents the client from seeing scope-expansion requests before SMD has reviewed them.

6. **Admin `entities` vs. `clients` nomenclature.** The PRD uses `clients` consistently; the codebase uses `entities`. The routes are `/admin/entities/[id]`. This is an intentional implementation choice (the code term is more general). Document it and use `entities` in admin UI copy where appropriate, `clients` in business-layer and data model references. No remediation required; just ensure consistency within each context.

7. **Pattern 08 adoption in portal.** The portal has no list surfaces with row-level edit/delete actions (clients do not manage data). Pattern 08 applies to admin list surfaces. Portal list surfaces (quotes list, invoices list, documents list) use the master-detail pattern: row click = open detail. No overflow menu needed on portal list rows.
