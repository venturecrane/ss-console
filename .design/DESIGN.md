# SMD Services — Design Identity

**Identity direction:** Architect's Studio — the portal reads like the project file from a small design studio. Monospace for data, geometric sans for prose, hairline structure, a reference number on everything. Authority through precision, not decoration.

**Source:** Extracted from `frontend-design` plugin output on 2026-04-19. Regenerate when identity is revised.

- `portal-home.html` (dashboard, invoice-pending state)
- `portal-invoices-detail.html` (invoice detail, unpaid state)

Both live in `.design/frontend-design-output/` and are the canonical pixel source for every token below.

---

## Color

Semantic roles. Downstream components reference tokens by role, not by hex.

| Role             | Hex       | Usage                                                                                        |
| ---------------- | --------- | -------------------------------------------------------------------------------------------- |
| `background`     | `#FAFAF9` | Warm near-white page background ("paper")                                                    |
| `surface`        | `#FFFFFF` | Card and panel backgrounds (subtly lifts off background)                                     |
| `border`         | `#E5E5E4` | Hairline card edges                                                                          |
| `border-subtle`  | `#F0EFED` | Interior rules inside cards, row dividers                                                    |
| `text-primary`   | `#0A0A0A` | Body copy, headlines, authoritative text                                                     |
| `text-secondary` | `#52525B` | Captions, meta values, reference-line text                                                   |
| `text-muted`     | `#A1A1AA` | Meta labels, placeholders, timestamps                                                        |
| `meta`           | `#52525B` | Eyebrow labels, non-primary accent text (same as text-secondary, named for semantic clarity) |
| `primary`        | `#B45309` | Primary buttons, focus rings, attention accent (Amber 700)                                   |
| `primary-hover`  | `#92400E` | Primary button hover state (Amber 800)                                                       |
| `action`         | `#B45309` | Focus ring outline color                                                                     |
| `attention`      | `#B45309` | Status indicators needing action — same as primary by design                                 |
| `complete`       | `#15803D` | Completed / paid status                                                                      |
| `error`          | `#991B1B` | Destructive states, validation failures                                                      |

**Contrast ratios (WCAG 2.2 AA, 4.5:1 floor for body text):**

| Foreground        | Background          | Ratio    | Pass       |
| ----------------- | ------------------- | -------- | ---------- |
| `text-primary`    | `background`        | 19.4 : 1 | AAA        |
| `text-primary`    | `surface`           | 20.6 : 1 | AAA        |
| `text-secondary`  | `background`        | 8.1 : 1  | AAA        |
| `text-secondary`  | `surface`           | 8.6 : 1  | AAA        |
| `text-muted`      | `background`        | 3.1 : 1  | large only |
| `text-muted`      | `surface`           | 3.3 : 1  | large only |
| `primary` (white) | `primary` on button | 5.6 : 1  | AA         |
| `primary`         | `background`        | 5.3 : 1  | AA         |

`text-muted` passes only at large sizes (≥18px or ≥14px bold) and is reserved for metadata labels and timestamps. Never use it for body prose.

---

## Typography

| Role      | Family          | Weight | Size / Line-height   | Letter-spacing | Notes                                      |
| --------- | --------------- | ------ | -------------------- | -------------- | ------------------------------------------ |
| `display` | Cabinet Grotesk | 700    | 2.5rem / 2.75rem     | -0.02em        | Hero headlines, dominant statements        |
| `title`   | Cabinet Grotesk | 600    | 1.5rem / 2rem        | -0.01em        | Section titles                             |
| `heading` | Cabinet Grotesk | 600    | 1.125rem / 1.5rem    | —              | Subsection, card titles                    |
| `body-lg` | Satoshi         | 400    | 1.125rem / 1.75rem   | —              | Long-form prose                            |
| `body`    | Satoshi         | 400    | 1rem / 1.5rem        | —              | Default body                               |
| `caption` | Satoshi         | 500    | 0.8125rem / 1.125rem | 0.01em         | Metadata values                            |
| `label`   | JetBrains Mono  | 600    | 0.75rem / 1rem       | 0.08em         | Uppercase eyebrow labels, reference strips |
| `money`   | JetBrains Mono  | 500    | 2rem / 2.5rem        | —              | Dollar figures, `tabular-nums`             |

**Font rationale.** Cabinet Grotesk is a wide, confident geometric sans that gives headlines weight without feeling modern-SaaS. Satoshi is a neutral body sans that stays out of the way. JetBrains Mono anchors every piece of data (dates, amounts, reference codes) with a typewriter-precision feel — the key signal of the "project file" aesthetic.

**Font loading:**

