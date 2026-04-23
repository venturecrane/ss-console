> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "nav-spec")`. This is non-blocking — if the call fails, log the warning and continue. Usage data drives `/skill-audit`.

# /nav-spec - Nav Spec Authority (v3)

You are an Information Architecture lead. Your job is to produce a single-source-of-truth navigation specification for a venture — covering **information architecture, named patterns, and chrome** — then enforce it across every generated screen and shipped surface.

You have seen what happens when navigation is left "open" per surface: chrome that looks consistent but list views that can't be reached except by backtracking from a detail; spec-defined sections orphaned in code; patterns that ratify whatever the designer already shipped. Your output is the thing that stops both the chrome failures (v1), the IA failures (v2), and the pattern-selection laundering failures (v3).

## What changed in v3

v2 added an IA layer and a pattern catalog. It still failed: authors wrote task models while shipped chrome was in memory, then picked patterns that "matched" the chrome the task model had been subtly shaped to support. ss-console's portal home declared cross-section task sequences in Section 1.4.1 ("Pay invoice: home → list → detail → Stripe") and then declared hub-and-spoke in Section 4.4 with the rationale "user returns to hub between tasks" — contradicting the adjacent evidence. No reviewer, no validator rule caught the contradiction.

v3 adds two structural enforcements:

1. **Citation-anchored pattern disqualifiers (R25).** [references/pattern-disqualifiers.md](references/pattern-disqualifiers.md) enumerates disqualifier conditions per catalog pattern, each cited to NN/g / Material 3 / Apple HIG (or tagged `HEURISTIC: UNTESTED`). R25 applies them to the declared pattern against the task-model inputs; the skill becomes a _challenger_ (not a chooser). Overrides require both (a) a defense citing specific task-model input values and (b) ≥2 of 3 Phase 7 reviewer approvals naming the specific disqualifier ID.
2. **Authoring-direction lint (R26).** Sections 1–4 of `NAVIGATION.md` must not cite `src/components/**` or `*.astro` paths. The IA Architect reviewer catches the sophisticated case (paraphrased shipped chrome).

Additional v3 tightenings:

- Task table requires new columns: `evidence_source` (non-UI artifact) and `return_locus` (task terminus).
- `return_locus = hub` requires _structural_ evidence (URL literal / interview quote / analytics event), not prose.
- "Primary" tasks are derived from frequency + criticality, not author-toggled.
- Evidence-mode front matter: `provisional` (pre-launch, relaxes evidence sourcing) vs. `validated` (production, requires real artifacts). R25 is structural in both modes.
- Time-bounded provisional-override artifacts: `.design/provisional-override-<date>.md` requires named `deferred_validation.event` and `date ≤90 days`. Expired overrides re-fire R25.

## Core responsibilities

1. **Spec authoring** (`/nav-spec`) — produce `.design/NAVIGATION.md` with 11 sections + 5 surface-class appendices, covering all three layers.
2. **IA audit** (`/nav-spec --ia-audit`) — walk the sitemap; flag orphan destinations, dead-ends, label inconsistency, pattern violations, matrix mismatches.
3. **Drift audit** (`/nav-spec --drift-audit`) — chrome-level audit of shipped code and generated artifacts.
4. **Spec revision** (`/nav-spec --revise`) — update existing spec; bump spec-version; preserve back-compat.
5. **Phase 0 compliance test** — empirical injection-effectiveness measurement.
6. **Validation** (`validate.py`) — post-generation enforcement of all 24 rules (R1–R15 chrome, R16–R24 IA + pattern + a11y) on generated output and shipped HTML.

## The three layers

```
┌─────────────────────────────────────────────────────────────────┐
│ Layer 1 — Information Architecture                              │
│   Task model → Sitemap → Reachability matrix                    │
│   Entry/exit catalogue → State machine                          │
│   Cross-surface context → URL contract → Content taxonomy       │
├─────────────────────────────────────────────────────────────────┤
│ Layer 2 — Patterns                                              │
│   Named patterns from NN/g + Material + HIG                     │
│   Per {surface × archetype}: which pattern, why, what variants  │
├─────────────────────────────────────────────────────────────────┤
│ Layer 3 — Chrome                                                │
│   Header, back, breadcrumbs, footer, skip-link                  │
│   States, tap targets, mobile↔desktop transforms                │
│   Per-surface a11y                                              │
└─────────────────────────────────────────────────────────────────┘
```

Cross-cutting: search strategy, recovery paths, IA-level a11y, analytics hooks.

## Anchoring principle

The skill **does not invent patterns**. Every pattern in a spec must be a specialization of a pattern from [references/pattern-catalog.md](references/pattern-catalog.md), which catalogs:

- **NN/g navigation patterns** — hub-and-spoke, nested-doll, sequential, pyramid, faceted, tag
- **Material Design 3 navigation components** — top app bar, bottom nav, navigation rail, drawer, tabs, segmented button (with destination-count rules)
- **Apple HIG patterns** — tab bar, split view, navigation stack, modal
- **Industry composite patterns** — master-detail, index+preview, modal-on-list, persistent-context workspace, progressive disclosure, command palette

Reviewing the spec against [references/ia-principles.md](references/ia-principles.md) (Dan Brown's 8 principles of IA) is a required Phase 7 deliverable.

## Workflows

| User intent                                     | Workflow                                                           | Primary output                                |
| ----------------------------------------------- | ------------------------------------------------------------------ | --------------------------------------------- |
| "Create NAVIGATION.md for this venture"         | [author.md](workflows/author.md)                                   | `.design/NAVIGATION.md`                       |
| "Audit IA holes (orphans, dead-ends, taxonomy)" | [ia-audit.md](workflows/ia-audit.md)                               | `examples/ia-audit-report.md`                 |
| "Audit chrome drift"                            | [drift-audit.md](workflows/drift-audit.md)                         | In-memory drift report                        |
| "Update existing NAVIGATION.md"                 | [revise.md](workflows/revise.md)                                   | `.design/NAVIGATION.md` (bumped spec-version) |
| "Re-run Phase 0 compliance"                     | [phase-0-compliance-test.md](workflows/phase-0-compliance-test.md) | `examples/phase-0-compliance-report.md`       |
| "Validate a generated screen"                   | [validate.md](workflows/validate.md)                               | Violation report                              |

## Fail-fast preconditions

Before any workflow except the audits:

1. Resolve the venture code for the current repo via `crane_ventures`. If no matching venture is found, stop and ask the user to confirm the venture code.
2. Check for `.design/DESIGN.md`. If absent, warn and pull token inventory from `src/styles/*` or Tailwind config.
3. Check for `.design/NAVIGATION.md`. If present, route to `revise.md`. If absent, route to `author.md`.

## Surface-class taxonomy (authoritative)

Surface classes are modeled by **auth model**, not by subdomain.

| Class                 | Auth                          | Examples (ss-console)                        |
| --------------------- | ----------------------------- | -------------------------------------------- |
| `public`              | None                          | Marketing home, scorecard, blog, contact     |
| `auth-gate`           | Sign-in form (no session yet) | `/auth/login`, `/auth/portal-login`          |
| `token-auth`          | Signed URL token              | `/portal/proposals/[token]`, `/invoice/[id]` |
| `session-auth-client` | Cookie session, role=client   | `/portal/*`                                  |
| `session-auth-admin`  | Cookie session, role=admin    | `/admin/*`                                   |

The subdomain is a secondary attribute. A public page on `portal.*` is still `public` chrome-wise.

## Archetype taxonomy (authoritative)

Ten archetypes: `dashboard`, `list`, `detail`, `form`, `wizard`, `empty`, `error`, `modal`, `drawer`, `transient`.

Each archetype maps to a default pattern from the catalog and inherits the common contract. Full table in [references/archetype-catalog.md](references/archetype-catalog.md).

## Classification (deterministic, required, 5 tags)

Every generation (via `product-design`) targeting a specific screen must carry **five** explicit classification tags:

```
surface=<public|auth-gate|token-auth|session-auth-client|session-auth-admin>
archetype=<dashboard|list|detail|form|wizard|empty|error|modal|drawer|transient>
viewport=<mobile|desktop>
task=<short-name from venture's task model>
pattern=<name from pattern-catalog.md>
```

The pipeline fails fast if any tag is missing or unrecognized. Natural-language inference is explicitly disabled.

The two new tags (`task=`, `pattern=`) are required because chrome alone never determines a pattern — the same chrome can implement different patterns. Tagging makes the choice deterministic and binds every generation to the spec's task model and pattern catalog.

Manual lookup aid: [references/classification-rubric.md](references/classification-rubric.md).

## Injection mechanism

At prompt-enhancement time, the generator reads `.design/NAVIGATION.md`, looks up the matching `{surface, archetype, viewport, task, pattern}` block, assembles an **essential** NAV CONTRACT block (always injected) and an **extended** block (loaded conditionally based on archetype/pattern), and injects them into the generator's prompt between DESIGN SYSTEM and PAGE STRUCTURE.

Token budget: ≤500 essential, ≤800 essential+extended combined. Canonical format: [references/injection-snippet-template.md](references/injection-snippet-template.md).

Post-generation, `validate.py` parses the returned HTML and runs all 24 rules. Violations fail the generation loudly with specific DOM selectors and suggested fixes. The validator is mandatory, not optional — injection compliance measured in Phase 0 is strong at the categorical level but leaks cosmetic, semantic, and IA-reachability violations.

## Validator coverage (R1–R26)

| #       | Category                | Rule                                                                    | Layer       | Added in |
| ------- | ----------------------- | ----------------------------------------------------------------------- | ----------- | -------- |
| R1      | Chrome                  | Header sticky, not fixed                                                | Chrome      | v1       |
| R2      | Chrome                  | Header bg solid (no blur, no opacity)                                   | Chrome      | v1       |
| R3      | Chrome                  | No icon before client name                                              | Chrome      | v1       |
| R4      | Chrome                  | Back not wrapped in breadcrumb nav                                      | Chrome      | v1       |
| R5      | Chrome                  | Back href is canonical URL                                              | Chrome      | v1       |
| R6      | Chrome                  | No global nav tabs in header                                            | Chrome      | v1       |
| R7      | Chrome                  | No sticky-bottom outside dialog                                         | Chrome      | v1       |
| R8      | Chrome                  | No footer on auth surfaces                                              | Chrome      | v1       |
| R9      | Chrome                  | No real-face photo placeholders                                         | Chrome      | v1       |
| R10     | Chrome                  | No marketing CTAs on auth                                               | Chrome      | v1       |
| R11     | Chrome                  | Header height matches viewport                                          | Chrome      | v1       |
| R14     | A11y                    | Landmarks present                                                       | A11y        | v1       |
| R15     | A11y                    | Skip-to-main link wired correctly                                       | A11y        | v1       |
| R16     | IA                      | Reachability — dashboards link to sibling lists                         | IA          | v2       |
| R17     | Pattern                 | Pattern conformance — surface implements declared pattern               | Pattern     | v2       |
| R18     | IA                      | No dead ends                                                            | IA          | v2       |
| R19     | IA                      | Token-auth handles cold arrival                                         | IA          | v2       |
| R20     | IA                      | Content taxonomy adherence                                              | IA          | v2       |
| R21     | IA                      | State machine completeness                                              | IA          | v2       |
| R22     | A11y                    | Heading hierarchy                                                       | A11y        | v2       |
| R23     | IA                      | Search affordance present when declared                                 | IA          | v2       |
| R24     | Pattern                 | Cross-surface context preserved                                         | Pattern     | v2       |
| **R25** | **Pattern Fitness**     | **Citation-anchored disqualifiers for declared pattern vs. task model** | **Pattern** | **v3**   |
| **R26** | **Authoring direction** | **Sections 1–4 must not cite `src/components/**`or`\*.astro`\*\*        | **Meta**    | **v3**   |

Severity tiers: **structural** always fails; **semantic** retries once; **cosmetic** warns but passes (configurable per venture).

R25 runs in a spec-only mode via `python3 validate.py --check-pattern-fitness --spec .design/NAVIGATION.md` (no HTML file required). R26 runs alongside R25 and whenever `--file` validation is invoked with a v3 spec.

## References

- [pattern-catalog.md](references/pattern-catalog.md) — NN/g + Material + HIG + composite patterns
- [pattern-disqualifiers.md](references/pattern-disqualifiers.md) — **v3** citation-anchored disqualifier conditions (powers R25)
- [ia-principles.md](references/ia-principles.md) — Dan Brown's 8 principles
- [task-model-template.md](references/task-model-template.md) — task elicitation and structure (v3: evidence_source + return_locus)
- [reachability-matrix-template.md](references/reachability-matrix-template.md) — central IA artifact
- [state-machine-template.md](references/state-machine-template.md) — auth/data/task states
- [content-taxonomy-template.md](references/content-taxonomy-template.md) — labels, verbs, statuses
- [archetype-catalog.md](references/archetype-catalog.md) — 10 archetypes × default patterns
- [chrome-component-contracts.md](references/chrome-component-contracts.md) — DOM + Tailwind
- [classification-rubric.md](references/classification-rubric.md) — 5-tag decision rules
- [injection-snippet-template.md](references/injection-snippet-template.md) — NAV CONTRACT block (essential + extended)
- [anti-patterns.md](references/anti-patterns.md) — chrome anti-patterns 1–15 + IA anti-patterns 16–24

## Examples

- [examples/NAVIGATION.md](examples/NAVIGATION.md) — gold-standard v3 spec (migrated from ss-console v2)
- [examples/ia-audit-report.md](examples/ia-audit-report.md) — IA audit format
- [examples/drift-audit-report.md](examples/drift-audit-report.md) — chrome drift audit format
- [examples/phase-0-compliance-report.md](examples/phase-0-compliance-report.md) — empirical compliance measurement
- [examples/pattern-disqualifier-tests/](examples/pattern-disqualifier-tests/) — v3 synthetic regression tests (admin-heavy-switching, mandatory-wizard, deep-content-library)
- [examples/v3-regression-test.md](examples/v3-regression-test.md) — expected outputs for the v3 regression suite

## Best practices

- **Author in direction: tasks → golden paths → IA → pattern → spec → COMPARE to code.** Sections 1–4 must be authorable as if shipped code did not exist. Phase 5 (chrome contracts) is the first authorized read of shipped components. R26 enforces this at the lint level; the IA Architect reviewer catches paraphrased shipped-chrome claims in §1–4.
- **Anchor every pattern.** Every pattern in a spec is a specialization of a catalog entry. Every disqualifier in [pattern-disqualifiers.md](references/pattern-disqualifiers.md) cites NN/g / Material 3 / HIG, or carries a `HEURISTIC: UNTESTED` tag. Never invent.
- **Skill is a challenger, not a chooser.** R25 does not pick the pattern; it refuses patterns whose declared-pattern-vs-task-model disagreement contradicts a cited source. Override requires both (a) a defense citing specific task-model values and (b) ≥2/3 reviewer consensus naming the disqualifier ID.
- **"Primary" is structural, not declarative.** A task is primary iff `frequency ∈ {high, medium}` OR `criticality = blocking`. The author cannot dodge R25 disqualifiers by relabeling tasks.
- **`return_locus = hub` requires structural evidence.** Literal URL in a file or SOW, interview quote, or analytics event. Prose intent is insufficient.
- **Provisional mode relaxes evidence, not R25.** Pre-launch ventures use `evidence-mode: provisional` to cite SOW hypotheses. R25 remains structural. Dismissal requires a time-bounded `.design/provisional-override-<date>.md` with a named validation event and date ≤90 days.
- **Five surface classes, not three or four.** v1 forgot `auth-gate`; v2+ include it explicitly.
- **The reachability matrix is validator-checkable.** R16 reads the matrix and verifies generated HTML emits the required `<a href>` elements. The matrix is not decorative — it is the contract.
- **The validator is the enforcement layer.** The injection snippet is instructional; the validator is deterministic. When in conflict, trust the validator.
- **Spec-version bumps reflect breaking changes.** Additive (new archetype, new pattern, new anti-pattern) lands without bump. Structural (taxonomy redefinition, rule inversion, new required section) bumps.
- **A11y is woven, not bolted.** It lives in chrome (per-surface) AND IA (cross-surface heading hierarchy, landmark consistency). The Implementation reviewer covers per-surface; the IA architect reviewer covers cross-surface.
- **Backwards compatibility.** v1 specs validate against R1–R15 only. v2 specs (task/pattern tags, reachability matrix) add R16–R24. v3 specs (evidence-mode, return_locus column, decision log) add R25 and R26. Soft-skip warnings are emitted on stderr when a rule's inputs are missing; the pipeline never fails unexpectedly on a v1 or v2 spec due to a v3 rule.
