# Client Portal — UX Redesign Brief (final, post-identity-reset)

_Revised 2026-04-19 against the Swiss Functional identity (see `.design/DESIGN.md`). Earlier revision was written against the generic SaaS placeholder identity — palette, typography, chrome conventions, and anti-patterns all updated to match the new direction. Structural decisions (surfaces, visit modes, three concepts, accessibility floor) unchanged._

## Context

SMD Services sells scope-based consulting engagements to businesses doing $750k-$5M in revenue. The portal is the documented side of a relationship between our visits — not software the client bought.

The portal is the project file from a small design studio, printed to the client's screen. Authority through precision, not decoration.

## Scope

Seven client-portal surfaces, two viewports each (390px mobile, 1280px desktop). All are `surface=session-auth-client`:

| #   | Surface                                                | Archetype | Task (NAVIGATION.md §1) |
| --- | ------------------------------------------------------ | --------- | ----------------------- |
| 1   | `portal-home` (home dashboard, "engagement in flight") | dashboard | see-whats-happening     |
| 2   | `portal-quotes-list` (proposal list)                   | list      | review-past-proposals   |
| 3   | `portal-quotes-detail` (proposal deep-link / detail)   | detail    | review-sign-proposal    |
| 4   | `portal-invoices-list` (invoice list)                  | list      | review-past-invoices    |
| 5   | `portal-invoices-detail` (invoice deep-link / detail)  | detail    | pay-pending-invoice     |
| 6   | `portal-documents` (document library)                  | list      | find-document           |
| 7   | `portal-engagement` (engagement progress)              | detail    | check-progress          |

Principles, anti-patterns, money rule, photo rule, accessibility floor, copy tone, and data schema below apply to all seven surfaces. Per-surface intent is called out in the "Per-surface intent" section.

## Per-surface intent

Each surface has one primary visit-mode fit and a single above-the-fold question it must answer:

- **`portal-home`** — action responder / status checker. "What needs my attention, and what's happening with my engagement?" Dashboard archetype. Dominant action card (pending invoice if any) or "next check-in" card; timeline of past work below. Detailed in the worked example below.

- **`portal-quotes-list`** — document retriever / status checker. "Show me every proposal sent to me." List of proposal rows: title, status tag (Pending Review / Accepted / Declined / Expired, rendered as rectangular mono-cap tag), sent date, total amount (per the money rule — no per-item breakdown), expiry caption if applicable. Each row links to the detail view. Chrome: sticky masthead + persistent tabs.

- **`portal-quotes-detail`** — action responder. "Show me this proposal; let me sign it or download the PDF." Five states (isSigned / isDeclined / isExpired / isSuperseded / isSent) — see state-rendering rules. Above fold on mobile: engagement title + status tag + dominant action (Review and sign / Signed confirmation / Superseded banner). Consultant block required.

- **`portal-invoices-list`** — document retriever. "Show me every invoice sent to me." Same structural pattern as quotes list: title ("Invoice #abc123"), status tag (Due / Paid / Overdue), issue date, amount in dollars, due date caption. Each row links to detail.

- **`portal-invoices-detail`** — action responder. "Show me this invoice, let me pay it via Stripe, and let me download the PDF." States: isUnpaid (dominant [Pay invoice] CTA) / isPaid (receipt + amount paid + date) / isOverdue (same as unpaid but with attention-color status). Consultant block required. Stripe URL comes from props.

- **`portal-documents`** — document retriever. "Show me everything the engagement has produced." List of documents by category (SOW, deliverable, reference, etc.): title, type icon, date, [Download] or [Open] button. No money fields on this surface. Each row is a direct download / external-link action — no intermediate detail page.

- **`portal-engagement`** — status checker. "Where are we in the work, what's been done, what's next?" Chronological timeline of completed milestones (past tense, concrete — per the timeline schema), next scheduled touchpoint card, consultant block. No progress bars of any kind (see anti-patterns). Evidence over reassurance.

## Visit modes

- **Action responder** — from email/SMS deep link for a specific task
- **Status checker** — returning to see how things are going. Also serves the "representative" case: a client reporting back to their spouse or business partner. Same need: a glanceable, concrete summary of recent work.
- **Document retriever** — needs a specific file
- **First-time arrival** — just signed; needs to see what happens next and who's driving it