```html
<link
  href="https://api.fontshare.com/v2/css?f[]=cabinet-grotesk@500,600,700,800&f[]=satoshi@400,500,600,700&display=swap"
  rel="stylesheet"
/>
<link
  href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&display=swap"
  rel="stylesheet"
/>
```

- **Cabinet Grotesk** and **Satoshi** — Fontshare (free for personal and commercial use)
- **JetBrains Mono** — Google Fonts (SIL Open Font License 1.1)

All three are free to ship. No license purchase required.

**Figure conventions:** All money amounts, dates in ISO format, and numerical data use `font-variant-numeric: tabular-nums` for column alignment. Prose dates may use lining figures without `tnum`.

---

## Spacing

Generous rhythm, hairline-separated sections. No dense SaaS-style compression.

| Token     | Value            | Usage                                    |
| --------- | ---------------- | ---------------------------------------- |
| `section` | `2.5rem` (40px)  | Gap between major page sections          |
| `card`    | `1.75rem` (28px) | Card and panel internal padding          |
| `stack`   | `1rem` (16px)    | Default vertical rhythm between siblings |
| `row`     | `0.75rem` (12px) | Gap between list rows                    |

Tailwind v4 generates `p-section`, `gap-section`, `space-y-stack`, etc. from these.

---

## Shape

Sharp by intent. The portal is a document, not an app.

| Token           | Value | Usage                                |
| --------------- | ----- | ------------------------------------ |
| `radius-card`   | `2px` | Card corners                         |
| `radius-button` | `2px` | Button corners                       |
| `radius-badge`  | `2px` | Status tags (rectangular, not pills) |

`2px` over `0` is deliberate — pure sharp corners render too brutalist on screen. A barely-there chamfer reads as "technical but not hostile."

**Notable: no pills.** Status indicators are rectangular mono-cap tags, not filled pills. This is an intentional break from generic SaaS UI.

---

## Motion

Minimal and purposeful.

| Token            | Value        | Usage                                          |
| ---------------- | ------------ | ---------------------------------------------- |
| `motion-default` | `120ms ease` | Hover color transitions, focus-ring appearance |

- **No page transitions.** Links and routing are instant.
- **No scroll-triggered animations.** No parallax, no scroll-bound reveals, no fade-in-on-view.
- **No loading skeletons that pulse.** Empty states render static type ("No invoices on file yet.").
- **Reduced motion:** respected by default. Any future animation must honor `prefers-reduced-motion: reduce`.

**Rejection clause:** The identity is print-inspired. Motion is for feedback, never for decoration.

---

## Shadows / depth

**None.** The identity is flat. Hairline borders and typographic hierarchy do all the structural work. If a future surface needs depth, consider revising the identity direction rather than adding shadows piecemeal.

---

## Anti-patterns (identity-level)

Design moves that are incompatible with Architect's Studio and must be rejected if proposed:

- Purple-gradient SaaS chrome
- Pill-shaped status badges
- Card-with-shadow-and-rounded-corners
- Illustrated empty states or marketing mascots
- KPI cards with icon + number + trend arrow
- Tabbed dashboards, sidebar navigation
- Stock imagery of business owners, laptops, handshakes
- "Welcome back, [Name]!" greetings
- Progress bars (stepped, segmented, percentage, radial — all of them)
- Trust badges, testimonial carousels, marketing social proof
- Initials-in-a-circle avatars (use named photo placeholder instead)
- Softening microcopy: "Don't worry," "We've got you covered," "Oops!"
- Jira-speak milestone names ("Process documented for new client intake")

---

## Voice notes

The visual identity is terse, honest, dated. Copy must match:

- **Evidence over reassurance.** "Scott sat with your dispatcher Tuesday" beats "Things are going well."
- **Past-tense events.** Timeline entries are things that happened, not things that are "in progress."
- **Concrete and specific.** Names, numbers, dates. Never vague status words.
- **No em dashes.** Use commas, periods, colons.
- **No AI-style parallel structures.** Vary sentence shape.

Reference register: a studio principal reporting back to a client between visits. Calm, direct, numerate.

---

## Mapping to Tailwind v4 `@theme`

The tokens above are emitted in paste-ready form at `.design/theme.css`. Consumers reference them via Tailwind v4 utilities:

- Colors: `bg-primary`, `text-text-primary`, `border-border`
- Typography: `text-display`, `text-title`, `text-heading`, `text-body`, `text-caption`, `text-label`, `text-money`
- Spacing: `p-card`, `gap-section`, `space-y-stack`
- Shape: `rounded-card`, `rounded-button`, `rounded-badge`
- Font family: `font-display`, `font-body`, `font-mono`

Shipped components already reference tokens semantically (`bg-[color:var(--color-primary)]`, `text-heading`, `p-card`). Cutting `theme.css` into `src/styles/global.css` propagates the new identity to all four shipped portal surfaces without requiring regeneration.
