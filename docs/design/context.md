# Design Brief Context

## Source Documents

- **PRD:** `docs/pm/prd.md` (1,557 lines — REQUIRED)
- **Executive Summary:** VCMS note `note_01KN2ZFMZ8PSXYYR9QAPV4MTNV` (tag: executive-summary, scope: ss). Fetch with `crane_notes(q: "SMD Services - Executive Summary")`.
- **Design Tokens (live):** `src/styles/global.css` (imports `@venturecrane/tokens/ss.css`). Compiled token values at `node_modules/@venturecrane/tokens/dist/ss.css` are the source of truth.
- **Component Libraries:**
  - `src/components/portal/` (17 portal components: PortalListItem, StatusPill, MoneyDisplay, ConsultantBlock, EngagementProgress, QuoteDetail, etc.)
  - `src/components/` (16 marketing components: Hero, Nav, Footer, Pricing, About, etc.)
  - `src/components/admin/` (admin console)
  - `src/components/booking/` (assessment booking flow)
- **UI Patterns (enforced):** `docs/style/UI-PATTERNS.md` — 7 cited rules (status display by context, redundancy ban, button hierarchy, heading skip ban, typography scale, spacing rhythm, shared primitives). These were promoted to enterprise scope.
- **Empty-state pattern:** `docs/style/empty-state-pattern.md` — render nothing or "TBD in SOW" rather than fabricated client-facing copy (P0 enforcement).
- **Design Charter:** Not found.
- **Live Site:** `https://smd.services` (marketing), `https://portal.smd.services` (client), `https://admin.smd.services` (internal).

## Enterprise Design System Context

The cross-venture catalog has been loaded via VCMS:

- **Patterns:** 8 cross-venture UX problem/solution pairs in Polaris Problem/Solution/Examples format. SS authored 7; the 8th (Actions and Menus) was authored in enterprise scope. Cited authority required (Polaris, Material 3, NN/g, Carbon, Atlassian, Apple HIG, WCAG).
- **Components:** Atomic Design vocabulary (atoms / molecules / organisms). Six seeded entries; the catalog is a map, not a library. SS's `PortalListItem`, `StatusPill`, `MoneyDisplay` are the canonical Pattern 7 reference.

Concrete pattern + component decisions in agent output should map back to the loaded catalog (or extend it with rationale).

## Design Maturity: Full system

Mature design system with named identity (Plainspoken Sign Shop, migrated from Desert Functional 2026-04-23), W3C-DTCG token package (`@venturecrane/tokens/ss.css`), 33+ implemented components, 7 cited and enforced UI patterns, merge-gate workflows.

**Your job is to refine, document gaps, and ensure consistency — not to redesign.**

Do NOT propose:

- Replacing existing tokens (cream / ink / burnt orange / olive / brick palette is canonical)
- Replacing existing typography (Archivo + Archivo Narrow + JetBrains Mono is canonical)
- Replacing existing components — mark each as "Exists" / "Exists (needs update)" / "New"
- Rounded corners, shadows, gradient washes, multiple accent colors, pill-shaped status badges (these are explicitly anti-pattern per identity rules)

Do propose:

- Concrete additions where gaps exist (e.g. components the PRD requires that don't exist)
- Token additions consistent with the existing taxonomy
- Pattern extensions with clear rationale
- Accessibility improvements
- Component states the PRD requires (empty/loading/error)

## Live Identity Summary (Plainspoken Sign Shop)

**Source of truth:** `node_modules/@venturecrane/tokens/dist/ss.css` and `src/styles/global.css`.

**Important:** `.design/DESIGN.md` documents a stale "Modern Institutional" direction (2026-04-19) that was SUPERSEDED on 2026-04-23. Do not follow `.design/DESIGN.md`. The current identity is Plainspoken Sign Shop — 1950s commercial signage register, paint-job not brochure.

**Live palette:**

| Role                         | Hex                   | Usage                                                           |
| ---------------------------- | --------------------- | --------------------------------------------------------------- |
| background                   | `#f5f0e3`             | Cream paper. Primary page background.                           |
| surface                      | `#f5f0e3`             | Card background — flat paper. Cards defined by rules, not fill. |
| surface-inverse              | `#1a1512`             | Ink. Inverted surfaces.                                         |
| border                       | `rgba(26,21,18,0.16)` | Default border (ink at 16% opacity).                            |
| border-subtle                | `rgba(26,21,18,0.08)` | Subtle divider (ink at 8% opacity).                             |
| text-primary                 | `#1a1512`             | Ink. Primary text on cream.                                     |
| text-secondary               | `#4a423c`             | Subdued ink. Metadata.                                          |
| text-muted                   | `#8a7f73`             | Muted ink. Tertiary.                                            |
| primary / action / attention | `#c5501e`             | Burnt orange. CTAs. Single-accent discipline.                   |
| primary-hover                | `#a84318`             | Burnt orange, deepened.                                         |
| complete                     | `#4a6b3e`             | Olive. Complement. Success states.                              |
| error                        | `#a02a2a`             | Brick. Errors.                                                  |

**Typography:** Archivo (display + body, single family for both — Plainspoken register), Archivo Narrow (chips/labels), JetBrains Mono (IDs/code).

Hero scale weight is **900** with negative tracking. Plainspoken display uses the hero/kpi/section-h/price-row/num-cell tokens — all weight 900.

**Spacing (rhythm tokens):** `section` 48px, `card` 32px, `stack` 16px, `row` 12px.

**Shape:** All radii `0` (flat institutional / commercial signage). No rounded corners anywhere.

**Shadows:** None. Typography and hairlines carry the page.

**Motion:** `instant 0ms / fast 150ms / base 250ms / slow 400ms`, easing tokens. Respect `prefers-reduced-motion`.

## User Corrections: None

(Auto mode — skill ran without confirmation pause; user pre-authorized 3 rounds.)