## Objectives, ranked

1. Confirm this is real and working — evidence of work in flight, not reassurance language
2. Take the action I was prompted to take
3. Understand where we are and what's next — concrete events, not a progress bar
4. Retrieve a document
5. Reach a named human via their preferred channel (SMS primary)

## Entry points (with email context)

- Proposal email → proposal ready to sign. Subject: "Your SMD proposal is ready." CTA: "Review proposal"
- SOW email → SOW. Subject: "Your SOW is signed — here's what's next." CTA: "Open SOW"
- Invoice email/SMS → invoice with payment. Subject: "Invoice #1023 due Friday, April 18." CTA: "Review and pay"
- Bookmark / direct → dashboard
- First login after signing → orientation

Deep links land on the thing, not on a dashboard that contains the thing.

## Design principles

- **Evidence over reassurance.** "Scott met with your dispatcher Tuesday; two issues surfaced" beats "Things are going well."
- **A named human, visible.** Real photo + next scheduled touchpoint on every surface. Nothing else substitutes.
- **Evidence, not theater.** No badges, testimonials, marketing chrome.
- **Printed dossier, not SaaS app.** Sharp corners, hairline structure, mono-anchored data. The page is the project file, not a product surface.
- **Precision over decoration.** Every reference number, every date, every dollar figure is typeset deliberately. Nothing is decorative.
- If the client forwarded a screenshot to their spouse, one glance confirms real work is happening.

## Identity chrome conventions (Swiss Functional)

These are the recurring structural/visual moves that define the aesthetic. Components in Step 5 must honor them:

- **Reference lines** cap every card at the top. Format: `REF {ID} / {ATTRIBUTE · VALUE} / {SECONDARY · VALUE}`. IBM Plex Mono, 12px (label token), uppercase, tracking 0.08em, `text-text-muted`. Separated from card body by a `border-border-subtle` hairline.
- **Section labels** introduce each major page section. Short, uppercase, mono-caps: `Activity log`, `Engagement`, `Ledger`, `Prior payments`. Rendered at the label token, underlined by a `border-border` hairline.
- **Status tags** are rectangular (0 radius), not pills. IBM Plex Mono caps, `text-label` size, background in the semantic color (`--color-attention` for Due, `--color-complete` for Paid, `--color-error` for failure), `text-white`. A small dot can precede the label for redundancy (`● STATUS · DUE`).
- **Dates render two ways.** In headlines: natural language ("due Friday", "April 2026 engagement"). In data rows, timeline entries, and reference lines: ISO (`2026-04-14`). Never mix registers within one element.
- **All money renders in IBM Plex Mono with `tabular-nums`.** No exceptions. The dollar sign is part of the value, not decoration.
- **Timeline entries read as log lines.** Format: `{ISO-date} · {ACTOR-CAPS}` as meta row (mono, label token), then narrative body in Switzer below. Optional artifact link in mono caps.
- **Hairlines, not shadows.** Cards separate from surface via 1px `--color-border`, interior rules via `--color-border-subtle`. The identity is flat.
- **Masthead at top of every surface.** Firm name or client label left, ISO date right, both in mono caps. Separated from page body by a 1px `--color-border` rule.

## Three concepts requested (structurally distinct, not visual variations)

**A — Timeline-centric.** The engagement narrative is the page. Actions appear inline at the relevant moment. On invoice-pending: the invoice pins to the top as today's entry.

**B — Conversation-centric.** Dated entries from the consultant, addressed to the client. Printed email archive, not Slack. **Read-only: no composer, no input field, no send button.**

- Desktop: most recent entry anchored bottom, older entries scroll above.
- Mobile: on pending-action states, the pending entry inverts and pins above the fold; older entries scroll below.

**C — Action-centric.** One primary object dominates the viewport based on state. Invoice-pending: the invoice _is_ the page, not the top of the page. Timeline collapses to one line or is absent. No-pending-action: next touchpoint is the dominant element.

## Worked example (fidelity reference)

Concept A, invoice-pending state, 390px above the fold (post-identity tokens):

