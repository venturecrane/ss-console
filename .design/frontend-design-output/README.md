# frontend-design output — SMD Services

Aesthetic direction chosen by Captain on 2026-04-19: **Swiss Functional**.

Thesis: modernist craft. Disciplined grid, neutral grotesque type + tabular
mono, restrained palette, single deep accent (petrol blue). Authority through
design rigor, not decoration. Müller-Brockmann / Vignelli tradition.

An earlier Architect's Studio direction (ochre + Cabinet Grotesk + Satoshi +
JetBrains Mono) was explored and initially taken forward across three PRs
(#455, #456, #457) before the Captain clarified that direction had not been
confirmed. Swiss Functional replaces it end to end via a tokens-only swap —
components and surfaces are aesthetic-direction-agnostic.

## Files

- `swiss-functional/portal-home.html` — hero surface 1. Dashboard in invoice-
  pending state. Masthead, section numbering, dominant invoice block, flat
  consultant block, activity log, engagement summary, ledger.
- `swiss-functional/portal-invoices-detail.html` — hero surface 2. Invoice
  deep-link, unpaid. Breadcrumb, status, amount hero, line-items table,
  prior payments, side-rail consultant.

Both files are self-contained HTML with `<style>` blocks declaring CSS
custom properties. `/design-brief --extract-identity` can re-parse them.

## Token stack (authoritative values live in `.design/DESIGN.md` and

`.design/theme.css`)

### Color

- Background `#F8F8F6` (pure paper)
- Surface `#FFFFFF`
- Border hairline `#D1D1CE`
- Border subtle `#E5E4E1`
- Text primary `#0A0A0A`
- Text secondary `#4A4A47`
- Text muted `#8E8E8A`
- Primary / action / attention `#1E4F5C` (petrol blue — single accent)
- Primary hover `#163E48`
- Complete `#2C6E3F` (deep forest, success-state only)
- Error `#8B1A1A`

### Typography

- Display + body — **Switzer** (Fontshare, free) 400/500/600/700
- Data / labels — **IBM Plex Mono** (Google OFL) 400/500/600

### Spacing rhythm

- section 48px, card 24px, stack 16px, row 12px

### Shape

- Card radius 0, button radius 0, badge radius 0. Swiss discipline.

### Motion

- Hover color transitions: 120ms ease
- No scroll-driven animations, no page-transition flourishes. Modernist restraint.
