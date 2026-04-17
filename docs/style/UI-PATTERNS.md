# UI patterns spec for client-facing and admin surfaces

Six rules governing how visual and component semantics are authored across
Astro and React code. Each rule is narrow, cited to a public authority, and
documented with real anti-patterns from the audit at
[`.stitch/audits/ui-drift-2026-04-16.md`](../../.stitch/audits/ui-drift-2026-04-16.md).
Enforcement lands PR-by-PR as remediation ships (see "Enforcement" below).

This document exists because our Stitch pipeline had carte blanche on visual
and component design, and produced generic polished SaaS — pills adjacent to
identical prose, inconsistent typography scales, inline spacing drift. The
navigation drift problem was solved by `.stitch/NAVIGATION.md` (cited to
NN/g + Material 3 + HIG). The visual drift problem requires the same
discipline one layer up: named rules, cited authorities, no invented
"best practices."

It pairs with two existing specs:

- [`empty-state-pattern.md`](./empty-state-pattern.md) — content rule for missing authored data.
- [`.stitch/NAVIGATION.md`](../../.stitch/NAVIGATION.md) — IA + navigation patterns + chrome.

Together these govern the three drift surfaces: content, navigation, visual.

## Scope

These rules bind all authored UI code under `src/pages/**` and `src/components/**`.
Dev-preview pages (`src/pages/dev/*`) are documented by the audit but are
not remediation priorities — they exist to exercise primitives.

## Authority anchors

Every rule cites a public, URL-resolvable authority. If a rule cannot be
cited, it does not belong in this spec.

- **NN/g** — https://www.nngroup.com/articles/
- **Material Design 3** — https://m3.material.io/
- **WCAG 2.2** — https://www.w3.org/TR/WCAG22/
- **Shopify Polaris** — https://polaris.shopify.com/
- **IBM Carbon** — https://carbondesignsystem.com/
- **Apple HIG** — https://developer.apple.com/design/human-interface-guidelines/
- **Atlassian Design System** — https://atlassian.design/

---

## Rule 1 — Status display by context

**Rule.** A status signal uses one of four treatments by context. Pill, eyebrow, dot, or prose. Never interchanged.

| Context                        | Treatment                     | Purpose                                    |
| ------------------------------ | ----------------------------- | ------------------------------------------ |
| **List row, dense repeating**  | Pill                          | Scan-time discrimination across many items |
| **Category label above title** | Eyebrow (small-caps, muted)   | Document category or section kind          |
| **Single-item dashboard card** | Dot + label OR prose          | Glanceable state without visual weight     |
| **Detail-page headline**       | Prose in headline or subtitle | State IS the page identity                 |

**Authority.**

- Material 3: "Chips help people enter information … don't use chips as decoration." https://m3.material.io/components/chips/guidelines
- Shopify Polaris on badges: "Use badges to indicate the status of an object. Don't use badges as a substitute for normal text." https://polaris.shopify.com/components/feedback-indicators/badge
- NN/g: "Labels and tags are scan-time affordances, not decorative categorization." https://www.nngroup.com/articles/ui-labels/

**Anti-pattern — category eyebrow misused as pill.**

```html
<!-- src/pages/portal/quotes/[id].astro:207-210 -->
<span
  class="inline-flex items-center rounded-full bg-[color:var(--color-meta)]/10 px-3 py-1 text-[13px] leading-[18px] font-medium tracking-[0.01em] text-[color:var(--color-meta)]"
>
  Proposal
</span>
<h1 class="mt-5 ...">{engagementTitle}</h1>
```

"Proposal" is a document category above a title. It's an eyebrow job. Pill
treatment implies state that can change (Signed/Declined/Expired) but
"Proposal" never changes on this page. The same file uses a pill for
actual status 250 lines later — now the user can't tell what pills mean.

**Correct pattern — eyebrow for category, pill reserved for state.**

