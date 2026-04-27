# Design Technologist Contribution - Design Brief Round 1

**Author:** Design Technologist (Sonnet)
**Date:** 2026-04-26
**Design Maturity:** Full system (tokens + components, W3C-DTCG source, 7 enforced UI patterns)

---

## Component Inventory

Every UI component required for PRD §8 MVP features (Phases 1–3). Components are inventoried across portal, admin, and shared surfaces. Status classification: **Exists** = usable as-is, **Exists (needs update)** = file present, change required, **New** = does not exist.

### Portal Components

| Name                         | Purpose                                                                                                                      | Variants                                                                   | Status  | File path                                         | ARIA role/pattern                                                                                                                             |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- | ------- | ------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `PortalHeader.astro`         | Sticky top band: client name left, contact icons right (email/SMS/tel); slot for sign-out                                    | default                                                                    | Exists  | `src/components/portal/PortalHeader.astro`        | `role="banner"`, icon links have `aria-label`                                                                                                 |
| `PortalTabs.astro`           | Persistent navigation — desktop top tabs, mobile fixed bottom bar; 4 destinations                                            | desktop / mobile (both in same component)                                  | Exists  | `src/components/portal/PortalTabs.astro`          | `<nav aria-label="Portal sections">`, `aria-current="page"` on active tab                                                                     |
| `PortalPageHead.astro`       | Page-level header block: crumb link, optional stamp tag, H1 (hero scale), optional mono meta column                          | with-crumb / no-crumb                                                      | Exists  | `src/components/portal/PortalPageHead.astro`      | Renders `<header>` with `<h1>` — callers must not add a second H1                                                                             |
| `PortalListItem.astro`       | The only list-row markup on portal surfaces; two variants × two chromes                                                      | `variant: 'status' \| 'document'` × `chrome: 'card' \| 'ticket'`           | Exists  | `src/components/portal/PortalListItem.astro`      | Whole card is `<a>`; chevron/arrow `aria-hidden`; no redundant "View" button                                                                  |
| `StatusPill.astro`           | Tone-based status tag (rectangle, mono-caps); single-accent discipline — one signal per fact                                 | `size: 'compact' \| 'base'`                                                | Exists  | `src/components/portal/StatusPill.astro`          | `<span>` with visible text label; decorative when text echoes surrounding prose (Rule 2)                                                      |
| `MoneyDisplay.astro`         | Dollar-figure renderer; cents-in to avoid float rounding; no decimals on whole-dollar amounts                                | `size: 'hero' \| 'total' \| 'kpi' \| 'row' \| 'display' \| 'h2' \| 'body'` | Exists  | `src/components/portal/MoneyDisplay.astro`        | `<span class="tabular-nums">` — purely presentational, no ARIA needed                                                                         |
| `ActionCard.astro`           | Dominant CTA card: ink-header label strip, hero money display, primary CTA button, optional subtext                          | single layout                                                              | Exists  | `src/components/portal/ActionCard.astro`          | `<section aria-label={pillLabel}>`; CTA is `<a>` not `<button>` (navigates to Stripe)                                                         |
| `ConsultantBlock.astro`      | Named-human block: photo/silhouette, name, role, next touchpoint, contact channel                                            | `variant: 'default' \| 'trade-card'`                                       | Exists  | `src/components/portal/ConsultantBlock.astro`     | Photo `alt` must be consultant name; silhouette fallback `alt=""`; contact links have `aria-label`                                            |
| `EngagementProgress.astro`   | Full engagement progress surface: summary panel, activity log, consultant block                                              | single layout                                                              | Exists  | `src/components/portal/EngagementProgress.astro`  | `<main id="main" role="main">`; sections use `aria-labelledby`; timeline rendered as `<ol role="list">`                                       |
| `PortalHomeDashboard.astro`  | Dashboard body component for `/portal/` home: state-branched by engagement lifecycle                                         | pre-sign / active / error states                                           | Exists  | `src/components/portal/PortalHomeDashboard.astro` | `<main id="main" role="main">`; error state uses `aria-live="polite"`                                                                         |
| `QuoteDetail.astro`          | Full proposal detail body: state-branched by quote status                                                                    | isSigned / isDeclined / isExpired / isSuperseded / isSent                  | Exists  | `src/components/portal/QuoteDetail.astro`         | `<main id="main" role="main">`; signing CTA is `<a>` to sign route                                                                            |
| `InvoiceDetail.astro`        | Invoice detail body: receipt vs. pending-payment vs. error states                                                            | isPaid / overdue / payment error states                                    | Exists  | `src/components/portal/InvoiceDetail.astro`       | `<main id="main" role="main">`                                                                                                                |
| `InvoicesList.astro`         | Invoice list surface; renders via `<PortalListItem>`                                                                         | list view                                                                  | Exists  | `src/components/portal/InvoicesList.astro`        | `<main>` with list of `<PortalListItem>` links                                                                                                |
| `QuoteList.astro`            | Quote list surface; renders via `<PortalListItem>`                                                                           | list view                                                                  | Exists  | `src/components/portal/QuoteList.astro`           | Same pattern as InvoicesList                                                                                                                  |
| `Documents.astro`            | Document library surface; flat list of `<PortalListItem variant="document">`                                                 | list view                                                                  | Exists  | `src/components/portal/Documents.astro`           | External links use `target="_blank" rel="noopener noreferrer"`                                                                                |
| `TimelineEntry.astro`        | Dated narrative entry in engagement activity log; Archivo Narrow meta row above body prose                                   | single                                                                     | Exists  | `src/components/portal/TimelineEntry.astro`       | Rendered inside `<ol role="list"><li>` by parent                                                                                              |
| `ArtifactChip.astro`         | Mono-caps inline artifact link (PDF, doc, external); plain underlined, no bordered chip                                      | with-icon / no-icon                                                        | Exists  | `src/components/portal/ArtifactChip.astro`        | Standard `<a>`; icon is `aria-hidden`                                                                                                         |
| `SkipToMain.astro`           | Skip-to-main accessibility link; appears on focus before all portal/booking pages                                            | single                                                                     | Exists  | `src/components/SkipToMain.astro`                 | `<a href="#main">` with focus-visible ring; targets `id="main"` on `<main>`                                                                   |
| `SigningView.astro`          | SignWell iframe container: scope reminder sidebar (collapsible mobile), full-width iframe, post-signature confirmation panel | desktop / mobile (responsive)                                              | **New** | —                                                 | iframe `title="Sign proposal"` required; post-sign confirmation is `aria-live="assertive"`; iframe must not trap keyboard focus (per PRD §14) |
| `MagicLinkExpiredForm.astro` | Single-field recovery form for expired/used tokens; email input + submit                                                     | single                                                                     | **New** | —                                                 | `<form>` with `<label>` on email input; error state via `aria-describedby`; submit button primary hierarchy                                   |
| `ParkingLotPanel.astro`      | Parking lot disposition list (Phase 5 scope, but component spec needed now); fold-in / follow-on / dropped states            | post-handoff view                                                          | **New** | —                                                 | `<section aria-labelledby>` with list items; each item shows disposition as text, not pill (detail-page rule, R1)                             |

