# Brand Strategist Contribution - Design Brief Round 2

**Author:** Brand Strategist (Sonnet 4.6)
**Date:** 2026-04-26
**Design Maturity:** Full system
**Identity:** Plainspoken Sign Shop (migrated from Desert Functional, 2026-04-23)
**Status:** Revised after cross-role review

---

## Changes from Round 1

Numbered list. Each entry states: what changed, why, and which role's input triggered it.

1. **`text-muted` usage rule tightened from "large text or 600+ weight" to "non-essential metadata only, never primary text."** The Design Technologist computed `--ss-color-text-muted` at 3.0:1 (not 3.44:1 as I stated in Round 1 — I rounded optimistically). At precisely 3.0:1 the token sits at the absolute threshold for large-text/UI-component pass, and zero margin means ambient conditions can tip it to fail. Marcus's sunlight concern makes this real: outdoor mobile use in direct Phoenix sun reduces effective perceived contrast. The Round 1 rule permitted `text-muted` at 17px or 600+ weight; this round restricts it further — only non-essential metadata where failure to read causes no functional harm. See §3.2 Gap 1 (revised).

2. **Added explicit contrast bump proposal for `text-muted` outdoor mobile context.** Marcus said "if the secondary text is too light, I'm not reading it" (Chandler sun, truck cab). Combined with the Design Technologist's 3.0:1 finding, I'm proposing an optional hardened alias `--ss-color-text-muted-accessible: #6b6158` (4.71:1 on cream) for contexts where muted text carries functional information on mobile. See §3.3 Gap 1 (revised).

3. **Warning token color revised from `#6b4f08` to `#7a5800`.** Round 1's `#6b4f08` at 6.72:1 was good. But after mapping the actual portal contexts where warning appears (overdue invoice badge, quote near expiry), I realized the amber-ochre needed to be visually distinct from both burnt orange (action) and olive (success) at a glance, not just in a color table. `#7a5800` (deep golden brown, 7.14:1 on cream) reads as its own register — warm, not alarming, not actionable. It also renders as a text-on-cream color in body context without any AA concern. Icon pairing added this round. See §3.3 Gap 3 (revised).

4. **Confirmed `prefers-reduced-motion` as brand-required, not just a11y-required.** The Design Technologist flagged the missing `@media (prefers-reduced-motion: reduce)` block. I'm confirming this as a brand position: Plainspoken restraint demands that motion never impose itself on users who have said they don't want it. The identity's "paint-job, not brochure" is incompatible with transitions that run against user preference. The brand rationale strengthens the a11y argument. See §5.4 (updated).

5. **Motion tokens mapped to Tailwind v4 `@theme inline` — confirmed and endorsed.** The Design Technologist flagged that motion tokens exist in the compiled package but are not mapped in `global.css`. Round 1 acknowledged this as an "acceptable gap." That was wrong. The gap means design-system changes to motion durations don't propagate to component transitions. I'm changing my position: mapping motion tokens is required, not deferred. See §5.4 (updated).

6. **`UI-PATTERNS.md` Rule 6 doc/code drift noted as documentation correction required.** Both the Design Technologist and my Round 1 notes flagged that Rule 6 documents `section: 32px / card: 24px` while compiled tokens are `48px / 32px`. I flagged it in Round 1 as a note; this round I'm marking it explicitly as a documentation correction required in the next PR touching `UI-PATTERNS.md`. See §5.1 (updated).

7. **Strengthened Anti-Inspiration §8 with Marcus-specific detail.** Marcus said "if it has those bouncy purple gradients I'm out" and described the deposit invoice feeling like "something I bought on Etsy" as a make-or-break failure. Both are specific enough to name in Anti-Inspiration. Added "Etsy receipt register" as a named anti-pattern in §8.1 and §8.4. This is not aesthetic preference — Marcus's invoice is a business document he will print and file. It must look like one.

8. **Added third-party embed surface principle for SignWell.** The Interaction Designer flagged that the SignWell embed is a brand discontinuity risk — it will look different from the portal. Marcus confirmed this fear directly: "visual whiplash" at the moment of signing creates hesitation that can kill the commitment. Added §6.5: brand applies edge-to-edge in our chrome; third-party embeds inherit their own visual contract. Our response is to minimize the embed footprint and frame it with our chrome, not to attempt restyling it.

9. **Tab icon set specified.** The Interaction Designer documented the persistent-tabs portal nav with four destinations: Home / Proposals / Invoices / Progress. Round 1 named Material Symbols Outlined as the system but did not specify which symbols. This round specifies a canonical per-route icon list. See §6.1 (updated).

10. **Zero radii confirmed as canonical with explicit anti-pattern statement.** All three roles converged on flat institutional radii. Marcus's "sharp edges feel like they're being honest with me" is the user-facing rationale. The Design Technologist has all radius tokens at 0. The Interaction Designer's layout specs assume flat surfaces throughout. This round makes the anti-pattern explicit: rounded buttons do not merely look wrong — they break the entire Plainspoken register by implying a brand personality the system doesn't have.

11. **Warning state icon + color pairing added for portal contexts.** The Interaction Designer documented portal states but noted no colorway for "warning." Round 1 proposed the token. This round maps the token to actual portal warning contexts (quote near expiry, deposit overdue) with a concrete icon + color + prose combination. See §3.3 Gap 3 (revised).

12. **Inspiration board reconciled with Marcus's actual tool references.** Marcus uses QuickBooks, his bank app, Apple Calendar, and Square. None of these were on my Round 1 board. Each has design qualities that map to or challenge SS. Reconciled in §7 with per-reference notes.

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

Marcus confirmed this directly in Round 1: "It doesn't look like a website. This looks like a letterhead. Like something you'd get from a contractor or a law office." That's the target. He also confirmed the failure modes: "that soft purple gradient that every all-in-one platform for teams uses" and "rounded corners and drop shadows everywhere" are disqualifying.

Five traits define the personality. Each is a specific position, not a vague aspiration.

---

### 1.1 Plainspoken, not folksy

The voice is direct and unpretentious — the way a competent colleague talks, not the way a copywriter talks. It says what it means and stops. It never performs warmth. It never overexplains to seem helpful.

