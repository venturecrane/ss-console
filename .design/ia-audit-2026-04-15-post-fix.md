# IA Audit Report — ss-console — 2026-04-15 (post-fix)

**Spec audited against:** `.design/NAVIGATION.md` spec-version=2, skill-version=2.0.0.
**Code commit:** `371f0ec` (main) — "feat(nav-spec): migrate NAVIGATION.md to v2 + fix R16 orphan destinations (#396)".
**Scope:** all `session-auth-client` routes (`src/pages/portal/**`).
**Invocation:** `/nav-spec --ia-audit` (second run; first audit was pre-fix at `.design/ia-audit-2026-04-15.md`).
**Purpose:** verify the 4 R16 violations flagged in the pre-fix audit are resolved post-PR #396.

---

## Summary

| Check                                        | Violations | Severity | Blocks merge? |
| -------------------------------------------- | ---------- | -------- | ------------- |
| A — Orphan destinations (R16)                | 0          | —        | —             |
| B — Dead-end surfaces (R18)                  | 0          | —        | —             |
| C — Matrix completeness for dashboards (R16) | 0          | —        | —             |
| D — Detail-to-parent (R5)                    | 0          | —        | —             |
| E — Pattern conformance (R17)                | 0          | —        | —             |
| F — Taxonomy adherence (R20)                 | 2          | semantic | No            |
| G — State handling (R21)                     | 0          | —        | —             |
| H — Token-auth cold arrival (R19)            | 0          | —        | —             |
| Auxiliary — Landmarks (R14a)                 | 7          | semantic | No            |
| Auxiliary — Skip-link (R15)                  | 7          | semantic | No            |
| Auxiliary — Heading hierarchy (R22a)         | 6          | semantic | No            |

**Structural violations:** 0 — **verdict: PASS (structural)**.
**Semantic violations:** 22 — not blocking; documented for follow-up PR.

The R16 fix in PR #396 is confirmed correct. All four previously-orphaned portal sibling lists (`/portal/quotes`, `/portal/invoices`, `/portal/documents`, `/portal/engagement`) are now reachable from `/portal` via section cards matching the pattern contract for hub-and-spoke (Section 6.4, Appendix C.1.1).

---

## What the validator ran

Per-route invocation of `python3 ~/.agents/skills/nav-spec/validate.py --spec .design/NAVIGATION.md --route <route>` across all 7 portal surfaces:

| Route                   | Archetype | Pattern       | Result          | Structural | Semantic |
| ----------------------- | --------- | ------------- | --------------- | ---------- | -------- |
| `/portal`               | dashboard | hub-and-spoke | **pass-struct** | 0          | 3        |
| `/portal/quotes`        | list      | master-detail | **pass-struct** | 0          | 5        |
| `/portal/invoices`      | list      | master-detail | **pass-struct** | 0          | 3        |
| `/portal/documents`     | list      | master-detail | **pass-struct** | 0          | 3        |
| `/portal/engagement`    | detail    | master-detail | **pass-struct** | 0          | 4        |
| `/portal/quotes/[id]`   | detail    | master-detail | **pass-struct** | 0          | 3        |
| `/portal/invoices/[id]` | detail    | master-detail | **pass-struct** | 0          | 2        |

"pass-struct" = zero R16/R17/R18/R19/R21/R23/R24 (structural IA rules) violations; semantic violations present but non-blocking.

---

## A. Orphan destinations — RESOLVED

Every code route has ≥1 matrix row where it appears as `To`.

| Code route              | Appears as `To` in matrix?                                         |
| ----------------------- | ------------------------------------------------------------------ |
| `/portal`               | Yes (3 rows: back from quotes/invoices/documents/engagement lists) |
| `/portal/quotes`        | Yes (section card from `/portal`)                                  |
| `/portal/quotes/[id]`   | Yes (row click from `/portal/quotes`)                              |
| `/portal/invoices`      | Yes (section card from `/portal`; ActionCard conditional)          |
| `/portal/invoices/[id]` | Yes (row click from `/portal/invoices`)                            |
| `/portal/documents`     | Yes (section card from `/portal`)                                  |
| `/portal/engagement`    | Yes (section card from `/portal`)                                  |