```html
<p class="text-[color:var(--color-meta)] text-label uppercase">Proposal</p>
<h1 class="mt-3 text-display font-bold text-[color:var(--color-text-primary)]">
  {engagementTitle}
</h1>
```

(`text-label` defined in `src/styles/global.css`; see Rule 5 / token reference.)

**Anti-pattern — detail-page status as pill on top of prose confirmation.**
See Rule 2. Detail pages own their state in prose.

**Detection.** `ui-drift-audit` Pills column + cross-reference with page archetype (list vs detail).

---

## Rule 2 — Redundancy ban: one signal per fact

**Rule.** A single fact gets one rendering. No pill adjacent to text that states the same thing. No triple-stacked confirmations.

**Authority.**

- Polaris: "Don't use badges as a substitute for normal text." (cited in Rule 1)
- Atlassian Design System on lozenges: "Lozenges are not labels for generic metadata." https://atlassian.design/components/lozenge/usage
- NN/g on redundancy: "Repetition of the same status in multiple visual treatments increases cognitive load without adding information." https://www.nngroup.com/articles/ui-copy/

**Anti-pattern — triple redundancy in invoice detail card.**

```html
<!-- src/pages/portal/invoices/[id].astro:450-461 -->
<span
  class="inline-flex items-center rounded-full bg-[color:var(--color-complete)]/10 ... text-[color:var(--color-complete)]"
>
  Paid
</span>
<div class="mt-5">
  <MoneyDisplay amountCents="{amountCents}" size="display" />
  <p class="mt-2 text-[color:var(--color-text-muted)]">Paid in full</p>
</div>
{paidShortDate && (
<p class="mt-6 text-[color:var(--color-text-muted)]">Paid {paidShortDate}.</p>
)}
```

"Paid" pill, "Paid in full" caption, "Paid {date}" confirmation. The fact is
one: this invoice is paid, on this date. Three renderings of it is noise.

**Correct pattern.**

```html
<MoneyDisplay amountCents="{amountCents}" size="display" />
<p class="mt-2 text-[color:var(--color-complete)] text-caption">Paid {paidShortDate}.</p>
```

Pill removed. "Paid in full" removed (amount display carries it). Single prose confirmation with the date. Complete color carries the semantic, not a bordered shape.

**Anti-pattern — "Signed" pill over "Signed {date}" block.**

```html
<!-- src/pages/portal/quotes/[id].astro:458-497 -->
<span class="inline-flex items-center rounded-full bg-[color:var(--color-meta)]/10 ...">
  {isSigned ? 'Signed' : isSuperseded ? 'Superseded' : 'other'}
</span>
... {isSigned ? (
<div class="mt-6 rounded-lg bg-[color:var(--color-complete)]/10 p-4">
  <p class="text-sm font-semibold">
    Signed{quote.accepted_at ? ` ${formatDate(quote.accepted_at)}` : ''}.
  </p>
</div>
) : ...}
```

The state pill and the "Signed {date}" confirmation block carry the same fact. The confirmation block is the richer, more informative rendering — the pill is redundant.

**Correct pattern.** Keep the confirmation block; drop the pill when the signed state is active. When the state is `Being prepared` / `Ready to sign` / other non-terminal values, prose at the top of the right-rail card carries it.

**Anti-pattern — "Paid" pill over "Paid: {date}" in list row.**

```html
<!-- src/pages/portal/invoices/index.astro:135-147 -->
<span
  class="inline-block px-2 py-0.5 rounded-full text-xs font-medium ${statusColorMap[inv.status]}"
>
  {INVOICE_STATUSES.find(...)?.label ?? inv.status}
</span>
...
<span class="text-green-600">Paid: {formatDate(inv.paid_at)}</span>
```

List rows are the Rule 1 pill-legitimate context — but not when the row
below repeats the same fact in prose. Fix: keep the pill (scan-time role
earns it here) and drop the `"Paid: "` prefix from the date line, or drop
the pill and keep just the prose. Pick one.

**Anti-pattern — status pill next to "Current Milestone: {status}" label.**

