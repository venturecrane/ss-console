# Client Portal — UX Redesign Brief (final)

## Context

SMD Services sells scope-based consulting engagements to businesses doing $750k-$5M in revenue. The portal is the documented side of a relationship between our visits — not software the client bought.

The current portal is undifferentiated. Every element has equal weight; nothing adapts to why a client arrived or what state their engagement is in.

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

- **`portal-home`** — action responder / status checker. "What needs my attention, and what's happening with my engagement?" Dashboard archetype. Dominant action card (pending invoice if any) or "next check-in" card; timeline of past work below. Detailed in the worked example above.

- **`portal-quotes-list`** — document retriever / status checker. "Show me every proposal sent to me." List of proposal rows: title, status pill (Pending Review / Accepted / Declined / Expired), sent date, total amount (per the money rule — no per-item breakdown), expiry caption if applicable. Each row links to the detail view. Chrome: sticky header + persistent tabs.

- **`portal-quotes-detail`** — action responder. "Show me this proposal; let me sign it or download the PDF." Five states (isSigned / isDeclined / isExpired / isSuperseded / isSent) — see state-rendering rules. Above fold on mobile: engagement title + status pill + dominant action (Review and sign / Signed confirmation / Superseded banner). Consultant block required.

- **`portal-invoices-list`** — document retriever. "Show me every invoice sent to me." Same structural pattern as quotes list: title ("Invoice #abc123"), status pill (Due / Paid / Overdue), issue date, amount in dollars, due date caption. Each row links to detail.

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
- **Printed dossier, not SaaS app.**
- If the client forwarded a screenshot to their spouse, one glance confirms real work is happening.

## Three concepts requested (structurally distinct, not visual variations)

**A — Timeline-centric.** The engagement narrative is the page. Actions appear inline at the relevant moment. On invoice-pending: the invoice pins to the top as today's entry.

**B — Conversation-centric.** Dated entries from the consultant, addressed to the client. Printed email archive, not Slack. **Read-only: no composer, no input field, no send button.**

- Desktop: most recent entry anchored bottom, older entries scroll above.
- Mobile: on pending-action states, the pending entry inverts and pins above the fold; older entries scroll below.

**C — Action-centric.** One primary object dominates the viewport based on state. Invoice-pending: the invoice _is_ the page, not the top of the page. Timeline collapses to one line or is absent. No-pending-action: next touchpoint is the dominant element.

## Worked example (fidelity reference)

Concept A, invoice-pending state, 390px above the fold:

- "Delgado Plumbing" (caption size, Inter 500, 13/18)
- Headline: "Your April invoice is due Friday." (PJS 700, 22/28)
- Invoice card:
  - Amount in dollars (Inter 500, tabular-nums, 24/28)
  - Due date (caption size)
  - [Pay invoice] primary button (44px min height, thumb zone)
- Consultant block: Scott Durgan with photo, "Next check-in Wednesday 10am" (Inter 400, 16/24)
- Below fold: previous timeline entries as muted dated lines (Inter 400, 14/20, slate-600)

## Above-fold specs for B and C (matching A's format)

**B, invoice-pending, 390px above fold:** Pending-action entry inverts to top — dated header ("Apr 14 — From Scott"), invoice card with amount + due date + [Pay invoice], consultant photo and next touchpoint underneath. Scroll reveals older dated entries below.

**C, invoice-pending, 390px above fold:** Full-bleed invoice treatment dominates viewport — amount in large display type (PJS 800, 32/36), due date caption, [Pay invoice] button in thumb zone, consultant block below the action, one-line recent-activity link at the bottom.

## What must be preserved

Typography, palette (hex in Appendix), 8px rounded surfaces + full-rounded pills, generous vertical rhythm on chrome, voice (guide persona, human and direct, no hype, no em dashes, no AI-flavored copy), minimal iconography (Material Symbols Outlined, the established set).

## What is open

Layout, hierarchy, component choice, grouping, navigation patterns, animation, empty states, entire flow.

## Anti-patterns (do not produce)

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

## Contact affordance spec

