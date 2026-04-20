# SMD Services — Design Identity

**Identity direction:** Modern Institutional — classical serif display paired with a humanist sans, hairline structure, restrained palette, single deep-navy accent. Reads like a well-designed university publication, a think tank, or a quiet foundation. Thoughtful, credentialed, measured.

**Source:** Re-explored on 2026-04-19 after two prior directions (Architect's Studio ochre, Swiss Functional petrol) were revised. Reference mockup at `.design/frontend-design-output/exploration-v2/b-modern-institutional.html`.

---

## Color

Semantic roles. Downstream components reference tokens by role, not by hex.

| Role              | Hex       | Usage                                                     |
| ----------------- | --------- | --------------------------------------------------------- |
| `background`      | `#F9F7F1` | Warm paper, slight cream                                  |
| `surface`         | `#FFFFFF` | Card and panel backgrounds                                |
| `surface-inverse` | `#2A2520` | Warm umber — dark-section backgrounds (Pricing, FinalCta) |
| `border`          | `#D8D4C8` | Hairline card edges                                       |
| `border-subtle`   | `#ECE8DD` | Interior rules, row dividers                              |
| `text-primary`    | `#1A1A1A` | Graphite ink; slightly softer than true black for serif   |
| `text-secondary`  | `#52514C` | Captions, meta values                                     |
| `text-muted`      | `#8E8C85` | Labels, placeholders, timestamps                          |
| `meta`            | `#52514C` | Eyebrow labels, non-primary accent text                   |
| `primary`         | `#2C5282` | Primary buttons, focus rings, single accent (deep navy)   |
| `primary-hover`   | `#1F3A62` | Primary button hover state                                |
| `action`          | `#2C5282` | Focus ring outline color                                  |
| `attention`       | `#2C5282` | Status indicators needing action — same as primary        |
| `complete`        | `#2F6E42` | Completed / paid status — deep forest green               |
| `error`           | `#A02A2A` | Destructive states, validation failures — warm red        |

**Single-accent discipline.** `primary` / `action` / `attention` all resolve to the same navy. `complete` is the deliberate complement (forest), reserved for success states. `error` reads as a distinct warm red.

---

## Typography

| Role      | Family        | Weight | Size / Line-height   | Letter-spacing | Notes                                                      |
| --------- | ------------- | ------ | -------------------- | -------------- | ---------------------------------------------------------- |
| `display` | Crimson Pro   | 500    | 3rem / 3.375rem      | -0.01em        | Hero headlines — classical serif                           |
| `title`   | Crimson Pro   | 500    | 1.75rem / 2.125rem   | -0.005em       | Section titles                                             |
| `heading` | Crimson Pro   | 600    | 1.125rem / 1.5rem    | —              | Subsection, card titles                                    |
| `body-lg` | Public Sans   | 400    | 1.0625rem / 1.625rem | —              | Long-form prose                                            |
| `body`    | Public Sans   | 400    | 1rem / 1.55rem       | —              | Default body                                               |
| `caption` | Public Sans   | 500    | 0.875rem / 1.25rem   | —              | Metadata values                                            |
| `label`   | IBM Plex Mono | 500    | 0.75rem / 1rem       | 0.08em         | Uppercase eyebrow labels, reference strips                 |
| `money`   | Crimson Pro   | 500    | 2.75rem / 3rem       | -0.01em        | Dollar figures at display size; `tabular-nums lining-nums` |

**Font rationale.** **Crimson Pro** (Google, free) is a classical serif with a full weight range and italic, tuned for screen rendering. Reads editorial and credentialed without being stuffy. **Public Sans** (Google, free, USWDS) is a neutral humanist sans — civic-coded, highly readable, pairs cleanly with serif. **IBM Plex Mono** (Google OFL) kept for tabular data and reference codes only.

**Font loading:**

```html
<link
  href="https://fonts.googleapis.com/css2?family=Crimson+Pro:ital,wght@0,400;0,500;0,600;0,700;1,400&family=Public+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap"
  rel="stylesheet"
/>
```

All three families on Google Fonts. No licensing to purchase.

---

## Spacing

| Token     | Value            | Usage                                    |
| --------- | ---------------- | ---------------------------------------- |
| `section` | `3rem` (48px)    | Gap between major page sections          |
| `card`    | `2rem` (32px)    | Card and panel internal padding          |
| `stack`   | `1rem` (16px)    | Default vertical rhythm between siblings |
| `row`     | `0.75rem` (12px) | Gap between list rows                    |

---

## Shape

| Token           | Value | Usage          |
| --------------- | ----- | -------------- |
| `radius-card`   | `0`   | Card corners   |
| `radius-button` | `0`   | Button corners |
| `radius-badge`  | `0`   | Status tags    |

Flat hairlines, not rounded containers. Matches classical publication layout.

---

## Motion

`motion-default: 120ms ease` for hover color transitions. No page transitions, no scroll-triggered animations. `prefers-reduced-motion` respected.

---

## Shadows / depth

**None.** Typography and hairlines carry the page.

---

## Anti-patterns (identity-level)

- Rounded corners on cards, buttons, or status tags
- Shadows, gradient washes, glow effects
- Multiple accent colors (single navy is the discipline)
- Pill-shaped status badges
- Condensed sans for prose
- Loose body tracking
- Stock photography, emoji, marketing mascots
- Caps-lock shouting in body prose (uppercase reserved for labels + mono)

---

## Voice notes

- **Evidence over reassurance.**
- **Past-tense events** in timelines.
- **Concrete and specific** — names, numbers, dates.
- **No em dashes.** Commas, periods, colons.
- **No AI-style parallel structures.**

Reference register: a well-edited publication — a senior quarterly report, a university journal, a credentialed advisory firm.

---

## Mapping to Tailwind v4 `@theme`

Tokens emitted at `.design/theme.css`. Consumers reference via Tailwind v4 utilities:

- Colors: `bg-primary`, `text-text-primary`, `bg-[color:var(--color-primary)]`
- Typography: `text-display`, `text-title`, `text-heading`, `text-body`, `text-caption`, `text-label`, `text-money`
- Spacing: `p-card`, `gap-section`, `space-y-stack`
- Shape: `rounded-card`, `rounded-button`, `rounded-badge` (all 0)
- Font family: `font-display` (Crimson Pro), `font-body` (Public Sans), `font-mono` (IBM Plex Mono)

Shipped components reference tokens semantically — cutover propagates across portal, admin, and marketing without regeneration.
