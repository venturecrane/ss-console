---
description: Post-generation validator rubric. Documents all 24 rules (R1–R15 chrome, R16–R24 IA + pattern + a11y). validate.py implements; this file specifies.
---

# Validation Rubric

`validate.py` enforces 24 rules on generated HTML and shipped surfaces. This document is the authoritative rubric; `validate.py` implements it.

Violations are reported with:

- `rule` — identifier (R1–R24)
- `selector` — DOM selector or description of where the violation was found
- `severity` — `cosmetic` | `semantic` | `structural`
- `message` — human-readable description
- `fix` — concrete suggested remediation

The validator exits 0 (pass) when violation_count is 0, and 1 (fail) otherwise. Severity affects retry behavior in the `stitch-design` pipeline:

- `structural` → always retries, up to 2 attempts
- `semantic` → retries once
- `cosmetic` → warns; passes by default; elevates per-venture configurable

---

## Invocation

```bash
python3 validate.py \
  --file /path/to/generated.html \
  --surface <public|auth-gate|token-auth|session-auth-client|session-auth-admin> \
  --archetype <dashboard|list|detail|form|wizard|empty|error|modal|drawer|transient> \
  --viewport <mobile|desktop> \
  --task <task-name>          # optional; required for R17 pattern check when surface has tasks
  --pattern <pattern-name>    # optional; required for R17
  --spec <path-to-NAVIGATION.md>  # required for R16, R20, R21, R23, R24
```

Output: JSON report to stdout.

---

## Chrome rules (R1–R15 — unchanged from v1)

### R1 — Header sticky, not fixed

**Severity:** semantic
**Selector:** `<header>`
**Check:** Header class list contains `sticky top-0` or equivalent; does NOT contain `fixed top-0`.
**Fix:** Replace `fixed top-0` with `sticky top-0`. Fixed removes header from document flow.

### R2 — Header bg solid

**Severity:** cosmetic (R2a), cosmetic (R2b)
**Applies when:** surface != `public`
**Check:** Header class list does not contain `backdrop-blur-*` (R2a) or `bg-*/\d+` opacity modifier (R2b).
**Fix:** Use solid `bg-white` or `bg-[color:var(--color-surface)]`.

### R3 — No icon before client name

**Severity:** cosmetic
**Applies when:** surface in `SURFACE_AUTHENTICATED`
**Check:** No `<span class="material-symbols-*">`, `<svg>`, or `<img>` appears in the header before the first visible text node.
**Fix:** Remove the decorative element. Client name stands alone.

### R4 — Back not wrapped in breadcrumb nav