- Masthead: `SMD SERVICES` left + `2026.04.19` right (IBM Plex Mono, 12/16, caps, tracking 0.08em)
- Client label: "Client · Delgado Plumbing" (IBM Plex Mono caps, label token 12/16)
- Headline: "Your April invoice is due Friday." (Switzer 700, 40/44, tracking -0.02em; mobile drops to 30/34)
- Invoice card:
  - Reference line: "REF INV-1023 / ISSUED 2026-04-15" left + "STATUS · DUE" right, with petrol blue attention dot (IBM Plex Mono caps, 12/16)
  - Amount: "$4,250.00" (IBM Plex Mono 500, 32/40 mobile, 48/52 desktop, tabular-nums)
  - Meta rows, 4 entries (IBM Plex Mono 500, 13/18, tabular-nums): Issued, Due, Method, Terms
  - [Pay invoice] primary button (44px min height, petrol blue `--color-primary`, 0 radius, thumb zone)
  - Secondary: [Download PDF] ghost button with `--color-border` outline
- Consultant block (separate card): photo placeholder 80px square, name "Scott Durgan" (Switzer 600, 18/24), role "Consultant" (IBM Plex Mono caps, label), next check-in line (IBM Plex Mono 500, 13/18)
- Below fold: timeline entries with mono date + actor prefix (`2026-04-14 · SCOTT`), body prose in Switzer (16/24, `text-text-primary`), optional artifact link (mono caps, underlined)

## Above-fold specs for B and C (matching A's format)

**B, invoice-pending, 390px above fold:** Pending-action entry inverts to top — dated header (`2026-04-14 · FROM SCOTT`, IBM Plex Mono caps), invoice card with amount + due date + [Pay invoice], consultant photo and next touchpoint underneath. Scroll reveals older dated entries below, each a log-line header + prose body.

**C, invoice-pending, 390px above fold:** Full-bleed invoice treatment dominates viewport — amount in large display type (IBM Plex Mono 500, 48/52 desktop / 32/40 mobile, tabular-nums), due date caption (mono 13/18), [Pay invoice] button in thumb zone, consultant block below the action, one-line recent-activity link at the bottom (`Last update · 2026-04-14`).

## What must be preserved

Typography (Switzer display, Switzer body, IBM Plex Mono data — see Appendix), palette (hex in Appendix), 0 radii (sharp) across surfaces and buttons (rectangular mono-cap tags, not pills), generous vertical rhythm on chrome (40px sections, 28px card padding), voice (guide persona, human and direct, no hype, no em dashes, no AI-flavored copy), minimal iconography (Material Symbols Outlined used sparingly — the data speaks louder than icons in this identity).

## What is open

Layout, hierarchy, component choice, grouping, navigation patterns, empty states, entire flow. Motion is locked (120ms color transitions only; no scroll-driven or page-transition effects per identity).

## Anti-patterns (do not produce)

Existing (pre-identity) anti-patterns, still banned:

- 3-up equal-weight tile grid
- "Welcome back, [Name]!" greetings
- Progress bars of any kind — including stepped, segmented, percentage, or radial
- Illustrated empty states
- KPI stat cards
- Tabbed dashboards
- Sidebar navigation
- Trust badges, testimonials, marketing chrome
- Stock imagery of business owners
- Chat UI with composer/input on Concept B
- Initials avatars (use consultant photo or neutral portrait placeholder)
- Softening or patronizing copy ("Don't worry", "We've got you covered")
- Jira-speak milestone names ("Process documented for new client intake")

New (identity-level) anti-patterns introduced by the Swiss Functional direction:

- **Pill-shaped status badges** (fully rounded). Status renders as a rectangular mono-cap tag.
- **Elevation / shadows of any kind.** The identity is flat. Hairlines and typographic hierarchy do the work.
- **Non-mono dates, money, or reference IDs.** These always render in IBM Plex Mono with `tabular-nums`.
- **Mixing ISO and natural-language dates in the same element.** Headlines are natural-language; data rows are ISO. One register per element.
- **Gradient backgrounds, glow effects, color washes.** The canvas is warm near-white (`#F8F8F6`), flat.
- **Heavy icon usage.** Icons as decoration, icons substituting for text labels, duotone icons, colorized icons. Material Symbols Outlined, monotone in `--color-text-muted`, used only when text alone is ambiguous.
- **Rounded-corner cards (above 4px).** 2px is the hard ceiling.
- **Caps-lock shouting in body copy.** Uppercase is reserved for labels, reference lines, and section eyebrows — never for body prose.