### Admin Components

| Name                          | Purpose                                                                                                 | Variants                       | Status  | File path                                          | ARIA role/pattern                                                                                              |
| ----------------------------- | ------------------------------------------------------------------------------------------------------- | ------------------------------ | ------- | -------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `EnrichmentStatusPanel.astro` | Shows enrichment pipeline status for a lead/client record                                               | running / timed-out / complete | Exists  | `src/components/admin/EnrichmentStatusPanel.astro` | `aria-live="polite"` for status changes                                                                        |
| `LogReplyDialog.astro`        | Modal dialog for logging a follow-up reply with notes                                                   | single                         | Exists  | `src/components/admin/LogReplyDialog.astro`        | `role="dialog"`, `aria-modal="true"`, focus trap required                                                      |
| `PipelineKanban.astro`        | Admin pipeline view: status columns, client cards, overdue flags                                        | 5 status columns               | **New** | —                                                  | `<main>` with column regions; each card is `<article>` or `<li>`; overdue flag is visible text, not color-only |
| `ClientCard.astro`            | Pipeline card: business name, vertical, days in status, next action, overdue flag                       | default / overdue              | **New** | —                                                  | `<a>` or `<article>` depending on clickability; overdue state announced via text label                         |
| `QuoteLineItemEditor.astro`   | Repeating line-item rows in quote builder: problem dropdown, description, hours, real-time totals       | single                         | **New** | —                                                  | Each row has labelled inputs; add-row trigger is `<button>`; delete on empty row is keyboard-operable          |
| `SOWPreviewPane.astro`        | Read-only SOW preview before PDF generation; split-pane with quote builder                              | single                         | **New** | —                                                  | `role="region" aria-label="SOW preview"`                                                                       |
| `FollowUpCard.astro`          | Follow-up cadence card: client name, type, date, one-click complete/skip actions                        | overdue / due-today / upcoming | **New** | —                                                  | `<article>`; action buttons meet 44px minimum tap target                                                       |
| `TimeEntryLog.astro`          | Time entry list for engagement dashboard: date, hours, category, description                            | list view                      | **New** | —                                                  | `<table>` with `<thead>` and column headers for the ledger structure                                           |
| `ExtractionPanel.astro`       | Renders structured Claude extraction output from assessment: problems, signals, champion, disqualifiers | single                         | **New** | —                                                  | `<section aria-labelledby>`; problem identifiers render as readable labels not raw enum keys                   |

### Shared / Booking Components

| Name               | Purpose                                                                                     | Variants                                  | Status                | File path                                 | ARIA role/pattern                                                                                     |
| ------------------ | ------------------------------------------------------------------------------------------- | ----------------------------------------- | --------------------- | ----------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `SlotPicker.astro` | Assessment booking slot selection and confirmation; 3-state: available / selected / confirm | slot-selection / confirm                  | Exists                | `src/components/booking/SlotPicker.astro` | Slot items are `<button>` or `<input type="radio">`; selected state announced; confirm is primary CTA |
| `CtaButton.astro`  | Shared primary button atom used on marketing and portal; burnt-orange solid                 | primary / secondary / ghost / destructive | Exists (needs update) | `src/components/CtaButton.astro`          | Needs `disabled` prop and ARIA handling; destructive variant not yet implemented                      |