- Primary action: tap-to-SMS to consultant's line
- Secondary: tap-to-call (on mobile)
- Label: "Replies within 1 business day." This is an operational commitment, not marketing copy.
- After-hours: same copy, no change
- Not a contact form, not a chat widget

## Copy samples (tone calibration)

- Invoice pending: "Your April invoice is due Friday."
- Mid-engagement status: "We're in week 2. Scott sat with your dispatcher Tuesday."
- Milestone (concrete, past tense): "New call script so Maria stops forgetting the warranty question."
- Human presence: "Scott Durgan, your consultant. Next check-in: Wednesday at 10am."
- Empty state: "Nothing needs your attention. Next touchpoint is Wednesday."
- Paid invoice: "Paid April 12. Receipt attached."

## Error states (must design)

Every error surface includes the named human and a next step. No generic error pages.

- **Invoice:** payment declined, card expired, link expired, already paid
- **Proposal:** link expired, already signed, superseded
- **Dashboard:** data fetch failure
- **Consultant photo fails to load:** fall back to neutral portrait placeholder, never initials

## Activity timeline schema

`Event = { date, actor, verb, object, optional artifact link }`. Past tense. No system-generated "status updated" entries.

Date format: "Apr 9" short form. Year appears only when crossing a year boundary.

Example: _"Apr 9 — Scott sat with dispatcher. Two issues identified."_

## Money rule

All monetary values render as dollar figures (e.g., "$4,250"). Never as percentages, bars, or progress indicators.

## Photo placeholder rule

Where consultant photo is called for and the real photo is not yet available, use a neutral portrait placeholder with caption "consultant photo". Never initials-in-a-circle avatars.

## Accessibility floor

- WCAG 2.2 AA: 4.5:1 text contrast minimum
- Visible focus rings on all interactive elements (2px ring, 2px offset, color #3B82F6)
- Semantic landmarks (header, main, nav)
- Screen-reader labels on icon-only buttons
- Tap targets ≥44px (see mobile spec)

## Success criteria

**Primary acceptance test:** On 390x844, the [Pay invoice] button's top edge sits at y ≤ 700px with no scroll required, first visit, no prior state.

**Secondary:**

- A status-checker can name the next milestone and when they'll next hear from us within 10 seconds of landing, testable with 3 users
- Each concept independently passes the 10-second next-milestone test
- Three concepts are structurally distinct, not visual variations

## Follow-ups (scheduled, not gaps)

These are real priorities scheduled after this Stitch pass:

- **Secondary contact access** (Elena use case) — target: next sprint
- **SMS inbound/outbound channel** — target: next sprint
- **Consultant real photo hosting** (Scott) — target: this week. Stitch uses neutral portrait placeholder until available.

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

- Astro + Tailwind + Cloudflare Pages implementation
- Single client user per account (v1; Elena access follow-up scheduled)
- No real-time updates; page-load freshness is acceptable
- No authentication UI in scope

## Approver

Scott Durgan. Stitch output reviewed before any iteration.

---

## Appendix: Hard design tokens

### Color

```
Primary:         #1E40AF   (deep indigo blue)
Primary hover:   #1E3A8A
Background:      #F8FAFC
Surface:         #FFFFFF
Border/divider:  #E2E8F0   (1px)
Text primary:    #0F172A
Text secondary:  #475569
Text muted:      #94A3B8   (distinct use from divider)
Focus ring:      #3B82F6   (2px ring, 2px offset)
Action:          #3B82F6
Complete:        #10B981
Attention:       #F59E0B
Error:           #EF4444
```

### Typography

```
Display:     Plus Jakarta Sans 800,  32/36, tracking -0.02em
H2:          Plus Jakarta Sans 700,  22/28
H3:          Plus Jakarta Sans 700,  18/24
Body:        Inter 400,              16/24
Body emph:   Inter 500,              16/24
Caption:     Inter 500,              13/18, tracking 0.01em
Money/data:  Inter 500,              16/24, tabular-nums
```

### Spacing and shape

```
Rounded:          8px (surfaces), full (pills)
Surface padding:  20-24px mobile, 24-32px desktop
Stack rhythm:     16px default, 24px between sections
Tap target:       44px minimum
Breakpoints:      390px (mobile) / 1280px (desktop)
```
