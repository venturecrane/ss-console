---
description: Update an existing `.stitch/NAVIGATION.md`. Versioning-aware. Runs a focused reviewer pass on the delta. Supports v1→v2 migration.
---

# Revise NAVIGATION.md

Use when:

- Venture introduces a new surface class or archetype
- A pattern selection changes (e.g., switching from hub-and-spoke to drawer on admin)
- The reachability matrix gains or loses routes
- The content taxonomy grows (new entity, new status label)
- A drift audit surfaces code-spec mismatch that should resolve in the spec's favor
- **Migrating from v1 (chrome-only) to v2 (three-layer)**

---

## Arguments

```
/nav-spec --revise [--add-surface-class X] [--add-archetype Y] [--add-pattern Z]
                   [--align-to-code <path>] [--migrate-to-v2] [--migrate-to-v3]
```

---

## Change classification

| Classification      | Examples                                                                                                                                                                        | Spec-version bump   | Reviewer pass                         |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------- | ------------------------------------- |
| **Additive**        | New archetype, new forbidden pattern, new matrix row                                                                                                                            | minor (1.0 → 1.1)   | Optional                              |
| **Structural**      | Taxonomy redefinition, rule inversion, surface-class reshape, chrome contract change for existing archetype                                                                     | major (1.x → 2.0)   | Required                              |
| **Corrective**      | Aligning spec to shipped code, fixing a wrong contract                                                                                                                          | major               | Required (Implementation-focused)     |
| **Pattern change**  | Changing declared pattern for a `{surface × archetype}`                                                                                                                         | major               | Required (Pattern specialist focused) |
| **v1→v2 migration** | Adding IA layer (task model, matrix, state machine, taxonomy) to a v1 chrome-only spec                                                                                          | major (bump to 2.0) | Required (full three-reviewer pass)   |
| **v2→v3 migration** | Adding evidence_source + return_locus columns (§1), pattern-decision-log format (§4), evidence-mode front matter; enabling R25 Pattern Fitness + R26 authoring-direction checks | major (bump to 3.0) | Required (full three-reviewer pass)   |

---

## Steps

1. **Load current spec.** Read `.stitch/NAVIGATION.md`. Note `spec-version` and `nav-spec-skill-version` in front matter.

2. **Detect v1→v2 migration case.** If spec-version is 1 and skill is 2.0+, the spec must be migrated. Run the full v2 authoring workflow (Phases 2–5) to add:
   - Section 1: Task model
   - Section 3: Reachability matrix
   - Section 4: Pattern selection (annotate each `{surface × archetype}`)
   - Section 5: State machine
   - Section 12: Content taxonomy

   Preserve Sections 6–11 (chrome contracts) from v1 with minor format adjustments.

3. **Classify the change.** Use the table above. For additive changes, may skip reviewer pass if the addition is obvious and low-risk. For anything else, reviewer pass is required.

4. **Run scoped drift audit and ia-audit.** Both audits narrowed to the affected surface(s) or archetypes. This grounds the revision in real code/artifact state.

5. **Draft the delta.** Edit only the affected sections. Do not rewrite unchanged sections. Preserve history in the front matter's `revisions:` log:

   ```yaml
   revisions:
     - {
         from: 1.0,
         to: 2.0,
         date: '2026-04-15',
         kind: 'v1→v2 migration',
         added: ['Section 1', 'Section 3', 'Section 4', 'Section 5', 'Section 12'],
       }
     - { from: 2.0, to: 2.1, date: '2026-05-20', kind: 'additive', added: ['archetype: transient'] }
   ```

6. **Focused reviewer pass** (if required). Subset of the three reviewers, parallel:
   - Additive → skip (unless the addition is a new pattern, then include Pattern specialist)
   - Structural → full three
   - Corrective → Implementation-focused (single reviewer)
   - Pattern change → Pattern specialist + IA architect
   - v1→v2 migration → full three

   Same output format as `author.md` Phase 7.

7. **Decision round** (if reviewers surfaced any). Same format as `author.md` Phase 8.

8. **Apply edits.** Bump `spec-version`. Update front matter with new `design-md-sha` if DESIGN.md changed; update `nav-spec-skill-version` if rev'd.

9. **Version-impact check.** For each existing generation under `.stitch/designs/**/*.html`, run `validate.py` against the new spec. Count non-compliant files. Do not auto-regenerate; surface for user decision.

10. **Integration-check regeneration** on one affected surface (if the change is structural, corrective, pattern change, or migration). Same as `author.md` Phase 10.

11. **IA audit re-run** (if change affected Sections 1–5). Same as `author.md` Phase 11b.

12. **Save and report.** Write new version. Summarize:
    - Spec-version N → M
    - What changed (sections touched)
    - Non-compliant existing designs count
    - IA audit result (violations caught)
    - Next steps (e.g., "run `/nav-spec --ia-audit` and fix the 4 orphan destinations")

---

## v2→v3 migration details

When migrating a v2 spec to v3, order of operations matters:

### Order