## Mobile spec (390x844)

- Design mobile first; desktop is an expansion
- Above the fold must answer "what do I do?" without scrolling
- Primary action in right-thumb zone (bottom 40% of screen)
- Tap targets ≥44px
- No hover-dependent affordances
- No horizontal scroll

## Desktop spec (1280px)

- Primary action in right-rail card at eye level, not bottom-anchored
- Timeline or main content fills the primary column
- Max content width 1040px, centered with generous side margins

## Contact affordance spec

- Primary action: tap-to-SMS to consultant's line
- Secondary: tap-to-call (on mobile)
- Label: "Replies within 1 business day." This is an operational commitment, not marketing copy.
- After-hours: same copy, no change
- Not a contact form, not a chat widget

## Copy samples (tone calibration)

Prose samples (Switzer body):

- Invoice pending: "Your April invoice is due Friday."
- Mid-engagement status: "We're in week 2. Scott sat with your dispatcher Tuesday."
- Milestone (concrete, past tense): "New call script so Maria stops forgetting the warranty question."
- Human presence: "Scott Durgan, your consultant. Next check-in: Wednesday at 10am."
- Empty state: "Nothing needs your attention. Next touchpoint is Wednesday."
- Paid invoice: "Paid April 12. Receipt attached."

Chrome samples (IBM Plex Mono caps):

- Reference line: `REF INV-1023 / ISSUED 2026-04-15`
- Status tag: `STATUS · DUE` (with leading dot in attention color)
- Section label: `ACTIVITY LOG` · `ENGAGEMENT` · `LEDGER` · `LINE ITEMS`
- Timeline meta: `2026-04-14 · SCOTT`
- Masthead: `SMD SERVICES` (left) · `2026.04.19` (right)
- Consultant role: `CONSULTANT` (under name)

## Error states (must design)

Every error surface includes the named human and a next step. No generic error pages.

- **Invoice:** payment declined, card expired, link expired, already paid
- **Proposal:** link expired, already signed, superseded
- **Dashboard:** data fetch failure
- **Consultant photo fails to load:** fall back to neutral portrait placeholder, never initials

## Activity timeline schema

`Event = { date, actor, verb, object, optional artifact link }`. Past tense. No system-generated "status updated" entries.

**Rendering format:** meta row is mono caps `{ISO-date} · {ACTOR}`, followed by narrative body in Switzer. Artifact link (when present) renders in mono caps, underlined.

Example:

```
2026-04-14 · SCOTT
Sat with your dispatcher. Two issues surfaced: warranty questions are
not being asked on intake, and after-hours calls route to voicemail
for twelve minutes before forwarding.
NOTES FILED 2026-04-14
```

ISO date format (`YYYY-MM-DD`) in the meta row is required for the mono alignment. Natural-language date ("Apr 14") may appear in prose bodies where it reads better.

## Money rule

All monetary values render as dollar figures (e.g., "$4,250.00"). Never as percentages, bars, or progress indicators. Typeset in IBM Plex Mono 500 with `font-variant-numeric: tabular-nums`. Two decimal places required on invoice detail surfaces for alignment; headline treatments may round (e.g., `$4,250`).

## Photo placeholder rule

Where consultant photo is called for and the real photo is not yet available, use a neutral portrait placeholder — cream-brown rectangle with mono-caps caption "Consultant photo" centered. Never initials-in-a-circle avatars. Rectangle radius 2px matches card radius.

## Accessibility floor

- WCAG 2.2 AA: 4.5:1 text contrast minimum (token pairings verified — see `.design/DESIGN.md` contrast table)
- Visible focus rings on all interactive elements (2px ring, 2px offset, color `--color-action` / `#1E4F5C` petrol blue)
- Semantic landmarks (header, main, nav)
- Screen-reader labels on icon-only buttons
- Tap targets ≥44px (see mobile spec)
- `prefers-reduced-motion: reduce` respected — not that we have much motion to disable (per identity)

## Success criteria

**Primary acceptance test:** On 390x844, the [Pay invoice] button's top edge sits at y ≤ 700px with no scroll required, first visit, no prior state.

**Secondary:**

