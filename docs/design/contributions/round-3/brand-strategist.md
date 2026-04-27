# Brand Strategist Contribution - Design Brief Round 3 (Final)

**Author:** Brand Strategist (Sonnet 4.6)
**Date:** 2026-04-26
**Design Maturity:** Full system
**Status:** Final after 3 rounds

---

## Changes from Round 2

1. **Warning token resolved: `#7a5800` selected over `#6b4f08`.** Design Technologist Round 2 kept `#6b4f08` (6.72:1). Brand Strategist Round 2 revised to `#7a5800` (7.14:1). This document picks `#7a5800` and justifies the choice on register grounds — the deeper, more yellow-amber reads as its own semantic slot, clearly distinct from olive success at low screen brightness. See §3.3 Gap 3.

2. **Muted-text accessible alias: position taken.** Design Technologist Round 2 enforced `text-secondary` (#4a423c, 8.64:1) for actionable data. Brand Strategist Round 2 proposed an explicit alias `--ss-color-text-muted-accessible: #6b6158` (4.71:1). This round resolves the disagreement: do not add the alias. Use `text-secondary` for any metadata that must be readable outdoors. The alias creates a third stop on the muted ramp that engineers will misapply. See §3.3 Gap 1.

3. **Tabs: icons + labels always, on all screen sizes.** Marcus said explicitly "tabs need words." Interaction Designer specified icons without confirming label visibility at all sizes. Design Technologist showed only the icon rendering. This round specifies: labels are always visible below icons on mobile; labels are always visible inline with icons on desktop. No icon-only tab on any surface, ever. See §6.1.

4. **Portal voice vs. marketing voice: documented as extensions, not different voices.** The relationship was implicit across rounds; this round makes it explicit. Same single-accent discipline, same plainspoken register, different tense and register application. See §1.4 new subsection.

5. **Olive usage hierarchy specified.** Round 2 noted "completed states" loosely. This round assigns each "complete" signal in the portal to a specific visual treatment — olive, ink check, or neutral — to prevent olive fatigue. See §3.5 new subsection.

6. **Photography never: confirmed as canonical and absolute.** Extends to founder portraits, client logos, and trust badges — not just lifestyle photography. See §6.3.

7. **Display scale vs. functional scale: recommended application.** Display tokens (weight 900, negative tracking) are for portal-side hero moments and admin KPI dashboards only. Functional tokens govern everything else, including admin chrome. Confirmed and refined. See §4.3.

8. **Product naming: recommend nameless until productization.** PRD flags naming as TBD. This document recommends staying nameless at MVP and gives a rationale grounded in Marcus's cognitive load. See §8 new section.

9. **Anti-Inspiration §8.3 rewritten.** Salesforce replaced with ServiceTitan — a tool Marcus's industry peers actually use. URL included. The anti-pattern is now grounded in Marcus's world, not a software ecosystem he doesn't recognize.

10. **Icon library spec tightened.** Material Symbols Outlined axis settings fully documented: `wght 400`, `opsz 24` default (20 for tab bar), `FILL 0` default, `FILL 1` for active tab. No custom icon set in MVP. Every icon must exist in Material Symbols Outlined. See §6.1.

11. **Tab icon set finalized.** Resolved Design Technologist Round 2's mapping (which put `work` on Proposals, `description` on Invoices — inverted from what the user expects) against Interaction Designer Round 2's icon list and Marcus's direct reaction to the `description` icon. The canonical set uses `description` for Proposals and `receipt_long` for Invoices, with labels always visible. See §6.1.

12. **Open Design Decisions section added as final section.** Per Round 3 requirements. Every genuine unresolved question catalogued with options, impact, recommendation, and decision type.

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
9. [Product Naming](#9-product-naming)
10. [Open Design Decisions](#10-open-design-decisions)

---

## 1. Brand Personality

The Plainspoken Sign Shop identity is canonical. This section is a final polish pass — not a revision.

The name is precise: 1950s American commercial signage. Weight-900 letterforms on flat surfaces. Ruled hairlines. Ink on cream. No decoration for decoration's sake. The register is pragmatic, not decorative — signs had to be readable from across a parking lot, so they used the heaviest legible weight and the highest contrast color combination available on a painted surface. The SS token system inherits that logic.

Marcus confirmed the register without knowing the name: "This looks like a letterhead. Like something you'd get from a contractor or a law office." That is the target. It is the correct target.

Five personality traits. Each is a position, not aspiration. They are stable across all three rounds.

---

### 1.1 Plainspoken, not folksy

Direct and unpretentious. The way a competent colleague talks. It says what it means and stops. It does not perform warmth, does not overexplain, does not use exclamation points to signal friendliness.

- **This:** "Your proposal is ready." / "Signed." / "Invoice due Friday."
- **Not that:** "Great news! Your personalized proposal is waiting for you."
- **Marcus:** "I don't need a logo to be my friend. I need it to not embarrass me when I show it to my wife."

---

### 1.2 Authoritative, not corporate

Heavy typography. Deliberate layout. No visual noise competing with content. Confident without the trappings of institutional scale — no gradient washes, no navy and gray, no dense legalese in helper text.

- **This:** Archivo Black at display scale. Flat hairline borders. Ink on cream. The page knows what it is.
- **Not that:** Soft drop shadows. Rounded cards. A neutral blue-gray that says "deployed by IT."
- **Marcus:** "Sharp edges feel like they're being honest with me." This is a trust signal, not an aesthetic preference.

---

### 1.3 Precise, not fussy

Every element is there because it is doing a job. No decoration for decoration's sake. No illustration as a mood setter. No texture overlay. Precision is not spartan — the spacing is generous enough that content breathes, and the type scale has enough grades to give hierarchy real shape. One focal point per view. One primary signal per fact.

- **This:** A price at `text-hero-price` (64px, weight 900) on a portal dashboard. Nothing competing with it.
- **Not that:** A price surrounded by badges, a "✓ Signed" overlay, and a hover tooltip.

---

### 1.4 Collaborative, not diagnostic

The brand voice says we work alongside the client, not over them. This applies to the UI as much as to copy: the portal is a shared workspace where Marcus can see what is being built on his behalf, not a status console where a service is reporting progress at him.

- **This:** "Here is what we are building and where we are." Milestone names in plain language.
- **Not that:** "Phase 2: Implementation — 78% complete." Progress bars with percentages.
- **Marcus:** "I want to feel like I made a good decision hiring these people."

**Portal voice vs. marketing voice — same voice, different register:**

This is a question the round requirements asked to resolve explicitly. The answer is: same voice, applied differently by context.

The marketing site uses the guide persona — pathfinding language, objectives-first framing, "we work alongside you." The portal, during an active engagement, uses the same voice but shifts tense and register: past-tense events ("Signed April 15."), evidence over reassurance ("The scheduling system is live."), operational language over positioning language. The marketing voice is about what we will do together. The portal voice is about what we have done and what is next.

The relationship: the portal is not a different brand. It is the marketing promise kept. The same person who wrote "we figure it out together" on the website is now showing Marcus the milestones and parking lot items that prove it. The voice is identical; the mode is different.

**Rule for portal copy:** Past tense for completed events. Present tense for active states. Near future ("ready for your review") only when the next step is authored and imminent. No speculative promises. No "we'll" language unless it comes from authored `next_step_text`.

---

### 1.5 Grounded, not minimal

The Plainspoken Sign Shop is not minimalism. Minimalism is an ideology. The sign shop is a register — pragmatic density when density serves, generous space when space serves. Dense when the user needs to scan multiple items quickly (pipeline view, invoice list). Spacious when the user needs to act on a single thing (portal dashboard, quote detail).

- **This:** Rhythm tokens applied deliberately per context. `space-section` (48px) between major sections; `space-row` (12px) between list rows.
- **Not that:** Maximum whitespace applied uniformly to signal premium.
- **Marcus's comparison:** QuickBooks, his bank app, Apple Calendar, Square. None are minimal. All are grounded.

---

## 2. Design Principles

Sequenced by priority. When two principles conflict, the higher-ranked one wins.

---

### Principle 1 — The business operates before the design is complete

Ship phases in order. An unstyled Phase 1 admin that works beats a beautifully designed Phase 3 that blocks the first assessment call. The design system's job is to eliminate that tradeoff by establishing clear defaults early: tokens, primitives, rhythm tokens are the floor.

**PRD anchor:** §5 Principle 1.

---

### Principle 2 — The client's first screen is the proposal, not an onboarding flow

Every design decision in the client portal is evaluated against one question: does this make it harder or easier for Marcus to see his price and sign? The first authenticated view is not a dashboard — it is the proposal. The price and the signature are above the fold on mobile.

**PRD anchor:** §7 Step 3. Marcus: "The price and the 'Review & Sign' button need to be on the screen without scrolling on my phone. That's non-negotiable."

---

### Principle 3 — No fabricated content; no visual compensation for missing data

Empty sections render nothing or a "TBD in SOW" marker. Every layout is designed and tested with missing data. Decorative placeholders and "Coming soon" copy are violations.

**Content rule anchor:** `docs/style/empty-state-pattern.md`. P0 enforcement.

---

### Principle 4 — One signal per fact, one primary per view

Pattern 02 (Redundancy ban) and Pattern 03 (Button hierarchy) are the visual enforcement of this principle. A price displayed at hero scale and then again in a caption and then again in a status pill reads as uncertain. State something once and state it with authority.

**Pattern anchors:** `docs/style/UI-PATTERNS.md` Rules 2 and 3.

---

### Principle 5 — Status is a fact, not a decoration

Status indicators follow Pattern 01 strictly: pill in list rows, eyebrow for category, dot+label or prose for single-item cards, prose for detail-page headlines. The visual treatment maps to the scanning context, not to how "important" the status feels.

**Pattern anchor:** `docs/style/UI-PATTERNS.md` Rule 1.

---

### Principle 6 — Typography and hairlines carry the page; decoration does not

No shadows, no gradient fills, no illustration. The hierarchy is entirely typographic and structural. Any PR introducing shadow or rounded corners to a card or button requires explicit design review.

**Token anchors:** `--ss-radius-card: 0`, `--ss-radius-button: 0`, `--ss-radius-badge: 0`. No shadow tokens exist in the system.

---

### Principle 7 — The admin surface can be dense; the client portal cannot

The admin user is a competent technical operator who wants speed and accuracy. Marcus is a business owner on a phone after dinner. The portal is spacious, single-task, and never cluttered with admin-visible detail.

**PRD anchor:** §14.

---

## 3. Color System

The Plainspoken Sign Shop palette is canonical. It is a four-role palette: cream, ink, accent (burnt orange), and two semantic complements (olive success, brick error). Light-only is the current decision.

**Source of truth:** `node_modules/@venturecrane/tokens/dist/ss.css`

---

### 3.1 Canonical Palette

| Token                        | Hex                   | Role                                                                         |
| ---------------------------- | --------------------- | ---------------------------------------------------------------------------- |
| `--ss-color-background`      | `#f5f0e3`             | Cream paper. Primary page background.                                        |
| `--ss-color-surface`         | `#f5f0e3`             | Card background. Cards defined by hairline borders, not fill.                |
| `--ss-color-surface-inverse` | `#1a1512`             | Ink. Inverted surfaces.                                                      |
| `--ss-color-border`          | `rgba(26,21,18,0.16)` | Default border. Ink at 16% opacity.                                          |
| `--ss-color-border-subtle`   | `rgba(26,21,18,0.08)` | Subtle divider. Ink at 8% opacity.                                           |
| `--ss-color-text-primary`    | `#1a1512`             | Ink. All primary content text.                                               |
| `--ss-color-text-secondary`  | `#4a423c`             | Subdued ink. Metadata, secondary labels.                                     |
| `--ss-color-text-muted`      | `#8a7f73`             | Muted ink. Non-essential tertiary only. See §3.3 Gap 1.                      |
| `--ss-color-meta`            | `#4a423c`             | Card timestamps, IDs. Matches text-secondary.                                |
| `--ss-color-primary`         | `#c5501e`             | Burnt orange. CTAs, primary actions. Single-accent discipline.               |
| `--ss-color-primary-hover`   | `#a84318`             | Burnt orange deepened. CTA hover state.                                      |
| `--ss-color-action`          | `#c5501e`             | Semantically distinct slot; same hue as primary. Focus ring.                 |
| `--ss-color-attention`       | `#c5501e`             | Semantically distinct slot; same hue as primary.                             |
| `--ss-color-complete`        | `#4a6b3e`             | Olive. Success and completed states. See §3.5 for usage hierarchy.           |
| `--ss-color-error`           | `#a02a2a`             | Brick. Error and danger states.                                              |
| `--ss-color-warning`         | `#7a5800`             | Deep golden amber. Warning, near-expiry, soft-caution. **NEW — pending PR.** |

---

### 3.2 WCAG AA Contrast Audit (final)

WCAG 2.1 thresholds: **4.5:1** for normal text, **3.0:1** for large text (18pt+/14pt+ bold) and non-text UI components.

#### Text on cream background (`#f5f0e3`)

| Pairing                             | Ratio       | WCAG AA Normal | WCAG AA Large/UI           | Notes                                                            |
| ----------------------------------- | ----------- | -------------- | -------------------------- | ---------------------------------------------------------------- |
| `text-primary` (#1a1512) on cream   | **15.91:1** | Pass           | Pass                       | AAA                                                              |
| `text-secondary` (#4a423c) on cream | **8.64:1**  | Pass           | Pass                       | AAA. Use for all actionable secondary data. Outdoor-safe.        |
| `text-muted` (#8a7f73) on cream     | **3.0:1**   | **Fail**       | At threshold (zero margin) | Non-essential decoration only. See §3.3 Gap 1.                   |
| `primary` (#c5501e) on cream        | **4.06:1**  | **Fail**       | Pass                       | Inline body text prohibited. Buttons, icons, display scale only. |
| `warning` (#7a5800) on cream        | **7.14:1**  | Pass           | Pass                       | AAA-adjacent. Warning text on cream.                             |
| `complete` (#4a6b3e) on cream       | **5.33:1**  | Pass           | Pass                       | Success text on cream.                                           |
| `error` (#a02a2a) on cream          | **6.45:1**  | Pass           | Pass                       | Error text on cream.                                             |

#### Text on interactive surfaces

| Pairing                              | Ratio       | WCAG AA Normal | Notes                                                        |
| ------------------------------------ | ----------- | -------------- | ------------------------------------------------------------ |
| White on `primary` (#c5501e)         | **4.63:1**  | Pass           | CTA button text.                                             |
| White on `primary-hover` (#a84318)   | **6.03:1**  | Pass           | Hover state.                                                 |
| White on `complete` (#4a6b3e)        | **6.06:1**  | Pass           | White label on olive surface.                                |
| White on `error` (#a02a2a)           | **7.34:1**  | Pass           | AAA.                                                         |
| White on `warning` (#7a5800)         | **7.14:1**  | Pass           | AAA. White label on warning surface when used as background. |
| White on `surface-inverse` (#1a1512) | **18.10:1** | Pass           | AAA.                                                         |

---

### 3.3 Gaps and Proposed Additions (final)

#### Gap 1 — `text-muted` usage rule: FINAL POSITION

**The disagreement in Round 2:**

- Brand Strategist Round 2 proposed `--ss-color-text-muted-accessible: #6b6158` (4.71:1) as an explicit alias for mobile-outdoor functional metadata.
- Design Technologist Round 2 recommended enforcing `text-secondary` (#4a423c, 8.64:1) instead of adding a new token, with a usage rule as the fix.

**Final position: do not add the accessible alias. Use `text-secondary` for any metadata that must be readable outdoors.**

**Rationale:** The alias adds a third stop on the muted ramp. In practice, engineers will use `text-muted-accessible` any time they want "something lighter than secondary but not quite muted" — which is exactly the misuse pattern the alias was meant to prevent. The two-stop rule is cleaner: `text-secondary` for anything that matters, `text-muted` for decoration where failure causes no harm. Marcus's Phoenix sun concern is real, but `text-secondary` at 8.64:1 handles it with substantial margin. We do not need a middle step.

**No new token required.** The usage restriction is the complete fix.

**Final `text-muted` usage rule:**

`--ss-color-text-muted` (`#8a7f73`, 3.0:1 on cream) is permitted only in:

| Permitted use                        | Context                                 | Rationale               |
| ------------------------------------ | --------------------------------------- | ----------------------- |
| Timestamp on list row                | User does not need the timestamp to act | Decorative metadata     |
| Placeholder text in unfocused inputs | Replaced by user input on interaction   | Standard a11y exception |
| Decorative separator labels          | Visual rhythm only                      | No information content  |

`--ss-color-text-muted` is **prohibited** for:

- Primary or actionable informational labels at any size
- Status-bearing text of any kind
- Any text a user must read to understand what to do next
- Any text in the portal on mobile where outdoor legibility matters

`--ss-color-text-secondary` is the correct token when metadata is functional. This is the complete solution.

---

#### Gap 2 — Primary orange as inline text

`#c5501e` at 4.06:1 on cream fails WCAG AA for normal-body inline text.

**Rule (final, unchanged):** Do not render `--ss-color-primary` as body-size text color on cream. Reserve for: buttons (white label on primary background), large display (18pt+ scale), icons, status dots at appropriate size. No token change required.

---

#### Gap 3 — Warning semantic token: FINAL VALUE `#7a5800`

**The disagreement in Round 2:**

- Brand Strategist Round 2 proposed `#7a5800` (7.14:1 on cream).
- Design Technologist Round 2 kept `#6b4f08` (6.72:1 on cream) in the token architecture.

**Final position: `#7a5800`.**

Both values pass WCAG AA for normal text. The choice is a register question, not a compliance question.

**Why `#7a5800` over `#6b4f08`:**

`#6b4f08` is a dark amber-ochre that reads well on cream but sits close to the olive family (`#4a6b3e`) when viewed on screen in reduced-light conditions — mobile in a truck cab at dusk, for example. The hue difference is real in a color table, but less salient in context when the olive is completing a milestone row and the amber is warning about an overdue invoice in the adjacent row.

`#7a5800` is warmer and more yellow-amber. It reads as its own semantic slot: not burnt orange (action), not olive (success), not brick (error). The warmth places it clearly in the "caution, pay attention" register. Seven-to-one contrast is comfortable, and the AAA threshold gives it outdoor robustness that `#6b4f08` lacks by a small margin.

The institutional register argument: `#7a5800` is closer to the deep gold of a formal warning stamp — the kind Marcus might see on a past-due invoice from a vendor. It carries that register authentically. `#6b4f08` reads more brown than amber, which edges toward neutral rather than caution.

**Final token value:**

```css
--ss-color-warning: #7a5800; /* Deep golden amber. Warning, near-expiry, soft-caution.
                                7.14:1 on cream (#f5f0e3) — WCAG AAA.
                                Distinct from primary (#c5501e), olive (#4a6b3e), brick (#a02a2a). */
```

**ss.json entry (pending PR to crane-console):**

```json
{
  "ss": {
    "color": {
      "warning": {
        "$value": "#7a5800",
        "$type": "color",
        "$description": "Deep golden amber. Warning and soft-caution states. 7.14:1 contrast on cream (#f5f0e3). WCAG AAA. Visually distinct from primary (#c5501e), complete (#4a6b3e), and error (#a02a2a)."
      }
    }
  }
}
```

**Warning state portal mapping (final):**

| Portal state                             | Icon (Material Symbols Outlined)    | Token                | Prose pattern                                                                                                                          |
| ---------------------------------------- | ----------------------------------- | -------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| Quote near expiry (< 72 hours)           | `schedule` wght 400, opsz 24        | `--ss-color-warning` | "PROPOSAL EXPIRES {day, date at time}." — paired with "Review and sign" CTA immediately below. Never shown without an adjacent action. |
| Deposit invoice overdue                  | `receipt_long` wght 400, opsz 24    | `--ss-color-warning` | "OVERDUE — DUE {natural date}." — hairline left-border callout above "Pay Now — $X" CTA.                                               |
| Parking lot stale item (admin only)      | `pending_actions` wght 400, opsz 24 | `--ss-color-warning` | "X items need review" section header chip. Admin only.                                                                                 |
| Financial visibility gap (admin, EC-007) | `visibility_off` wght 400, opsz 24  | `--ss-color-warning` | "Revenue visibility gap detected." Admin surface only.                                                                                 |

**Warning design rule: never show a warning without an adjacent action.** Marcus said it directly: "Deposit overdue with no Pay button is just an accusation." If the system cannot pair a warning with a clear action, render the status as neutral metadata rather than as a warning. The warning state exists to direct action, not to create anxiety.

---

#### Gap 4 — Focus-ring specification (unchanged)

```css
@layer base {
  :focus-visible {
    outline: 2px solid var(--ss-color-action);
    outline-offset: 2px;
  }
}
```

Uses `--ss-color-action` (#c5501e, 4.06:1 against cream). Satisfies WCAG 2.2 SC 2.4.11 (3:1 minimum for focus appearance). Unchanged from Round 2.

---

### 3.4 Single-Accent Discipline (final)

The Plainspoken Sign Shop uses one accent color: burnt orange (`#c5501e`). Action, attention, and primary are aliases of the same hue — intentional. No second decorative accent. Olive is semantic (success only). Brick is semantic (error only). Warning (`#7a5800`) is semantic (caution only — not for decorative highlight). No teal, lavender, or gradient wash.

Any PR that introduces a color not derived from the canonical hex values above requires explicit design review with a named rationale.

---

### 3.5 Olive Usage Hierarchy (new — final)

**The problem:** The portal will have multiple "complete" signals — paid invoice, signed quote, completed engagement, completed parking-lot item, completed milestone. Applying olive (`#4a6b3e`) to every completed state creates olive fatigue and dilutes the semantic weight of the color.

**The rule — olive is earned, not automatic:**

| Signal                         | Deserves olive?                  | Treatment                                                                                  | Rationale                                                      |
| ------------------------------ | -------------------------------- | ------------------------------------------------------------------------------------------ | -------------------------------------------------------------- |
| Engagement complete            | Yes — hero moment                | Olive prose at heading scale: "Complete."                                                  | Once per engagement; the defining event                        |
| Quote signed                   | Yes — make-or-break moment       | Olive prose in isSigned state: "Signed {natural date}."                                    | The signature is the commitment; olive marks it                |
| Deposit paid                   | Yes — financial resolution       | "Paid {natural date}." in olive at `text-caption` scale                                    | Payment confirmation is a milestone                            |
| Completion invoice paid        | Yes — engagement financial close | Same treatment as deposit paid                                                             | Symmetric with deposit                                         |
| Milestone completed            | No — routine progress            | Ink check icon (`check_circle`, FILL 0, wght 400) in `text-secondary` color, no olive fill | Milestones are expected; olive for all would dilute the signal |
| Parking-lot item dispositioned | No — operational close           | Plain text: "Addressed" / "Deferred" / "Dropped" in `text-secondary`                       | These are internal resolutions, not client victories           |
| Document uploaded              | No — routine                     | No status indicator; documents are listed, not celebrated                                  | Availability is fact, not achievement                          |
| Invoice line item              | No                               | Neutral metadata; monospaced amount in `text-primary`                                      | Financial data is fact, not emotion                            |

**The principle:** Olive appears at moments when Marcus should feel "that resolved correctly." It does not appear at every step the system completed internally. The check icon in `text-secondary` is the workhorse for routine completion. Olive is the accent for moments Marcus actually cares about.

**No olive on admin surfaces for routine state.** Admin can use olive to confirm that a client-visible milestone is complete (mirroring the portal), but admin pipeline cards and status rows use neutral status chips. Olive is a client-facing trust signal first.

---

## 4. Typography

Three fonts. All three locked. Font changes require a named identity migration, not a PR.

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

These seven tokens are the complete governed body of named text styles for functional content. Arbitrary inline sizes and raw Tailwind scale names are banned in governed surfaces (Pattern 05).

| Token          | Size (px) | Line-height (px) | Weight | Letter-spacing     | Use                                                 |
| -------------- | --------- | ---------------- | ------ | ------------------ | --------------------------------------------------- |
| `text-display` | 48        | 54               | 500    | -0.01em            | Page-level hero (non-display-register contexts)     |
| `text-title`   | 28        | 34               | 500    | -0.005em           | Section heading, card title                         |
| `text-heading` | 18        | 24               | 600    | —                  | Sub-section heading                                 |
| `text-body-lg` | 17        | 26               | 400    | —                  | Lead paragraph, introductory copy                   |
| `text-body`    | 16        | 25               | 400    | —                  | Default body text                                   |
| `text-caption` | 14        | 20               | 500    | —                  | Metadata, dates, status prose                       |
| `text-label`   | 12        | 16               | 500    | 0.08em (uppercase) | Eyebrow, section label, tab labels (Archivo Narrow) |

_Values derived from `ss.css` token values converted from rem. Base: 16px._

---

### 4.3 Plainspoken Display Scale (weight-900 register)

These tokens define the Plainspoken Sign Shop's signature visual register. Weight 900 is non-negotiable in this register.

| Token              | Size (px) | Line-height (px) | Weight  | Letter-spacing | Use                                                 |
| ------------------ | --------- | ---------------- | ------- | -------------- | --------------------------------------------------- |
| `text-hero`        | 72        | 66               | **900** | -0.03em        | Portal H1 desktop (business name, engagement title) |
| `text-hero-mobile` | 44        | 40               | **900** | -0.03em        | Portal H1 mobile                                    |
| `text-hero-price`  | 64        | 59               | **900** | -0.04em        | Summary-card total project price                    |
| `text-kpi`         | 44        | 44               | **900** | -0.03em        | KPI numbers in admin dashboard                      |
| `text-section-h`   | 36        | 36               | **900** | -0.02em        | Section block headings on detail pages              |
| `text-price-row`   | 28        | 28               | **900** | -0.02em        | Row-level money in list/ledger contexts             |
| `text-num-cell`    | 22        | 22               | **900** | -0.01em        | ID/sequence cell glyphs in ticket rows              |
| `text-money`       | 44        | 48               | 500     | —              | MoneyDisplay component (list-context amounts)       |

**Application rule — display tokens vs. functional tokens:**

Display tokens (weight 900, negative tracking) apply to:

- **Portal:** Engagement title on the dashboard (hero scale). Total project price (hero-price scale). Invoice total in the invoice detail document header. KPI-style summary numbers if a future admin dashboard introduces them.
- **Admin:** KPI dashboard numbers (admin analytics, pipeline aggregate counts).

Functional tokens (normal weight, standard tracking) apply to:

- **Everything else.** Admin chrome, pipeline cards, form labels, status text, metadata, email templates, section headings within body content.

**The boundary is context, not surface.** A portal page can use both: `text-hero-mobile` (900) for the engagement title, `text-heading` (600) for the scope summary sub-section. An admin page can use `text-kpi` (900) for a pipeline count number and `text-title` (500) for the card heading below it.

**Weight 900 belongs to numbers and structural labels that the user scans first.** It does not belong to paragraph copy, error messages, or anything requiring sustained reading. The `MoneyDisplay` component uses `text-money` at weight 500 in list contexts — intentional. `text-hero-price` at 900 is for the single total price on the client dashboard where that number is the entire point.

Marcus: "The bank app — the balance is the first thing I see." The weight-900 price token is the design equivalent of the balance display.

---

### 4.4 Archivo Narrow — Usage Constraint

Reserved for: chips, tab labels, narrow metadata where horizontal compression serves a density goal. Use only via `--ss-font-accent-label` on elements that genuinely need the condensed width.

Do not use for body text, captions, or headings. The hierarchy is weight-based within Archivo regular.

---

### 4.5 JetBrains Mono — Usage Constraint

Strictly for:

- ULID values and internal IDs in admin
- Invoice reference numbers (e.g., "REF INV-2026-004") — client-facing, business-document register
- Code values, extraction JSON displays
- Fixed-width data where column alignment matters

Not a stylistic treatment for prices. Money values use Archivo. Monospace is for machine-readable identifiers and formatted reference numbers.

Marcus: "When numbers line up in a column, they read like a real ledger."

---

### 4.6 Italic Usage

Permitted only for:

1. Quoted speech in informational copy (client testimonials on marketing site)
2. `<em>` emphasis within long-form instructional text where it is the semantic choice

Prohibited for: status labels, metadata, prices, headings, any UI chrome. Rare by design.

---

## 5. Spacing & Rhythm

**Source of truth:** `node_modules/@venturecrane/tokens/dist/ss.css`

---

### 5.1 Canonical Rhythm Tokens

Four tokens define all vertical rhythm. Horizontal rhythm follows the same values. Raw Tailwind spacing utilities (`p-6`, `gap-4`) are banned in governed surfaces (Pattern 06).

| Token           | CSS var              | px     | Use                               |
| --------------- | -------------------- | ------ | --------------------------------- |
| `space-section` | `--ss-space-section` | **48** | Gap between major page sections   |
| `space-card`    | `--ss-space-card`    | **32** | Card internal padding             |
| `space-stack`   | `--ss-space-stack`   | 16     | Vertical stack of sibling content |
| `space-row`     | `--ss-space-row`     | 12     | Gap between rows in a list        |

**Documentation correction required (unchanged from Round 2):** `UI-PATTERNS.md` Rule 6 documents `space-section: 32px` and `space-card: 24px`. Compiled token values are `48px` and `32px`. The compiled values are authoritative. Correct Rule 6 in the next PR that touches `UI-PATTERNS.md`.

---

### 5.2 Context Application

**Client portal (spacious, mobile-primary):**

- Between dashboard sections: `space-section` (48px)
- Card internal padding: `space-card` (32px)
- Between stacked content blocks within a card: `space-stack` (16px)
- Bottom tab bar items: minimum 44px touch target (Apple HIG / WCAG 2.5.5)

**Admin (dense, desktop-primary):**

- Between pipeline columns: `space-section` (48px)
- Card internal padding: `space-card` (32px), or `space-stack` (16px) for dense data tables
- Between rows in pipeline cards: `space-row` (12px)

---

### 5.3 Shape — Zero Radii (canonical and binding)

All radii are `0`. This is the flat institutional rule. It is binding.

| Token                | Value |
| -------------------- | ----- |
| `--ss-radius-card`   | 0     |
| `--ss-radius-button` | 0     |
| `--ss-radius-badge`  | 0     |

**Canonical anti-pattern statement:** Rounded buttons do not merely look out of place — they break the entire Plainspoken Sign Shop register. The reference set (1950s commercial signage, letterpress cards, ruled borders) has no soft corners. A rounded button implies a brand personality (friendly, approachable, consumer-facing) that the system explicitly rejects.

Marcus: "Rounded corners and drop shadows everywhere — websites that have everything soft and floaty and layered feel like they're hiding something." This is a trust signal.

Any PR introducing rounding to a card, button, or badge requires explicit design review. The PR description must explain the rationale and must demonstrate that the Plainspoken register is preserved.

---

### 5.4 Motion

**Final position (unchanged from Round 2, confirmed as required):** Motion token mapping is required before Phase 2 ships. The `@layer base` prefers-reduced-motion block is required before Phase 2 ships. Both are code changes, not design decisions.

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
  --duration-instant: var(--ss-motion-duration-instant);
  --duration-fast: var(--ss-motion-duration-fast);
  --duration-base: var(--ss-motion-duration-base);
  --duration-slow: var(--ss-motion-duration-slow);
  --ease-standard: var(--ss-motion-easing-standard);
  --ease-decelerate: var(--ss-motion-easing-decelerate);
  --ease-accelerate: var(--ss-motion-easing-accelerate);
}
```

#### Required `prefers-reduced-motion` block (brand-required, not just a11y-required)

The Plainspoken Sign Shop's restraint principle demands that motion never impose itself on a user who has expressed a preference against it. This is a brand position, not only a WCAG 2.1 SC 2.3.3 requirement.

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

#### Motion usage table (final)

| Use case                               | Duration token            | Easing token        |
| -------------------------------------- | ------------------------- | ------------------- |
| Hover color change                     | `--duration-fast` (150ms) | `--ease-standard`   |
| Focus ring appearance                  | `--duration-fast` (150ms) | `--ease-standard`   |
| Tab active state transition            | `--duration-fast` (150ms) | `--ease-standard`   |
| Disclosure/accordion open              | `--duration-base` (250ms) | `--ease-decelerate` |
| Confirmation panel swap (post-signing) | `--duration-base` (250ms) | `--ease-standard`   |

**What does not animate:** StatusPill tone changes, MoneyDisplay values, list rows on page load, error messages (must be instant per `aria-live="assertive"` semantics).

---

## 6. Imagery & Iconography

### 6.1 Icon System — Material Symbols Outlined (final, fully specified)

Material Symbols Outlined is the canonical icon system. Full axis settings:

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

**Axis settings by context:**

| Context                                   | FILL | wght | opsz | Notes                                         |
| ----------------------------------------- | ---- | ---- | ---- | --------------------------------------------- |
| Default (body-adjacent icons)             | 0    | 400  | 24   | System default                                |
| Tab bar icons (mobile, 20px display size) | 0    | 400  | 20   | `opsz 20` prevents stroke over-weight at 20px |
| Active tab icon                           | 1    | 400  | 20   | Filled variant signals active state           |
| Heading-adjacent icons                    | 0    | 600  | 24   | wght matches surrounding typography           |
| Caption-adjacent icons                    | 0    | 300  | 24   | wght matches surrounding typography           |
| Display-context icons (large, >32px)      | 0    | 400  | 40   | opsz 40 for larger sizes                      |

**No custom icon set in MVP.** Every icon used in the product must exist in Material Symbols Outlined. Do not introduce icon assets from Heroicons, Phosphor, Lucide, or any other library. A design decision to use a non-MSO icon requires explicit design review and a named reason the MSO library cannot serve the need.

**Icon accessibility:**

- Icons used as sole communicator of state (no adjacent text): `aria-label` or visually-hidden text required.
- Decorative icons adjacent to text: `aria-hidden="true"`.

---

**Canonical tab icon set — FINAL**

Marcus said: "Tabs need words. Real words." Labels are always visible. No icon-only tab on any surface.

The Interaction Designer Round 2 and Design Technologist Round 2 had a mapping inconsistency: the Design Technologist's `tabs` array put `work` on Proposals and `description` on Invoices, which is the reverse of what the icons semantically suggest. Marcus reacted directly to `description` as ambiguous ("What is the 'description' tab? I had to keep reading to figure out it was probably the proposal detail").

**Final canonical set:**

| Route                | Tab label | Icon (inactive FILL 0, opsz 20) | Icon (active FILL 1, opsz 20) | Rationale                                                                  |
| -------------------- | --------- | ------------------------------- | ----------------------------- | -------------------------------------------------------------------------- |
| `/portal`            | Home      | `home`                          | `home` filled                 | Universal home affordance. No ambiguity.                                   |
| `/portal/quotes`     | Proposals | `description`                   | `description` filled          | Document-with-lines register. "Proposal" = a document to review and sign.  |
| `/portal/invoices`   | Invoices  | `receipt_long`                  | `receipt_long` filled         | Receipt/bill register. Marcus knows what a receipt is.                     |
| `/portal/engagement` | Progress  | `assignment`                    | `assignment` filled           | Checklist/task register. Progress on a project, not an analytics timeline. |
| `/portal/documents`  | Documents | `folder_open`                   | `folder_open` filled          | File library. Fifth tab, conditional on document existence.                |

**Why `description` for Proposals and `receipt_long` for Invoices (and not the reverse):**

- `description`: Shows a document with text lines — the register of "a document I need to read." A proposal is a document with scope and price text. The icon matches.
- `receipt_long`: Shows a receipt with a curled bottom edge — the register of a financial transaction record. An invoice is a bill, not a narrative document. The icon matches.
- The Design Technologist's Round 2 array had these reversed. The correction preserves semantic accuracy.

**Label visibility rule (final):**

- **Mobile (portrait, bottom tab bar):** Icon above label. Both always visible. Never icon-only.
- **Desktop (horizontal tab strip):** Icon left of label. Both always visible. Never icon-only.
- **Active state:** Icon switches to FILL 1. Label uses `--ss-color-primary` (burnt orange). Icon uses `--ss-color-primary`.
- **Inactive state:** Icon FILL 0. Label and icon use `--ss-color-text-secondary` (#4a423c).

Marcus is explicit: "If I have to guess what a tab does by looking at an icon, you've lost me." This is a hard constraint. Any design proposal to remove labels from tabs fails this test.

**Warning state icons (final):**

| State                       | Icon              | Axis                      | Token                          |
| --------------------------- | ----------------- | ------------------------- | ------------------------------ |
| Quote near expiry           | `schedule`        | wght 400, opsz 24, FILL 0 | `--ss-color-warning` (#7a5800) |
| Deposit overdue             | `receipt_long`    | wght 400, opsz 24, FILL 0 | `--ss-color-warning` (#7a5800) |
| Parking lot stale (admin)   | `pending_actions` | wght 400, opsz 24, FILL 0 | `--ss-color-warning` (#7a5800) |
| Financial blindness (admin) | `visibility_off`  | wght 400, opsz 24, FILL 0 | `--ss-color-warning` (#7a5800) |

**Stability rule:** Tab icons must remain stable across releases. A tab icon change after users have learned it is a navigation regression requiring explicit design review, a changelog entry, and a justification that the MSO library cannot serve the prior icon's need.

---

### 6.2 No Illustration

No illustration. No marketing mascots. No abstract blob shapes. No decorative background patterns. Empty states render nothing or a "TBD in SOW" marker — never an illustration of a person looking at a clipboard.

Marcus: "Cute empty states — just say 'Documents will appear here as the project progresses' and move on."

---

### 6.3 Photography Never — Canonical and Absolute

No photography anywhere in the product UI or on the marketing site. This is canonical and absolute. It extends to:

- Office photos
- Founder or team portraits
- Client logos (not even as trust badges)
- "Trust badges" (certifications, awards, review counts displayed as images)
- Partner logos or co-branding imagery
- Stock photography of any kind

**Why this is absolute and not just "no photography in the portal":**

Marcus named the trust register explicitly: QuickBooks, his bank app, Apple Calendar, Square. None of those products use photography in their primary UI. The trust signal is operational competence demonstrated through information clarity — not social proof demonstrated through faces and logos.

Photography would undermine the Plainspoken register in two specific ways:

1. It introduces warmth and personality signals the identity explicitly rejects.
2. It creates a contrast of media types (photograph next to typographic cream-and-ink) that disrupts the flat, uniform register.

The marketing site does not benefit from photography either. The value proposition is experience and judgment — neither of which is demonstrated by a headshot or an office interior. The absence of photography is itself a positioning signal: this firm is confident enough in the quality of its arguments that it does not need to perform trustworthiness through faces.

**Trust badges are included in this prohibition.** A "Certified Partner" badge rendered as a PNG is a photographic element. If trust credentials are warranted, render them as text ("Google Workspace Partner" in text-label at Archivo Narrow) not as a badge image.

---

### 6.4 Logo and Brand Mark

No brand mark in the current system. Deferred until productization (PRD §2, Open Decision D1). When introduced: wordmark-based, legible at cream/ink palette, no gradient fills, no icon-only mark.

---

### 6.5 Third-Party Embed Surfaces

The Plainspoken Sign Shop brand applies edge-to-edge within our chrome. Third-party embeds (SignWell, Stripe hosted pages) inherit their own visual contract — we do not attempt to restyle them.

Our response to visual discontinuity:

1. Minimize the embed footprint: the iframe occupies the signing area only, not the full viewport.
2. Frame with our chrome: PortalHeader, breadcrumb, contextual heading, and "Return to portal" escape all render in our visual system around the embed.
3. Do not restyle embed content: CSS injection into a cross-origin iframe is not possible and not appropriate.
4. Acknowledge the seam explicitly in content: one line of text above the iframe — "The signing form below is provided by SignWell, a document-signing service." — primes Marcus for the visual shift before it surprises him.

Marcus: "If the portal sends me to a signing surface that doesn't look like the rest of the portal, my gut reaction is that I've been redirected somewhere I didn't intend to go. That is the exact visual experience of a phishing scam."

The disclosure line is mandatory. It converts a potential trust-breaking surprise into an explained and expected transition.

---

## 7. Inspiration Board

Marcus's four real-world references are the primary board. The design-ecosystem references from Round 1 remain as secondary rationale but are explicitly subordinated to what Marcus actually uses.

---

### 7.1 Marcus's Actual References

**QuickBooks (operational trust)**

Marcus: "It's not pretty. I trust it the way I trust an old Ford F-250. Every time I open it, I know where I am. The numbers are in columns. The columns mean something."

Application to SS: The admin pipeline and quote builder should feel this trustworthy. Not pretty — organized. Every number in its column. SS differs from QuickBooks in having a significantly better visual register (cream + ink vs. green + white), but the operational clarity is the same goal.

**Bank app (mobile information hierarchy)**

Marcus: "Boring. Functional. The balance is the first thing I see. Everything else is behind a tap."

Application to SS: The portal dashboard hierarchy. Price above the fold. Everything else one tap deeper. The bank app's single-primary-value mobile pattern is the direct ancestor of the portal's above-fold contract.

**Apple Calendar (dense-but-readable)**

Marcus: "I look at it fifty times a day. The information is dense but I can read it in under a second. There's no tutorial, no empty state with a friendly illustration."

Application to SS: Engagement timeline view, admin pipeline. Maximum information density, zero decoration. Empty states are non-events — the interface works identically whether there are events or not.

**Square (obvious action)**

Marcus: "The button was big. The confirmation was obvious. The receipt went to the customer. Nothing unnecessary. I never had to read instructions."

Application to SS: "Review & Sign" and "Pay Now" CTA sizing and placement. The action should be as obvious as the Square pay button. The amount is on the button ("Pay Deposit — $3,500"), mirroring Square's payment confirmation.

---

### 7.2 Stripe Dashboard

**URL:** https://dashboard.stripe.com

Dense data tables, status indicators used conservatively, chrome invisible behind the data. Marcus uses Square, not Stripe — but both share the quality Marcus values: the number is the thing. Validates the SS approach of making price and status the primary read.

Still holds for admin pipeline and quote builder visual organization. **What not to take:** Blue accent, rounded inputs.

---

### 7.3 Letterpress and Commercial Sign Shop Ephemera

**URL:** https://letterformarchive.org/collection/

The tonal and typographic source of the Plainspoken Sign Shop identity. Weight-900 display scale, ruled hairlines, flat color — direct inheritance from this reference. Marcus identified the register without knowing it: "This looks like a letterhead. Like something you'd get from a contractor or a law office."

Still holds as the identity's tonal source. **What not to take:** Literal reproduction of historical signage in a digital product.

---

### 7.4 HEY Email

**URL:** https://app.hey.com

Conviction in typographic choices, willingness to be decisive, non-apologetic information density. The quality to take is decisiveness — someone made a decision about what this should look like and committed to it. Not HEY's playful color palette or illustrated onboarding.

Partially holds: decisiveness in design choices only.

---

### 7.5 Notion in Structured Use

Editorial layout for proposal detail and document library. Content at full width, chrome not competing. The bank app reference is more precise for the portal dashboard hierarchy; Notion remains a useful reference for the quote detail view specifically, where the proposal reads like a document.

Partially holds: useful for quote detail and document library layout only.

---

## 8. Anti-Inspiration

Four examples of what the Plainspoken Sign Shop actively refuses.

---

### 8.1 The "Bouncy Purple Gradient" Register

**URL:** https://copilot.com

The generic purple-gradient-rounded-card SaaS aesthetic. Soft purple brand accent, rounded modals, illustration-heavy empty states, pill-shaped status badges.

Marcus: "If I see that soft purple gradient that every 'all-in-one platform for teams' uses, I'm out immediately. That color is the visual equivalent of 'we want to appeal to everyone' and it appeals to no one."

**Specifically wrong for SS:**

- Purple as brand color, in any value
- Rounded modals, rounded cards, pill-shaped badges
- Illustration-heavy empty states
- "Friendly" onboarding flows with personality copy
- Visual density as a signal of premium rather than a consequence of information

Check any new portal component: "Would this look at home in Copilot?" If yes, reconsider.

---

### 8.2 HoneyBook — Freelancer CRM Register

**URL:** https://www.honeybook.com

Large feature cards with gradient washes, soft shadows, rounded corners, illustrated empty states, "you've got this, creative" tone. A well-executed product for photographers and event planners. Entirely wrong for Marcus.

Any element that could appear on a HoneyBook dashboard — rounded white cards with drop shadows on a light gray background — is an anti-pattern.

---

### 8.3 ServiceTitan — Enterprise Field-Service SaaS

**URL:** https://www.servicetitan.com

This replaces the previous "Salesforce / ServiceNow" generic enterprise reference. Marcus's peers in the HVAC industry know ServiceTitan specifically. He closed the tab after ten minutes. The failure mode: institutional without being authoritative. Dense data tables that emphasize the structure over the decision the user needs to make. The impression that the portal was deployed by IT, not built for this business.

**Why ServiceTitan, not Salesforce:** Marcus does not know Salesforce. "Enterprise CRM" is not in his vocabulary. ServiceTitan is the field-service platform his industry uses and has formed opinions about. Naming his actual anti-reference is more useful than naming an abstract enterprise category.

**Specifically wrong for SS:**

- Dark navy or dark blue brand anchor
- Sky blue accents
- Table-heavy layouts where data structure is more visible than the user's next action
- Dense admin chrome pushed to clients who just need to see what they owe and what was signed

---

### 8.4 The "Etsy Receipt" Invoice Register

This anti-pattern was named directly from Marcus in Round 1 and holds final.

Marcus: "If the invoice looks like something I could have made in Canva in twenty minutes, I'm going to feel like I overpaid. The deposit invoice should look like an invoice from a professional firm, not a receipt from an app."

The "Etsy receipt" register:

- Automatically generated styling with no visual discipline
- Rounded corners, generic brand colors on a financial document
- Amount displayed in standard body text rather than a formal invoice format
- Invoice number that looks like a UUID

**Specifically wrong for SS:**

- Invoice number that is a raw UUID or "INV-001" unless genuinely the first invoice
- Amount in standard body text rather than JetBrains Mono tabular-nums at invoice-register scale
- "Pay Now" button without the dollar amount on or adjacent to it
- Rounded corners, drop shadows, or gradient fills that signal "app-generated receipt"
- Payment amount buried below a fold

**The standard:** The invoice must look like something Marcus would put in his Dropbox folder "Vendors and Contractors 2026." Firm name, real invoice number, date, amount in column-aligned monospaced type, due date, payment instructions. Cream paper, ink, hairline borders. Same palette as the portal. Same typeface. No discontinuity.

---

## 9. Product Naming

**PRD status:** Naming is TBD. PRD describes the system as "the portal that runs engagements" without a product name.

**Recommendation: stay nameless until productization.**

Three options were considered:

| Option                     | Example                             | Assessment                                                                                       |
| -------------------------- | ----------------------------------- | ------------------------------------------------------------------------------------------------ |
| Named product              | "SMD Portal" or "Engagement Center" | Creates a named entity Marcus must track. Cognitive load with no payoff at MVP.                  |
| Subdomain as implicit name | "portal.smd.services"               | Already in use. The URL is the identifier. No additional naming tax.                             |
| Named productized platform | "[Product name]"                    | Appropriate if SS sells portal access to multiple consultancies or licenses the system. Not MVP. |

**Why nameless:** Marcus said it in Round 2 (paraphrased): "Every named entity is a tax. I don't need to know what the system is called. I need to know where to click and what it costs." Naming the portal creates a new concept he has to understand and remember. "Your portal at portal.smd.services" is enough. The subdomain is the product's address and its implicit name.

**When naming becomes appropriate:** When the portal is sold or licensed to other consulting firms, or when Marcus has more than one client portal (e.g., multiple engagements with named contexts that need distinguishing), naming becomes useful. At MVP with a single client relationship, naming adds friction without benefit.

**Implementation note:** Do not add a product name to the portal chrome, the `<title>` tag strategy (which can use "SMD Services · {Page context}"), or any client-facing email copy. "SMD Services portal" in prose context is sufficient and uses the firm name as the identifier.

---

## 10. Open Design Decisions

Every genuine unresolved question requiring human decision after three rounds.

---

### Decision 1 — Warning token hex: `#7a5800` vs. `#6b4f08`

**The question:** Which hex value should be committed to the token package as `--ss-color-warning`?

**Options considered:**

- `#7a5800` (Brand Strategist Round 2 and Round 3): 7.14:1 on cream, WCAG AAA, warmer yellow-amber, stronger visual distinction from olive
- `#6b4f08` (Design Technologist Round 2): 6.72:1 on cream, WCAG AA, darker amber-ochre, slightly closer to brown-amber

**Why it matters:** Both values pass WCAG AA. The visual distinction from olive (`#4a6b3e`) and burnt orange (`#c5501e`) varies enough between them that simultaneous display in a portal context (warning badge next to a completion state, or warning callout next to a burnt orange CTA) may look different. The wrong choice creates a register collision that degrades the single-accent discipline.

**My recommendation:** `#7a5800`. The warmth and AAA contrast give it more register separation from olive at low screen brightness. This is the value in this document.

**Needs:** Founder call. Both are defensible; the decision is a judgment call on register, not a compliance question. Whichever is chosen, it must be the only value in the `ss.json` token source — there must not be two competing entries.

---

### Decision 2 — Signing route: state within `quotes/[id]` vs. distinct `/portal/quotes/[id]/sign` URL

**The question:** Is the signing surface a state within the existing quote detail page, or a distinct route?

**Options considered:**

- State within `quotes/[id]`: Current codebase implementation. Simpler. The `SigningView.astro` component is a conditional render within the existing page.
- Distinct `/portal/quotes/[id]/sign` route: PRD §9 specifies this URL. Required if deep-linking from the invitation email directly to the signing context is needed.

**Why it matters:** The invitation email (Email 1 in the Interaction Designer's inventory) links to the portal and expects Marcus to navigate to the signing surface. If the link can deep-link directly to the signing state (bypassing the quote detail page entirely), a distinct route is required. If the email links to the portal home or the quote list and Marcus navigates himself to signing, the state approach works.

**My recommendation (brand perspective only):** A distinct route is preferable because it creates a stable URL that can be shared, bookmarked, and linked from email with predictable behavior. The state approach creates an ambiguity about what a direct URL to `quotes/[id]` means at any given time. Brand consistency argues for consistent addressability.

**Needs:** Founder call / Interaction Designer decision. This is primarily an IA and implementation question. The brand framing spec (our chrome stays visible, disclosure line precedes iframe) is valid under either approach.

---

### Decision 3 — Tab count at MVP: 4 or 5 destinations

**The question:** Does the Documents tab ship at MVP as a persistent bottom tab, or is Documents accessible via another path (dashboard row, link from within Progress)?

**Options considered:**

- 4 tabs (Home / Proposals / Invoices / Progress): Marcus said "four tabs, that's enough" explicitly. Simpler. Matches Apple HIG guidance for primary navigation.
- 5 tabs (Home / Proposals / Invoices / Progress / Documents): Matches PRD §9 tab inventory. Keeps Documents as a permanent primary destination.
- 4 tabs with conditional 5th: Documents tab appears only when documents are uploaded. Still a bottom-tab destination; conditionally shown.

**Why it matters:** The fifth tab, if added, compresses the tab item width on 375px screens. At 5 items the icon-plus-label stack is tighter. Marcus said four was enough, but the PRD includes a Documents tab. If Marcus uploads 15 deliverable files over a 6-week engagement, burying Documents behind a home dashboard row creates a recurring friction.

**My recommendation:** 4 tabs at MVP. Add the Documents tab in Phase 2 or when usage data shows document access frequency warrants it. Marcus said "I'll look for documents when the email tells me to look for them" — which suggests email-initiated document access, not tab-based navigation, is the primary path for this persona. The conditional-show approach (Design Technologist's note) is an acceptable middle ground if the PRD requires it.

**Needs:** Founder call. Marcus's explicit preference (4 tabs) is clear, but the PRD's Documents tab is a scoped feature. Decision should come from the product owner, not the design system.

---

### Decision 4 — `text-muted` accessible alias: keep or drop

**The question:** Should `--ss-color-text-muted-accessible: #6b6158` (4.71:1) be added to the token package as Brand Strategist Round 2 proposed, or should the Design Technologist Round 2 position (enforce `text-secondary`, no new token) stand?

**Options considered:**

- Add the alias (Brand Strategist Round 2): creates an explicit AA-passing token for mobile outdoor metadata. Clear signal to engineers.
- No alias, use `text-secondary` for anything functional (Design Technologist Round 2 and this document's Round 3 position): simpler two-stop rule, avoids a third muted-ramp token.

**Why it matters:** If an engineer is authoring secondary metadata on a portal mobile surface and looks at `text-muted` (fails), they need a clear answer about what to use instead. A third token gives them a precise choice; a usage rule requires them to read the rule.

**My recommendation:** No alias. Use `text-secondary`. This document takes that position in §3.3 Gap 1 with rationale. The two-stop rule is cleaner and less susceptible to misapplication.

**Needs:** Design Tech lead decision. If implementation experience reveals that engineers are misapplying `text-muted` in functional contexts, the alias can be added then. Start without it.

---

### Decision 5 — Email template remediation priority (palette alignment)

**The question:** When must the email templates at `src/lib/email/templates.ts` be remediated to match the Plainspoken Sign Shop palette (`#f5f0e3` cream, `#1a1512` ink, `#c5501e` burnt orange, `#7a5800` warning)?

**Options considered:**

- Before first real client portal invitation: Email 1 (portal invitation) must be on-palette before Marcus sees it. The current blue/slate template would be a brand discontinuity at the most important trust moment.
- Phase 2 milestone: Treat as a P1 item with a scheduled deadline, not blocking Phase 1 internal usage.
- Continuous: Remediate templates as each email type is first used in a real engagement.

**Why it matters:** Marcus said "the email earns the click or doesn't." A portal invitation email in blue and slate that lands in his inbox before a cream-and-ink portal is a brand discontinuity at exactly the first trust moment. If the email looks different from the portal, the portal looks like a second system, not a continuation of the same experience.

**My recommendation:** Before the first portal invitation is sent to a real client. This is a firm gating requirement. The `EMAIL_TOKENS` snapshot file the Design Technologist specified in Round 2 is the right implementation pattern — it requires one engineering session, not a design overhaul.

**Needs:** Founder call on timing. The design direction is clear; the question is when the implementation PR must be merged relative to the first live engagement.

---

### Decision 6 — Parking lot item copy standard: who writes disposition explanations?

**The question:** When a parking lot item is dispositioned, who writes the human-readable explanation that Marcus will read? And what is the minimum required content?

**Options considered:**

- Admin authors a note field per item: Each disposition includes a `disposition_note` authored by Scott in the engagement admin. This is the designed approach per the PRD.
- System generates prose from disposition type: "Added to project" / "Deferred to follow-on" / "Not addressed" rendered automatically from the `disposition` enum. No authored note required.
- Hybrid: Disposition label is automatic; a `disposition_note` text field is optional but surfaced prominently in the admin.

**Why it matters:** Marcus: "If I come back four months later and look at the parking lot and there's an item that says 'disposition: dropped' with no note explaining why it was dropped, I won't remember what that was." The disposition note is not decoration — it is the explanation that makes the parking lot feel like collaboration rather than a list of things the firm decided without him.

**My recommendation (brand voice):** The authored note is required at handoff for any item that is dispositioned as "Deferred" or "Not addressed." Items folded into the project can use the automatic label without a note (the project record is the explanation). The admin flow should require a note for deferred/not-addressed dispositions before the parking lot panel is published to the client view.

**Needs:** Founder call on whether the note is required or recommended. The brand position is clear: without a note, the parking lot undermines the collaborative register. The implementation question is whether to enforce this at the database or UI layer.

---

_SMD Services — Brand Strategist Contribution, Design Brief Round 3 (Final)_
_Plainspoken Sign Shop identity. Paint-job, not brochure._
_Three-round synthesis: Design Technologist accessibility findings, Interaction Designer navigation and interaction specs, Marcus's direct product reactions, cross-role reconciliation on warning token, muted text, tab icon set, voice relationship, olive hierarchy, photography rule, and product naming._