```html
<!-- src/pages/portal/engagement/index.astro:125-131, 144-148 -->
<span class="{`..." rounded-full ${CLIENT_STATUS_COLORS[engagement.status]}`}>
  {CLIENT_STATUS_LABELS[engagement.status]}
</span>
...
<div>
  <span class="text-slate-500">Current Milestone</span>
  <p class="font-medium text-slate-900">{CLIENT_STATUS_LABELS[engagement.status]}</p>
</div>
```

The label is the same value twice, rendered as a pill in one spot and as prose in another. The engagement status is the page's fact — pick prose (detail-page rule) and drop the pill.

**Detection.** `ui-drift-audit` Redundancy column. Tinted pill with status-keyword content echoed in ±10 lines of surrounding prose.

---

## Rule 3 — Button hierarchy: one primary per view

**Rule.** A surface has exactly one primary action visible at a time. Secondary, tertiary, and destructive actions have distinct visual treatments. A screen that needs two primaries is showing two tasks and should be split.

**Authority.**

- Material 3 actions: "Ensure there is only one primary button on each screen." https://m3.material.io/components/all-buttons
- Apple HIG button hierarchy: "Prefer one default action. Additional actions should be clearly subordinate." https://developer.apple.com/design/human-interface-guidelines/buttons

**Treatment spec.**

| Level                | Visual                                               | Usage                                          |
| -------------------- | ---------------------------------------------------- | ---------------------------------------------- |
| **Primary**          | Solid `bg-[color:var(--color-primary)]` + white text | The one action                                 |
| **Secondary**        | Border + primary text color                          | Alternative actions                            |
| **Tertiary (ghost)** | Text-only primary color                              | Low-stakes inline actions (links, "view more") |
| **Destructive**      | Solid `bg-[color:var(--color-error)]` + white text   | Irreversible or data-loss actions              |

**Anti-pattern — multiple primary actions rendered simultaneously.**

The audit counts `bg-primary` + button-shaped padding per file. A count > 1
is a signal to review, not a verdict: **state-branch conditional CTAs
(the same slot rendering Start / Continue / Submit depending on state)
are compliant.** Only co-rendered primaries on the same screen are
violations.

After the refined heuristic (ignoring tinted backgrounds, hover colors,
decorative progress bars and icon badges), remaining count-≥2 files:

- `src/pages/scorecard.astro` — 4. Verified state-branch: Start / Start
  (summary) / Next / Submit. Only one renders per assessment phase.
  Compliant.
- `src/pages/book.astro` — 2. Booking-flow state branches. Compliant.
- `src/pages/book/manage/[token].astro` — 2. Manage-booking state
  branches. Compliant.
- `src/components/booking/SlotPicker.astro` — 3. Slot-selection state
  (selected / available / confirm). Compliant.

No simultaneous-primary violations found. When one is introduced, it
will show up as a count-≥2 file with all primaries rendering in the
same top-level block (no `{condition ? <a/> : <b/>}` wrapping the second
primary). This pattern is what reviewers should look for.

**Detection.** `ui-drift-audit` Primary CTAs column + manual review of
any file with count ≥ 2.

---

## Rule 4 — Heading skip ban

**Rule.** Heading levels descend in steps. h1 → h2 → h3. No h1 → h3, no h2 → h4. Eyebrows are not headings. Visual size does not imply heading level.

**Authority.**

- WCAG 2.2 SC 1.3.1 (Info and Relationships, Level A): headings must convey document structure. https://www.w3.org/WAI/WCAG22/Understanding/info-and-relationships.html
- NN/g on heading hierarchy: "Screen readers and scanning both rely on ordered heading levels to convey structure." https://www.nngroup.com/articles/html-headings/

**Anti-pattern.** None found. The audit returns zero in-file skips.
Composed-component headings were verified manually:

- Marketing components (`Hero`, `About`, `CaseStudies`, `HowItWorks`,
  `Pricing`, `ProblemCards`, `WhatYouGet`, `WhoWeHelp`, `FinalCta`):
  `Hero` emits h1; all other sections emit h2 then h3 where nested. No
  skip within any component, no skip in `src/pages/index.astro` where
  they compose together.
- Portal components (`PortalHeader`, `PortalTabs`, `ActionCard`,
  `ArtifactChip`, `ConsultantBlock`, `MoneyDisplay`, `TimelineEntry`):
  none render headings internally, so portal page hierarchy is
  entirely file-local. Portal pages' own h1/h2/h3 sequences are
  compliant.

**Detection.** `ui-drift-audit` H-skips column covers in-file hierarchy.
Composed-component hierarchy is verified by a manual pass when a new
component that emits headings is added; add the component to the Rule 4
component-inventory note in this file and re-verify.

---

## Rule 5 — Typography scale

**Rule.** Every user-visible text node resolves to one of the seven named scale tokens. Arbitrary `text-[Npx]` and raw `text-xs/sm/base/lg/xl/...` are banned in governed contexts.

**Scale.**

| Token          | Size / LH                                | Weight | Use                           |
| -------------- | ---------------------------------------- | ------ | ----------------------------- |
| `text-display` | 32px / 40px                              | 700    | Page hero                     |
| `text-title`   | 20px / 28px                              | 700    | Section heading, card title   |
| `text-heading` | 16px / 22px                              | 600    | Sub-section heading           |
| `text-body-lg` | 18px / 28px                              | 400    | Lead paragraph                |
| `text-body`    | 15px / 24px                              | 400    | Default body                  |
| `text-caption` | 13px / 18px                              | 500    | Metadata, dates, status prose |
| `text-label`   | 12px / 16px (uppercase, 0.08em tracking) | 600    | Eyebrow, section label        |

**Authority.**

- Material 3 type scale: https://m3.material.io/styles/typography/type-scale-tokens
- IBM Carbon typography: https://carbondesignsystem.com/guidelines/typography/overview/

**Anti-pattern — inline arbitrary sizes pervasive in portal detail pages.**

```html
<!-- src/pages/portal/quotes/[id].astro:207-220 (among many) -->
class="text-[13px] leading-[18px] font-medium tracking-[0.01em] ..." ... class="mt-5
font-['Plus_Jakarta_Sans'] font-extrabold text-[32px] sm:text-[42px] leading-tight
tracking-[-0.02em] ..." ... class="mt-5 text-[18px] leading-[28px] ..."
```

Audit flagged 32 arbitrary sizes in this file and 27 in `portal/invoices/[id].astro`. Every inline `text-[Npx]` is a declaration that this particular instance is outside the system. Aggregate effect: no consistent scale.

**Correct pattern.**

```html
<p class="text-label uppercase text-[color:var(--color-meta)]">Proposal</p>
<h1 class="mt-3 text-display font-bold text-[color:var(--color-text-primary)]">
  {engagementTitle}