1. **Add v3 front matter.** Insert `evidence-mode: validated` (or `provisional` for pre-launch). If provisional, add `provisional-review-date: <YYYY-MM-DD>` (≤6 months out).
2. **Expand Section 1 task tables** per surface class to include `evidence_source` and `return_locus` columns. Cite a non-UI artifact per task; use `provisional:<type>` values only in provisional mode. Prose citation is sufficient for non-hub `return_locus` values; `return_locus=hub` requires structural evidence (URL literal, interview quote, or analytics event).
3. **Rewrite Section 4 pattern selections** into the v3 decision-log format:
   - `**Chosen pattern:** <name>`
   - `**Runner-up pattern:** <name>`
   - `**Defense:** <prose; if overriding R25, cite specific task-model values>`
   - (If override) Reviewer-approval lines from Phase 7 citing the specific disqualifier IDs.
4. **Run `validate.py --check-pattern-fitness --spec .stitch/NAVIGATION.md`** to fire R25 against the updated spec. Any R25 violations surface latent design debt from v2 (patterns that contradict the task model's declared return_locus distribution). Resolve per author.md Phase 4c: defense + reviewer approvals, switch to a surviving pattern, or file a provisional-override artifact.
5. **Run R26 lint** (runs automatically alongside R25). If §1–4 cite `src/components/**` or `*.astro`, remove or move those citations to §6+ (chrome contracts).
6. **Bump spec-version to 3.0** and `nav-spec-skill-version: 3.0.0`.
7. **Preserve v2's spec-version history** in `revisions:` front matter. Add a new entry summarizing what changed.

### What typically surfaces in v2→v3 migration

- Pattern-task-model disagreements — v2 specs were susceptible to the "pattern ratifies shipped chrome" failure. v3 R25 surfaces any case where the task model's evidence contradicts the chosen pattern.
- Prose `return_locus=hub` claims that lack structural evidence — v2 had no such requirement; v3 requires URL literal / interview / analytics event.
- Authoring-direction citations in Sections 1–4 — v2 author.md licensed `src/pages/` scans; v3 forbids them. Move citations to §6+ or re-source from non-UI artifacts.

Document each of these in the migration's revisions entry.

### Output

After v3 migration:

- Spec-version bumped to 3.0
- `nav-spec-skill-version: 3.0.0` in front matter
- R25 + R26 passing (or override artifacts filed in provisional mode)
- Pattern decisions in §4 use the structured format
- Any pattern-level changes flagged in the `revisions` entry

---

## v1→v2 migration details

When migrating a v1 spec to v2, order of operations matters:

### Order

1. Run v2 authoring Phase 2 (Task model) against the existing venture
2. Run v2 authoring Phase 3 (IA model — sitemap, reachability matrix, etc.)
3. Run v2 authoring Phase 4 (Pattern selection — annotate each surface with its pattern)
4. Run v2 authoring Phase 5 (Chrome — verify v1 contracts still hold, adjust as needed)
5. Preserve v1's spec-version history in `revisions:` front matter
6. Run Phase 11b IA reachability traversal — this is the step that falsifies v1 chrome-only assumptions
7. If R16–R24 violations found, fix the code first (don't adjust the spec to match broken code unless you're consciously ratifying a shipped reality)

After v1→v2 migration, consider whether v2→v3 migration is also warranted (running them back-to-back is fine).

### What typically surfaces in v1→v2 migration

- Orphan destinations — v1 may have declared "no primary nav on portal header" without verifying sibling-list reachability. This is the v1 failure mode.
- Missing task model — v1 never captured why each surface exists
- Invented pattern names — v1 chrome contracts may have been authored without reference to a catalog; v2 requires anchoring to NN/g/Material/HIG
- Taxonomy drift — v1 lacked a taxonomy section, so labels drifted freely

Document each of these in the migration's revisions entry.

### Output

After migration:

- Spec-version bumped to 2.0 (or higher if prior revisions occurred)
- `nav-spec-skill-version: 2.0.0` in front matter
- IA audit report: `examples/ia-audit-<YYYY-MM-DD>.md`
- Summary of violations found and whether they were fixed in code (separate PR) or ratified in the spec

---

## Spec-version bump guidance

- **Minor (N.0 → N.1):** additive change. Existing generations stay valid. Existing skill consumers can continue.
- **Major (N.x → (N+1).0):** structural, corrective, pattern change, or v1→v2 migration. Existing generations are at-risk; flag for review. Existing consumer skills must handle the new version (they already should — all nav-spec-aware skills graceful-degrade).

---

## When NOT to revise

Don't run `revise` to silence a Stitch drift. First ask: "Is this drift a Stitch failure, or a spec gap?"

- If Stitch drifted against a clear spec rule, the validator should have caught it — fix the validator (add rule or refine existing one).
- If the spec rule was ambiguous, revise the spec with the clarification.
- If the code is right and the spec is wrong, revise the spec (corrective).
- If both are wrong, fix both.

Never revise _away_ the spec's contract because one Stitch generation disagreed. The spec is the source of truth; drift is the input, not the output.