---

## Component Props Interfaces

Key interfaces for New components and those with complex prop contracts:

```ts
// SigningView.astro
interface Props {
  /** Quote data for scope reminder sidebar */
  quote: {
    engagementTitle: string
    depositPct: number
    totalCents: number
    paymentSplitText: string
    deliverables: Array<{ title: string; body: string }>
  }
  /** SignWell embed URL for the iframe */
  signwellEmbedUrl: string
  /** Called after SignWell fires document.completed postMessage */
  postSignRedirectHref: string
  /** Whether signing has already completed (render confirmation state) */
  isSigned: boolean
  /** PDF href for signed SOW (shown in post-sign state) */
  signedPdfHref?: string | null
}
```

```ts
// MagicLinkExpiredForm.astro
interface Props {
  /** Pre-fill email if known from the expired token context */
  emailHint?: string | null
  /** Error message to display (e.g., "Rate limit reached. Try again in 10 minutes.") */
  errorMessage?: string | null
  /** POST endpoint */
  action: string
}
```

```ts
// ParkingLotPanel.astro
interface Props {
  items: Array<{
    id: string
    description: string
    requestedBy: string | null
    requestedAt: string
    disposition: 'fold_in' | 'follow_on' | 'dropped' | null
    dispositionNote: string | null
    reviewedAt: string | null
    followOnQuoteId: string | null
  }>
  /** Render in read-only mode (client portal) vs. admin edit mode */
  readonly: boolean
}
```

```ts
// PipelineKanban.astro
interface Props {
  columns: Array<{
    status: 'prospect' | 'assessed' | 'quoted' | 'active' | 'completed'
    clients: Array<{
      id: string
      businessName: string
      vertical: string
      daysInStatus: number
      nextAction: string | null
      isOverdue: boolean
    }>
  }>
  showDead: boolean
}
```

```ts
// QuoteLineItemEditor.astro
// Note: This is a React island (interactive); the only JS-required component in MVP
interface Props {
  /** Pre-populated from assessment extraction */
  initialItems: Array<{
    problem: string
    description: string
    estimatedHours: number
  }>
  /** Rate frozen at quote creation — display only, not editable */
  rate: number
  /** Readonly when quote.status !== 'draft' */
  readonly: boolean
  /** POST endpoint for saving items */
  saveHref: string
}
```

```ts
// ExtractionPanel.astro
interface Props {
  extraction: {
    problems: Array<
      | 'owner_bottleneck'
      | 'lead_leakage'
      | 'financial_blindness'
      | 'scheduling_chaos'
      | 'manual_communication'
      | 'employee_retention'
    >
    complexity_signals: string[]
    champion_candidate: string | null
    disqualification_flags: string[]
  } | null
  /** Whether to show the "paste JSON" edit mode */
  editable: boolean
}
```

---

## Design Token Architecture

### Naming Convention

All tokens use the `--ss-{category}-{variant}` prefix. This is already in place across the compiled output and all component source. Token source lives in:

- **Source of truth (W3C-DTCG JSON):** `/Users/scottdurgan/dev/crane-console/packages/tokens/src/ventures/ss.json`
- **Compiled CSS (published package):** `node_modules/@venturecrane/tokens/dist/ss.css`
- **Tailwind v4 mapping:** `src/styles/global.css` (`@theme inline` block)

No token naming changes are needed. The convention is confirmed and consistent.

### Token Categories

#### Color (`--ss-color-*`)

```css
/* Surface */
--ss-color-background: #f5f0e3; /* Cream paper — body background */
--ss-color-surface: #f5f0e3; /* Card background — same as background; cards use border rules */
--ss-color-surface-inverse: #1a1512; /* Ink — inverted surfaces */

/* Border */
--ss-color-border: rgba(26, 21, 18, 0.16); /* Ink at 16% — default border */
--ss-color-border-subtle: rgba(26, 21, 18, 0.08); /* Ink at 8% — dividers */

/* Text */
--ss-color-text-primary: #1a1512; /* Ink — primary content */
--ss-color-text-secondary: #4a423c; /* Subdued ink — metadata */
--ss-color-text-muted: #8a7f73; /* Muted ink — placeholders, tertiary */
--ss-color-meta: #4a423c; /* Alias of text-secondary for card timestamps/IDs */

/* Brand & semantic */
--ss-color-primary: #c5501e; /* Burnt orange — CTAs, single accent */
--ss-color-primary-hover: #a84318; /* Deepened orange — hover state */
--ss-color-action: #c5501e; /* Focus-ring color — semantically distinct from primary */
--ss-color-attention: #c5501e; /* Attention signals — same hue, distinct slot */
--ss-color-complete: #4a6b3e; /* Olive — success, completed states */
--ss-color-error: #a02a2a; /* Brick — error, danger states */
```

Single-accent discipline: `primary`, `action`, and `attention` resolve to the same burnt orange value. Three named slots allow semantic evolution without changing the rendered color. No second accent color is in scope.

