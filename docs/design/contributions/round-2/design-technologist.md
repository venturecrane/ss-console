# Design Technologist Contribution - Design Brief Round 2

**Author:** Design Technologist (Sonnet 4.6)
**Date:** 2026-04-26
**Design Maturity:** Full system (tokens + 33+ components, W3C-DTCG source, 7 enforced UI patterns)
**Status:** Revised after cross-role review

---

## Changes from Round 1

1. **Warning token added to Token Architecture** — Brand Strategist's Gap 3 (attention === primary conflict) landed a specific hex and contrast ratio. Resolved by adding `--ss-color-warning: #6b4f08` with JSON entry, compiled CSS, and Tailwind mapping. Triggered by: Brand Strategist §3.3 Gap 3.

2. **Focus-ring formalized as CSS rule + tokens** — Brand Strategist's Gap 4 converted from a documentation note to an actionable `global.css` addition: `:focus-visible` rule, two new focus-ring tokens (`--ss-focus-ring-width`, `--ss-focus-ring-offset`), and the `@layer base` placement decision. Triggered by: Brand Strategist §3.3 Gap 4.

3. **`PortalTabs.astro` update spec added** — Interaction Designer specified Material Symbols icons for the mobile bottom bar. Round 1 documented that `PortalTabs.astro` uses typographic `§ 0N` anchors with no icons. Round 2 resolves the divergence: icons are added to the mobile bar alongside the existing anchors, with a prop-controlled opt-in. Triggered by: Interaction Designer §Navigation Model (portal tabs section).

4. **`text-muted` outdoor-readability rule tightened** — Marcus's observation about direct-sunlight readability moved this from a usage note to a formal rule with a clear enforcement line: `text-muted` is non-essential metadata only. `--ss-color-text-secondary` (#4a423c, 8.64:1) is the minimum for any data Marcus needs to act on. Triggered by: Target User §First Impressions (cream-on-cream concern).

5. **Touch target enforcement added to Accessibility section** — Interaction Designer §Responsive Strategy cited WCAG 2.5.5 (44×44pt) as a requirement. Round 2 adds a utility class spec, a Tailwind plugin approach, and an audit of existing button components for compliance. Triggered by: Interaction Designer §Responsive Strategy.