</h1>
<p class="mt-5 text-body-lg text-[color:var(--color-text-secondary)] max-w-2xl">
  {engagementSubtitle}
</p>
```

**Detection.** `ui-drift-audit` Typo (arb / token) columns. Arbitrary values are hard violations. Token values are pre-remediation informational; once a page is converted, ESLint/grep bans inline `text-[...]` in that surface.

---

## Rule 6 — Spacing rhythm

**Rule.** Vertical gaps between sibling sections and padding on cards/surfaces resolve to named rhythm tokens. Raw `gap-*`, `py-*`, `px-*` with arbitrary integers are banned in governed contexts.

**Scale.**

| Token           | Value | Use                               |
| --------------- | ----- | --------------------------------- |
| `space-section` | 32px  | Gap between major page sections   |
| `space-card`    | 24px  | Card internal padding             |
| `space-row`     | 12px  | Gap between rows in a list        |
| `space-stack`   | 16px  | Vertical stack of sibling content |

**Authority.**

- IBM Carbon spacing: https://carbondesignsystem.com/guidelines/spacing/overview/
- Material 3 layout: https://m3.material.io/foundations/layout/understanding-layout/overview

**Anti-pattern.** Across the codebase the audit counted ~1,000 raw Tailwind spacing tokens (`p-6`, `p-4`, `gap-3`, etc.) — none arbitrary, but all unnamed. The drift is not "out of scale," it's "no rhythm names, so every card picks its own." Rule 6 remediation converts the dominant patterns (`p-6 sm:p-8`, `p-6`, `gap-6`) to `space-card`, `space-section`, etc., and bans raw Tailwind spacing in the converted surfaces.

**Correct pattern.**

```html
<div class="bg-[color:var(--color-surface)] rounded-card border p-card">
  <h2 class="text-heading">Overview</h2>
  <div class="mt-stack">...</div>
