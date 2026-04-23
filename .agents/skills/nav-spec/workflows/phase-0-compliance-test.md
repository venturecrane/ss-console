---
description: Empirical measurement of whether prompt injection is honored by the generator. Must run before authoring the first NAVIGATION.md for a venture, or when the generator's underlying model version changes.
---

# Phase 0 — Injection compliance test

Measures compliance empirically. Informs whether injection alone is sufficient (rare) or whether a validator is required (always, given measurements to date).

## Why this exists

Per the critique in the originating plan file: LLM design generators have strong training priors. A constraint block is a hint, not a guarantee. Before committing to injection-first enforcement, measure reality. Skip this step and the first NAVIGATION.md ships to production on faith.

## Protocol

### Step 1 — Draft a minimal injection snippet

A short, high-constraint version of the canonical NAV CONTRACT block. Target 400–500 tokens. Focus on the forbidden list, state hex values, and the "this block wins" override. See `references/injection-snippet-template.md` for the template; trim appendix-specific content for Phase 0.

### Step 2 — Pick three representative prompts

Pick one prompt per major surface/archetype combination the venture actually uses. For ss-console the canonical triad is:

- `home-mobile` (portal home dashboard, 390×844)
- `invoice-mobile` (portal invoice detail, 390×844)
- `proposal-desktop` (portal proposal detail, 1280)

Each prompt should include DESIGN SYSTEM and PAGE STRUCTURE sections, but NOT a nav spec — leave nav "open" to establish the baseline.

### Step 3 — Fire 6 generations in parallel

Three base prompts × two variants (baseline vs injected) = 6 runs. Use the `product-design` skill to drive the generator.

Python runner template at `/tmp/phase0-runner.py` (from the ss-console Phase 0 run). Adapt prompts as needed.

### Step 4 — Collect HTML for all six

Extract the generated HTML from each run and write to `/tmp/phase0/<label>.html`.

### Step 5 — Compliance scoring

Two scoring passes:

**Major-category compliance** — did injection prevent categorically-forbidden chrome? For each injected run, score 0 or 1 for each of:

- No global nav tabs / menu links in header
- No sidebar / hamburger / drawer
- No logo / brand wordmark in header
- No breadcrumb trail
- No bottom-tab nav
- No sticky bottom action bar
- No footer / copyright / legal links
- No marketing CTAs (book, schedule, etc.)
- No testimonials / pull quotes
- No hero imagery
- No real-face photo placeholder (check for `googleusercontent.com/aida/` or similar)

Total: 11 categories. Score = categorical compliance percentage.

**Strict compliance** — for each injected run, score against every rule in the injection snippet (header sticky-not-fixed, border color exact, 56/64px height, client-name-only, back button wrapping semantics, etc.). Record specific violations.

### Step 6 — Decision

| Outcome               | Architecture                                                                                                                |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| Major-category ≥ 95%  | Injection-first + **required** lightweight validator                                                                        |
| Major-category 80–95% | Injection-first + **required** strict validator (this is the common case)                                                   |
| Major-category < 80%  | Validator-first with minimal injection (~150 tokens forbidden-list only); injection is a hint, validator is the enforcement |

The validator is always present. The scoring determines how much of the work injection versus validator does.

### Step 7 — Write the report

Save to `examples/phase-0-compliance-report.md` (copy from `/tmp/phase0/COMPLIANCE-REPORT.md`). Include:

- Test matrix and scores
- Specific violations from strict pass → become validator rules
- Recommended architecture decision
- Date (for re-runs when the generator or spec updates)

### Step 8 — Re-run cadence

Re-run Phase 0 when:

- The generator's underlying model version changes
- The injection snippet structure is substantively revised
- Validator rules are under-catching violations that feel like they should be catchable via injection

## ss-console reference run

See `examples/phase-0-compliance-report.md` for the 2026-04-15 ss-console measurement:

- Major-category: 97%
- Strict: ~87%
- Decision: injection-first + required validator covering 5 specific residual violation types
