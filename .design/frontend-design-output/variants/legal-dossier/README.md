# Legal Dossier — comparison variant

Same two hero surfaces as the parent directory, rendered in the **Legal Dossier**
direction rather than **Architect's Studio**. Produced for side-by-side visual
comparison before committing.

## What's different from the Architect's Studio version

| Axis              | Architect's Studio (parent)               | Legal Dossier (this variant)                           |
| ----------------- | ----------------------------------------- | ------------------------------------------------------ |
| Display font      | Cabinet Grotesk (Fontshare)               | **EB Garamond** (Google, serif with real small caps)   |
| Body font         | Satoshi (Fontshare)                       | **EB Garamond** — prose in serif for a reading feel    |
| Meta font         | JetBrains Mono (mono)                     | **Public Sans** (civic sans, tracked caps)             |
| Background        | `#FAFAF9` (warm near-white)               | `#F8F5EF` (document cream)                             |
| Ink               | `#0A0A0A` (graphite)                      | `#111827` (near-black, slightly cooler)                |
| Accent            | `#B45309` (deep ochre)                    | `#7C1D1D` (claret — "wax seal")                        |
| Card radius       | 2px                                       | 0 — sharp rectangles                                   |
| Max content width | 1040px                                    | 780px — classical brief column                         |
| Layout            | Two-column on desktop (main + rail)       | Single column — no side rail                           |
| Chrome            | Reference strips on every card, mono data | Section numbering (I–IV), numbered entries, letterhead |
| Date format       | `2026-04-14` (ISO)                        | `April 14, 2026` (natural American)                    |
| Money             | Mono tabular                              | Serif with lining tabular figures                      |
| Status indicator  | Filled rectangular badge                  | Small-caps inline marker with left rule                |
| Figure style      | Lining throughout                         | Old-style in prose, lining in tables                   |

## What stays the same

- Same 7 surfaces eventually (this variant only renders 2 hero examples)
- Same data, same fixture copy, same "named human" requirement, same money rule
- Same anti-patterns respected (no tiles, no progress bars, no KPI cards, etc.)

## How to preview

```bash
open /Users/scottdurgan/dev/ss-console/.design/frontend-design-output/variants/legal-dossier/portal-home.html
open /Users/scottdurgan/dev/ss-console/.design/frontend-design-output/variants/legal-dossier/portal-invoices-detail.html
```

Compare side-by-side with the Architect's Studio files one level up.

## If this is the chosen direction

Move these two files up one level, overwriting the Studio versions, and update
the parent `README.md` token summary. The `/design-brief --extract-identity`
step reads whatever is in the parent directory.