**Severity:** semantic
**Check:** No `<nav aria-label="Breadcrumb">` wrapping a single link on surfaces where breadcrumbs are forbidden (R4), OR wrapping a single link on surfaces where breadcrumbs ARE allowed (R4b — that's a back button, not breadcrumbs).
**Fix:** Unwrap. Use `<a>` or `<button>` with `aria-label` describing target.

### R5 — Back href is canonical

**Severity:** semantic
**Applies when:** archetype == `detail`
**Check:** Back-chevron anchor's href is not `#`, `javascript:*`, or `onclick="history.back()"`.
**Fix:** Hardcoded canonical URL (e.g., `/portal/invoices`).

### R6 — No global nav tabs in header

**Severity:** structural
**Applies when:** surface != `session-auth-admin` (admin has ratified exception)
**Check:** Header does not contain `role="tablist"`/`role="tab"` (R6); header does not contain 3+ short-text non-contact links (R6b — heuristic for nav-tab bar).
**Fix:** Remove. Secondary navigation below the header.

### R7 — No sticky-bottom outside dialog

**Severity:** structural
**Check:** No element with `fixed bottom-0` or `sticky bottom-0` outside a `<dialog>` or `role="dialog"`.
**Fix:** Remove. Primary action above the fold via document flow.

### R8 — No footer on auth surfaces

**Severity:** structural
**Applies when:** surface in `SURFACE_AUTHENTICATED`
**Check:** No `<footer>` element.
**Fix:** Remove.

### R9 — No real-face photos

**Severity:** structural
**Check:** No `<img>` with src matching `googleusercontent.com/aida[-/]`, `unsplash.com`, `pexels.com`, or similar stock-photo domains.
**Fix:** Replace with initials circle.

### R10 — No marketing CTAs on auth

**Severity:** structural
**Applies when:** surface in `SURFACE_AUTHENTICATED`
**Check:** No text match of "schedule a call", "book a demo", "get started", "learn more", "sign up now/today/free".
**Fix:** Remove. User is a customer.

### R11 — Header height matches viewport

**Severity:** cosmetic
**Check:** Header class contains `h-14` (mobile) or `h-16`/`md:h-16` (desktop).
**Fix:** Set correct height.

### R14 — Landmarks present

**Severity:** semantic
**Check:** `<header>` (R14a) and `<main>` (R14b) elements exist.
**Fix:** Add landmark elements.

### R15 — Skip-to-main wired

**Severity:** semantic
**Check:** First-body `<a class="sr-only focus:not-sr-only ..." href="#<id>">` exists (R15), AND `<main id="<id>">` matches (R15b).
**Fix:** Add skip link at body top; add matching `id` on `<main>`.

---

## IA rules (R16–R24 — NEW in v2)

### R16 — Reachability matrix enforcement

**Severity:** structural
**Layer:** IA
**Applies when:** archetype in `{dashboard, list, detail, form, wizard, empty, error, transient}`
**Requires:** `--spec` argument

**Check:** Parse NAVIGATION.md reachability matrix (Section 3.2). For each row where `From` matches the current surface and `Required = Yes`, verify the generated HTML emits an `<a href>` whose href matches the row's `To`.

- Exception: `Conditional` rows with a stated condition — the validator evaluates whether the condition holds (via heuristic: for `hasPendingInvoice`, check for an ActionCard structure; for `consultantAssigned`, check for a ConsultantBlock structure). If the condition holds, the link is required.
- Dashboards with no sibling lists in the matrix are exempt (but then the archetype is probably wrong — flag with R18).

**Fix:** Add `<a href="{To}">` to the surface's HTML. If the surface should not link to `{To}`, update the matrix.

**Rationale:** Catches the April 2026 ss-console failure mode. Before this rule, a dashboard could render polished chrome while orphan-ing every sibling list; the validator would pass. R16 guarantees that's impossible going forward.

### R17 — Pattern conformance

**Severity:** structural
**Layer:** Pattern
**Applies when:** `--pattern` is provided
**Requires:** `--spec` argument

**Check:** Look up the pattern's required elements in `pattern-catalog.md` (the validator has a built-in mapping). For the declared pattern, verify the HTML contains each required element.

Examples:

- `pattern=hub-and-spoke` → requires ≥1 entry-point element per sibling list (checked via R16)
- `pattern=master-detail` → requires back affordance on detail (checked via R5), row-click on list
- `pattern=sequential` → requires progress indicator ("Step N of M") + Previous + Next buttons
- `pattern=modal` → requires close button (X), Esc handler, click-outside handler, `aria-modal="true"`

**Fix:** Implement the pattern's required elements, or change the pattern declaration.

### R18 — No dead ends

**Severity:** structural
**Layer:** IA

**Check:** Surface has ≥1 navigation exit in rendered HTML. Exit = `<a href>`, `<button>` with handler (form submit, modal trigger), primary action, or back affordance.

- Exception: surfaces declared as `Terminal` in Section 3.4 of the spec.
- Error archetype surfaces must have ≥1 exit (Retry, Go home, Contact support).

**Fix:** Add a navigation exit (primary action, back affordance, or sibling link).

### R19 — Token-auth cold arrival

**Severity:** structural
**Layer:** IA
**Applies when:** surface == `token-auth`

**Check:** Generated HTML does not:

- Render "Welcome back", "Continue where you left off", or similar prior-session-assuming copy
- Reference user session state (pattern heuristic; limited to text scan)
- Display a chrome element requiring session (sign out button)

**Fix:** Render self-contained context. Token-auth is cold-arrival by definition.

### R20 — Content taxonomy adherence

**Severity:** semantic
**Layer:** IA
**Requires:** `--spec` argument

**Check:** Parse NAVIGATION.md Section 12 (Content taxonomy). For every entity with declared canonical label and forbidden synonyms:

- Scan rendered text (h1, h2, p, span, button, a, badge, div with class including "title"/"label"/"status").
- Flag forbidden synonyms in user-visible positions.
- Flag action verbs that don't match canonical form.
- Flag status labels that don't match declared format.

**Fix:** Replace with canonical label.

### R21 — State machine completeness

**Severity:** structural (empty/error), semantic (loading)
**Layer:** IA
**Requires:** `--spec` argument

**Check:** For each surface declared in Section 5 (state machine), the rendered HTML includes a `data-nav-state="<state>"` attribute on `<main>` OR the rendering clearly matches one of the declared states:

- Empty state: contains non-empty help message describing the empty condition
- Error state: contains recovery affordance (Retry, Go home, or Contact CTA)
- Loading state: preserves chrome landmarks even while data is unresolved

**Fix:** Render explicit state handling with appropriate copy and affordances.

### R22 — Heading hierarchy

**Severity:** semantic
**Layer:** A11y

**Check:**

- Exactly one `<h1>` per surface (R22a)
- No skipping levels (e.g., h1 → h3 without h2) (R22b)
- All major sections use `<h2>` (not `<div class="section-title">`)

**Fix:** Adjust heading levels to form a linear hierarchy.

### R23 — Search affordance

**Severity:** semantic
**Layer:** IA
**Applies when:** spec declares search on this surface
**Requires:** `--spec` argument

**Check:** If NAVIGATION.md declares search for the surface (inside cross-cutting "search strategy"), generated HTML contains `<input type="search">` or `<input ... role="searchbox">`.

**Fix:** Render search input per spec, OR remove search declaration from spec.

### R24 — Cross-surface context

**Severity:** structural
**Layer:** Pattern
**Applies when:** spec declares persistent-context pattern on this surface class
**Requires:** `--spec` argument

**Check:** If surface class uses persistent-context pattern (e.g., admin with selected client), every surface within the workspace scope renders the context indicator (header chip, breadcrumb prefix, etc.).

**Fix:** Render context indicator per spec.

---

## CI integration

Recommended CI step:

```yaml
- name: Validate navigation (chrome + IA)
  run: |
    for file in .stitch/designs/**/*.html; do
      python3 ~/.agents/skills/nav-spec/validate.py \
        --file "$file" \
        --surface "$(yq .surface "${file%.html}.meta.yaml")" \
        --archetype "$(yq .archetype "${file%.html}.meta.yaml")" \
        --viewport "$(yq .viewport "${file%.html}.meta.yaml")" \
        --task "$(yq .task "${file%.html}.meta.yaml")" \
        --pattern "$(yq .pattern "${file%.html}.meta.yaml")" \
        --spec .stitch/NAVIGATION.md
    done
```

For shipped `src/pages/**` files, the spec-version-aware Astro build hook runs the validator in a pre-build step and fails the build on structural violations.

---

## False-positive handling

The validator is deterministic. False positives happen when:

- The spec doesn't match shipped conventions (R16 wrong because the matrix is wrong)
- A pattern's required elements differ from the catalog default (R17 wrong because the surface specializes)
- A taxonomy term has legitimate polysemy (R20 wrong because "contract" appeared in an unrelated context)

Remediation:

- Update the spec first, not the validator. The validator reflects the spec.
- If the catalog default is wrong, update `pattern-catalog.md` (cite a source for the change).
- For polysemous taxonomy terms, narrow the rule to specific DOM positions (buttons, headings) rather than broad text scans.

Never disable a rule to pass a generation. The failing rule is telling you something.