**Delta from pre-fix:** 4 violations → 0. PR #396 section card grid resolved all four.

---

## B. Dead-end surfaces

No violations. Every surface has at least one navigation exit.

- Lists exit to parent hub (/portal) and forward to details.
- Details exit to parent list (quotes/invoices) or hub (engagement).
- Dashboard exits to four siblings + consultant contact + logout.

---

## C. Matrix completeness for dashboards — RESOLVED

Pre-fix audit flagged `/portal` as missing 4 Required=Yes outbound links. Post-fix verification (grep of `src/pages/portal/index.astro`):

```
✓ href="/portal/quotes"       line 470
✓ href="/portal/invoices"     line 494
✓ href="/portal/documents"    line 518
✓ href="/portal/engagement"   line 542
```

All four section cards render with:

- Full-tile `<a>` element (entire card clickable, not just label)
- `min-h-[88px]` ≥ 44px tap target (R7)
- `aria-label` composed of destination + status caption
- Focus ring per Section 8 state colors
- Material Symbols icon + Plus Jakarta Sans heading + muted caption

`/admin` and `/admin/analytics` continue to pass this check (tabs in `AdminLayout.astro`).

---

## D. Detail-to-parent

All detail archetypes retain correct back affordances:

| Detail                    | Back target            | Mechanism    | Status |
| ------------------------- | ---------------------- | ------------ | ------ |
| `/portal/quotes/[id]`     | `/portal/quotes`       | chevron_left | ✓      |
| `/portal/invoices/[id]`   | `/portal/invoices`     | chevron_left | ✓      |
| `/portal/engagement`      | `/portal`              | chevron_left | ✓      |
| `/admin/entities/[id]`    | `/admin/entities`      | breadcrumb   | ✓      |
| `/admin/engagements/[id]` | `/admin/entities/[id]` | breadcrumb   | ✓      |
| `/admin/assessments/[id]` | `/admin/entities/[id]` | breadcrumb   | ✓      |

---

## E. Pattern conformance

`/portal` declared as `hub-and-spoke with dominant-action + recent-activity variants`. Required elements per `pattern-catalog.md §1.1`:

| Required element                               | Implemented? | Evidence                                        |
| ---------------------------------------------- | ------------ | ----------------------------------------------- |
| Section cards → every sibling list             | ✓ (new)      | 2×2 grid at `src/pages/portal/index.astro:459+` |
| ActionCard (dominant-action, conditional)      | ✓            | Existing; conditional on `hasPendingInvoice`    |
| Recent Activity feed (recent-activity variant) | ✓            | Existing                                        |
| Consultant block                               | ✓            | Existing                                        |

Pattern now fully conforms. Pre-fix gap (section cards missing) → resolved.

---

## F. Taxonomy adherence

2 real violations found after filtering false positives:

| Route                   | Text                    | Canonical term      | Severity |
| ----------------------- | ----------------------- | ------------------- | -------- |
| `/portal/quotes` (list) | "Project price" (label) | "Engagement price"  | semantic |
| `/portal/engagement`    | "Current Phase"         | "Current Milestone" | semantic |

**False positives filtered** (these are NOT violations per Section 12 guidance):

- TypeScript type imports: `import type { Quote }` — type/DB names allowed.
- Variable names: `(quote: Quote)` — internal code, not user-visible.
- "Statement of Work" — distinct legal instrument, not a synonym for "Invoice". Validator's naive regex over-matches on the substring "Statement"; this is a known limitation to address in a future validator rev.

**Fix:** single-file edits.

- `src/pages/portal/quotes/index.astro:153` → change `Project price` to `Engagement price`
- `src/pages/portal/engagement/index.astro:145` → change `Current Phase` to `Current Milestone`

---

## G. State handling

All declared state machine entries (Section 5.1) have code branches:

- `/portal` — empty, error, populated-pending, populated-touchpoint, populated-idle all rendered via `src/lib/portal/states.ts`
- `/portal/quotes/[id]` — sent, accepted, declined, expired branches present
- `/portal/invoices/[id]` — sent, overdue, paid, void branches present
- List empty states match Section 12.6 copy

---

## H. Token-auth cold arrival

