---
description: Produce `.stitch/NAVIGATION.md` from scratch for a venture. Twelve phases. Authors tasks → IA → patterns → chrome top-down; compares to shipped code only AFTER the spec is drafted. v3 adds the evidence-mode gate, structural return_locus columns, and the R25 Pattern Fitness + R26 authoring-direction checks.
---

# Author NAVIGATION.md (v3)

Full-fidelity authoring workflow. Run this once per venture; thereafter use [revise.md](revise.md) for updates, [ia-audit.md](ia-audit.md) for IA checks, and [drift-audit.md](drift-audit.md) for chrome drift checks.

v1's nine-phase workflow started with chrome. v2 inverted the order (tasks first). v3 closes the v2 loophole where the author could write the task model while shipped chrome was in memory and then "deterministically" confirm the incumbent pattern. The v3 rule: **Sections 1–4 must be authorable before shipped code existed.** R26 enforces this at the lint level; Phase 4 adds an algorithmic pattern-fitness check (R25) against the task model; Phase 7 reviewers confirm the evidence is non-UI-sourced.

## Authoring direction (v3)

| Phase                                           | May read shipped code?                                                                                                                                         |
| ----------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1 (Intake)                                      | Route paths from `src/pages/` FILENAMES only. No `.astro` body reads.                                                                                          |
| 2 (Task model)                                  | NO. Source from contracts, tickets, interviews, analytics. UI-sourced evidence is forbidden — use `evidence-mode: provisional` if that's all that's available. |
| 3 (IA model)                                    | NO. Derive matrix + state machine from task model, not from shipped chrome.                                                                                    |
| 4 (Pattern selection)                           | NO. Run R25 algorithmically against task-model inputs.                                                                                                         |
| 5 (Chrome contracts)                            | YES. Chrome must align with shipped components; diff to shipped code is expected.                                                                              |
| 6 (Save draft v1)                               | YES. Artifact on disk.                                                                                                                                         |
| 7 (Parallel reviewers)                          | YES for Implementation reviewer; NO for IA architect when evaluating §1–4.                                                                                     |
| 8+ (Decision rounds, integration, verification) | YES.                                                                                                                                                           |

R26 is the lint that enforces this; it runs on every save and fires if §1–4 cite `src/components/**` or `*.astro` paths. The IA Architect reviewer catches the sophisticated case (paraphrased shipped chrome without a file citation).

---

## Phase 1 — Intake

Gather context before drafting any layer. Read-rules per the authoring-direction table above.

