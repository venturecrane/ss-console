# Design Technologist Contribution - Design Brief Round 3 (Final)

**Author:** Design Technologist (claude-sonnet-4-6)
**Date:** 2026-04-26
**Design Maturity:** Full system
**Status:** Final after 3 rounds

---

## Changes from Round 2

1. **Warning token reconciled to Brand Strategist's `#7a5800`.** Round 2 used `#6b4f08` (6.72:1 on cream). Brand Strategist Round 2 revised to `#7a5800` (7.14:1 on cream, AAA pass). This round adopts `#7a5800` throughout — all token JSON, CSS, email constants, and contrast tables updated. The tradeoff is documented in §2.3. Every hex instance of `#6b4f08` in this document has been replaced.

2. **Consolidated token-package diff produced as a single JSON snippet (§2.5).** Round 2 had the warning token entry in §2.3 and focus-ring tokens scattered in §2.2. Round 3 consolidates every new token into one apply-ready JSON snippet targeting `/Users/scottdurgan/dev/crane-console/packages/tokens/src/ventures/ss.json`.

3. **Consolidated `global.css` diff produced (§3.6).** Round 2 spread required additions across §3.2, §3.3, §3.4, and §3.5. Round 3 produces a single ordered block of paste-ready CSS enumerating every change required: motion `@theme inline`, new color `@theme inline`, `:focus-visible` rule, `prefers-reduced-motion` block. Each section is labeled for precise insertion point.

4. **Component inventory expanded and cross-referenced with file paths, TypeScript props interfaces, ARIA, states, and test surface.** Round 2 had a summary table and four sub-specs. Round 3 adds full specs for every "Exists (needs update)" and "New" component, not just the four highest-priority ones. See §1.

5. **Email rendering pipeline finalized.** Round 2 proposed the `EMAIL_TOKENS` snapshot approach but deferred the build script. Round 3 specifies the exact build script command, location (`scripts/sync-email-tokens.mjs`), script structure, and invocation hook. See §5.

6. **Print stylesheet hardened with `@page` rule and data-attribute selectors.** Round 2's `@media print` block used class selectors. Round 3 switches to `data-*` attribute selectors for resilience against Tailwind class refactors, adds `@page` margins, and adds the `break-inside: avoid` pair. See §6.

7. **PDF generation library confirmed as `@react-pdf/renderer` (same as SOW).** Round 2 left this open. Round 3 closes it: Cloudflare Workers cannot run Chromium/Puppeteer, `@react-pdf/renderer` is already in use for SOW, and invoice PDFs must use the same library. See §7.

8. **CI quality gate matrix completed.** Round 2 had Performance Budget but no CI spec. Round 3 documents all required automated checks, gaps in current coverage (token compliance audit, WCAG automation), and exact commands. See §8.

9. **Performance budget revalidated with TTI and image notes.** TTI targets added: <3500ms (3G), <1800ms (4G). Image optimization rule confirmed as a no-op (no images in MVP). Full table in §9.

10. **Token compliance enforcement gap documented as follow-on Open Design Decision.** `ui-drift-audit` covers 6 of 7 UI-PATTERNS rules but does not catch raw hex values in source. Round 3 proposes a lint-level fix and files it as Open Design Decision #11. See §10.

11. **Material Symbols font loading confirmed and variable axis values specified.** Round 2 documented the `.material-symbols-outlined` class but did not confirm the `<link>` loading strategy. Round 3 confirms `<link rel="preload">` + `<link rel="stylesheet">` approach, specifies all variable axis defaults and active-state override. See §11.

12. **i18n preparatory note added (§12).** Not in scope for MVP; confirmed as a structural non-issue given Astro's component model. No concrete action required.

13. **Tab icon reconciliation.** Brand Strategist specified `timeline` for Progress; Interaction Designer specified `assignment`. Round 3 defers to Interaction Designer: `assignment` for Progress. Full canonical set in §1.1 and `PortalTabs.astro` spec.

14. **`--ss-color-text-muted-accessible` token added.** Brand Strategist Round 2 proposed `#6b6158` (4.71:1) as a hardened alias for mobile outdoor contexts. Round 3 includes this token in the JSON diff and CSS additions. See §2.

---

## Table of Contents