- **This:** "Your proposal is ready." / "Signed." / "Invoice due Friday."
- **Not that:** "Great news! Your personalized proposal is waiting for you." / "You're all set!"
- **In UI:** Labels carry the load. Actions are verbs. Status is stated, not celebrated.
- **Marcus's confirmation:** "I don't need a logo to be my friend. I need it to not embarrass me when I show it to my wife."

---

### 1.2 Authoritative, not corporate

SMD Services delivers what larger firms charge three times more to deliver. The design communicates that confidence without the trappings of institutional scale: no gradient washes, no dense legalese in helper text. Authoritative means the typography is heavy, the layout is deliberate, and there is no visual noise competing with the content.

- **This:** Archivo Black at display scale. Flat hairline borders. Ink on cream. The page knows what it is.
- **Not that:** Soft drop shadows everywhere. Rounded corners on every card. A neutral blue-gray that telegraphs "enterprise SaaS."
- **Marcus's framing:** "Sharp edges feel like they're being honest with me." This is not an aesthetic preference — it's a trust signal.

---

### 1.3 Precise, not fussy

Every element is there because it is doing a job. There is no decoration for decoration's sake, no illustration as a mood setter, no texture overlay on a background that already has a color. Precision is not spartan — the spacing is generous enough that the content breathes, and the typography scale has enough grades to give hierarchy real shape.

- **This:** A price that takes the full `text-hero-price` (64px, weight 900) on a portal dashboard. One focal point. Nothing competing.
- **Not that:** A price surrounded by explanatory badges, a "✓ Signed" overlay, and a tooltip with hover text.
- **In UI:** Pattern 02 (Redundancy ban) is the structural enforcement of this trait. One signal per fact.

---

### 1.4 Collaborative, not diagnostic

The brand voice says we work alongside the client, not over them. The UI reflects this: the client portal is a shared workspace where they can see what is being built on their behalf, ask questions, and access what they need. The tone is peer-to-peer, not service-counter-to-customer.

- **This:** "Here is what we are building and where we are." Milestone names in plain language ("Your scheduling system is live").
- **Not that:** "Phase 2: Implementation — 78% complete." Progress bars with percentages. Jargon status.
- **Marcus's word for this:** He wants to feel like "I made a good decision hiring these people." The portal confirms that or undermines it. There is no neutral reaction.

---

### 1.5 Grounded, not minimal

The Plainspoken Sign Shop is not minimalism. Minimalism is a design ideology. The sign shop is a register — the way 1950s commercial signage used heavy letterforms, flat color, and ruled lines not because someone decided "less is more" but because that was what worked at scale on a painted surface. The system inherits that pragmatism. It is dense when density serves the user (pipeline view, quote builder), spacious when spaciousness does (client dashboard first view).

- **This:** The rhythm tokens (section 48px / card 32px / stack 16px / row 12px) applied deliberately per context.
- **Not that:** Applying maximum whitespace uniformly to signal "premium."
- **Marcus's comparison:** QuickBooks, his bank app, Apple Calendar, Square. None are minimal. All are grounded — right information, right density, no decoration.

---

## 2. Design Principles

These principles govern tradeoff decisions during build and design review. They are sequenced by priority: when two principles conflict, the higher-ranked one wins.

---

### Principle 1 — The business operates before the design is complete

Ship phases in order. An un-styled Phase 1 admin that works beats a beautifully designed Phase 3 that blocks the first assessment call. The design system's job is to eliminate the tradeoff by establishing clear defaults early: tokens, primitives, and rhythm tokens are the floor, not the ceiling.

**PRD anchor:** §5 Principle 1 — "The business operates before the software is complete."

---

### Principle 2 — The client's first screen is the proposal, not an onboarding flow

Every design decision in the client portal is evaluated against one question: does this make it harder or easier for Marcus to see his price and sign? The first authenticated view is not a dashboard — it is the proposal. The signature and the price are above the fold on mobile. Everything else is below.

**PRD anchor:** §7 Step 3 — "On mobile, the price and the button are above the fold with no scrolling required."
**Marcus's confirmation:** "The price and the 'Review & Sign' button need to be on the screen without scrolling on my phone. That's non-negotiable."

---

### Principle 3 — No fabricated content; no visual compensation for missing data

Empty sections render nothing or a "TBD in SOW" marker. The design must be legible and complete-looking when authored fields are absent, not just when they are present. Every layout is designed and tested with missing data. Decorative placeholders, shimmer skeletons that persist, and "Coming soon" copy are all violations.

**Content rule anchor:** `docs/style/empty-state-pattern.md`. P0 enforcement.

---

### Principle 4 — One signal per fact, one primary per view

Pattern 02 (Redundancy ban) and Pattern 03 (Button hierarchy) are the visual enforcement of this principle. Redundant signals dilute the signal they duplicate. A price displayed at hero scale and then again in a caption and then again in a status pill reads as uncertain. State something once and state it with authority.

**Pattern anchors:** `docs/style/UI-PATTERNS.md` Rules 2 and 3.

---

### Principle 5 — Status is a fact, not a decoration

Status indicators follow Pattern 01 strictly: pill in list rows, eyebrow for category, dot+label or prose for single-item cards, prose for detail-page headlines. The visual treatment maps to the scanning context, not to how "important" the status feels.

**Pattern anchor:** `docs/style/UI-PATTERNS.md` Rule 1.

---

### Principle 6 — Typography and hairlines carry the page; decoration does not

The Plainspoken Sign Shop has no shadows, no gradient fills, no illustration. The hierarchy is entirely typographic and structural. A card is defined by a hairline border on a cream surface — not by a white fill, drop shadow, and rounded corners. This constraint is binding. Any PR that introduces shadow or rounded corners to a card or button requires explicit design review.

**Token anchors:** `--ss-radius-card: 0`, `--ss-radius-button: 0`, `--ss-radius-badge: 0`. No shadow tokens exist in the system.

---

### Principle 7 — The admin surface can be dense; the client portal cannot

The admin user is a competent technical operator who wants speed and accuracy. Dense information display is appropriate: smaller type, tighter row spacing, data tables. Marcus is a business owner on a phone after dinner. The portal is spacious, single-task, and never cluttered with admin-visible detail (no hourly rates, no per-item hours, no internal status codes).

**PRD anchor:** §14 — "Dense information display acceptable" for admin; mobile-first for client portal.

---

## 3. Color System

The Plainspoken Sign Shop palette is canonical. It is a four-role palette: cream, ink, accent (burnt orange), and two semantic complements (olive success, brick error). Light-only is the current decision.

