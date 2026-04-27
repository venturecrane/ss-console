# SMD Services Design Spec

> Design system reference for SMD Services agents. Auto-synced to crane-context.
> Design Maturity: Tier 1 - Established system with documented tokens, component library, and 7 enforced UI patterns.
> Last updated: 2026-04-26

## Identity

- **Venture:** SMD Services
- **Code:** ss
- **Tagline (internal):** The system that runs the engagements the way the Decision Stack says they should run.
- **Audience:** SMB owners ($750k–$5M revenue, expanding to $10M). Phoenix metro Phase 1 in-person; remote-capable. Persona: Marcus, HVAC owner, Chandler — phone-first, time-pressed, evaluating whether the firm matches the price.
- **Brand Voice:** "We" / "our team" — never "I" or "the consultant" (Decision #20). Plainspoken not folksy. Authoritative not corporate. Past-tense events in the portal; future-tense pathfinding on marketing. No em dashes, no AI parallel structures, no fabricated client-facing content (P0 enforcement). Evidence over reassurance.
- **Identity name:** Plainspoken Sign Shop — 1950s commercial signage register applied to a modern operational portal. "Paint-job, not brochure." Migrated from Desert Functional on 2026-04-23.

## Tech Stack

- **Framework:** Astro SSR on Cloudflare Workers + Static Assets
- **CSS Methodology:** Tailwind v4 with CSS custom properties via `@theme inline`. All tokens prefixed `--ss-*`.
- **Tailwind Version:** 4.x
- **Token Source:** `@venturecrane/tokens` (W3C-DTCG, compiled by Style Dictionary v4). JSON source: `crane-console/packages/tokens/src/ventures/ss.json`.
- **Hosting:** Cloudflare Workers + Static Assets (`ss-web` worker)
- **Domain:** `smd.services` (marketing), `admin.smd.services` (internal), `portal.smd.services` (client) — three subdomains, one Worker, host-based middleware rewrite.

## Three-Subdomain Architecture

| Host                  | Serves                                   | Auth role |
| --------------------- | ---------------------------------------- | --------- |
| `smd.services`        | Marketing                                | Public    |
| `admin.smd.services`  | Admin console (rewritten to `/admin/*`)  | `admin`   |
| `portal.smd.services` | Client portal (rewritten to `/portal/*`) | `client`  |

Routing handled by `src/middleware.ts`. Cookies are per-host (no `Domain` attribute).

## Component Library

33+ components currently in source. Atomic-design vocabulary: atoms / molecules / organisms.

**Canonical shared primitives** (Pattern 07 reference implementations, promoted to enterprise scope):

- `PortalListItem.astro` — list-row organism for portal index pages
- `StatusPill.astro` — status molecule (pill, eyebrow, dot, prose modes per Pattern 01)
- `MoneyDisplay.astro` — money atom with `tabular-nums lining-nums`

**Portal components** (`src/components/portal/`, 17 total):
ActionCard, ArtifactChip, ConsultantBlock, Documents, EngagementProgress, InvoiceDetail, InvoicesList, MoneyDisplay, PortalHeader, PortalHomeDashboard, PortalListItem, PortalPageHead, PortalTabs, QuoteDetail, QuoteList, StatusPill, TimelineEntry.

**Marketing components** (`src/components/`, ~16 total):
About, CaseStudies, CtaButton, EventsTracker, FinalCta, Footer, Hero, HowItWorks, JsonLd, Nav, Pricing, ProblemCards, SkipToMain, WhatYouGet, WhoWeHelp.

**Admin & booking:** `src/components/admin/`, `src/components/booking/`.

**Naming:** PascalCase, `.astro` extension. ARIA roles required on every interactive element.

**New components proposed by design brief (3 portal + 7 admin):** SigningView, MagicLinkExpiredForm, ParkingLotPanel, PreSigningPrep (portal); PipelineKanban, ClientCard, QuoteLineItemEditor, SOWPreviewPane, FollowUpCard, TimeEntryLog, ExtractionPanel (admin). See `docs/design/brief.md` §7 for spec.

## Dark/Light Mode

**Light-only.** No dark-mode variant. Cream paper background (`#f5f0e3`) with ink text (`#1a1512`) is canonical.

Rationale: Plainspoken Sign Shop register requires the cream-paper substrate. A dark mode would break the letterhead aesthetic that Marcus and other SMB owner personas connect to trust.

## Accessibility

- **WCAG Target:** 2.1 AA. Strive for AAA on contrast where readable in Phoenix outdoor sunlight is a real-world concern (Persona 1 lives in his truck).
- **Focus Indicators:** `outline: 2px solid var(--ss-color-action); outline-offset: 2px` via `:focus-visible` in `@layer base`.
- **Skip Link:** `SkipToMain.astro` already exists. Absolute-positioned, visually hidden until focused.
- **Touch Targets:** Minimum 44×44px per WCAG 2.5.5 + Apple HIG. Marcus wears work gloves on cold mornings; mistaps on a financial portal are unacceptable.
- **Reduced Motion:** Required `@media (prefers-reduced-motion: reduce)` block in `global.css` (proposed addition; currently absent — see Open Decisions).
- **Live Regions:** Parking-lot updates use `aria-live="polite"`; pre-signing post-completion confirmation uses `aria-live="assertive"`.

## Color Tokens

All tokens use the `--ss-*` prefix.

### Core Palette

| Token                        | Hex                   | Purpose                                                         | WCAG (on bg `#f5f0e3`)                     |
| ---------------------------- | --------------------- | --------------------------------------------------------------- | ------------------------------------------ |
| `--ss-color-background`      | `#f5f0e3`             | Cream paper. Primary page background.                           | —                                          |
| `--ss-color-surface`         | `#f5f0e3`             | Card background — flat paper. Cards defined by rules, not fill. | —                                          |
| `--ss-color-surface-inverse` | `#1a1512`             | Ink. Inverted surfaces.                                         | 15.91:1 (AAA)                              |
| `--ss-color-border`          | `rgba(26,21,18,0.16)` | Default border (ink at 16% opacity).                            | —                                          |
| `--ss-color-border-subtle`   | `rgba(26,21,18,0.08)` | Subtle divider (ink at 8% opacity).                             | —                                          |
| `--ss-color-text-primary`    | `#1a1512`             | Ink. Primary text.                                              | 15.91:1 (AAA)                              |
| `--ss-color-text-secondary`  | `#4a423c`             | Subdued ink. Metadata.                                          | 8.64:1 (AAA)                               |
| `--ss-color-text-muted`      | `#8a7f73`             | Muted ink. **Decoration only — never primary information.**     | 3.0:1 (decoration only; fails AA for text) |
| `--ss-color-meta`            | `#4a423c`             | Eyebrow labels, non-primary accent. Same as text-secondary.     | 8.64:1 (AAA)                               |

### Action Palette

| Token                      | Hex       | Purpose                                                    | WCAG                    |
| -------------------------- | --------- | ---------------------------------------------------------- | ----------------------- |
| `--ss-color-primary`       | `#c5501e` | Burnt orange. Primary CTAs.                                | 4.63:1 white-on-primary |
| `--ss-color-primary-hover` | `#a84318` | Burnt orange, deepened. Hover state.                       | 5.8:1 white-on-hover    |
| `--ss-color-action`        | `#c5501e` | Action color — same hue as primary.                        | 4.63:1                  |
| `--ss-color-attention`     | `#c5501e` | Attention — same hue as primary. Single-accent discipline. | 4.63:1                  |

### Status Colors

| Token                 | Hex       | Purpose                                                                                                      | WCAG         |
| --------------------- | --------- | ------------------------------------------------------------------------------------------------------------ | ------------ |
| `--ss-color-complete` | `#4a6b3e` | Olive — complement to burnt orange. Success states.                                                          | 5.33:1 (AA)  |
| `--ss-color-warning`  | `#7a5800` | **Proposed (new).** Deep amber. AAA-adjacent. Quote-near-expiry, deposit-overdue, parking-lot-stale (admin). | 7.14:1 (AAA) |
| `--ss-color-error`    | `#a02a2a` | Brick. Errors.                                                                                               | 6.45:1 (AA)  |

**Single-accent discipline:** `primary` / `action` / `attention` all resolve to burnt orange. `complete` is the deliberate complement (olive). `warning` is the new amber. `error` reads as a distinct warm red.

**Olive usage hierarchy** (from design brief §4): Olive applies only to four hero completion events — engagement complete, quote signed, deposit paid, completion invoice paid. Routine milestone completions use an ink check icon, not olive. Avoid "olive fatigue."

## Typography

### Font Stacks

```css
--ss-font-display: 'Archivo', system-ui, sans-serif;
--ss-font-body: 'Archivo', system-ui, sans-serif;
--ss-font-accent-label: 'Archivo Narrow', 'Archivo', system-ui, sans-serif;
--ss-font-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
```

Plainspoken register: single-family display + body via Archivo. Archivo Narrow for chips and tags. JetBrains Mono for IDs, invoice numbers, code, fixed-width data.

### Functional Type Scale

| Token                    | Size             | Line-height | Weight | Letter-spacing | Usage                           |
| ------------------------ | ---------------- | ----------- | ------ | -------------- | ------------------------------- |
| `--ss-text-size-display` | 3rem (48px)      | 3.375rem    | 500    | -0.01em        | Display headings                |
| `--ss-text-size-title`   | 1.75rem (28px)   | 2.125rem    | 500    | -0.005em       | Section titles                  |
| `--ss-text-size-heading` | 1.125rem (18px)  | 1.5rem      | 600    | —              | Subsection, card titles         |
| `--ss-text-size-body-lg` | 1.0625rem (17px) | 1.625rem    | 400    | —              | Long-form prose                 |
| `--ss-text-size-body`    | 1rem (16px)      | 1.55rem     | 400    | —              | Default body                    |
| `--ss-text-size-caption` | 0.875rem (14px)  | 1.25rem     | 500    | —              | Metadata                        |
| `--ss-text-size-label`   | 0.75rem (12px)   | 1rem        | 500    | 0.08em         | Uppercase eyebrow labels (mono) |
| `--ss-text-size-money`   | 2.75rem (44px)   | 3rem        | 500    | -0.01em        | Inline money display            |

### Plainspoken Display Scale (weight 900)

For portal hero moments and admin KPI dashboards. Functional scale used everywhere else.

| Token                        | Size            | Weight | Letter-spacing | Usage                      |
| ---------------------------- | --------------- | ------ | -------------- | -------------------------- |
| `--ss-text-size-hero`        | 4.5rem (72px)   | 900    | -0.03em        | Portal H1 desktop          |
| `--ss-text-size-hero-mobile` | 2.75rem (44px)  | 900    | -0.03em        | Portal H1 mobile           |
| `--ss-text-size-hero-price`  | 4rem (64px)     | 900    | -0.04em        | Summary-card total price   |
| `--ss-text-size-kpi`         | 2.75rem (44px)  | 900    | -0.03em        | KPI dashboard numbers      |
| `--ss-text-size-section-h`   | 2.25rem (36px)  | 900    | -0.02em        | Detail-page block headings |
| `--ss-text-size-price-row`   | 1.75rem (28px)  | 900    | -0.02em        | Row-level money            |
| `--ss-text-size-num-cell`    | 1.375rem (22px) | 900    | -0.01em        | Ticket-row numerals        |

## Spacing

Token rhythm — see Pattern 06 (Spacing Rhythm).

| Token                | Value          | Usage                           |
| -------------------- | -------------- | ------------------------------- |
| `--ss-space-section` | 3rem (48px)    | Gap between major page sections |
| `--ss-space-card`    | 2rem (32px)    | Card and panel internal padding |
| `--ss-space-stack`   | 1rem (16px)    | Default vertical rhythm         |
| `--ss-space-row`     | 0.75rem (12px) | Gap between list rows           |

**Note:** `docs/style/UI-PATTERNS.md` Rule 6 documents `section: 32px / card: 24px` — a stale draft. Live token values (48 / 32) are canonical. Doc correction tracked in design brief Open Decisions.

## Shape

All radii are `0`. Flat institutional / commercial signage register. **Hard rule:** no rounded corners anywhere — buttons, cards, badges, chips, modals. Round corners would break the entire register.

| Token                | Value |
| -------------------- | ----- |
| `--ss-radius-card`   | 0     |
| `--ss-radius-button` | 0     |
| `--ss-radius-badge`  | 0     |

## Motion

| Token                           | Value                              | Usage                  |
| ------------------------------- | ---------------------------------- | ---------------------- |
| `--ss-motion-duration-instant`  | 0ms                                | No-op transitions      |
| `--ss-motion-duration-fast`     | 150ms                              | Hover states           |
| `--ss-motion-duration-base`     | 250ms                              | Default transitions    |
| `--ss-motion-duration-slow`     | 400ms                              | Larger spatial changes |
| `--ss-motion-easing-standard`   | `cubic-bezier(0.4, 0.0, 0.2, 1)`   | Standard               |
| `--ss-motion-easing-decelerate` | `cubic-bezier(0.0, 0.0, 0.2, 1)`   | Entering               |
| `--ss-motion-easing-accelerate` | `cubic-bezier(0.4, 0.0, 1.0, 1.0)` | Exiting                |

**Required:** `prefers-reduced-motion` global rule (currently missing from `global.css`). No celebratory animations on success states.

## Surface Hierarchy

Plainspoken does not use elevation tiers — cards are defined by rules (hairlines), not fill. There is one surface (`--ss-color-surface` = `--ss-color-background`).

Inverse surface (`--ss-color-surface-inverse`, `#1a1512`) is reserved for inverted regions on the marketing site (Pricing, FinalCta).

## Shadows

**None.** Typography and hairline borders carry the page. Drop shadows are an explicit anti-pattern.

## Imagery & Iconography

- **Icon library:** Material Symbols Outlined. Axis: `wght 400, opsz 24, FILL 0, GRAD 0`. Tab-icon context uses `opsz 20`. Active state may use `FILL 1`.
- **No illustration.** No marketing mascots, no flat-vector explainer art.
- **No photography in portal/admin.** Marketing-site photography is an Open Decision (Marcus pushed back; Brand Strategist favors absolute no-photo rule).
- **No client logos / trust badges / partner imagery.**
- **No emoji in body prose.**

## UI Patterns (enforced)

The 7 cited rules promoted from SS to enterprise scope. Source: `docs/style/UI-PATTERNS.md`. Catalog: `crane_doc('global', 'design-system.patterns.index.md')`.

1. **Status display by context** — pill vs eyebrow vs dot vs prose. Cited: Material 3, Polaris, Atlassian.
2. **Redundancy ban** — one signal per fact. Cited: Carbon, NN/g.
3. **Button hierarchy** — one primary per view. Cited: Material 3, HIG.
4. **Heading skip ban** — h1 → h2 → h3 descending. Cited: WCAG 1.3.1.
5. **Typography scale** — 7 functional tokens, no inline sizes. Cited: Material 3.
6. **Spacing rhythm** — 4 tokens (section/card/stack/row). Cited: Atlassian, NN/g.
7. **Shared primitives** — repeated elements rendered through components, not hand-rolled. Cited: Polaris.

Pattern 8 (Actions and menus) authored in enterprise scope, applies to row-action / context-menu surfaces in admin.

**Empty-state pattern:** `docs/style/empty-state-pattern.md` — render nothing or "TBD in SOW" rather than fabricated client-facing copy. P0 enforcement via `forbidden-strings.test.ts` and merge-gate workflows.

## Performance Budget

| Metric             | Target (3G)                             | Target (4G) |
| ------------------ | --------------------------------------- | ----------- |
| FCP                | 1500ms                                  | 800ms       |
| LCP                | 2500ms                                  | 1200ms      |
| TTI                | 3500ms                                  | 1800ms      |
| CLS                | <0.1                                    | <0.1        |
| CSS bundle (gz)    | <30KB                                   | <30KB       |
| Portal JS bundle   | 0KB (Astro static)                      | 0KB         |
| Admin JS bundle    | <50KB (QuoteLineItemEditor island only) | <50KB       |
| PDF response (p95) | <800ms                                  | <800ms      |

## Design Maturity

**Tier 1 — Established.** SS is the only venture marked Concrete on both L3 (Components) and L4 (Patterns) in the enterprise inventory. Token JSON source lives in `crane-console/packages/tokens/src/ventures/ss.json` and compiles to `node_modules/@venturecrane/tokens/dist/ss.css`.

**Maturity gaps tracked in design brief Open Decisions:**

- Warning token (`#7a5800`) needs to be added to token JSON
- Focus-ring tokens (`--ss-focus-ring-width`, `--ss-focus-ring-offset`) need to be added
- `@media (prefers-reduced-motion: reduce)` block missing from `global.css`
- Motion tokens not mapped into Tailwind v4 `@theme inline` (cannot use `duration-*` / `ease-*` utilities)
- `UI-PATTERNS.md` Rule 6 spacing values drift from compiled tokens

Run `/design-brief` for the full multi-agent design definition. Synthesized brief: `docs/design/brief.md`.