#### Typography (`--ss-text-{prop}-{name}`)

The token package uses a flat naming scheme. Tailwind v4's `@theme inline` remaps using the sibling quartet syntax (`--text-{name}`, `--text-{name}--line-height`, `--text-{name}--font-weight`, `--text-{name}--letter-spacing`).

**Standard scale (all surfaces):**

| Token name | Size             | Line-height      | Weight | Letter-spacing | Use                                |
| ---------- | ---------------- | ---------------- | ------ | -------------- | ---------------------------------- |
| `display`  | 48px (3rem)      | 54px (3.375rem)  | 500    | -0.01em        | Page hero on non-portal surfaces   |
| `title`    | 28px (1.75rem)   | 34px (2.125rem)  | 500    | -0.005em       | Section heading, card title        |
| `heading`  | 18px (1.125rem)  | 24px (1.5rem)    | 600    | —              | Sub-section heading                |
| `body-lg`  | 17px (1.0625rem) | 26px (1.625rem)  | 400    | —              | Lead paragraph                     |
| `body`     | 16px (1rem)      | 24.8px (1.55rem) | 400    | —              | Default body                       |
| `caption`  | 14px (0.875rem)  | 20px (1.25rem)   | 500    | —              | Metadata, dates, status prose      |
| `label`    | 12px (0.75rem)   | 16px (1rem)      | 500    | 0.08em         | Eyebrow, section label, tab labels |
| `money`    | 44px (2.75rem)   | 48px (3rem)      | 500    | —              | Legacy money display (back-compat) |

**Plainspoken display scale (portal + hero surfaces, weight 900):**

| Token name    | Size            | Line-height     | Weight | Letter-spacing | Use                                    |
| ------------- | --------------- | --------------- | ------ | -------------- | -------------------------------------- |
| `hero`        | 72px (4.5rem)   | 66px (4.14rem)  | 900    | -0.03em        | Portal H1 — desktop                    |
| `hero-mobile` | 44px (2.75rem)  | 40px (2.53rem)  | 900    | -0.03em        | Portal H1 — mobile                     |
| `hero-price`  | 64px (4rem)     | 59px (3.68rem)  | 900    | -0.04em        | Summary-card total price               |
| `kpi`         | 44px (2.75rem)  | 44px (2.75rem)  | 900    | -0.03em        | KPI row big numbers                    |
| `section-h`   | 36px (2.25rem)  | 36px (2.25rem)  | 900    | -0.02em        | Section block headings on detail pages |
| `price-row`   | 28px (1.75rem)  | 28px (1.75rem)  | 900    | -0.02em        | Row-level money in list/ledger rows    |
| `num-cell`    | 22px (1.375rem) | 22px (1.375rem) | 900    | -0.01em        | № / § cell glyph in ticket-chrome rows |

**Mobile hero variant.** The `hero-mobile` token exists explicitly to serve the responsive pattern without media-query duplication inside components. `PortalPageHead.astro` uses `text-hero-mobile md:text-hero` — the only place this breakpoint-swap pattern appears. New portal pages with a hero H1 must follow this pattern; do not introduce ad-hoc `text-[Npx] sm:text-[Npx]` overrides.

#### Font Families (`--ss-font-*`)

```css
--ss-font-display: 'Archivo', system-ui, sans-serif;
--ss-font-body: 'Archivo', system-ui, sans-serif; /* same family — Plainspoken register */
--ss-font-accent-label: 'Archivo Narrow', 'Archivo', system-ui, sans-serif;
--ss-font-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
```

Display and body share the Archivo family. Archivo Narrow is used for tab labels, eyebrow labels, and data-table headers. JetBrains Mono is used for IDs, reference numbers, and fixed-width data values.

#### Spacing (`--ss-space-*`)

```css
--ss-space-section: 3rem; /* 48px — gap between major page sections */
--ss-space-card: 2rem; /* 32px — card internal padding */
--ss-space-stack: 1rem; /* 16px — vertical stack of sibling content */
--ss-space-row: 0.75rem; /* 12px — gap between rows in a list */
```

Note: the `UI-PATTERNS.md` Rule 6 table documents `space-section: 32px` and `space-card: 24px`. These are the documented intent values; the compiled token values in `ss.css` are `48px` and `32px` respectively. The compiled values are the source of truth. This discrepancy should be reconciled in a follow-on issue against `UI-PATTERNS.md` Rule 6 documentation — the token JSON is correct.

#### Shape (`--ss-radius-*`)

```css
--ss-radius-card: 0; /* Flat institutional — no card rounding */
--ss-radius-button: 0; /* Flat institutional — no button rounding */
--ss-radius-badge: 0; /* Flat institutional — no badge rounding */
```

All radii are zero. This is a hard identity constraint. `rounded-*` Tailwind utilities must not appear on any card, button, badge, or interactive surface in portal or admin. The identity is 1950s commercial signage — no softening.

#### Motion (`--ss-motion-*`)