</div>
```

**Detection.** `ui-drift-audit` Spacing (arb / token) columns. Same enforcement shape as Rule 5.

---

## Rule 7 — Shared primitives for repeated patterns

**Rule.** When the same visual element appears on multiple surfaces, it
renders through a shared component. The component is the enforcement; prose
rules about "use tokens" and "match the design system" do not survive
multiple generations of AI-authored screens without a code contract behind
them.

**Why.** AI generators (Stitch today, possibly others later) produce each
screen in isolation. Tokens unify colors and spacing; nothing unifies
element _shape_. Without a shared primitive, list-row markup diverges on
every regeneration — different status pills, date formats, CTA positions —
even when every surface "uses the design system." The 2026-04-17 portal
screenshots (proposals vs. invoices vs. documents) are the canonical
example.

**Authority.** Shopify Polaris component system, IBM Carbon design system,
Atlassian Design System — all treat named components as the source of
truth, not token conformance. "Every surface should have a consistent
look" is an aspiration; "every surface must import the same component" is
a contract.

**Anti-pattern.** Every portal list surface (before this rule) hand-rolled:

```tsx
<a href={...} class="block bg-white rounded-lg border border-slate-200 p-stack ...">
  <div class="flex items-center justify-between gap-stack">
    <span class={`inline-block px-2.5 py-0.5 rounded-full text-xs ${statusColorMap[status]}`}>
      {statusLabelMap[status]}
    </span>
    {/* ... */}
  </div>
</a>
```

Each surface chose slightly different class orderings, different pill
tints, different date formats, different CTAs (chevron / button /
icon-circle). Class reorder evasion means a "no forbidden markup string"
test cannot defend this.

**Correct pattern.**

```tsx
import PortalListItem from '../../../components/portal/PortalListItem.astro'
import { resolveInvoiceTone, resolveInvoiceLabel } from '../../../lib/portal/status'

