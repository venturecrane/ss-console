# Extract identity — frontend-design output → `.design/DESIGN.md`

Invoked via `/design-brief --extract-identity <path>`.

Anthropic's `frontend-design` plugin generates distinctive UI for greenfield surfaces — typography, color, spacing, motion, component style. That output is pixels. To plug it into our harness (nav-spec → ux-brief → product-design), we need concrete tokens in `.design/DESIGN.md`.

This workflow parses one or more frontend-design outputs and produces a token spec the downstream skills consume.

## Input

Either:

- A single HTML file (`path/to/frontend-design-output.html`)
- A directory of related outputs (`path/to/frontend-design-session/` containing multiple views)
- Inline paste (if the user pastes CSS/HTML directly into the conversation)

The workflow accepts all three. For a directory, process each file and synthesize common tokens across them.

## Steps

### 1. Parse the source

Read every HTML/CSS file under the input path. Extract:

- **Color values** — hex, rgb, oklch, hsl. Group by semantic role based on usage context (backgrounds, text, borders, primary actions, attention states, etc.). Frontend-design output often uses inline Tailwind arbitrary values (`bg-[#1a1a1a]`); resolve those.
- **Typography** — `font-family` declarations, `font-weight` ranges, `font-size` + `line-height` pairs. Group by role (display headline, section heading, body, caption). Note if fonts are imported from a CDN (e.g., Google Fonts `<link>`).
- **Spacing** — padding/margin values. Identify the rhythm (is it a 4px base? 8px? custom?). Record the discrete values in use; don't invent scales.
- **Radius** — `border-radius` values. Usually 2-4 distinct values (sharp / small / medium / pill).
- **Shadows / depth** — `box-shadow` strings if present. Note if the direction uses shadows at all (some aesthetic directions reject them).
- **Motion / transitions** — `transition`, `animation`, `transform` declarations. Record duration + easing.

If the output uses semantic CSS variables (`--color-primary`, etc.), prefer those over raw hex values — the aesthetic is already thinking in tokens.

### 2. Name the identity

Frontend-design picks a direction (brutalist, luxury, retro-futuristic, etc.). Read the first 20 lines of any source file to surface that language. Capture the direction as a one-line identity statement at the top of the extracted DESIGN.md:

```markdown
**Identity direction:** Brutalist — bold typography, sharp edges, high contrast, minimal motion.
```

This becomes the north star for the venture. Downstream skills cite it.

### 3. Emit `.design/DESIGN.md`

Write a structured token spec to `<venture>/.design/DESIGN.md`. Template:

```markdown
# {VENTURE_NAME} — Design Identity

**Identity direction:** {one-line statement from step 2}

**Source:** Extracted from `frontend-design` output on {DATE}. Regenerate when identity is revised.

## Color

Semantic roles and hex values (downstream consumers reference these by role, not hex):

| Role           | Hex     | Usage                  |
| -------------- | ------- | ---------------------- |
| primary        | #XXXXXX | Links, primary actions |
| primary-hover  | #XXXXXX |                        |
| surface        | #XXXXXX | Card/panel backgrounds |
| background     | #XXXXXX | Page background        |
| border         | #XXXXXX | Dividers, card edges   |
| text-primary   | #XXXXXX | Body copy              |
| text-secondary | #XXXXXX | Captions, metadata     |
| text-muted     | #XXXXXX | Placeholders, disabled |
| action         | #XXXXXX | Focus rings            |
| complete       | #XXXXXX | Success state          |
| attention      | #XXXXXX | Warning state          |
| error          | #XXXXXX | Error state            |
| meta           | #XXXXXX | Eyebrow labels, accent |

Add or omit rows per the identity's actual palette — don't invent roles the source didn't establish.

## Typography

| Role    | Family   | Weight   | Size / Line-height | Letter-spacing | Notes                  |
| ------- | -------- | -------- | ------------------ | -------------- | ---------------------- |
| display | {family} | {weight} | {px/lh}            | {tracking}     | hero/landing headlines |
| title   | {family} | {weight} | {px/lh}            |                | section titles         |
| heading | {family} | {weight} | {px/lh}            |                | subsection             |
| body-lg | {family} | {weight} | {px/lh}            |                | prose, long-form       |
| body    | {family} | {weight} | {px/lh}            |                | default body           |
| caption | {family} | {weight} | {px/lh}            | {tracking}     | metadata, timestamps   |
| label   | {family} | {weight} | {px/lh}            | {tracking}     | uppercase eyebrows     |
| money   | {family} | {weight} | {px/lh}            | tabular-nums   | dollar figures         |

**Font loading:** {list imports — Google Fonts `<link>`, @font-face, etc.}

## Spacing

Discrete values in the source, and the semantic naming we'll use:

| Token   | Value | Usage                             |
| ------- | ----- | --------------------------------- |
| section | {Npx} | Gap between major page sections   |
| card    | {Npx} | Card internal padding             |
| stack   | {Npx} | Vertical stack of sibling content |
| row     | {Npx} | Gap between list rows             |

Add more rows if the identity uses more distinct values. If the identity is maximalist with 12 spacing values, record them all.

## Shape

| Token         | Value   | Usage              |
| ------------- | ------- | ------------------ |
| radius-card   | {Npx}   | Card/panel corners |
| radius-button | {Npx}   | Button corners     |
| radius-badge  | {value} | Pill shape         |

If the identity rejects rounding entirely (brutalist), document that: `radius-card: 0 — sharp corners per identity direction`.

## Motion

- **Defaults:** `{duration} {easing}` for simple state transitions (hover, focus).
- **Page transitions:** {describe if any}
- **Scroll-triggered:** {describe if any — some identities use heavy scroll animations, some reject them}
- **Rejection clause:** e.g., "No scroll-driven parallax; identity is print-inspired."

## Shadows / depth

Either: list the shadow tokens (`shadow-sm`, `shadow-md`, etc.) with their values.
Or: document rejection if the identity is flat.

## Anti-patterns (identity-level)

List visual/structural things this identity explicitly rejects:

- Generic SaaS purple gradients
- System font fallbacks
- {whatever the source output clearly avoided}

## Voice notes

If the frontend-design output included copy, note the tone register (terse / warm / formal / irreverent). This informs downstream ux-brief authoring.

## Mapping to Tailwind v4 `@theme`

The tokens above must land in the venture's `src/styles/global.css` `@theme` block (Tailwind v4) for Astro/Next.js consumers. Emit a second file: `.design/theme.css` containing the `@theme { ... }` block ready to paste or merge. Downstream product-design invocations consume these tokens via semantic class names (`bg-[color:var(--color-primary)]`, etc.).
```

### 4. Emit `.design/theme.css` (Tailwind v4 `@theme` block)

A paste-ready `@theme` declaration derived from the DESIGN.md tokens. This is what the venture's `src/styles/global.css` needs to include for Tailwind to generate the utility classes the product-design skill will emit.

Example:

```css
@theme {
  --color-primary: #1e40af;
  --color-primary-hover: #1e3a8a;
  /* ... */

  --text-display: 2rem;
  --text-display--line-height: 2.5rem;
  --text-display--font-weight: 700;
  /* ... */

  --spacing-section: 2rem;
  --spacing-card: 1.5rem;
  /* ... */

  --radius-badge: 9999px;
  --radius-card: 0.75rem;
  --radius-button: 0.5rem;
}
```

### 5. Report

Tell the Captain:

- Path to the new/updated `.design/DESIGN.md`
- Path to `.design/theme.css`
- Identity direction (one line)
- Key tokens extracted (counts: N colors, N type roles, N spacing values)
- Next step prompt: "Identity captured. Next: `/nav-spec` (if structural spec not yet authored) → `/ux-brief <surface-area>` → `/product-design --set <surface-area>`."

Do NOT modify the venture's `global.css` automatically — that's a human/agent decision about when to cut over to the new identity. The captain or a follow-up agent merges `theme.css` into `global.css` when ready.

## Failure modes

- **Source is ambiguous or contradictory.** If the frontend-design output contains multiple inconsistent directions (e.g., brutalist heading + glassmorphism cards), surface the inconsistency rather than averaging it. Ask the Captain which direction wins.
- **Source has no identifiable tokens.** Happens with hand-typed inline styles rather than systematic design. Extract what's there and flag the gaps.
- **Fonts require licensing we can't resolve.** If frontend-design chose a commercial font (e.g., a licensed specimen from a type foundry), record the selection but flag that the license purchase is a Captain-gated external dependency.
