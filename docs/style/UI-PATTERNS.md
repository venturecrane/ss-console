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

**Anti-pattern — multiple primary actions in one view.**

Audit surfaced 7 files with primary-CTA count > 1. Manual review required
for each: some genuinely have conditional branches (state machine where only
one renders per state — legitimate), others render multiple primaries
simultaneously (violation).

Known violators that need review:

- `src/pages/scorecard.astro` — 5 primary CTAs counted. Public marketing page; multiple calls-to-action are common but one should dominate.
- `src/pages/book.astro` — 3 primaries. Booking flow should have one "next action" per step.
- `src/components/booking/SlotPicker.astro` — 4 primaries. Button for each slot likely overqualifies as primary styling.
- `src/pages/portal/quotes/[id].astro` — 2 primaries. Likely conditional (sign button vs revised-version link) — verify.

**Detection.** `ui-drift-audit` Primary CTAs column. A file with count > 1 needs human review to distinguish state-machine branching from simultaneous rendering.

---

## Rule 4 — Heading skip ban

**Rule.** Heading levels descend in steps. h1 → h2 → h3. No h1 → h3, no h2 → h4. Eyebrows are not headings. Visual size does not imply heading level.

**Authority.**

- WCAG 2.2 SC 1.3.1 (Info and Relationships, Level A): headings must convey document structure. https://www.w3.org/WAI/WCAG22/Understanding/info-and-relationships.html
- NN/g on heading hierarchy: "Screen readers and scanning both rely on ordered heading levels to convey structure." https://www.nngroup.com/articles/html-headings/

**Anti-pattern.** None surfaced by the audit at `.stitch/audits/ui-drift-2026-04-16.md` — but detection is a known false-negative for composed headings (a page including `<PortalHeader>` that internally renders an `<h1>` is invisible to the source-level grep). Rule 4 remediation will start with a manual pass over composed headers before the automated gate lands.

**Detection.** `ui-drift-audit` H-skips column, plus a manual audit of components that render headings internally.

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