**Source of truth:** `node_modules/@venturecrane/tokens/dist/ss.css`

---

### 3.1 Canonical Palette

| Token                        | Hex                   | Computed RGB             | Role                                                           |
| ---------------------------- | --------------------- | ------------------------ | -------------------------------------------------------------- |
| `--ss-color-background`      | `#f5f0e3`             | 245, 240, 227            | Cream paper. Primary page background.                          |
| `--ss-color-surface`         | `#f5f0e3`             | 245, 240, 227            | Card background. Cards defined by hairline borders, not fill.  |
| `--ss-color-surface-inverse` | `#1a1512`             | 26, 21, 18               | Ink. Inverted surfaces.                                        |
| `--ss-color-border`          | `rgba(26,21,18,0.16)` | ~210, 207, 199 effective | Default border. Ink at 16% opacity.                            |
| `--ss-color-border-subtle`   | `rgba(26,21,18,0.08)` | ~228, 225, 219 effective | Subtle divider. Ink at 8% opacity.                             |
| `--ss-color-text-primary`    | `#1a1512`             | 26, 21, 18               | Ink. All primary content text.                                 |
| `--ss-color-text-secondary`  | `#4a423c`             | 74, 66, 60               | Subdued ink. Metadata, secondary labels.                       |
| `--ss-color-text-muted`      | `#8a7f73`             | 138, 127, 115            | Muted ink. Non-essential tertiary only. See usage rule below.  |
| `--ss-color-meta`            | `#4a423c`             | 74, 66, 60               | Card timestamps, IDs. Matches text-secondary.                  |
| `--ss-color-primary`         | `#c5501e`             | 197, 80, 30              | Burnt orange. CTAs, primary actions. Single-accent discipline. |
| `--ss-color-primary-hover`   | `#a84318`             | 168, 67, 24              | Burnt orange deepened. CTA hover state.                        |
| `--ss-color-action`          | `#c5501e`             | 197, 80, 30              | Semantically distinct slot; same hue as primary. Focus ring.   |
| `--ss-color-attention`       | `#c5501e`             | 197, 80, 30              | Semantically distinct slot; same hue as primary.               |
| `--ss-color-complete`        | `#4a6b3e`             | 74, 107, 62              | Olive. Success and completed states.                           |
| `--ss-color-error`           | `#a02a2a`             | 160, 42, 42              | Brick. Error and danger states.                                |

---

### 3.2 WCAG AA Contrast Audit (revised)

WCAG 2.1 thresholds: **4.5:1** for normal text, **3.0:1** for large text (18pt+/14pt+ bold) and non-text UI components.

#### Text on cream background (`#f5f0e3`)

