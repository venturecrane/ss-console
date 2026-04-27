# Brand Strategist Contribution - Design Brief Round 1

**Author:** Brand Strategist (Sonnet)
**Date:** 2026-04-26
**Design Maturity:** Full system
**Identity:** Plainspoken Sign Shop (migrated from Desert Functional, 2026-04-23)
**Source of truth:** `node_modules/@venturecrane/tokens/dist/ss.css` + `src/styles/global.css`

---

## Table of Contents

1. [Brand Personality](#1-brand-personality)
2. [Design Principles](#2-design-principles)
3. [Color System](#3-color-system)
4. [Typography](#4-typography)
5. [Spacing & Rhythm](#5-spacing--rhythm)
6. [Imagery & Iconography](#6-imagery--iconography)
7. [Inspiration Board](#7-inspiration-board)
8. [Anti-Inspiration](#8-anti-inspiration)

---

## 1. Brand Personality

The Plainspoken Sign Shop identity emerged from one core intuition: a business owner who runs 14 people and reviews their portal after dinner does not want to feel like they opened a startup's landing page. They want to feel like they stepped into a well-run office where everything has a place, the labels are legible from across the room, and nothing is trying to impress them.

Five traits define the personality. Each is a specific position, not a vague aspiration.

---

### 1.1 Plainspoken, not folksy

The voice is direct and unpretentious — the way a competent colleague talks, not the way a copywriter talks. It says what it means and stops. It never performs warmth. It never overexplains to seem helpful.

- **This:** "Your proposal is ready." / "Signed." / "We'll start after payment clears."
- **Not that:** "Great news! Your personalized proposal is waiting for you." / "You're all set! Everything looks good on our end."
- **In UI:** Labels carry the load. Actions are verbs. Status is stated, not celebrated.
- **Voice standard anchor:** CLAUDE.md voice rules — "we / our team," objectives-first framing, no fabricated client copy, no cheerful filler.

---

### 1.2 Authoritative, not corporate

SMD Services delivers what larger firms charge three times more to deliver. The design should communicate that confidence without the trappings of institutional scale: no crest-style logos, no gradient washes that say "Fortune 500 had a bad decade," no dense legalese in helper text. Authoritative means the typography is heavy, the layout is deliberate, and there is no visual noise competing with the content.

- **This:** Archivo Black at display scale. Flat hairline borders. Ink on cream. The page knows what it is.
- **Not that:** Soft drop shadows everywhere. Rounded corners on every card. A neutral blue-gray that telegraphs "enterprise SaaS."
- **In UI:** Weight 900 display scale headings claim authority without decoration. The flatness is the authority.

---

### 1.3 Precise, not fussy

Every element is there because it is doing a job. There is no decoration for decoration's sake, no illustration as a mood setter, no texture overlay on a background that already has a color. At the same time, precision is not spartan — the spacing is generous enough that the content breathes, and the typography scale has enough grades to give hierarchy real shape.

- **This:** A price that takes the full `text-hero-price` (64px, weight 900) on a portal dashboard. One focal point. Nothing competing.
- **Not that:** A price surrounded by explanatory badges, a "✓ Signed" overlay, and a tooltip with hover text.
- **In UI:** Pattern 02 (Redundancy ban) is the structural enforcement of this trait. One signal per fact.

---

### 1.4 Collaborative, not diagnostic

The brand voice says we work alongside the client, not over them. The UI reflects this: the client portal is never a report card, never a status board that tracks them. It is a shared workspace where they can see what is being built on their behalf, ask questions, and access what they need. The tone is peer-to-peer, not service-counter-to-customer.

- **This:** "Here is what we are building and where we are." Milestone names in plain language ("Your scheduling system is live").
- **Not that:** "Phase 2: Implementation — 78% complete." Progress bars with percentages. Engagement status shown as jargon.
- **In UI:** Milestone text must be authored by admin in plain language. The system enforces the empty-state pattern (no jargon fallbacks). This is a content rule with brand implications.

---

### 1.5 Grounded, not minimal

The Plainspoken Sign Shop is not minimalism. Minimalism is a design ideology. The sign shop is a register — the way 1950s commercial signage used heavy letterforms, flat color, and ruled lines not because someone decided "less is more" but because that was what worked at scale on a painted surface. The system inherits that pragmatism. It is dense when density serves the user (pipeline view, quote builder), spacious when spaciousness does (client dashboard first view).

- **This:** The rhythm tokens (section 48px / card 32px / stack 16px / row 12px) applied deliberately per context. The admin is tighter. The portal is airier.
- **Not that:** Applying maximum whitespace uniformly to signal "premium." A 200px-tall empty hero section above a sign-in form.
- **In UI:** Pattern 06 (Spacing rhythm) governs this. The rule is not "be minimal" — it is "use named rhythm tokens and choose the right one per context."

---

## 2. Design Principles

These principles govern tradeoff decisions during build and design review. They are sequenced by priority: when two principles conflict, the higher-ranked one wins. They derive from the PRD's Principle Ladder (§5), the Decision Stack voice rules, and the Plainspoken Sign Shop identity.

---

### Principle 1 — The business operates before the design is complete

Ship phases in order. An un-styled Phase 1 admin that works beats a beautifully designed Phase 3 that blocks the first assessment call. The design system's job is to eliminate the tradeoff by establishing clear defaults early: tokens, primitives, and rhythm tokens are the floor, not the ceiling.

**PRD anchor:** §5 Principle 1 — "The business operates before the software is complete."

---

### Principle 2 — The client's first screen is the proposal, not an onboarding flow

Every design decision in the client portal is evaluated against one question: does this make it harder or easier for Marcus to see his price and sign? The first authenticated view is not a dashboard — it is the proposal. The signature and the price are above the fold on mobile. Everything else is below.

**PRD anchor:** §7 Step 3 — "On mobile, the price and the button are above the fold with no scrolling required."
**Platform constraint anchor:** §14 — Mobile-first for client portal; 44×44pt minimum touch targets.

---

### Principle 3 — No fabricated content; no visual compensation for missing data

Empty sections render nothing or a "TBD in SOW" marker. The design must be legible and complete-looking when authored fields are absent, not just when they are present. This means every layout is designed and tested with missing data, not only with full data. Decorative placeholders, shimmer skeletons that persist, and "Coming soon" copy are all violations.

**Content rule anchor:** `docs/style/empty-state-pattern.md`. P0 enforcement.

---

### Principle 4 — One signal per fact, one primary per view

Pattern 02 (Redundancy ban) and Pattern 03 (Button hierarchy) are the visual enforcement of this principle. Redundant signals are not "belt and suspenders" — they dilute the signal they duplicate. A price displayed at hero scale and then again in a caption and then again in a status pill is a price that reads as uncertain. State something once and state it with authority.

**Pattern anchors:** `docs/style/UI-PATTERNS.md` Rules 2 and 3.

---

### Principle 5 — Status is a fact, not a decoration

Status indicators follow Pattern 01 strictly: pill in list rows, eyebrow for category, dot+label or prose for single-item cards, prose for detail-page headlines. The visual treatment maps to the scanning context, not to how "important" the status feels. Overdue invoices do not get red banners unless that treatment is earned by the scanning context.

**Pattern anchor:** `docs/style/UI-PATTERNS.md` Rule 1. PRD anchor: §9 Interaction Patterns.

---

### Principle 6 — Typography and hairlines carry the page; decoration does not

The Plainspoken Sign Shop has no shadows, no gradient fills, no illustration. The hierarchy is entirely typographic and structural. A card is defined by a hairline border (`rgba(26,21,18,0.16)`) on a cream surface — not by a white fill, drop shadow, and rounded corners that lift it off the page. This constraint is binding, not aspirational. Any PR that introduces shadow or rounded corners to a card or button requires explicit design review.

**Token anchors:** `--ss-radius-card: 0`, `--ss-radius-button: 0`, `--ss-radius-badge: 0`. No shadow tokens exist in the system.

---

### Principle 7 — The admin surface can be dense; the client portal cannot

The admin user is a competent technical operator. They want speed and accuracy, not hand-holding. Dense information display is appropriate: smaller type (approaching caption), tighter row spacing, data tables. The client is a business owner on a phone after dinner. The portal is spacious, single-task, and never cluttered with admin-visible detail (no hourly rates, no per-item hours, no internal status codes). The design system serves both by using the same tokens at different rhythm choices.

**PRD anchor:** §14 — "Dense information display acceptable" for admin; mobile-first for client portal.

---

## 3. Color System

The Plainspoken Sign Shop palette is canonical. It is a four-role palette: cream, ink, accent (burnt orange), and two semantic complements (olive success, brick error). Light-only is the current decision. Dark mode is explicitly not implemented.

**Source of truth:** `node_modules/@venturecrane/tokens/dist/ss.css`

---

### 3.1 Canonical Palette

| Token                        | Hex                   | Computed RGB             | Role                                                          |
| ---------------------------- | --------------------- | ------------------------ | ------------------------------------------------------------- |
| `--ss-color-background`      | `#f5f0e3`             | 245, 240, 227            | Cream paper. Primary page background.                         |
| `--ss-color-surface`         | `#f5f0e3`             | 245, 240, 227            | Card background. Cards defined by hairline borders, not fill. |
| `--ss-color-surface-inverse` | `#1a1512`             | 26, 21, 18               | Ink. Inverted surfaces (e.g., dark headers).                  |
| `--ss-color-border`          | `rgba(26,21,18,0.16)` | ~210, 207, 199 effective | Default border. Ink at 16% opacity.                           |
| `--ss-color-border-subtle`   | `rgba(26,21,18,0.08)` | ~228, 225, 219 effective | Subtle divider. Ink at 8% opacity.                            |
| `--ss-color-text-primary`    | `#1a1512`             | 26, 21, 18               | Ink. All primary content text.                                |
| `--ss-color-text-secondary`  | `#4a423c`             | 74, 66, 60               | Subdued ink. Metadata, secondary labels.                      |
| `--ss-color-text-muted`      | `#8a7f73`             | 138, 127, 115            | Muted ink. Tertiary, placeholders.                            |
| `--ss-color-meta`            | `#4a423c`             | 74, 66, 60               | Card timestamps, IDs. Matches text-secondary.                 |
| `--ss-color-primary`         | `#c5501e`             | 197, 80, 30              | Burnt orange. CTAs, primary actions.                          |
| `--ss-color-primary-hover`   | `#a84318`             | 168, 67, 24              | Burnt orange deepened. CTA hover state.                       |
| `--ss-color-action`          | `#c5501e`             | 197, 80, 30              | Semantically distinct slot; same hue as primary.              |
| `--ss-color-attention`       | `#c5501e`             | 197, 80, 30              | Semantically distinct slot; same hue as primary.              |
| `--ss-color-complete`        | `#4a6b3e`             | 74, 107, 62              | Olive. Success and completed states.                          |
| `--ss-color-error`           | `#a02a2a`             | 160, 42, 42              | Brick. Error and danger states.                               |

---

### 3.2 WCAG AA Contrast Audit

WCAG 2.1 thresholds: **4.5:1** for normal text, **3.0:1** for large text (18pt+/14pt+ bold) and UI components. All ratios computed against actual background context.

#### Text on cream background (`#f5f0e3`)

| Pairing                             | Ratio       | WCAG AA Normal | WCAG AA Large/UI | Notes                                                           |
| ----------------------------------- | ----------- | -------------- | ---------------- | --------------------------------------------------------------- |
| `text-primary` (#1a1512) on cream   | **15.91:1** | Pass           | Pass             | AAA                                                             |
| `text-secondary` (#4a423c) on cream | **8.64:1**  | Pass           | Pass             | AAA                                                             |
| `text-muted` (#8a7f73) on cream     | **3.44:1**  | **Fail**       | Pass             | Normal-size body text in muted color fails AA. See Gap 1 below. |
| `primary` (#c5501e) on cream        | **4.06:1**  | **Fail**       | Pass             | Primary orange as inline text fails AA normal. See Gap 2 below. |

#### Text on interactive surfaces

| Pairing                               | Ratio      | WCAG AA Normal | WCAG AA Large/UI | Notes                              |
| ------------------------------------- | ---------- | -------------- | ---------------- | ---------------------------------- |
| White on `primary` button (#c5501e)   | **4.63:1** | Pass           | Pass             | CTA button text: passes.           |
| White on `primary-hover` (#a84318)    | **6.03:1** | Pass           | Pass             | Hover state: comfortable.          |
| `complete` (#4a6b3e) as text on cream | **5.33:1** | Pass           | Pass             | Success text in body context.      |
| White on `complete` (#4a6b3e)         | **6.06:1** | Pass           | Pass             | White label on olive surface.      |
| `error` (#a02a2a) as text on cream    | **6.45:1** | Pass           | Pass             | Error message body text.           |
| White on `error` (#a02a2a)            | **7.34:1** | Pass           | Pass             | AAA. White on brick error surface. |

#### Inverted surfaces (`surface-inverse` = `#1a1512`)

| Pairing                                  | Ratio       | WCAG AA Normal | Notes                                                                                                                                            |
| ---------------------------------------- | ----------- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| White on `surface-inverse`               | **18.10:1** | Pass           | AAA. White text on ink surface.                                                                                                                  |
| `primary` (#c5501e) on `surface-inverse` | **3.91:1**  | Fail           | Large-only / UI-only pass. Do not use primary orange as normal-body text on ink backgrounds. Acceptable for large headings (18pt+ / bold 14pt+). |

#### Borders (informational — non-text UI components, 3:1 threshold applies)

Borders are structural hairlines, not text. The 3:1 threshold for non-text UI components applies only when the border itself conveys meaningful state. For decorative card hairlines (which are conveyed redundantly by the card's contents), the contrast requirement is informational only.

| Pairing                         | Effective hex | Ratio against cream | Notes                                                                              |
| ------------------------------- | ------------- | ------------------- | ---------------------------------------------------------------------------------- |
| Border (16% ink) on cream       | ~#d2cfc7      | 1.39:1              | Decorative only. Not used as a sole state indicator. Compliant per WCAG technique. |
| Border-subtle (8% ink) on cream | ~#e4e1db      | 1.18:1              | Decorative only. Same exemption.                                                   |

---

### 3.3 Gaps and Proposed Additions

#### Gap 1 — `text-muted` fails WCAG AA for normal-size body text

`#8a7f73` achieves 3.44:1 on cream — sufficient for large text (18px+) and UI components, but not for 14–16px body copy or captions. Current usage in `src/pages/portal/` at `text-body` scale in muted classes needs audit.

**Rule:** `text-muted` is only AA-compliant when rendered at `text-body-lg` (17px) or larger, or with weight 600+. Use `text-secondary` (#4a423c, 8.64:1) for normal-size body text that needs to pass AA.

**No token change required.** The existing token is correct for placeholders and tertiary metadata (where large-text rules apply). The rule is a usage constraint, not a token gap.

#### Gap 2 — Primary orange as inline text fails AA normal

`#c5501e` at 4.06:1 on cream is below 4.5:1. It passes for large text (18px+) and UI components (buttons, icons). It **fails** for normal-body inline link-style text.

**Rule:** Do not render `--ss-color-primary` as body-size text color on cream. Reserve it for: buttons (white label on primary bg), large display (hero/kpi/section-h scale), icons, and status dots where size threshold is met.

**No token change required.** The existing token is correct; the rule is a usage constraint.

#### Gap 3 — No `warning` semantic token

There is currently no distinct warning semantic. `--ss-color-attention` is aliased to `--ss-color-primary` (`#c5501e`), which means "warning" and "action" produce identical visual output. The system cannot visually distinguish "this is a thing you can do" from "something requires your attention before proceeding."

This gap surfaces in the PRD at:

- EC-007: financial blindness warning before admin confirmation
- BR-012: `financial_blindness` soft warning
- Invoice overdue indicators (where burnt orange warning visually conflicts with burnt orange CTAs)

**Proposed addition:** A distinct `--ss-color-warning` token. The color must harmonize with the ink/cream/burnt orange/olive/brick palette and pass AA contrast on cream for both text and UI component usage.

**Proposed value:** `#6b4f08` (deep amber-ochre, 6.72:1 on cream — AA pass, AAA-adjacent)

Rationale: Amber-ochre reads as "caution" without the alarm of brick red. It sits between burnt orange (action) and olive (success) on the warm spectrum, preserving palette cohesion. At 6.72:1 it passes for normal text — the warning message body copies can use it directly. It is warm enough to register as related to the burnt-orange primary family, distinct enough to not be confused with it.

**Token addition (pending PR):**

```css
--ss-color-warning: #6b4f08; /* Deep amber-ochre. Warning and soft-caution states. 6.72:1 on cream (AA). */
```

#### Gap 4 — No focus-ring specification

WCAG 2.1 SC 2.4.7 (Focus Visible) requires a visible focus indicator. WCAG 2.2 SC 2.4.11 (Focus Appearance, AA) specifies 3:1 contrast between focus indicator and adjacent colors.

Primary burnt orange (`#c5501e`) achieves 4.06:1 against cream — satisfies the 3:1 focus appearance requirement. However, there is no token or CSS rule that explicitly specifies the focus ring. This means AI-generated components and regenerations produce ad-hoc `:focus` styling.

**Proposed addition:** Define focus-ring explicitly in `global.css` as a base layer rule using the primary token.

```css
@layer base {
  :focus-visible {
    outline: 2px solid var(--ss-color-primary);
    outline-offset: 2px;
  }
}
```

And a corresponding documentation note in the design spec: focus ring uses `--ss-color-primary` at 2px, offset 2px. This is the system default. Component-level overrides must meet the 3:1 focus appearance requirement.

---

### 3.4 Single-Accent Discipline

The Plainspoken Sign Shop uses one accent color: burnt orange (`#c5501e`). The action, attention, and primary tokens are aliases of the same hue — this is intentional. The discipline is that no second decorative accent is introduced. Olive is semantic (success only). Brick is semantic (error only). No teal, no lavender, no gradient wash.

This is a binding constraint. Any PR that introduces a new color not derived from the canonical hex values above requires design review.

---

## 4. Typography

The type system is a trio with distinct roles. All three are locked. Font changes require a named identity migration, not a PR.

**Source of truth:** `node_modules/@venturecrane/tokens/dist/ss.css`

---

### 4.1 Font Families

| Token                    | Value                                                              | Role                                       |
| ------------------------ | ------------------------------------------------------------------ | ------------------------------------------ |
| `--ss-font-display`      | `'Archivo', system-ui, sans-serif`                                 | Display headings, hero text, all h1/h2/h3  |
| `--ss-font-body`         | `'Archivo', system-ui, sans-serif`                                 | Body copy, form labels, button text        |
| `--ss-font-accent-label` | `'Archivo Narrow', 'Archivo', system-ui, sans-serif`               | Chips, narrow labels, compact metadata     |
| `--ss-font-mono`         | `'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace` | IDs (ULIDs), code values, fixed-width data |

Note: Display and body use the same family (Archivo). This is the Plainspoken register — the sign shop uses one typeface and varies weight, not family. The hierarchy is entirely weight-driven, not family-driven.

---

### 4.2 Functional Scale (7 tokens)

These seven tokens are the complete governed body of named text styles. Arbitrary `text-[Npx]` and raw Tailwind scale names are banned in governed surfaces (Pattern 05).

| Token          | Size (px) | Line-height (px) | Weight | Letter-spacing     | Use                                       |
| -------------- | --------- | ---------------- | ------ | ------------------ | ----------------------------------------- |
| `text-display` | 48        | 54               | 500    | -0.01em            | Page-level hero (non-Plainspoken display) |
| `text-title`   | 28        | 34               | 500    | -0.005em           | Section heading, card title               |
| `text-heading` | 18        | 24               | 600    | —                  | Sub-section heading                       |
| `text-body-lg` | 17        | 26               | 400    | —                  | Lead paragraph, introductory copy         |
| `text-body`    | 16        | 25               | 400    | —                  | Default body text                         |
| `text-caption` | 14        | 20               | 500    | —                  | Metadata, dates, status prose             |
| `text-label`   | 12        | 16               | 500    | 0.08em (uppercase) | Eyebrow, section label                    |

_Values derived from `ss.css` token values converted from rem. Base: 16px._

---

### 4.3 Plainspoken Display Scale (weight-900 register)

These tokens define the Plainspoken Sign Shop's signature: oversized, condensed, negative-tracked numerals and headings that fill their container the way a painted sign fills a board. Weight 900 is non-negotiable in this register. They are the visual identity.

| Token              | Size (px) | Line-height (px) | Weight  | Letter-spacing | Use                                                      |
| ------------------ | --------- | ---------------- | ------- | -------------- | -------------------------------------------------------- |
| `text-hero`        | 72        | 66               | **900** | -0.03em        | Portal H1 desktop (business name, engagement title)      |
| `text-hero-mobile` | 44        | 40               | **900** | -0.03em        | Portal H1 mobile                                         |
| `text-hero-price`  | 64        | 59               | **900** | -0.04em        | Summary-card total project price                         |
| `text-kpi`         | 44        | 44               | **900** | -0.03em        | KPI numbers in admin dashboard                           |
| `text-section-h`   | 36        | 36               | **900** | -0.02em        | Section block headings on detail pages                   |
| `text-price-row`   | 28        | 28               | **900** | -0.02em        | Row-level money in list / ledger contexts                |
| `text-num-cell`    | 22        | 22               | **900** | -0.01em        | ID / sequence cell glyphs in ticket rows                 |
| `text-money`       | 44        | 48               | 500     | —              | MoneyDisplay component (large amounts, not hero context) |

**Where weight-900 applies and does not apply:**

Weight 900 belongs to numbers, prices, and structural labels that the user scans first. It does not belong to paragraph copy, error messages, or anything requiring sustained reading. Applied in the wrong context it becomes visual noise. The rule: use the Plainspoken display scale only when the value is the focal point of its surface and the user's first job is to read that one value.

The MoneyDisplay component uses `text-money` at weight 500 rather than 900 — this is intentional. `text-money` is for amounts in list contexts where multiple amounts appear in rows. `text-hero-price` at weight 900 is for the single total price on the client dashboard where that number is the entire point of the view.

---

### 4.4 Archivo Narrow — Usage Constraint

Archivo Narrow is reserved for chips and narrow labels where horizontal compression serves a density goal. It is not a decorative variation. Use it only via `--ss-font-accent-label` on elements that genuinely need the condensed width — typically: status pills (Pattern 01), tab labels at compressed widths, inline chips in the admin pipeline.

Do not use Archivo Narrow for body text, captions, or headings. The system's hierarchy is weight-based within Archivo regular; introducing Archivo Narrow in hierarchical contexts implies a role it is not performing.

---

### 4.5 JetBrains Mono — Usage Constraint

JetBrains Mono is strictly for:

- ULID values rendered in the admin (ID fields)
- Code values, extraction JSON displays
- Fixed-width data where column alignment matters

It is not a "stylistic monospace for prices." Money values use Archivo at `text-money` or `text-hero-price` scale. Monospace is for machine-readable identifiers, not for making numbers look technical.

---

### 4.6 Italic Usage (Gap)

The current token set defines no italic usage. Archivo supports italic. The system has not addressed when italic is appropriate.

**Proposed rule (addition to spec):**

Italic is permitted only for:

1. Quoted speech in informational copy (e.g., client testimonials on the marketing site)
2. `<em>` emphasis within long-form instructional text where it is the semantic choice

Italic is prohibited for: status labels, metadata, prices, headings, and any UI chrome. Introducing italic to a screen is a signal that the text is playing an unusual rhetorical role — it should be rare.

---

## 5. Spacing & Rhythm

**Source of truth:** `node_modules/@venturecrane/tokens/dist/ss.css`, mapped in `src/styles/global.css` under `--spacing-*`.

---

### 5.1 Canonical Rhythm Tokens

Four tokens define all vertical rhythm in the system. Horizontal rhythm follows the same values. Raw Tailwind spacing utilities (`p-6`, `gap-4`, etc.) are banned in governed surfaces (Pattern 06).

| Token           | CSS var              | rem     | px  | Use                               |
| --------------- | -------------------- | ------- | --- | --------------------------------- |
| `space-section` | `--ss-space-section` | 3rem    | 48  | Gap between major page sections   |
| `space-card`    | `--ss-space-card`    | 2rem    | 32  | Card internal padding             |
| `space-stack`   | `--ss-space-stack`   | 1rem    | 16  | Vertical stack of sibling content |
| `space-row`     | `--ss-space-row`     | 0.75rem | 12  | Gap between rows in a list        |

_Note: The UI-PATTERNS.md Rule 6 table cites `space-card` as 24px and `space-section` as 32px — these appear to be earlier draft values. The live token values in `ss.css` are authoritative: `space-section` = 48px (3rem), `space-card` = 32px (2rem). This document reflects the live token values._

---

### 5.2 Context Application

**Client portal (spacious, mobile-primary):**

- Between dashboard sections: `space-section` (48px)
- Card internal padding: `space-card` (32px)
- Between stacked content blocks within a card: `space-stack` (16px)
- Bottom tab bar items: minimum 44px touch target height per Apple HIG / WCAG 2.5.5

**Admin (dense, desktop-primary):**

- Between pipeline columns: `space-section` (48px)
- Card internal padding: `space-card` (32px), or `space-stack` (16px) for dense data tables where horizontal space is more constrained than vertical
- Between rows in pipeline cards: `space-row` (12px)
- Quote builder line item rows: `space-row` (12px)

---

### 5.3 Shape

All radii are `0`. This is the flat institutional rule and it is binding.

| Token                | Value |
| -------------------- | ----- |
| `--ss-radius-card`   | 0     |
| `--ss-radius-button` | 0     |
| `--ss-radius-badge`  | 0     |

No exceptions are granted by "it would look better rounded." If a UI element reads as broken without rounding, the underlying layout or hierarchy is the problem, not the radius.

---

### 5.4 Motion

Motion tokens are defined but no motion rule currently governs when they apply. This is an acceptable gap at current maturity.

| Token                          | Duration |
| ------------------------------ | -------- |
| `--ss-motion-duration-instant` | 0ms      |
| `--ss-motion-duration-fast`    | 150ms    |
| `--ss-motion-duration-base`    | 250ms    |
| `--ss-motion-duration-slow`    | 400ms    |

**Easing:** standard `cubic-bezier(0.4, 0.0, 0.2, 1)` (Material Design standard curve) for state transitions. Decelerate for elements entering the screen. Accelerate for elements leaving.

**Required:** All motion must respect `prefers-reduced-motion`. A `@media (prefers-reduced-motion: reduce)` block that sets all transition durations to 0ms is required in `global.css`. This is a WCAG 2.1 SC 2.3.3 requirement.

**Gap:** No `prefers-reduced-motion` override block exists in the current `global.css`. This should be filed as an accessibility PR before Phase 2 ships.

---

## 6. Imagery & Iconography

### 6.1 Icon System — Material Symbols Outlined

Material Symbols Outlined is the canonical icon system, loaded in `src/styles/global.css`:

```css
.material-symbols-outlined {
  font-family: 'Material Symbols Outlined';
  font-weight: normal;
  font-style: normal;
  font-size: 24px;
  font-variation-settings:
    'FILL' 0,
    'wght' 400,
    'GRAD' 0,
    'opsz' 24;
}
```

The `FILL' 0` setting uses the outlined (unfilled) variant system-wide. This is correct for the Plainspoken register — filled icons read as heavier and more decorative; the outlined weight is more utilitarian and consistent with the typography weight at body scale.

**Usage rules:**

- Default size: 24px (matching the CSS default). Scale with `opsz` variation setting, not by resizing with CSS `width/height`. Use `opsz: 20` for inline-text-adjacent icons, `opsz: 40` for large display contexts.
- Icons used as the sole communicator of state (no text label) require `aria-label` or visually-hidden text per PRD §14 accessibility requirements.
- Decorative icons (adjacent to text that already communicates the same meaning) get `aria-hidden="true"`.
- Icon weight (`wght` variation) may be adjusted to match surrounding typography weight: `wght: 300` for caption-adjacent icons, `wght: 600` for heading-adjacent icons. The default 400 is correct for body text context.

### 6.2 No Illustration

No illustration. No marketing mascots. No abstract blob shapes. No decorative background patterns. The Plainspoken Sign Shop is a typographic system — the content is the content, not a backdrop for it.

**Binding rule:** Any graphic element that is not typography, a Material Symbols icon, a hairline border, or an authored data value (chart, image uploaded by admin) is out of scope. Empty states in the portal render nothing or a "TBD in SOW" marker — never an illustration of a person looking at a clipboard.

**Why this matters for a portal:** Empty state illustrations are the classic cargo-culting of consumer apps into B2B tools. Marcus does not need a friendly illustration when his documents list is empty — he needs to understand that documents will appear when the engagement progresses. Plain prose achieves this. Illustration achieves the opposite: it suggests the product was designed by a team that prioritized personality over utility.

### 6.3 No Photography

No photography in the product UI. The marketing site (`smd.services`) may carry photography if the marketing direction calls for it — that is a separate design scope. The portal and admin contain no photographic content.

**Rationale:** The trust signal for this product is operational competence, not lifestyle photography. A photo of a person smiling in a branded shirt undermines the Plainspoken register; it signals "startup pitch" rather than "experienced operator."

### 6.4 Logo and Brand Mark

No brand mark is present in the current system. A named product is deferred until productization (PRD §2, Open Decision D1). The current absence of a logo is intentional and correct at this stage. When a logo is introduced it must be wordmark-based and legible at the cream/ink palette — no gradient fills, no icon-only marks that would require color to read.

---

## 7. Inspiration Board

Five references that capture the Plainspoken Sign Shop register: heavy commercial typography, flat color, institutional authority without corporate gloss, operational density in the service of legibility.

---

### 7.1 Stripe Dashboard

**URL:** https://dashboard.stripe.com

The Stripe admin dashboard is the closest reference for the admin surface tone: dense data tables, a neutral page chrome that stays out of the way of financial data, and status indicators that use color conservatively. Stripe's typography is not weight-900 display scale — but the layout discipline (one action per row, status at scan time, financial amounts at the right column edge) is the model.

**What to take:** The pipeline view and quote builder should feel this organized. Everything is data; the chrome is invisible.
**What not to take:** Stripe uses blue as its accent. Stripe has rounded inputs and card shadows. Both are wrong for SS.

---

### 7.2 Linear Issue Tracker

**URL:** https://linear.app

Linear's typographic density and keyboard-first design serve as a reference for the admin pipeline view. The issue list is dense without feeling cluttered because the hierarchy is entirely typographic and the status indicators are minimal dots, not colorful badges. The "no decoration on productive surfaces" principle is executed here.

**What to take:** Status dot discipline. Row density with clear primary/secondary text hierarchy. No rounded corners on action surfaces in the dense view.
**What not to take:** Linear uses a dark mode by default and a purple brand accent. Neither applies.

---

### 7.3 Letterpress and commercial sign shop ephemera (reference, not digital)

**URL:** https://letterformarchive.org/collection/

The Letterform Archive in San Francisco documents mid-century commercial typography: painted business signs, letterpress trade cards, industrial catalog covers. The Plainspoken Sign Shop identity is a direct reference to this register. The 1950s sign-painting idiom used heavy slab or grotesque fonts, ruled borders, and flat color because those were what survived at scale and distance — not for aesthetic reasons, but functional ones.

**What to take:** The weight-900 hero/kpi/section-h display scale. The ruled hairline as the only decoration. The lack of drop shadows. The color discipline (one accent, two semantic colors).
**What not to take:** Literally reproduce historical signage design in a web product. The reference is tonal and typographic, not literal.

---

### 7.4 Basecamp / HEY Email

**URL:** https://app.hey.com

HEY's interface demonstrates that a highly opinionated design personality can coexist with a dense, functional product. The typography is confident, the layout is structured, and the interface makes strong choices without softening them into generic SaaS. The willingness to be decisive — to not hedge every design decision into a rounded, neutral, inoffensive surface — is the quality this reference captures.

**What to take:** Conviction in typographic choices. Willingness to use large, heavy text where it serves the user's goal. Non-apologetic information density in list views.
**What not to take:** HEY's playful color palette and illustrated onboarding. The tone is wrong for a consulting operations portal.

---

### 7.5 Stacks (Stack Overflow for Teams) / Notion in structured use

**URL:** https://stackoverflow.com (Teams product)

For the client-facing portal specifically: the reference is any product that successfully uses a document/editorial register to present structured information to non-technical users. The proposal detail view (`/portal/quotes/[id]`) should read like a well-composed document, not a software interface. Stack Overflow's Q&A surface uses typography and spacing to make dense structured content readable without UI chrome getting in the way.

**What to take:** The editorial layout for the quote detail view and document library. Content at full width, no chrome competing with it.
**What not to take:** Stack's community features, reputation system, and developer-focused identity.

---

## 8. Anti-Inspiration

Three examples of what the Plainspoken Sign Shop actively refuses. Understanding what is wrong is as important as understanding what is right.

---

### 8.1 Copilot.com (client portal product)

**URL:** https://copilot.com

Copilot is the closest direct competitor and the clearest anti-reference for the client portal design. It uses the generic purple-gradient-rounded-card SaaS aesthetic: purple brand accent, rounded modals, illustration-heavy empty states, and a visual language that communicates "friendly startup product" rather than "professional services operation." A Marcus who opened a Copilot portal would feel like he signed up for a productivity app, not engaged an experienced consulting team.

**Specifically wrong:** The onboarding illustrations. The purple. The heavy use of whitespace that fills screen real estate without communicating information. The pill-shaped status badges.

---

### 8.2 HoneyBook (all-in-one CRM)

**URL:** https://www.honeybook.com

HoneyBook targets freelancers and creative professionals — photographers, event planners, designers. Its visual language is optimistic, warm, and heavily illustrated. The dashboard uses large feature cards with gradient washes, the proposal view uses soft shadows and rounded corners throughout, and the general tone is "you've got this, creative" rather than "here is the operational status of your engagement." It is a well-executed product for its target — and entirely wrong for SMD Services.

**Specifically wrong:** Any design element that could appear on the HoneyBook dashboard — rounded white cards with drop shadows on a light gray background, soft accent colors, graphic illustration for empty states — is an anti-pattern for SS. The comparison is useful for any PR review: "would this look at home in HoneyBook?" If yes, reconsider.

---

### 8.3 Generic enterprise SaaS (Salesforce / ServiceNow aesthetic)

**URL:** https://www.salesforce.com / https://www.servicenow.com

The opposite failure mode from HoneyBook: institutional without being authoritative. The old-enterprise palette of navy, sky blue, and gray produces surfaces that communicate "deployed by IT" rather than "built for this business." The typography is safe, the data tables are dense but visually oppressive, and the overall impression is that the software is serving the organization rather than the user.

**Specifically wrong:** Navy or dark blue as a brand anchor. Sky blue accents. Table-heavy layouts that emphasize the data structure over the decision the user needs to make. The impression that the portal exists because corporate policy requires it, not because it makes the engagement better.

The SMD Services portal should feel different from both of these failure modes: not a startup product pitching itself to freelancers, and not an enterprise system deployed by committee. It should feel like it was built by the team that is delivering the engagement — which is exactly what it was.

---

## Appendix: Token Gaps Summary

| Gap                                           | Severity               | Current state                                   | Proposed resolution                                                              |
| --------------------------------------------- | ---------------------- | ----------------------------------------------- | -------------------------------------------------------------------------------- |
| No `warning` semantic token                   | Medium                 | `attention === primary` (burnt orange conflict) | Add `--ss-color-warning: #6b4f08` (6.72:1 on cream)                              |
| No focus-ring specification                   | Medium (a11y)          | Ad-hoc per component                            | Add `:focus-visible` base rule using `--ss-color-primary`, 2px, offset 2px       |
| No `prefers-reduced-motion` block             | Medium (a11y)          | Motion tokens exist; no override rule           | Add `@media (prefers-reduced-motion: reduce)` in global.css                      |
| `text-muted` fails AA normal text             | Low (usage constraint) | Token is correct; usage scope needs tightening  | Document: `text-muted` restricted to large-text (17px+) or UI-component contexts |
| Primary orange fails AA as normal inline text | Low (usage constraint) | Token is correct for buttons/icons/display      | Document: primary never used as body-size text color                             |
| No italic usage rule                          | Low                    | No guidance exists                              | Add italic rule to type spec: quoted speech and `<em>` only                      |

---

_SMD Services — Brand Strategist Contribution, Design Brief Round 1_
_Plainspoken Sign Shop identity. Paint-job, not brochure._