1. Resolve venture code from the current repo via `crane_ventures`. Read `stitchProjectId`. If null, stop — see SKILL.md fail-fast.
2. **Enumerate route paths from `src/pages/` FILENAMES only** (no `.astro` body reads). Group by surface class (use the 5-class taxonomy — don't fold `auth-gate` into `public`).
3. Check `.stitch/DESIGN.md`. If absent, warn; pull token inventory from `src/styles/*` or Tailwind config.
4. Load the venture's `CLAUDE.md` for voice, business-model context, forbidden patterns.
5. Reference Phase 0 compliance report if present at `examples/phase-0-compliance-report.md`. If not, run `phase-0-compliance-test.md` before proceeding.
6. **Detect evidence mode.** Read the venture's `CLAUDE.md` and any status documents. If the venture is pre-launch (no shipped product, no real users), default `evidence-mode: provisional`. If the venture has live users and collects analytics / support tickets, default `evidence-mode: validated`. The author may override.

Display an **Intake Summary** table:

| Field                     | Value                                                                  |
| ------------------------- | ---------------------------------------------------------------------- |
| Venture                   | code + name                                                            |
| Stitch project            | ID                                                                     |
| Surface classes present   | list (all 5 classes, check each)                                       |
| Routes per surface class  | path inventory (FILENAMES only, no body reads)                         |
| Tokens source             | path                                                                   |
| DESIGN.md status          | present / absent                                                       |
| Phase 0 compliance        | % categorical / % strict, or "not run"                                 |
| Evidence mode (v3)        | provisional \| validated — with detection reasoning                    |
| Shipped chrome components | list paths (for Phase 5 comparison only, NOT for Phases 2–4 authoring) |

Ask: **"Does this capture the intake? Anything to correct before authoring?"** Wait for confirmation.

---

## Phase 2 — Task model (IA layer 1; v3 evidence-sourcing rules)

Before any IA structure, author the task model per surface class. v3 tightens v2's elicitation rules: every primary task requires a cited evidence artifact, and every task declares `return_locus`. "I looked at the portal and saw X" is forbidden as evidence.

For each surface class present in the venture, fill the v3 task table per [references/task-model-template.md](../references/task-model-template.md) with the required columns:

| Task | Frequency | Criticality | Evidence source | return_locus | return_locus_evidence |

**Required columns:**

- `Frequency` ∈ {high, medium, low, variable}
- `Criticality` ∈ {blocking, high, medium, low}
- `Evidence source` — a cited non-UI artifact. Accepted: `SOW §N.M`, `ticket <ID>`, `analytics <event>`, `interview <path>§<n>`, or `provisional:<type>` (requires evidence-mode = provisional).
- `return_locus` ∈ {hub, last-visited-surface, external, new-destination}
- `return_locus_evidence` — structural evidence for `return_locus=hub` (literal URL, interview quote, or analytics event). Prose citation is sufficient only for non-hub return_loci.

**Elicitation order (v3):**

- Read `CLAUDE.md` and `docs/**` for product intent and business-model context.
- Read any user-interview transcripts, support tickets, or analytics dumps.
- Read contracts (SOW, engagement letters) for required deliverables and task cadence.
- **Do NOT read `src/components/**`or`.astro` bodies to elicit tasks.\*\* The filename inventory from Phase 1 is enough; body reads bias the task model toward incumbent chrome.
- For pre-launch ventures with no user research: draft from SOW scope and product goals, cite each as `provisional:SOW-scope` or `provisional:design-hypothesis`, and declare `evidence-mode: provisional` in the front matter.

**"Primary" is derived, not author-declared.** A task is primary iff `frequency ∈ {high, medium}` OR `criticality = blocking`. The validator computes this mechanically; the author cannot toggle.

**Output.** Section 1 of `NAVIGATION.md`. Show the draft and ask: "Do these tasks match how you think about this product? Is any cited evidence missing or weak?"

---

## Phase 3 — IA model (NEW — IA layers 2–7)

With tasks in hand, author the IA:

1. **Sitemap** — route inventory grouped by surface class (Section 2 of spec)
2. **Auth boundary table** — which routes require which auth (Section 2b)
3. **Reachability matrix** — the central IA artifact (Section 3)
   - Use [references/reachability-matrix-template.md](../references/reachability-matrix-template.md)
   - Every surface appears as From ≥1 time and To ≥1 time (exceptions: entry-only, terminal)
   - Every dashboard has rows to every sibling list
   - Every detail has a row back to its parent list
4. **Entry/exit catalogue** — how users arrive (email, SMS, bookmark, direct URL) and where each surface exits
5. **State persistence** — what persists across navigation (filters, scroll, selection, dirty forms)
6. **Cross-surface context** — does a selected entity (client, engagement) persist? How is it indicated?
7. **URL/routing contract** — canonical URL structure, redirect strategy for renamed routes, deep-link durability

**Output:** Sections 2, 3 of `NAVIGATION.md`. Show the reachability matrix as a rendered table. Ask: "Review this matrix — any missing rows (orphan destinations) or wrong rows (dead-ends, wrong mechanisms)?"

---

## Phase 4 — Pattern selection with v3 Pattern Fitness (R25)

For every `{surface class × archetype}` combination, author a decision entry in Section 4. The v3 format is structured: chosen pattern, runner-up pattern, defense citing specific task-model input values, and (if a disqualifier is overridden) reviewer-approval citations.

### 4a. Propose a pattern

Pick a candidate pattern from [references/pattern-catalog.md](../references/pattern-catalog.md). Record:

- Pattern name (verbatim from catalog)
- Source (NN/g / Material / HIG / composite)
- Runner-up pattern (the second-best catalog entry for this surface)
- Defense citing specific task-model values (see 4c below)
- Required elements (copied from catalog)

### 4b. Run the Pattern Fitness check

Run:

```
python3 ~/.agents/skills/nav-spec/validate.py --check-pattern-fitness \
    --spec .stitch/NAVIGATION.md
```

This invokes R25 (disqualifier check) and R26 (authoring-direction lint). Expected outcomes:

- **R25 passes:** proceed.
- **R25 fires:** the chosen pattern is disqualified per a citation-anchored rule in [references/pattern-disqualifiers.md](../references/pattern-disqualifiers.md). The validator names the disqualifier ID (e.g., D1), the citation, and the surviving patterns. Resolve per 4c.
- **R26 fires:** Sections 1–4 cite shipped code. Edit out the citation (re-source the claim from contracts / tickets / interviews / analytics) or move the citation to Section 6+ (chrome contracts).

### 4c. Defending an override

If R25 fires but the author believes the chosen pattern is correct, the defense in Section 4's decision block must:

1. **Cite specific input values** that would make the disqualifier not fire. Example for D1: "return_locus for pay-invoice is actually `hub` (not `external`) because Stripe redirects back to /portal after payment, cited at `src/pages/api/stripe/return.ts:12` (structural evidence type 1)."
2. **Use structural evidence for `return_locus=hub` claims** (URL literal, interview quote, or analytics event). Prose intent is insufficient.
3. **Obtain ≥2 of 3 reviewer approvals** in Phase 7, each citing the specific disqualifier ID being overridden (e.g., "I approve override of D1 because the cited redirect path satisfies NN/g's hub-return requirement").

If either defense or reviewer consensus is missing, R25 continues to fire and the decision cannot ship. The author's other options:

- **Switch to a surviving pattern** per the validator's output.
- **In `evidence-mode: provisional` only:** file `.stitch/provisional-override-<YYYY-MM-DD>.md` per the schema in [references/task-model-template.md](../references/task-model-template.md). Requires a named `deferred_validation.event` and `date ≤ 90 days` from filing.

### 4d. Compose patterns

Patterns compose. For example, `session-auth-client × dashboard` might declare `persistent-tabs` (primary) + `dominant-action variant` (conditional ActionCard) + `recent-activity variant` (timeline feed). Document each; R25 evaluates only the primary pattern.

### 4e. State machine

Also in this phase, author the **Navigation state machine** (Section 5): auth states, data states, task states per surface class using [references/state-machine-template.md](../references/state-machine-template.md).

**Output.** Section 4 (Patterns with decision entries) + Section 5 (State machine). Show the pattern decisions and ask: **"Are the cited task-model input values correct? (Not: does the pattern look right.)"** The challenge should be to the _inputs_, not the _output_ — the algorithm's job is to make the output deterministic given the inputs.

---

## Phase 5 — Chrome contracts (Layer 3, unchanged from v1 in scope)

Now, and only now, author chrome:

1. **Chrome component contracts** (Section 6): header, back, breadcrumbs, footer, skip-link — DOM + Tailwind per [references/chrome-component-contracts.md](../references/chrome-component-contracts.md)
2. **Mobile↔desktop transforms** (Section 7): per surface class, what changes at the 768px breakpoint
3. **State conventions** (Section 8): active, hover, focus, disabled — hex values pulled from DESIGN.md
4. **Transition contracts** (Section 9): back-target canonical URLs, modal close rules, cross-auth-boundary = full reload
5. **Anti-patterns** (Section 10): chrome and IA anti-patterns per surface class, pulled from [references/anti-patterns.md](../references/anti-patterns.md)
6. **A11y floor** (Section 11): landmarks, skip-link, aria-current, focus rings with hex, semantic heading hierarchy
7. **Content taxonomy** (Section 12): object names, action verbs, status labels, time/numeric formats using [references/content-taxonomy-template.md](../references/content-taxonomy-template.md)

**Output:** Sections 6–12 of `NAVIGATION.md`. Plus the five surface-class appendices (A public, B auth-gate, C token-auth, D session-auth-client, E session-auth-admin) each stating deltas from the parent spec.

---

## Phase 6 — Save draft v1

Save `.stitch/NAVIGATION.md`. Bump `spec-version` to the next value (1 for first author, 2 if migrating a v1 spec, etc.). Show the user a diff-summary of what was generated.

Front matter must include:

```yaml
---
spec-version: <N>
nav-spec-skill-version: 2.0.0
design-md-sha: <SHA of DESIGN.md, or "absent">
stitch-project-id: <ID>
phase-0-compliance: { categorical: '%', strict: '%', date: 'YYYY-MM-DD' }
injection-budgets:
  <surface/archetype/viewport/pattern>: { essential: N, extended: N, total: N }
---
```

---

## Phase 7 — Parallel three-reviewer pass

Spawn three agents in a single message via the Task tool using `subagent_type: general-purpose`. Each reviewer gets the draft + Phase 0 report + CLAUDE.md + drift-audit results (from Phase 8 if already run) + pattern-catalog.md + ia-principles.md.

v2 reviewer rubrics are upgraded:

### IA architect reviewer (v3)

> You are a senior Information Architect who has designed navigation systems at Figma, Linear, and Stripe. You are reviewing a venture's navigation spec for completeness and rigor, with specific attention to Dan Brown's 8 IA principles, the reachability matrix, AND whether Sections 1–4 could have been authored before any shipped code existed.

**Rubric:**

1. Evaluate the spec against each of Dan Brown's 8 principles (Objects, Choices, Disclosure, Exemplars, Front Doors, Multiple Classifications, Focused Navigation, Growth). Pass/fail per principle with evidence.
2. Reachability matrix completeness — is every surface a From and a To? Are all dashboards linking to all sibling lists? Are entry-only / terminal surfaces correctly flagged?
3. Task model coverage — does every declared task have a surface mapping?
4. State machine completeness — does every surface handle auth, data, and task states?
5. Content taxonomy — is it exhaustive, consistent, canonical-label-only?
6. Cross-surface context — if persistent-context pattern is used, is it applied consistently?
7. **Authoring direction (v3).** Sections 1–4 must be writable as if shipped code did not exist. For each claim in Sections 1–4, ask: "could this sentence have been written before any code existed, using only contracts / tickets / interviews / analytics?" Any sentence whose truth depends on shipped UI state — present-tense claims about existing UI ("the portal currently has…", "the sidebar shows…"), paraphrases of shipped chrome, or URL paths cited without an independent source — fails this rubric item. Return the offending sentences verbatim.
8. **Focused Navigation + Pattern Fitness coherence (v3).** Per the updated "Focused Navigation" application in [references/ia-principles.md](../references/ia-principles.md), the chosen pattern must match the `return_locus` distribution of the top-3-by-frequency tasks. If Section 4's decision disagrees with what §1 evidence would suggest (i.e., R25 would fire), flag as an IA principle failure.

### Pattern specialist reviewer (v3)

> You are a design-systems specialist who has shipped pattern libraries at Shopify, Atlassian, or IBM. You know NN/g, Material Design 3, Apple HIG, and Dan Brown deeply. You are reviewing a venture's pattern selections against established catalogs AND against the disqualifier conditions in `references/pattern-disqualifiers.md`.

**Rubric:**

1. For each `{surface × archetype}` pattern selection: is it present in the catalog? Is the rationale valid?
2. Are any patterns invented (not in the catalog)? If so, flag.
3. Does the pattern's required-elements list match the chrome contract in Section 6?
4. Are composite patterns (e.g., hub-and-spoke + dominant-action) documented explicitly?
5. Do destination counts match pattern constraints (e.g., Material bottom nav = 3–5 destinations; not 7)?
6. Are anti-patterns flagged correctly (no hamburger on desktop, no tabs changing URL, no invented patterns)?
7. **Runner-up explicitly named (v3).** Does Section 4 name a runner-up pattern for every decision? A specific named catalog entry, not "considered alternatives." Missing runner-up = failed review.
8. **Value-cited defense (v3).** For every override of an R25 disqualifier, does the defense cite specific task-model input VALUES (not prose)? Acceptable: "return_locus for pay-invoice is /portal, not external, because…" Unacceptable: "hub-and-spoke feels right for our users."
9. **Override approval gate (v3).** When reviewing a §4 decision whose defense overrides an R25 disqualifier, you are one of the 3 voters on the ≥2-of-3 reviewer-consensus gate. Your rubric response for that decision MUST either explicitly approve the override citing the disqualifier ID (e.g., "I approve override of D1 on hub-and-spoke because the cited redirect path satisfies NN/g's hub-return requirement") OR explicitly decline with a reason. Do not equivocate.

### Implementation reviewer (v2)

> You are a senior Astro+Tailwind engineer reviewing a proposed spec against the venture's existing component shapes. You think about class lists, semantic HTML, keyboard traversal, and whether a contract can be implemented without refactoring working components.

**Rubric:**

1. Diff the proposed chrome contracts against shipped components (`Nav.astro`, `PortalHeader.astro`, `AdminLayout.astro`, etc.). Flag any contract that would require component refactor, with refactor scope.
2. A11y floor — per-surface landmarks, focus rings, skip-to-main, icon-only button labels.
3. Cross-surface a11y — heading hierarchy consistency, tab order across surfaces, consistent landmark semantics.
4. Injection budget — does every `{surface, archetype, viewport, pattern}` combo stay within 800 tokens?
5. Validator rule coverage — are R16–R24 inputs (reachability matrix, taxonomy, state machine) complete enough for the validator to run?

### Output format (all three)

```
## Overall assessment
[2–3 sentences, not diplomatic]

## Critical issues (ranked)
1. <issue + why it matters + specific fix>
2. ...

## Principles/pattern scorecard (IA and Pattern reviewers only)
<per-principle or per-pattern pass/fail with evidence>

## Decisions needed from user
<items requiring human judgment>
```

**IMPORTANT:** launch all three in a single message for parallel execution.

---

## Phase 8 — Decision rounds

Surface the Decisions-needed items from the reviewers. Filter to the 3–5 that materially change the spec. Present each with your recommendation and rationale. Wait for the user's answers.

Typical v2 decisions:

- "Dashboard surface declares hub-and-spoke; Implementation reviewer flags no section cards in current `/portal/index.astro`. Add section cards to the spec (and to code) or choose a different pattern?"
- "Pattern specialist flags drawer on admin as Material anti-pattern (destination count too low). Switch admin to tabs (current) or keep drawer?"
- "IA architect flags orphan destination `/portal/settings` exists in code but not in matrix. Add to matrix + dashboard section cards, or remove from code?"
- "Taxonomy says 'Proposal' everywhere but 2 shipped pages say 'Quote.' Update code, or accept shipped inconsistency?"

---

## Phase 9 — Final spec saved

Apply reviewer fixes and user decisions. Save `.stitch/NAVIGATION.md`. Ensure `spec-version` correct. Show summary of shifts from draft.

---

## Phase 10 — Integration-check regeneration (chrome-level)

Pick one existing surface (recommended: the venture's primary dashboard, mobile). Regenerate with the new injection snippet live.

- Extract HTML download URL from Stitch response
- Download HTML
- Run `validate.py` with the surface's full classification (5 tags)
- Pass = zero violations against the predicted spec
- Fail = do not proceed; tune the spec and repeat

This proves the spec is **self-consistent**. Phase 11 proves it **prevents drift**.

---

## Phase 11 — Adversarial verification (NEW coverage)

### 11a — Chrome adversarial (as in v1)

Generate three prompts the spec author has NOT seen with full 5-tag classification. Run validator on each. Pass = validator reports zero chrome-rule violations.

### 11b — IA reachability traversal (NEW in v2)

Walk the actual codebase (`src/pages/**`) and verify the reachability matrix matches live code:

1. For every route in `src/pages/**/index.astro` (and `[id].astro`), look up the matrix.
2. For every matrix row where `From` is a dashboard and `Required=Yes`, grep the dashboard's HTML for `<a href="{To}">`. If missing, the spec's pattern isn't implemented — real failure mode.
3. For every matrix row where `From` is a detail and `Mechanism=Back button`, grep for a back affordance with href={To}.
4. Surface orphan destinations: routes not listed as `To` in any matrix row.
5. Surface dead-ends: surfaces listed as `From` with no Required=Yes outbound rows.

This is the step that would have caught the April 2026 portal-home gap. If any violation, return to Phase 3 (IA model) and fix.

### 11c — Pattern conformance (NEW in v2)

For each declared pattern in Section 4, verify its required-elements list is satisfied by the shipped or generated HTML. Spot-check 2–3 surfaces per pattern.

---

## Phase 12 — Write-back to consuming skills

Only run if the venture is the first v2-adopter globally. Otherwise skip (consuming skills already updated for v2).

Edits required:

- `~/.agents/skills/stitch-design/SKILL.md` — classification now requires 5 tags (task, pattern added); pipeline step 1b fail-fast text updated; step 3 NAV CONTRACT block uses essential + extended format per [injection-snippet-template.md](../references/injection-snippet-template.md); step 3b validator now runs R1–R24.
- `~/.agents/skills/stitch-design/workflows/text-to-design.md` and `edit-design.md` — 5-tag reference.
- `~/.agents/skills/stitch-design/examples/enhanced-prompt.md` — add v2 example showing 5-tag classification and essential+extended NAV CONTRACT.
- `.agents/skills/stitch-ux-brief/SKILL.md` (per-venture) — Phase 1 check for NAVIGATION.md updated to detect spec-version; Phase 7 concept-prompt template uses 5 tags; Phase 11 strip directive driven by chrome-forbidden list; Phase 12 RUN-LOG records `nav-spec-skill-version` and `spec-version`.

All edits are guarded by `hasNavigationMd && specVersion >= 2` — v1 specs still work with v1-compatible injection (chrome-only).

Produce a PR with these edits on a separate branch. Title: `feat(stitch): nav-spec v2 — IA + patterns + chrome three-layer injection`.

---

## Final output

Tell the user:

- Where `.stitch/NAVIGATION.md` lives and its spec-version
- Phase 10 integration-check result
- Phase 11 adversarial + reachability traversal results
- Phase 11b — any IA violations found (orphans, dead-ends)
- Whether the write-back PR was produced or skipped
- Next step: run `/stitch-design` or `/stitch-ux-brief` — NAV CONTRACT (v2) is now injected automatically for any 5-tag prompt