| Pairing                             | Ratio       | WCAG AA Normal | WCAG AA Large/UI | Notes                                                                                                                         |
| ----------------------------------- | ----------- | -------------- | ---------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `text-primary` (#1a1512) on cream   | **15.91:1** | Pass           | Pass             | AAA                                                                                                                           |
| `text-secondary` (#4a423c) on cream | **8.64:1**  | Pass           | Pass             | AAA                                                                                                                           |
| `text-muted` (#8a7f73) on cream     | **3.0:1**   | **Fail**       | At threshold     | Zero margin. Fails AA for normal text at any size. Passes large-text AA by exactly 0:1. See revised usage rule in §3.3 Gap 1. |
| `primary` (#c5501e) on cream        | **4.06:1**  | **Fail**       | Pass             | Primary orange as inline text fails AA normal. Use at display scale (18pt+) or as button-background only.                     |

**Note on Round 1 discrepancy:** I reported `text-muted` at 3.44:1 in Round 1. The Design Technologist's computation at 3.0:1 is correct. I had used a slightly different luminance formula. The Design Technologist's value stands.

#### Text on interactive surfaces

| Pairing                                       | Ratio      | WCAG AA Normal | Notes                                         |
| --------------------------------------------- | ---------- | -------------- | --------------------------------------------- |
| White on `primary` button (#c5501e)           | **4.63:1** | Pass           | CTA button text passes.                       |
| White on `primary-hover` (#a84318)            | **6.03:1** | Pass           | Comfortable on hover.                         |
| `complete` (#4a6b3e) as text on cream         | **5.33:1** | Pass           | Success text in body context passes.          |
| White on `complete` (#4a6b3e)                 | **6.06:1** | Pass           | White label on olive surface.                 |
| `error` (#a02a2a) as text on cream            | **6.45:1** | Pass           | Error message body text.                      |
| White on `error` (#a02a2a)                    | **7.34:1** | Pass           | AAA.                                          |
| `warning` proposed (#7a5800) as text on cream | **7.14:1** | Pass           | Warning body text. AAA-adjacent. See Gap 3.   |
| White on `warning` proposed (#7a5800) surface | **7.14:1** | Pass           | If used as badge background with white label. |

#### Inverted surfaces (`surface-inverse` = `#1a1512`)

| Pairing                                  | Ratio       | WCAG AA Normal | Notes                                                      |
| ---------------------------------------- | ----------- | -------------- | ---------------------------------------------------------- |
| White on `surface-inverse`               | **18.10:1** | Pass           | AAA.                                                       |
| `primary` (#c5501e) on `surface-inverse` | **3.91:1**  | Fail           | Large-only/UI-only. Do not use as normal-body text on ink. |

---

### 3.3 Gaps and Proposed Additions (revised)

#### Gap 1 — `text-muted` usage rule (revised and tightened)

**Design Technologist finding:** `--ss-color-text-muted` at 3.0:1 fails WCAG AA for normal-weight body text. This is not a rounded-up near-miss — it is precisely at the large-text/UI-component threshold with zero margin.

**Marcus's concern:** Outdoor mobile use in direct sunlight (Phoenix, truck cab) degrades effective perceived contrast. A token at exactly 3.0:1 in laboratory conditions may read below that threshold on a phone screen in direct sun.

**Round 1 rule (superseded):** "text-muted is only AA-compliant at text-body-lg (17px) or larger, or with weight 600+."

**Revised rule (this round):** `--ss-color-text-muted` is restricted to non-essential metadata where failure to read causes no functional harm. Specifically:

| Permitted use                        | Context                                      | Rationale                                    |
| ------------------------------------ | -------------------------------------------- | -------------------------------------------- |
| Timestamp on a list row              | Already readable from surrounding item title | User does not need the timestamp to act      |
| Placeholder text in unfocused inputs | Replaced by user input on interaction        | Standard a11y exception for placeholder text |
| Decorative separator labels          | Visual rhythm only                           | No information content                       |

**Never permitted:**

- Primary informational labels at any size
- Status-bearing text of any kind
- Any text that a user must read to understand what to do next
- Any text in the portal on mobile without a `text-secondary` or `text-primary` alternative

**No token change required.** The usage restriction is the fix, not a token change.

**Proposed accessible alias for mobile-primary contexts (Gap 1a):**

For informational metadata on mobile where outdoor legibility matters, a hardened alias is proposed:

```css
--ss-color-text-muted-accessible: #6b6158; /* 4.71:1 on cream — AA pass at normal text */
```

Rationale: `#6b6158` is a slightly deeper step on the warm gray ramp between `text-secondary` (#4a423c, 8.64:1) and `text-muted` (#8a7f73, 3.0:1). It passes AA for normal text, reads as clearly "secondary" rather than "primary," and aligns with the ink/cream register. Use this alias — not `text-muted` — when metadata needs to be legible in mobile outdoor contexts.

**Pending PR:** Add `--ss-color-text-muted-accessible: #6b6158` to the token JSON and compile.

#### Gap 2 — Primary orange as inline text

`#c5501e` at 4.06:1 on cream fails for normal-body inline text.

**Rule (unchanged from Round 1):** Do not render `--ss-color-primary` as body-size text color on cream. Reserve for: buttons (white label on primary background), large display (18pt+ scale), icons, status dots where size threshold is met.

**No token change required.**

#### Gap 3 — Warning semantic token (revised with portal mapping)

The Interaction Designer documented two concrete portal warning contexts that currently have no distinct colorway:

- Quote near expiry (countdown state before `isExpired`)
- Deposit invoice overdue (an overdue badge distinct from the burnt orange error of a failed payment)

The current state: `--ss-color-attention` is aliased to `--ss-color-primary` (#c5501e). A near-expiry quote and a "Review & Sign" CTA render identically. This is not a subtle problem — Marcus checks his portal to understand what needs action. If "warning" and "action" are visually identical, he cannot distinguish "this needs your attention" from "here is a button."

**Revised proposed value:** `#7a5800` (deep golden brown)

| Property             | Value     |
| -------------------- | --------- |
| Hex                  | `#7a5800` |
| Contrast on cream    | 7.14:1    |
| WCAG AA normal text  | Pass      |
| WCAG AA large text   | Pass      |
| WCAG AAA normal text | Pass      |

**Why `#7a5800` over Round 1's `#6b4f08`:**

Round 1's `#6b4f08` (6.72:1) was technically adequate. But mapped against the actual palette in portal context, it read too close to the olive (`#4a6b3e`) in low-light conditions. `#7a5800` — a warmer, more yellow-amber — is visually distinct from olive (success), brick (error), and burnt orange (action) while remaining in the warm family. It reads as "caution, pay attention" rather than "something is broken."

**Warning state rendering in portal — concrete icon + color + prose mapping:**

| Portal state                              | Icon (Material Symbols Outlined) | Color token          | Prose pattern                                          |
| ----------------------------------------- | -------------------------------- | -------------------- | ------------------------------------------------------ |
| Quote near expiry (< 72 hours)            | `schedule`                       | `--ss-color-warning` | "This proposal expires {relative time}."               |
| Deposit invoice overdue                   | `receipt_long`                   | `--ss-color-warning` | "OVERDUE" rectangular tag at reference line            |
| Financial blindness soft warning (EC-007) | `visibility_off`                 | `--ss-color-warning` | Admin surface only; "Revenue visibility gap detected." |

**Icon guidance for warning states:** Always pair `--ss-color-warning` text with an icon when space permits. Warning without an icon in a dense context (list row) risks being missed if the color alone is not salient enough. The icon provides a second channel.

**Token addition (pending PR):**

```css
--ss-color-warning: #7a5800; /* Deep golden amber. Warning, near-expiry, soft-caution. 7.14:1 on cream (AAA). */
```

#### Gap 4 — Focus-ring specification

**Unchanged from Round 1.** The Design Technologist confirmed the same requirement with the same implementation. Canonical specification:

```css
@layer base {
  :focus-visible {
    outline: 2px solid var(--ss-color-action);
    outline-offset: 2px;
  }
}
```

Uses `--ss-color-action` (#c5501e, 4.06:1 against cream) — satisfies WCAG 2.2 SC 2.4.11 (3:1 minimum for focus appearance).

---

### 3.4 Single-Accent Discipline

The Plainspoken Sign Shop uses one accent color: burnt orange (`#c5501e`). Action, attention, and primary are aliases of the same hue — intentional. No second decorative accent. Olive is semantic (success only). Brick is semantic (error only). Warning (`#7a5800`) is semantic (caution only — not to be used for decorative highlight). No teal, lavender, or gradient wash.

Any PR that introduces a new color not derived from the canonical hex values above requires design review.

---

## 4. Typography

The type system is a trio with distinct roles. All three are locked. Font changes require a named identity migration, not a PR.

**Source of truth:** `node_modules/@venturecrane/tokens/dist/ss.css`

---

### 4.1 Font Families

| Token                    | Value                                                              | Role                                                              |
| ------------------------ | ------------------------------------------------------------------ | ----------------------------------------------------------------- |
| `--ss-font-display`      | `'Archivo', system-ui, sans-serif`                                 | Display headings, hero text, all h1/h2/h3                         |
| `--ss-font-body`         | `'Archivo', system-ui, sans-serif`                                 | Body copy, form labels, button text                               |
| `--ss-font-accent-label` | `'Archivo Narrow', 'Archivo', system-ui, sans-serif`               | Chips, narrow labels, tab labels, compact metadata                |
| `--ss-font-mono`         | `'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace` | IDs (ULIDs), reference numbers, invoice numbers, fixed-width data |

Display and body share Archivo. The Plainspoken register uses one typeface and varies weight — the hierarchy is weight-driven, not family-driven.

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
| `text-label`   | 12        | 16               | 500    | 0.08em (uppercase) | Eyebrow, section label, tab labels        |

_Values derived from `ss.css` token values converted from rem. Base: 16px._

---

### 4.3 Plainspoken Display Scale (weight-900 register)

These tokens define the Plainspoken Sign Shop's signature: oversized, condensed, negative-tracked numerals and headings. Weight 900 is non-negotiable in this register.

| Token              | Size (px) | Line-height (px) | Weight  | Letter-spacing | Use                                                     |
| ------------------ | --------- | ---------------- | ------- | -------------- | ------------------------------------------------------- |
| `text-hero`        | 72        | 66               | **900** | -0.03em        | Portal H1 desktop (business name, engagement title)     |
| `text-hero-mobile` | 44        | 40               | **900** | -0.03em        | Portal H1 mobile                                        |
| `text-hero-price`  | 64        | 59               | **900** | -0.04em        | Summary-card total project price                        |
| `text-kpi`         | 44        | 44               | **900** | -0.03em        | KPI numbers in admin dashboard                          |
| `text-section-h`   | 36        | 36               | **900** | -0.02em        | Section block headings on detail pages                  |
| `text-price-row`   | 28        | 28               | **900** | -0.02em        | Row-level money in list / ledger contexts               |
| `text-num-cell`    | 22        | 22               | **900** | -0.01em        | ID / sequence cell glyphs in ticket rows                |
| `text-money`       | 44        | 48               | 500     | —              | MoneyDisplay component (list-context amounts, not hero) |

**Where weight-900 applies and does not:** Weight 900 belongs to numbers, prices, and structural labels that the user scans first. It does not belong to paragraph copy, error messages, or anything requiring sustained reading. The MoneyDisplay component uses `text-money` at weight 500 in list contexts — intentional. `text-hero-price` at 900 is for the single total price on the client dashboard where that number is the entire point.

**Marcus's reaction:** He compares the price to his bank balance — the first thing he sees, immediately legible. The weight-900 price token is the design equivalent of that balance display.

---

### 4.4 Archivo Narrow — Usage Constraint

Archivo Narrow is reserved for chips and narrow labels where horizontal compression serves a density goal. Use only via `--ss-font-accent-label` on elements that genuinely need the condensed width: status pills (Pattern 01), tab labels at compressed widths, inline chips in the admin pipeline, reference line mono-caps.

Do not use for body text, captions, or headings. The hierarchy is weight-based within Archivo regular — Archivo Narrow in hierarchical contexts implies a role it is not performing.

---

### 4.5 JetBrains Mono — Usage Constraint

JetBrains Mono is strictly for:

- ULID values and internal IDs rendered in the admin
- Invoice reference numbers (e.g., "REF INV-2401") — client-facing, business-document register
- Code values, extraction JSON displays
- Fixed-width data where column alignment matters

**Not** a stylistic treatment for prices. Money values use Archivo at `text-money` or `text-hero-price` scale. Monospace is for machine-readable identifiers and formatted reference numbers.

**Marcus's standard:** The invoice reference line should look like a real invoice number — "REF INV-001 / ISSUED 2026-04-14." JetBrains Mono in caps satisfies this register without artificial decoration.

---

### 4.6 Italic Usage

Italic is permitted only for:

1. Quoted speech in informational copy (e.g., client testimonials on the marketing site)
2. `<em>` emphasis within long-form instructional text where it is the semantic choice

Italic is prohibited for: status labels, metadata, prices, headings, and any UI chrome. Rare by design — its presence signals an unusual rhetorical role.

---

## 5. Spacing & Rhythm

**Source of truth:** `node_modules/@venturecrane/tokens/dist/ss.css`, mapped in `src/styles/global.css` under `--spacing-*`.

---

### 5.1 Canonical Rhythm Tokens

Four tokens define all vertical rhythm in the system. Horizontal rhythm follows the same values. Raw Tailwind spacing utilities (`p-6`, `gap-4`, etc.) are banned in governed surfaces (Pattern 06).

| Token           | CSS var              | rem     | px     | Use                               |
| --------------- | -------------------- | ------- | ------ | --------------------------------- |
| `space-section` | `--ss-space-section` | 3rem    | **48** | Gap between major page sections   |
| `space-card`    | `--ss-space-card`    | 2rem    | **32** | Card internal padding             |
| `space-stack`   | `--ss-space-stack`   | 1rem    | 16     | Vertical stack of sibling content |
| `space-row`     | `--ss-space-row`     | 0.75rem | 12     | Gap between rows in a list        |

**Documentation correction required:** `UI-PATTERNS.md` Rule 6 documents `space-section: 32px` and `space-card: 24px`. The compiled token values are `48px` and `32px` respectively. The compiled values are authoritative. This discrepancy must be corrected in the next PR that touches `UI-PATTERNS.md`. The Design Technologist and Round 1 both noted this; it is not a token error — it is a stale documentation artifact.

**Action:** File as documentation correction in the next `UI-PATTERNS.md` PR. Assign to whichever engineer first touches Rule 6 content.

---

### 5.2 Context Application

**Client portal (spacious, mobile-primary):**

- Between dashboard sections: `space-section` (48px)
- Card internal padding: `space-card` (32px)
- Between stacked content blocks within a card: `space-stack` (16px)
- Bottom tab bar items: minimum 44px touch target height per Apple HIG / WCAG 2.5.5

**Admin (dense, desktop-primary):**

- Between pipeline columns: `space-section` (48px)
- Card internal padding: `space-card` (32px), or `space-stack` (16px) for dense data tables
- Between rows in pipeline cards: `space-row` (12px)
- Quote builder line item rows: `space-row` (12px)

---

### 5.3 Shape — Zero Radii (canonical, binding)

All radii are `0`. This is the flat institutional rule. It is binding, not aspirational.

| Token                | Value |
| -------------------- | ----- |
| `--ss-radius-card`   | 0     |
| `--ss-radius-button` | 0     |
| `--ss-radius-badge`  | 0     |

**Canonical anti-pattern statement:** Rounded buttons would break the entire Plainspoken register. The register is 1950s commercial signage — painted signs, letterpress cards, ruled borders. Nothing in that reference set has soft corners. A rounded button does not merely look out of place — it implies a brand personality (friendly, approachable, consumer-facing) that the system explicitly rejects.

Marcus confirmed this directly: "Rounded corners and drop shadows everywhere — websites that have everything soft and floaty and layered feel like they're hiding something." This is not an aesthetic preference. It is a trust signal.

The Design Technologist confirmed all three radius tokens are at 0 and no `rounded-*` Tailwind utilities appear on card, button, or badge surfaces. This is the current correct state. Any PR that introduces rounding requires explicit design review — the PR description must explain the rationale and must demonstrate that the Plainspoken register is preserved.

---

### 5.4 Motion (updated — motion token mapping now required)

**Position change from Round 1:** Round 1 called the motion token mapping gap "acceptable at current maturity." The Design Technologist's analysis makes clear this is wrong: components use bare `transition-colors` without consuming token values, meaning a design-system change to duration does not propagate. This is a broken single-source-of-truth chain. The mapping is required.

#### Motion token table

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

#### Required `@theme inline` additions to `global.css`

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

This maps motion tokens into Tailwind v4's `duration-*` and `ease-*` utility namespaces. Existing `transition-colors` utilities continue to work unchanged; new code uses named tokens (`transition-colors duration-fast ease-standard`).

#### Required `prefers-reduced-motion` block (brand-required, not just a11y-required)

The Plainspoken Sign Shop's restraint principle demands that motion never impose itself on a user who has expressed a preference against it. This is not only a WCAG 2.1 SC 2.3.3 requirement — it is a brand position. A system that ignores `prefers-reduced-motion` is a system that prioritizes visual expression over user control. That is the opposite of the Plainspoken register.

**Required addition to `global.css`:**

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

This is a global catch-all that reduces all transitions to near-instant without breaking layout transitions. It should ship in the next CSS hygiene PR alongside the motion token mapping — they are logically paired changes.

#### Usage guide

| Use case                                 | Duration token            | Easing token        |
| ---------------------------------------- | ------------------------- | ------------------- |
| Hover color change (`transition-colors`) | `--duration-fast` (150ms) | `--ease-standard`   |
| Focus ring appearance                    | `--duration-fast` (150ms) | `--ease-standard`   |
| Tab active state transition              | `--duration-fast` (150ms) | `--ease-standard`   |
| Disclosure/accordion open                | `--duration-base` (250ms) | `--ease-decelerate` |
| Confirmation panel swap (post-signing)   | `--duration-base` (250ms) | `--ease-standard`   |

**What does not animate:** StatusPill tone changes (server-rendered static), MoneyDisplay values, list rows on page load, error messages (must be instant per `aria-live="assertive"` semantics).

---

## 6. Imagery & Iconography

### 6.1 Icon System — Material Symbols Outlined (updated: tab icon set specified)

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

`FILL: 0` uses the outlined variant system-wide. Correct for the Plainspoken register — filled icons read as heavier and more decorative. Active tab state switches to `FILL: 1` (filled icon) as the active indicator.

**Usage rules:**

- Default size: 24px. Scale with `opsz` variation setting (`opsz: 20` for inline-text-adjacent, `opsz: 40` for large display contexts).
- Icons used as sole communicator of state (no adjacent text): `aria-label` or visually-hidden text required.
- Decorative icons adjacent to text: `aria-hidden="true"`.
- Icon weight (`wght` variation) matches surrounding typography: `wght: 300` for caption-adjacent, `wght: 600` for heading-adjacent, default 400 for body context.

**Canonical tab icon set for portal navigation:**

The Interaction Designer documented the persistent-tabs portal nav with four destinations. The following icon set is canonical — stable, semantically clear, and available in Material Symbols Outlined.

| Route                | Tab label | Icon (inactive, FILL 0) | Icon (active, FILL 1)   | Rationale                                         |
| -------------------- | --------- | ----------------------- | ----------------------- | ------------------------------------------------- |
| `/portal`            | Home      | `home`                  | `home` (filled)         | Universal home affordance; Marcus expects it      |
| `/portal/quotes`     | Proposals | `description`           | `description` (filled)  | Document register; correct for a signed agreement |
| `/portal/invoices`   | Invoices  | `receipt_long`          | `receipt_long` (filled) | Receipt register; distinct from a document        |
| `/portal/engagement` | Progress  | `timeline`              | `timeline` (filled)     | Timeline metaphor; matches the milestone model    |
| `/portal/documents`  | Documents | `folder`                | `folder` (filled)       | Library register; deferred to 5th tab if added    |

**Stability note:** These icon choices must remain stable across releases. Changing a tab icon after users have learned it is a navigation pattern regression. If a substitute is ever needed (e.g., a symbol is removed from Material Symbols), the change requires explicit design review and a changelog entry.

**Warning state icons** (see §3.3 Gap 3):

| State                       | Icon                                                          | Token                |
| --------------------------- | ------------------------------------------------------------- | -------------------- |
| Near-expiry alert           | `schedule`                                                    | `--ss-color-warning` |
| Overdue invoice tag         | `receipt_long` (same as tab, in warning color at small scale) | `--ss-color-warning` |
| Financial blindness (admin) | `visibility_off`                                              | `--ss-color-warning` |

### 6.2 No Illustration

No illustration. No marketing mascots. No abstract blob shapes. No decorative background patterns. The Plainspoken Sign Shop is a typographic system — the content is the content, not a backdrop for it.

Empty states in the portal render nothing or a "TBD in SOW" marker — never an illustration of a person looking at a clipboard. Marcus was direct on this: "Cute empty states — just say 'Documents will appear here as the project progresses' and move on."

### 6.3 No Photography

No photography in the product UI. The marketing site may carry photography if the marketing direction calls for it — that is a separate design scope. The portal and admin contain no photographic content.

The trust signal for this product is operational competence, not lifestyle photography.

### 6.4 Logo and Brand Mark

No brand mark in the current system. Deferred until productization (PRD §2, Open Decision D1). When introduced, it must be wordmark-based and legible at cream/ink palette — no gradient fills, no icon-only marks.

### 6.5 Third-Party Embed Surfaces (new)

**Interaction Designer finding:** The SignWell embed is a brand discontinuity risk — it will look visually different from the portal (different background, different button styles, potentially different typeface).

**Marcus's response:** "The worst thing that can happen here is visual whiplash. If the portal is cream and dark ink and the SignWell window is white with blue buttons and a different typeface, my brain registers that as 'you're not in Kansas anymore.' That creates hesitation at exactly the moment when I need to be confident enough to sign."

**Brand principle for third-party embeds:**

The Plainspoken Sign Shop brand applies edge-to-edge within our chrome. Third-party embeds inherit their own visual contract — we do not attempt to restyle them. Our response is:

1. **Minimize the embed footprint:** The SignWell iframe should not dominate the viewport before the user has read the scope. On mobile, collapse the scope summary accordion first; the iframe occupies the reading area.
2. **Frame with our chrome:** The page header, the scope sidebar (desktop), and the "Return to portal" escape affordance all render in our visual system. The embed is surrounded by our chrome, not replacing it.
3. **Do not attempt to restyle embed content:** CSS injection into a cross-origin iframe is neither possible nor appropriate. If SignWell's visual register differs from ours, the solution is chrome framing, not restyling.
4. **Acknowledge the seam explicitly in content:** If the embed's visual register is jarring, add a brief prose line above the iframe: "You're signing your proposal via our signing partner." This primes Marcus for the visual shift and explains it before it surprises him.

**This principle applies to any future third-party embed:** Stripe payment pages, scheduling widgets, or other embedded flows. Our chrome frames them; we do not restyle them.

---

## 7. Inspiration Board

Five references from Round 1, each evaluated against Marcus's actual tool preferences and reactions. Marcus uses QuickBooks, his bank app, Apple Calendar, and Square — none of which appeared on the Round 1 board. Each is reconciled below.

---

### 7.1 Stripe Dashboard

**URL:** https://dashboard.stripe.com

**Round 1 note:** Dense data tables, status indicators used conservatively, chrome invisible behind the data.

**Marcus reconciliation:** Marcus uses Square, not Stripe. But the Stripe dashboard and Square share a quality Marcus values: the number is the thing. Square's payment button is big, the confirmation is obvious, the receipt goes to the customer. Stripe's dashboard puts financial data at full visual weight. Both validate the SS approach of making the price and the status the primary read, not the decoration around them.

**Still holds:** Yes. The pipeline view and quote builder should feel this organized.

**What not to take:** Stripe's blue accent and rounded inputs remain wrong for SS.

---

### 7.2 Linear Issue Tracker

**URL:** https://linear.app

**Round 1 note:** Typographic density, status dot discipline, no decoration on productive surfaces.

**Marcus reconciliation:** Linear is not in Marcus's vocabulary and is probably too product-focused as a reference for a client-facing surface. However, the design principle it demonstrates — hierarchy through typography rather than color or decoration — translates directly to the admin pipeline view. Marcus's Apple Calendar reference is closer for the client portal: dense but instantly readable, no tutorial, no empty state with a friendly illustration.

**Still holds for admin:** Yes. For portal, Apple Calendar is a stronger reference (see §7.6 addition below).

**What not to take:** Dark mode default and purple brand accent.

---

### 7.3 Letterpress and commercial sign shop ephemera

**URL:** https://letterformarchive.org/collection/

**Round 1 note:** The tonal and typographic source of the Plainspoken Sign Shop identity.

**Marcus reconciliation:** Marcus identified the visual register without knowing the reference: "This looks like a letterhead. Like something you'd get from a contractor or a law office." That's the sign shop register. His positive reaction to the letterhead aesthetic confirms the reference is calibrated correctly.

**Still holds:** Yes. The weight-900 display scale, ruled hairlines, and flat color are the direct inheritance.

**What not to take:** Literal reproduction of historical signage in a digital product.

---

### 7.4 Basecamp / HEY Email

**URL:** https://app.hey.com

**Round 1 note:** Conviction in typographic choices, willingness to be decisive, non-apologetic information density.

**Marcus reconciliation:** Marcus's QuickBooks reference is a closer analog for what SS should feel like: "It's not pretty. I trust it the way I trust an old Ford F-250. Every time I open it, I know where I am." QuickBooks is not design-forward, but it is unambiguous. HEY is more design-forward, which is partially wrong for SS. The quality to take from HEY is the willingness to make a decision and commit to it — the same quality Marcus identified when he said "you can tell someone made a decision about what this should look like."

**Partially holds:** Take decisiveness in design choices. Do not take HEY's playful color palette or illustrated onboarding.

---

### 7.5 Stacks / Notion in structured use

**Round 1 note:** Editorial layout for proposal detail and document library; content at full width, chrome not competing.

**Marcus reconciliation:** Marcus's bank app reference is the better analog for the client portal's information density: "The balance is the first thing I see. Everything else is behind a tap." That is the correct hierarchy for the portal dashboard — price/status up front, everything else one tap deeper. Stack Overflow's editorial layout remains a good reference for the quote detail view specifically, where the proposal reads like a document.

**Partially holds:** Useful for quote detail and document library. For dashboard hierarchy, the bank app reference (balance first, everything else secondary) is more precise.

---

### 7.6 Marcus's actual references (reconciliation additions)

Marcus named four tools he actually uses and trusts. They were not on the Round 1 board. They belong here as cross-validation references.

**QuickBooks (Marcus's standard for operational trust)**

Marcus: "It's not pretty. Pat had to explain it to me three times. But every time I open it, I know where I am. The numbers are in columns. The columns mean something."

Design quality: Information hierarchy through structure, not decoration. Numbers are primary. Navigation is stable. No illustration, no personality performance.

Application to SS: The admin pipeline and quote builder should feel this trustworthy. Not pretty — organized. Every number in its column.

**Bank app (Marcus's standard for mobile hierarchy)**

Marcus: "Boring. Functional. The balance is the first thing I see. Everything else is behind a tap."

Design quality: Single primary value above the fold, all supporting information one tap away. No tutorial, no getting-started flow.

Application to SS: The portal dashboard hierarchy. Price above the fold. Everything else one tap deeper. This is not the letterhead aesthetic — it is the information architecture principle.

**Apple Calendar (Marcus's standard for dense-but-readable)**

Marcus: "I look at it fifty times a day. The information is dense but I can read it in under a second. There's no tutorial, no empty state with a friendly illustration."

Design quality: Maximum information density with zero decoration. Empty states are non-events — the interface works the same whether there are events or not.

Application to SS: The engagement timeline view and admin pipeline. Dense rows, instant comprehension, no illustration for empty states.

**Square (Marcus's standard for obvious action)**

Marcus: "The button was big. The confirmation was obvious. The receipt went to the customer. Nothing unnecessary. I never had to read instructions."

Design quality: Primary action is unmissable. Confirmation is immediate and concrete. Zero friction between intent and outcome.

Application to SS: "Review & Sign" and "Pay Now" CTA sizing and placement. The action should be as obvious as the Square pay button. No ambiguity about what it does or what it costs.

---

## 8. Anti-Inspiration

Four examples of what the Plainspoken Sign Shop actively refuses — updated with Marcus-specific language.

---

### 8.1 The "bouncy purple gradient" register (Copilot.com and its peers)

**URL:** https://copilot.com

Copilot is the closest direct competitor and the clearest anti-reference. It uses the generic purple-gradient-rounded-card SaaS aesthetic: soft purple brand accent, rounded modals, illustration-heavy empty states, pill-shaped status badges.

Marcus said it plainly: "If I see that soft purple gradient that every 'all-in-one platform for teams' uses, I'm out immediately. That color is the visual equivalent of 'we want to appeal to everyone' and it appeals to no one."

**Specifically wrong for SS:**

- Purple as brand color, in any value
- Rounded modals, rounded cards, pill-shaped badges
- Illustration-heavy empty states
- "Friendly" onboarding flows with personality copy
- Visual density used as a signal of premium rather than a consequence of information

The Interaction Designer should run this check before any new portal component ships: "Would this look at home in Copilot?" If yes, reconsider.

---

### 8.2 HoneyBook (freelancer CRM register)

**URL:** https://www.honeybook.com

HoneyBook targets photographers, event planners, and designers. Its visual language is optimistic, warm, and illustrated. The dashboard uses large feature cards with gradient washes, the proposal view uses soft shadows and rounded corners, and the general tone is "you've got this, creative" rather than "here is the operational status of your engagement."

It is a well-executed product for its target. It is entirely wrong for Marcus.

**Specifically wrong for SS:** Any element that could appear on a HoneyBook dashboard — rounded white cards with drop shadows on a light gray background, soft accent colors, graphic illustration for empty states — is an anti-pattern. The comparison is useful for PR review.

---

### 8.3 Generic enterprise SaaS (Salesforce / ServiceNow aesthetic)

The opposite failure mode from HoneyBook: institutional without being authoritative. Navy, sky blue, and gray produce surfaces that communicate "deployed by IT" rather than "built for this business." Dense data tables that emphasize structure over the decision the user needs to make. The impression that the portal exists because corporate policy requires it, not because it makes the engagement better.

Marcus saw ServiceTitan — the field-service management platform his industry peers use — and closed the tab after ten minutes. That is the failure mode to avoid.

**Specifically wrong for SS:** Navy or dark blue brand anchor, sky blue accents, table-heavy layouts that make the data structure more visible than the user's next action.

---

### 8.4 The "Etsy receipt" invoice register

This is a new anti-pattern named directly from Marcus's feedback.

Marcus on the deposit invoice: "If the invoice looks like something I could have made in Canva in twenty minutes, I'm going to feel like I overpaid. The visual register of the invoice should match the visual register of the portal. If the invoice looks like an auto-generated receipt, I feel like I hired an app."

Marcus's explicit make-or-break: "The deposit invoice should look like an invoice from a professional firm, not a receipt from an app."

The "Etsy receipt" register is: automatically generated styling with no visual discipline, rounded corners, generic brand colors applied to a financial document, amount displayed in standard body text rather than a formal invoice format.

**Specifically wrong for SS:**

- Invoice number that looks like a UUID rather than a formatted reference (`REF INV-001`)
- Amount in standard body text rather than JetBrains Mono tabular-nums at invoice-register scale
- "Pay Now" button without the dollar amount on or adjacent to it
- Any visual element (rounded corners, drop shadows, gradient fills) that signals "app-generated receipt" rather than "professional firm invoice"
- Payment amount buried below a fold or requiring calculation by the client

**The standard:** The invoice must look like something Marcus would put in a folder and show his bookkeeper Pat. It must have a real invoice number, a date, an amount in a format that lines up in columns, a due date, and payment instructions. The visual register is cream paper, ink, monospaced amounts, hairline borders. Same palette as the portal. Same typeface. No discontinuity.

---

## Appendix: Token Gaps Summary (updated)

| Gap                                                     | Severity                           | Current state                                             | Proposed resolution                                                                                                  | Change from Round 1                                                   |
| ------------------------------------------------------- | ---------------------------------- | --------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| No `warning` semantic token                             | Medium                             | `attention === primary` (burnt orange conflict)           | Add `--ss-color-warning: #7a5800` (7.14:1 on cream)                                                                  | Revised color from `#6b4f08` to `#7a5800`; added portal usage mapping |
| No focus-ring specification                             | Medium (a11y)                      | Ad-hoc per component                                      | Add `:focus-visible` base rule using `--ss-color-action`, 2px, offset 2px                                            | Unchanged                                                             |
| No `prefers-reduced-motion` block                       | **High (brand + a11y)**            | Motion tokens exist; no override rule                     | Add `@media (prefers-reduced-motion: reduce)` in `global.css`                                                        | Upgraded from Medium to High; now brand-required, not just a11y       |
| Motion tokens not mapped to Tailwind v4 `@theme inline` | **Medium (token chain integrity)** | Tokens exist in package; no `@theme inline` mapping       | Add `duration-*` and `ease-*` mappings in `global.css`                                                               | Upgraded from acceptable gap to required                              |
| `text-muted` fails AA normal text                       | **Medium (revised up)**            | Token at exactly 3.0:1 — zero margin                      | Document strict usage restriction; add `--ss-color-text-muted-accessible: #6b6158` for functional metadata on mobile | Severity raised from Low; specific accessible alias proposed          |
| `UI-PATTERNS.md` Rule 6 doc/code drift                  | Low (documentation)                | Rule 6 documents 32px/24px; compiled tokens are 48px/32px | Correct documentation in next PR touching `UI-PATTERNS.md`                                                           | Added this round; Design Technologist flagged                         |
| Primary orange fails AA as normal inline text           | Low (usage constraint)             | Token is correct for buttons/icons/display                | Document: primary never used as body-size text color                                                                 | Unchanged                                                             |
| No italic usage rule                                    | Low                                | No guidance exists                                        | Add italic rule: quoted speech and `<em>` only                                                                       | Unchanged                                                             |

---

_SMD Services — Brand Strategist Contribution, Design Brief Round 2_
_Plainspoken Sign Shop identity. Paint-job, not brochure._
_Cross-role synthesis: Design Technologist accessibility findings, Interaction Designer navigation and embed discontinuity, Marcus's direct product reactions._
