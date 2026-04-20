# frontend-design output — SMD Services

Aesthetic direction chosen by Captain on 2026-04-19 (third iteration): **Modern Institutional**, navy accent `#2C5282`.

Thesis: classical serif display paired with a humanist sans, hairline structure, restrained palette, single deep-navy accent. Reads like a well-designed university publication, a think tank, or a quiet foundation. Thoughtful, credentialed, measured.

## Exploration history

Three directions taken before Modern Institutional landed:

1. **Architect's Studio** (ochre + Cabinet Grotesk + Satoshi + JetBrains Mono + 2px radii) — taken through three PRs (#455 portal, #456 admin, #457 marketing) before Captain clarified direction hadn't been confirmed.
2. **Swiss Functional** (petrol / oxblood + Switzer + IBM Plex Mono + 0 radii) — rendered and partially applied via PR #458; color disliked.
3. **Modern Institutional** (navy + Crimson Pro + Public Sans + IBM Plex Mono + 0 radii) — current, applied via PR #458.

Earlier exploration HTMLs have been removed; the chosen direction's reference lives at `exploration-v2/b-modern-institutional.html`.

## Files

- `exploration-v2/a-workshop-utility.html` — Archivo Black industrial direction (not chosen, kept as reference)
- `exploration-v2/b-modern-institutional.html` — **chosen direction** reference render
- `exploration-v2/c-soft-contemporary.html` — Cormorant Garamond hospitality direction (not chosen, kept as reference)

## Token stack (authoritative values live in `.design/DESIGN.md` and `.design/theme.css`)

### Color

- Background `#F9F7F1` (warm paper, slight cream)
- Surface `#FFFFFF`
- Border hairline `#D8D4C8`
- Border subtle `#ECE8DD`
- Text primary `#1A1A1A` (graphite)
- Text secondary `#52514C`
- Text muted `#8E8C85`
- Primary / action / attention `#2C5282` (deep navy — single accent)
- Primary hover `#1F3A62`
- Complete `#2F6E42` (deep forest, success-state only)
- Error `#A02A2A` (warm red)

### Typography

- Display — **Crimson Pro** (Google, free) — classical serif
- Body — **Public Sans** (Google, free, USWDS) — humanist sans
- Data / tabular — **IBM Plex Mono** (Google OFL) for reference IDs and ISO timestamps

### Spacing rhythm

- section 48px, card 32px, stack 16px, row 12px

### Shape

- Card radius 0, button radius 0, badge radius 0. Flat institutional chrome.

### Motion

- Hover color transitions: 120ms ease. No scroll-driven animations.
