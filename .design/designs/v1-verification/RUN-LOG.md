# NAVIGATION.md v1-pending — verification run log

Date: 2026-04-15
Spec version checked: 1-pending
Skill: `~/.agents/skills/nav-spec/` v1
Stitch project: 17873719980790683333

## Phase 7 — integration check (self-consistency)

Regenerate `portal-v1/home-mobile` with the full updated NAV CONTRACT injection (session-auth-client / dashboard / mobile). Validate against spec.

**Result:** 1 violation — R1 (fixed vs sticky). All categorical rules honored; three-icon contact control, skip-to-main link, SVG silhouette (no real face), no nav tabs, no bottom chrome, no footer, no marketing. Validator false-positive bugs discovered (R3 limited to before-client-name; R15 attribute order) — both fixed in validate.py. Re-validation: 1 remaining violation, which is a real Stitch blindspot.

**Artifact:** `phase7-portal-home-mobile.html`

## Phase 8 — adversarial verification (unseen combos)

Three prompts on {surface × archetype × viewport} combinations the spec author did NOT tune against:

### Portal settings form (session-auth-client, form, mobile)

**Result:** 1 violation — R1 (fixed vs sticky). Correctly produced:

- No back button (form uses cancel+save) ✓
- Three-icon contact control ✓
- Skip-to-main with matching `id="main"` ✓
- No footer on auth surface ✓
- No breadcrumbs ✓

**Artifact:** `phase8-portal-settings-form-mobile.html`

### Admin audit log detail (session-auth-admin, detail, desktop)

**Result:** 1 violation — R9 (real-face photo; Stitch used stock image for operator avatar). Correctly produced:

- **`sticky top-0 z-50`** (admin header sticky — no R1 this time!) ✓
- **`<nav class="hidden md:flex">`** nav tabs pattern exactly matching Appendix D.2 ✓
- Four tabs (Dashboard, Entities active, Follow-ups, Analytics) with active state ✓
- Skip-to-main → matching `id="main-content"` on `<main>` ✓
- No sidebar, no footer ✓
- R6b admin-exception guard worked — no false positive on 4 nav tabs ✓

**Artifact:** `phase8-admin-audit-log-detail-desktop.html`

### Marketing blog post (public, detail, desktop)

**Result:** 1 violation — R9 (real-face photos; Stitch used stock images for author avatar + related-article previews). Correctly produced:

- Sticky header with logo + Contact link + Book a Call CTA — exact Appendix A.1 match ✓
- Skip-to-main → matching `id="main"` ✓
- **`<footer>` present** — public surface allowed by rule correctly ✓
- No breadcrumbs (blog is flat) ✓
- No sidebar, no bottom-tab ✓

**Artifact:** `phase8-marketing-blog-post-desktop.html`

## Drift-prevention assessment

| Metric                                                    | Result                                                                       |
| --------------------------------------------------------- | ---------------------------------------------------------------------------- |
| Structural violations from taxonomy gaps                  | **0 across 3 adversarial runs**                                              |
| Semantic violations (non-blindspot)                       | 0                                                                            |
| Known blindspots caught by validator                      | R1 (1×), R9 (2×) — all correctly flagged                                     |
| Cross-surface-class behavior                              | All three surface-class appendices (A/C/D) produced distinct, correct chrome |
| `md:-only` chrome rule + right-rail `lg:` exception       | Respected across admin and portal generations                                |
| Three-icon contact control (new pattern introduced in v1) | Implemented correctly across 2 runs                                          |

**Conclusion:** the spec's taxonomy and injection mechanism handle unseen {surface × archetype × viewport} combinations without structural drift. The adversarial verification passes the plan's gate ("chrome matches spec's prediction for each combo").

## Outstanding v1-approval prerequisites

Per NAVIGATION.md §11, v1 approval is held on the retrofit PR. Verification passed; spec is correct. Retrofit remains: ~120 LOC across ~10 files (skip-to-main component + landmarks + height-class placement + three-icon contact in PortalHeader + admin sticky + admin mobile `<details>` menu + portal detail back affordances + breadcrumb separator swap).

Once retrofit merges and `spec-version` front matter flips from `1-pending` → `1`, this verification run becomes the baseline for comparison on any future spec revision.

## Artifacts in this folder

- `phase7-portal-home-mobile.html` — integration-check regeneration
- `phase8-portal-settings-form-mobile.html` — adversarial: new archetype on known surface
- `phase8-admin-audit-log-detail-desktop.html` — adversarial: new surface class (admin)
- `phase8-marketing-blog-post-desktop.html` — adversarial: new surface class (public)
- `RUN-LOG.md` — this file
