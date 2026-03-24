# SS Design Spec

> Design system reference for SS agents. Auto-synced to crane-context.
> Design Maturity: Greenfield
> Last updated: 2026-03-24

## Identity

- **Venture:** SS
- **Code:** ss
- **Tagline:** TBD
- **Audience:** TBD
- **Brand Voice:** TBD

## Tech Stack

- **Framework:** TBD
- **CSS Methodology:** TBD
- **Tailwind Version:** TBD
- **Hosting:** Cloudflare Pages

## Component Patterns

No components defined yet. Use standard HTML5 semantic elements.

When creating components:

- Use PascalCase naming (e.g., `ExpenseCard`, `StatusBadge`)
- Include ARIA roles and keyboard navigation
- Support all states: default, loading, empty, error
- Use venture-prefixed tokens for all visual properties

## Dark/Light Mode

TBD

Implementation: CSS custom properties in `:root` with `@media (prefers-color-scheme: dark)` override.

## Accessibility

- **WCAG Target:** 2.1 AA
- **Focus Indicators:** 2px solid `var(--ss-accent)`, offset 2px
- **Motion:** Respect `prefers-reduced-motion` - disable animations, use crossfade fallbacks
- **Contrast:** All text/background pairings must pass 4.5:1 (normal text) or 3:1 (large text)
- **Touch Targets:** Minimum 44px per Apple HIG / WCAG 2.5.8

## Color Tokens

All tokens use the `--ss-` prefix.

### Core Palette

| Token                     | Value   | Purpose                               |
| ------------------------- | ------- | ------------------------------------- |
| `--ss-chrome`         | `TBD` | Site chrome (header, footer, page bg) |
| `--ss-surface`        | `TBD` | Content area background               |
| `--ss-surface-raised` | `TBD` | Elevated surface (cards, modals)      |
| `--ss-text`           | `TBD` | Primary text                          |
| `--ss-text-muted`     | `TBD` | Secondary/muted text                  |
| `--ss-text-inverse`   | `TBD` | Text on accent backgrounds            |
| `--ss-accent`         | `TBD` | Primary accent (links, buttons)       |
| `--ss-accent-hover`   | `TBD` | Accent hover state                    |
| `--ss-border`         | `TBD` | Default border color                  |

### Status Colors

| Token              | Value   | Purpose       |
| ------------------ | ------- | ------------- |
| `--ss-success` | `TBD` | Success state |
| `--ss-warning` | `TBD` | Warning state |
| `--ss-error`   | `TBD` | Error state   |

## Typography

| Property        | Value                                                                           |
| --------------- | ------------------------------------------------------------------------------- |
| **Body font**   | System stack: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif |
| **Mono font**   | ui-monospace, 'Cascadia Code', 'Source Code Pro', Menlo, monospace              |
| **Base size**   | 1rem (16px)                                                                     |
| **Line height** | 1.6                                                                             |
| **H1**          | 2rem / 1.2 / 700                                                                |
| **H2**          | 1.5rem / 1.3 / 600                                                              |
| **H3**          | 1.25rem / 1.4 / 600                                                             |
| **Small**       | 0.875rem / 1.5                                                                  |

## Spacing

Base unit: 4px. Scale: 4, 8, 12, 16, 24, 32, 48, 64.

| Token               | Value |
| ------------------- | ----- |
| `--ss-space-1`  | 4px   |
| `--ss-space-2`  | 8px   |
| `--ss-space-3`  | 12px  |
| `--ss-space-4`  | 16px  |
| `--ss-space-6`  | 24px  |
| `--ss-space-8`  | 32px  |
| `--ss-space-12` | 48px  |
| `--ss-space-16` | 64px  |

## Surface Hierarchy

| Tier    | Token                     | Purpose                          |
| ------- | ------------------------- | -------------------------------- |
| Base    | `--ss-chrome`         | Page background, header, footer  |
| Content | `--ss-surface`        | Main content area                |
| Raised  | `--ss-surface-raised` | Cards, modals, elevated elements |

## Design Maturity Roadmap

1. **Foundation** - Define core tokens (colors, typography, spacing). This template.
2. **Components** - Build first 5 components using tokens. Document in this spec.
3. **Patterns** - Establish interaction patterns (forms, navigation, feedback). Document here.
4. **Polish** - Contrast audit, animation tokens, performance budget. Graduate to Tier 2.

Run `/design-brief` for a full multi-agent design definition process.
