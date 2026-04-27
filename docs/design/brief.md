# SMD Services Client Portal — Design Brief

> Synthesized from 3-round, 4-role design brief process. Generated 2026-04-26.
> Design Maturity: Full system

## Table of Contents

1. [Product Identity](#1-product-identity)
2. [Brand Personality & Design Principles](#2-brand-personality--design-principles)
3. [Target User Context](#3-target-user-context)
4. [Visual Language](#4-visual-language)
5. [Screen Inventory & Key Screens](#5-screen-inventory--key-screens)
6. [Interaction Patterns](#6-interaction-patterns)
7. [Component System Direction](#7-component-system-direction)
8. [Technical Constraints](#8-technical-constraints)
9. [Inspiration & Anti-Inspiration](#9-inspiration--anti-inspiration)
10. [Design Asks](#10-design-asks)
11. [Open Design Decisions](#11-open-design-decisions)

---

## 1. Product Identity

### 1.1 What This Is

The SMD Services Client Portal is the operational interface for a scope-based solutions consulting engagement. It is not a SaaS product. It does not have a product name at MVP and should not be given one (see Open Decision D-3). It exists at `portal.smd.services` and delivers one thing: a bounded, documented record of what Marcus paid for, what was agreed to, what has been built, and what was decided about everything that came up along the way.

Three surfaces share one Astro application:

| Host                  | Serves        | Auth                       |
| --------------------- | ------------- | -------------------------- |
| `smd.services`        | Marketing     | Public                     |
| `admin.smd.services`  | Admin console | `admin` role               |
| `portal.smd.services` | Client portal | `client` role (magic link) |

The portal surface is the client-facing core of this brief. The admin surface enables it. The marketing site is the front door.

### 1.2 The Portal's Job

Before Marcus signed: show him the price and the sign button above the fold on his phone.

After Marcus signed: show him what is being built, what he owes, and what was decided about anything that came up.

After the engagement: give him a complete record he can file in Dropbox alongside the invoice.

### 1.3 Naming Recommendation

Stay nameless at MVP. The subdomain `portal.smd.services` is the implicit name. Marcus does not benefit from an additional named entity to remember. "Your portal" in prose is sufficient.

When naming becomes appropriate: at productization, if the portal is licensed to other consulting firms, or when Marcus has multiple concurrent engagements needing disambiguation.

Do not add a product name to portal chrome, `<title>` tags, or client-facing email copy. "SMD Services portal" in prose context is sufficient.

### 1.4 Voice: Same Register, Different Mode

The marketing site and the portal use the same voice. The register differs.

Marketing voice: guide persona, pathfinding language, objectives-first framing, future tense. "We figure it out together."

Portal voice: the same promise kept. Past tense for completed events. Present tense for active states. Near-future only when the next step is authored and imminent.

**Rule for portal copy:** Never "we'll" unless it comes from an authored `next_step_text` field. No speculative promises. No fabricated reassurance. If data is missing, render nothing or a "TBD in SOW" marker.

---

## 2. Brand Personality & Design Principles

### 2.1 The Plainspoken Sign Shop

The identity is named and canonical. It derives from 1950s American commercial signage: weight-900 letterforms on flat surfaces, ruled hairlines, ink on cream, no decoration for decoration's sake. The register is pragmatic — signs had to be readable from across a parking lot, so they used the heaviest legible weight and highest contrast combination available. The SS token system inherits that logic.

Marcus identified the register without knowing the name: "This looks like a letterhead. Like something you'd get from a contractor or a law office." That is the target.

### 2.2 Five Personality Traits

**Plainspoken, not folksy.** Direct and unpretentious. "Your proposal is ready." Not "Great news! Your personalized proposal is waiting for you."

**Authoritative, not corporate.** Heavy typography. Deliberate layout. No visual noise competing with content. Confident without the trappings of institutional scale.

> Marcus: "Sharp edges feel like they're being honest with me."

**Precise, not fussy.** Every element is doing a job. No decoration for decoration's sake. No illustration as a mood setter. One focal point per view. One primary signal per fact.

**Collaborative, not diagnostic.** The portal is a shared workspace where Marcus can see what is being built on his behalf. Not a status console where a service reports progress at him.

**Grounded, not minimal.** Minimalism is an ideology; the Sign Shop is a register. Dense when the user needs to scan quickly (pipeline view, invoice list). Spacious when the user needs to act on a single thing (portal dashboard, quote detail).

### 2.3 Design Principles

Sequenced by priority. When two conflict, the higher-ranked wins.

**Principle 1 — The business operates before the design is complete.**
Ship phases in order. The design system's job is to eliminate the tension between speed and quality by establishing clear defaults early.

**Principle 2 — The client's first screen is the proposal, not an onboarding flow.**
Every design decision is evaluated against: does this make it harder or easier for Marcus to see his price and sign? The price and the "Review & Sign" button must be above the fold on mobile without scrolling.

> Marcus: "The price and the 'Review & Sign' button need to be on the screen without scrolling on my phone. That's non-negotiable."

**Principle 3 — No fabricated content; no visual compensation for missing data.**
Empty sections render nothing or a "TBD in SOW" marker. Every layout is designed and tested with missing data. Decorative placeholders and "Coming soon" copy are P0 violations. Anchored in `docs/style/empty-state-pattern.md`.

**Principle 4 — One signal per fact, one primary per view.**
A price displayed at hero scale and again in a caption and again in a status pill reads as uncertain. State something once and state it with authority. Enforced by `UI-PATTERNS.md` Rules 2 and 3.

**Principle 5 — Status is a fact, not a decoration.**
Status indicators follow Pattern 01 strictly: pill in list rows, eyebrow for category, dot+label or prose for single-item cards, prose for detail-page headlines. The visual treatment maps to the scanning context, not to how important the status feels.

**Principle 6 — Typography and hairlines carry the page; decoration does not.**
No shadows, no gradient fills, no illustration. Any PR introducing shadow or rounded corners to a card or button requires explicit design review. Token anchors: `--ss-radius-card: 0`, `--ss-radius-button: 0`, `--ss-radius-badge: 0`.

**Principle 7 — The admin surface can be dense; the client portal cannot.**
The admin user wants speed and accuracy. Marcus is a business owner on a phone after dinner. The portal is spacious, single-task, and never cluttered with admin-visible detail.

---

## 3. Target User Context

### 3.1 Marcus, HVAC Business Owner, Chandler AZ

Marcus runs an HVAC company in the Phoenix metro. He has employees, customers, and more operational decisions than he can comfortably make. He uses QuickBooks, his bank app, Apple Calendar, and Square. He hired SMD Services because something in his business is working against him and he ran out of time to figure out what.

He accesses the portal on his iPhone between service calls, from his truck, in a parking lot in Peoria. Patchy LTE. Possible work gloves. Sun glare.

He checks the portal when an email tells him to — not daily. Typical gap between visits: 4-7 days. The home screen must orient him in under 3 seconds on return.

### 3.2 What Makes Marcus Trust a Firm

Not visual design. He trusts:

- Someone he knows recommended them
- The portal loaded when he tried to use it (reliability = trustworthiness)
- The content is specific to him — his company name, his scope in plain language, real invoice numbers, not placeholder text
- Emails arrive when they're supposed to (within 5 minutes of signing, not 45)
- The parking lot does not feel like a sales tool for follow-on work

### 3.3 Make-or-Break Moments

Six moments where the engagement can succeed or fail based on portal experience alone:

1. **The email that earns the click.** Generic subject, wrong sender, no dollar amount — he doesn't click it. The portal never opens.
2. **The first authenticated screen.** Business name, total, sign button, no scrolling. Must be immediate and specific.
3. **The signing moment.** Cream and ink must be visible around the signing frame. Visual discontinuity at the iframe registers as a phishing signal.
4. **The deposit invoice as a real document.** Not a receipt. Not a screenshot. A PDF with an invoice number, a firm name, an amount in a format he can file.
5. **Coming back after four days.** Home screen orients him in under 10 seconds. What to do. Anything coming up this week.
6. **The parking lot at handoff.** Specific items, human explanations for each disposition, no homework, no hint-list for upsells. If handled right, he refers the firm to peers.

### 3.4 What Marcus Does Not Care About

- ESLint rules, CI gates, ARIA labels, token architecture — these are the team's job, not his.
- The visual design as a standalone thing: "Packaging matters for the first impression. But it's the operational stuff — speed, specificity, reliability, no upselling — that I'll actually remember six months later."

### 3.5 Phone-First Reality Check

The above-fold spec from the Interaction Designer is correct: the pixel budget, the element order, the explicit constraint that the CTA is visible at 375px without scrolling. This is genuine phone-first thinking.

Marcus's invoice concern: "The 'Pay Now' button needs to be reachable without excessive scrolling. Put the pay button near the top too, or make it sticky."

On the signing accordion: if Marcus returns to sign a week after reading the proposal, he may not remember the details. The "Review scope" accordion must be easy to find and open. One tap to expand.

---

## 4. Visual Language

### 4.1 Canonical Palette

Source of truth: `node_modules/@venturecrane/tokens/dist/ss.css`

| Token                        | Hex                   | Role                                                                          |
| ---------------------------- | --------------------- | ----------------------------------------------------------------------------- |
| `--ss-color-background`      | `#f5f0e3`             | Cream paper. Primary page background.                                         |
| `--ss-color-surface`         | `#f5f0e3`             | Card background. Cards defined by hairline borders, not fill.                 |
| `--ss-color-surface-inverse` | `#1a1512`             | Ink. Inverted surfaces.                                                       |
| `--ss-color-border`          | `rgba(26,21,18,0.16)` | Default border. Ink at 16% opacity.                                           |
| `--ss-color-border-subtle`   | `rgba(26,21,18,0.08)` | Subtle divider. Ink at 8% opacity.                                            |
| `--ss-color-text-primary`    | `#1a1512`             | Ink. All primary content text.                                                |
| `--ss-color-text-secondary`  | `#4a423c`             | Subdued ink. Metadata, secondary labels. 8.64:1 on cream. Outdoor-safe.       |
| `--ss-color-text-muted`      | `#8a7f73`             | Muted ink. Non-essential decoration only. 3.0:1 — see §4.3.                   |
| `--ss-color-meta`            | `#4a423c`             | Card timestamps, IDs. Matches text-secondary.                                 |
| `--ss-color-primary`         | `#c5501e`             | Burnt orange. CTAs, primary actions. Single-accent discipline.                |
| `--ss-color-primary-hover`   | `#a84318`             | Burnt orange deepened. CTA hover.                                             |
| `--ss-color-action`          | `#c5501e`             | Focus ring. Same hue as primary.                                              |
| `--ss-color-attention`       | `#c5501e`             | Semantic alias. Same hue as primary.                                          |
| `--ss-color-complete`        | `#4a6b3e`             | Olive. Success and completed states. See §4.5.                                |
| `--ss-color-error`           | `#a02a2a`             | Brick. Error and danger states.                                               |
| `--ss-color-warning`         | `#7a5800`             | **FINAL.** Deep golden amber. Warning, near-expiry, soft-caution. Pending PR. |

### 4.2 WCAG AA Contrast Audit

WCAG 2.1 thresholds: 4.5:1 for normal text, 3.0:1 for large text (18pt+/14pt+ bold) and non-text UI components.

#### Text on cream (`#f5f0e3`)

| Pairing                             | Ratio       | WCAG AA Normal | Notes                                                            |
| ----------------------------------- | ----------- | -------------- | ---------------------------------------------------------------- |
| `text-primary` (#1a1512) on cream   | **15.91:1** | Pass           | AAA                                                              |
| `text-secondary` (#4a423c) on cream | **8.64:1**  | Pass           | AAA. Outdoor-safe. Use for all actionable secondary data.        |
| `text-muted` (#8a7f73) on cream     | **3.0:1**   | Fail           | Decoration only. See §4.3.                                       |
| `primary` (#c5501e) on cream        | **4.06:1**  | Fail           | Inline body text prohibited. Buttons, icons, display scale only. |
| `warning` (#7a5800) on cream        | **7.14:1**  | Pass           | AAA-adjacent.                                                    |
| `complete` (#4a6b3e) on cream       | **5.33:1**  | Pass           | Success text on cream.                                           |
| `error` (#a02a2a) on cream          | **6.45:1**  | Pass           | Error text on cream.                                             |

#### Text on interactive surfaces

| Pairing                              | Ratio       | WCAG AA Normal | Notes                                   |
| ------------------------------------ | ----------- | -------------- | --------------------------------------- |
| White on `primary` (#c5501e)         | **4.63:1**  | Pass           | CTA button text.                        |
| White on `primary-hover` (#a84318)   | **6.03:1**  | Pass           | Hover state.                            |
| White on `complete` (#4a6b3e)        | **6.06:1**  | Pass           | White label on olive surface.           |
| White on `error` (#a02a2a)           | **7.34:1**  | Pass           | AAA.                                    |
| White on `warning` (#7a5800)         | **7.14:1**  | Pass           | AAA. White label on warning background. |
| White on `surface-inverse` (#1a1512) | **18.10:1** | Pass           | AAA.                                    |

### 4.3 Warning Token: `#7a5800` — CONFIRMED FINAL

Both `#7a5800` (Brand Strategist) and `#6b4f08` (Design Technologist Round 2) pass WCAG AA. The choice is a register question.

`#7a5800` is warmer and more yellow-amber. It reads as its own semantic slot — not burnt orange (action), not olive (success), not brick (error). The warmth places it in the "caution, pay attention" register. At low screen brightness, the distinction from olive (`#4a6b3e`) is clearer with `#7a5800`. The AAA margin (7.14:1 vs. 6.72:1) provides additional outdoor robustness.

**Final token value:**

```css
--ss-color-warning: #7a5800; /* Deep golden amber. Warning, near-expiry, soft-caution.
                                7.14:1 on cream (#f5f0e3) — WCAG AAA.
                                Distinct from primary (#c5501e), complete (#4a6b3e), error (#a02a2a). */
```

**Token package JSON (apply to `crane-console/packages/tokens/src/ventures/ss.json`):**

```json
{
  "color": {
    "warning": {
      "$value": "#7a5800",
      "$type": "color",
      "$description": "Deep golden amber. Warning and soft-caution states. 7.14:1 contrast on cream (#f5f0e3). WCAG AAA. Visually distinct from primary (#c5501e), complete (#4a6b3e), and error (#a02a2a)."
    },
    "text": {
      "muted-accessible": {
        "$value": "#6b6158",
        "$type": "color",
        "$description": "Hardened muted alias for mobile outdoor contexts. 4.71:1 on cream — WCAG AA normal text pass. Use when --ss-color-text-muted (3.0:1) is insufficient for outdoor legibility."
      }
    }
  },
  "focus": {
    "ring-width": { "$value": "2px", "$type": "dimension" },
    "ring-offset": { "$value": "2px", "$type": "dimension" }
  }
}
```

Marcus's honest reaction to the warning color: "Warning should be visible enough that I notice it unprompted, but calm enough that I don't feel attacked." He wants someone to put `#7a5800` on a real phone screen in a dim room and verify it reads as "pay attention" rather than "fine to ignore." This is an outstanding real-device test. See Open Decision D-5.

**Warning state rule: never show a warning without an adjacent action.**

If the system cannot pair a warning with a clear action, render the status as neutral metadata rather than a warning.

### 4.4 `text-muted` Usage Rule — FINAL

`--ss-color-text-muted` (`#8a7f73`, 3.0:1 on cream) is **permitted only for:**

| Permitted use                        | Context                                 | Rationale               |
| ------------------------------------ | --------------------------------------- | ----------------------- |
| Timestamp on list row                | User does not need the timestamp to act | Decorative metadata     |
| Placeholder text in unfocused inputs | Replaced by user input on interaction   | Standard a11y exception |
| Decorative separator labels          | Visual rhythm only                      | No information content  |

`--ss-color-text-muted` is **prohibited for:**

- Primary or actionable informational labels at any size
- Status-bearing text of any kind
- Any text a user must read to understand what to do next
- Any text in the portal on mobile where outdoor legibility matters

`--ss-color-text-secondary` (#4a423c, 8.64:1) is the correct token when metadata is functional. Do not add `--ss-color-text-muted-accessible` as a third stop. The two-stop rule is cleaner and less susceptible to misapplication by engineers.

**The `--ss-color-text-muted-accessible: #6b6158` token is included in the token JSON above for completeness, but its use is restricted:** only where `text-secondary` is genuinely too dark for the specific context and a careful engineering review has confirmed the outdoor-legibility exception. It is not a default replacement for `text-muted`.

### 4.5 Olive Usage Hierarchy

Olive is earned, not automatic. The portal will have multiple "complete" signals. Applying olive to all of them creates olive fatigue and dilutes semantic weight.

| Signal                         | Olive?                     | Treatment                                                             | Rationale                                  |
| ------------------------------ | -------------------------- | --------------------------------------------------------------------- | ------------------------------------------ |
| Engagement complete            | Yes — hero moment          | Olive prose at heading scale: "Complete."                             | Once per engagement; the defining event    |
| Quote signed                   | Yes — make-or-break        | Olive prose: "Signed {natural date}."                                 | The signature is the commitment            |
| Deposit paid                   | Yes — financial resolution | "Paid {natural date}." in olive at `text-caption`                     | Payment confirmation is a milestone        |
| Completion invoice paid        | Yes — financial close      | Same as deposit paid                                                  | Symmetric with deposit                     |
| Milestone completed            | No — routine progress      | Ink check icon (`check_circle`, FILL 0, wght 400) in `text-secondary` | Expected; olive for all would dilute       |
| Parking-lot item dispositioned | No — operational close     | "Addressed" / "Deferred" / "Dropped" in `text-secondary`              | Internal resolutions, not client victories |
| Document uploaded              | No — routine               | No indicator; documents are listed, not celebrated                    | Availability is fact                       |
| Invoice line item              | No                         | Neutral metadata; monospaced amount in `text-primary`                 | Financial data is fact                     |

No olive on admin surfaces for routine state. Olive is a client-facing trust signal first.

### 4.6 Single-Accent Discipline

One accent color: burnt orange (`#c5501e`). Action, attention, and primary are aliases of the same hue — intentional. No second decorative accent. Olive is semantic (success only). Brick is semantic (error only). Warning (`#7a5800`) is semantic (caution only — not for decorative highlight).

Any PR introducing a color not derived from the canonical hex values requires explicit design review with a named rationale.

### 4.7 Typography

Three fonts. Locked. Font changes require a named identity migration, not a PR.

| Token                    | Value                                                              | Role                                                     |
| ------------------------ | ------------------------------------------------------------------ | -------------------------------------------------------- |
| `--ss-font-display`      | `'Archivo', system-ui, sans-serif`                                 | All h1/h2/h3, display headings                           |
| `--ss-font-body`         | `'Archivo', system-ui, sans-serif`                                 | Body copy, form labels, button text                      |
| `--ss-font-accent-label` | `'Archivo Narrow', 'Archivo', system-ui, sans-serif`               | Chips, tab labels, compact metadata                      |
| `--ss-font-mono`         | `'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace` | IDs (ULIDs), invoice reference numbers, fixed-width data |

Display and body share Archivo. Hierarchy is weight-driven, not family-driven.

**Functional Scale (7 tokens):**

| Token          | Size (px) | Line-height (px) | Weight | Letter-spacing     | Use                                                 |
| -------------- | --------- | ---------------- | ------ | ------------------ | --------------------------------------------------- |
| `text-display` | 48        | 54               | 500    | -0.01em            | Page-level hero (non-display-register contexts)     |
| `text-title`   | 28        | 34               | 500    | -0.005em           | Section heading, card title                         |
| `text-heading` | 18        | 24               | 600    | —                  | Sub-section heading                                 |
| `text-body-lg` | 17        | 26               | 400    | —                  | Lead paragraph, introductory copy                   |
| `text-body`    | 16        | 25               | 400    | —                  | Default body text                                   |
| `text-caption` | 14        | 20               | 500    | —                  | Metadata, dates, status prose                       |
| `text-label`   | 12        | 16               | 500    | 0.08em (uppercase) | Eyebrow, section label, tab labels (Archivo Narrow) |

**Plainspoken Display Scale (weight-900 register):**

| Token              | Size (px) | Weight  | Use                                           |
| ------------------ | --------- | ------- | --------------------------------------------- |
| `text-hero`        | 72        | **900** | Portal H1 desktop (engagement title)          |
| `text-hero-mobile` | 44        | **900** | Portal H1 mobile                              |
| `text-hero-price`  | 64        | **900** | Summary-card total project price              |
| `text-kpi`         | 44        | **900** | KPI numbers in admin dashboard                |
| `text-section-h`   | 36        | **900** | Section block headings on detail pages        |
| `text-price-row`   | 28        | **900** | Row-level money in list/ledger contexts       |
| `text-num-cell`    | 22        | **900** | ID/sequence cell glyphs in ticket rows        |
| `text-money`       | 44        | 500     | MoneyDisplay component (list-context amounts) |

Display tokens (weight 900, negative tracking) apply only to: portal engagement title, total project price, invoice total in document header, admin KPI dashboard numbers. Everything else uses functional tokens.

**JetBrains Mono is strictly for:** ULID values, invoice reference numbers, code values, fixed-width data where column alignment matters. Not a stylistic treatment for prices.

### 4.8 Spacing & Rhythm

**Documentation correction required:** `UI-PATTERNS.md` Rule 6 documents `space-section: 32px` and `space-card: 24px`. Compiled token values are `48px` and `32px`. The compiled values are authoritative. Correct in the next PR touching `UI-PATTERNS.md`. This must not be deferred again.

| Token           | CSS var              | px     | Use                               |
| --------------- | -------------------- | ------ | --------------------------------- |
| `space-section` | `--ss-space-section` | **48** | Gap between major page sections   |
| `space-card`    | `--ss-space-card`    | **32** | Card internal padding             |
| `space-stack`   | `--ss-space-stack`   | 16     | Vertical stack of sibling content |
| `space-row`     | `--ss-space-row`     | 12     | Gap between rows in a list        |

Raw Tailwind spacing utilities (`p-6`, `gap-4`) are banned in governed surfaces.

### 4.9 Shape: Zero Radii

All radii are `0`. Binding and canonical.

| Token                | Value |
| -------------------- | ----- |
| `--ss-radius-card`   | 0     |
| `--ss-radius-button` | 0     |
| `--ss-radius-badge`  | 0     |

Any PR introducing rounding to a card, button, or badge requires explicit design review. The PR description must explain the rationale and demonstrate that the Plainspoken register is preserved.

### 4.10 Motion

Motion tokens required before Phase 2 ships:

| Token                          | Duration |
| ------------------------------ | -------- |
| `--ss-motion-duration-instant` | 0ms      |
| `--ss-motion-duration-fast`    | 150ms    |
| `--ss-motion-duration-base`    | 250ms    |
| `--ss-motion-duration-slow`    | 400ms    |

| Token                           | Value                              |
| ------------------------------- | ---------------------------------- |
| `--ss-motion-easing-standard`   | `cubic-bezier(0.4, 0.0, 0.2, 1)`   |
| `--ss-motion-easing-decelerate` | `cubic-bezier(0.0, 0.0, 0.2, 1)`   |
| `--ss-motion-easing-accelerate` | `cubic-bezier(0.4, 0.0, 1.0, 1.0)` |

**`@theme inline` additions to `global.css`:**

```css
@theme inline {
  --duration-instant: var(--ss-motion-duration-instant);
  --duration-fast: var(--ss-motion-duration-fast);
  --duration-base: var(--ss-motion-duration-base);
  --duration-slow: var(--ss-motion-duration-slow);
  --ease-standard: var(--ss-motion-easing-standard);
  --ease-decelerate: var(--ss-motion-easing-decelerate);
  --ease-accelerate: var(--ss-motion-easing-accelerate);
}
```

**Required `prefers-reduced-motion` block (brand-required, not just a11y-required):**

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

0.01ms, not 0ms: avoids a Safari state-transition rendering bug. `scroll-behavior: auto` disables smooth scroll for vestibular safety.

**Motion usage table:**

| Use case                               | Duration     | Easing     |
| -------------------------------------- | ------------ | ---------- |
| Hover color change                     | fast (150ms) | standard   |
| Focus ring appearance                  | fast (150ms) | standard   |
| Tab active state transition            | fast (150ms) | standard   |
| Disclosure/accordion open              | base (250ms) | decelerate |
| Confirmation panel swap (post-signing) | base (250ms) | standard   |

**What does not animate:** StatusPill tone changes, MoneyDisplay values, list rows on page load, error messages (must be instant per `aria-live="assertive"` semantics).

### 4.11 Icon System — Material Symbols Outlined

Material Symbols Outlined is the canonical icon system. No custom icon sets in MVP. Every icon must exist in Material Symbols Outlined.

```css
.material-symbols-outlined {
  font-family: 'Material Symbols Outlined';
  font-variation-settings:
    'FILL' 0,
    'wght' 400,
    'GRAD' 0,
    'opsz' 24;
}
```

**Axis settings by context:**

| Context                       | FILL | wght | opsz |
| ----------------------------- | ---- | ---- | ---- |
| Default (body-adjacent)       | 0    | 400  | 24   |
| Mobile tab bar (20px display) | 0    | 400  | 20   |
| Active tab icon               | 1    | 400  | 20   |
| Heading-adjacent              | 0    | 600  | 24   |
| Caption-adjacent              | 0    | 300  | 24   |
| Display-context (>32px)       | 0    | 400  | 40   |

**Canonical tab icon set — FINAL:**

Icons + labels always visible on all viewports and screen sizes. No icon-only tab, ever. Authority: NN/g "Icon Usability" (https://www.nngroup.com/articles/icon-usability/).

> Marcus: "If I have to guess what a tab does by looking at an icon, you've lost me."

| Route                | Tab label | Icon (inactive FILL 0) | Icon (active FILL 1)  | Rationale                                                               |
| -------------------- | --------- | ---------------------- | --------------------- | ----------------------------------------------------------------------- |
| `/portal`            | Home      | `home`                 | `home` filled         | Universal affordance. No label ambiguity.                               |
| `/portal/quotes`     | Proposals | `description`          | `description` filled  | Document-with-lines register. Proposal = a document to review and sign. |
| `/portal/invoices`   | Invoices  | `receipt_long`         | `receipt_long` filled | Receipt register. Distinct from document.                               |
| `/portal/engagement` | Progress  | `assignment`           | `assignment` filled   | Checklist/task register. Engagement timeline.                           |
| `/portal/documents`  | Documents | `folder_open`          | `folder_open` filled  | Library register. Fifth tab, conditional on document existence.         |

**Why `description` for Proposals and `receipt_long` for Invoices (not the reverse):** `description` shows a document with text lines — the register of "a document I need to read." `receipt_long` shows a receipt with a curled bottom edge — the register of a financial transaction record.

**Label visibility rule:**

- Mobile: icon above label. Both always visible.
- Desktop: icon left of label. Both always visible.
- Active: icon FILL 1, label `--ss-color-primary`. Inactive: icon FILL 0, label `--ss-color-text-secondary`.

**Warning state icons:**

| State                            | Icon                                          | Token                |
| -------------------------------- | --------------------------------------------- | -------------------- |
| Quote near expiry                | `schedule` (wght 400, opsz 24, FILL 0)        | `--ss-color-warning` |
| Deposit overdue                  | `receipt_long` (wght 400, opsz 24, FILL 0)    | `--ss-color-warning` |
| Parking lot stale (admin only)   | `pending_actions` (wght 400, opsz 24, FILL 0) | `--ss-color-warning` |
| Financial blindness (admin only) | `visibility_off` (wght 400, opsz 24, FILL 0)  | `--ss-color-warning` |

### 4.12 Photography and Imagery

**Portal and admin: no photography ever.** This extends to: office photos, founder or team portraits, client logos (not even as trust badges), certifications, awards, review counts displayed as images, stock photography of any kind.

Trust badges are included in this prohibition. If trust credentials are warranted, render them as text ("Google Workspace Partner" in `text-label` at Archivo Narrow), not as a badge image.

**Marketing site photography: open question** — see Open Decision D-4.

No illustration anywhere. Empty states render nothing or a "TBD in SOW" marker — never an illustration of a person looking at a clipboard.

**Third-party embed surfaces (SignWell, Stripe):** We do not attempt to restyle cross-origin iframes. Our response: minimize the embed footprint, frame with our chrome, and prime Marcus for the visual shift with a mandatory disclosure line before the iframe: "The signing form below is provided by SignWell, a document-signing service."

> Marcus: "If the portal sends me to a signing surface that doesn't look like the rest of the portal, my gut reaction is that I've been redirected somewhere I didn't intend to go. That is the exact visual experience of a phishing scam."

---

## 5. Screen Inventory & Key Screens

### 5.1 `smd.services` — Marketing (Public)

| URL                    | Purpose               | Primary Action       | Status |
| ---------------------- | --------------------- | -------------------- | ------ |
| `/`                    | Marketing home        | Book assessment      | Exists |
| `/get-started`         | Warm landing          | Book assessment      | Exists |
| `/scorecard`           | Self-serve assessment | Complete & book      | Exists |
| `/book`                | Booking form          | Confirm booking      | Exists |
| `/book/manage/[token]` | Booking management    | Reschedule or cancel | Exists |
| `/contact`             | General inquiry       | Submit inquiry       | Exists |
| `/404`                 | Not-found             | Back to home         | Exists |

**Auth entry points:**

| URL                  | Purpose                     | Primary Action | Status |
| -------------------- | --------------------------- | -------------- | ------ |
| `/auth/login`        | Admin login                 | Sign in        | Exists |
| `/auth/portal-login` | Client magic-link request   | Request link   | Exists |
| `/auth/verify`       | Token consumption + session | Auto-redirect  | Exists |

### 5.2 `portal.smd.services` — Client Portal

| URL                                       | Purpose                                      | Primary Action             | Status                                         |
| ----------------------------------------- | -------------------------------------------- | -------------------------- | ---------------------------------------------- |
| `/portal`                                 | Dashboard — state-responsive home            | Review & Sign or Pay Now   | Exists                                         |
| `/portal/quotes`                          | Proposal list                                | Row click to detail        | Exists                                         |
| `/portal/quotes/[id]`                     | Quote detail — 5-state machine               | Review & Sign (sent state) | Exists                                         |
| `/portal/quotes/[id]` → pre-signing state | Pre-signing prep screen                      | Gate → reveal iframe       | New (`PreSigningPrep.astro`)                   |
| `/portal/quotes/[id]` → signing state     | Signing surface — framed SignWell iframe     | SignWell iframe action     | New (`SigningView.astro`)                      |
| `/portal/invoices`                        | Invoice list                                 | Row click to detail        | Exists                                         |
| `/portal/invoices/[id]`                   | Invoice detail — professional invoice format | Pay Now (unpaid)           | Exists (needs update)                          |
| `/portal/documents`                       | Document library                             | Download / Open            | Exists                                         |
| `/portal/engagement`                      | Engagement progress, parking lot             | (informational)            | Exists (portal); New (`ParkingLotPanel.astro`) |
| Magic-link recovery                       | Expired/used token recovery                  | Request new link           | New (`MagicLinkExpiredForm.astro`)             |

**Note A — Signing route:** PRD §9 lists `/portal/quotes/[id]/sign` as a distinct route. Current implementation renders signing as a state within `quotes/[id]`. See Open Decision D-1.

**Champion access (UX-001): DEFERRED to Phase 4.** Architecture must not foreclose this option. See §6.10.

### 5.3 `admin.smd.services` — Admin Console

| URL                                         | Purpose                                                             | Primary Action                | Status                                       |
| ------------------------------------------- | ------------------------------------------------------------------- | ----------------------------- | -------------------------------------------- |
| `/admin`                                    | Dashboard — overdue follow-ups, pending signatures                  | Triage                        | Exists                                       |
| `/admin/pipeline`                           | Pipeline kanban — Prospect / Assessed / Quoted / Active / Completed | Navigate to entity            | New (components); Exists (entities list)     |
| `/admin/entities`                           | Client list                                                         | New entity                    | Exists                                       |
| `/admin/entities/[id]`                      | Client/entity detail                                                | New assessment / New quote    | Exists                                       |
| `/admin/entities/[id]/quotes/[quoteId]`     | Quote builder                                                       | Generate SOW / Send to Client | Exists (page); New (components)              |
| `/admin/entities/[id]/meetings/[meetingId]` | Assessment/meeting detail                                           | Complete assessment           | Exists (page); New (`ExtractionPanel.astro`) |
| `/admin/engagements/[id]`                   | Engagement lifecycle                                                | Log time / Advance status     | Exists (page); New (components)              |
| `/admin/follow-ups`                         | Follow-up queue                                                     | Complete / Skip               | Exists (page); New (`FollowUpCard.astro`)    |
| `/admin/analytics`                          | Reports                                                             | (read-only)                   | Exists                                       |
| `/admin/generators`                         | Content generator catalog                                           | Run generator                 | Exists                                       |
| `/admin/settings/google-connect`            | Google Calendar / Drive OAuth                                       | Connect                       | Exists                                       |

### 5.4 Portal Dashboard — Key Screen Breakdown (`/portal`)

**Above-the-fold contract (375px, State 1 — pre-signature):**

```
PortalHeader (masthead, client name)            44px
─────────────────────────────────────────────
Last activity banner (returning user only)      ~28px
─────────────────────────────────────────────
Eyebrow: "{BUSINESS NAME}" (text-label)         20px
Engagement title (text-hero-mobile, w900)       48px
Scope summary (text-body-lg, 2-3 lines)         ~60px
Project total (MoneyDisplay hero, w900)         72px
Payment structure caption (text-caption)        20px
"Review and sign" primary CTA (full-width)      52px
─────────────────────────────────────────────
Total with rhythm overhead                      ~416px within 564px usable
```

CTA top edge must sit at y ≤ 560px within the ~564px usable area.

**Stale visit recovery — "Last activity" banner:**

When Marcus returns after 4+ days, the home screen must orient him in under 3 seconds.

- Placement: directly below `PortalHeader`, above the primary engagement card. Full-width, cream surface, hairline bottom border.
- Time expression: relative ("4 days ago", "yesterday"). No ISO timestamps.
- Event description: authored prose from `engagement_events` log — not a system-generated label. If `last_event.prose` is null, render nothing.
- First session: omit entirely.

**"What's new since your last visit" section:**

- Rendered below primary engagement card.
- Heading: "Since your last visit" (text-heading, weight 600). Suppressed if never logged in before.
- Content: chronological list of authored events since `session.last_seen_at`. Each event is a `TimelineEntry` with authored prose and natural date.
- Empty state: cream paper. No content. No "You're all caught up!" The absence of new items is the information.
- Max visible: 4 items. "Show all activity" tertiary link if more.

**Marcus's addition for the stale-visit header:** If there is a milestone or scheduled touchpoint coming up this week, surface it in one sentence on the header. "Kickoff review coming up Thursday." If nothing is scheduled, say nothing. See Open Decision D-6.

### 5.5 Pre-Signing Prep Screen (`PreSigningPrep.astro` — New)

Before the SignWell iframe loads, Marcus sees a full-screen summary of what he is about to sign. The "Continue to signing" button is visible but disabled until he scrolls past the summary content.

**Screen structure:**

```
PortalHeader (visible, cream)
Breadcrumb: "← Proposals / {Engagement Title}"
──────────────────────────────────────────────
"BEFORE YOU SIGN" (text-label, mono caps, hairline underline)

Summary block:
  Project total (MoneyDisplay, hero size, weight 900)
  Payment structure (authored: "$3,500 now · $3,500 at completion")
  Hairline rule

"WHAT'S INCLUDED" (text-label, mono caps)
  Each deliverable: authored line_items.title and 1-sentence description
  If empty: "Deliverables are detailed in the full proposal above."

"WHAT YOU'RE AGREEING TO" (text-label, mono caps)
  Two authored sentences (Captain-reviewed system config)
  Maximum two sentences. Not a wall of legalese.

Scroll anchor: #sign-anchor (invisible) at bottom of block

"Continue to signing →" primary CTA
  - Disabled (aria-disabled="true") until IntersectionObserver fires
  - Enabled: after #sign-anchor enters viewport
  - Progressive enhancement: if JS disabled, button is always-enabled
  - Adjacent explainer when disabled: "Scroll to review the summary above, then continue."
```

**Why not a modal:** A confirmation modal is a last-second interruption. The prep screen is a preparation space. Marcus reads at his own pace, then proceeds.

### 5.6 Framed Signing View (`SigningView.astro` — New)

Portal chrome (PortalHeader) remains visible. Pre-signing disclosure line above the iframe is mandatory:

```html
<p class="text-caption text-text-secondary">
  The signing form below is provided by SignWell, a document-signing service.
</p>
```

Mobile: `<details>/<summary>` accordion ("Review scope") collapsed by default, above the iframe. Iframe height: `calc(100dvh - 7rem)` with `calc(100vh - 7rem)` fallback.

Desktop: two-column. Scope summary sidebar (sticky, max-width 280px) left. Iframe (flex-1) right.

### 5.7 Parking Lot Panel (`ParkingLotPanel.astro` — New)

Each parking lot item requires four authored fields before it appears to the client. If any field is missing, the item is not shown.

| Field              | Who authors            | Rules                                                                                     |
| ------------------ | ---------------------- | ----------------------------------------------------------------------------------------- |
| `title`            | Admin at item creation | Plain language. "Job completion tracking" not "scope_expansion_002."                      |
| `decision_framing` | Admin at review        | 1-2 sentences in client's language framing the choice                                     |
| `options`          | Admin at review        | 2-3 objects: `{label, rationale}`. Each in plain language with 1-sentence rationale.      |
| `disposition`      | Admin at disposition   | Human label: "decided" / "deferred_to_call" / "out_of_scope"                              |
| `disposition_note` | Admin at disposition   | 1-2 sentences required for "decided" and "out_of_scope". Optional for "deferred_to_call". |

**Render gate:** Portal shows parking lot only at `engagement.status = complete` or when admin has explicitly flagged items `visible_to_client = true`.

**Empty state:** If no items meet the visibility and authoring gates, the section does not render. No "Nothing here yet."

**Admin stale warning:** Disposition = null AND `requested_at` > 14 days → "STALE" tag in `--ss-color-warning` on the item row. Admin only; Marcus never sees "STALE" labels.

---

## 6. Interaction Patterns

### 6.1 Navigation Model

**Portal (mobile-first):** Bottom tab bar, fixed, 64px height minimum. Persistent labels under every icon at all viewports. No hamburger, no sidebar, no drawer.

Tab visibility gating: Home always shown. Proposals: ≥1 quote exists. Invoices: ≥1 invoice exists. Progress: active engagement exists. Documents: ≥1 document uploaded.

**Admin (desktop-first):** Sticky top nav, ≥1024px primary. Hub-and-spoke with breadcrumbs. Below 1024px: informational banner "ADMIN IS BEST VIEWED ON A WIDER SCREEN" (not blocking). Critical admin actions remain mobile-functional at 375px.

Critical mobile-functional admin actions:

- Disposition a parking lot item
- Send a new magic link
- Mark a quote as sent
- View engagement status

### 6.2 Warning State Rendering

| State                            | Screen                               | Visual spec                                                                                                                                                                      |
| -------------------------------- | ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Quote near expiry (<72hr)        | `/portal/quotes/[id]` (isSent state) | Full-width callout strip, hairline left-border (3px solid warning), "PROPOSAL EXPIRES {day, date at time}", `role="alert"`. Paired with "Review and sign" CTA immediately below. |
| Deposit overdue                  | `/portal/invoices/[id]` (isOverdue)  | Callout strip above "Pay Now" CTA, "OVERDUE — DUE {natural date}".                                                                                                               |
| Deposit overdue (dashboard chip) | `/portal` State 2                    | `StatusPill` compact variant, `--ss-color-warning` text on cream. Label: "OVERDUE".                                                                                              |
| Parking lot stale (admin only)   | `/admin/engagements/[id]`            | Section header chip "X items need review" in warning color. Individual row tag "STALE".                                                                                          |

### 6.3 Form Patterns

**Inputs:** Full-width, border `--ss-color-border`, 0 radius, text-body, text-primary, surface background. Label above input always (WCAG §4.1). Required fields: "(required)" in label text.

**Focus state:**

```css
:focus-visible {
  outline: var(--ss-focus-ring-width, 2px) solid var(--ss-color-action);
  outline-offset: var(--ss-focus-ring-offset, 2px);
}
```

`--ss-color-action` (#c5501e, 4.06:1 against cream) satisfies WCAG 2.2 SC 2.4.11 (3:1 minimum for focus appearance).

**Validation timing:** On submit for required fields. On blur for format-critical fields (email, phone). Real-time for quote builder line item totals.

**Error placement:** Field-level: directly below input, text-caption, error color, `aria-describedby`, complete sentence. Form-level (admin): top-of-form strip. Portal single-field forms: inline below input.

**Touch target floor (WCAG 2.5.5 AAA): 44×44px.**

| Element                   | Minimum size                        |
| ------------------------- | ----------------------------------- |
| Tab bar items             | 64px height (full bar tappable)     |
| Primary CTA buttons       | 52px height, full-width             |
| Ghost / secondary buttons | 44px height minimum                 |
| List row tap targets      | Full-row width, 56px height minimum |
| Back / crumb links        | 44px height via padding             |
| Icon buttons in header    | 44px × 44px                         |

**Mobile keyboard behavior (Quote Builder React island):**

- `inputmode="decimal"` for money fields (shows numeric keypad with decimal point on iOS)
- `inputmode="numeric"` for integer hours
- Scroll-into-view on focus (300ms delay for keyboard animation)
- `autocomplete="off"` for currency and date inputs

### 6.4 Feedback Patterns

**Toast style:** Bottom-center on mobile (above tab bar), top-right on desktop. 4 seconds auto-dismiss. Past tense, concrete text: "Link sent." not "Sent successfully!" No celebratory animation.

**Signing confirmation:** In-page prose replacement (isSigned state IS the confirmation). No toast.

**Destructive confirmations:** Inline-expand within action row or narrowly scoped dialog. Not full-page modal. Two actions: destructive CTA (error fill, specific label) + neutral "Go back". Never auto-confirm.

**Progress indicators:** Skeleton at section level (portal), skeleton at panel level (admin). Form submit: disable button, replace label with spinner + "Sending...".

### 6.5 Email Touchpoint Inventory

Eight transactional emails. Trust signal requirements (all):

- From: `team@smd.services`
- Reply-to: authored consultant email
- Plain-text fallback required
- No marketing graphics, no unsubscribe footer
- Subject includes client business name or direct reference to prior interaction

**Email timing:**

| #   | Email                    | Timing                     | Batch? |
| --- | ------------------------ | -------------------------- | ------ |
| 1   | Portal invitation        | Immediate on trigger       | No     |
| 2   | Proposal signed          | Within 5 min of webhook    | No     |
| 3   | Countersigned / firm ACK | Within 5 min of trigger    | No     |
| 4   | Invoice issued           | Immediate                  | No     |
| 5   | Payment confirmed        | Within 5 min of webhook    | No     |
| 6   | Parking lot item         | Daily at 9am (all pending) | Yes    |
| 7   | Engagement complete      | Immediate on trigger       | No     |
| 8   | Magic link re-auth       | Immediate                  | No     |

**Three content layers per email:**

- **Layer 1 — System-prefilled:** Client name, business name, dollar amounts, invoice number, dates, portal URLs, magic link URLs. Never fabricated.
- **Layer 2 — Admin-authored per engagement:** Scope reference (Email 1), "what happens next" text (Email 3), parking lot framing (Email 6), milestone names (Email 7). Authored by admin when setting up or advancing the engagement.
- **Layer 3 — Captain-reviewed system config:** Surrounding prose templates. Authored once, reviewed by Captain, stored as system config. Voice rules apply (no em dashes, no parallel structures, no AI phrasing, no fixed timeframes). **If a Layer 3 template is missing, the email sends with Layer 1 + Layer 2 only. No fabricated filler.**

**Email 3 (countersigned) conditional send:** Email 3 should only send when `engagement.next_step_text` is authored. If the field is null, suppress Email 3 — an email with no "what happens next" content is noise.

### 6.6 Pre-Signing Data Integrity Constraint

**This is a hard constraint, not a preference.**

The pre-signing summary screen must source data from the **same database record** that generates the SignWell document. Not a cached value. Not a different field. Not whatever the portal record happened to say at invitation time.

> Marcus: "I'm going to notice. And when I notice, I'm not going to sign. I'm going to close the tab and call someone."

If any chance exists that the portal summary can get out of sync with the actual document — a line item added after invitation, a price update that didn't propagate — the engagement can fail at the signing moment. This is a system design constraint that must be confirmed resolved before the pre-signing prep screen ships.

**Implementation requirement:** Both the pre-signing summary and the SignWell document must derive from the same query against the same source-of-truth records (`quotes.total_cents`, `line_items`, `payment_terms`). No denormalized cache, no separate portal-side storage of quote values that could drift.

This constraint belongs in both the `SigningView.astro` component spec and in the quote API route that generates the SignWell embed URL.

### 6.7 ACH Payment Path

Marcus may prefer ACH for large invoices to avoid credit card fees. The `Pay Now` button must lead to a Stripe page where ACH is available as an option (per BR-036). The portal UI should not assume card-only payment. See Open Decision D-7.

### 6.8 Invoice Pay Button Position

Marcus's concern: "If the invoice is long and the pay button is at the very bottom, I'm going to scroll past it, wonder where it is, scroll back down."

**Requirement:** The "Pay Now — $amount" button appears near the top of the invoice detail page (before the line items) AND at the bottom (after). Or the button is sticky as the user scrolls. The bottom-only position is insufficient for a long invoice.

### 6.9 Responsive Strategy

**Portal — mobile-first:**

- Primary breakpoint: 375px (single-column throughout)
- Persistent tab bar fixed at bottom, 64px
- Content scrolls behind tab bar — bottom padding = tab bar height + `space-stack` (16px)
- No horizontal scroll (WCAG 1.4.10 reflow)
- Desktop adaptation (1280px): max content width 1040px centered; two-column layout on dashboard and quote detail; tab bar migrates to horizontal strip below masthead

**Admin — desktop-first:**

- Primary breakpoint: ≥1024px
- Sticky top nav; side-by-side panels on entity and engagement detail
- Below 1024px: informational banner; four critical actions remain mobile-functional

### 6.10 Champion Access (Phase 4 Architecture Preservation)

Maria is Marcus's operations manager. She needs engagement status without seeing pricing or payment history.

**What Phase 1-3 must NOT do:**

- Hardcode `session.role === 'client'` checks that preclude a second role value
- Remove `is_champion` from `engagement_contacts`
- Design the URL structure to assume a single authenticated user per engagement
- Build Invoices or Proposals tabs in a way that cannot be conditionally suppressed by role at the Astro SSR layer

---

## 7. Component System Direction

### 7.1 Portal Components

| Name                         | Status                | File                                               | Change required                                                       |
| ---------------------------- | --------------------- | -------------------------------------------------- | --------------------------------------------------------------------- |
| `PortalHeader.astro`         | Exists                | `src/components/portal/PortalHeader.astro`         | Add `data-portal-header` to root element                              |
| `PortalTabs.astro`           | Exists (needs update) | `src/components/portal/PortalTabs.astro`           | Icon prop interface; updated tabs array; FILL toggling                |
| `PortalPageHead.astro`       | Exists                | `src/components/portal/PortalPageHead.astro`       | None                                                                  |
| `PortalListItem.astro`       | Exists                | `src/components/portal/PortalListItem.astro`       | None                                                                  |
| `StatusPill.astro`           | Exists                | `src/components/portal/StatusPill.astro`           | None                                                                  |
| `MoneyDisplay.astro`         | Exists                | `src/components/portal/MoneyDisplay.astro`         | None                                                                  |
| `ActionCard.astro`           | Exists                | `src/components/portal/ActionCard.astro`           | Confirm `min-h-[44px]` on CTA                                         |
| `ConsultantBlock.astro`      | Exists                | `src/components/portal/ConsultantBlock.astro`      | Add `width={120} height={120}` on `<img>` for CLS                     |
| `EngagementProgress.astro`   | Exists                | `src/components/portal/EngagementProgress.astro`   | Confirm timeline `<ol role="list">` for iOS VoiceOver                 |
| `PortalHomeDashboard.astro`  | Exists                | `src/components/portal/PortalHomeDashboard.astro`  | None                                                                  |
| `QuoteDetail.astro`          | Exists (needs update) | `src/components/portal/QuoteDetail.astro`          | Pattern 01 violation line 207-210; Pattern 02 violation lines 458-497 |
| `InvoiceDetail.astro`        | Exists (needs update) | `src/components/portal/InvoiceDetail.astro`        | Print stylesheet; Pattern 02 triple-redundancy lines 450-461          |
| `InvoicesList.astro`         | Exists                | `src/components/portal/InvoicesList.astro`         | None                                                                  |
| `QuoteList.astro`            | Exists                | `src/components/portal/QuoteList.astro`            | None                                                                  |
| `Documents.astro`            | Exists                | `src/components/portal/Documents.astro`            | None                                                                  |
| `TimelineEntry.astro`        | Exists                | `src/components/portal/TimelineEntry.astro`        | None                                                                  |
| `ArtifactChip.astro`         | Exists                | `src/components/portal/ArtifactChip.astro`         | None                                                                  |
| `SigningView.astro`          | **New**               | `src/components/portal/SigningView.astro`          | Full spec in §7.4                                                     |
| `PreSigningPrep.astro`       | **New**               | `src/components/portal/PreSigningPrep.astro`       | Full spec in §5.5                                                     |
| `MagicLinkExpiredForm.astro` | **New**               | `src/components/portal/MagicLinkExpiredForm.astro` | See §7.5                                                              |
| `ParkingLotPanel.astro`      | **New**               | `src/components/portal/ParkingLotPanel.astro`      | See §7.6                                                              |

### 7.2 Admin Components

| Name                          | Status  | File                                               | Change required          |
| ----------------------------- | ------- | -------------------------------------------------- | ------------------------ |
| `EnrichmentStatusPanel.astro` | Exists  | `src/components/admin/EnrichmentStatusPanel.astro` | None                     |
| `LogReplyDialog.astro`        | Exists  | `src/components/admin/LogReplyDialog.astro`        | None                     |
| `PipelineKanban.astro`        | **New** | `src/components/admin/PipelineKanban.astro`        | See §7.7                 |
| `ClientCard.astro`            | **New** | `src/components/admin/ClientCard.astro`            | See §7.8                 |
| `QuoteLineItemEditor.tsx`     | **New** | `src/components/admin/QuoteLineItemEditor.tsx`     | React island; admin-only |
| `SOWPreviewPane.astro`        | **New** | `src/components/admin/SOWPreviewPane.astro`        | See §7.9                 |
| `FollowUpCard.astro`          | **New** | `src/components/admin/FollowUpCard.astro`          | See §7.10                |
| `TimeEntryLog.astro`          | **New** | `src/components/admin/TimeEntryLog.astro`          | See §7.11                |
| `ExtractionPanel.astro`       | **New** | `src/components/admin/ExtractionPanel.astro`       | See §7.12                |

### 7.3 Shared and Email Components

| Name                       | Status                | File                                   | Change required                                     |
| -------------------------- | --------------------- | -------------------------------------- | --------------------------------------------------- |
| `CtaButton.astro`          | Exists (needs update) | `src/components/CtaButton.astro`       | Add `disabled` prop + ARIA; add destructive variant |
| `SkipToMain.astro`         | Exists                | `src/components/SkipToMain.astro`      | Add `data-skip-link` for print stylesheet           |
| Magic link email template  | Exists (needs update) | `src/lib/email/templates.ts`           | Replace blue/slate palette with `EMAIL_TOKENS`      |
| Portal invitation email    | Exists (needs update) | `src/lib/email/templates.ts`           | Replace blue/slate palette with `EMAIL_TOKENS`      |
| Booking confirmation email | Exists (needs update) | `src/lib/email/booking-emails.ts`      | Replace blue/slate palette with `EMAIL_TOKENS`      |
| Follow-up email series     | Exists (needs update) | `src/lib/email/follow-up-templates.ts` | Replace blue/slate palette with `EMAIL_TOKENS`      |

### 7.4 `SigningView.astro` — Full Spec (New)

**Props interface:**

```ts
interface Props {
  quoteId: string
  signWellEmbedUrl: string
  postSignRedirectHref: string
  isSigned: boolean
  signedPdfHref?: string | null
  quote: {
    engagementTitle: string
    totalCents: number
    depositCents: number
    paymentSplitText: string
    deliverables: Array<{ title: string; body: string }>
  }
}
```

**ARIA:**

| Element                     | Attribute   | Value                                                  |
| --------------------------- | ----------- | ------------------------------------------------------ |
| Outer iframe container      | `aria-busy` | `"true"` while loading; removed on iframe `load` event |
| `<iframe>`                  | `title`     | `"Sign proposal"`                                      |
| Post-sign panel             | `aria-live` | `"assertive"`                                          |
| Scope accordion `<summary>` | —           | Native `<details>/<summary>` provides disclosure role  |

**States:** loading / ready / error (30s timeout → "The signing document isn't available right now…") / signed.

### 7.5 `MagicLinkExpiredForm.astro` — Spec (New)

```ts
interface Props {
  errorType: 'expired' | 'used'
  prefillEmail?: string
}
```

States: idle / submitting / success ("Check your email for a new link." — `role="status" aria-live="polite"`) / error (format validation).

### 7.6 `ParkingLotPanel.astro` — Spec (New)

```ts
interface ParkingLotItem {
  id: string
  title: string
  decision_framing: string
  options: Array<{ label: string; rationale: string }>
  disposition: 'decided' | 'deferred_to_call' | 'out_of_scope' | null
  dispositionNote?: string | null
  requestedAt: Date
}

interface Props {
  items: ParkingLotItem[]
  readonly: boolean
}
```

**Disposition colors:**

- `decided`: `--ss-color-complete` (olive)
- `deferred_to_call`: `--ss-color-text-secondary` (subdued ink)
- `out_of_scope`: `--ss-color-text-muted` (muted ink)

ARIA: `role="region" aria-label="Parking lot items"`. Items as `<li>` within `<ul role="list">`.

### 7.7 `PipelineKanban.astro` — Spec (New)

Columns: prospect / assessed / quoted / active / completed. Each column: `role="region" aria-label="{status} column"`. Each card: `<article>`. Overdue state: "OVERDUE" visible text label (not color-only).

### 7.8 `ClientCard.astro` — Spec (New)

`isOverdue` renders as visible "OVERDUE" text in `--ss-color-error` within the card. Card is wrapped in `<a href={href}>`. `nextAction` is rendered only if authored; never fabricated.

### 7.9 `SOWPreviewPane.astro` — Spec (New)

`role="region" aria-label="SOW preview"`. Empty state (`sowData === null`): "SOW preview will appear here when line items are added." No illustration.

### 7.10 `FollowUpCard.astro` — Spec (New)

Urgency conveyed by visible text label ("OVERDUE", "DUE TODAY") in addition to color. Colors: `--ss-color-error` for overdue, `--ss-color-warning` for due-today, `--ss-color-text-secondary` for upcoming.

### 7.11 `TimeEntryLog.astro` — Spec (New)

`<table>` with `<thead>`, `scope="col"` on each `<th>`, `<caption>` "Time entries". Empty state: "No time entries yet." plain text, no illustration.

### 7.12 `ExtractionPanel.astro` — Spec (New)

`role="region" aria-label="Assessment extraction"`. `aria-busy="true"` while running. Error: `aria-live="assertive"`. Problem identifiers render as `problemLabel` (human-readable), never as raw `problemId` enum keys.

### 7.13 `CtaButton.astro` — Update Spec

```ts
interface Props {
  variant: 'primary' | 'secondary' | 'ghost' | 'destructive' // destructive is NEW
  disabled?: boolean // NEW
  type?: 'button' | 'submit' | 'reset'
  href?: string
  ariaLabel?: string
}
```

Destructive variant: `--ss-color-error` background, white text. `min-h-[44px]`. Never used as a primary CTA — only for destructive confirmations.

### 7.14 `PortalTabs.astro` — Update Spec

```ts
interface TabDef {
  href: string
  label: string
  anchor: string
  matchPrefix: string
  iconName?: string
}
```

Icon rendering (add `data-portal-tabs` to root `<nav>`):

```astro
<nav data-portal-tabs aria-label="Portal navigation">
  <span
    class="material-symbols-outlined text-[20px] leading-none"
    aria-hidden="true"
    style={`font-variation-settings: 'FILL' ${isActive ? 1 : 0}, 'wght' 400, 'GRAD' 0, 'opsz' 20;`}
  >
    {tab.iconName}
  </span>
  <span class="font-accent-label text-label">{tab.label}</span>
</nav>
```

### 7.15 Email Rendering Pipeline

Email clients cannot resolve CSS custom properties. Tokens must be inlined as literal hex values. Build script:

```bash
node scripts/sync-email-tokens.mjs
```

**Expected output (`src/lib/email/tokens.ts`):**

```ts
export const EMAIL_TOKENS = {
  colorBackground: '#f5f0e3',
  colorSurface: '#f5f0e3',
  colorTextPrimary: '#1a1512',
  colorTextSecondary: '#4a423c',
  colorTextMuted: '#8a7f73',
  colorPrimary: '#c5501e',
  colorComplete: '#4a6b3e',
  colorError: '#a02a2a',
  colorWarning: '#7a5800',
  colorBorder: '#d2cec6',
  fontStackSans: 'Archivo, Arial, Helvetica, sans-serif',
  fontStackMono: '"JetBrains Mono", "Courier New", Courier, monospace',
} as const
```

Email template remediation is P1. Must be complete before the first real client portal invitation is sent to a real client.

### 7.16 Print Stylesheet for Invoice

Add to `InvoiceDetail.astro`'s `<style>` block or a dedicated `src/styles/print.css` imported in the portal layout.

```css
@page {
  size: letter portrait;
  margin: 0.75in 1in;
}

@media print {
  [data-portal-header],
  [data-portal-tabs],
  [data-skip-link],
  [data-consultant-block],
  [data-action-bar] {
    display: none !important;
  }

  html,
  body,
  main,
  [data-invoice-surface] {
    background-color: #ffffff !important;
    color: #000000 !important;
  }

  [data-invoice-amount],
  [data-invoice-reference] {
    color: #000000 !important;
    font-variant-numeric: tabular-nums;
  }

  [data-invoice-card],
  [data-invoice-line-items] {
    page-break-inside: avoid;
    break-inside: avoid;
  }

  [data-pay-button],
  [data-download-button],
  [data-print-button] {
    display: none !important;
  }

  a[href]::after {
    content: none !important;
  }

  .print-only {
    display: block !important;
  }
}
```

**Data attributes required (add to root elements):**

- `PortalHeader.astro` root: `data-portal-header`
- `PortalTabs.astro` root `<nav>`: `data-portal-tabs`
- `SkipToMain.astro` root: `data-skip-link`
- `InvoiceDetail.astro` containers: `data-invoice-surface`, `data-invoice-card`, `data-invoice-line-items`, `data-invoice-amount`, `data-invoice-reference`, `data-pay-button`, `data-download-button`, `data-print-button`

---

## 8. Technical Constraints

### 8.1 Platform

- Astro SSR on Cloudflare Workers + Static Assets
- `run_worker_first = true` — every request flows through Astro middleware
- `import { env } from 'cloudflare:workers'` for env access (adapter v13 removed `Astro.locals.runtime`)
- No Chromium/Puppeteer on Workers — PDF generation via `@react-pdf/renderer` only
- PDF library: `@react-pdf/renderer` (already in use for SOW at `src/lib/pdf/sow-template.tsx`). Invoice PDFs at `src/lib/pdf/invoice-template.tsx`.

### 8.2 Performance Budget

**Core Web Vitals:**

| Metric | Slow 3G target | 4G target     |
| ------ | -------------- | ------------- |
| FCP    | 1,500ms        | 800ms         |
| LCP    | 2,500ms        | 1,200ms       |
| CLS    | < 0.1          | < 0.1         |
| INP    | < 500ms        | < 200ms       |
| TTI    | **< 3,500ms**  | **< 1,800ms** |

**Asset budgets:**

| Asset                      | Budget (gzipped)                         |
| -------------------------- | ---------------------------------------- |
| Total CSS bundle           | < 30KB                                   |
| Initial JS — portal routes | **0KB** (Astro HTML-first)               |
| Initial JS — admin routes  | ≈ 50KB (QuoteLineItemEditor island only) |
| Font payload (WOFF2)       | < 200KB total                            |
| Hero images                | None (no images in MVP)                  |
| Consultant photos          | < 40KB per photo at 120×120px            |
| SOW PDF response           | < 800ms p95                              |
| Invoice PDF response       | < 800ms p95                              |

If PDF generation approaches 800ms p95 under load: async generation with Cloudflare Queue, polling endpoint, redirect to R2 presigned URL on complete.

**Real-device validation required:** Someone must test the portal on a throttled connection before shipping Phase 2. The spec is correct; the test confirms it.

### 8.3 CLS Prevention

- `SigningView.astro` iframe container: `height: calc(100dvh - 7rem)` as a CSS rule (not inline)
- `ConsultantBlock.astro` photos: `width={120} height={120}` on `<img>` (required change)
- `MoneyDisplay.astro`: server-rendered — no CLS risk
- `PortalTabs.astro`: fixed 64px height tab bar — icons are font glyphs, no image CLS

### 8.4 Material Symbols Font Loading

Add to all layout files (`src/layouts/PortalLayout.astro`, `src/layouts/AdminLayout.astro`):

```html
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link
  rel="stylesheet"
  href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200"
/>
```

Preferred for Phase 3: self-host via `@font-face` in `global.css` (eliminates Google Fonts dependency). Acceptable for Phase 2: Google Fonts CDN.

### 8.5 CI Quality Gate Matrix

| Gate                        | Blocks merge                   | Tool                                        |
| --------------------------- | ------------------------------ | ------------------------------------------- |
| `typecheck`                 | Yes                            | GitHub Actions                              |
| `lint`                      | Yes                            | GitHub Actions                              |
| Unit tests (`npm run test`) | Yes                            | GitHub Actions                              |
| Semgrep security            | Yes                            | `.github/workflows/semgrep.yml`             |
| Scope-deferred TODO         | Yes                            | `.github/workflows/scope-deferred-todo.yml` |
| Unmet AC on close           | Yes (reopens)                  | `.github/workflows/unmet-ac-on-close.yml`   |
| E2E (Playwright)            | Yes once suite exists          | GitHub Actions — needs setup                |
| UI drift audit              | Manual PR check                | Skill output in PR description              |
| WCAG automated              | Advisory (not blocking in MVP) | `@axe-core/playwright`                      |

**E2E suites required before Phase 2:**

| Suite             | File                                      |
| ----------------- | ----------------------------------------- |
| Portal navigation | `tests/e2e/portal/navigation.spec.ts`     |
| Magic link flow   | `tests/e2e/portal/magic-link.spec.ts`     |
| Quote detail      | `tests/e2e/portal/quote-detail.spec.ts`   |
| Signing view      | `tests/e2e/portal/signing-view.spec.ts`   |
| Invoice detail    | `tests/e2e/portal/invoice-detail.spec.ts` |
| Admin pipeline    | `tests/e2e/admin/pipeline.spec.ts`        |

### 8.6 Dark Mode

Light-only. `#f5f0e3` cream is the identity, not a light-mode variant. The `--ss-color-*` token layer is architecturally ready to support dark mode via `@media (prefers-color-scheme: dark)` token reassignment — but this is post-Phase 5 only.

### 8.7 Token Compliance Gap

`ui-drift-audit` covers 6 of 7 UI-PATTERNS rules but does not catch raw hex values in source (e.g., `bg-[#f5f0e3]` instead of `bg-background`). Proposed fix: ESLint custom rule erroring on raw hex values in `.astro`, `.ts`, `.tsx` files. File as a follow-on issue targeting Phase 3, before the admin build phase begins.

### 8.8 `UI-PATTERNS.md` Rule 6 Correction (Overdue)

Rule 6 documents `space-section: 32px` and `space-card: 24px`. Live compiled tokens are `48px` and `32px`. The documentation is wrong; the tokens are correct. **Fix in next PR touching `UI-PATTERNS.md`. This must not be deferred again.**

---

## 9. Inspiration & Anti-Inspiration

### 9.1 Marcus's Reference Set (Primary)

**QuickBooks — operational trust**
Not pretty, but trusted. Numbers in columns. Every time he opens it, he knows where he is. Application: admin pipeline and quote builder. Organized, not styled.

**Bank app — mobile information hierarchy**
The balance is the first thing he sees. Everything else is behind a tap. Application: portal dashboard hierarchy. Price above the fold. Everything else one tap deeper.

**Apple Calendar — dense but readable**
He uses it dozens of times a day. Dense information, zero decoration, readable in under a second. Application: engagement timeline view, admin pipeline. Maximum information density, no illustration.

**Square — obvious action**
The button was big. The confirmation was obvious. The receipt went to the customer. Nothing unnecessary. Application: "Review & Sign" and "Pay Now" CTA sizing and placement. Amount on or adjacent to the button ("Pay Deposit — $3,500").

### 9.2 Design Ecosystem References (Secondary)

**Stripe Dashboard** (`https://dashboard.stripe.com`): Dense data tables, conservative status indicators, chrome invisible behind the data. Validates the SS approach of making price and status the primary read. What not to take: blue accent, rounded inputs.

**Letterpress and Commercial Sign Shop Ephemera** (`https://letterformarchive.org/collection/`): The tonal and typographic source of the Plainspoken Sign Shop identity. Weight-900 display scale, ruled hairlines, flat color — direct inheritance. What not to take: literal reproduction of historical signage in a digital product.

**HEY Email** (`https://app.hey.com`): Conviction in typographic choices, willingness to be decisive. Take: decisiveness only. Not HEY's playful color palette or illustrated onboarding.

**Notion in structured use:** Editorial layout for proposal detail and document library. Useful for quote detail and document library layout only.

### 9.3 Anti-Inspiration

**The "Bouncy Purple Gradient" Register** (`https://copilot.com`)

Marcus's reaction is immediate and negative — this register signals "appeal to everyone" and therefore appeals to no one. Specifically wrong: purple accent of any value; rounded modals, cards, or badges; illustration-heavy empty states; "friendly" onboarding with personality copy; visual density as a signal of premium.

**HoneyBook — Freelancer CRM Register** (`https://www.honeybook.com`)

Large feature cards with gradient washes, soft shadows, rounded corners, illustrated empty states. A well-executed product for photographers and event planners. Entirely wrong for Marcus.

**ServiceTitan — Enterprise Field-Service SaaS** (`https://www.servicetitan.com`)

Marcus's industry peers know ServiceTitan specifically. He closed the tab after ten minutes. The failure mode: institutional without being authoritative. Dense data tables that emphasize structure over the decision the user needs to make.

Specifically wrong: dark navy or blue brand anchor; sky blue accents; table-heavy layouts where data structure is more visible than the user's next action; dense admin chrome pushed to clients.

**The "Etsy Receipt" Invoice Register**

An invoice that looks template-generated reads as underpriced work. Marcus notices. Specifically wrong: invoice number that is a raw UUID; amount in standard body text rather than JetBrains Mono tabular-nums at invoice-register scale; "Pay Now" button without the dollar amount; rounded corners, drop shadows, or gradient fills; payment amount buried below a fold.

The standard: the invoice looks like something Marcus would put in his Dropbox folder "Vendors and Contractors 2026." Firm name, real invoice number, date, amount in column-aligned monospaced type, due date, payment instructions. Cream paper, ink, hairline borders. Same palette as the portal.

---

## 10. Design Asks

Numbered, actionable, with priority and originating role.

1. **Token PR — warning, muted-accessible, focus-ring tokens**
   Add `--ss-color-warning: #7a5800`, `--ss-color-text-muted-accessible: #6b6158`, `--ss-focus-ring-width: 2px`, `--ss-focus-ring-offset: 2px` to `crane-console/packages/tokens/src/ventures/ss.json`. Recompile. Update `global.css` with `@theme inline` additions for warning and motion tokens, plus `@layer base` additions for focus-ring and prefers-reduced-motion rules.
   **Priority: P0 | Origin: Brand Strategist, Design Technologist**

2. **`global.css` changes — motion, focus-ring, reduced-motion**
   Four discrete changes to `src/styles/global.css`: (1) add warning + muted-accessible color tokens to `@theme inline`; (2) add motion tokens to `@theme inline`; (3) add `:focus-visible` rule + `@media (prefers-reduced-motion: reduce)` block to `@layer base`; (4) add `.touch-target` utility.
   **Priority: P0 | Origin: Design Technologist**

3. **Email template remediation**
   Replace blue/slate palette across `src/lib/email/templates.ts`, `src/lib/email/booking-emails.ts`, `src/lib/email/follow-up-templates.ts` using `EMAIL_TOKENS` constants from `src/lib/email/tokens.ts`. Run `scripts/sync-email-tokens.mjs` to generate the constants file. Must complete before the first real client portal invitation is sent.
   **Priority: P1 | Origin: Design Technologist**

4. **`PortalTabs.astro` icon update**
   Add `iconName` prop to `TabDef` interface. Implement icon rendering with `font-variation-settings` FILL toggling. Add `data-portal-tabs` to root `<nav>`. Update tabs array with canonical icon set. Labels always visible. Write `tests/portal/navigation.test.ts`.
   **Priority: P1 | Origin: Design Technologist, Brand Strategist**

5. **`SigningView.astro` — build new component**
   Full spec in §7.4. PortalHeader visible. Pre-signing disclosure line mandatory. Mobile accordion / desktop two-column. ARIA states for loading/ready/error/signed. Iframe keyboard focus (Tab exit) verified against SignWell embed API. Write `tests/portal/signing-view.test.ts`.
   **Priority: P1 (Phase 2 prerequisite) | Origin: Interaction Designer, Design Technologist**

6. **`PreSigningPrep.astro` — build new component**
   Full spec in §5.5. Scroll-gated CTA. IntersectionObserver on `#sign-anchor`. Progressive enhancement: enabled by default if JS disabled. Adjacent explainer text while button is disabled. Pre-signing data integrity constraint confirmed (same DB query as SignWell document generation).
   **Priority: P1 (Phase 2 prerequisite) | Origin: Interaction Designer**

7. **Pre-signing data integrity audit**
   Confirm that the pre-signing summary screen and the SignWell document derive from the same DB query against the same source-of-truth records (`quotes.total_cents`, `line_items`, `payment_terms`). No denormalized cache, no drift-capable portal-side storage. Document the query path in a code comment at the API route. This is a hard constraint — see §6.6.
   **Priority: P0 (must be confirmed before Phase 2 ships) | Origin: Target User**

8. **`ParkingLotPanel.astro` — build new component**
   Full spec in §7.6. Four required authored fields per item; render gate enforced. Readonly portal view vs. admin editable view. Stale warning (>14 days, admin only). `disposition_note` required for "decided" and "out_of_scope" dispositions. Write `tests/portal/parking-lot-panel.test.ts`.
   **Priority: P1 (Phase 5 prerequisite, architecture now) | Origin: Interaction Designer, Design Technologist, Target User**

9. **`MagicLinkExpiredForm.astro` — build new component**
   Full spec in §7.5. States: idle / submitting / success / error. `aria-live="polite"` on success confirmation. Write `tests/portal/magic-link-expired.test.ts`.
   **Priority: P1 | Origin: Interaction Designer, Design Technologist**

10. **`PipelineKanban.astro` and `ClientCard.astro` — build new components**
    Full specs in §7.7 and §7.8. Overdue state as visible text label (not color-only). Write `tests/admin/pipeline-kanban.test.ts` and `tests/admin/client-card.test.ts`.
    **Priority: P1 | Origin: Interaction Designer, Design Technologist**

11. **`QuoteLineItemEditor.tsx` — build React island**
    Full spec in §7.2. Admin-only. `client:load` on quote builder page only. ARIA labels on all inputs. `inputmode="decimal"` for money fields. Scroll-into-view on focus (300ms delay). Keyboard navigation: Tab to next field, Enter on hours to add row, Backspace on empty hours to remove row. Write `tests/admin/quote-line-item-editor.test.ts`.
    **Priority: P2 | Origin: Interaction Designer, Design Technologist**

12. **`CtaButton.astro` — add disabled prop and destructive variant**
    Full spec in §7.13. Both `disabled` (native) and `aria-disabled` set appropriately. Destructive variant: error fill, white text, `min-h-[44px]`. Write `tests/components/cta-button.test.ts`.
    **Priority: P2 (Phase 3 prerequisite) | Origin: Design Technologist**

13. **Print stylesheet for invoice**
    Add print CSS to `InvoiceDetail.astro` or `src/styles/print.css`. Data-attribute selectors throughout. `@page` margins. White background for print. Hide all portal chrome and interactive buttons. Suppress URL printing after links. Full spec in §7.16.
    **Priority: P2 | Origin: Design Technologist**

14. **Invoice PDF via `@react-pdf/renderer`**
    Create `src/lib/pdf/invoice-template.tsx` mirroring `src/lib/pdf/sow-template.tsx`. Cream palette in print context maps to white. JetBrains Mono for amounts and reference numbers. < 800ms p95 response; async generation path if exceeded. Store in R2; gate "Download PDF" button on `invoice.pdf_url !== null`.
    **Priority: P2 | Origin: Design Technologist**

15. **`UI-PATTERNS.md` Rule 6 correction**
    Change `space-section: 32px` to `48px` and `space-card: 24px` to `32px` in Rule 6. Update any examples or diagrams. This correction has been noted across all three rounds. It must ship in the next PR touching `UI-PATTERNS.md`.
    **Priority: P1 | Origin: Brand Strategist, Design Technologist**

16. **Stale-visit "upcoming this week" feature**
    Extend the "Last activity" banner to surface any milestone or scheduled touchpoint in the current calendar week. "Kickoff review coming up Thursday." If nothing is scheduled or the system has no date for the next milestone, say nothing. Depends on milestones having authored `scheduled_date` fields. See Open Decision D-6.
    **Priority: P2 | Origin: Target User**

17. **Pay Now button position — sticky or dual-placement**
    On `/portal/invoices/[id]`, ensure the "Pay Now — $amount" button is visible near the top of the invoice (before line items) as well as at the bottom, or implement sticky positioning. Bottom-only placement is insufficient for long invoices.
    **Priority: P1 | Origin: Target User**

18. **Playwright E2E suite — Phase 2 prerequisite**
    Build the six E2E suites listed in §8.5. Add `@axe-core/playwright` `checkA11y` call to each portal key screen test. Promote to blocking merge gate once baseline is clean.
    **Priority: P1 (Phase 2 prerequisite) | Origin: Design Technologist**

---

## 11. Open Design Decisions

Each decision requires a named decision-maker to resolve it before the phase noted. Options are documented with rationale. Synthesizer recommendation is provided.

---

### D-1 — Signing route: distinct URL vs. state within `quotes/[id]`

**The question:** Is the pre-signing prep screen and signing view rendered at `/portal/quotes/[id]/sign` (distinct URL) or as a state within `/portal/quotes/[id]`?

**Options considered:**

- **A — Distinct route** (PRD §9, Interaction Designer, Brand Strategist): Stable URL, direct deep-linking from email CTA, cleaner URL semantics, enables back-navigation to quote detail.
- **B — State within `quotes/[id]`** (current codebase pattern): Simpler routing, no back-navigation needed (breadcrumb handles return), simpler server-side logic.

**Why it matters:** The invitation email CTA links to the portal expecting to reach the proposal. If we add a pre-signing prep screen before the iframe, both the email CTA and the "Review and sign" button on the quote detail may need to reach the prep screen. A distinct route enables the email to link directly to it.

**Recommended:** Distinct route (`/portal/quotes/[id]/sign`). The prep screen is a meaningful step in the signing journey, not a transient UI state. PRD §9 already specifies the distinct URL.

**Needs:** Founder call before Phase 2 ships.

---

### D-2 — Documents tab: MVP (5 tabs) or post-launch (4 tabs)

**The question:** Is the Documents tab in the MVP tab bar (5 tabs) or deferred, with Documents accessible via a dashboard row link?

**Options considered:**

- **A — 4 tabs at MVP** (Marcus explicit preference, Interaction Designer recommendation): Matches Apple HIG guidance for primary navigation. Documents accessible via engagement summary card "View project files" secondary link.
- **B — 5 tabs at MVP** (PRD §9): Documents are part of engagement deliverables. Clients will want them at handoff.
- **C — Conditional 5th tab** (Design Technologist Round 2 note): Documents tab appears only when documents are uploaded.

**Why it matters:** Five tabs compresses tab item width at 375px. Marcus said "four tabs, that's enough" explicitly. But Marcus is commenting before the handoff phase when document access may become frequent.

**Recommended:** 4 tabs at MVP per Marcus's explicit preference. Add 5th tab post-launch if session data shows >20% of portal sessions include a documents page view.

**Needs:** Founder call before Phase 2 ships.

---

### D-3 — Product naming: stay nameless or name the portal

**The question:** Should the portal have a product name at MVP?

**Options considered:**

- **A — Nameless** (Brand Strategist recommendation): The subdomain `portal.smd.services` is the implicit name and address. No additional cognitive tax on Marcus.
- **B — Named product** ("SMD Portal", "Engagement Center"): Creates a named entity. Cognitive load with no payoff at MVP.
- **C — Named productized platform**: Appropriate only if SS sells portal access to other consultancies.

**Why it matters:** Every named entity is a tax. Marcus does not benefit from knowing what the system is called. He benefits from knowing where to click and what it costs.

**Recommended:** Nameless at MVP. "SMD Services portal" in prose context is sufficient. Revisit when productization is on the table.

**Needs:** Founder call.

---

### D-4 — Marketing site photography: no photography or real faces

**The question:** Does the no-photography rule extend to the marketing site (`smd.services`), or does the marketing site permit real photos of the consulting team?

**Options considered:**

- **A — No photography anywhere** (Brand Strategist Round 3 initial position): The absence of photography is itself a positioning signal. The value proposition is experience and judgment.
- **B — Real photos on marketing site only** (Marcus pushback): Consulting firms are personal relationships. Without a face, the site reads as either hiding something or admitting it's a solo operator. No stock photography — real photos of the actual team.

**Why it matters:** Marcus said: "At some point before I hired this firm, I went to the website. And the thing that a website with no faces says to me is: I don't know who I'm dealing with." This is first-call conversion, not portal UX.

**Recommended:** Show two versions to Marcus (with and without real team photos) before finalizing. His gut reaction is the answer. Portal and admin: no photography, confirmed absolute. Marketing site: hold the decision pending real-user feedback.

**Needs:** Founder call. Requires showing Marcus two versions — one with real team photos, one without. Both must use the same brand register; stock photography is not an option.

---

### D-5 — Warning color real-device validation

**The question:** Does `#7a5800` read as "pay attention" or "fine to ignore" on a real phone screen in a dim room?

**Options considered:**

- **A — `#7a5800` confirmed** (Brand Strategist): 7.14:1, AAA, warmest amber, visually distinct from olive in low-light. Design Technologist adopted this value.
- **B — Real-device test required before ship** (Target User): Marcus cannot confirm from a hex code. Someone needs to put the warning color on a real phone screen in a dim room.

**Why it matters:** The warning color is in the "calm" register (warm amber, not red). Calm warnings have a real risk: Marcus might scroll past an overdue invoice at 10pm without registering it.

**Recommended:** Conduct the real-device test. Put `#7a5800` warning callout on a phone in a room at typical evening lighting. Ask whether "PROPOSAL EXPIRES TOMORROW" in that color immediately registers as requiring attention. If it does not, revisit `#7a5800` vs. a slightly higher-chroma option.

**Needs:** Real-device test before Phase 2 ships.

---

### D-6 — Stale-visit "upcoming this week" preview

**The question:** Should the "Last activity" banner include a one-line preview of any milestone or scheduled touchpoint coming up in the current calendar week?

**Options considered:**

- **A — Include it** (Target User request): "Kickoff review coming up Thursday." Marcus does not want to be surprised by a meeting he forgot to prepare for.
- **B — Omit it** (simpler): The banner already handles orientation for completed events. Upcoming-event awareness requires milestones to have authored `scheduled_date` fields.

**Why it matters:** Marcus's Apple Calendar comparison: "When I open the app on a Monday morning, I can see immediately what's on this week. I don't want to navigate anywhere." If the system knows about an upcoming meeting or milestone date and Marcus doesn't see it on the home screen, he may walk into a client meeting unprepared.

**Options also include:** Only show the upcoming event if `milestone.scheduled_date` is authored; if no date is set, say nothing (empty-state-pattern applies).

**Recommended:** Include it, gated on authored `scheduled_date`. If the milestone has no date, the banner shows last activity only. No fabricated preview text.

**Needs:** Founder call on whether milestones will consistently have `scheduled_date` values authored by admin. If not, the feature is unreliable and should be deferred.

---

### D-7 — ACH payment path in invoice detail

**The question:** Does the "Pay Now" button in `InvoiceDetail.astro` lead to a Stripe page where ACH is available as a payment option?

**Options considered:**

- **A — Card-only Stripe flow** (simpler integration): Standard Stripe payment link or Stripe-hosted page. ACH requires additional Stripe configuration.
- **B — ACH-enabled Stripe flow** (per BR-036): Stripe's ACH Direct Debit is available for bank transfers. Marcus may prefer ACH for large invoices ($3,000-$6,000+) to avoid card fees.

**Why it matters:** Marcus: "I might actually pay by ACH for a $6,000 invoice to avoid the credit card fee. Ask me." If the portal only offers card and Marcus prefers ACH, the payment moment creates friction at a trust-critical step.

**Recommended:** Confirm with Marcus whether he would use ACH. If yes, confirm with Stripe integration design that ACH is available in the payment link flow. The portal UI does not need to change — the Stripe hosted page handles the payment method selection.

**Needs:** Founder call + Marcus verification + Stripe integration check.

---

### D-8 — Pre-signing scroll gate: enforced or always-enabled

**The question:** Is the "Continue to signing" CTA disabled until Marcus scrolls past the acknowledgments (IntersectionObserver gate), or always-enabled with a scroll nudge?

**Options considered:**

- **A — JS-gated** (Interaction Designer recommendation): Disabled until scroll anchor in viewport. Progressive enhancement — always-enabled if JS disabled. Adjacent explainer text: "Scroll to review the summary above, then continue."
- **B — Always-enabled with nudge** (simpler): Button always active. "Scroll to review all terms" hint above button if anchor not yet in viewport.

**Why it matters:** A disabled primary CTA may read as broken before Marcus realizes he needs to scroll. But Option A with the adjacent explainer resolves this — he sees the reason before hitting confusion.

**Recommended:** Option A (JS-gated) with the adjacent explainer text. The explainer text is the key addition that prevents the "why can't I tap this?" confusion.

**Needs:** Founder call before Phase 2 ships.

---

### D-9 — Email 3 conditional send

**The question:** Should Email 3 (countersigned / firm acknowledgment) send only when `engagement.next_step_text` is authored, or always send?

**Options considered:**

- **A — Conditional send** (Target User, Interaction Designer): Only send Email 3 when there is authored "what happens next" content. An empty Email 3 ("We received your signed document." — full stop) is noise.
- **B — Always send** (simpler implementation): Email 3 always fires on the countersigned trigger, even with no `next_step_text`.

**Why it matters:** Marcus: "If the answer is 'it's the one that tells me what happens next,' then keep it. But only if it actually has the 'here's what happens next' content from the authored field." An email with no next-step text is a dead-end email Marcus would mute after the first time.

**Recommended:** Conditional send. If `engagement.next_step_text` is null, suppress Email 3. The suppression logic is a single conditional at the email trigger point.

**Needs:** Founder call + engineering confirmation that suppression is simple to implement cleanly.

---

### D-10 — Parking lot disposition note: required or recommended

**The question:** Is the `disposition_note` field required (enforced at DB or UI layer) or recommended (surfaced prominently but not enforced)?

**Options considered:**

- **A — Required for "decided" and "out_of_scope" dispositions** (Brand Strategist recommendation): Admin cannot publish these dispositions without a note. Enforced at UI layer — the "mark as decided" action requires a note field to be non-empty.
- **B — Recommended** (simpler): Note field is prominent and labeled "required" in the admin UI but not enforced at the code layer.

**Why it matters:** Marcus: "If I come back four months later and look at the parking lot and there's an item that says 'disposition: dropped' with no note explaining why it was dropped, I won't remember what that was." The disposition note is not decoration — it is the explanation that makes the parking lot feel like collaboration rather than a list of things the firm decided without him.

**Recommended:** Required (Option A) for "decided" and "out_of_scope" at the UI layer. Items with "deferred_to_call" disposition may omit the note (the call is the note).

**Needs:** Founder call on enforcement level. The brand position is clear; the question is DB vs. UI layer enforcement.

---

### D-11 — Champion invite: admin-triggered or client-triggered (Phase 4)

**The question:** When champion access is implemented, who sends the champion magic-link invitation?

**Options considered:**

- **A — Admin-triggered** (Interaction Designer recommendation): Scott sends from `/admin/engagements/[id]`. Simple; no portal self-service complexity.
- **B — Client-triggered** (portal self-service): Marcus initiates from a portal settings page. More empowering for Marcus; requires admin approval step to prevent arbitrary email additions.

**Why it matters:** Phase 4 architecture must accommodate whichever path is chosen. Option A is admin UI only. Option B requires portal self-service flow, email confirmation loop, and admin approval.

**Recommended:** Admin-triggered (Option A) for Phase 4 MVP. Revisit Option B in Phase 5 if Marcus frequently requests champion access verbally.

**Needs:** Founder call before Phase 4 scoping.

---

### D-12 — Material Symbols hosting: CDN or self-hosted (Phase 3)

**The question:** Should Material Symbols be loaded from Google Fonts CDN (current Phase 2 spec) or self-hosted via `@font-face` in `global.css`?

**Options considered:**

- **A — Google Fonts CDN**: No repo footprint. Acceptable for Phase 2.
- **B — Self-hosted**: No Google Fonts dependency. Better privacy. Better performance (eliminates third-party DNS). Requires font files added to repo and `@font-face` declaration.

**Recommended:** Self-host for Phase 3. Google Fonts CDN is acceptable for Phase 2.

**Needs:** Engineering decision at Phase 3 start.

---

### D-13 — `--ss-color-text-muted-accessible` formal adoption

**The question:** Should `#6b6158` (4.71:1 on cream) be formally adopted as a token, or should the two-stop rule (`text-secondary` for functional, `text-muted` for decoration) remain the complete guidance?

**Options considered:**

- **A — No third token** (Brand Strategist Round 3 final position): The two-stop rule is cleaner. Engineers will misapply a middle-stop token. `text-secondary` at 8.64:1 handles outdoor legibility with substantial margin.
- **B — Add `text-muted-accessible`** (Design Technologist Round 3 included it): Explicit AA-passing token for mobile outdoor contexts. Clear signal to engineers who want "something lighter than secondary."

**Why it matters:** If engineers reach for `text-muted` on mobile metadata and find it fails WCAG AA, they need a clear answer about what to use instead. A third token gives them a precise choice; a usage rule requires them to read documentation.

**Resolution:** This brief includes the token in the JSON diff for completeness (Design Technologist's Round 3 addition) but the Brand Strategist's final position is the governing guidance: do not use `text-muted-accessible` as a default replacement. It is reserved for situations where `text-secondary` is demonstrably too dark for the specific design context, confirmed by careful engineering review. The token exists; the default is `text-secondary`.

**Needs:** Design Tech lead decision. No urgency — the usage rule is the complete guidance for Phase 2.

---

_SMD Services Client Portal — Design Brief_
_Synthesized from 3-round, 4-role design brief process._
_Brand Strategist: Plainspoken Sign Shop identity, color system, typography, imagery, inspiration. Interaction Designer: screen inventory, interaction patterns, email flows, navigation model. Design Technologist: component specs, token architecture, CSS strategy, technical constraints, CI gates. Target User (Marcus): use context, make-or-break moments, trust signals, real-device concerns._
_The brief overwrites contributions. The contributions are the audit trail._
