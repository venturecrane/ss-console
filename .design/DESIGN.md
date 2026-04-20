# SMD Services — Design Identity

**Identity direction:** Swiss Functional — modernist craft. Disciplined grid, neutral grotesque + tabular mono, restrained palette, single deep accent. Authority through design rigor, not decoration.

**Source:** Extracted from `frontend-design` plugin output on 2026-04-19 (Swiss Functional exploration). The prior Architect's Studio direction was replaced. Regenerate when identity is revised.

- `portal-home.html` (dashboard, invoice-pending state)
- `portal-invoices-detail.html` (invoice detail, unpaid state)

Both live in `.design/frontend-design-output/swiss-functional/` and are the canonical pixel source for every token below.

---

## Color

Semantic roles. Downstream components reference tokens by role, not by hex.

| Role             | Hex       | Usage                                                             |
| ---------------- | --------- | ----------------------------------------------------------------- |
| `background`     | `#F8F8F6` | Pure paper — slight warmth, reads neutral                         |
| `surface`        | `#FFFFFF` | Card and panel backgrounds                                        |
| `border`         | `#D1D1CE` | Hairline card edges                                               |
| `border-subtle`  | `#E5E4E1` | Interior rules, row dividers                                      |
| `text-primary`   | `#0A0A0A` | True-black ink; grotesque needs the full contrast                 |
| `text-secondary` | `#4A4A47` | Captions, meta values, reference text                             |
| `text-muted`     | `#8E8E8A` | Meta labels, placeholders, timestamps                             |
| `meta`           | `#4A4A47` | Eyebrow labels, non-primary accent text (alias of text-secondary) |
| `primary`        | `#1E4F5C` | Primary buttons, focus rings, single accent (petrol blue)         |
| `primary-hover`  | `#163E48` | Primary button hover state                                        |
| `action`         | `#1E4F5C` | Focus ring outline color                                          |
| `attention`      | `#1E4F5C` | Status indicators needing action — same as primary by design      |
| `complete`       | `#2C6E3F` | Completed / paid status — deep forest green                       |
| `error`          | `#8B1A1A` | Destructive states, validation failures                           |

**Single-accent discipline.** Swiss modernism uses one primary color, not two or three. `primary` / `action` / `attention` all resolve to the same petrol blue. `complete` is the deliberate complement (deep forest), reserved for success states. `error` reads as a distinct red, one step warmer than primary.

**Contrast ratios (WCAG 2.2 AA, 4.5:1 floor for body text):**

| Foreground         | Background    | Ratio    | Pass       |
| ------------------ | ------------- | -------- | ---------- |
| `text-primary`     | `background`  | 19.2 : 1 | AAA        |
| `text-primary`     | `surface`     | 21.0 : 1 | AAA        |
| `text-secondary`   | `background`  | 9.3 : 1  | AAA        |
| `text-secondary`   | `surface`     | 10.2 : 1 | AAA        |
| `text-muted`       | `background`  | 4.1 : 1  | large only |
| `text-muted`       | `surface`     | 4.4 : 1  | large only |
| `primary` (white)  | `primary` bg  | 9.4 : 1  | AAA        |
| `primary`          | `background`  | 8.4 : 1  | AAA        |
| `complete` (white) | `complete` bg | 6.7 : 1  | AA         |
| `error` (white)    | `error` bg    | 8.8 : 1  | AAA        |

`text-muted` passes only at large sizes (≥18px or ≥14px bold); reserved for metadata labels and timestamps. Never used for body prose.

---

## Typography

| Role      | Family        | Weight | Size / Line-height   | Letter-spacing | Notes                                 |
| --------- | ------------- | ------ | -------------------- | -------------- | ------------------------------------- |
| `display` | Switzer       | 700    | 2.25rem / 2.5rem     | -0.015em       | Hero headlines                        |
| `title`   | Switzer       | 600    | 1.375rem / 1.75rem   | -0.005em       | Section titles                        |
| `heading` | Switzer       | 600    | 1rem / 1.375rem      | —              | Subsection, card titles               |
| `body-lg` | Switzer       | 400    | 1.0625rem / 1.625rem | —              | Long-form prose                       |
| `body`    | Switzer       | 400    | 0.9375rem / 1.5rem   | —              | Default body                          |
| `caption` | Switzer       | 500    | 0.8125rem / 1.125rem | —              | Metadata values                       |
| `label`   | IBM Plex Mono | 500    | 0.75rem / 1rem       | 0.05em         | Uppercase eyebrow labels, meta rows   |
| `money`   | IBM Plex Mono | 500    | 2rem / 2.25rem       | -0.01em        | Dollar figures; always `tabular-nums` |