`/book/manage/[token]` (only current token-auth surface) continues to:

- Not reference session cookies
- Not render "welcome back" copy
- Not render Sign out affordance
- Not access `Astro.locals.session`

No new token-auth surfaces introduced.

---

## Auxiliary findings (semantic, not blocking)

These were not scoped into the pre-fix audit but emerged from the v2 validator's comprehensive per-file sweep. All are semantic (not structural), so they don't fail the audit — but they're worth capturing for a follow-up PR.

### R14a — Missing `<header role="banner">` landmark (7 routes)

Portal pages wrap their sticky top band in `<div class="sticky top-0...">` rather than `<header>`. Screen reader landmark navigation is degraded.

**Fix:** one-line tag swap per route. Low effort. No visual change.

### R15 — Missing skip-to-main link (7 routes)

No `<a href="#main" class="sr-only focus:not-sr-only ...">Skip to main content</a>` before the header. Keyboard users cannot bypass header chrome.

**Fix:** add skip link to a shared layout/partial so it lives in one place. Would require factoring the portal shell — more scope than one-line tag swaps.

### R22a — Heading hierarchy (6 routes)

- `/portal`, `/portal/quotes/[id]`, `/portal/invoices/[id]` — each renders 2+ `<h1>` (mobile/desktop split layouts; only one visible at a time but both in DOM).
- `/portal/quotes`, `/portal/invoices`, `/portal/documents`, `/portal/engagement` — page heading is `<h2>`, no `<h1>`.

**Fix:** pick one primary heading per route; demote siblings. Moderate scope since the mobile/desktop split pattern is load-bearing for the current layout.

---

## Comparison: pre-fix vs post-fix

| Check                                | Pre-fix (2026-04-15 first) | Post-fix (2026-04-15 second) | Delta  |
| ------------------------------------ | -------------------------- | ---------------------------- | ------ |
| A — Orphan destinations              | 4 structural               | 0                            | -4     |
| B — Dead-end surfaces                | 0                          | 0                            | 0      |
| C — Matrix completeness (dashboards) | 4 structural               | 0                            | -4     |
| D — Detail-to-parent                 | 0                          | 0                            | 0      |
| E — Pattern conformance              | 1 structural               | 0                            | -1     |
| F — Taxonomy adherence               | 0 (then)                   | 2 semantic                   | +2 \*  |
| G — State handling                   | 0                          | 0                            | 0      |
| H — Token-auth cold arrival          | 0                          | 0                            | 0      |
| **Structural total**                 | **9 (5 unique)**           | **0**                        | **-9** |

\* Pre-fix run used a coarse grep per the workflow and scanned user-visible text only; the post-fix run used `validate.py` which scans raw file content more aggressively. The two new semantic F violations ("Project price", "Current Phase") existed pre-fix too — they just weren't surfaced by the first pass. These are pre-existing drift items the v2 validator is now catching.

---

## Verdict

**Structural: PASS.** The v2 skill's thesis holds:

1. It authored a reachability matrix declaring 4 Required=Yes portal sibling links.
2. The pre-fix audit exposed 4 orphan destinations against that matrix.
3. PR #396 implemented the section card grid per the pattern contract.
4. The post-fix audit confirms 0 structural violations across all portal routes.

This is the end-to-end loop v1 couldn't execute: **spec prediction → code disagreed → fix → spec holds.** v1 had no matrix, so no prediction could be falsified; v2 does, and PR #396 proves it.

**Semantic: 22 violations logged**, none structural, none blocking. Primary clusters:

- A11y landmarks + skip link (R14a, R15) — systematic across portal, 14 violations from a single missing shell pattern.
- Heading hierarchy (R22a) — mobile/desktop split layouts leak to screen readers.
- Taxonomy drift (R20) — 2 surgical label fixes.

**Recommended follow-up:** a separate PR addressing the a11y cluster (single layout refactor wipes out ~21 of 22). Taxonomy fixes can piggy-back.

---

## Next audit date

2026-04-29 (two weeks). Per README.md §7 verification protocol: re-run against any Stitch output produced in the interim to measure drift-prevention in practice. Use `/nav-spec --ia-audit`.