- A status-checker can name the next milestone and when they'll next hear from us within 10 seconds of landing, testable with 3 users
- Each concept independently passes the 10-second next-milestone test
- Three concepts are structurally distinct, not visual variations
- Every reference line, timeline entry, and money value renders in IBM Plex Mono (identity integrity check)
- No pill-shaped status badges anywhere (identity integrity check)

## Follow-ups (scheduled, not gaps)

These are real priorities scheduled after this identity-reset sweep:

- **Secondary contact access** (Elena use case) — target: next sprint
- **SMS inbound/outbound channel** — target: next sprint
- **Consultant real photo hosting** (Scott) — target: this week. Portal uses neutral portrait placeholder until available.
- **Email / PDF identity cut-over** — email templates, SOW PDF, scorecard PDF, and the `book/manage` apex page still reference Inter + Plus Jakarta Sans. Not in portal scope; separate decision whether the firm identity should propagate to those surfaces.

## Data available

- Client name, primary contact
- Engagement: state, scope summary, start date, estimated completion
- Milestones: name, status, sort order, optional due date
- Quotes: status, sent/accepted/expired dates, value, PDF link
- Invoices: status, amount, due date, paid date, payment URL
- Documents: PDFs, deliverables
- Activity timeline (per schema above)
- Assigned consultant: name, photo URL, role
- Next scheduled touchpoint: ISO datetime with label
- Engagement ledger: paid ($), remaining ($), next charge ($) — dollar figures, always

If a proposed design needs data not listed, call it out as an open question.

## Constraints

- Astro + Tailwind v4 + Cloudflare Workers (Static Assets) implementation
- Single client user per account (v1; Elena access follow-up scheduled)
- No real-time updates; page-load freshness is acceptable
- No authentication UI in scope

## Approver

Scott Durgan. Generated components reviewed visually at `/design-preview/portal-*` before any iteration.

---

## Appendix: Hard design tokens (Swiss Functional — authoritative)

Full token spec and rationale: `.design/DESIGN.md`. Paste-ready `@theme` block: `.design/theme.css` (already merged into `src/styles/global.css` in PR #455).

### Color

```
Background:       #F8F8F6   (warm near-white)
Surface:          #FFFFFF
Border:           #D1D1CE   (hairline, 1px)
Border subtle:    #E5E4E1   (interior rules)
Text primary:     #0A0A0A   (warm graphite)
Text secondary:   #4A4A47
Text muted:       #8E8E8A
Primary:          #1E4F5C   (deep petrol blue, petrol blue)
Primary hover:    #163E48
Action:           #1E4F5C   (focus ring, 2px ring + 2px offset)
Attention:        #1E4F5C   (same as primary by design)
Complete:         #2C6E3F
Error:            #8B1A1A
Meta:             #4A4A47   (same as text-secondary, semantic name)
```

### Typography

```
Display:      Switzer 700,  40/44, tracking -0.02em
Title:        Switzer 600,  24/32, tracking -0.01em
Heading:      Switzer 600,  18/24
Body-lg:      Switzer 400,           18/28
Body:         Switzer 400,           16/24
Body emph:    Switzer 500,           16/24
Caption:      Switzer 500,           13/18, tracking 0.01em
Label:        IBM Plex Mono 600,    12/16, tracking 0.08em, uppercase
Money:        IBM Plex Mono 500,    32/40, tabular-nums
Data meta:    IBM Plex Mono 500,    13/18, tabular-nums (in-flow mono)
```

### Spacing and shape

```
Section:          40px (vertical gap between major page sections)
Card:             28px (card internal padding)
Stack:            16px (default vertical rhythm)
Row:              12px (list-row gap)
Tap target:       44px minimum
Radius:           2px (cards, buttons, badges — no pills)
Breakpoints:      390px (mobile) / 1280px (desktop)
Max content:      1040px (centered, generous side margins)
```

### Font loading

Served via two CDNs. The Astro layouts (`Base.astro`, `AdminLayout.astro`) and the four portal design-preview pages include:

```html
<link
  href="https://api.fontshare.com/v2/css?f[]=switzer@400,500,600,700,800&display=swap"
  rel="stylesheet"
/>
<link
  href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&display=swap"
  rel="stylesheet"
/>
```

Switzer + Switzer: Fontshare (free for personal + commercial). IBM Plex Mono: Google Fonts (SIL OFL 1.1). No licensing to purchase.