**Font rationale.** **Switzer** (Fontshare, free) is a contemporary grotesque tuned for interface use — Helvetica-adjacent but sharper and better at display sizes. Its name and family of weights make it read as deliberately Swiss. **IBM Plex Mono** (Google OFL) is precise, technical, and pairs cleanly against Switzer without dominating. The two-family stack is pure discipline; no third face.

**Font loading:**

```html
<link
  href="https://api.fontshare.com/v2/css?f[]=switzer@400,500,600,700,800&display=swap"
  rel="stylesheet"
/>
<link
  href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&display=swap"
  rel="stylesheet"
/>
```

- **Switzer** — Fontshare (free for personal and commercial use)
- **IBM Plex Mono** — Google Fonts (SIL Open Font License 1.1)

**Figure conventions:** Money amounts, ISO dates, numerical columns all use `font-variant-numeric: tabular-nums`. Swiss tightens letter-spacing slightly for display sizes (tracking -0.015em on display, -0.005em on title). Body text stays at default tracking.

---

## Spacing

Swiss prefers generous vertical rhythm between sections and tight internal padding. More air between things than within things.

| Token     | Value            | Usage                                    |
| --------- | ---------------- | ---------------------------------------- |
| `section` | `3rem` (48px)    | Gap between major page sections          |
| `card`    | `1.5rem` (24px)  | Card and panel internal padding          |
| `stack`   | `1rem` (16px)    | Default vertical rhythm between siblings |
| `row`     | `0.75rem` (12px) | Gap between list rows                    |

Tailwind v4 generates `p-section`, `gap-section`, `space-y-stack`, etc. from these.

---

## Shape

Swiss modernism is 0 radius. Absolute discipline.

| Token           | Value | Usage                                  |
| --------------- | ----- | -------------------------------------- |
| `radius-card`   | `0`   | Card corners                           |
| `radius-button` | `0`   | Button corners                         |
| `radius-badge`  | `0`   | Status tags (rectangular, no rounding) |

Pure 0 — no chamfer, no softening. Müller-Brockmann, Vignelli, the Swiss school. The restraint is the point.

---

## Motion

Minimal and purposeful.

| Token            | Value        | Usage                                          |
| ---------------- | ------------ | ---------------------------------------------- |
| `motion-default` | `120ms ease` | Hover color transitions, focus-ring appearance |

- **No page transitions.** Links and routing are instant.
- **No scroll-triggered animations.** No parallax, no scroll-bound reveals.
- **No loading skeletons that pulse.** Empty states render static type.
- **Reduced motion:** respected by default. Any future animation must honor `prefers-reduced-motion: reduce`.

---

## Shadows / depth

**None.** The identity is flat. Hairline borders and typographic hierarchy do all the structural work. Shadows violate Swiss design discipline — the grid carries the hierarchy, not elevation.

---

## Anti-patterns (identity-level)

Design moves that are incompatible with Swiss Functional and must be rejected if proposed:

- Rounded corners of any kind
- Elevation / shadows / glow effects
- Gradient washes, tinted opacity overlays on brand elements
- Pill-shaped status badges
- Decorative icons or illustration
- Multiple accent colors (single accent is the discipline)
- Serif type anywhere (Swiss is strictly sans + mono)
- Loose letter-spacing on body (kerning stays close to 0 except on display)
- Hand-drawn or script accents
- Stock photography, emoji, marketing mascots
- "Welcome back, [Name]!" greetings, softening microcopy
- Jira-speak milestone names
- Caps-lock shouting in body prose (uppercase reserved for labels + mono)

---

## Voice notes

The visual identity is terse, precise, and confident. Copy matches:

- **Evidence over reassurance.** "Scott sat with your dispatcher Tuesday" beats "Things are going well."
- **Past-tense events.** Timeline entries are things that happened.
- **Concrete and specific.** Names, numbers, dates. Never vague status words.
- **No em dashes.** Use commas, periods, colons.
- **No AI-style parallel structures.** Vary sentence shape.

Reference register: a studio principal or senior operator reporting between visits. Calm, direct, numerate, Swiss.

---

## Mapping to Tailwind v4 `@theme`

The tokens above are emitted in paste-ready form at `.design/theme.css`. Consumers reference them via Tailwind v4 utilities:

- Colors: `bg-primary`, `text-text-primary`, `border-border`, `bg-[color:var(--color-primary)]`
- Typography: `text-display`, `text-title`, `text-heading`, `text-body`, `text-caption`, `text-label`, `text-money`
- Spacing: `p-card`, `gap-section`, `space-y-stack`
- Shape: `rounded-card`, `rounded-button`, `rounded-badge` (all resolve to 0)
- Font family: `font-display` (Switzer), `font-body` (Switzer), `font-mono` (IBM Plex Mono)

Shipped components reference tokens semantically — the cutover propagates the identity across portal, admin, and marketing surfaces without requiring regeneration.
