---
description: Standalone IA audit. Walks the sitemap and reachability matrix; flags orphan destinations, dead-end surfaces, taxonomy drift, pattern-impersonation, state omissions. This is the audit nav-spec v1 could not run because v1 had no IA layer.
---

# IA Audit

Produces `examples/ia-audit-report.md` in the venture's skill output directory. Reads `.stitch/NAVIGATION.md` + `src/pages/**` and measures whether the code matches the spec's IA contract.

This is distinct from the drift audit (chrome consistency across generated and shipped artifacts). IA audit measures: _are destinations reachable?_ _do patterns hold?_ _are labels consistent?_

---

## Prerequisites

- `.stitch/NAVIGATION.md` exists with spec-version ≥ 2 (has reachability matrix).
- Venture codebase available at current working directory.
- Route inventory in `src/pages/**`.

If spec-version = 1: stop. Tell user to run `/nav-spec --revise` first to add IA layer.

---

## Audit steps

### 1. Parse the spec

Extract from `.stitch/NAVIGATION.md`:

- Reachability matrix rows (Section 3.2)
- Entry-only surfaces (Section 3.3)
- Terminal surfaces (Section 3.4)
- Pattern selections per `{surface × archetype}` (Section 4)
- State machine declarations (Section 5)
- Content taxonomy: object names, action verbs, status labels (Section 12)

### 2. Walk the code

Enumerate:

- Every route under `src/pages/**/*.astro`
- For each route, identify its surface class (from middleware / path pattern)
- For each route, extract visible `<a href="...">` elements and their text content
- Identify forms, buttons, and other navigation-triggering elements

Record: `code_routes = set(routes)`, `code_links[route] = list[(href, text)]`.

### 3. Run IA checks

For each check, produce a violation list with `route`, `rule`, `severity`, `message`, `fix`.

#### Check A — Orphan destinations (rule IA-O)

For every route in `code_routes`, is there at least one row in the matrix where `To = route`?

- If no: orphan. Severity `structural`.
- Exception: routes flagged as entry-only in matrix Section 3.3 can have no internal `To`.

Output:

```
## Orphan destinations
- /portal/settings (session-auth-client, form) — no matrix row references this route
  Fix: add matrix row from /portal to /portal/settings (section card or menu), OR remove /portal/settings if not needed
```

#### Check B — Dead-end surfaces (rule IA-D)

For every route in `code_routes`, does it have at least one Required=Yes outbound row?

- If no and not flagged as terminal: dead-end. Severity `structural`.

#### Check C — Matrix completeness for dashboards (rule IA-M)

For every matrix row where `From = dashboard archetype` and `Required=Yes`, does `code_links[From]` contain an `<a href>` matching `To`?

- If no: missing affordance. Severity `structural`.
- This catches the April 2026 ss-console portal-home gap.

#### Check D — Detail-to-parent (rule IA-B)

For every matrix row where `From` is detail archetype and `Mechanism = Back button`, does `code_links[From]` contain an `<a href>` matching `To` AND is it inside a back-affordance pattern (chevron_left icon, aria-label with parent name)?

- If missing: broken ascent. Severity `structural`.

#### Check E — Pattern conformance (rule IA-P)

For each declared pattern in Section 4, spot-check the surface's HTML for the pattern's required elements (from pattern-catalog.md).

Example: `/portal` declares `hub-and-spoke with dominant-action variant`. Required elements:

- Section cards to every sibling list → Check A catches this
- ActionCard rendered conditionally when `hasPendingInvoice` → parse conditional and confirm code has the matching branch
- Recent-activity feed → confirm presence

Partial implementation (e.g., 3 section cards but matrix says 4) is a pattern violation even if Check A passes for the 3 that exist.

#### Check F — Taxonomy adherence (rule IA-T)

Scan rendered HTML text content across all routes. For every entity, action, status declared in Section 12 taxonomy:

- Flag occurrences of "forbidden synonyms" (e.g., "Quote" when canonical is "Proposal")
- Flag action-verb drift (e.g., "Accept" button when canonical verb for proposals is "Review & Sign")
- Flag status-label drift

Severity: `semantic` (not `structural` — these rarely break functionality but erode trust).

#### Check G — State handling (rule IA-S)

For each route with a declared state machine entry in Section 5:

- Grep code for the empty-state, error-state, and loading-state rendering branches
- If a declared state has no code branch: violation

Severity `structural` for empty and error states (these always exist in practice); `semantic` for loading states.

#### Check H — Token-auth cold arrival (rule IA-TC)

For every route with surface class `token-auth`:

- Parse the page's frontmatter / script
- Check: does the page access `Astro.locals.session`? If yes, violation — token-auth surfaces must not assume session exists
- Check: does the page render a "welcome back" or similar that implies prior session?

---

### 4. Produce report

Save to `examples/ia-audit-report.md` (or `.stitch/ia-audit-<YYYY-MM-DD>.md` within the venture). Format in [examples/ia-audit-report.md](../examples/ia-audit-report.md).

Summary table at top:

| Check                                | Violations | Severity   |
| ------------------------------------ | ---------- | ---------- |
| A — Orphan destinations              | 4          | structural |
| B — Dead-end surfaces                | 0          | —          |
| C — Matrix completeness (dashboards) | 4          | structural |
| D — Detail-to-parent                 | 0          | —          |
| E — Pattern conformance              | 1          | structural |
| F — Taxonomy adherence               | 3          | semantic   |
| G — State handling                   | 2          | structural |
| H — Token-auth cold arrival          | 0          | —          |
| **Total**                            | **14**     | —          |

Then detailed sections per check.

---

## Exit criteria

IA audit passes if:

- 0 structural violations
- ≤5 semantic violations (configurable threshold per venture)

If structural violations exist, the audit fails loud — these are IA bugs. The report lists each with a specific fix; pursuing them is the remediation work.

---

## Recommended usage

- Before each nav-spec revision (catches divergence since last spec)
- Weekly during active IA changes
- After adding a new route or new surface class
- Before shipping a design system update that touches navigation

---

## Relationship to Phase 11b of author workflow

Phase 11b of `author.md` runs a subset of these checks (A, C, D, E) as the adversarial-verification step. The standalone `ia-audit` workflow runs all eight checks and produces a persisted report.

Both are valuable. The standalone audit is run regularly; Phase 11b is a gate during initial spec authoring.