```css
--ss-motion-duration-instant: 0ms;
--ss-motion-duration-fast: 150ms;
--ss-motion-duration-base: 250ms;
--ss-motion-duration-slow: 400ms;

--ss-motion-easing-standard: cubic-bezier(0.4, 0, 0.2, 1); /* Material standard */
--ss-motion-easing-decelerate: cubic-bezier(0, 0, 0.2, 1); /* Enters */
--ss-motion-easing-accelerate: cubic-bezier(0.4, 0, 1, 1); /* Exits */
```

Motion tokens are present in the compiled package. They are not yet mapped into `global.css` `@theme inline`. Components use inline `transition-colors` and `transition-transform` Tailwind utilities, which do not consume the token values. This is a gap: if the design decision changes from `250ms` to `200ms` base, the compiled token changes but component transitions do not follow.

**Recommended addition to `global.css`:**

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

This maps motion tokens into Tailwind v4's `duration-*` and `ease-*` utility namespaces, making `transition-colors duration-base ease-standard` a valid Tailwind pattern. Adding this mapping is low-risk and deferred-safe: existing `transition-colors` utilities continue to work unchanged; new authored code uses named tokens.

---

## CSS Strategy

**Primary:** Utility-first via Tailwind v4. All styling is accomplished through utility classes that resolve to `--ss-*` token values via the `@theme inline` mapping.

**Token references in source:** Components reference tokens directly as `var(--ss-color-*)` inside `[color:var(--ss-color-*)]` bracket syntax and as bare Tailwind utilities (`bg-primary`, `text-caption`, `p-card`, `rounded-card`). Both patterns are valid; new code should prefer the Tailwind utility form where the token map exists.

**`@layer base`:** Establishes body defaults — background, text color, font family, font smoothing. H1/H2/H3 use `--ss-font-display`. No other base overrides.

**Material Symbols Outlined:** Already loaded via Google Fonts link in the page head. Icon usage is `<span class="material-symbols-outlined" aria-hidden="true">{iconName}</span>` with an explicit `aria-label` or surrounding accessible text on the parent interactive element.

**Component-scoped CSS:** Essentially absent. No `<style>` blocks in existing portal components. All styling is via utility classes. This is the correct pattern — component-scoped styles fragment the cascade and make token overrides harder to reason about.

**Card definition:** Cards are defined by border rules (1px at `--ss-color-border` or 3px at `--ss-color-text-primary` for the Plainspoken ticket chrome), not by fill. Background and surface are the same cream value. New "card" components must not introduce a distinct background fill — the rule draws the card, the fill does not.

---

## Tailwind v4 `@theme inline` Mapping Pattern

`src/styles/global.css` imports `@venturecrane/tokens/ss.css` (which sets all `--ss-*` custom properties on `:root`), then uses Tailwind v4's `@theme inline` block to project those values into the Tailwind utility namespace.

```css
/* How the bridge works — excerpt from global.css */
@theme inline {
  --color-primary: var(--ss-color-primary); /* → bg-primary, text-primary, border-primary */
  --text-display: var(--ss-text-size-display); /* → text-display (size + siblings below) */
  --text-display--line-height: var(--ss-text-line-height-display);
  --text-display--font-weight: var(--ss-text-weight-display);
  --spacing-card: var(--ss-space-card); /* → p-card, m-card, gap-card */
  --radius-card: var(--ss-radius-card); /* → rounded-card */
}
```

The Tailwind v4 sibling quartet: for each `--text-{name}`, Tailwind also reads `--text-{name}--line-height`, `--text-{name}--font-weight`, and `--text-{name}--letter-spacing`. When `text-display` is used as a utility class, Tailwind automatically applies all four properties. This replaces the need for `leading-[N] tracking-[N] font-{weight}` companion classes.

**Single source of truth chain:**

```
ss.json (W3C-DTCG) → compiled ss.css (--ss-*) → global.css @theme inline → Tailwind utilities
```

Any token value change at the JSON source propagates through the entire chain automatically after the package is rebuilt and `npm install` runs.

---

## Dark Mode Implementation

**None.** Light-only is the current decision. Cream paper background (`#f5f0e3`) is the identity, not a light-mode variant. There is no dark mode token set, no `prefers-color-scheme` media query, and no `dark:` Tailwind prefix in use.

**Open Design Decision:** If dark mode becomes a future requirement (e.g., admin interface at Phase 5 for extended-session use), the token architecture supports it: adding a `prefers-color-scheme: dark` block that reassigns `--ss-color-*` values would propagate through all components automatically. The Plainspoken Sign Shop identity — ink on cream — would need to be reconsidered for a dark inversion; a direct swap (cream on ink) is plausible given the high-contrast foundation.

Do not implement dark mode speculatively. Track as a post-Phase 5 option if operator feedback identifies the need.

---

## Responsive Implementation

**Strategy:** Mobile-first. All components are authored at the narrowest viewport and `md:` breakpoint utilities upgrade the layout for desktop.

**Tailwind v4 default breakpoints (unchanged from v3 defaults):**

- `sm`: 640px
- `md`: 768px
- `lg`: 1024px
- `xl`: 1280px