6. **`SigningView.astro` added to Component Inventory** — Interaction Designer confirmed this is New (no file at `portal/quotes/[id]/sign`). Round 2 formalized the props interface (simplified from Round 1's `signwellEmbedUrl` naming) and added ARIA spec for iframe busy state. Marcus's visual-discontinuity concern added the "frame, don't restyle" constraint. Triggered by: Interaction Designer §Key Screen Breakdowns §2; Target User §The SignWell embed.

7. **Motion token mapping resolved with actual code** — Brand Strategist flagged motion tokens not mapped. Round 1 provided the `@theme inline` block as a recommendation. Round 2 confirms the addition is required (verified against current `global.css`), provides the exact code, and calls it a code change required before Phase 2 ships. Triggered by: Brand Strategist §5.4 Motion gap.

8. **`prefers-reduced-motion` rule formalized** — Round 1 had a partial catch-all; Brand Strategist called for the full WCAG-compliant block including `scroll-behavior`. Round 2 provides the exact code with `scroll-behavior: auto !important` included and confirms placement. Triggered by: Brand Strategist §5.4 Motion gap.

9. **UI-PATTERNS.md Rule 6 drift documented as correction required** — Round 1 noted the discrepancy. Round 2 documents it as a correction needed, clarifies which document wins (live tokens), and proposes the specific text change to Rule 6. Triggered by: Brand Strategist §5.1 Note.

10. **`QuoteLineItemEditor` JS island scope confirmed; performance budget split** — Round 1 flagged this as a question. Round 2 closes it: the island is admin-route-only, and the performance budget is formally split into two lines: portal routes 0KB client JS, admin routes ≈50KB. Triggered by: Round 1 Open Design Decision #6.

11. **Email template inventory and token-snapshot strategy added** — Interaction Designer's Email Touchpoint Inventory implied that email templates exist as styled HTML. Audit found them at `src/lib/email/templates.ts`. Current templates use a non-compliant inline palette (blue, slate grays). Round 2 proposes a token snapshot file for compile-time inlining. Triggered by: Interaction Designer §Email as a channel.

12. **`InvoiceDetail.astro` print stylesheet added to Component Inventory** — Marcus's invoice-as-record expectation (print-and-file behavior) required specifying `@media print` rules. PDF endpoint latency bound of <800ms added to Performance Budget. Triggered by: Target User §The first invoice lands; Interaction Designer §Invoice Detail.

---

## Table of Contents

1. [Component Inventory](#1-component-inventory)
2. [Token Architecture](#2-token-architecture)
3. [CSS Strategy](#3-css-strategy)
4. [Dark Mode](#4-dark-mode)
5. [Responsive Implementation](#5-responsive-implementation)
6. [Accessibility](#6-accessibility)
7. [Performance Budget](#7-performance-budget)
8. [Animation and Motion](#8-animation-and-motion)

---

## 1. Component Inventory

Status classification: **Exists** = usable as-is | **Exists (needs update)** = file present, change required | **New** = does not exist.

### Portal Components

| Name                         | Purpose                                                                                    | Variants                                                                   | Status                    | File path                                         | Notes                                                                                                  |
| ---------------------------- | ------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------- | ------------------------- | ------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `PortalHeader.astro`         | Sticky top band: client name left, contact icons right; sign-out slot                      | default                                                                    | Exists                    | `src/components/portal/PortalHeader.astro`        | No change required                                                                                     |
| `PortalTabs.astro`           | Persistent navigation — desktop top tabs, mobile fixed bottom bar                          | desktop / mobile                                                           | **Exists (needs update)** | `src/components/portal/PortalTabs.astro`          | See §1.1: icon-prop interface, icon-prop opt-in for mobile bar                                         |
| `PortalPageHead.astro`       | Page-level header: crumb link, optional stamp tag, H1, optional mono meta                  | with-crumb / no-crumb                                                      | Exists                    | `src/components/portal/PortalPageHead.astro`      | No change required                                                                                     |
| `PortalListItem.astro`       | Only list-row markup on portal surfaces                                                    | `variant: 'status' \| 'document'` × `chrome: 'card' \| 'ticket'`           | Exists                    | `src/components/portal/PortalListItem.astro`      | No change required                                                                                     |
| `StatusPill.astro`           | Tone-based status tag (rectangle, mono-caps)                                               | `size: 'compact' \| 'base'`                                                | Exists                    | `src/components/portal/StatusPill.astro`          | No change required                                                                                     |
| `MoneyDisplay.astro`         | Dollar-figure renderer; cents-in; tabular-nums                                             | `size: 'hero' \| 'total' \| 'kpi' \| 'row' \| 'display' \| 'h2' \| 'body'` | Exists                    | `src/components/portal/MoneyDisplay.astro`        | No change required                                                                                     |
| `ActionCard.astro`           | Dominant CTA card: ink-header label, hero money, primary CTA, optional subtext             | single layout                                                              | Exists                    | `src/components/portal/ActionCard.astro`          | No change required                                                                                     |
| `ConsultantBlock.astro`      | Named-human block: photo/silhouette, name, role, next touchpoint                           | `variant: 'default' \| 'trade-card'`                                       | Exists                    | `src/components/portal/ConsultantBlock.astro`     | No change required                                                                                     |
| `EngagementProgress.astro`   | Full engagement progress surface: summary, activity log, consultant block                  | single layout                                                              | Exists                    | `src/components/portal/EngagementProgress.astro`  | No change required                                                                                     |
| `PortalHomeDashboard.astro`  | Dashboard body: state-branched by lifecycle                                                | pre-sign / active / error states                                           | Exists                    | `src/components/portal/PortalHomeDashboard.astro` | No change required                                                                                     |
| `QuoteDetail.astro`          | Proposal detail: state-branched by quote status                                            | isSigned / isDeclined / isExpired / isSuperseded / isSent                  | Exists                    | `src/components/portal/QuoteDetail.astro`         | Remediation required: Pattern 01 violation line 207-210, Pattern 02 violation lines 458-497            |
| `InvoiceDetail.astro`        | Invoice detail: receipt vs. pending-payment vs. error                                      | isPaid / overdue / payment error                                           | **Exists (needs update)** | `src/components/portal/InvoiceDetail.astro`       | See §1.2: print stylesheet addition required. Pattern 02 triple-redundancy violation at lines 450-461. |
| `InvoicesList.astro`         | Invoice list surface                                                                       | list view                                                                  | Exists                    | `src/components/portal/InvoicesList.astro`        | No change required                                                                                     |
| `QuoteList.astro`            | Quote list surface                                                                         | list view                                                                  | Exists                    | `src/components/portal/QuoteList.astro`           | No change required                                                                                     |
| `Documents.astro`            | Document library surface                                                                   | list view                                                                  | Exists                    | `src/components/portal/Documents.astro`           | No change required                                                                                     |
| `TimelineEntry.astro`        | Dated narrative entry in engagement activity log                                           | single                                                                     | Exists                    | `src/components/portal/TimelineEntry.astro`       | No change required                                                                                     |
| `ArtifactChip.astro`         | Mono-caps inline artifact link                                                             | with-icon / no-icon                                                        | Exists                    | `src/components/portal/ArtifactChip.astro`        | No change required                                                                                     |
| `SkipToMain.astro`           | Skip-to-main accessibility link                                                            | single                                                                     | Exists                    | `src/components/SkipToMain.astro`                 | No change required                                                                                     |
| `SigningView.astro`          | SignWell iframe container: scope reminder sidebar, full-width iframe, post-signature panel | desktop / mobile                                                           | **New**                   | —                                                 | See §1.3 for full spec                                                                                 |
| `MagicLinkExpiredForm.astro` | Single-field recovery form for expired/used tokens                                         | single                                                                     | **New**                   | —                                                 | `<form>` with labeled email input; error state via `aria-describedby`                                  |
| `ParkingLotPanel.astro`      | Parking lot disposition list (Phase 5); fold-in / follow-on / dropped states               | post-handoff view                                                          | **New**                   | —                                                 | Read-only in portal; disposition as text, not pill                                                     |

### Admin Components

| Name                          | Purpose                                                             | Variants                       | Status  | File path                                          | Notes                                                                  |
| ----------------------------- | ------------------------------------------------------------------- | ------------------------------ | ------- | -------------------------------------------------- | ---------------------------------------------------------------------- |
| `EnrichmentStatusPanel.astro` | Enrichment pipeline status                                          | running / timed-out / complete | Exists  | `src/components/admin/EnrichmentStatusPanel.astro` | No change required                                                     |
| `LogReplyDialog.astro`        | Modal for logging follow-up reply                                   | single                         | Exists  | `src/components/admin/LogReplyDialog.astro`        | No change required                                                     |
| `PipelineKanban.astro`        | Admin pipeline view: status columns, client cards                   | 5 status columns               | **New** | —                                                  | Each card is `<article>`; overdue flag is visible text, not color-only |
| `ClientCard.astro`            | Pipeline card: business name, vertical, days in status, next action | default / overdue              | **New** | —                                                  | Overdue state announced via text label                                 |
| `QuoteLineItemEditor.astro`   | Repeating line-item rows in quote builder                           | single (React island)          | **New** | —                                                  | Admin-route-only. See §7 Performance Budget.                           |
| `SOWPreviewPane.astro`        | Read-only SOW preview before PDF generation                         | single                         | **New** | —                                                  | `role="region" aria-label="SOW preview"`                               |
| `FollowUpCard.astro`          | Follow-up cadence card: client name, type, date, one-click actions  | overdue / due-today / upcoming | **New** | —                                                  | Action buttons meet 44px minimum tap target                            |
| `TimeEntryLog.astro`          | Time entry list for engagement dashboard                            | list view                      | **New** | —                                                  | `<table>` with `<thead>`; column headers required                      |
| `ExtractionPanel.astro`       | Renders structured Claude extraction output from assessment         | single                         | **New** | —                                                  | Problem identifiers rendered as readable labels, not raw enum keys     |

### Email Templates

| Name                       | Purpose                              | Location                               | Status                    | Notes                                                                                               |
| -------------------------- | ------------------------------------ | -------------------------------------- | ------------------------- | --------------------------------------------------------------------------------------------------- |
| Magic link email           | Token delivery to client             | `src/lib/email/templates.ts`           | **Exists (needs update)** | Current palette: blue/slate. Does not use `--ss-*` tokens. See §1.4: email token snapshot required. |
| Portal invitation email    | First portal invite with quote ready | `src/lib/email/templates.ts`           | **Exists (needs update)** | Same palette violation as magic link template.                                                      |
| Booking confirmation email | Assessment booking receipt           | `src/lib/email/booking-emails.ts`      | **Exists (needs update)** | Same palette violation.                                                                             |
| Follow-up email series     | Proposal follow-up cadence           | `src/lib/email/follow-up-templates.ts` | **Exists (needs update)** | Same palette violation.                                                                             |

### Shared / Booking Components

| Name               | Purpose                           | Variants                                  | Status                    | File path                                 | Notes                                                                          |
| ------------------ | --------------------------------- | ----------------------------------------- | ------------------------- | ----------------------------------------- | ------------------------------------------------------------------------------ |
| `SlotPicker.astro` | Assessment booking slot selection | slot-selection / confirm                  | Exists                    | `src/components/booking/SlotPicker.astro` | No change required                                                             |
| `CtaButton.astro`  | Shared primary button atom        | primary / secondary / ghost / destructive | **Exists (needs update)** | `src/components/CtaButton.astro`          | Needs `disabled` prop + ARIA handling; destructive variant not yet implemented |

---

### 1.1 `PortalTabs.astro` Update Spec

**Current state:** `PortalTabs.astro` Exists at `src/components/portal/PortalTabs.astro`. The mobile bottom bar uses a typographic `§ 0N` JetBrains Mono anchor above the Archivo Narrow label. No Material Symbols icons are present. The desktop tab row uses Archivo Narrow uppercase labels with `§ 0N` anchors. This is the Plainspoken Sign Shop register — utilitarian, not icon-decorated.

**Interaction Designer specified:** Material Symbols Outlined icons for the mobile bottom bar: `home` (Dashboard), `work` (Proposals), `description` (Invoices), `receipt_long` (Progress), `folder_open` (Documents).

**Resolution — additive, not replacing:** Icons are added to the mobile bar as an opt-in enhancement that sits alongside the existing `§ 0N` + label pattern. The `§ 0N` anchors are removed when icons are present to avoid three competing signals on a constrained mobile tab. Desktop tabs remain label-only (the Plainspoken register is text-led on desktop; icons in the desktop tab strip would introduce decoration the identity rejects).

**Updated prop interface:**

```ts
// PortalTabs.astro
interface TabDef {
  href: string
  label: string
  anchor: string // § section anchor (shown on desktop; mobile: shown when iconName is absent)
  matchPrefix: string
  iconName?: string // Material Symbols Outlined name. When present, replaces § anchor on mobile bar.
}

interface Props {
  pathname: string
  /** Show Material Symbols icons in mobile bar instead of § anchors (default: true when any tab has iconName) */
  showIcons?: boolean
}
```

**Updated `tabs` array (code change required in `PortalTabs.astro`):**

```ts
const tabs: TabDef[] = [
  { href: '/portal', label: 'Home', anchor: '00', matchPrefix: '/portal', iconName: 'home' },
  {
    href: '/portal/quotes',
    label: 'Proposals',
    anchor: '01',
    matchPrefix: '/portal/quotes',
    iconName: 'work',
  },
  {
    href: '/portal/invoices',
    label: 'Invoices',
    anchor: '02',
    matchPrefix: '/portal/invoices',
    iconName: 'description',
  },
  {
    href: '/portal/engagement',
    label: 'Progress',
    anchor: '03',
    matchPrefix: '/portal/engagement',
    iconName: 'receipt_long',
  },
  {
    href: '/portal/documents',
    label: 'Documents',
    anchor: '04',
    matchPrefix: '/portal/documents',
    iconName: 'folder_open',
  },
]
```

Note: the Interaction Designer resolved 4 tabs (no Home tab) per Apple HIG maximum for a bottom bar. The PRD §9 tab visibility rule (tabs conditional on data presence) is a server-side conditional, not a tabs-count issue. The current implementation has 4 tabs without a Home destination. The `isActive` function returns `false` for `/portal` — the dashboard is the implicit root. If a fifth tab for Documents is added, the Apple HIG 5-item limit is still respected. Either 4 or 5 is valid; defer to Interaction Designer for the final count decision. This spec assumes 4 tabs to match current `PortalTabs.astro` plus a Home destination reconciliation.

**Active state token:** Active tab uses `--ss-color-primary` background (burnt orange) with `--ss-color-background` (cream) text. This is correct and matches the current implementation. No change to active-state token.

**Icon rendering (mobile bar only):**

```astro
{
  iconName ? (
    <span
      class="material-symbols-outlined text-[20px] leading-none"
      aria-hidden="true"
      style="font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 20;"
    >
      {iconName}
    </span>
  ) : (
    <span aria-hidden="true" class="font-mono text-[11px] leading-none tracking-[0.06em] ...">
      § {tab.anchor}
    </span>
  )
}
```

`opsz: 20` is required at the mobile tab bar's display size (20px effective). The default `opsz: 24` at 20px produces a visually heavier stroke that reads as slightly off-size. `FILL' 0` maintains the outlined (not filled) variant consistent with the system-wide icon rule.

**ARIA:** Icons are `aria-hidden="true"` — the visible label text carries the accessible name. No `aria-label` needed on the icon span.

---

### 1.2 `InvoiceDetail.astro` Print Stylesheet

**Marcus's expectation:** Invoice is a business record to be printed and filed. "I want a confirmation that looks like something I'd print and put in a folder."

**Constraint:** `InvoiceDetail.astro` renders the complete invoice detail view. There is no separate PDF generation for invoices at MVP — the PDF download endpoint exists for SOW documents (`src/pages/api/admin/quotes/[id].ts`). For invoices, browser print is the mechanism at MVP.

**Required addition — `@media print` rules** (to be added to `InvoiceDetail.astro`'s `<style>` block, or to a global `print.css` imported in portal layouts):

```css
@media print {
  /* Hide portal chrome */
  [data-portal-header],
  [data-portal-tabs],
  [data-skip-link],
  .consultant-block,
  .portal-nav {
    display: none !important;
  }

  /* Reset background to white — cream does not print true */
  body,
  main,
  .invoice-surface {
    background-color: #ffffff !important;
    color: #000000 !important;
  }

  /* Preserve ink and amount typography */
  .invoice-amount,
  .invoice-reference {
    color: #000000 !important;
  }

  /* No page break inside the invoice card */
  .invoice-card {
    page-break-inside: avoid;
    break-inside: avoid;
  }

  /* Remove interactive affordances */
  a[href]::after {
    content: none; /* Suppress URL printing */
  }

  .pay-now-button,
  .download-button {
    display: none !important;
  }

  /* Print-only: company name and address block if absent from screen layout */
  .print-only {
    display: block !important;
  }
}
```

**ARIA/HTML hooks required:** Add `data-portal-header` to `PortalHeader.astro`'s root element, `data-portal-tabs` to `PortalTabs.astro`'s root `<nav>`, and `data-skip-link` to `SkipToMain.astro`. These data attributes are the print-stylesheet selectors — safer than class selectors that may change during Tailwind utility refactors.

**PDF download endpoint latency:** The existing SOW PDF endpoint must respond in <800ms p95. This is added to Performance Budget §7. Invoice print is browser-native and has no server latency.

---

### 1.3 `SigningView.astro` Full Spec (New)

**Marcus's constraint:** "If the SignWell window is white with blue buttons and a different typeface, my brain registers that as 'you're not in Kansas anymore.'" We cannot restyle the iframe. We can frame it in cream chrome that creates visual continuity.

**Constraint:** SignWell iframe is third-party. We own the container, not the iframe content. The container must create visual continuity; the iframe content cannot be restyled.

**Props interface:**

```ts
// SigningView.astro
interface Props {
  quoteId: string
  /** SignWell embed URL — must be server-validated before passing to client */
  signWellEmbedUrl: string
  /** Redirect href after signing completes (portal-side; not SignWell's redirect) */
  postSignRedirectHref: string
  /** True when signing is already complete — renders confirmation panel instead of iframe */
  isSigned: boolean
  /** Signed SOW PDF URL (R2 presigned). Shown in post-sign confirmation. Null until webhook fires. */
  signedPdfHref?: string | null
  /** Scope summary data for the collapsible sidebar */
  quote: {
    engagementTitle: string
    totalCents: number
    depositCents: number
    paymentSplitText: string // Authored field. e.g. "50% now, 50% at completion"
    deliverables: Array<{ title: string; body: string }>
  }
}
```

**ARIA spec:**

- Outer container: `aria-busy="true"` while iframe is loading; removed via `load` event listener on the iframe. This signals screen readers that the content area is loading.
- Iframe: `title="Sign proposal"` required. This is the accessible name for the iframe landmark.
- Post-sign confirmation panel: `aria-live="assertive"` — the transition from signing to confirmed is significant. Focus moves to the confirmation heading on transition.
- Scope reminder accordion (mobile): `<details>/<summary>` pattern. `summary` text: "Review scope summary" (collapsed) / "Hide scope summary" (expanded). No custom ARIA needed; `<details>` provides the disclosure role natively.
- Keyboard: Tab must be able to exit the iframe to the portal's controls. If SignWell's embed traps Tab, a visible "Return to portal" button outside the iframe must be present. Verify with SignWell embed API before Phase 2 ships.

**Responsive layout:**

Mobile (`< md`): Full-width iframe. Scope summary in a `<details>` accordion above the iframe (collapsed by default — Marcus: "I'll read the scope first, then sign"). Height: `calc(100dvh - 7rem)` for the iframe container. `dvh` fallback: `calc(100vh - 7rem)`.

Desktop (`≥ md`): Two-column. Left column: scope summary sidebar (sticky, max-width 280px). Right column: SignWell iframe (flex-1). Sidebar is not collapsible on desktop — it is always visible.

**Loading state:** Centered "Loading signing document..." text with a visually hidden `<span role="status" aria-live="polite">Loading signing document</span>` inside the iframe container while `aria-busy="true"`. Not a skeleton — iframe load time is not predictable in shape.

**Iframe failure fallback (UX-002):** If `iframe.onerror` or a 30-second timeout fires: "The signing document isn't available right now. Email us at {authored consultant email} to sign via a direct link." Replace the iframe container with this message. No broken empty frame.

---

### 1.4 Email Templates — Token Snapshot Strategy

**Current state (audit finding):** Email templates at `src/lib/email/templates.ts` use a non-compliant inline palette — blue (`#1e40af`), slate grays (`#64748b`, `#94a3b8`), and white backgrounds. This is the default Resend template aesthetic and is entirely disconnected from the Plainspoken Sign Shop identity.

**Constraint:** Email clients do not support CSS custom properties. `var(--ss-color-*)` is invalid in email HTML. Tokens must be inlined as literal hex values.

**Proposed solution — email token snapshot file:**

```ts
// src/lib/email/tokens.ts
// Compile-time snapshot of SS token values for email rendering.
// These values must be kept in sync with @venturecrane/tokens/ss.json.
// DO NOT import CSS variables here — email clients cannot resolve them.

export const EMAIL_TOKENS = {
  colorBackground: '#f5f0e3', // --ss-color-background (cream)
  colorSurface: '#f5f0e3', // --ss-color-surface
  colorTextPrimary: '#1a1512', // --ss-color-text-primary (ink)
  colorTextSecondary: '#4a423c', // --ss-color-text-secondary
  colorTextMuted: '#8a7f73', // --ss-color-text-muted
  colorPrimary: '#c5501e', // --ss-color-primary (burnt orange)
  colorComplete: '#4a6b3e', // --ss-color-complete (olive)
  colorError: '#a02a2a', // --ss-color-error (brick)
  colorWarning: '#6b4f08', // --ss-color-warning (amber-ochre)  ← NEW
  fontStackSans: 'Archivo, Arial, Helvetica, sans-serif',
  fontStackMono: '"JetBrains Mono", "Courier New", Courier, monospace',
  borderColor: '#d2cec6', // rgba(26,21,18,0.16) approximated for email contexts
  radiusNone: '0px', // Identity: flat institutional, no rounding
} as const
```

**Enforcement:** A lint rule or build-time check should flag any hardcoded hex in `src/lib/email/*.ts` that does not match a value in `EMAIL_TOKENS`. This prevents palette drift between email and portal.

**Current templates are P1 remediation items** (not P0 — they are functional and branded under the brand name; the palette mismatch is a visual quality issue, not a fabrication violation). Remediation is recommended before the first quote invite is sent to a real client.

---

## 2. Token Architecture

### 2.1 Naming Convention

All tokens use the `--ss-{category}-{variant}` prefix. Source of truth chain:

```
crane-console/packages/tokens/src/ventures/ss.json  (W3C-DTCG)
  → node_modules/@venturecrane/tokens/dist/ss.css    (compiled --ss-*)
  → src/styles/global.css @theme inline              (Tailwind utilities)
```

No naming convention changes are needed.

### 2.2 Color Tokens — Complete Inventory

All existing tokens remain unchanged. New additions are marked **NEW**.

```css
/* Surface */
--ss-color-background: #f5f0e3;
--ss-color-surface: #f5f0e3;
--ss-color-surface-inverse: #1a1512;

/* Border */
--ss-color-border: rgba(26, 21, 18, 0.16);
--ss-color-border-subtle: rgba(26, 21, 18, 0.08);

/* Text */
--ss-color-text-primary: #1a1512;
--ss-color-text-secondary: #4a423c;
--ss-color-text-muted: #8a7f73;
--ss-color-meta: #4a423c;

/* Brand & semantic */
--ss-color-primary: #c5501e;
--ss-color-primary-hover: #a84318;
--ss-color-action: #c5501e;
--ss-color-attention: #c5501e;
--ss-color-complete: #4a6b3e;
--ss-color-error: #a02a2a;

/* NEW — Round 2 addition */
--ss-color-warning: #6b4f08; /* Deep amber-ochre. Warning and soft-caution states.
                                       6.72:1 on cream (#f5f0e3) — WCAG AA pass for normal text.
                                       Sits between burnt orange (action) and olive (success)
                                       on the warm spectrum. Not confused with --ss-color-primary
                                       (#c5501e) — distinct enough for simultaneous display. */

/* NEW — Round 2 addition */
--ss-focus-ring-width: 2px; /* Focus ring stroke width. Referenced in :focus-visible rule. */
--ss-focus-ring-offset: 2px; /* Focus ring offset. Creates visible gap against cream background. */
```

### 2.3 Warning Token — ss.json Entry

Add to `/Users/scottdurgan/dev/crane-console/packages/tokens/src/ventures/ss.json` under the `color` category:

```json
{
  "ss": {
    "color": {
      "warning": {
        "$value": "#6b4f08",
        "$type": "color",
        "$description": "Deep amber-ochre. Warning and soft-caution states. 6.72:1 contrast on cream (#f5f0e3). AA-compliant for normal text. Distinct from --ss-color-primary (#c5501e) and --ss-color-attention (#c5501e) — visually separates cautionary states from actionable states."
      }
    }
  }
}
```

**Compiled output** (what the build step writes to `dist/ss.css`):

```css
:root {
  /* ... existing tokens ... */
  --ss-color-warning: #6b4f08;
}
```

**Tailwind v4 `@theme inline` mapping** (add to `src/styles/global.css`):

```css
@theme inline {
  /* Add to the existing color roles block */
  --color-warning: var(--ss-color-warning);
}
```

This makes `text-warning`, `bg-warning`, `border-warning` valid Tailwind utilities.

**Focus-ring tokens — Tailwind mapping** (add alongside `--color-warning`):

```css
@theme inline {
  --color-focus-ring: var(--ss-color-action); /* Alias — focus ring uses action color */
}
```

The width and offset tokens (`--ss-focus-ring-width`, `--ss-focus-ring-offset`) are consumed directly via `var()` in the `:focus-visible` rule — Tailwind does not have a native focus-ring-width utility in v4.

### 2.4 Usage Rules for New Tokens

| Token                    | Permitted uses                                                                                                                 | Prohibited uses                                                                                                        |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------- |
| `--ss-color-warning`     | Soft warning states (financial blindness EC-007, BR-012), invoice overdue indicator text, parking lot "scope expansion" signal | CTAs, primary actions (use `--ss-color-primary`), success (use `--ss-color-complete`), errors (use `--ss-color-error`) |
| `--ss-focus-ring-width`  | `:focus-visible` rule only                                                                                                     | Do not use as a border-width in layout contexts                                                                        |
| `--ss-focus-ring-offset` | `:focus-visible` rule only                                                                                                     | Do not use as padding or gap values                                                                                    |

**Warning token contrast audit:**

| Pairing                                           | Ratio      | WCAG AA Normal | WCAG AA Large/UI                |
| ------------------------------------------------- | ---------- | -------------- | ------------------------------- |
| `--ss-color-warning` (#6b4f08) on cream (#f5f0e3) | **6.72:1** | Pass           | Pass (AAA-adjacent)             |
| White on `--ss-color-warning` (#6b4f08)           | **3.06:1** | Fail           | Pass (UI components/large text) |

**Implication:** `--ss-color-warning` is suitable as text color on cream at any size (AA pass at 6.72:1). It is not suitable as a background color with white text for normal-size text (3.06:1 fails AA for normal text). Use ink (`#1a1512`) as text on a warning background if a warning-colored surface is ever needed — the ratio is 15.91:1.

---

## 3. CSS Strategy

### 3.1 Core Architecture (unchanged from Round 1)

Utility-first via Tailwind v4. `@layer base` for body defaults. No component-scoped `<style>` blocks. Cards defined by border rules, not fill.

### 3.2 Motion Token Mapping — Code Change Required

**Current state:** `global.css` maps color, typography, spacing, shape, and font tokens into Tailwind's `@theme inline`. Motion tokens (`--ss-motion-duration-*`, `--ss-motion-easing-*`) exist in the compiled package but are **not mapped**. Components use bare Tailwind `transition-colors` (defaults to 150ms, not consuming the token value).

**Verified against `src/styles/global.css` (read 2026-04-26):** The `@theme inline` block ends at `--radius-badge`. No `--duration-*` or `--ease-*` entries exist.

**Required addition — add to `src/styles/global.css` `@theme inline` block:**

```css
@theme inline {
  /* ---------- Motion ---------- */
  --duration-instant: var(--ss-motion-duration-instant);
  --duration-fast: var(--ss-motion-duration-fast);
  --duration-base: var(--ss-motion-duration-base);
  --duration-slow: var(--ss-motion-duration-slow);
  --ease-standard: var(--ss-motion-easing-standard);
  --ease-decelerate: var(--ss-motion-easing-decelerate);
  --ease-accelerate: var(--ss-motion-easing-accelerate);
}
```

**Effect:** After this addition, `transition-colors duration-fast ease-standard` is a valid Tailwind class trio that resolves to `transition-colors 150ms cubic-bezier(0.4, 0.0, 0.2, 1)`. Existing components that use bare `transition-colors` continue to work unchanged (Tailwind's default 150ms duration still applies; they simply do not consume the token). New authored code must use the token-backed utilities.

**This is a code change required before Phase 2 ships.** Target: CSS hygiene PR.

### 3.3 Focus Ring — Global Rule Required

**Current state:** No `:focus-visible` global rule in `global.css`. Components use ad-hoc Tailwind `focus-visible:ring-2 focus-visible:ring-[color:var(--ss-color-action)]` — correct in existing components, but not guaranteed in new components or external contributions.

**Required addition — add to `src/styles/global.css` `@layer base` block:**

```css
@layer base {
  :focus-visible {
    outline: var(--ss-focus-ring-width, 2px) solid var(--ss-color-action);
    outline-offset: var(--ss-focus-ring-offset, 2px);
  }
}
```

**Fallback values** (`2px` defaults in `var()`) ensure the rule works even if the token tokens are not compiled. This is a belt-and-suspenders approach for the focus ring specifically — it must always render.

**WCAG anchor:** SC 2.4.7 (Focus Visible, AA) and SC 2.4.11 (Focus Appearance, AA in WCAG 2.2). The burnt orange (#c5501e) at 2px achieves 4.06:1 against cream — satisfies the 3:1 focus appearance threshold.

**Component-level overrides** (e.g., `PortalTabs.astro` mobile inset ring) remain valid. The global rule establishes the floor; components may tighten it with `outline: none; outline-offset: ...` followed by the inset pattern. Never suppress focus-visible without providing an equivalent visible indicator.

### 3.4 `prefers-reduced-motion` — Required Global Rule

**Current state:** No `prefers-reduced-motion` override in `global.css`. Brand Strategist and Round 1 both flagged this as a WCAG 2.1 SC 2.3.3 gap.

**Required addition — add to `src/styles/global.css` `@layer base` block (after `:focus-visible` rule):**

```css
@layer base {
  @media (prefers-reduced-motion: reduce) {
    *,
    *::before,
    *::after {
      animation-duration: 0.01ms !important;
      animation-iteration-count: 1 !important;
      transition-duration: 0.01ms !important;
      scroll-behavior: auto !important;
    }
  }
}
```

**`scroll-behavior: auto`** is included per WCAG 2.1 SC 2.3.3 guidance. Smooth scrolling can trigger vestibular issues in users with motion sensitivity. Setting it to `auto` disables smooth scroll globally when `prefers-reduced-motion` is active.

**`0.01ms` not `0ms`:** Using `0.01ms` rather than `0ms` avoids a known browser bug in some versions of Safari where `transition-duration: 0ms` can prevent some state transitions from rendering visually. `0.01ms` is effectively instant while maintaining correct cascade behavior.

**`!important` is intentional:** This is a user accessibility preference. It must override component-level animation settings regardless of specificity.

**This is a code change required before Phase 2 ships.** Same CSS hygiene PR as the motion token mapping.

### 3.5 Complete `@layer base` After Changes

The `@layer base` block in `global.css` should read:

```css
@layer base {
  body {
    background-color: var(--ss-color-background);
    color: var(--ss-color-text-primary);
    font-family: var(--ss-font-body);
    -webkit-font-smoothing: antialiased;
  }
  h1,
  h2,
  h3 {
    font-family: var(--ss-font-display);
  }

  /* Focus ring — system default. Component overrides must maintain visible indicator. */
  :focus-visible {
    outline: var(--ss-focus-ring-width, 2px) solid var(--ss-color-action);
    outline-offset: var(--ss-focus-ring-offset, 2px);
  }

  /* Reduced motion — WCAG 2.1 SC 2.3.3. Overrides all component-level animation. */
  @media (prefers-reduced-motion: reduce) {
    *,
    *::before,
    *::after {
      animation-duration: 0.01ms !important;
      animation-iteration-count: 1 !important;
      transition-duration: 0.01ms !important;
      scroll-behavior: auto !important;
    }
  }
}
```

---

## 4. Dark Mode

**No change from Round 1.** Light-only is the current decision. Cream paper (`#f5f0e3`) is the identity, not a light-mode variant.

**Architecture note for future readiness:** The `--ss-color-*` token layer would support dark mode by reassigning values under `@media (prefers-color-scheme: dark)`. The Tailwind `dark:` utility prefix is not in use. Do not implement speculatively. Track as post-Phase 5 option.

---

## 5. Responsive Implementation

### 5.1 Strategy (unchanged from Round 1)

Mobile-first. Portal: `max-w-5xl mx-auto`. Admin: dense information display, desktop-primary.

### 5.2 Touch Target Enforcement

**WCAG 2.5.5 AA:** Minimum 44×44 CSS pixels for all interactive targets.

**Utility class approach (preferred for Tailwind v4 projects):**

```css
/* Add to src/styles/global.css — not in @layer base, as a standalone utility */
.touch-target {
  min-block-size: 44px;
  min-inline-size: 44px;
}
```

`min-block-size` and `min-inline-size` are the logical property equivalents of `min-height` and `min-width`, correct for i18n-aware layouts. In an LTR/horizontal-writing context (which is the full scope of this product), they resolve identically to `min-height` and `min-width`.

**Alternative — Tailwind plugin approach:** A Tailwind v4 plugin can add `touch-target` as a generated utility. At this codebase's scale (Tailwind v4 without a plugin ecosystem yet established), the explicit CSS class in `global.css` is simpler and less fragile.

**Existing component audit:**

| Component                         | Touch target status                  | Action needed                                                                                 |
| --------------------------------- | ------------------------------------ | --------------------------------------------------------------------------------------------- |
| `PortalHeader.astro` icon buttons | `w-11 h-11` (44px square)            | Compliant                                                                                     |
| `PortalTabs.astro` mobile items   | `min-h-[64px]`                       | Compliant                                                                                     |
| `PortalTabs.astro` desktop items  | `min-h-[44px]`                       | Compliant                                                                                     |
| `PortalListItem.astro` rows       | `min-h-[44px]` on flex container     | Compliant                                                                                     |
| `PortalPageHead.astro` crumb link | `min-h-11` (44px)                    | Compliant                                                                                     |
| `ActionCard.astro` CTA            | Full-width; height driven by padding | Audit required — confirm `min-h-[44px]` is set                                                |
| `CtaButton.astro`                 | Needs audit                          | Likely compliant for primary; ghost variant may be undersized                                 |
| `QuoteLineItemEditor.astro` (New) | Must specify                         | Delete and add-row buttons: `min-h-[44px] min-w-[44px]` required even when visually icon-only |
| `FollowUpCard.astro` (New)        | Must specify                         | Action buttons: `min-h-[44px]` required per admin-ux-brief                                    |

**Row-level tappable area extension:** On portal list surfaces, the tappable area for each row extends full row width (not just the chevron icon). `PortalListItem.astro` already implements this via wrapping the full card in `<a>` — compliant. Admin table rows that require row-level action must extend the touch target to the full row if the row is tappable.

### 5.3 `SigningView.astro` Responsive Notes

- Mobile: iframe height `calc(100dvh - 7rem)`. `dvh` fallback `calc(100vh - 7rem)`. Avoid fixed pixel heights — iOS Safari changes its chrome dynamically.
- Scope reminder: `<details>/<summary>` — collapsed by default on mobile, always open on desktop.
- No horizontal scroll at any breakpoint (WCAG 1.4.10 Reflow).

### 5.4 CLS Prevention

- `SigningView.astro` iframe: pre-specify container dimensions in HTML to prevent CLS during iframe load.
- `ConsultantBlock.astro` photos: explicit `width` and `height` attributes or `aspect-ratio: 1` CSS.
- `MoneyDisplay.astro`: amounts are server-rendered; no CLS risk.

---

## 6. Accessibility

### 6.1 Focus Management

**Focus ring spec (formalized in Round 2):**

```css
/* Canonical focus ring — defined in @layer base, global.css */
:focus-visible {
  outline: 2px solid var(--ss-color-action);
  outline-offset: 2px;
}
```

**Rationale:** `outline` rather than Tailwind's `ring` utilities for the base rule. `outline` is the native browser focus indicator mechanism and is not affected by `overflow: hidden` clipping issues that `box-shadow`-based rings can encounter.

**Component overrides remain valid:**

- `PortalTabs.astro` mobile (inset): `focus-visible:ring-2 focus-visible:ring-[color:var(--ss-color-action)] focus-visible:ring-inset`
- `ActionCard.astro` (inset on ink border): same inset pattern

The global rule ensures zero-configuration compliance for all new components. Component overrides must still provide a visible focus indicator — `focus-visible:outline-none` alone is a WCAG violation unless paired with an equivalent ring.

### 6.2 Touch Targets

See §5.2.

### 6.3 Outdoor Readability — `text-muted` Usage Rule

**Marcus's observation:** "If any of the secondary text — the muted ink color — is too light, I'm not reading it [in direct sunlight in my truck]."

**WCAG context:** `--ss-color-text-muted` (#8a7f73) achieves 3.44:1 on cream. This passes WCAG AA for large text (18px+) and UI components (3:1 threshold) but fails for normal text (4.5:1 threshold).

**Outdoor readability context:** Direct sunlight significantly reduces effective contrast. Mobile display brightness is limited. Studies from the Web Accessibility Initiative suggest that contrast ratios perceived in controlled environments can effectively drop by 30–50% in high-ambient-light conditions. A 3.44:1 ratio in sunlight may read as effectively 2:1 or below — subjectively unreadable to many users.

**WCAG AAA for outdoor robustness:** 7:1 threshold. `--ss-color-text-secondary` (#4a423c) at 8.64:1 meets this comfortably. This is the appropriate choice for any data Marcus needs to act on.

**Formal usage rule (enforced from Round 2):**

`--ss-color-text-muted` (`#8a7f73`, 3.44:1) is restricted to:

- Non-essential metadata: timestamps, record IDs (ULID values), decorative labels that duplicate adjacent information
- Placeholder text in form inputs (where placeholder is supplementary, not the label — labels use `text-secondary`)
- Tertiary information in list rows where `text-primary` carries the essential data

`--ss-color-text-muted` is **prohibited** for:

- Primary information: prices, proposal status, invoice amounts, milestone names
- Information Marcus needs to act on (pay, sign, contact)
- Any standalone status or date where no `text-primary` value redundantly conveys the same meaning

**No new token required.** `--ss-color-text-secondary` (#4a423c, 8.64:1) is the correct choice for secondary-but-actionable information. The rule clarifies the boundary between `text-secondary` (always legible, even outdoors) and `text-muted` (decorative only).

**Implementation note:** The Interaction Designer's portal layouts already use `text-caption` with `--ss-color-text-secondary` for due dates and invoice amounts. This is compliant. A usage audit of `text-muted` in portal components is recommended before Phase 2 ships to confirm no essential data uses it.

### 6.4 ARIA Patterns

**`StatusPill.astro`:** `aria-hidden` is the consuming page's responsibility, not the component's. Pattern 02 compliance (suppress pill when prose states the same fact) is enforced at the page level. This remains unchanged from Round 1.

**`SigningView.astro` (New):**

- `aria-busy="true"` on outer container while iframe loads
- `title="Sign proposal"` on `<iframe>` element — required for WCAG 2.4.1 (Bypass Blocks) and 4.1.2 (Name, Role, Value)
- Post-sign panel: `aria-live="assertive"` + focus shift to confirmation heading

**`EngagementProgress.astro`:** Timeline as `<ol role="list">` — required for iOS Safari VoiceOver when `list-style: none` is applied.

**`QuoteLineItemEditor.astro` (New, React island):** Each row's inputs must have explicit `<label>` or `aria-label`. The add-row trigger must be `<button>` (not `<div>` or `<span>`). Delete-row: keyboard-operable, 44px touch target.

**Dynamic content:**

- Status updates: `aria-live="polite"`
- Form errors: `aria-live="assertive"`, linked to input via `aria-describedby`
- Loading: visually hidden `<span role="status">Loading...</span>` that clears when content appears

### 6.5 Color Contrast Summary

| Pairing                             | Ratio   | WCAG AA Normal                 | Notes                                            |
| ----------------------------------- | ------- | ------------------------------ | ------------------------------------------------ |
| `text-primary` (#1a1512) on cream   | 15.91:1 | Pass (AAA)                     | All primary content                              |
| `text-secondary` (#4a423c) on cream | 8.64:1  | Pass (AAA)                     | Outdoor-safe. Use for actionable secondary data. |
| `text-muted` (#8a7f73) on cream     | 3.44:1  | Fail (normal), Pass (large/UI) | Decorative and non-essential metadata only       |
| `primary` (#c5501e) on cream        | 4.06:1  | Fail (normal), Pass (large/UI) | Not for body-size inline text                    |
| White on `primary` (#c5501e)        | 4.63:1  | Pass                           | CTA button text                                  |
| White on `primary-hover` (#a84318)  | 6.03:1  | Pass                           | Hover state                                      |
| `complete` (#4a6b3e) on cream       | 5.33:1  | Pass                           | Success text                                     |
| White on `complete` (#4a6b3e)       | 6.06:1  | Pass                           | White on olive surface                           |
| `error` (#a02a2a) on cream          | 6.45:1  | Pass                           | Error message body                               |
| `warning` (#6b4f08) on cream        | 6.72:1  | Pass (AAA-adjacent)            | Warning text on cream                            |
| White on `warning` (#6b4f08)        | 3.06:1  | Fail (normal), Pass (large/UI) | Use ink on warning surface                       |
| Focus ring (`#c5501e`) on cream     | 4.06:1  | 3:1 focus threshold — Pass     | WCAG 2.2 SC 2.4.11                               |

---

## 7. Performance Budget

### 7.1 Core Web Vitals (unchanged from Round 1)

| Metric | Slow 3G target | 4G target |
| ------ | -------------- | --------- |
| FCP    | 1,500ms        | 800ms     |
| LCP    | 2,500ms        | 1,200ms   |
| CLS    | < 0.1          | < 0.1     |
| INP    | < 500ms        | < 200ms   |

### 7.2 Asset Budgets — Revised

| Asset                          | Budget (gzipped)                             | Change from Round 1         |
| ------------------------------ | -------------------------------------------- | --------------------------- |
| Total CSS bundle               | < 30KB                                       | Unchanged                   |
| Initial JS — **portal routes** | **0KB**                                      | Clarified from "< 50KB"     |
| Initial JS — **admin routes**  | **≈50KB** (for `QuoteLineItemEditor` island) | Split from single 50KB line |
| Font payload (WOFF2)           | < 200KB total                                | Unchanged                   |
| Hero images                    | None                                         | Unchanged                   |
| Consultant photos              | < 40KB per photo at 120×120px                | Unchanged                   |

**Portal JS budget is 0KB.** Astro is HTML-first. `QuoteLineItemEditor` is the only interactive island in MVP scope. It is a React island used on the admin quote builder page (`/admin/entities/[id]/quotes/[quoteId]`) and must be scoped to that route only via `client:load` on that page. It must not appear on any portal route.

**Confirmation:** No portal page in `src/pages/portal/` should import a component with `client:load`, `client:idle`, or `client:visible` in MVP scope. The signing interaction within `SigningView.astro` is a third-party iframe — no portal-side JS is needed for it beyond an optional `load` event listener for `aria-busy` removal (which can be a minimal inline `<script>`, not a React island).

**SOW PDF endpoint latency:** `< 800ms` p95 response time. This covers the PDF generation and R2 upload path triggered by the admin "Generate SOW" action. This is a server-side budget — not a client-side metric. If PDF generation approaches the 800ms limit, consider async generation with a job-queue pattern and polling UI.

**Invoice print:** Browser native — no server latency budget applies.

### 7.3 CLS Prevention

- `SigningView.astro` iframe: pre-specify `height: calc(100dvh - 7rem)` on the container as a CSS rule (not inline) so the browser can allocate space before the iframe loads.
- `ConsultantBlock.astro` photos: `width={120} height={120}` attributes on `<img>` tags.
- `MoneyDisplay.astro`: server-rendered — no CLS risk.

### 7.4 Font Loading

Add `<link rel="preconnect">` tags to portal and admin layout `<head>`:

```html
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
```

Reduces font load latency by approximately 100–200ms on mobile networks (one fewer DNS+TLS round trip).

---

## 8. Animation and Motion

### 8.1 Guiding Principle (unchanged)

Paint-job, not brochure. Motion is functional, not expressive. No scroll-triggered animations. No celebratory animations. No animated loading skeletons.

### 8.2 Duration Reference

| Use case                               | Duration | Token (after `@theme inline` addition)      |
| -------------------------------------- | -------- | ------------------------------------------- |
| Hover color change                     | 150ms    | `duration-fast`                             |
| Focus ring appearance                  | 150ms    | `duration-fast`                             |
| Tab active state transition            | 150ms    | `duration-fast`                             |
| Disclosure/accordion open              | 250ms    | `duration-base`                             |
| Confirmation panel swap (post-signing) | 250ms    | `duration-base`                             |
| Page-level transition (deferred)       | 300ms    | Between `duration-base` and `duration-slow` |

### 8.3 Easing

Use `ease-standard` (after `@theme inline` addition) for all directional transitions. `ease-decelerate` for elements entering the viewport. `ease-accelerate` for elements exiting. In MVP scope, only `ease-standard` applies — the motion events are hover-level color/transform changes, not entrance/exit animations.

### 8.4 What Does Not Animate

- `StatusPill` tone changes (server-rendered static content)
- `MoneyDisplay` value changes (not dynamic in current implementation)
- List rows appearing on page load
- Error messages (must be instant — `aria-live="assertive"` context requires immediate render)

### 8.5 Existing Motion Inventory

All existing component motion is hover-only and sub-200ms:

- `PortalListItem.astro`: `transition-colors hover:border-[color:var(--ss-color-text-muted)]` — 150ms color only
- `PortalListItem.astro` ticket arrow: `transition-transform group-hover:translate-x-0.5` — 150ms translate
- `PortalTabs.astro`: `transition-colors` on tab items — 150ms
- `PortalHeader.astro` icon links: `hover:text-[color:var(--ss-color-primary)]` — 150ms
- `ActionCard.astro` CTA: `hover:bg-[color:var(--ss-color-primary-hover)] transition-colors` — 150ms

All compliant with the identity and the PRD §14 "no hover-dependent interactions on mobile" constraint. Hover states are progressive enhancement only.

### 8.6 `prefers-reduced-motion` Implementation

See §3.4 for the full CSS block. The rule reduces all component transitions to `0.01ms` — effectively instant. The `--ss-motion-duration-instant: 0ms` token is available for components that want to explicitly opt into instant transitions regardless of user preference (e.g., error message display, which should always be instant).

---

## Open Design Decisions

The following items were not fully resolved in Round 2 and require a decision before Phase 3 ships:

1. **Tab count — 4 or 5 destinations.** The Interaction Designer deferred Documents to a potential 5th tab. `PortalTabs.astro` currently renders 4 destinations. The Apple HIG recommendation for mobile bottom navigation is 2–5 items. Adding a 5th (Documents) is within spec. Decision point: does documents frequency justify a persistent tab, or is it better reached from the dashboard or via a secondary action? Defer to Interaction Designer.

2. **Signing route — distinct URL or state within quotes/[id].** The Interaction Designer identified this as a disambiguation point (PRD §9 specifies `/portal/quotes/[id]/sign` as a distinct route; current codebase renders signing as a state). `SigningView.astro` (New) is specified for the distinct-route pattern. If the state-within-page pattern is chosen, the component spec remains valid but the routing changes.

3. **Email template remediation priority.** Current templates use a non-compliant palette (blue, slate grays). This is a visual quality issue, not a P0 violation. The `EMAIL_TOKENS` snapshot is specified; implementation timing depends on when the first real client portal invitation will be sent.

4. **`CtaButton.astro` `disabled` prop and destructive variant.** Required before Phase 3 admin destructive actions ship. Track as a Phase 3 prerequisite.

5. **SignWell iframe keyboard focus (Tab exit).** Verify with SignWell's embed API documentation that Tab can exit the iframe. If it cannot, a visible "Return to portal" button outside the iframe is required. This is a UX-002 dependency and must be resolved before Phase 2 ships.

---

_SMD Services — Design Technologist Contribution, Design Brief Round 2_
_Plainspoken Sign Shop identity. Cross-role revision. Paint-job, not brochure._
