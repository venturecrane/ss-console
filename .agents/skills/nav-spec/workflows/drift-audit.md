---
description: Standalone chrome-level drift audit. Scans shipped code and generated artifacts for chrome inconsistencies. Distinct from ia-audit (which covers reachability and patterns).
---

# Drift Audit (chrome)

Chrome-level consistency audit. Scans shipped code and generated artifacts; flags inconsistencies across headers, back affordances, breadcrumbs, footers, skip-links, state colors, and mobile↔desktop transforms.

Output: an in-memory drift report or saved to `.stitch/drift-audit-<YYYY-MM-DD>.md`. Format in [examples/drift-audit-report.md](../examples/drift-audit-report.md).

---

## When to run this (vs ia-audit)

| Audit                         | Measures                                       | When to run                                                        |
| ----------------------------- | ---------------------------------------------- | ------------------------------------------------------------------ |
| `/nav-spec --drift-audit`     | Chrome consistency across pages and artifacts  | After multiple Stitch generations, before pattern roundtrip review |
| `/nav-spec --ia-audit`        | IA reachability, pattern conformance, taxonomy | After route changes, before shipping IA-affecting work             |
| `/nav-spec --validate <file>` | Single-file rule check (R1–R24)                | Post-generation, CI                                                |

Drift audit is retrospective — it measures what has shipped. ia-audit is structural — it measures whether what shipped matches the spec.

---

## Audit steps

### 1. Locate artifacts

- `src/layouts/*.astro` — every layout
- `src/components/**/*{Nav,Header,Footer,Sidebar,Layout}*.astro` — shared chrome components
- `src/pages/**/*.astro` — note pages that bypass the layout and render own chrome
- `.stitch/designs/**/*.html` — prior Stitch output (categorize by generation date if possible)

### 2. Extract chrome features per artifact

For each file, record:

| Field                                | Values                                                                     |
| ------------------------------------ | -------------------------------------------------------------------------- |
| Surface class (inferred or declared) | public / auth-gate / token-auth / session-auth-client / session-auth-admin |
| Archetype (inferred)                 | dashboard / list / detail / ...                                            |
| Header pattern                       | sticky / fixed / absent                                                    |
| Header background                    | solid white / translucent / colored                                        |
| Header height                        | 56 / 64 / other                                                            |
| Client-name decoration               | none / leading icon / leading SVG / logo mark                              |
| Back affordance                      | present / absent / history.back() / href="#" / canonical URL               |
| Breadcrumbs                          | none / single-link / multi-level                                           |
| Nav tabs in header                   | none / 2 / 3+                                                              |
| Bottom-tab nav                       | present / absent                                                           |
| Sticky bottom action bar             | present / absent                                                           |
| Footer                               | present / absent                                                           |
| Skip-to-main                         | present (correct target) / present (wrong target) / absent                 |
| Mobile pattern                       | flex / hidden / collapsed                                                  |
| Breakpoint                           | 768 / 1024 / other                                                         |

### 3. Build two matrices

**Live code matrix** (rows = shipped files):

```
| File | Surface | Archetype | Header | Back | Breadcrumbs | Mobile | Layout? |
```

**Generated artifact matrix** (rows = .stitch/designs/\* files):

```
| File | Surface (inferred) | Archetype | Header | Back | Breadcrumbs | Mobile |
```

### 4. Identify drift clusters

Group files by surface class. Within each cluster, count the distinct values for each chrome feature. If >1 distinct value exists (excluding genuine per-archetype variation), that feature has drift.

Example outputs:

```
## Drift summary

### session-auth-client (portal)
- **Header pattern:** 2 distinct values across 7 files
  - `sticky top-0`: 4 files (post-PR #391 retrofit)
  - `fixed top-0`: 3 files (pre-retrofit — /portal/dev/*, /portal/legacy/*)
  - Drift: files not yet retrofitted

- **Header client-name decoration:** 3 distinct values
  - none (correct per spec): 5 files
  - leading water-drop icon: 1 file (portal/demo-v2.html, Stitch drift)
  - leading logo: 1 file (portal/legacy/invoice.astro — pre-PR #375)

- **Back affordance (detail archetypes):** 2 distinct values
  - `<a href="/portal/invoices">`: 2 files (canonical, correct)
  - `<a href="#">` and `onclick="history.back()"`: 1 file (Stitch artifact not yet reconciled)
```

### 5. Score drift severity

- **Structural drift:** fixed-vs-sticky, nav-tabs-present, bottom-tab-nav, footer-on-auth. Count and list.
- **Semantic drift:** back-href-canonical-vs-hash, breadcrumb-wrapper, landmark-absence.
- **Cosmetic drift:** header-height-off-by-pixels, backdrop-blur, opacity modifiers.

### 6. Output the drift summary

4–6 bullets that are specific, file-referenced, and lead the eye to the biggest deltas. Not a complete list — ammunition for Phase 3 of `author.md` (or for `revise.md` when the spec already exists).

Example bullets:

- Portal headers split: 4 sticky (post-retrofit), 3 fixed (pre-retrofit)
- 2 of 7 portal detail pages lack a back affordance; remaining 5 use canonical URLs (correct)
- 3 Stitch artifacts in `.stitch/designs/portal-v1/` use `backdrop-blur-sm` — confirms Phase 0 strict-compliance leakage
- 1 portal list page uses history.back() — this is the Stitch-artifact version, shipped version was fixed in PR #391
- No drift on mobile breakpoint (consistent 768px)
- Admin nav tabs consistent across all admin routes (ratified exception in spec Appendix D)

---

## Output artifacts

### Option 1 — In-memory (default)

Drift audit is run as a phase within `author.md` Phase 8 (was Phase 2 in v1). Output is consumed by the author workflow and not persisted.

### Option 2 — Standalone run

When invoked directly (`/nav-spec --drift-audit`), save output to `.stitch/drift-audit-<YYYY-MM-DD>.md`. Keep historical audits for measuring drift change over time.

---

## Exit criteria

Drift audit does not fail/pass. It produces an inventory. Humans decide which drift matters — the spec is the declaration.

However, if the audit is run after `validate.py` has run on the same artifacts, the drift audit should find a strict subset of what validate.py found (drift audit measures presence across files; validate.py measures per-file conformance). Any drift not caught by validate.py is a signal that a new validator rule may be needed.