Portal uses `max-w-5xl mx-auto` as the content container (1024px max-width with auto-centering). Admin uses its own layout logic — dense information display is acceptable per PRD §14.

**Key responsive patterns in use:**

1. **Hero H1:** `text-hero-mobile md:text-hero` — `PortalPageHead.astro`. This is the only place explicit mobile/desktop typography variants appear in the token system.

2. **Navigation chrome:** `PortalTabs.astro` uses `hidden md:block` for desktop tabs and `md:hidden fixed bottom-0` for the mobile bottom bar. Both are in the same component; the consumer gets both automatically.

3. **Ticket-chrome grid:** `PortalListItem.astro` collapses to a 2-row stacked layout on mobile (`md:grid md:grid-cols-[...]` pattern). Mobile: serial+title+arrow / subCaption+price+date stacked. Desktop: 5-column grid.

4. **Content container:** `max-w-5xl mx-auto px-4 sm:px-6` — consistent across all portal `<main>` blocks. New portal pages must use this exact wrapper to maintain alignment.

5. **`SigningView.astro` (New):** On mobile (< `md`), the scope reminder sidebar is collapsible (hidden by default, toggled by a disclosure button). The SignWell iframe is full-width. On desktop, the iframe occupies the main content area with the sidebar fixed to the right. Container height: `calc(100dvh - 7rem)` (accounts for header + tab bar height). Avoid fixed pixel heights — iOS Safari changes its chrome height dynamically.

---

## Accessibility (WCAG 2.1 AA)

### Focus Management