1. [Component Inventory — Final](#1-component-inventory--final)
2. [Token Architecture — Final](#2-token-architecture--final)
3. [CSS Strategy — Final Changes](#3-css-strategy--final-changes)
4. [Dark Mode](#4-dark-mode)
5. [Email Rendering Pipeline](#5-email-rendering-pipeline)
6. [Print Stylesheet for Invoice](#6-print-stylesheet-for-invoice)
7. [PDF Generation](#7-pdf-generation)
8. [CI Quality Gates](#8-ci-quality-gates)
9. [Performance Budget — Revalidated](#9-performance-budget--revalidated)
10. [Token Compliance Enforcement](#10-token-compliance-enforcement)
11. [Material Symbols Loading](#11-material-symbols-loading)
12. [i18n Preparation](#12-i18n-preparation)
13. [Open Design Decisions](#13-open-design-decisions)

---

## 1. Component Inventory — Final

Status classification: **Exists** = usable as-is | **Exists (needs update)** = file present, change required | **New** = does not exist.

### 1.1 Portal Components — Summary Table

| Name                         | Status                    | File path                                          | Change required                                                                              |
| ---------------------------- | ------------------------- | -------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `PortalHeader.astro`         | Exists                    | `src/components/portal/PortalHeader.astro`         | Add `data-portal-header` to root element for print stylesheet                                |
| `PortalTabs.astro`           | **Exists (needs update)** | `src/components/portal/PortalTabs.astro`           | Icon prop interface; updated tabs array; active-state icon FILL toggling                     |
| `PortalPageHead.astro`       | Exists                    | `src/components/portal/PortalPageHead.astro`       | None                                                                                         |
| `PortalListItem.astro`       | Exists                    | `src/components/portal/PortalListItem.astro`       | None                                                                                         |
| `StatusPill.astro`           | Exists                    | `src/components/portal/StatusPill.astro`           | None                                                                                         |
| `MoneyDisplay.astro`         | Exists                    | `src/components/portal/MoneyDisplay.astro`         | None                                                                                         |
| `ActionCard.astro`           | Exists                    | `src/components/portal/ActionCard.astro`           | Confirm `min-h-[44px]` on CTA (touch target audit)                                           |
| `ConsultantBlock.astro`      | Exists                    | `src/components/portal/ConsultantBlock.astro`      | Add `width={120} height={120}` attrs to `<img>` for CLS prevention                           |
| `EngagementProgress.astro`   | Exists                    | `src/components/portal/EngagementProgress.astro`   | Confirm timeline `<ol role="list">` for iOS VoiceOver                                        |
| `PortalHomeDashboard.astro`  | Exists                    | `src/components/portal/PortalHomeDashboard.astro`  | None                                                                                         |
| `QuoteDetail.astro`          | **Exists (needs update)** | `src/components/portal/QuoteDetail.astro`          | Pattern 01 violation line 207-210; Pattern 02 violation lines 458-497; Pattern 05 violations |
| `InvoiceDetail.astro`        | **Exists (needs update)** | `src/components/portal/InvoiceDetail.astro`        | Print stylesheet addition; Pattern 02 triple-redundancy at lines 450-461                     |
| `InvoicesList.astro`         | Exists                    | `src/components/portal/InvoicesList.astro`         | None                                                                                         |
| `QuoteList.astro`            | Exists                    | `src/components/portal/QuoteList.astro`            | None                                                                                         |
| `Documents.astro`            | Exists                    | `src/components/portal/Documents.astro`            | None                                                                                         |
| `TimelineEntry.astro`        | Exists                    | `src/components/portal/TimelineEntry.astro`        | None                                                                                         |
| `ArtifactChip.astro`         | Exists                    | `src/components/portal/ArtifactChip.astro`         | None                                                                                         |
| `SkipToMain.astro`           | Exists                    | `src/components/SkipToMain.astro`                  | Add `data-skip-link` to root element for print stylesheet                                    |
| `SigningView.astro`          | **New**                   | `src/components/portal/SigningView.astro`          | Full spec in §1.3                                                                            |
| `MagicLinkExpiredForm.astro` | **New**                   | `src/components/portal/MagicLinkExpiredForm.astro` | Full spec in §1.4                                                                            |
| `ParkingLotPanel.astro`      | **New**                   | `src/components/portal/ParkingLotPanel.astro`      | Full spec in §1.5                                                                            |

### 1.2 Admin Components — Summary Table

| Name                          | Status  | File path                                          | Change required                                   |
| ----------------------------- | ------- | -------------------------------------------------- | ------------------------------------------------- |
| `EnrichmentStatusPanel.astro` | Exists  | `src/components/admin/EnrichmentStatusPanel.astro` | None                                              |
| `LogReplyDialog.astro`        | Exists  | `src/components/admin/LogReplyDialog.astro`        | None                                              |
| `PipelineKanban.astro`        | **New** | `src/components/admin/PipelineKanban.astro`        | Full spec in §1.6                                 |
| `ClientCard.astro`            | **New** | `src/components/admin/ClientCard.astro`            | Full spec in §1.7                                 |
| `QuoteLineItemEditor.astro`   | **New** | `src/components/admin/QuoteLineItemEditor.astro`   | React island; admin-route-only; full spec in §1.8 |
| `SOWPreviewPane.astro`        | **New** | `src/components/admin/SOWPreviewPane.astro`        | Full spec in §1.9                                 |
| `FollowUpCard.astro`          | **New** | `src/components/admin/FollowUpCard.astro`          | Full spec in §1.10                                |
| `TimeEntryLog.astro`          | **New** | `src/components/admin/TimeEntryLog.astro`          | Full spec in §1.11                                |
| `ExtractionPanel.astro`       | **New** | `src/components/admin/ExtractionPanel.astro`       | Full spec in §1.12                                |

### 1.3 Shared and Booking Components

| Name               | Status                    | File path                                 | Change required                                     |
| ------------------ | ------------------------- | ----------------------------------------- | --------------------------------------------------- |
| `SlotPicker.astro` | Exists                    | `src/components/booking/SlotPicker.astro` | None                                                |
| `CtaButton.astro`  | **Exists (needs update)** | `src/components/CtaButton.astro`          | Add `disabled` prop + ARIA; add destructive variant |

### 1.4 Email Templates

| Name                       | Status                    | Location                               | Change required                                          |
| -------------------------- | ------------------------- | -------------------------------------- | -------------------------------------------------------- |
| Magic link email           | **Exists (needs update)** | `src/lib/email/templates.ts`           | Replace blue/slate palette with `EMAIL_TOKENS` constants |
| Portal invitation email    | **Exists (needs update)** | `src/lib/email/templates.ts`           | Replace blue/slate palette with `EMAIL_TOKENS` constants |
| Booking confirmation email | **Exists (needs update)** | `src/lib/email/booking-emails.ts`      | Replace blue/slate palette with `EMAIL_TOKENS` constants |
| Follow-up email series     | **Exists (needs update)** | `src/lib/email/follow-up-templates.ts` | Replace blue/slate palette with `EMAIL_TOKENS` constants |

---

### 1.1 `PortalTabs.astro` Update Spec

**Required change summary:** Add icon support to mobile bar. Icons replace `§ 0N` anchors when `iconName` is present. Desktop remains label-only. Add `data-portal-tabs` to root `<nav>` element.

**Canonical tab set (final — reconciling Brand Strategist and Interaction Designer):**

| Route                | Label     | Icon (FILL 0)  | Icon active (FILL 1)    | Authority                                                              |
| -------------------- | --------- | -------------- | ----------------------- | ---------------------------------------------------------------------- |
| `/portal`            | Home      | `home`         | `home` (filled)         | Interaction Designer R2                                                |
| `/portal/quotes`     | Proposals | `description`  | `description` (filled)  | Interaction Designer R2 (preferred over Brand Strategist's `work`)     |
| `/portal/invoices`   | Invoices  | `receipt_long` | `receipt_long` (filled) | Both roles aligned                                                     |
| `/portal/engagement` | Progress  | `assignment`   | `assignment` (filled)   | Interaction Designer R2 (preferred over Brand Strategist's `timeline`) |
| `/portal/documents`  | Documents | `folder_open`  | `folder` (filled)       | Brand Strategist R2                                                    |

Note: Marcus (Target User R2) confirmed four tabs are sufficient for MVP. `Documents` is the fifth tab if post-launch frequency data justifies it. Current implementation: 4 tabs.

**Props interface:**

```ts
interface TabDef {
  href: string
  label: string
  anchor: string // shown desktop only when iconName is absent
  matchPrefix: string
  iconName?: string // Material Symbols Outlined name
}

interface Props {
  pathname: string
}
```

**Mobile icon rendering (add `data-portal-tabs` to root `<nav>`):**

```astro
---
// src/components/portal/PortalTabs.astro
// data-portal-tabs added to root <nav> for print stylesheet selector
---

<nav data-portal-tabs aria-label="Portal navigation">
  {/* ...tabs... */}
  {
    tab.iconName ? (
      <span
        class="material-symbols-outlined text-[20px] leading-none"
        aria-hidden="true"
        style={`font-variation-settings: 'FILL' ${isActive ? 1 : 0}, 'wght' 400, 'GRAD' 0, 'opsz' 20;`}
      >
        {tab.iconName}
      </span>
    ) : (
      <span aria-hidden="true" class="font-mono text-[11px] leading-none tracking-[0.06em]">
        § {tab.anchor}
      </span>
    )
  }
  <span class="font-accent-label text-label">{tab.label}</span>
</nav>
```

`opsz: 20` at 20px effective size. `FILL` toggles 0/1 based on active state. Icons are `aria-hidden="true"` — label text carries the accessible name. Active tab: `--ss-color-primary` background, `--ss-color-background` text.

**States:** active (FILL 1, primary bg) / inactive (FILL 0, muted label) / no icon fallback (§ anchor).

**Test surface:** `tests/portal/navigation.test.ts` (needs test — does not yet exist).

---

### 1.2 `PortalHeader.astro` Data Attribute

Add `data-portal-header` to the root element. This is the print stylesheet selector. No other change.

```astro
<header data-portal-header class="..."></header>
```

**Test surface:** None required for this change.

---

### 1.3 `SigningView.astro` Full Spec (New)

**File:** `src/components/portal/SigningView.astro`

**Props interface:**

```ts
interface Props {
  quoteId: string
  signWellEmbedUrl: string // server-validated before passing to template
  postSignRedirectHref: string // portal-side redirect after signing
  isSigned: boolean // renders confirmation panel if true
  signedPdfHref?: string | null // R2 presigned URL; null until webhook fires
  quote: {
    engagementTitle: string
    totalCents: number
    depositCents: number
    paymentSplitText: string // authored; e.g. "50% now, 50% at completion"
    deliverables: Array<{ title: string; body: string }>
  }
}
```

**Required ARIA attributes:**

| Element                     | Attribute   | Value                                                  |
| --------------------------- | ----------- | ------------------------------------------------------ |
| Outer iframe container      | `aria-busy` | `"true"` while loading; removed on iframe `load` event |
| `<iframe>`                  | `title`     | `"Sign proposal"`                                      |
| Post-sign panel             | `aria-live` | `"assertive"`                                          |
| Scope accordion `<summary>` | —           | Native `<details>/<summary>` provides disclosure role  |

**States:**

| State   | Trigger                                     | Renders                                                                                                                                                      |
| ------- | ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| loading | `isSigned === false`, iframe not yet loaded | `aria-busy="true"` container; visually centered "Loading signing document..." text; `<span role="status" aria-live="polite">Loading signing document</span>` |
| ready   | iframe `load` event fires                   | Remove `aria-busy`; iframe content visible                                                                                                                   |
| error   | `iframe.onerror` or 30s timeout             | Replace iframe with: "The signing document isn't available right now. Email us at {authored email} to sign via a direct link."                               |
| signed  | `isSigned === true`                         | Confirmation panel: `aria-live="assertive"`, focus shifted to confirmation heading                                                                           |

**Responsive layout:**

Mobile (`< md`): `<details>/<summary>` accordion above iframe (collapsed by default). Iframe height: `calc(100dvh - 7rem)` with `calc(100vh - 7rem)` fallback.

Desktop (`>= md`): Two-column. Scope summary sidebar (sticky, max-width 280px) left. Iframe (flex-1) right.

**Pre-signing disclosure line (mandatory, above iframe):**

```html
<p class="text-caption text-text-secondary">
  The signing form below is provided by SignWell, a document-signing service.
</p>
```

**Test surface:** `tests/portal/signing-view.test.ts` (needs test).

---

### 1.4 `MagicLinkExpiredForm.astro` Spec (New)

**File:** `src/components/portal/MagicLinkExpiredForm.astro`

**Props interface:**

```ts
interface Props {
  errorType: 'expired' | 'used' // both render the same message
  prefillEmail?: string // optional; pre-fills if available from URL context
}
```

**Required ARIA attributes:**

| Element                      | Attribute                            | Value                                       |
| ---------------------------- | ------------------------------------ | ------------------------------------------- |
| Email input                  | `aria-describedby`                   | ID of error/help text below input           |
| In-page success confirmation | `role="status"` `aria-live="polite"` | Announces "Check your email for a new link" |
| Email input                  | `aria-required`                      | `"true"`                                    |

**States:**

| State          | Renders                                                                |
| -------------- | ---------------------------------------------------------------------- |
| idle           | Label + email input + "Send new link" primary button                   |
| submitting     | Button disabled, label "Sending..."                                    |
| success        | In-page confirmation: "Check your email for a new link." Input hidden. |
| error (format) | Field-level error below input via `aria-describedby`                   |

**Test surface:** `tests/portal/magic-link-expired.test.ts` (needs test).

---

### 1.5 `ParkingLotPanel.astro` Spec (New)

**File:** `src/components/portal/ParkingLotPanel.astro`

**Props interface:**

```ts
interface ParkingLotItem {
  id: string
  description: string // authored plain language
  disposition: 'added' | 'deferred' | 'dropped' | null // null = undispositioned (admin only)
  dispositionNote?: string | null // authored explanation; required per Marcus R2
  requestedAt: Date
}

interface Props {
  items: ParkingLotItem[]
  readonly: boolean // true = portal client view (no disposition controls); false = admin editable
}
```

**Required ARIA attributes:**

| Element      | Attribute                                    | Value                 |
| ------------ | -------------------------------------------- | --------------------- |
| Panel region | `role="region"` `aria-label`                 | `"Parking lot items"` |
| Each item    | Rendered as `<li>` within `<ul role="list">` | —                     |

**States:**

| State                      | Trigger                                                | Renders                                                                         |
| -------------------------- | ------------------------------------------------------ | ------------------------------------------------------------------------------- |
| empty                      | `items.length === 0`                                   | Render nothing (empty-state-pattern)                                            |
| data (readonly)            | `readonly === true`                                    | Item description + disposition in plain English + `dispositionNote` if authored |
| data (admin editable)      | `readonly === false`                                   | Item + disposition dropdown + stale warning tag if >14 days undispositioned     |
| stale warning (admin only) | `disposition === null` AND `requestedAt` > 14 days ago | "STALE" mono-caps tag (`text-label`, Archivo Narrow, `--ss-color-warning`)      |

**Marcus's requirement:** Disposition must have a human explanation (`dispositionNote`), not just a status tag. If `dispositionNote` is null, render the disposition label only — do not fabricate an explanation. Do not render "Dropped" with no context for items Marcus will review months later.

**Test surface:** `tests/portal/parking-lot-panel.test.ts` (needs test).

---

### 1.6 `PipelineKanban.astro` Spec (New)

**File:** `src/components/admin/PipelineKanban.astro`

**Props interface:**

```ts
interface Props {
  columns: Array<{
    status: 'prospect' | 'assessed' | 'quoted' | 'active' | 'completed'
    label: string
    cards: PipelineCardData[]
  }>
}
```

**Required ARIA attributes:**

| Element     | Attribute                                      |
| ----------- | ---------------------------------------------- |
| Each column | `role="region"` `aria-label="{status} column"` |
| Each card   | `<article>`                                    |

**States:** loading (skeleton at column level) / empty column (render empty column — not hidden) / data.

**Overdue state:** Rendered as visible text label "OVERDUE" on the card — not color-only. Color and text together. See `ClientCard.astro` spec.

**Test surface:** `tests/admin/pipeline-kanban.test.ts` (needs test).

---

### 1.7 `ClientCard.astro` Spec (New)

**File:** `src/components/admin/ClientCard.astro`

**Props interface:**

```ts
interface Props {
  entityId: string
  businessName: string
  vertical: string
  daysInStatus: number
  nextAction?: string | null // authored; render nothing if null
  isOverdue: boolean
  href: string // link to /admin/entities/[id]
}
```

**Required ARIA attributes:** Card is wrapped in `<a href={href}>`. `isOverdue` renders as visible text "OVERDUE" (not color-only) within the card. `aria-label` on the anchor if the visible text is ambiguous.

**States:** default / overdue (visible "OVERDUE" text label in `--ss-color-error`, not warning — overdue pipeline cards are error-severity in admin context).

**Test surface:** `tests/admin/client-card.test.ts` (needs test).

---

### 1.8 `QuoteLineItemEditor.astro` Spec (New — React island)

**File:** `src/components/admin/QuoteLineItemEditor.tsx` (React, not `.astro`)

**Note:** This is the only React island in the MVP. Loaded with `client:load` on `/admin/entities/[id]/quotes/[quoteId]` only. Must not appear on any portal route.

**Props interface:**

```ts
interface LineItem {
  id: string
  description: string
  hours: number
  unitCents: number // rate × 100
}

interface Props {
  initialItems: LineItem[]
  hourlyRateCents: number // from engagement config
  onChange: (items: LineItem[]) => void
}
```

**Required ARIA attributes:**

| Element                | Attribute            | Value                                              |
| ---------------------- | -------------------- | -------------------------------------------------- |
| Each description input | `aria-label`         | `"Line item {n} description"`                      |
| Each hours input       | `aria-label`         | `"Line item {n} hours"`                            |
| Add-row button         | —                    | `<button type="button">` with text "Add line item" |
| Delete-row button      | `aria-label`         | `"Remove line item {n}"`                           |
| Row total              | `aria-live="polite"` | Announces recalculated total on hours change       |

**States:** empty (one blank row by default) / data (rows populated) / error (hours input: non-numeric entry shows field-level error).

**Touch target:** Delete and add buttons: `min-h-[44px] min-w-[44px]` even when visually icon-only.

**Keyboard:** Tab between description → hours → next row. Enter on hours adds new row. Backspace/Delete on empty hours field removes row.

**Test surface:** `tests/admin/quote-line-item-editor.test.ts` (needs test).

---

### 1.9 `SOWPreviewPane.astro` Spec (New)

**File:** `src/components/admin/SOWPreviewPane.astro`

**Props interface:**

```ts
interface Props {
  sowData: {
    engagementTitle: string
    clientName: string
    deliverables: Array<{ title: string; description: string }>
    totalCents: number
    paymentTermsText: string // authored
  } | null
}
```

**Required ARIA attributes:** `role="region"` `aria-label="SOW preview"`.

**States:** empty (`sowData === null` — render "SOW preview will appear here when line items are added") / data (rendered preview matching PDF output).

**Test surface:** `tests/admin/sow-preview-pane.test.ts` (needs test).

---

### 1.10 `FollowUpCard.astro` Spec (New)

**File:** `src/components/admin/FollowUpCard.astro`

**Props interface:**

```ts
interface Props {
  followUpId: string
  clientName: string
  followUpType: 'proposal' | 'invoice' | 'check-in' | 'parking-lot'
  dueDate: Date
  urgency: 'overdue' | 'due-today' | 'upcoming'
  href: string
}
```

**Required ARIA attributes:** Action buttons minimum 44px touch target. Urgency conveyed by text label, not color-only ("OVERDUE", "DUE TODAY" as visible text in appropriate color).

**States:** overdue / due-today / upcoming — each renders a visible text label in corresponding semantic color (`--ss-color-error` for overdue, `--ss-color-warning` for due-today, `--ss-color-text-secondary` for upcoming).

**Test surface:** `tests/admin/follow-up-card.test.ts` (needs test).

---

### 1.11 `TimeEntryLog.astro` Spec (New)

**File:** `src/components/admin/TimeEntryLog.astro`

**Props interface:**

```ts
interface TimeEntry {
  id: string
  date: Date
  description: string
  hours: number
  billingStatus: 'billable' | 'internal'
}

interface Props {
  entries: TimeEntry[]
  totalHours: number
}
```

**Required ARIA attributes:** `<table>` with `<thead>` and column headers. `scope="col"` on each `<th>`. `<caption>` with "Time entries" for screen readers.

**States:** empty (`entries.length === 0` — render "No time entries yet." plain text, no illustration) / data.

**Test surface:** `tests/admin/time-entry-log.test.ts` (needs test).

---

### 1.12 `ExtractionPanel.astro` Spec (New)

**File:** `src/components/admin/ExtractionPanel.astro`

**Props interface:**

```ts
interface ExtractionResult {
  problemId: string // internal enum key
  problemLabel: string // human-readable label
  confidence: 'high' | 'medium' | 'low'
  evidence: string // quoted or paraphrased from transcript
}

interface Props {
  results: ExtractionResult[]
  extractionStatus: 'pending' | 'running' | 'complete' | 'error'
  errorMessage?: string | null
}
```

**Required ARIA attributes:** `role="region"` `aria-label="Assessment extraction"`. Loading state: `aria-busy="true"` on region while status is `running`. Error state: `aria-live="assertive"` on error message.

**States:** pending (render "Extraction not started") / running (`aria-busy`, spinner) / complete (result list) / error (error message with `aria-live="assertive"`).

**Problem identifiers:** Render as `problemLabel` (human-readable), never as raw `problemId` enum keys. Internal identifiers are never client-visible and must not be visible to Scott in the admin UI either.

**Test surface:** `tests/admin/extraction-panel.test.ts` (needs test).

---

### 1.13 `CtaButton.astro` Update Spec

**File:** `src/components/CtaButton.astro`

**Required additions:**

```ts
interface Props {
  variant: 'primary' | 'secondary' | 'ghost' | 'destructive' // destructive is NEW
  disabled?: boolean // NEW
  type?: 'button' | 'submit' | 'reset' // default: 'button'
  href?: string // renders <a> if present
  ariaLabel?: string // for icon-only buttons
}
```

**Disabled state:**

```astro
<button class={/* ... */} disabled={disabled} aria-disabled={disabled ? 'true' : undefined}
></button>
```

Both `disabled` (native) and `aria-disabled` (for cases where a button must remain focusable) are set. Use only `disabled` for true form submission prevention. Use `aria-disabled` without `disabled` if the button must remain in tab order for progressive disclosure patterns.

**Destructive variant:** `--ss-color-error` background, white text. `min-h-[44px]`. Never used as a primary CTA — only for destructive confirmations (cancel engagement, delete draft quote).

**Test surface:** `tests/components/cta-button.test.ts` (needs test).

---

## 2. Token Architecture — Final

### 2.1 Naming Convention

All tokens use the `--ss-{category}-{variant}` prefix. Source of truth chain:

```
crane-console/packages/tokens/src/ventures/ss.json  (W3C-DTCG)
  → node_modules/@venturecrane/tokens/dist/ss.css    (compiled --ss-*)
  → src/styles/global.css @theme inline              (Tailwind v4 utilities)
```

No naming convention changes.

### 2.2 Warning Token — Final Reconciliation

**Decision: adopt Brand Strategist's `#7a5800` over Round 2's `#6b4f08`.**

| Candidate           | Hex       | Contrast on cream | WCAG AA normal | Notes                                                                             |
| ------------------- | --------- | ----------------- | -------------- | --------------------------------------------------------------------------------- |
| Design Tech R2      | `#6b4f08` | 6.72:1            | Pass           | AA-safe; slightly closer to olive in low light                                    |
| Brand Strategist R2 | `#7a5800` | 7.14:1            | Pass (AAA)     | Warmer amber; visually distinct from olive; Phoenix sun legibility argument holds |

**Rationale for `#7a5800`:** The 0.42:1 contrast difference is marginal for the AA threshold, but `#7a5800` is a warmer, more yellow-amber that reads as distinct from olive (`#4a6b3e`, cooler green-brown) in low-light conditions. Brand Strategist's mapping of portal contexts (near-expiry quote, overdue deposit) confirms the need for visual distinction from both burnt orange (action) and olive (success). `#7a5800` achieves that distinction. The "slightly warmer in Phoenix sun" argument is secondary — both exceed AA by comfortable margin — but the hue distinctiveness argument is concrete.

**All subsequent references in this document use `#7a5800`.**

**Contrast table for `#7a5800`:**

| Pairing                         | Ratio  | WCAG AA Normal | WCAG AA Large/UI | Notes                                                                       |
| ------------------------------- | ------ | -------------- | ---------------- | --------------------------------------------------------------------------- |
| `#7a5800` on cream (`#f5f0e3`)  | 7.14:1 | Pass           | Pass (AAA)       | Warning text on cream at any size                                           |
| White on `#7a5800`              | 7.14:1 | Pass           | Pass             | White label on warning background (Brand Strategist confirmed this pairing) |
| Ink (`#1a1512`) on `#7a5800` bg | ~15:1  | Pass           | Pass             | Alternative if white-on-warning is not used                                 |

### 2.3 Complete New Token Set — Final

All existing tokens remain unchanged. New tokens added in these rounds:

```css
/* NEW — Round 2/3 addition */
--ss-color-warning: #7a5800; /* Deep golden amber. Warning, near-expiry, soft-caution.
                                               7.14:1 on cream (AAA). Distinct from primary (#c5501e)
                                               and complete (#4a6b3e) in portal warning contexts. */

/* NEW — Round 2/3 addition (Brand Strategist R2 proposal) */
--ss-color-text-muted-accessible: #6b6158; /* Hardened alias for mobile outdoor contexts.
                                               4.71:1 on cream — AA pass at normal text.
                                               Use instead of --ss-color-text-muted when metadata
                                               must be legible outdoors (Marcus truck-cab context). */

/* NEW — Round 2/3 addition */
--ss-focus-ring-width: 2px; /* Focus ring stroke. :focus-visible rule only. */
--ss-focus-ring-offset: 2px; /* Focus ring offset. :focus-visible rule only. */
```

### 2.4 Usage Rules

| Token                              | Permitted                                                                                                                       | Prohibited                                                                          |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `--ss-color-warning`               | Warning callout text, overdue chip background (with white text), near-expiry prose, admin stale tags                            | CTAs (use primary), success (use complete), errors (use error)                      |
| `--ss-color-text-muted-accessible` | Mobile portal metadata that must be read outdoors: timestamps in critical context, secondary labels adjacent to actionable data | Body copy (use text-secondary), decorative-only metadata (text-muted is sufficient) |
| `--ss-focus-ring-width`            | `:focus-visible` rule only                                                                                                      | Border widths in layout                                                             |
| `--ss-focus-ring-offset`           | `:focus-visible` rule only                                                                                                      | Padding or gap values                                                               |

### 2.5 Token Package Diff — Apply-Ready JSON Snippet

Apply to `/Users/scottdurgan/dev/crane-console/packages/tokens/src/ventures/ss.json` under the existing `color` object and a new `focus` object:

```json
{
  "color": {
    "warning": {
      "$value": "#7a5800",
      "$type": "color",
      "$description": "Deep golden amber. Warning and near-expiry states. 7.14:1 contrast on cream (#f5f0e3) — WCAG AAA pass. Distinct from --ss-color-primary (#c5501e) and --ss-color-complete (#4a6b3e) for simultaneous portal display."
    },
    "text": {
      "muted-accessible": {
        "$value": "#6b6158",
        "$type": "color",
        "$description": "Hardened muted alias for mobile outdoor contexts. 4.71:1 on cream — WCAG AA normal text pass. Use when --ss-color-text-muted (3.0:1) is insufficient for outdoor legibility (Marcus HVAC, direct sunlight, Phoenix)."
      }
    }
  },
  "focus": {
    "ring-width": {
      "$value": "2px",
      "$type": "dimension",
      "$description": "Focus ring stroke width. Consumed only by the :focus-visible base rule in global.css."
    },
    "ring-offset": {
      "$value": "2px",
      "$type": "dimension",
      "$description": "Focus ring offset from element edge. Creates visible gap against cream background."
    }
  }
}
```

**Compiled output** (Style Dictionary v4 emits to `dist/ss.css`):

```css
:root {
  /* New tokens from rounds 2-3 */
  --ss-color-warning: #7a5800;
  --ss-color-text-muted-accessible: #6b6158;
  --ss-focus-ring-width: 2px;
  --ss-focus-ring-offset: 2px;
}
```

**Build command to recompile after JSON change:**

```bash
# From crane-console/ root
npm run build:tokens
# or
cd packages/tokens && npx style-dictionary build
```

---

## 3. CSS Strategy — Final Changes

### 3.1 Architecture (confirmed, no change)

Utility-first via Tailwind v4. `@layer base` for body defaults and accessibility rules. No component-scoped `<style>` blocks. Cards defined by border rules, not fill.

### 3.2 Complete `global.css` Change List

The following is the consolidated, ordered set of changes required to `src/styles/global.css`. Each block is labeled with its insertion point.

**Change 1 — Add new color tokens to `@theme inline` (insert after `--color-error` line):**

```css
@theme inline {
  /* ... existing color roles ... */
  --color-error: var(--ss-color-error);

  /* Round 2/3 additions */
  --color-warning: var(--ss-color-warning);
  --color-text-muted-accessible: var(--ss-color-text-muted-accessible);
}
```

**Effect:** `text-warning`, `bg-warning`, `border-warning`, `text-text-muted-accessible` become valid Tailwind utilities.

**Change 2 — Add motion mappings to `@theme inline` (insert after `--radius-badge` line):**

```css
@theme inline {
  /* ... existing radius tokens ... */
  --radius-badge: var(--ss-radius-badge);

  /* ---------- Motion — Round 2/3 addition ---------- */
  --duration-instant: var(--ss-motion-duration-instant);
  --duration-fast: var(--ss-motion-duration-fast);
  --duration-base: var(--ss-motion-duration-base);
  --duration-slow: var(--ss-motion-duration-slow);
  --ease-standard: var(--ss-motion-easing-standard);
  --ease-decelerate: var(--ss-motion-easing-decelerate);
  --ease-accelerate: var(--ss-motion-easing-accelerate);
}
```

**Effect:** `transition-colors duration-fast ease-standard` resolves to `transition-colors 150ms cubic-bezier(0.4, 0.0, 0.2, 1)`. Existing bare `transition-colors` continue to work unchanged (Tailwind default 150ms still applies).

**Change 3 — Add focus-ring and reduced-motion to `@layer base` (insert after the existing `h1, h2, h3` rule):**

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

  /* Focus ring — canonical base rule. Component overrides must maintain
   * a visible indicator. Never suppress :focus-visible without an equivalent.
   * WCAG 2.1 SC 2.4.7 (Focus Visible, AA) and SC 2.4.11 (WCAG 2.2, AA).
   * Burnt orange (#c5501e) at 2px achieves 4.06:1 against cream — satisfies
   * the 3:1 focus appearance minimum. Fallback values ensure the rule fires
   * even if token compilation fails. */
  :focus-visible {
    outline: var(--ss-focus-ring-width, 2px) solid var(--ss-color-action);
    outline-offset: var(--ss-focus-ring-offset, 2px);
  }

  /* Reduced motion — WCAG 2.1 SC 2.3.3. Also brand-required: Plainspoken
   * restraint means motion never imposes itself on users who have asked for
   * none. 0.01ms not 0ms: avoids Safari state-transition rendering bug.
   * scroll-behavior: auto disables smooth scroll for vestibular safety. */
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

**Change 4 — Add touch target utility (insert after `@layer base` block, before `.material-symbols-outlined`):**

```css
/* Touch target minimum — WCAG 2.5.5 (44×44px).
 * Logical properties for i18n-aware layout. */
.touch-target {
  min-block-size: 44px;
  min-inline-size: 44px;
}
```

### 3.3 Final `@layer base` — Complete State

After all changes, the `@layer base` block reads:

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

  :focus-visible {
    outline: var(--ss-focus-ring-width, 2px) solid var(--ss-color-action);
    outline-offset: var(--ss-focus-ring-offset, 2px);
  }

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

### 3.4 `UI-PATTERNS.md` Rule 6 Documentation Correction (Required)

`UI-PATTERNS.md` Rule 6 documents `space-section: 32px` and `space-card: 24px`. Compiled tokens are `--ss-space-section: 48px` and `--ss-space-card: 32px`. The documentation is wrong; the tokens are correct. Correct on next PR touching `UI-PATTERNS.md`. This correction has been noted across all three rounds and must not be deferred further.

---

## 4. Dark Mode

Light-only. Cream paper (`#f5f0e3`) is the identity, not a light-mode variant. No `dark:` Tailwind utilities in use. The `--ss-color-*` token layer would support dark mode by reassigning values under `@media (prefers-color-scheme: dark)` — this is architecturally possible but not planned. Post-Phase 5 consideration only.

---

## 5. Email Rendering Pipeline

### 5.1 Problem

Email clients do not support CSS custom properties. `var(--ss-color-warning)` is invalid in email HTML. Tokens must be inlined as literal hex values. Current templates at `src/lib/email/templates.ts` and related files use a non-compliant blue/slate palette.

### 5.2 Solution Architecture

A build script reads the compiled token CSS, extracts hex values, and emits a TypeScript constants file. Email templates import constants, never CSS variables.

**Source:** `node_modules/@venturecrane/tokens/dist/ss.css`
**Output:** `src/lib/email/tokens.ts`
**Script location:** `scripts/sync-email-tokens.mjs`

### 5.3 Build Script Spec

**Command:**

```bash
node scripts/sync-email-tokens.mjs
```

**Hook — run as part of token build:**

Add to `package.json` scripts:

```json
{
  "scripts": {
    "build:tokens": "cd ../crane-console/packages/tokens && npx style-dictionary build && cd - && node scripts/sync-email-tokens.mjs"
  }
}
```

Or as a postinstall step if the token package is consumed from npm.

**Script structure (`scripts/sync-email-tokens.mjs`):**

```js
// scripts/sync-email-tokens.mjs
// Reads compiled SS token CSS and emits hex constants for email templates.
// Email clients cannot resolve CSS custom properties — this snapshot is required.
// Run via: node scripts/sync-email-tokens.mjs
// Or: npm run build:tokens (includes this as a post-step)

import { readFileSync, writeFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const tokensPath = resolve(__dirname, '../node_modules/@venturecrane/tokens/dist/ss.css')
const outputPath = resolve(__dirname, '../src/lib/email/tokens.ts')

const css = readFileSync(tokensPath, 'utf-8')

function extract(varName) {
  const match = css.match(new RegExp(`${varName}:\\s*([^;]+);`))
  if (!match) throw new Error(`Token not found: ${varName}`)
  return match[1].trim()
}

const tokens = {
  colorBackground: extract('--ss-color-background'),
  colorSurface: extract('--ss-color-surface'),
  colorTextPrimary: extract('--ss-color-text-primary'),
  colorTextSecondary: extract('--ss-color-text-secondary'),
  colorTextMuted: extract('--ss-color-text-muted'),
  colorPrimary: extract('--ss-color-primary'),
  colorComplete: extract('--ss-color-complete'),
  colorError: extract('--ss-color-error'),
  colorWarning: extract('--ss-color-warning'),
  colorBorder: '#d2cec6', // rgba(26,21,18,0.16) approximated for email
  fontStackSans: 'Archivo, Arial, Helvetica, sans-serif',
  fontStackMono: '"JetBrains Mono", "Courier New", Courier, monospace',
}

const output = `// src/lib/email/tokens.ts
// AUTO-GENERATED by scripts/sync-email-tokens.mjs
// DO NOT EDIT BY HAND — run \`node scripts/sync-email-tokens.mjs\` to regenerate.
// Source: node_modules/@venturecrane/tokens/dist/ss.css
// Email clients cannot resolve CSS custom properties; use these constants only.

export const EMAIL_TOKENS = {
${Object.entries(tokens)
  .map(([k, v]) => `  ${k}: ${JSON.stringify(v)},`)
  .join('\n')}
} as const

export type EmailTokenKey = keyof typeof EMAIL_TOKENS
`

writeFileSync(outputPath, output, 'utf-8')
console.log('[sync-email-tokens] Written to', outputPath)
```

### 5.4 Final `EMAIL_TOKENS` Constants File

After running the script against the compiled package (with `#7a5800` warning token):

```ts
// src/lib/email/tokens.ts — expected output
export const EMAIL_TOKENS = {
  colorBackground: '#f5f0e3',
  colorSurface: '#f5f0e3',
  colorTextPrimary: '#1a1512',
  colorTextSecondary: '#4a423c',
  colorTextMuted: '#8a7f73',
  colorPrimary: '#c5501e',
  colorComplete: '#4a6b3e',
  colorError: '#a02a2a',
  colorWarning: '#7a5800', // Final value — Brand Strategist R2 recommendation
  colorBorder: '#d2cec6', // rgba approximation for email context
  fontStackSans: 'Archivo, Arial, Helvetica, sans-serif',
  fontStackMono: '"JetBrains Mono", "Courier New", Courier, monospace',
} as const
```

### 5.5 Email Template Remediation

Current templates use blue (`#1e40af`) and slate grays — entirely disconnected from the Plainspoken Sign Shop identity. Remediation priority: **P1** (not P0 — functional but brand-inconsistent). Must be complete before the first real client portal invitation is sent.

Templates requiring remediation:

- `src/lib/email/templates.ts` (magic link, portal invitation)
- `src/lib/email/booking-emails.ts` (booking confirmation)
- `src/lib/email/follow-up-templates.ts` (follow-up series)

---

## 6. Print Stylesheet for Invoice

### 6.1 Print Block

Add to `InvoiceDetail.astro`'s `<style>` block, or to a dedicated `src/styles/print.css` imported in the portal layout. Data attributes are used for all portal chrome selectors — resilient against Tailwind class refactors.

**Data attributes required (add to root elements of listed components):**

- `PortalHeader.astro` root: `data-portal-header`
- `PortalTabs.astro` root `<nav>`: `data-portal-tabs`
- `SkipToMain.astro` root: `data-skip-link`

```css
/* Invoice print stylesheet
 * Selector strategy: data-* attributes, not class names.
 * Class names are refactored with Tailwind; data attributes are structural. */

@page {
  size: letter portrait;
  margin: 0.75in 1in; /* standard professional invoice margins */
}

@media print {
  /* ---- Hide portal chrome ---- */
  [data-portal-header],
  [data-portal-tabs],
  [data-skip-link],
  [data-consultant-block],
  [data-action-bar] {
    display: none !important;
  }

  /* ---- Reset surface colors ----
   * Cream (#f5f0e3) does not print true across all printers.
   * White is the correct invoice paper background in print context. */
  html,
  body,
  main,
  [data-invoice-surface] {
    background-color: #ffffff !important;
    color: #000000 !important;
  }

  /* ---- Typography in print ----
   * Ink (#1a1512) maps to near-black for print.
   * Maintain tabular-nums for amount columns. */
  [data-invoice-amount],
  [data-invoice-reference] {
    color: #000000 !important;
    font-variant-numeric: tabular-nums;
  }

  /* ---- Page break discipline ---- */
  [data-invoice-card] {
    page-break-inside: avoid;
    break-inside: avoid;
  }

  [data-invoice-line-items] {
    page-break-inside: avoid;
    break-inside: avoid;
  }

  /* ---- Remove interactive affordances ---- */
  [data-pay-button],
  [data-download-button],
  [data-print-button] {
    display: none !important;
  }

  /* Suppress URL printing after links — cleaner invoice document */
  a[href]::after {
    content: none !important;
  }

  /* ---- Print-only elements ----
   * Use class="print-only hidden" (Tailwind: hidden; print: override)
   * for elements like firm address blocks that are not in the screen layout. */
  .print-only {
    display: block !important;
  }
}
```

### 6.2 Data Attribute Additions Required in Components

| Component               | Element                         | Attribute to add          |
| ----------------------- | ------------------------------- | ------------------------- |
| `PortalHeader.astro`    | Root `<header>`                 | `data-portal-header`      |
| `PortalTabs.astro`      | Root `<nav>`                    | `data-portal-tabs`        |
| `SkipToMain.astro`      | Root element                    | `data-skip-link`          |
| `InvoiceDetail.astro`   | Invoice surface container       | `data-invoice-surface`    |
| `InvoiceDetail.astro`   | Invoice card wrapper            | `data-invoice-card`       |
| `InvoiceDetail.astro`   | Line items `<table>` or wrapper | `data-invoice-line-items` |
| `InvoiceDetail.astro`   | Amount display elements         | `data-invoice-amount`     |
| `InvoiceDetail.astro`   | Reference number element        | `data-invoice-reference`  |
| `InvoiceDetail.astro`   | Pay Now button                  | `data-pay-button`         |
| `InvoiceDetail.astro`   | Download PDF button             | `data-download-button`    |
| `InvoiceDetail.astro`   | Print button                    | `data-print-button`       |
| `ConsultantBlock.astro` | Root element                    | `data-consultant-block`   |

---

## 7. PDF Generation

### 7.1 Library Decision: `@react-pdf/renderer`

**Confirmed.** The PRD's SOW PDF generation at `src/lib/pdf/sow-template.tsx` already uses `@react-pdf/renderer`. Invoice PDFs must use the same library.

**Why not alternatives:**

| Candidate                | Status        | Reason ruled out                                                                                                                                      |
| ------------------------ | ------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `puppeteer` / `chromium` | Ruled out     | Cloudflare Workers cannot run Chromium. No headless browser available in Worker runtime.                                                              |
| `pdf-lib`                | Ruled out     | Low-level PDF generation; requires building layout from scratch. `@react-pdf/renderer` provides React component abstraction already used in codebase. |
| `jsPDF`                  | Ruled out     | Browser-oriented; not ideal for server-side Worker generation.                                                                                        |
| `@react-pdf/renderer`    | **Confirmed** | Already in use for SOW. Single library = single dependency = single rendering model.                                                                  |

### 7.2 Invoice PDF Component Location

```
src/lib/pdf/invoice-template.tsx
```

Mirrors the existing `src/lib/pdf/sow-template.tsx` pattern.

### 7.3 Performance Budget

Invoice PDF generation endpoint must respond in **<800ms p95**. Same bound as SOW PDFs. This is a server-side budget enforced by monitoring, not a client metric.

If PDF generation approaches the 800ms limit under load, the mitigation path is:

1. Async generation with a Cloudflare Queue job
2. Polling endpoint (`GET /api/portal/invoices/[id]/pdf-status`)
3. On complete: redirect to R2 presigned URL

Do not block the client on synchronous PDF generation if p95 exceeds 800ms in production load testing.

### 7.4 R2 Storage

Generated PDFs are stored in R2 and served via presigned URLs. The generated URL is written to `invoices.pdf_url` on completion. `InvoiceDetail.astro` gates the "Download PDF" button on `invoice.pdf_url !== null`.

---

## 8. CI Quality Gates

### 8.1 Required Automated Checks

| Check                  | Command                                    | Scope                             | Current status                    |
| ---------------------- | ------------------------------------------ | --------------------------------- | --------------------------------- |
| TypeScript validation  | `npm run typecheck` (runs `astro check`)   | All `.astro`, `.ts`, `.tsx` files | Active                            |
| Lint                   | `npm run lint`                             | ESLint + Prettier                 | Active                            |
| Unit/integration tests | `npm run test` (Vitest)                    | `tests/**/*.test.ts`              | Active                            |
| E2E tests              | `npm run test:e2e` (Playwright)            | `tests/e2e/**`                    | Needs suite definition (see §8.2) |
| UI drift audit         | `.agents/skills/ui-drift-audit/`           | 6 of 7 UI-PATTERNS rules          | Active; gap documented in §10     |
| WCAG accessibility     | `@axe-core/playwright` in Playwright suite | Portal key screens                | Partially active; see §8.3        |
| Token compliance       | No dedicated gate currently                | Raw hex in source                 | Gap; see §10                      |
| Semgrep security gate  | `.github/workflows/semgrep.yml` (PR #575)  | Security patterns                 | Active                            |

### 8.2 Playwright E2E Suite — Required Coverage

The following suites must exist or be created before Phase 2 ships:

| Suite             | File                                      | Key scenarios                                                    |
| ----------------- | ----------------------------------------- | ---------------------------------------------------------------- |
| Portal navigation | `tests/e2e/portal/navigation.spec.ts`     | Tab visibility gating; persistent tabs visible; back links       |
| Magic link flow   | `tests/e2e/portal/magic-link.spec.ts`     | Valid token → redirect; expired token → recovery form            |
| Quote detail      | `tests/e2e/portal/quote-detail.spec.ts`   | All 5 states; Pattern 01 eyebrow; Pattern 02 no redundancy       |
| Signing view      | `tests/e2e/portal/signing-view.spec.ts`   | Portal chrome visible; pre-signing disclosure line; iframe loads |
| Invoice detail    | `tests/e2e/portal/invoice-detail.spec.ts` | Professional invoice format; print trigger; PDF gate             |
| Admin pipeline    | `tests/e2e/admin/pipeline.spec.ts`        | Kanban columns visible; overdue text label present               |

### 8.3 WCAG Automated Coverage

Use `@axe-core/playwright` to run axe-core against portal key screens in the E2E suite:

```ts
// In each portal Playwright spec:
import { checkA11y } from 'axe-playwright'

test('portal dashboard passes axe-core', async ({ page }) => {
  await page.goto('/portal')
  await checkA11y(page, undefined, {
    detailedReport: true,
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa'] },
  })
})
```

**Limitation:** Automated axe-core catches ~30–40% of WCAG issues. Color contrast, touch target sizing, and focus management require manual review. Schedule manual WCAG audit before Phase 2 launch.

**Manual audit priority:** Focus ring visibility at all interactive elements; `text-muted` usage audit confirming no essential data uses the 3.0:1 token; `SigningView.astro` iframe keyboard focus (Tab exit verification).

### 8.4 Merge Gate Summary

| Gate                        | Blocks merge                   | Tool                                        |
| --------------------------- | ------------------------------ | ------------------------------------------- |
| `typecheck`                 | Yes                            | GitHub Actions                              |
| `lint`                      | Yes                            | GitHub Actions                              |
| Unit tests (`npm run test`) | Yes                            | GitHub Actions                              |
| Semgrep security            | Yes                            | `.github/workflows/semgrep.yml`             |
| Scope-deferred TODO         | Yes                            | `.github/workflows/scope-deferred-todo.yml` |
| Unmet AC on close           | Yes (reopens)                  | `.github/workflows/unmet-ac-on-close.yml`   |
| E2E (Playwright)            | Yes (once suite exists)        | GitHub Actions — needs setup                |
| UI drift audit              | Manual PR check                | Skill output in PR description              |
| WCAG automated              | Advisory (not blocking in MVP) | `@axe-core/playwright` in Playwright        |

---

## 9. Performance Budget — Revalidated

### 9.1 Core Web Vitals

| Metric | Slow 3G target | 4G target     | Change from Round 2     |
| ------ | -------------- | ------------- | ----------------------- |
| FCP    | 1,500ms        | 800ms         | Unchanged               |
| LCP    | 2,500ms        | 1,200ms       | Unchanged               |
| CLS    | < 0.1          | < 0.1         | Unchanged               |
| INP    | < 500ms        | < 200ms       | Unchanged               |
| TTI    | **< 3,500ms**  | **< 1,800ms** | **New — added Round 3** |

**TTI rationale:** Time to Interactive is the metric most relevant to Marcus's use context — he needs the "Review and sign" CTA to be interactive within 3.5s on 3G (common in Phoenix suburban areas with variable signal). Portal JS bundle is 0KB (Astro HTML-first), so TTI should closely track FCP in practice. This budget formalizes the expectation and gives CI a check point.

### 9.2 Asset Budgets

| Asset                      | Budget (gzipped)                        | Change from Round 2         |
| -------------------------- | --------------------------------------- | --------------------------- |
| Total CSS bundle           | < 30KB                                  | Unchanged                   |
| Initial JS — portal routes | **0KB**                                 | Unchanged                   |
| Initial JS — admin routes  | **≈ 50KB** (QuoteLineItemEditor island) | Unchanged                   |
| Font payload (WOFF2)       | < 200KB total                           | Unchanged                   |
| Hero images                | None (no images in MVP)                 | Unchanged — confirmed below |
| Consultant photos          | < 40KB per photo at 120×120px           | Unchanged                   |
| SOW PDF response           | < 800ms p95                             | Unchanged                   |
| Invoice PDF response       | < 800ms p95                             | Confirmed same bound as SOW |

### 9.3 Image Optimization — No-Op Confirmation

**MVP has no images.** The Plainspoken Sign Shop identity explicitly rejects photography in product UI (Brand Strategist §6.3). Empty states render nothing or text — no illustrations. Consultant photos (120×120px, < 40KB) are the only images and are already budgeted. No image optimization pipeline is required for MVP.

This is a clean no-op, not a gap. If images are introduced post-MVP (e.g., productization with a brand mark, consultant photography beyond the avatar), revisit with a Cloudflare Image optimization strategy.

### 9.4 CLS Prevention

- `SigningView.astro` iframe container: `height: calc(100dvh - 7rem)` as a CSS rule (not inline) — browser allocates space before iframe loads.
- `ConsultantBlock.astro` photos: `width={120} height={120}` on `<img>`. This change is required and noted in the component inventory.
- `MoneyDisplay.astro`: server-rendered — no CLS risk.
- `PortalTabs.astro`: fixed 64px height tab bar — no layout shift on icon load (icons are font glyphs via Material Symbols, not image files).

### 9.5 Font Loading

Add to portal and admin layout `<head>` for Material Symbols (see also §11):

```html
<!-- Preconnect for Google Fonts (Material Symbols) -->
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
```

Reduces font load latency by ~100–200ms on mobile networks.

---

## 10. Token Compliance Enforcement

### 10.1 Current Gap

`ui-drift-audit` covers 6 of 7 UI-PATTERNS rules (patterns 01-06 plus the "actions and menus" pattern). It does NOT enforce raw color usage in source — e.g., a stray `#ffffff` hex or `rgb(255,255,255)` in a Tailwind utility or inline style is not caught.

**Why this matters:** Every time a developer writes `bg-[#f5f0e3]` instead of `bg-background`, or `color: #c5501e` instead of `color: var(--ss-color-primary)`, the token chain breaks. Design system changes to those tokens do not propagate to that element. The palette drifts silently.

### 10.2 Proposed Enforcement

**Option A — ESLint custom rule:** A custom ESLint rule that errors on raw hex values (`/#[0-9a-fA-F]{3,6}/`) in:

- `.astro` files (inline style attributes)
- `.ts` / `.tsx` files (string literals used in style contexts)
- Exceptions: hex values that exactly match a token value are allowed with a `// eslint-disable-next-line -- token: --ss-color-*` comment

**Option B — Tailwind plugin (simpler):** A Tailwind v4 plugin that errors when `bg-[#hex]` or `text-[#hex]` arbitrary values are used outside the token system. Tailwind's `allowedColors` config can enforce a whitelist.

**Recommendation: Option A (ESLint)** for broader coverage (catches inline styles and TypeScript string literals, not just Tailwind class strings). Option B covers Tailwind usage only.

**Filed as Open Design Decision #11.** This is a follow-on issue — not blocking for Phase 2, but should ship before Phase 3 to prevent drift accumulation during the admin build phase.

---

## 11. Material Symbols Loading

### 11.1 Font Face Loading Confirmation

The current `global.css` defines the `.material-symbols-outlined` class (verified at lines 150-168) but does not include the font `@font-face` declaration or a `<link>` tag. The font must be loaded via one of two mechanisms:

**Confirmed mechanism: `<link>` in layout `<head>`**

Add to all layout files (`src/layouts/PortalLayout.astro`, `src/layouts/AdminLayout.astro`, and any marketing layout that uses icons):

```html
<!-- Material Symbols — loaded via Google Fonts API -->
<!-- preload as font to reduce render-blocking -->
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link
  rel="stylesheet"
  href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200"
/>
```

The variable axis range `opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200` loads the full variable font, enabling all axis values used in the system.

**Alternative: self-hosted via `@font-face`** in `global.css`. This is preferable for privacy (no Google Fonts dependency) and performance (avoids third-party DNS). Self-hosting requires adding the font files to the repo and a `@font-face` declaration. This is the recommended approach for Phase 3 if bundle size allows.

### 11.2 Variable Axis Values — Final Spec

| Axis   | Default value  | Active state override                         | Context                                           |
| ------ | -------------- | --------------------------------------------- | ------------------------------------------------- |
| `FILL` | `0` (outlined) | `1` (filled)                                  | Active tab in `PortalTabs.astro`                  |
| `wght` | `400`          | `400` (unchanged)                             | Body context; heading-adjacent should use `600`   |
| `GRAD` | `0`            | `0`                                           | No grade adjustment; system-wide                  |
| `opsz` | `24`           | `20` for mobile tab bar (20px effective size) | `opsz: 20` produces correct stroke weight at 20px |

**Default `font-variation-settings` (already in `global.css`):**

```css
font-variation-settings:
  'FILL' 0,
  'wght' 400,
  'GRAD' 0,
  'opsz' 24;
```

**Active-state override (apply inline or via Astro style binding when `isActive`):**

```css
font-variation-settings:
  'FILL' 1,
  'wght' 400,
  'GRAD' 0,
  'opsz' 24;
```

**Mobile tab bar icon (20px effective):**

```css
font-variation-settings:
  'FILL' 0,
  'wght' 400,
  'GRAD' 0,
  'opsz' 20;
/* Active state: */
font-variation-settings:
  'FILL' 1,
  'wght' 400,
  'GRAD' 0,
  'opsz' 20;
```

### 11.3 Accessibility for Icons

- Decorative icons adjacent to text: `aria-hidden="true"`. The text label carries the accessible name.
- Icons as sole communicators (no adjacent text): require either `aria-label` on the button parent or a visually-hidden `<span>` with descriptive text. No icon-only affordances without an accessible name.

---

## 12. i18n Preparation

**Not in scope for MVP.** Marcus speaks English natively. No translation work is planned.

**Structural note for future readiness:** Astro's component model naturally supports i18n preparation. Component text is in template markup, not deeply nested in computed values or interpolated in ways that would block a future translation pass. The main structural concern is:

1. **No string concatenation for UI labels.** Labels like `"${status} — Due ${date}"` in TypeScript logic are harder to translate than slot-based or prop-based text. New components should accept label text as props where feasible.
2. **JetBrains Mono for IDs and reference numbers** is language-neutral — these values do not require translation.
3. **Archivo family** covers Latin character sets including Spanish diacritics (á, é, í, ó, ú, ñ, ü). No font change required for Spanish support.
4. **`dir="ltr"` is implicit** — no explicit directionality is set. For a future RTL language, this would require explicit `dir` management. LTR is the full scope of MVP and near-term expansion.

No concrete action required for MVP. This note satisfies the "structural confirmation" requirement without generating implementation work.

---

## 13. Open Design Decisions

The following items are unresolved and require a decision before the phase noted.

1. **Signing route — distinct URL vs. state within `quotes/[id]`.** PRD §9 specifies `/portal/quotes/[id]/sign` as a distinct route. Current implementation renders signing as a state. `SigningView.astro` is compatible with either approach. If deep-linking from the invitation email directly to the signing context is required (PRD §7 Step 5 implies it is), a distinct route is necessary. **Resolve before Phase 2 ships.**

2. **Tab count — 4 or 5 destinations.** Documents tab deferred to post-launch frequency analysis per Interaction Designer R2 and Marcus R2 confirmation (four tabs are enough at MVP). Documents reachable from dashboard row. **Resolve post-Phase 1 based on usage data.**

3. **Email template remediation timing.** `EMAIL_TOKENS` script is specified. Template remediation (replacing blue/slate palette) is P1. **Must complete before first real client portal invitation is sent.**

4. **`CtaButton.astro` `disabled` prop and destructive variant.** Required before Phase 3 admin destructive actions ship. **Phase 3 prerequisite.**

5. **SignWell iframe keyboard focus (Tab exit).** Must verify with SignWell embed API documentation that Tab can exit the iframe. If it cannot, a visible "Return to portal" button outside the iframe is required (UX-002). **Resolve before Phase 2 ships.**

6. **Material Symbols self-hosting vs. Google Fonts CDN.** Current spec uses Google Fonts CDN. Self-hosting preferred for privacy and performance. Requires adding font files to repo and `@font-face` in `global.css`. **Resolve before Phase 3 — Google Fonts CDN is acceptable for Phase 2.**

7. **`@axe-core/playwright` gate — advisory vs. blocking.** Currently advisory. Promoting to a blocking merge gate requires a clean baseline (zero existing violations across all key screens). Schedule baseline audit at Phase 2 launch. **Resolve at Phase 2 launch.**

8. **Invoice numbering scheme implementation.** Invoice detail requires a firm numbering scheme (`INV-2026-004`). This needs a system-level counter or config — not a raw UUID. Database schema and invoice creation flow must implement human-readable reference numbers before invoices are sent to real clients. **Phase 2 prerequisite (deposit invoice path).**

9. **`prefers-color-scheme: dark` — post-Phase 5.** No dark mode planned. Track as a post-Phase 5 architectural option. The `--ss-color-*` token layer is ready to support it without structural changes. **Post-Phase 5.**

10. **`UI-PATTERNS.md` Rule 6 documentation correction.** Rule 6 documents `space-section: 32px` and `space-card: 24px`. Live tokens are 48px and 32px. **Fix in next PR that touches `UI-PATTERNS.md`. Overdue — must not be deferred again.**

11. **Raw hex lint enforcement (token compliance).** `ui-drift-audit` does not catch stray `#hex` values in source. Propose ESLint custom rule or Tailwind plugin to error on raw hex values outside the token package. **File as a follow-on issue targeting Phase 3 before admin build phase begins.** This is the highest-priority gap not covered by existing tooling.

12. **`--ss-color-text-muted-accessible` formal review.** Brand Strategist proposed `#6b6158` as a hardened alias for outdoor mobile contexts. This document includes it in the token diff. If the Brand Strategist confirms this token in their Round 3 contribution, it is resolved. If not reconciled, defer to a follow-on issue. **Resolve by cross-referencing Brand Strategist Round 3.**

---

_SMD Services — Design Technologist Contribution, Design Brief Round 3 (Final)_
_Plainspoken Sign Shop identity. Three-round synthesis. Paint-job, not brochure._
_Token prefix: `--ss-_`. Stack: Astro 5.x, Cloudflare Workers + Static Assets, Tailwind v4, WCAG 2.1 AA.\*