{
  invoices.map((inv) => (
    <PortalListItem
      variant="status"
      href={`/portal/invoices/${inv.id}`}
      tone={resolveInvoiceTone(inv.status)}
      toneLabel={resolveInvoiceLabel(inv.status)}
      title={typeLabel[inv.type]}
      amountCents={Math.round(inv.amount * 100)}
      metaCaption={resolveMetaCaption(inv)}
    />
  ))
}
```

**Registered primitives (portal).**

- [`src/components/portal/PortalListItem.astro`](../../src/components/portal/PortalListItem.astro) — card-shell list row; `variant: 'status' | 'document'`.
- [`src/components/portal/StatusPill.astro`](../../src/components/portal/StatusPill.astro) — tone-based pill; consumes `Tone` from `status.ts`.
- [`src/components/portal/MoneyDisplay.astro`](../../src/components/portal/MoneyDisplay.astro) — dollar-figure renderer (pre-existing).
- [`src/lib/portal/formatters.ts`](../../src/lib/portal/formatters.ts) — `formatShortDate`, `formatRelativeDueCaption`, `formatCentsToCurrency`.
- [`src/lib/portal/status.ts`](../../src/lib/portal/status.ts) — `Tone` type, per-entity `resolveInvoiceTone/Label`, `resolveQuoteTone/Label`.

**Source-of-truth contract.**

- Portal surfaces: import from `src/lib/portal/status.ts` and `src/lib/portal/formatters.ts`.
- Admin surfaces: import from `src/lib/ui/status-badge.ts` (raw Tailwind classes; pre-existing; stays un-migrated).
- Shared / cross-surface code (e.g., email notifications, cross-context reports): add a new helper when the first such caller appears. Do not mix imports.

**Detection.** Two assertion families in [`tests/forbidden-strings.test.ts`](../../tests/forbidden-strings.test.ts) under the heading "Portal list-row registry":

1. **Presence.** Every `src/pages/portal/*/index.astro` (except `engagement/index.astro`, which is a detail surface) that iterates via `.map(` must render through `<PortalListItem>`. Defeats class-reorder evasion because presence is required, not absence.
2. **No local helper redefinition.** No `const formatDate`, `const formatCurrency`, `const statusColorMap`, `const statusLabelMap`, `const typeLabels` in portal list-index files.

The test auto-enrolls new portal list-index files. Exceptions are an explicit `LIST_INDEX_ALLOWLIST` array (commented rationale required).

**Escape hatch.** Add the file path to `LIST_INDEX_ALLOWLIST` with an inline comment explaining why the surface genuinely cannot use the primitive (e.g., "milestone rail is a vertical-timeline, not a list row"). Cap: **≤3 allowlist entries globally**. Exceeding the cap means the primitive is wrong — extend its variants or split, don't allowlist around.

**When to split.** `PortalListItem` is one component with two variants today. Split into `PortalStatusListItem` + `PortalDocumentListItem` only when more than ~5 conditionals key on `variant`, or when a third variant is needed. Don't pre-split.

---

## Enforcement

- **Grep / AST rules** in the `nav-spec/validate.py` extension or a sibling validator: redundancy detector (Rule 2), inline typography detector (Rule 5), inline spacing detector (Rule 6), multi-primary detector (Rule 3), heading-skip detector (Rule 4), pill-context detector (Rule 1).
- **Merge gate** (`.github/workflows/ui-patterns.yml`, pattern mirrors `scope-deferred-todo.yml`) blocks PRs on shipped-rule violations. Gate is expanded rule-by-rule as remediation PRs merge; no gate lands ahead of its remediation.
- **Escape hatch** — `data-pattern="<rule-name>"` attribute on an element, accompanied by an inline HTML comment citing which rule is overridden and why. Cap: **≤3 escape hatches per rule globally**. Monthly audit (same cadence as `crane_skill_audit`) counts hatches; exceeding the cap means the rule is wrong or the pattern is systemic — revise the rule, don't annotate around it.
- **Rendered-DOM checks via Playwright** are deliberately not built speculatively. They earn in per-rule only when a specific rule is demonstrably escapable via component composition (e.g., a `<StatusChip>` component that passes grep but renders the banned shape).

## Relationship to other specs

- [`empty-state-pattern.md`](./empty-state-pattern.md) — content pattern. When rule 1's "prose in detail page" pattern has no authored value, the empty-state pattern governs what renders instead.
- [`.stitch/NAVIGATION.md`](../../.stitch/NAVIGATION.md) — IA + chrome. Heading hierarchy in Rule 4 complements nav's landmark structure rules.
- `.agents/skills/ui-drift-audit/` — produces the audit that seeds this spec's anti-pattern citations.

## Stitch injection (earned, not planned)

If post-remediation Stitch generations still drift, extend `stitch-design`'s
injection template with a UI CONTRACT alongside the existing NAV CONTRACT.
The contract is derived from this spec. Not implemented until evidence
shows it's needed.

## Tokens

Defined in [`src/styles/global.css`](../../src/styles/global.css) under
`@theme`. The typography and spacing tokens in Rules 5 and 6 are authoritative;
color roles (`--color-primary`, `--color-complete`, etc.) are pre-existing
and continue to serve their current semantics.