**Focus ring color:** `--ss-color-action` (#c5501e, burnt orange).

**Standard focus ring pattern** (used across all interactive elements):

```css
focus-visible:outline-none
focus-visible:ring-2
focus-visible:ring-[color:var(--ss-color-action)]
focus-visible:ring-offset-2
```

Ring width: 2px. Offset: 2px (creates visible gap between element and ring on the cream background). This achieves the required 3:1 contrast ratio for focus indicators (WCAG 2.2 SC 2.4.11).

**Inset focus ring** (used on elements with 3px ink borders where ring-offset would create visual noise):

```css
focus-visible: ring-inset;
```

Used in: `ActionCard.astro` CTA, mobile nav tabs in `PortalTabs.astro`.

### Touch Targets

Minimum 44×44px on all interactive elements (Apple HIG / WCAG 2.5.5 AA per PRD §14).

- `PortalHeader.astro` icon buttons: `w-11 h-11` (44px square).
- `PortalTabs.astro` mobile items: `min-h-[64px]` — above minimum, accounts for safe area.
- `PortalListItem.astro` rows: `min-h-[44px]` via `min-h-[44px]` on the inner flex container.
- `PortalPageHead.astro` crumb link: `min-h-11` (44px).
- New `QuoteLineItemEditor.astro` row actions: delete and add-row buttons must have explicit `min-h-[44px] min-w-[44px]` even when visually smaller.

### Keyboard Navigation

- **Portal list rows** (`PortalListItem.astro`): the entire card is `<a>` — Tab focuses the card, Enter/Space activates. No arrow-key navigation needed; it is a link list, not a widget.
- **`PortalTabs.astro` desktop tabs:** Tab moves between tab links sequentially. The active tab does not use `role="tab"` / `role="tabpanel"` — it is a standard link-based navigation pattern (separate pages per destination, not inline content switching). `aria-current="page"` signals active state to screen readers.
- **`QuoteLineItemEditor.astro` (React island):** Tab between fields within a row. Enter on last field adds a new row. Delete/Backspace on an empty row removes it (PRD §9 Interaction Patterns). The keyboard behavior must be documented in the component's prop interface and tested manually.
- **`LogReplyDialog.astro`:** Modal focus trap — focus confined within dialog on open, returned to trigger on close. Escape closes. This is the one existing admin component that requires a focus trap.
- **`SigningView.astro` (New):** The SignWell iframe must not trap keyboard focus on the portal page level. PRD §14 cites this explicitly. The iframe is sandboxed; verify with SignWell's embed documentation that Tab can exit the iframe to the portal's controls.
- **Skip link:** `SkipToMain.astro` renders `<a href="#main">Skip to main content</a>` visible on focus. All portal pages import this component. `<main id="main">` is required on every page that uses it — confirmed present in all existing portal page bodies.

### ARIA Patterns

**`StatusPill.astro`:** The status tag renders as `<span>` with visible text content. It is not a live region — status is static on load. It is not decorative when it is the first (or only) status signal in a list row. It is redundant (and should be suppressed or `aria-hidden`) when the same status is stated in adjacent prose on a detail page — per UI-PATTERNS Rule 2. The component does not implement `aria-hidden` internally; the consuming page is responsible for Rule 2 compliance. This is the correct separation.

**`EngagementProgress.astro`:** Timeline rendered as `<ol role="list">`. Each entry is `<li>` with `<TimelineEntry>` as the child. `role="list"` is required when CSS `list-style: none` removes the semantic list role in Safari (VoiceOver on iOS).

**`PortalHomeDashboard.astro` error state:** Uses `aria-live="polite"` on the error section. This is correct — the error renders server-side into the DOM and does not need `aria-live` for the initial render, but the attribute is harmless and documents intent for future dynamic updates.

**`SigningView.astro` (New):** Post-signature confirmation panel should use `aria-live="assertive"` — the signing event is a significant state change that the user needs to know about immediately. The panel replaces the iframe on the same DOM area; focus should move to the confirmation heading.

**Dynamic content (PRD §14 requirement):**

- Status updates: `aria-live="polite"` — applies to follow-up status changes, invoice status updates.
- Errors: `aria-live="assertive"` — applies to form validation errors, payment failure states.
- Loading states: use a visually hidden `<span aria-live="polite">Loading...</span>` that clears when content appears. Do not use spinners without text alternatives.

### Color Contrast

**Text on cream background (`#f5f0e3`):**

- `--ss-color-text-primary` (#1a1512): contrast ratio ~16.5:1. Exceeds AA and AAA.
- `--ss-color-text-secondary` (#4a423c): contrast ratio ~7.2:1. Exceeds AA.
- `--ss-color-text-muted` (#8a7f73): contrast ratio ~3.0:1. **Fails AA for body text (4.5:1 required), passes AA for large text (3:1).** Use muted text only at `text-heading` size (18px) or larger, and only for non-essential metadata. Do not use for any interactive element label or status-bearing text.
- `--ss-color-primary` (#c5501e) on cream: contrast ratio ~4.7:1. Passes AA for normal text.

**`StatusPill.astro` tags:** Background is the tone color (primary, complete, error, or neutral). Text should be white or cream on colored backgrounds. Verify each TONE_CLASS entry in `src/lib/portal/status.ts` for 4.5:1 contrast — specifically the neutral/info variants which may use lower-contrast tints.

**Focus ring:** `--ss-color-action` (#c5501e) at 2px width on cream background achieves 3:1 contrast (WCAG 2.2 SC 2.4.11 AA).

### Reduced Motion

**Current state:** `250ms` base duration is the default for `transition-colors` and `transition-transform` utilities. No `prefers-reduced-motion` media query is explicitly authored in any component.

**Required addition** (in `global.css` `@layer base`, or in the motion token mapping):

```css
@layer base {
  @media (prefers-reduced-motion: reduce) {
    *,
    *::before,
    *::after {
      transition-duration: 0.01ms !important;
      animation-duration: 0.01ms !important;
    }
  }
}
```

This is a global catch-all. It reduces all transitions to near-instant without breaking the layout transitions that depend on `transition-` being set. The `--ss-motion-duration-instant: 0ms` token exists for cases where specific components want to explicitly disable motion in the reduced-motion context.

---

## Performance Budget

Numeric targets for the client portal (primary surface; mobile-first per PRD §14).

### Core Web Vitals

| Metric                          | Slow 3G target | 4G target | Measurement method |
| ------------------------------- | -------------- | --------- | ------------------ |
| FCP (First Contentful Paint)    | 1,500ms        | 800ms     | Lighthouse / CrUX  |
| LCP (Largest Contentful Paint)  | 2,500ms        | 1,200ms   | Lighthouse / CrUX  |
| CLS (Cumulative Layout Shift)   | < 0.1          | < 0.1     | Lighthouse / CrUX  |
| INP (Interaction to Next Paint) | < 500ms        | < 200ms   | CrUX / lab test    |

PRD §13 worker-level targets (server-side):

- Portal SSR page load: < 800ms p95 (Workers response time)
- Admin SSR page load: < 800ms p95 (Workers response time)
- API endpoint (CRUD): < 300ms p95
- D1 indexed read: < 10ms p95

### Asset Budgets

| Asset                | Budget (gzipped) | Rationale                                                                                                                                                                                        |
| -------------------- | ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Total CSS bundle     | < 30KB           | Tailwind v4 purges unused utilities at build time; Astro component-scoped styles are minimal to non-existent. Current stack should be well under.                                                |
| Initial JS (portal)  | < 50KB           | Astro is HTML-first. Only interactive islands ship JS. Portal MVP has one confirmed JS island: `QuoteLineItemEditor` (admin-only, not portal). Portal JS budget at MVP may be near-zero.         |
| Initial JS (admin)   | < 100KB          | Admin surfaces are desktop-primary and can tolerate slightly higher initial JS for the quote builder island.                                                                                     |
| Font payload (WOFF2) | < 200KB total    | Archivo + Archivo Narrow + JetBrains Mono subsets. Google Fonts serves WOFF2 with `font-display: swap`. Confirm subset loading covers the Latin Extended range needed for client business names. |
| Hero images          | None             | Plainspoken identity uses no hero images. Consultant photos in `ConsultantBlock.astro` are the only raster images — should be < 40KB per photo at 120×120px display size.                        |

### CLS Prevention

The portal's largest layout stability risk is the `SigningView.astro` SignWell iframe. The iframe dimensions must be pre-specified in HTML (`width` and `height` attributes or equivalent CSS) to prevent CLS during load. Use `min-height: calc(100dvh - 7rem)` with an explicit fallback height for browsers without `dvh` support.

Consultant photos in `ConsultantBlock.astro` should have explicit `width` and `height` attributes (or `aspect-ratio` CSS) to prevent CLS on image load.

### Font Loading

All portal fonts are loaded via Google Fonts with `display=swap` in the `<link>` URL. Swap means text renders in the fallback font immediately, then swaps to Archivo when loaded. The cream background and Archivo system fallback are close enough that FOUT is visually mild.

**Recommendation:** Add `<link rel="preconnect" href="https://fonts.googleapis.com">` and `<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>` in the page `<head>` to reduce font load latency by ~100-200ms on mobile.

---

## Animation and Motion

**Guiding principle:** Paint-job, not brochure. Motion is functional, not expressive. No scroll-triggered animations. No celebratory animations on success states (signing confirmation, payment confirmation). No loading skeletons that animate — use a static "Loading..." text state.

### Duration Reference

| Use case                                        | Duration | Token                       |
| ----------------------------------------------- | -------- | --------------------------- |
| Hover color change (`transition-colors`)        | 150ms    | `--ss-motion-duration-fast` |
| Focus ring appearance                           | 150ms    | `--ss-motion-duration-fast` |
| Tab active state transition                     | 150ms    | `--ss-motion-duration-fast` |
| Disclosure/accordion open (collapsible sidebar) | 250ms    | `--ss-motion-duration-base` |
| Page-level transition (if added in future)      | 300ms    | between base and slow       |
| Confirmation panel swap (post-signing)          | 250ms    | `--ss-motion-duration-base` |

**What does not animate:**

- StatusPill tone changes (server-rendered static content)
- MoneyDisplay value changes (not dynamic in current implementation)
- List rows appearing on page load
- Error messages appearing (instant, to meet `aria-live="assertive"` expectations)

### Existing Motion in Components

- `PortalListItem.astro` card chrome: `transition-colors hover:border-[color:var(--ss-color-text-muted)]` — color only, 150ms (Tailwind default).
- `PortalListItem.astro` ticket chrome arrow: `transition-transform group-hover:translate-x-0.5` — 150ms translate.
- `PortalTabs.astro` tab items: `transition-colors` — 150ms.
- `PortalHeader.astro` icon links: `hover:text-[color:var(--ss-color-primary)]` — 150ms via `transition-colors`.
- `ActionCard.astro` CTA: `hover:bg-[color:var(--ss-color-primary-hover)]` + `transition-colors`.

All existing motion is hover-only and sub-200ms. This is correct per the identity and per the "no hover-dependent interactions on mobile" constraint (PRD §14) — hover states are progressive enhancement only.

### Easing

`--ss-motion-easing-standard: cubic-bezier(0.4, 0.0, 0.2, 1)` is the Material Design standard easing. Use for all directional transitions (in and out of the same element). `decelerate` for elements entering the viewport; `accelerate` for elements exiting. In practice at MVP scope, only `standard` is needed — the motion events are all hover-level color/transform changes, not entrance/exit animations.

---

## Open Design Decisions

1. **Motion token mapping to Tailwind v4.** The `--ss-motion-*` tokens are present in the compiled package but not mapped in `global.css`. Components use bare `transition-colors` without consuming the token values. Adding the `@theme inline` mapping for `duration-*` and `ease-*` closes this gap without breaking anything. Recommend: include in the next CSS hygiene PR.

2. **`prefers-reduced-motion` global rule.** Not currently in `global.css`. Adding a `@layer base` reduced-motion catch-all is zero-risk and a WCAG 2.1 AA requirement (SC 2.3.3). Recommend: include in the next CSS hygiene PR alongside the motion token mapping.

3. **`--ss-color-text-muted` contrast.** At 3.0:1 on cream, this fails WCAG AA for normal-weight body text. Current uses (placeholders, tertiary metadata at caption/label sizes) are generally at large-text sizes where 3:1 passes AA. A formal audit of every `text-muted` usage site is recommended before Phase 4 ships to confirm no normal-weight body text uses this token.

4. **UI-PATTERNS.md Rule 6 spacing values.** The documented values in Rule 6 (`section: 32px`, `card: 24px`) do not match the compiled token values (`section: 48px`, `card: 32px`). The token JSON is authoritative. The documentation should be corrected to avoid confusion when authors cross-reference the pattern spec.

5. **`CtaButton.astro` hierarchy completeness.** The shared button atom exists but lacks `disabled` prop handling and the destructive variant. Both are needed before admin destructive actions (mark-as-dead, void invoice) ship in Phase 3. Recommend: update `CtaButton.astro` as part of Phase 3 admin work.

6. **`QuoteLineItemEditor` as React island.** This is the only client-side-interactive component in the MVP scope. It ships JS to the browser — confirm it is scoped to the admin quote-builder route only and does not inflate the portal JS bundle. Use Astro's `client:load` directive only on the admin quote builder page.

7. **SignWell iframe keyboard focus.** PRD §14 states the iframe must not trap keyboard focus. This is a SignWell embed behavior outside our code. Verify with SignWell's embed API documentation before Phase 2 ships. If SignWell does not expose a focus-management API, add a visible "Return to portal" keyboard-operable exit outside the iframe.
