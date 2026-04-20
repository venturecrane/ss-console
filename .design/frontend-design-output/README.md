# frontend-design output — SMD Services portal

Aesthetic direction chosen by Captain on 2026-04-19: **Architect's Studio**.

Thesis: the portal reads like the project file from a small design studio.
Monospace for data, geometric sans for prose, hairline structure, a reference
number on everything. Authority through precision, not decoration.

## Files

- `portal-home.html` — hero surface 1. Dashboard in invoice-pending state.
  Shows: masthead, dominant action card, consultant block with photo placeholder,
  activity log, engagement summary, ledger. Responsive 390px / 1280px.
- `portal-invoices-detail.html` — hero surface 2. Invoice deep-link, unpaid.
  Shows: breadcrumb, status badge, invoice body with line items, pay CTA,
  consultant block, download affordance. Responsive 390px / 1280px.

Both files are self-contained HTML with `<style>` blocks declaring CSS custom
properties. `/design-brief --extract-identity` reads them directly.

## Token stack (summary; authoritative values live in each file's `:root`)

### Color

- Background `#FAFAF9` (warm near-white)
- Surface `#FFFFFF`
- Border hairline `#E5E5E4`
- Border subtle `#F0EFED`
- Text primary `#0A0A0A` (graphite)
- Text secondary `#52525B`
- Text muted `#A1A1AA`
- Primary / attention `#B45309` (deep ochre, amber 700)
- Primary hover `#92400E`
- Complete `#15803D`
- Error `#991B1B`

### Typography

- Display — **Cabinet Grotesk** (Fontshare, free) 700, tracking -0.02em
- Body — **Satoshi** (Fontshare, free) 400/500
- Data / labels — **JetBrains Mono** (Google, OFL) 500, tabular-nums
- No Inter anywhere. No PJS anywhere.

### Spacing rhythm

- section 40px, card 28px, stack 16px, row 12px

### Shape

- Card radius 2px, button radius 2px, badge radius 2px. Sharp by intent.

### Motion

- Hover color transitions: 120ms ease
- No scroll-driven animations, no page-transition flourishes. Print logic.

## Next

Step 2 of the pipeline: `/design-brief --extract-identity /Users/scottdurgan/dev/ss-console/.design/frontend-design-output/`

That emits `.design/DESIGN.md` + `.design/theme.css` from these files.
